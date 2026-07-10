"""Scheduler / Trigger — Layer B.

A deliberately SIMPLE in-process interval loop (spec §11: do NOT build
production cron). Every `tick_seconds` it wakes, finds active standing orders
whose `next_run_at` is due, and fires a run. The demo-critical path is the
`run_now` trigger (in api.py); this loop just proves cadence works.

Runs execute on the shared asyncio loop. Each execution gets its own DB session.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from croon.cap_client import CapClient
from croon.config import get_settings
from croon.db import engine
from croon.engine import execute_run
from croon.models import StandingOrder


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Scheduler:
    """Background asyncio task that fires due standing orders on cadence."""

    def __init__(self, cap: CapClient) -> None:
        self.cap = cap
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        tick = get_settings().scheduler_tick_seconds
        while not self._stop.is_set():
            try:
                await self._fire_due_orders()
            except Exception:  # noqa: BLE001 — a bad tick must never kill the loop
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=tick)
            except asyncio.TimeoutError:
                pass

    async def _fire_due_orders(self) -> None:
        now = _now()
        with Session(engine) as session:
            due = session.exec(
                select(StandingOrder).where(StandingOrder.status == "active")
            ).all()
            for order in due:
                if order.next_run_at <= now:
                    await self.trigger(order.id)

    async def trigger(self, order_id: str) -> None:
        """Run a single standing order now and reschedule its next_run_at."""
        with Session(engine) as session:
            order = session.get(StandingOrder, order_id)
            if order is None:
                return
            await execute_run(order, session, self.cap)
            # Reschedule (refresh: execute_run may have flipped status).
            session.refresh(order)
            if order.status == "active":
                order.next_run_at = _now() + timedelta(seconds=order.cadence_seconds)
                session.add(order)
                session.commit()
