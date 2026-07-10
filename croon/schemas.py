"""Pydantic schemas for all I/O boundaries.

These are the *contract types* that CapClient and the engine speak in.
They are intentionally SDK-agnostic: the real CAP SDK is adapted to these
inside `cap_client.py`, nowhere else.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


# --- CAP domain types (what CapClient returns) ------------------------------


class AgentInfo(BaseModel):
    """A discoverable CROO agent (supply side)."""

    agent_id: str
    name: str
    category: str | None = None
    listed_price_usdc: Decimal | None = None
    # SLA / reputation signal (0..1). Placeholder for MVP — see scoring.py.
    reputation: float = 0.5
    is_base_agent: bool = False  # True for OUR fallback providers (§7, §10)


class TaskSpec(BaseModel):
    """The unit of work sent to a candidate agent for quoting/hiring."""

    task_prompt: str
    category: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)


class Quote(BaseModel):
    """A bid from one agent in the mini-RFQ round."""

    agent_id: str
    agent_name: str
    price_usdc: Decimal
    eta_seconds: int
    confidence: float  # 0..1 self-reported / derived confidence
    is_base_agent: bool = False


class Settlement(BaseModel):
    """Result of hire_and_pay — the on-chain payment outcome."""

    order_id: str
    agent_id: str
    amount_paid_usdc: Decimal
    tx_hash: str | None = None  # Base tx, BaseScan-linkable
    settled_at: datetime


class Delivery(BaseModel):
    """The output produced by the hired agent."""

    order_id: str
    output_text: str
    delivered_at: datetime


# --- Internal record types (persisted inside Run JSON) ----------------------


class QuoteRecord(BaseModel):
    """A quote annotated with its computed score, stored in run history."""

    agent_id: str
    agent_name: str
    price_usdc: Decimal
    eta_seconds: int
    confidence: float
    is_base_agent: bool = False
    score: float | None = None
    excluded: bool = False  # True if over budget (hard rule)
    exclusion_reason: str | None = None


# --- HTTP API request/response bodies ---------------------------------------


class StandingOrderCreate(BaseModel):
    name: str
    task_prompt: str
    category: str | None = None
    cadence_seconds: int = 300
    budget_per_run_usdc: Decimal = Decimal("0.50")
    max_total_budget_usdc: Decimal = Decimal("5.00")
    max_agents_to_query: int = 3
    selection_policy: str = "best_score_under_budget"
    acceptance_criteria: list[str] = Field(default_factory=list)
