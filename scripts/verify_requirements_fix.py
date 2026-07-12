"""Verify the CAP `requirements` payload fix against the LIVE API.

Run:  PYTHONPATH="$PWD" .venv/bin/python scripts/verify_requirements_fix.py

Background: the API rejected object-shaped requirements with
INVALID_PARAMETERS ("unsupported requirement field ..."). The SDK exposes NO
service-describe endpoint (only /orders/* and /objects/*), so the schema can't
be introspected. Our fix sends a SCHEMA-AGNOSTIC bare JSON string by default
(json.dumps(prompt) == '"prompt"'): valid JSON, zero object fields to reject.

This script forces live mode, builds the payload our LiveCapClient would send,
and attempts a real negotiate_order to confirm the API accepts it.
"""

from __future__ import annotations

import asyncio
import json
import os

os.environ["CROON_CAP_MODE"] = "live"

from croon.config import get_settings
from croon.cap_client import LiveCapClient
from croon.schemas import TaskSpec


async def main() -> None:
    settings = get_settings()
    client = LiveCapClient(settings)

    agents = await client.discover_agents(None, 3)
    if not agents:
        print("No live candidates configured (CROON_LIVE_CANDIDATES_JSON).")
        await client.close()
        return

    agent = agents[0]
    task = TaskSpec(
        task_prompt="Produce a short wallet risk brief.",
        acceptance_criteria=["clear", "concise"],
    )

    requirements = client._build_requirements(agent, task)
    print("candidate service_id :", agent.service_id)
    print("requirements payload :", repr(requirements))

    from croo import NegotiateOrderRequest  # type: ignore

    req = NegotiateOrderRequest(
        service_id=agent.service_id,
        requirements=requirements,
        metadata=json.dumps({"acceptance_criteria": task.acceptance_criteria}),
        requester_agent_id=client._requester_agent_id,
    )

    try:
        neg = await client._client.negotiate_order(req)
        neg_id = getattr(neg, "negotiation_id", None) or getattr(neg, "id", neg)
        print("NEGOTIATE OK -> negotiation_id:", neg_id)
    except Exception as exc:  # noqa: BLE001 - we want to see the real error
        print("NEGOTIATE ERR ->", type(exc).__name__, exc)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
