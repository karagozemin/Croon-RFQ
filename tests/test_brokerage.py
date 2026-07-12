"""Tests for the main-service brokerage (croon.brokerage).

When a buyer hires the MAIN CROON RFQ service, CROON must RE-OPEN THE MARKET:
discover -> quote -> score -> hire+pay a downstream child -> return a proof
bundle. These tests exercise that path fully offline against MockCapClient and
verify the two hard safety properties:

  * SPEND GUARD  - child spend is capped at CROON_MAX_CHILD_SPEND_USDC even when
    the buyer supplies a larger budget.
  * IDEMPOTENCY  - a replayed parent ORDER_PAID returns the cached deliverable
    and does NOT hire/pay a second child.

No network, no keys, no funded wallet: a MockCapClient is injected directly.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from sqlmodel import Session

from croon import brokerage
from croon.cap_client import MockCapClient
from croon.config import Settings
from croon.db import engine as _db_engine
from croon.models import BrokerageOrder



def _settings(**overrides) -> Settings:
    base = dict(cap_mode="mock")
    base.update(overrides)
    return Settings(**base)


class CountingCap(MockCapClient):
    """MockCapClient that records how many child settlements it performs."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.hire_calls = 0

    async def hire_and_pay(self, agent, task, agreed_price_usdc):  # type: ignore[override]
        self.hire_calls += 1
        return await super().hire_and_pay(agent, task, agreed_price_usdc)


@pytest.fixture(autouse=True)
def _clear_cache():
    brokerage._reset_idempotency_cache()
    yield
    brokerage._reset_idempotency_cache()


def _run(coro):
    return asyncio.run(coro)


# --- Happy path -------------------------------------------------------------


def test_brokered_order_reopens_market_and_settles_child():
    cap = CountingCap(simulate_latency=False)
    out = _run(
        brokerage.execute_main_brokerage_order(
            parent_order_id="parent_1",
            task_prompt="Produce a wallet risk brief",
            params={"category": "risk", "budget_per_run_usdc": "0.50"},
            cap=cap,
            settings=_settings(),
        )
    )
    assert cap.hire_calls == 1
    # Human-readable proof bundle markers.
    assert "CROON RFQ - brokered fulfilment" in out
    assert "Competitive quotes:" in out
    assert "Winner:" in out
    assert "receipt hash:" in out
    # Machine-readable receipt is embedded and parseable.
    receipt_json = out.split("--- machine-readable receipt ---")[1].strip()
    receipt = json.loads(receipt_json)
    assert receipt["kind"] == "croon_rfq_brokerage_receipt"
    assert receipt["parent_order_id"] == "parent_1"
    assert receipt["winner_agent_id"]
    assert receipt["child_tx_hash"]
    assert receipt["receipt_hash"]
    # CROON must never hire itself.
    assert receipt["winner_agent_id"] != "croon_recurring_rfq"


# --- Idempotency ------------------------------------------------------------


def test_replayed_parent_order_returns_cache_without_second_payment():
    cap = CountingCap(simulate_latency=False)
    kwargs = dict(
        parent_order_id="parent_dup",
        task_prompt="risk brief",
        params={"category": "risk"},
        cap=cap,
        settings=_settings(),
    )
    first = _run(brokerage.execute_main_brokerage_order(**kwargs))
    second = _run(brokerage.execute_main_brokerage_order(**kwargs))
    assert first == second           # identical deliverable
    assert cap.hire_calls == 1       # child paid exactly once across the replay


# --- Durability: idempotency survives a process restart ---------------------


def test_settled_state_recovers_across_restart_without_second_payment():
    # Simulate a crash AFTER the child was hired+paid but BEFORE the deliverable
    # was stored: the durable BrokerageOrder row is left in 'settled'. This is
    # exactly the state an in-memory cache would LOSE on restart. A replay must
    # recover from SQLite and NEVER pay a second child.
    parent_id = "parent_restart"
    child_order_id = "mock_order_preexisting"
    child_tx = "0xdeadbeefrestart"
    with Session(_db_engine) as session:
        session.add(
            BrokerageOrder(
                parent_order_id=parent_id,
                status="settled",
                child_order_id=child_order_id,
                child_tx_hash=child_tx,
            )
        )
        session.commit()

    # Fresh cap == fresh process: no in-memory knowledge of the prior payment.
    cap = CountingCap(simulate_latency=False)
    out = _run(
        brokerage.execute_main_brokerage_order(
            parent_order_id=parent_id,
            task_prompt="risk brief",
            params={"category": "risk"},
            cap=cap,
            settings=_settings(),
        )
    )
    assert cap.hire_calls == 0                     # NO second child payment
    assert "recovered after interruption" in out
    assert child_tx in out                          # anchored to the real spend

    # The row is advanced to 'completed' and the deliverable persisted.
    with Session(_db_engine) as session:
        row = session.get(BrokerageOrder, parent_id)
        assert row is not None
        assert row.status == "completed"
        assert row.deliverable == out

    # A further replay now returns the stored deliverable verbatim, still no pay.
    again = _run(
        brokerage.execute_main_brokerage_order(
            parent_order_id=parent_id,
            task_prompt="risk brief",
            params={"category": "risk"},
            cap=cap,
            settings=_settings(),
        )
    )
    assert again == out
    assert cap.hire_calls == 0


# --- Spend guard ------------------------------------------------------------



def test_spend_guard_caps_child_budget_below_buyer_budget():
    # Buyer offers a huge budget; the guard must clamp the effective child
    # budget. We pick "research" so a provider (Gamma @ 0.08) is still eligible
    # UNDER the 0.10 cap: this proves the guard is applied AND a settlement
    # still happens (rather than falling through to the no-provider path).
    cap = CountingCap(simulate_latency=False)
    settings = _settings()
    settings.max_child_spend_usdc = Decimal("0.10")
    out = _run(
        brokerage.execute_main_brokerage_order(
            parent_order_id="parent_guard",
            task_prompt="research brief",
            params={"category": "research", "budget_per_run_usdc": "999.00"},
            cap=cap,
            settings=settings,
        )
    )
    receipt = json.loads(out.split("--- machine-readable receipt ---")[1].strip())
    assert receipt["spend_guard_applied"] is True
    assert Decimal(receipt["effective_child_budget_usdc"]) == Decimal("0.10")
    assert Decimal(receipt["amount_paid_usdc"]) <= Decimal("0.10")
    assert cap.hire_calls == 1



# --- No eligible provider (no spend) ----------------------------------------


def test_no_eligible_provider_yields_no_spend_deliverable():
    # Force every non-base quote to fail AND cap the budget below the base
    # agents' listed prices so nothing is eligible -> no settlement at all.
    cap = CountingCap(simulate_latency=False, fail_non_base_quotes=True)
    settings = _settings()
    settings.max_child_spend_usdc = Decimal("0.001")  # below any listed price
    out = _run(
        brokerage.execute_main_brokerage_order(
            parent_order_id="parent_none",
            task_prompt="risk brief",
            params={"category": "risk"},
            cap=cap,
            settings=settings,
        )
    )
    assert cap.hire_calls == 0
    assert "No child settlement performed - budget protected." in out


if __name__ == "__main__":  # allow `python tests/test_brokerage.py`
    raise SystemExit(pytest.main([__file__, "-v"]))
