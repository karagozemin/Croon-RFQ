"""Discover the ACCEPTED requirements OBJECT shape for live services.

Run:
  PYTHONPATH="$PWD" .venv/bin/python scripts/probe_requirements_shape.py

Why: live providers reject our requirements with:
  - string payload -> "requirements must be a JSON object" / "cannot unmarshal
    string into Go value of type map[string]interface {}"
  - {"task_prompt": ...} -> "unsupported requirement field task_prompt"
So requirements MUST be a JSON object, but with provider-specific field names.
There is NO service-describe endpoint, so we discover the shape empirically:
send several candidate objects, poll the negotiation to ACCEPTED/REJECTED, and
record the exact reject reason. NEGOTIATE + poll costs NOTHING (spend only at
pay_order, which we never call here).

The reject reason often names the missing/required field (e.g. "missing
required field X"), which tells us the correct shape.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

os.environ["CROON_CAP_MODE"] = "live"

from croon.config import get_settings
from croon.cap_client import LiveCapClient, _attr
from croon.schemas import TaskSpec


ACCEPT_TIMEOUT_S = 25
POLL_INTERVAL_S = 2.0

PROMPT = "Produce a short wallet risk brief."

# Candidate requirement OBJECTS to probe (all valid JSON objects).
# Ordered from most-likely generic to more specific field names.
CANDIDATE_SHAPES: list[tuple[str, dict]] = [
    ("empty", {}),
    ("prompt", {"prompt": PROMPT}),
    ("input", {"input": PROMPT}),
    ("query", {"query": PROMPT}),
    ("message", {"message": PROMPT}),
    ("text", {"text": PROMPT}),
    ("description", {"description": PROMPT}),
    ("task", {"task": PROMPT}),
    ("instructions", {"instructions": PROMPT}),
    ("request", {"request": PROMPT}),
    # Polymarket-flavored guesses (these services are Polymarket trackers/brokers)
    ("wallet_address", {"wallet_address": "0x0000000000000000000000000000000000000000"}),
    ("address", {"address": "0x0000000000000000000000000000000000000000"}),
    ("market", {"market": PROMPT}),
]


def log(msg: str) -> None:
    print(msg, flush=True)


async def probe_one(client, agent, label: str, shape: dict) -> tuple[str, str]:
    """Send one requirements object; return (status, reason). No spend."""
    from croo import NegotiateOrderRequest  # type: ignore

    req = NegotiateOrderRequest(
        service_id=agent.service_id,
        requirements=json.dumps(shape),
        metadata=json.dumps({"probe": label}),
        requester_agent_id=client._requester_agent_id,
    )
    try:
        neg = await client._client.negotiate_order(req)
    except Exception as exc:  # noqa: BLE001
        return ("NEGOTIATE_ERR", f"{type(exc).__name__}: {exc}")
    negotiation_id = str(_attr(neg, "negotiation_id", "id"))

    status = "UNKNOWN"
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

    agents = await client.discover_agents(None, 3)
    if not agents:
        log("No live candidates configured.")
        await client.close()
        return 1

    for agent in agents:
        log("")
        log(f"================ {agent.name} ({agent.service_id}) ================")
        for label, shape in CANDIDATE_SHAPES:
            status, detail = await probe_one(client, agent, label, shape)
            log(f"  [{label:16}] {json.dumps(shape)[:60]:60} -> {status}: {detail}")
            if status == "ACCEPTED":
                log(f"  >>> ACCEPTED shape found: {json.dumps(shape)}")
                log(f"  >>> negotiation_id={detail}")
                # Stop at first accepting shape for this agent.
                break

    await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
