"""LIVE readiness check — proves CROON can talk to CAP WITHOUT spending a cent.

This is the demo-day pre-flight. It answers ONE question:
    "If I flip CROON_CAP_MODE=live and press run-now, will it actually work?"
...while GUARANTEEING zero on-chain activity:

  * NO negotiate_order   (would create dangling on-chain state)
  * NO pay_order         (the only method that moves USDC)
  * NO provider worker   (we don't open the accept-loop here)

What it DOES do (all read-only / offline):
  1. Force live mode and construct LiveCapClient  -> validates SDK auth wiring
     (key present, Config/AgentClient build, requester agent id).
  2. Build the candidate roster from CROON_LIVE_CANDIDATES_JSON via the SDK
     adapter -> validates the roster is parseable and every candidate is
     hireable (has a service_id).
  3. One READ-ONLY API call: list_orders(role="buyer") -> proves the API key
     authenticates against the live CROO API and the endpoint is reachable.
  4. Validate the provider service map (owned services we could serve).

Prints a compact PASS/FAIL report and always reports "Transactions initiated: 0".
Exit code is non-zero if any invariant fails.

Usage:  PYTHONPATH="$PWD" python scripts/readiness_check.py
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

from croon.config import get_settings


CHECK = "\u2705"
CROSS = "\u274c"
WARN = "\u26a0\ufe0f"


def _line(ok: bool, label: str, detail: str = "") -> tuple[bool, str]:
    mark = CHECK if ok else CROSS
    return ok, f"{mark} {label}" + (f" — {detail}" if detail else "")


async def main() -> int:
    # get_settings() is lru_cached; build a fresh Settings so we can force live
    # mode for THIS process only, without mutating the user's .env on disk.
    from croon.config import Settings

    settings = Settings(cap_mode="live")  # type: ignore[call-arg]

    print("=== CROON RFQ — LIVE readiness check (NO SPEND) ===")
    print(f"    api_url : {settings.croo_api_url}")
    print(f"    ws_url  : {settings.croo_ws_url}")
    print(f"    rpc_url : {settings.base_rpc_url}")
    print(f"    usdc    : {settings.usdc_contract_address}")
    print("---")

    results: list[tuple[bool, str]] = []
    tx_initiated = 0  # invariant: this MUST stay 0 for the whole run.

    # --- 1. Auth wiring / client construction -----------------------------
    key = settings.croo_sdk_key or ""
    results.append(
        _line(
            bool(key) and key.startswith("croo_sk_"),
            "SDK key present",
            f"{key[:11]}…" if key else "MISSING — set CROON_CROO_SDK_KEY",
        )
    )

    client = None
    try:
        from croon.cap_client import LiveCapClient

        client = LiveCapClient(settings)
        results.append(_line(True, "LiveCapClient constructed", "auth/config wired"))
    except Exception as e:  # noqa: BLE001
        results.append(_line(False, "LiveCapClient constructed", repr(e)))

    # --- 2. Candidate roster is parseable + hireable ----------------------
    roster_ok = False
    if client is not None:
        try:
            agents = await client.discover_agents(category=None, limit=99)
            missing_svc = [a.agent_id for a in agents if not a.service_id]
            dup_agents = len({a.agent_id for a in agents}) != len(agents)
            dup_svcs = len({a.service_id for a in agents}) != len(agents)
            roster_ok = (
                len(agents) >= 1
                and not missing_svc
                and not dup_agents
                and not dup_svcs
            )
            detail = f"{len(agents)} candidate(s)"
            if missing_svc:
                detail += f"; MISSING service_id: {missing_svc}"
            if dup_agents:
                detail += "; DUPLICATE agent_id"
            if dup_svcs:
                detail += "; DUPLICATE service_id"
            results.append(_line(roster_ok, "Candidate roster valid", detail))

            # Anti-self-trade: none of our candidates may be CROON itself.
            self_id = settings.croo_requester_agent_id
            self_hit = [a.agent_id for a in agents if self_id and a.agent_id == self_id]
            results.append(
                _line(
                    not self_hit,
                    "Anti-self-trade",
                    "no candidate == requester agent"
                    if not self_hit
                    else f"SELF-TRADE: {self_hit}",
                )
            )
        except Exception as e:  # noqa: BLE001
            results.append(_line(False, "Candidate roster valid", repr(e)))

    # --- 3. READ-ONLY API probe (proves auth + reachability, no spend) ----
    if client is not None:
        try:
            from croo import ListOptions  # type: ignore

            opts = ListOptions(
                role="buyer",
                agent_id=settings.croo_requester_agent_id or None,
                page=1,
                page_size=1,
            )
            orders = await client._client.list_orders(opts)  # read-only
            n = len(orders or [])
            results.append(
                _line(True, "Live API auth (list_orders read-only)", f"reachable, {n} buyer order(s) visible")
            )
        except Exception as e:  # noqa: BLE001
            results.append(
                _line(False, "Live API auth (list_orders read-only)", repr(e))
            )

    # --- 4. Provider service map (supply side) ----------------------------
    smap = settings.provider_service_map
    results.append(
        _line(
            len(smap) >= 1,
            "Provider service map",
            f"{len(smap)} owned service(s) mapped" if smap else "empty (provider off)",
        )
    )

    # --- 5. Budget sanity (no spend, just arithmetic) ---------------------
    cheapest = None
    if client is not None:
        try:
            agents = await client.discover_agents(category=None, limit=99)
            prices = [a.listed_price_usdc for a in agents if a.listed_price_usdc is not None]
            if prices:
                cheapest = min(prices)
        except Exception:  # noqa: BLE001
            pass
    if cheapest is not None:
        cap = settings.max_child_spend_usdc
        ok = cheapest <= cap
        results.append(
            _line(
                ok,
                "Child-spend cap covers cheapest candidate",
                f"cheapest {cheapest} USDC <= cap {cap} USDC"
                if ok
                else f"cheapest {cheapest} > cap {cap} — raise CROON_MAX_CHILD_SPEND_USDC",
            )
        )

    # --- cleanup ----------------------------------------------------------
    if client is not None:
        await client.close()

    # --- report -----------------------------------------------------------
    print("Readiness checks:")
    for ok, text in results:
        print(f"  {text}")
    print("---")
    print(f"Transactions initiated: {tx_initiated}  (guaranteed zero — no negotiate/pay called)")

    all_ok = all(ok for ok, _ in results)
    print(f"\nProvider readiness: {'PASS ' + CHECK if all_ok else 'FAIL ' + CROSS}")
    if all_ok:
        print("Next: fund confirmed → do ONE controlled live run (run-now) to capture the first child tx.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
