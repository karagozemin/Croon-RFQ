"""End-to-end smoke test for the mini-RFQ pipeline (mock mode, no network).

Runs TWO scenarios and prints a compact report:
  1. Normal run  -> a market agent wins, gets paid, delivers.
  2. Fallback run -> all market agents time out (failure injection) -> one of
     OUR base agents wins and delivers its REAL work product (§7/§10).

Usage:  python -m scripts.smoke_test
Exit code is non-zero if any invariant fails.
"""

from __future__ import annotations

import asyncio
import tempfile
from decimal import Decimal

from sqlmodel import Session, SQLModel, create_engine

from croon.cap_client import MockCapClient
from croon.engine import execute_run
from croon.models import StandingOrder


def _fresh_session() -> Session:
    # Isolated in-memory-ish DB per test run so we never touch croon.db.
    path = tempfile.mktemp(suffix=".db")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    import croon.models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _make_order(name: str) -> StandingOrder:
    return StandingOrder(
        name=name,
        task_prompt="Produce a wallet risk brief for 0xabc...",
        category="risk",
        cadence_seconds=300,
        budget_per_run_usdc=Decimal("0.50"),
        max_total_budget_usdc=Decimal("5.00"),
        max_agents_to_query=3,
        acceptance_criteria=["cite sources", "flag high-risk approvals"],
    )


async def _scenario_normal() -> bool:
    session = _fresh_session()
    order = _make_order("Wallet Risk Brief")
    session.add(order)
    session.commit()
    session.refresh(order)

    cap = MockCapClient(simulate_latency=False)
    run = await execute_run(order, session, cap)

    ok = (
        run.status == "completed"
        and run.fallback_used is False
        and run.winner_agent_id is not None
        and run.tx_hash and run.tx_hash.startswith("0x") and len(run.tx_hash) == 66
        and run.receipt_hash
        and run.output_hash
    )
    print(f"[normal]   status={run.status} winner={run.winner_agent_id} "
          f"paid={run.amount_paid_usdc} tx={run.tx_hash[:12]}... "
          f"receipt={run.receipt_hash[:12]}...")
    return bool(ok)


async def _scenario_fallback() -> bool:
    session = _fresh_session()
    order = _make_order("Wallet Risk Brief (fallback)")
    session.add(order)
    session.commit()
    session.refresh(order)

    # Failure injection: every market agent stalls past the RFQ timeout.
    cap = MockCapClient(simulate_latency=False, fail_non_base_quotes=True)
    run = await execute_run(order, session, cap)

    is_base_winner = run.winner_agent_id in {"base_listing_copy", "base_gas_oracle"}
    # The delivered output must be a base agent's REAL work product, not a stub.
    real_output = run.output_ref and "[MOCK OUTPUT]" not in run.output_ref

    ok = (
        run.status == "fallback_used"
        and run.fallback_used is True
        and is_base_winner
        and run.tx_hash
        and real_output
    )
    print(f"[fallback] status={run.status} winner={run.winner_agent_id} "
          f"paid={run.amount_paid_usdc} real_output={bool(real_output)}")
    if real_output:
        first_line = run.output_ref.splitlines()[0]
        print(f"           output preview: {first_line[:70]}")
    return bool(ok)


async def main() -> int:
    print("=== CROON RFQ pipeline smoke test (mock mode) ===")
    results = {
        "normal": await _scenario_normal(),
        "fallback": await _scenario_fallback(),
    }
    print("---")
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
