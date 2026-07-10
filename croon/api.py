"""FastAPI app — HTTP API (spec §8) + serves the demo UI (spec §9).

Wires everything together:
  - builds the CapClient from CROON_CAP_MODE (mock|live) — the demo safety net
  - starts the in-process Scheduler (Layer B) on startup
  - exposes standing-order CRUD, run-now, pause, run detail, and a live event
    stream for the mini-RFQ money shot

The winner-selection + settlement logic lives in the engine; this file is only
transport + serialization.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from croon.cap_client import build_cap_client
from croon.config import get_settings
from croon.db import engine, get_session, init_db
from croon.events import EVENT_BUS
from croon.models import Run, StandingOrder
from croon.scheduler import Scheduler
from croon.schemas import StandingOrderCreate

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cap = build_cap_client()
    scheduler = Scheduler(cap)
    scheduler.start()
    app.state.cap = cap
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(title="CROON RFQ", version="0.1.0", lifespan=lifespan)


# --- Serialization helpers --------------------------------------------------


def _order_out(order: StandingOrder) -> dict:
    return {
        "id": order.id,
        "name": order.name,
        "task_prompt": order.task_prompt,
        "category": order.category,
        "cadence_seconds": order.cadence_seconds,
        "budget_per_run_usdc": str(order.budget_per_run_usdc),
        "max_total_budget_usdc": str(order.max_total_budget_usdc),
        "max_agents_to_query": order.max_agents_to_query,
        "selection_policy": order.selection_policy,
        "acceptance_criteria": order.acceptance_criteria,
        "status": order.status,
        "total_spent_usdc": str(order.total_spent_usdc),
        "created_at": order.created_at.isoformat(),
        "next_run_at": order.next_run_at.isoformat(),
    }


def _run_out(run: Run) -> dict:
    try:
        quotes = json.loads(run.quotes_json or "[]")
    except (ValueError, TypeError):
        quotes = []
    return {
        "id": run.id,
        "standing_order_id": run.standing_order_id,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "status": run.status,
        "quotes": quotes,
        "winner_agent_id": run.winner_agent_id,
        "selection_reason": run.selection_reason,
        "amount_paid_usdc": str(run.amount_paid_usdc),
        "tx_hash": run.tx_hash,
        "output_ref": run.output_ref,
        "output_hash": run.output_hash,
        "receipt_hash": run.receipt_hash,
        "fallback_used": run.fallback_used,
    }


# --- Standing-order endpoints (spec §8) -------------------------------------


@app.post("/standing-orders")
def create_standing_order(
    body: StandingOrderCreate, session: Session = Depends(get_session)
) -> dict:
    order = StandingOrder(
        name=body.name,
        task_prompt=body.task_prompt,
        category=body.category,
        cadence_seconds=body.cadence_seconds,
        budget_per_run_usdc=body.budget_per_run_usdc,
        max_total_budget_usdc=body.max_total_budget_usdc,
        max_agents_to_query=body.max_agents_to_query,
        selection_policy=body.selection_policy,
    )
    order.acceptance_criteria = body.acceptance_criteria
    session.add(order)
    session.commit()
    session.refresh(order)
    return _order_out(order)


@app.get("/standing-orders")
def list_standing_orders(session: Session = Depends(get_session)) -> list[dict]:
    orders = session.exec(select(StandingOrder)).all()
    return [_order_out(o) for o in orders]


@app.get("/standing-orders/{order_id}")
def get_standing_order(
    order_id: str, session: Session = Depends(get_session)
) -> dict:
    order = session.get(StandingOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="standing order not found")
    runs = session.exec(
        select(Run)
        .where(Run.standing_order_id == order_id)
        .order_by(Run.started_at.desc())
    ).all()
    return {**_order_out(order), "runs": [_run_out(r) for r in runs]}


@app.post("/standing-orders/{order_id}/run-now")
async def run_now(order_id: str) -> dict:
    """DEMO CRITICAL — trigger a full mini-RFQ + settlement cycle immediately."""
    with Session(engine) as session:
        order = session.get(StandingOrder, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="standing order not found")
    # Delegate to the scheduler so cadence bookkeeping stays in one place.
    await app.state.scheduler.trigger(order_id)
    with Session(engine) as session:
        run = session.exec(
            select(Run)
            .where(Run.standing_order_id == order_id)
            .order_by(Run.started_at.desc())
        ).first()
        return _run_out(run) if run else {"status": "no_run"}


@app.post("/standing-orders/{order_id}/pause")
def pause_standing_order(
    order_id: str, session: Session = Depends(get_session)
) -> dict:
    order = session.get(StandingOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="standing order not found")
    order.status = "paused"
    session.add(order)
    session.commit()
    session.refresh(order)
    return _order_out(order)


@app.post("/standing-orders/{order_id}/resume")
def resume_standing_order(
    order_id: str, session: Session = Depends(get_session)
) -> dict:
    order = session.get(StandingOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="standing order not found")
    if order.status == "paused":
        order.status = "active"
        session.add(order)
        session.commit()
        session.refresh(order)
    return _order_out(order)


@app.get("/standing-orders/{order_id}/events")
def get_events(order_id: str, after: int = Query(0)) -> JSONResponse:
    """Live event feed for the UI (poll with ?after=<last_seq>)."""
    events = EVENT_BUS.get_since(order_id, after)
    return JSONResponse(events)


# --- Run endpoint -----------------------------------------------------------


@app.get("/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_out(run)


# --- Meta + UI --------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "cap_mode": s.cap_mode}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


# Mount static assets (created in the UI step).
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
