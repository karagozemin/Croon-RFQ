"""Validate the live candidate roster BEFORE spending real USDC.

Why this exists
---------------
In live mode the mini-RFQ engine builds its candidate set from
CROON_LIVE_CANDIDATES_JSON (the SDK has NO discovery primitive - confirmed
against the live API). CROON will REALLY hire + pay the winner, so a bad roster
is expensive:

  * a candidate priced ABOVE budget_per_run is silently EXCLUDED by scoring
    (croon/scoring.py) -> wasted slot, and if ALL are excluded the run falls to
    the fallback provider every time (no A2A diversity -> lost hackathon points);
  * a candidate from the WRONG bucket (different input/output shape) makes
    scoring meaningless and risks paying for garbage output;
  * a missing service_id means hire_and_pay CANNOT negotiate it at all.

This script catches all of that OFFLINE, before any on-chain action. It makes
NO network calls and spends NOTHING.

Usage
-----
    # validate whatever is in .env
    .venv/bin/python scripts/validate_candidates.py

    # or validate a roster file / pasted JSON against an explicit budget
    .venv/bin/python scripts/validate_candidates.py --budget 0.10 --file roster.json
    .venv/bin/python scripts/validate_candidates.py --budget 0.10 --json '[{...}]'

Exit code is 0 only if the roster is safe to run live.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Make `croon` importable when run as `python scripts/validate_candidates.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from croon.config import get_settings

try:
    # Our OWN provider identities. Any candidate colliding with these is a
    # self-trade (CROON hiring itself) and triggers the anti-sybil flags
    # (<3 unique counterparties + concentrated self-trade). Base agents are
    # FALLBACK ONLY - never legitimate competitive candidates.
    from agents.provider import ALL_AGENTS

    _OWN_AGENT_IDS = set(ALL_AGENTS)
except Exception:  # noqa: BLE001 - agents pkg optional at validation time
    _OWN_AGENT_IDS = {"croon_recurring_rfq", "base_listing_copy", "base_gas_oracle"}

REQUIRED = ("agent_id", "name", "service_id")
RECOMMENDED = ("category", "listed_price_usdc", "listed_eta_seconds", "reputation")


# Guidance from the owner: keep candidates cheap so one run can't drain the
# wallet. Anything at/above this is flagged (not fatal, but loud).
CHEAP_CEILING = Decimal("0.10")


def _load_roster(args: argparse.Namespace) -> list[dict]:
    if args.json:
        return json.loads(args.json)
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    # Default: whatever the app would actually use in live mode.
    return get_settings().live_candidates


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the live RFQ candidate roster.")
    ap.add_argument("--budget", help="budget_per_run_usdc to check prices against")
    ap.add_argument("--file", help="path to a JSON roster file")
    ap.add_argument("--json", help="inline JSON roster string")
    args = ap.parse_args()

    try:
        roster = _load_roster(args)
    except json.JSONDecodeError as exc:
        print(f"[X] Roster is not valid JSON: {exc}")
        return 1

    if not isinstance(roster, list) or not roster:
        print("[X] Roster is empty. Set CROON_LIVE_CANDIDATES_JSON or pass --json/--file.")
        print("   Need 3-4 CHEAP, same-bucket, LIVE providers for A2A diversity.")
        return 1

    budget = _as_decimal(args.budget)

    # Our own provider service_ids are self-trade too (not just agent_ids).
    own_service_ids = set(get_settings().provider_service_map)

    errors: list[str] = []
    warnings: list[str] = []
    categories: set[str] = set()
    seen_agent_ids: set[str] = set()
    seen_service_ids: set[str] = set()
    under_budget = 0


    print(f"Validating {len(roster)} candidate(s)"
          + (f" against budget_per_run = {budget} USDC" if budget else "")
          + "\n")

    for i, c in enumerate(roster):
        tag = c.get("agent_id") or c.get("name") or f"#{i}"

        # Hard requirements ------------------------------------------------
        for field in REQUIRED:
            if not c.get(field):
                errors.append(f"[{tag}] missing required field '{field}'")

        aid = (c.get("agent_id") or "").strip()
        sid = (c.get("service_id") or "").strip()

        # Anti-self-trade guard -------------------------------------------
        # Candidates must be EXTERNAL third parties. Our own agents (main +
        # base/fallback) hiring themselves = concentrated self-trade + fewer
        # unique counterparties -> anti-sybil flags. Base agents stay fallback
        # ONLY; they must never appear in the competitive candidate pool.
        if aid and aid in _OWN_AGENT_IDS:
            errors.append(
                f"[{tag}] agent_id '{aid}' is one of OUR OWN agents - self-trade. "
                "Base agents are fallback-only, never RFQ candidates."
            )
        if sid and sid in own_service_ids:
            errors.append(
                f"[{tag}] service_id '{sid}' is one of OUR OWN provider services - "
                "self-trade. Use an EXTERNAL third-party agent."
            )

        # Duplicate guard: repeated ids fake diversity but are one counterparty.
        if aid:
            if aid in seen_agent_ids:
                errors.append(f"[{tag}] duplicate agent_id '{aid}' - not a distinct counterparty")
            seen_agent_ids.add(aid)
        if sid:
            if sid in seen_service_ids:
                errors.append(f"[{tag}] duplicate service_id '{sid}' - not a distinct counterparty")
            seen_service_ids.add(sid)


        # Recommended (scoring quality) ------------------------------------
        for field in RECOMMENDED:
            if c.get(field) in (None, ""):
                warnings.append(f"[{tag}] missing '{field}' (scoring will use a default)")

        price = _as_decimal(c.get("listed_price_usdc"))
        if price is not None:
            if price >= CHEAP_CEILING:
                warnings.append(
                    f"[{tag}] price {price} USDC >= {CHEAP_CEILING} - consider a cheaper "
                    "provider to protect the wallet"
                )
            if budget is not None:
                if price > budget:
                    errors.append(
                        f"[{tag}] price {price} > budget {budget} - this candidate is "
                        "EXCLUDED by scoring and just wastes an RFQ slot"
                    )
                else:
                    under_budget += 1

        cat = (c.get("category") or "").strip().lower()
        if cat:
            categories.add(cat)

    # Cross-candidate sanity ----------------------------------------------
    if len(categories) > 1:
        warnings.append(
            f"Mixed categories {sorted(categories)} - candidates should share ONE "
            "task bucket (same input/output shape) or scoring is meaningless"
        )
    if len(roster) < 3:
        warnings.append(
            f"Only {len(roster)} candidate(s) - 3-4 recommended for visible A2A "
            "diversity ('different winner each run')"
        )
    if budget is not None and under_budget < 2:
        errors.append(
            f"Only {under_budget} candidate(s) under budget - the RFQ needs >=2 real "
            "bidders or it collapses to the fallback provider every run"
        )

    # Report ---------------------------------------------------------------
    for w in warnings:
        print(f"[!]  {w}")
    for e in errors:
        print(f"[X] {e}")

    print()
    if errors:
        print(f"RESULT: [X] NOT safe to run live ({len(errors)} error(s), "
              f"{len(warnings)} warning(s)). Fix errors above.")
        return 1

    print(f"RESULT: [OK] Roster looks safe to run live "
          f"({len(warnings)} warning(s), {len(roster)} candidates, "
          f"{len(categories) or 'n/a'} category).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
