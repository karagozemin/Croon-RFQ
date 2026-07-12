"""Probe the Broker's market_id field with REAL Polymarket ids (no spend).

Only negotiate_order + get_negotiation are called; pay_order is never invoked,
so this costs nothing. Finds which market_id format the Broker accepts.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

os.environ["CROON_CAP_MODE"] = "live"

from croon.config import get_settings
from croon.cap_client import LiveCapClient, _attr

BROKER_SERVICE_ID = "23632a1d-d232-4a4e-b928-da30a73f1dcf"
ACCEPT_TIMEOUT_S = 25
POLL_INTERVAL_S = 2.0

# Real live Polymarket market identifiers (from Gamma API).
GAMMA_ID = "540817"
CONDITION_ID = "0x1fad72fae204143ff1c3035e99e7c0f65ea8d5cd9bd1070987bd1a3316f772be"
SLUG = "new-rhianna-album-before-gta-vi-926"

CANDIDATES: list[tuple[str, dict]] = [
    ("market_id=gamma", {"market_id": GAMMA_ID}),
    ("market_id=condition", {"market_id": CONDITION_ID}),
    ("market_id=slug", {"market_id": SLUG}),
]


def log(msg: str) -> None:
    print(msg, flush=True)


async def probe_one(client, service_id, label, shape):
    from croo import NegotiateOrderRequest  # type: ignore

    req = NegotiateOrderRequest(
        service_id=service_id,
        requirements=json.dumps(shape),
        metadata=json.dumps({"probe": label}),
        requester_agent_id=client._requester_agent_id,
    )
    try:
        neg = await client._client.negotiate_order(req)
    except Exception as exc:  # noqa: BLE001
        return ("NEGOTIATE_ERR", f"{type(exc).__name__}: {exc}")
    negotiation_id = str(_attr(neg, "negotiation_id", "id"))

    deadline = asyncio.get_event_loop().time() + ACCEPT_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        neg_now = await client._client.get_negotiation(negotiation_id)
        status = str(_attr(neg_now, "status", default="") or "").upper()
        if status == "ACCEPTED":
            return ("ACCEPTED", negotiation_id)
        if status in {"REJECTED", "EXPIRED", "CANCELLED"}:
            reason = _attr(neg_now, "reason", "reject_reason", default="")
            return (status, str(reason))
        await asyncio.sleep(POLL_INTERVAL_S)
    return ("TIMEOUT", f"no accept within {ACCEPT_TIMEOUT_S}s")


async def main() -> int:
    settings = get_settings()
    client = LiveCapClient(settings)
    log(f"================ Polymarket Broker ({BROKER_SERVICE_ID}) ================")
    for label, shape in CANDIDATES:
        status, detail = await probe_one(client, BROKER_SERVICE_ID, label, shape)
        log(f"  [{label:22}] {json.dumps(shape)[:70]:70} -> {status}: {detail}")
        if status == "ACCEPTED":
            log(f"  >>> ACCEPTED shape: {json.dumps(shape)}")
            break
    await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
