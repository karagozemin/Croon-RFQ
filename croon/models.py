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

