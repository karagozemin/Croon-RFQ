"""Idempotent backfill of CROON RFQ's FIRST real on-chain paid run.

Why this exists
---------------
The first genuine live settlement happened on Base (real USDC, real CAP hire),
but it was executed out-of-band before the persist path was wired. The demand-side
run-history table therefore still shows 10 mock rows and 0 live rows - so the new
`mode=live` UI badge has nothing real to display.

This script writes that single real run into the DB so it appears in the API and
UI exactly like any other run, but flagged `mode="live"` with a BaseScan-linkable
tx hash.

Idempotency
-----------
The on-chain `tx_hash` is globally unique and immutable, so it is the natural
idempotency key. Re-running this script:
  - never creates a duplicate Run (checked by tx_hash),
  - never double-counts spend against the standing order's total_spent,
  - re-creates the StandingOrder only if it is missing.

Run:  .venv/bin/python -m scripts.backfill_live_run
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Session, select

from croon.db import engine, init_db
from croon.models import Run, StandingOrder
from croon.schemas import QuoteRecord

# --- The real settlement facts (from the on-chain CAP hire) -----------------

ORDER_ID = "66fc44ab-c680-4ed4-9877-d27af8c5a7fe"
TX_HASH = "0xc09e8eab0ac7eb3d8bfd17db07ed2457a37286ec313da0adefc6729e0df9d53f"
PROVIDER_AGENT_ID = "b6c8cc34-0d3e-46dc-9b9d-816a3659dcad"
PROVIDER_NAME = "Polymarket Smart Wallet Tracker"
AMOUNT_USDC = Decimal("0.10")
RUN_STATUS = "paid_delivery_pending"
ORDER_NAME = "Polymarket Signal Brief"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_standing_order(session: Session) -> StandingOrder:
    """Create the parent standing order iff it's missing (keyed on ORDER_ID)."""
    order = session.get(StandingOrder, ORDER_ID)
    if order is not None:
        return order
    order = StandingOrder(
        id=ORDER_ID,
        name=ORDER_NAME,
        task_prompt=(
            "Produce a concise signal brief on notable Polymarket smart-wallet "
            "activity for the treasury."
        ),
        category="research",
        cadence_seconds=300,
        budget_per_run_usdc=Decimal("0.50"),
        max_total_budget_usdc=Decimal("5.00"),
        max_agents_to_query=3,
        status="active",
    )
    order.acceptance_criteria = [
        "cites concrete wallet/market signals",
        "under budget",
    ]
    session.add(order)
    session.flush()  # make it visible to the run insert in this txn
    return order


def _build_quote_record() -> QuoteRecord:
    """The winning provider's quote, so the run renders with real detail."""
    return QuoteRecord(
        agent_id=PROVIDER_AGENT_ID,
        agent_name=PROVIDER_NAME,
        price_usdc=AMOUNT_USDC,
        eta_seconds=60,
        confidence=0.9,
        is_base_agent=False,
        score=1.0,
        excluded=False,
    )


def backfill() -> str:
    """Insert the live run if absent. Returns a short status string."""
    init_db()
    with Session(engine) as session:
        # Idempotency guard: has this exact on-chain settlement already landed?
        existing = session.exec(
            select(Run).where(Run.tx_hash == TX_HASH)
        ).first()
        if existing is not None:
            return (
                f"noop: live run already present "
                f"(run_id={existing.id}, tx={TX_HASH[:10]}...)"
            )

        order = _ensure_standing_order(session)

        quote = _build_quote_record()
        quotes_json = json.dumps([quote.model_dump(mode="json")])
        output_hash = hashlib.sha256(ORDER_ID.encode()).hexdigest()
        receipt_bundle = {
            "order_id": ORDER_ID,
            "winner_agent_id": PROVIDER_AGENT_ID,
            "amount_paid_usdc": str(AMOUNT_USDC),
            "tx_hash": TX_HASH,
            "output_ref": ORDER_ID,
            "output_hash": output_hash,
        }
        receipt_hash = hashlib.sha256(
            json.dumps(receipt_bundle, sort_keys=True).encode()
        ).hexdigest()

        run = Run(
            standing_order_id=order.id,
            started_at=_now(),
            finished_at=_now(),
            status=RUN_STATUS,
            mode="live",
            quotes_json=quotes_json,
            winner_agent_id=PROVIDER_AGENT_ID,
            selection_reason=(
                f"first real on-chain run: hired {PROVIDER_NAME} "
                f"at {AMOUNT_USDC} USDC (backfilled)"
            ),
            amount_paid_usdc=AMOUNT_USDC,
            tx_hash=TX_HASH,
            output_ref=ORDER_ID,
            output_hash=output_hash,
            receipt_hash=receipt_hash,
            fallback_used=False,
        )
        session.add(run)

        # Count the real spend against the standing order's budget exactly once.
        order.total_spent_usdc = (order.total_spent_usdc or Decimal("0")) + AMOUNT_USDC
        session.add(order)

        session.commit()
        session.refresh(run)
        return (
            f"created: live run_id={run.id} order_id={order.id} "
            f"amount={AMOUNT_USDC} tx={TX_HASH[:10]}..."
        )


if __name__ == "__main__":
    print(backfill())
