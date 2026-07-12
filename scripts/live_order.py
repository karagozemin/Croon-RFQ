"""Run ONE real on-chain CAP order against a chosen counterparty, then persist it.

This generalises `live_chain_test.py` (raw chain) + `backfill_live_run.py`
(idempotent persist) into a single, targeted, repeatable tool used for Part 2
(supply diversity). It targets ONE counterparty by agent_id so we can grow the
run history one deliberate, budgeted settlement at a time.

Chain (spend happens ONLY at step 4):
  1. negotiate_order      -> negotiation_id            (no spend)
  2. poll get_negotiation -> ACCEPTED / REJECTED / ...  (no spend)
  3. list_orders match    -> order_id, wait 'created'   (no spend)
  4. pay_order            -> REAL Base tx_hash          (<-- the only spend)
  5. get_delivery         -> deliverable (best-effort; async, may be pending)

Persist (idempotent on tx_hash, like backfill_live_run.py):
  - never creates a duplicate Run (checked by tx_hash),
  - never double-counts spend against the standing order,
  - creates the parent StandingOrder only if missing.

Usage (keep .env in mock; override on the command line only):
  CROON_CAP_MODE=live PYTHONPATH="$PWD" .venv/bin/python -m scripts.live_order \
      --agent-id 49373b68-8c41-4c95-b162-e9343f104de4 \
      --requirements '{}' \
      --so-name "Polymind Research Brief" \
      --prompt "Produce a concise market-sentiment brief."

Dry-run the FREE part only (steps 1-3, aborts BEFORE pay_order):
  ... -m scripts.live_order --agent-id <id> --requirements '{...}' --probe-only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault("CROON_CAP_MODE", "live")

from sqlmodel import Session, select

from croon.cap_client import LiveCapClient, _attr
from croon.config import get_settings
from croon.db import engine, init_db
from croon.models import Run, StandingOrder
from croon.schemas import QuoteRecord, TaskSpec

ACCEPT_TIMEOUT_S = 60
POLL_INTERVAL_S = 2.0


def log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def read_balance(client: LiveCapClient) -> str:
    for name in ("get_balance", "balance", "get_usdc_balance"):
        fn = getattr(client._client, name, None)
        if fn is None:
            continue
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                res = await res
            return str(res)
        except Exception as exc:  # noqa: BLE001
            return f"(balance read failed via {name}: {exc})"
    return "(no balance method on SDK client)"


def persist_live_run(
    *,
    order_id: str,
    tx_hash: str,
    agent_id: str,
    agent_name: str,
    amount_usdc: Decimal,
    so_name: str,
    prompt: str,
    category: str,
    delivery_text: str,
) -> str:
    """Idempotently write the real settlement into the run history (tx_hash key)."""
    init_db()
    with Session(engine) as session:
        existing = session.exec(select(Run).where(Run.tx_hash == tx_hash)).first()
        if existing is not None:
            return f"noop: live run already present (run_id={existing.id}, tx={tx_hash[:10]}...)"

        # Parent standing order: reuse one with this name, else create it.
        order = session.exec(
            select(StandingOrder).where(StandingOrder.name == so_name)
        ).first()
        if order is None:
            order = StandingOrder(
                name=so_name,
                task_prompt=prompt,
                category=category,
                cadence_seconds=300,
                budget_per_run_usdc=Decimal("0.50"),
                max_total_budget_usdc=Decimal("5.00"),
                max_agents_to_query=3,
                status="active",
            )
            order.acceptance_criteria = ["under budget", "delivered via CAP on Base"]
            session.add(order)
            session.flush()

        quote = QuoteRecord(
            agent_id=agent_id,
            agent_name=agent_name,
            price_usdc=amount_usdc,
            eta_seconds=60,
            confidence=0.9,
            is_base_agent=False,
            score=1.0,
            excluded=False,
        )
        quotes_json = json.dumps([quote.model_dump(mode="json")])
        output_ref = delivery_text or order_id
        output_hash = hashlib.sha256(output_ref.encode()).hexdigest()
        receipt_bundle = {
            "order_id": order_id,
            "winner_agent_id": agent_id,
            "amount_paid_usdc": str(amount_usdc),
            "tx_hash": tx_hash,
            "output_ref": output_ref,
            "output_hash": output_hash,
        }
        receipt_hash = hashlib.sha256(
            json.dumps(receipt_bundle, sort_keys=True).encode()
        ).hexdigest()

        status = "completed" if delivery_text else "paid_delivery_pending"
        run = Run(
            standing_order_id=order.id,
            started_at=_now(),
            finished_at=_now(),
            status=status,
            mode="live",
            quotes_json=quotes_json,
            winner_agent_id=agent_id,
            selection_reason=(
                f"real on-chain run: hired {agent_name} at {amount_usdc} USDC"
            ),
            amount_paid_usdc=amount_usdc,
            tx_hash=tx_hash,
            output_ref=output_ref,
            output_hash=output_hash,
            receipt_hash=receipt_hash,
            fallback_used=False,
        )
        session.add(run)
        order.total_spent_usdc = (order.total_spent_usdc or Decimal("0")) + amount_usdc
        session.add(order)
        session.commit()
        session.refresh(run)
        return (
            f"created: live run_id={run.id} order_id={order.id} "
            f"status={status} amount={amount_usdc} tx={tx_hash[:10]}..."
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real CAP order + persist it.")
    parser.add_argument("--agent-id", required=True, help="Counterparty agent_id (from live roster).")
    parser.add_argument("--requirements", default="{}", help="Verbatim requirements payload (JSON).")
    parser.add_argument("--so-name", default="Live Diversity Run", help="Standing order name to attach the run to.")
    parser.add_argument("--prompt", default="Produce a concise brief.", help="Task prompt.")
    parser.add_argument("--category", default="research", help="Task/order category.")
    parser.add_argument("--amount", default=None, help="Override recorded amount USDC (default: candidate listed price).")
    parser.add_argument("--probe-only", action="store_true", help="Run steps 1-3 only; abort BEFORE any spend.")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.is_live:
        log("SETUP", "CROON_CAP_MODE is not 'live'. Set CROON_CAP_MODE=live. Aborting.")
        return 1

    client = LiveCapClient(settings)

    # Resolve the target candidate from the configured live roster.
    agents = await client.discover_agents(None, 10)
    agent = next((a for a in agents if a.agent_id == args.agent_id), None)
    if agent is None:
        log("SETUP", f"agent_id {args.agent_id} not in CROON_LIVE_CANDIDATES_JSON. Aborting.")
        await client.close()
        return 1

    # Requirements come verbatim from the CLI so we control exactly what the
    # provider receives (Polymind -> '{}', Broker -> '{"market_id": "..."}').
    task = TaskSpec(
        task_prompt=args.prompt,
        category=args.category,
        acceptance_criteria=["under budget", "delivered via CAP"],
    )
    # Force the exact requirements payload by overriding the candidate template.
    agent.requirements_template = json.loads(args.requirements)

    listed = agent.listed_price_usdc or Decimal("0.10")
    amount = Decimal(str(args.amount)) if args.amount is not None else Decimal(str(listed))

    log("SETUP", f"target={agent.name} agent_id={agent.agent_id} service_id={agent.service_id}")
    log("SETUP", f"recorded amount={amount} USDC  probe_only={args.probe_only}")
    bal_before = await read_balance(client)
    log("BALANCE", f"USDC before: {bal_before}")

    from croo import ListOptions, NegotiateOrderRequest  # type: ignore

    # --- Step 1: negotiate_order (no spend) ---------------------------------
    requirements = client._build_requirements(agent, task)
    log("1-NEGOTIATE", f"requirements payload -> {requirements!r}")
    req = NegotiateOrderRequest(
        service_id=agent.service_id,
        requirements=requirements,
        metadata=json.dumps({"acceptance_criteria": task.acceptance_criteria}),
        requester_agent_id=client._requester_agent_id,
    )
    try:
        neg = await client._client.negotiate_order(req)
    except Exception as exc:  # noqa: BLE001
        log("1-NEGOTIATE", f"ERROR -> {type(exc).__name__}: {exc}  (no spend)")
        await client.close()
        return 1
    negotiation_id = str(_attr(neg, "negotiation_id", "id"))
    log("1-NEGOTIATE", f"OK -> negotiation_id={negotiation_id}  (no spend)")

    # --- Step 2: poll for provider ACCEPT (no spend) ------------------------
    status = "UNKNOWN"
    deadline = asyncio.get_event_loop().time() + ACCEPT_TIMEOUT_S
    last_status = None
    while asyncio.get_event_loop().time() < deadline:
        neg_now = await client._client.get_negotiation(negotiation_id)
        status = str(_attr(neg_now, "status", default="") or "").upper()
        if status != last_status:
            log("2-ACCEPT", f"negotiation status={status}")
            last_status = status
        if status == "ACCEPTED":
            break
        if status in {"REJECTED", "EXPIRED", "CANCELLED"}:
            reason = _attr(neg_now, "reason", "reject_reason", default="")
            log("2-ACCEPT", f"provider did NOT accept -> {status} reason={reason!r}  (no spend)")
            break
        await asyncio.sleep(POLL_INTERVAL_S)

    if status != "ACCEPTED":
        if status == "UNKNOWN":
            log("2-ACCEPT", f"TIMEOUT after {ACCEPT_TIMEOUT_S}s  (no spend)")
        log("RESULT", "Not accepted. Nothing paid.")
        await client.close()
        return 2

    # --- Step 3: resolve order_id + wait for 'created' (no spend) -----------
    order_id = None
    order_status = ""
    order_deadline = asyncio.get_event_loop().time() + ACCEPT_TIMEOUT_S
    while asyncio.get_event_loop().time() < order_deadline:
        opts = ListOptions(
            role="buyer",
            agent_id=client._requester_agent_id or None,
            page=1,
            page_size=50,
        )
        orders = await client._client.list_orders(opts)
        for order in orders or []:
            if str(_attr(order, "negotiation_id", default="")) == negotiation_id:
                order_id = str(_attr(order, "order_id", "id"))
                order_status = str(_attr(order, "status", default="") or "").lower()
                break
        log("3-ORDER", f"order_id={order_id} status={order_status!r}")
        if order_id and order_status == "created":
            break
        if order_status in {"rejected", "expired", "cancelled"}:
            log("3-ORDER", f"order not payable -> {order_status} (no spend)")
            order_id = None
            break
        await asyncio.sleep(POLL_INTERVAL_S)

    if not order_id or order_status != "created":
        log("3-ORDER", "order never became payable ('created') (no spend).")
        await client.close()
        return 2
    log("3-ORDER", f"OK -> order_id={order_id} status=created  (no spend)")

    if args.probe_only:
        log("PROBE", "probe-only: stopping BEFORE pay_order. Nothing spent.")
        await client.close()
        return 0

    # --- Step 4: pay_order (THE ONLY SPEND) ---------------------------------
    log("4-PAY", "calling pay_order (USDC on Base) ...")
    try:
        pay = await client._client.pay_order(order_id)
    except Exception as exc:  # noqa: BLE001
        log("4-PAY", f"ERROR -> {type(exc).__name__}: {exc}")
        await client.close()
        return 1
    tx_hash = _attr(pay, "tx_hash", default=None)
    if not tx_hash:
        order_obj = _attr(pay, "order", default=None)
        if order_obj is not None:
            tx_hash = _attr(order_obj, "pay_tx_hash", default=None)
    log("4-PAY", f"OK -> tx_hash={tx_hash}")
    if tx_hash:
        log("4-PAY", f"BaseScan: https://basescan.org/tx/{tx_hash}")
    if not tx_hash:
        log("4-PAY", "no tx_hash returned; cannot persist idempotently. Aborting persist.")
        await client.close()
        return 1

    # --- Step 5: get_delivery (best-effort; async) --------------------------
    delivery_text = ""
    try:
        delivery = await client.get_delivery(order_id)
        delivery_text = delivery.output_text or ""
        preview = delivery_text[:200]
        log("5-DELIVER", f"{'OK' if delivery_text else 'PENDING'} -> {preview!r}")
    except Exception as exc:  # noqa: BLE001
        log("5-DELIVER", f"pending/failed -> {type(exc).__name__}: {exc}")

    bal_after = await read_balance(client)
    log("BALANCE", f"USDC after: {bal_after}")

    # --- Persist (idempotent on tx_hash) ------------------------------------
    result = persist_live_run(
        order_id=order_id,
        tx_hash=str(tx_hash),
        agent_id=agent.agent_id,
        agent_name=agent.name,
        amount_usdc=amount,
        so_name=args.so_name,
        prompt=args.prompt,
        category=args.category,
        delivery_text=delivery_text,
    )
    log("PERSIST", result)
    log("RESULT", f"SUCCESS via {agent.name}. order_id={order_id} tx_hash={tx_hash}")
    await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
