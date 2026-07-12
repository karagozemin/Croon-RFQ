"""One-off: discover a provider service's accepted requirement schema.

Why: a live negotiation was REJECTED with
    reject_reason = 'invalid requirements: unsupported requirement field "task_prompt"'
So each service validates `requirements` against ITS OWN field schema. Our
generic {"task_prompt": ...} body doesn't match. This script inspects the SDK
for a service-describe method and prints whatever the provider advertises about
its expected requirement fields, so we can build a per-service payload.

Read-only. No negotiate/pay. Safe to run.

    PYTHONPATH="$PWD" python scripts/inspect_service_schema.py
"""

from __future__ import annotations

import asyncio
import json

import croo
from croo import AgentClient, Config

from croon.config import get_settings

# Services from CROON_LIVE_CANDIDATES_JSON (the 3 Polymarket providers).
SERVICE_IDS = [
    "022c38ad-0be9-4ee1-8f76-d645cb182010",  # Polymarket Smart Wallet Tracker
    "23632a1d-d232-4a4e-b928-da30a73f1dcf",  # Polymarket Broker
    "bfddc0e8-fb82-4115-9370-ef235c8996db",  # Polymind
]


def _dump_obj(obj, indent: str = "  ") -> None:
    """Print every non-callable public attr of an SDK object."""
    for attr in dir(obj):
        if attr.startswith("_"):
            continue
        try:
            val = getattr(obj, attr)
        except Exception as exc:  # noqa: BLE001
            print(f"{indent}{attr} = <err {exc}>")
            continue
        if callable(val):
            continue
        print(f"{indent}{attr} = {val!r}")


async def main() -> None:
    s = get_settings()
    client = AgentClient(Config(base_url=s.croo_api_url), s.croo_sdk_key)

    print("=== croo exports (service / requirement / schema / field) ===")
    for n in sorted(dir(croo)):
        low = n.lower()
        if any(k in low for k in ("service", "requirement", "schema", "field")):
            print("  ", n)

    print("\n=== AgentClient methods (service / get / list / describe) ===")
    svc_methods = [
        n for n in sorted(dir(client))
        if not n.startswith("_")
        and any(k in n.lower() for k in ("service", "get", "list", "describe"))
    ]
    for n in svc_methods:
        print("  ", n)

    # Try every plausible "get one service" method name.
    getter = None
    for name in ("get_service", "get_service_by_id", "describe_service",
                 "service", "fetch_service"):
        if hasattr(client, name):
            getter = getattr(client, name)
            print(f"\n>>> using service getter: client.{name}()")
            break

    for svc_id in SERVICE_IDS:
        print(f"\n================ SERVICE {svc_id} ================")
        if getter is None:
            print("  (no single-service getter found; trying list_services)")
            lister = getattr(client, "list_services", None)
            if lister:
                try:
                    services = await lister()
                    for svc in services or []:
                        sid = getattr(svc, "service_id", getattr(svc, "id", ""))
                        if str(sid) == svc_id:
                            _dump_obj(svc)
                            break
                except Exception as exc:  # noqa: BLE001
                    print("  list_services failed:", repr(exc))
            continue
        try:
            svc = await getter(svc_id)
            _dump_obj(svc)
            # If any attr looks like a JSON string schema, pretty-print it.
            for attr in ("requirements_schema", "requirement_schema",
                         "input_schema", "schema", "requirements"):
                raw = getattr(svc, attr, None)
                if isinstance(raw, str) and raw.strip().startswith(("{", "[")):
                    try:
                        print(f"\n  -- parsed {attr} --")
                        print(json.dumps(json.loads(raw), indent=2))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            print("  getter failed:", repr(exc))


if __name__ == "__main__":
    asyncio.run(main())
