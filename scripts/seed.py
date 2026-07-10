"""Seed a standing order and execute a few runs to populate history.

Run AFTER installing deps:  python scripts/seed.py

Uses the same pipeline the API uses (MockCapClient in mock mode), so the demo
opens with a non-empty run history (spec §12: "a run history with multiple
completed runs").
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Make `croon` importable when run as `python scripts/seed.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from croon.cap_client import build_cap_client
from croon.db import engine, init_db
from croon.engine import execute_run
from croon.models import Run, StandingOrder


async def main() -> None:
    init_db()
    cap = build_cap_client()

    with Session(engine) as session:
        existing = session.exec(
            select(StandingOrder).where(StandingOrder.name == "Wallet Risk Brief")
        ).first()
        if existing is None:
            order = StandingOrder(
                name="Wallet Risk Brief",
                task_prompt="Produce a concise wallet risk brief for the treasury.",
                category="risk",
                cadence_seconds=300,
                budget_per_run_usdc=Decimal("0.50"),
                max_total_budget_usdc=Decimal("5.00"),
                max_agents_to_query=3,
            )
            order.acceptance_criteria = ["cite sources", "under 300 words"]
            session.add(order)
            session.commit()
            session.refresh(order)
        else:
            order = existing
        order_id = order.id

    # Execute three runs to build history.
    for i in range(3):
        with Session(engine) as session:
            order = session.get(StandingOrder, order_id)
            if order.status != "active":
                break
            run = await execute_run(order, session, cap)
            print(f"Run {i + 1}: status={run.status} winner={run.winner_agent_id} "
                  f"paid={run.amount_paid_usdc} tx={run.tx_hash}")

    with Session(engine) as session:
        runs = session.exec(
            select(Run).where(Run.standing_order_id == order_id)
        ).all()
        print(f"\nSeeded standing order {order_id} with {len(runs)} run(s).")


if __name__ == "__main__":
    asyncio.run(main())
