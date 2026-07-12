"""Full LIVE CAP chain test - step by step, with per-step logging.

Run:
  PYTHONPATH="$PWD" .venv/bin/python scripts/live_chain_test.py

Chain (each step logged; STOPS at the first failure):
  1. negotiate_order          -> negotiation_id           (no spend)
  2. poll get_negotiation     -> ACCEPTED / REJECTED / EXPIRED (no spend)
  3. list_orders match        -> order_id                 (no spend)
  4. pay_order                -> REAL Base tx_hash         (<-- the only spend)
  5. get_delivery             -> deliverable text

Spend safety: USDC only leaves the wallet at step 4 (pay_order). If the chain
fails at steps 1-3, NOTHING is spent - we log that explicitly. We also print the
wallet USDC balance before and after so any spend is visible.

This is intentionally OUTSIDE the engine so we can observe each raw SDK step and
capture the exact provider rejection reason if one occurs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

os.environ["CROON_CAP_MODE"] = "live"

from decimal import Decimal

from croon.config import get_settings
from croon.cap_client import LiveCapClient, _attr
from croon.schemas import TaskSpec


ACCEPT_TIMEOUT_S = 45
POLL_INTERVAL_S = 2.0


def log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


async def read_balance(client: LiveCapClient) -> str:
    """Best-effort USDC balance read (for spend visibility)."""
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


async def main() -> int:
    settings = get_settings()
    client = LiveCapClient(settings)

    agents = await client.discover_agents(None, 3)
    if not agents:
        log("SETUP", "No live candidates configured. Aborting.")
        await client.close()
        return 1

    task = TaskSpec(
        task_prompt="Produce a short wallet risk brief.",
        category="risk",
        acceptance_criteria=["clear", "concise"],
    )

    bal_before = await read_balance(client)
    log("BALANCE", f"USDC before: {bal_before}")

    # Try each candidate until one ACCEPTS (or we exhaust them).
    from croo import ListOptions, NegotiateOrderRequest  # type: ignore

    for idx, agent in enumerate(agents, 1):
        log("CANDIDATE", f"{idx}/{len(agents)} service_id={agent.service_id} "
                         f"name={agent.name}")

        # --- Step 1: negotiate_order (no spend) ---------------------------
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
            continue
        negotiation_id = str(_attr(neg, "negotiation_id", "id"))
        log("1-NEGOTIATE", f"OK -> negotiation_id={negotiation_id}  (no spend)")

        # --- Step 2: poll for provider ACCEPT (no spend) ------------------
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
                log("2-ACCEPT", f"provider did NOT accept -> {status} "
                               f"reason={reason!r}  (no spend)")
                break
            await asyncio.sleep(POLL_INTERVAL_S)

        if status != "ACCEPTED":
            if status == "UNKNOWN":
                log("2-ACCEPT", f"TIMEOUT after {ACCEPT_TIMEOUT_S}s waiting for "
                               f"accept  (no spend)")
            log("RESULT", f"Candidate {idx} unusable at ACCEPT step. Trying next.")
            continue

        # --- Step 3: resolve order_id AND wait for status 'created' -------
        # An accepted negotiation spawns an order in status "creating" while
        # its create_tx confirms on Base, THEN flips to "created" (payable).
        # pay_order() 400s (INVALID_STATUS) if called during "creating", so we
        # MUST poll past it before spending anything.
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
                    order_status = str(
                        _attr(order, "status", default="") or ""
                    ).lower()
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
            log("3-ORDER", "order never became payable ('created') "
                          "(no spend). Trying next candidate.")
            continue
        log("3-ORDER", f"OK -> order_id={order_id} status=created  (no spend)")

        # --- Step 4: pay_order (THE ONLY SPEND) ---------------------------
        log("4-PAY", "calling pay_order (USDC on Base) ...")
        try:
            pay = await client._client.pay_order(order_id)
        except Exception as exc:  # noqa: BLE001
            log("4-PAY", f"ERROR -> {type(exc).__name__}: {exc}")
            bal_after = await read_balance(client)
            log("BALANCE", f"USDC after: {bal_after}")
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

        # --- Step 5: get_delivery -----------------------------------------
        try:
            delivery = await client._client.get_delivery(order_id)
            text = _attr(delivery, "deliverable_text", "output_text", "text",
                         default="")
            preview = str(text)[:280]
            log("5-DELIVER", f"OK -> {preview!r}")
        except Exception as exc:  # noqa: BLE001
            log("5-DELIVER", f"pending/failed -> {type(exc).__name__}: {exc}")

        bal_after = await read_balance(client)
        log("BALANCE", f"USDC after: {bal_after}")
        log("RESULT", f"SUCCESS via candidate {idx} ({agent.name}). "
                     f"order_id={order_id} tx_hash={tx_hash}")
        await client.close()
        return 0

    # No candidate accepted.
    bal_after = await read_balance(client)
    log("BALANCE", f"USDC after: {bal_after}")
    log("RESULT", "No candidate ACCEPTED. Nothing paid. Consider Plan B "
                 "(input-free services or a mutual Discord partner).")
    await client.close()
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
