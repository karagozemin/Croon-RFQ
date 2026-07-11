"""SQLModel tables — Layer A (Standing Order Store).

This is our "this is NOT a cron job" proof: stateful, budgeted, historical
commercial relationships. Standing-order state + budgets live HERE (off-chain),
never in a smart contract.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StandingOrder(SQLModel, table=True):
    """A recurring job with a budget + cadence (the demand side)."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    task_prompt: str
    category: str | None = None
    cadence_seconds: int = 300
    budget_per_run_usdc: Decimal = Field(default=Decimal("0.50"))
    max_total_budget_usdc: Decimal = Field(default=Decimal("5.00"))
    max_agents_to_query: int = 3
    selection_policy: str = "best_score_under_budget"

    # Stored as JSON string (list[str]).
    acceptance_criteria_json: str = "[]"

    status: str = "active"  # active | paused | budget_exhausted
    total_spent_usdc: Decimal = Field(default=Decimal("0"))

    created_at: datetime = Field(default_factory=_now)
    next_run_at: datetime = Field(default_factory=_now)

    @property
    def acceptance_criteria(self) -> list[str]:
        try:
            return list(json.loads(self.acceptance_criteria_json or "[]"))
        except (ValueError, TypeError):
            return []

    @acceptance_criteria.setter
    def acceptance_criteria(self, value: list[str]) -> None:
        self.acceptance_criteria_json = json.dumps(value or [])


class Run(SQLModel, table=True):
    """One execution of a standing order — a full mini-RFQ + settlement cycle."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    standing_order_id: str = Field(foreign_key="standingorder.id", index=True)

    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None

    status: str = "running"  # running | completed | failed | fallback_used

    # Every agent that quoted (list[QuoteRecord]) serialized to JSON.
    quotes_json: str = "[]"

    winner_agent_id: str | None = None
    selection_reason: str = ""
    amount_paid_usdc: Decimal = Field(default=Decimal("0"))
    tx_hash: str | None = None  # Base tx, BaseScan-linkable

    output_ref: str = ""  # output text or storage ref
    output_hash: str = ""  # sha256 of output
    receipt_hash: str = ""  # sha256 of the full receipt bundle

    fallback_used: bool = False


class BrokerageOrder(SQLModel, table=True):
    """Durable idempotency record for a MAIN-service brokered order.

    When a buyer hires the MAIN CROON RFQ service, CROON re-opens the market and
    HIRES + PAYS a downstream (child) agent — real USDC on Base. The CAP
    WebSocket can replay ``ORDER_PAID`` on reconnect AND across a provider
    process RESTART, so an in-memory cache is NOT sufficient: a replay after a
    restart would pay a second child and drain the wallet.

    This table is the crash-safe idempotency key. ``parent_order_id`` is the
    primary key and the row moves through a two-phase lifecycle:

      claimed   -> a child cycle is starting for this parent (no spend yet)
      settled   -> the child was HIRED + PAID; ``child_tx_hash`` is recorded
                   BEFORE the deliverable is assembled, so a crash between
                   payment and delivery still proves the spend happened and a
                   replay rebuilds the deliverable WITHOUT paying again
      completed -> the full proof-bundled ``deliverable`` is stored; a replay
                   returns it verbatim
      failed    -> the cycle ended with no spend (no eligible provider); a
                   replay is free to retry

    The narrow crash window (pay -> record settlement) is minimised by writing
    the settled row immediately after ``hire_and_pay`` returns.
    """

    parent_order_id: str = Field(primary_key=True)

    # claimed | settled | completed | failed
    status: str = "claimed"

    child_order_id: str | None = None
    child_tx_hash: str | None = None

    # Full buyer-facing deliverable text, stored once status == completed.
    deliverable: str = ""

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ProviderJob(SQLModel, table=True):
    """One incoming order served by OUR base agents as a CAP PROVIDER (§7/§10).

    This is the SUPPLY-side ledger, distinct from Run (the demand side). Its
    real job is IDEMPOTENCY: the CAP WebSocket may replay `order_negotiation_
    created` on reconnect, and a provider must never accept/deliver the same
    negotiation twice (double on-chain state, double work). `negotiation_id` is
    UNIQUE, so a duplicate insert fails fast and we skip.
    """

    id: str = Field(default_factory=_uuid, primary_key=True)

    # Idempotency key — the CAP negotiation this job is fulfilling.
    negotiation_id: str = Field(unique=True, index=True)
    service_id: str = Field(index=True)
    agent_id: str = ""  # OUR base-agent id handling it (e.g. base_gas_oracle)
    order_id: str | None = None  # on-chain order, set once accepted

    # received -> accepted -> delivered  (or rejected | failed)
    status: str = "received"

    requirements: str = ""       # the requester's task prompt
    output_hash: str = ""        # sha256 of the deliverable we produced
    accept_tx_hash: str | None = None
    deliver_tx_hash: str | None = None
    error: str = ""

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
