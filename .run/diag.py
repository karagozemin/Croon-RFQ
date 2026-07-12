"""Local-only diagnostic: inspect the live candidate roster + spend guard.

Answers the '3 truly-independent providers' question BEFORE spending USDC:
  - how many candidates are configured
  - are their agent_ids / service_ids unique (not the same team)
  - do they have a listed price (else request_quote() skips them)
  - is the price under the spend guard
No network, no chain writes. Just reads .env.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in Path(".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def main() -> None:
    env = load_env()
    print("CAP_MODE            :", env.get("CROON_CAP_MODE"))
    print("MAX_CHILD_SPEND     :", env.get("CROON_MAX_CHILD_SPEND_USDC"))
    print("PROVIDER_ENABLED    :", env.get("CROON_PROVIDER_ENABLED"))
    print("FALLBACK_SERVICE_ID :", env.get("CROON_FALLBACK_SERVICE_ID"))
    print("PROVIDER_SERVICE_MAP:", env.get("CROON_PROVIDER_SERVICE_MAP_JSON"))
    print()

    guard = Decimal(env.get("CROON_MAX_CHILD_SPEND_USDC", "0.50"))
    raw = env.get("CROON_LIVE_CANDIDATES_JSON", "[]")
    try:
        cands = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        print("CANDIDATES JSON PARSE ERROR:", e)
        cands = []

    print(f"=== LIVE CANDIDATES (n={len(cands)}) ===")
    seen_service: set = set()
    seen_agent: set = set()
    usable = 0
    for i, c in enumerate(cands, 1):
        sid = c.get("service_id")
        aid = c.get("agent_id")
        price = c.get("listed_price_usdc")
        dup_s = " <DUP-SERVICE!>" if sid in seen_service else ""
        dup_a = " <DUP-AGENT!>" if aid in seen_agent else ""
        seen_service.add(sid)
        seen_agent.add(aid)

        flags = []
        if price is None:
            flags.append("NO-PRICE(skip in quote)")
        else:
            try:
                if Decimal(str(price)) > guard:
                    flags.append(f"OVER-GUARD(>{guard})")
                else:
                    usable += 1
            except Exception:  # noqa: BLE001
                flags.append("BAD-PRICE")
        if not sid:
            flags.append("NO-SERVICE-ID(cannot hire)")
        flag_str = ("  [" + ", ".join(flags) + "]") if flags else "  [OK]"

        print(f"{i}. agent={aid}{dup_a}")
        print(f"   name  = {c.get('name')}   cat={c.get('category')}")
        print(f"   svc   = {sid}{dup_s}")
        print(f"   price = {price}  eta={c.get('listed_eta_seconds')}  "
              f"rep={c.get('reputation')}{flag_str}")
        rt = c.get("requirements_template")
        if rt is not None:
            print(f"   req_template = {rt}")
    print()
    print(f"unique agent_ids   : {len(seen_agent)}")
    print(f"unique service_ids : {len(seen_service)}")
    print(f"usable (priced, <= guard): {usable}")
    print()
    verdict = ("PASS" if len(seen_agent) >= 3 and len(seen_service) >= 3
               and usable >= 3 else "NEEDS ATTENTION")
    print(f">>> 3-independent-provider readiness: {verdict}")


if __name__ == "__main__":
    main()
