"""Fetch provider service metadata to learn its accepted requirement schema.

SDK uses base path `<api>/backend/v1` (see croo/http_client.py). The negotiation
was rejected for 'unsupported requirement field "task_prompt"', so each service
validates `requirements` against its own field set. We probe the marketplace /
service endpoints under /backend/v1 to read that schema.

Read-only GETs. No spend.

    PYTHONPATH="$PWD" python scripts/http_service_schema.py
"""

from __future__ import annotations

import asyncio
import json

import httpx

from croon.config import get_settings

SERVICE_IDS = [
    "022c38ad-0be9-4ee1-8f76-d645cb182010",  # Polymarket Smart Wallet Tracker
    "23632a1d-d232-4a4e-b928-da30a73f1dcf",  # Polymarket Broker
    "bfddc0e8-fb82-4115-9370-ef235c8996db",  # Polymind
]


async def _try(client: httpx.AsyncClient, base: str, path: str, headers) -> dict | list | None:
    url = base + path
    try:
        r = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        print(f"  GET {path} -> ERR {exc!r}")
        return None
    print(f"  GET {path} -> {r.status_code}")
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            print(r.text[:800])
    return None


async def main() -> None:
    s = get_settings()
    base = s.croo_api_url.rstrip("/") + "/backend/v1"
    headers = {"X-SDK-Key": s.croo_sdk_key, "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=15) as client:
        # 1) Discovery / marketplace listing (learn shape + find our services).
        print("=== marketplace / services listing ===")
        for lp in ("/services", "/marketplace/services", "/marketplace",
                   "/agents", "/discover", "/store/services"):
            data = await _try(client, base, lp, headers)
            if data is not None:
                items = data if isinstance(data, list) else (
                    data.get("services") or data.get("data")
                    or data.get("items") or data.get("agents") or []
                )
                print(f"    -> {len(items)} item(s)")
                if items:
                    print("    -- first item shape --")
                    print(json.dumps(items[0], indent=2)[:2500])
                    # find our target services in this list
                    by_id = {
                        str(it.get("service_id") or it.get("id") or ""): it
                        for it in items
                    }
                    for svc_id in SERVICE_IDS:
                        hit = by_id.get(svc_id)
                        if hit:
                            print(f"\n    === matched {svc_id} ===")
                            print(json.dumps(hit, indent=2)[:2500])
                break

        # 2) Direct single-service fetch under /backend/v1.
        print("\n=== single-service fetch ===")
        for svc_id in SERVICE_IDS[:1]:
            for p in (f"/services/{svc_id}", f"/service/{svc_id}",
                      f"/marketplace/services/{svc_id}"):
                data = await _try(client, base, p, headers)
                if data:
                    print(json.dumps(data, indent=2)[:2500])
                    break


if __name__ == "__main__":
    asyncio.run(main())
