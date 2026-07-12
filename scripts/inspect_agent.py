"""READ-ONLY probe: resolve a CROO agent's service_id from the live API.

Spends NOTHING. Makes only GET-style SDK calls (agent/service lookups). No
negotiation, no payment, no state mutation.

Why introspect first
--------------------
We do NOT hardcode REST paths (spec: don't invent endpoints). Instead we ask the
installed `croo` SDK which read methods it actually exposes, then call the most
specific agent/service getter for the requested agent_id and print the raw
service_id(s) found.

Usage
-----
    .venv/bin/python scripts/inspect_agent.py <agent_id>
    .venv/bin/python scripts/inspect_agent.py 2c61c35b-57ad-4082-92f7-bde5591cc0c2
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from croon.config import get_settings


def _list_read_methods(client: object) -> list[str]:
    """Public coroutine/method names that look like reads (get/list/fetch)."""
    names = []
    for name in dir(client):
        if name.startswith("_"):
            continue
        attr = getattr(client, name, None)
        if callable(attr) and any(
            name.startswith(p) for p in ("get", "list", "fetch", "find", "search")
        ):
            names.append(name)
    return sorted(names)


async def _try_call(client: object, method_name: str, agent_id: str):
    """Best-effort call a single-arg getter with the agent_id. Read-only."""
    method = getattr(client, method_name, None)
    if method is None:
        return None
    try:
        sig = inspect.signature(method)
    except (ValueError, TypeError):
        sig = None
    try:
        result = method(agent_id)
        if inspect.isawaitable(result):
            result = await result
        return result
    except Exception as exc:  # noqa: BLE001 - probe, never fatal
        return f"(call failed: {type(exc).__name__}: {exc})"


def _dump(obj: object, indent: str = "  ") -> None:
    """Pretty-print an SDK object, surfacing any service_id-like fields."""
    if obj is None:
        print(f"{indent}None")
        return
    if isinstance(obj, (list, tuple)):
        print(f"{indent}<{len(obj)} item(s)>")
        for i, item in enumerate(obj):
            print(f"{indent}[{i}]")
            _dump(item, indent + "  ")
        return
    # object with attributes
    interesting = ("service_id", "id", "agent_id", "name", "price", "category",
                   "status", "services")
    printed = False
    for field in interesting:
        if isinstance(obj, dict) and field in obj:
            print(f"{indent}{field} = {obj[field]!r}")
            printed = True
        elif hasattr(obj, field):
            print(f"{indent}{field} = {getattr(obj, field)!r}")
            printed = True
    if not printed:
        print(f"{indent}{obj!r}")


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/inspect_agent.py <agent_id>")
        return 2
    agent_id = sys.argv[1]

    settings = get_settings()
    if not settings.croo_sdk_key:
        print("[X] CROON_CROO_SDK_KEY not set - cannot query live API.")
        return 1

    from croo import AgentClient, Config  # type: ignore

    config = Config(
        base_url=settings.croo_api_url,
        ws_url=settings.croo_ws_url,
        rpc_url=settings.base_rpc_url,
    )
    client = AgentClient(config, settings.croo_sdk_key)

    print(f"Querying live CROO API for agent_id={agent_id}")
    print(f"  base_url = {settings.croo_api_url}\n")

    read_methods = _list_read_methods(client)
    print("Available read-only SDK methods:")
    for m in read_methods:
        try:
            sig = inspect.signature(getattr(client, m))
        except (ValueError, TypeError):
            sig = "(?)"
        print(f"  - {m}{sig}")
    print()

    # Prefer the most specific agent/service getters.
    preferred = [
        m for m in read_methods
        if any(k in m for k in ("agent", "service"))
        and any(m.startswith(p) for p in ("get", "list", "fetch", "find"))
    ]
    if not preferred:
        print("[!]  No agent/service getter found in this SDK version.")
        print("   Full method list above - tell me which one to use.")
        try:
            await client.close()
        except Exception:
            pass
        return 0

    for m in preferred:
        print(f"-- Trying {m}({agent_id!r}) --")
        result = await _try_call(client, m, agent_id)
        _dump(result)
        print()

    try:
        await client.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
