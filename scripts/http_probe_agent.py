"""READ-ONLY raw HTTP probe for an agent's service_id.

The installed `croo` SDK exposes NO agent/service getter (only order/negotiation/
delivery reads), so we probe the REST API directly with GET requests. This is
strictly read-only — no negotiation, no payment, no mutation. Auth via the same
X-SDK-Key header the SDK uses.

We try a handful of plausible, conventional REST paths and print status + body
for each so we can see which (if any) the API actually serves. We do NOT assume
any single path is correct.

Usage
-----
    .venv/bin/python scripts/http_probe_agent.py <agent_id>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from croon.config import get_settings

# Conventional read paths to probe. {id} is substituted with the agent_id.
CANDIDATE_PATHS = [
    "/agents/{id}",
    "/agents/{id}/services",
    "/agents/{id}/service",
    "/agent/{id}",
    "/services?agent_id={id}",
    "/services?agentId={id}",
    "/v1/agents/{id}",
    "/v1/agents/{id}/services",
    "/store/agents/{id}",
    "/store/agents/{id}/services",
]


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/http_probe_agent.py <agent_id>")
        return 2
    agent_id = sys.argv[1]

    settings = get_settings()
    if not settings.croo_sdk_key:
        print("❌ CROON_CROO_SDK_KEY not set — cannot query live API.")
        return 1

    base = settings.croo_api_url.rstrip("/")
    headers = {
        "X-SDK-Key": settings.croo_sdk_key,
        "Accept": "application/json",
    }

    print(f"Read-only HTTP probe against {base}")
    print(f"  agent_id = {agent_id}")
    print(f"  auth     = X-SDK-Key {settings.croo_sdk_key[:12]}…\n")

    found_service_ids: set[str] = set()

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=15) as client:
        for tmpl in CANDIDATE_PATHS:
            path = tmpl.format(id=agent_id)
            try:
                resp = await client.get(path)
            except Exception as exc:  # noqa: BLE001
                print(f"GET {path:40s} → ERROR {type(exc).__name__}: {exc}")
                continue

            status = resp.status_code
            body_preview = resp.text[:400].replace("\n", " ")
            marker = "✅" if status == 200 else ("·" if status in (401, 403) else " ")
            print(f"{marker} GET {path:40s} → {status}  {body_preview}")

            if status == 200:
                # Try to surface any service_id in the JSON.
                try:
                    data = resp.json()
                except Exception:
                    data = None
                for sid in _find_service_ids(data):
                    found_service_ids.add(sid)

    print()
    if found_service_ids:
        print("🎯 service_id(s) discovered:")
        for sid in sorted(found_service_ids):
            print(f"   {sid}")
    else:
        print("No service_id found via HTTP. Likely the Store service_id is only")
        print("visible in the Dashboard UI (not exposed on a public read endpoint).")
        print("Grab it from the Agent Store listing and paste it to me.")
    return 0


def _find_service_ids(obj: object) -> list[str]:
    """Recursively collect any 'service_id' / 'serviceId' values from JSON."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("service_id", "serviceId") and isinstance(v, str):
                out.append(v)
            else:
                out.extend(_find_service_ids(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_find_service_ids(item))
    return out


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
