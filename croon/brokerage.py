"""Main-service brokerage — fulfil an inbound PAID order for CROON RFQ itself.

When a buyer hires the MAIN CROON RFQ service on the Store (spec: the product is
sold, not just its base agents), the deliverable is NOT a static report. CROON
must honour its one-line pitch and RE-OPEN THE MARKET for that buyer:

    discover candidates -> quote -> score -> select winner under budget
    -> HIRE + PAY a downstream (child) agent via CAP
    -> collect the child's delivery
    -> return a PROOF-BUNDLED deliverable (every bidder, the winner, the child
       tx hash, the child output hash, and a receipt hash over the whole bundle)

This is the supply-side counterpart to the standing-order engine: same CapClient,
same scoring, same fallback discipline — but driven by an inbound paid order
instead of the scheduler, and it produces a text deliverable (what the buyer
receives) rather than a persisted Run row.

Two hard safety properties (both tested):

  * SPEND GUARD (CROON_MAX_CHILD_SPEND_USDC): the child settlement is capped
    OFF-CHAIN regardless of the buyer-supplied budget, so a single paid order can
    never drain CROON's agent wallet. The effective budget is
    min(buyer_budget, CROON_MAX_CHILD_SPEND_USDC).

  * IDEMPOTENCY (parent order id): paying a child spends real USDC, and a WS
    reconnect can replay ORDER_PAID. We key on the PARENT order id: the child
    cycle runs at most once per parent order; a replay returns the cached
    deliverable without paying again.

All CAP interaction still goes exclusively through CapClient (spec §4/§13); this
module orchestrates, it never touches the SDK directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

from croon import engine
from croon.cap_client import CapClient, build_cap_client
from croon.config import Settings, get_settings
from croon.schemas import TaskSpec

from croon.scoring import score_quotes

logger = logging.getLogger("croon.brokerage")

# agent_id of the MAIN service — excluded from its own candidate set so CROON can
# never hire itself in a loop. Imported lazily (agents.provider pulls in croon.*).
_MAIN_AGENT_ID = "croon_recurring_rfq"


# --- Idempotency (parent order id -> completed deliverable) ------------------
# In-memory is sufficient: the provider worker is a single process, and the
# point is to avoid double-paying a child across ORDER_PAID replays within that
# process. A per-parent lock serialises concurrent replays; the results cache
# short-circuits any later duplicate.
_RESULTS: dict[str, str] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_REGISTRY_LOCK = asyncio.Lock()


async def _lock_for(parent_order_id: str) -> asyncio.Lock:
    async with _REGISTRY_LOCK:
        lock = _LOCKS.get(parent_order_id)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[parent_order_id] = lock
        return lock


def _params_budget(params: dict, settings: Settings) -> Decimal:
    """Buyer-requested per-order budget, defaulting to the spend guard."""
    raw = params.get("budget_per_run_usdc", params.get("budget_usdc"))
    if raw is None:
        return settings.max_child_spend_usdc
    try:
        return Decimal(str(raw))
    except (ArithmeticError, ValueError, TypeError):
        return settings.max_child_spend_usdc


async def execute_main_brokerage_order(
    *,
    parent_order_id: str,
    task_prompt: str,
    params: dict | None = None,
    cap: CapClient | None = None,
    settings: Settings | None = None,
) -> str:
    """Run ONE competitive child cycle for an inbound paid MAIN order.

    Returns the buyer-facing deliverable text (a proof-bundled brokerage
    receipt). Idempotent on `parent_order_id`: the child is hired+paid at most
    once; a replay returns the cached deliverable.
    """
    settings = settings or get_settings()
    params = dict(params or {})

    lock = await _lock_for(parent_order_id)
    async with lock:
        cached = _RESULTS.get(parent_order_id)
        if cached is not None:
            logger.info(
                "brokerage: parent order=%s already fulfilled; returning cached "
                "deliverable (no re-payment)",
                parent_order_id,
            )
            return cached

        # Build a CapClient if the caller didn't inject one. If we built it and
        # it owns network resources, close it when done.
        owns_cap = cap is None
        cap = cap or build_cap_client(settings)
        try:
            deliverable = await _run_child_cycle(
                parent_order_id=parent_order_id,
                task_prompt=task_prompt,
                params=params,
                cap=cap,
                settings=settings,
            )
        finally:
            if owns_cap:
                close = getattr(cap, "close", None)
                if callable(close):
                    try:
                        await close()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        logger.debug(
                            "brokerage: cap close failed", exc_info=True
                        )

        _RESULTS[parent_order_id] = deliverable
        return deliverable


async def _run_child_cycle(
    *,
    parent_order_id: str,
    task_prompt: str,
    params: dict,
    cap: CapClient,
    settings: Settings,
) -> str:
    category = params.get("category")
    max_agents = int(params.get("max_agents_to_query", 3) or 3)
    criteria = list(params.get("acceptance_criteria") or [])

    # SPEND GUARD: never spend more than CROON_MAX_CHILD_SPEND_USDC on the child,
    # no matter how large a budget the buyer supplied.
    requested = _params_budget(params, settings)
    effective_budget = min(requested, settings.max_child_spend_usdc)
    capped = effective_budget < requested

    task = TaskSpec(
        task_prompt=task_prompt,
        category=category,
        acceptance_criteria=criteria,
    )

    def emit(event_type: str, **data) -> None:
        logger.info("brokerage[%s] %s: %s", parent_order_id, event_type, data)

    # --- Re-open the market: discover candidates (exclude self) --------------
    candidates = await cap.discover_agents(category=category, limit=max_agents)
    candidates = [c for c in candidates if c.agent_id != _MAIN_AGENT_ID]

    fallback_roster = engine._fallback_agents(settings)
    capable_fallbacks = engine._capable_fallbacks(fallback_roster, category)

    # --- Collect competitive quotes under the RFQ timeout --------------------
    quotes = await engine._collect_quotes(
        cap, candidates, task, settings.rfq_timeout_seconds, emit
    )
    fallback_used = False

    if len(quotes) < 1:
        if not capable_fallbacks:
            return _no_provider_deliverable(
                task_prompt=task_prompt,
                category=category,
                effective_budget=effective_budget,
                reason=(
                    "no live bids and no capability-appropriate fallback "
                    f"provider for category '{category}' — no spend"
                ),
            )
        emit("fallback_triggered", reason="no live bids")
        quotes = await engine._collect_quotes(
            cap, capable_fallbacks, task, settings.rfq_timeout_seconds, emit,
            is_fallback=True,
        )
        fallback_used = True
        if len(quotes) < 1:
            return _no_provider_deliverable(
                task_prompt=task_prompt,
                category=category,
                effective_budget=effective_budget,
                reason="capability-matched fallback did not respond — no spend",
            )

    # --- Score + select under the (capped) budget ----------------------------
    selection = score_quotes(quotes, effective_budget)

    if selection.winner is None and not fallback_used and capable_fallbacks:
        emit("fallback_triggered", reason="all quotes over capped budget")
        fb_quotes = await engine._collect_quotes(
            cap, capable_fallbacks, task, settings.rfq_timeout_seconds, emit,
            is_fallback=True,
        )
        fallback_used = True
        selection = score_quotes(fb_quotes, effective_budget)

    if selection.winner is None:
        return _no_provider_deliverable(
            task_prompt=task_prompt,
            category=category,
            effective_budget=effective_budget,
            reason=(
                "no eligible quote under the capped child budget "
                f"({effective_budget} USDC) — no spend"
            ),
        )

    winner = selection.winner

    # --- Hire + PAY the winning child via CAP --------------------------------
    winner_info = engine._find_agent(candidates + fallback_roster, winner.agent_id)
    settlement = await cap.hire_and_pay(winner_info, task, winner.price_usdc)
    delivery = await cap.get_delivery(settlement.order_id)
    output_hash = engine._sha256(delivery.output_text)

    # --- Assemble the proof-bundled deliverable ------------------------------
    receipt = {
        "kind": "croon_rfq_brokerage_receipt",
        "parent_order_id": parent_order_id,
        "task_prompt": task_prompt,
        "category": category,
        "requested_budget_usdc": str(requested),
        "effective_child_budget_usdc": str(effective_budget),
        "spend_guard_applied": capped,
        "quotes": [engine._qr_json(r) for r in selection.scored_quotes],
        "winner_agent_id": winner.agent_id,
        "winner_agent_name": winner.agent_name,
        "selection_reason": selection.reason,
        "child_order_id": settlement.order_id,
        "child_tx_hash": settlement.tx_hash,
        "amount_paid_usdc": str(settlement.amount_paid_usdc),
        "output_hash": output_hash,
        "fallback_used": fallback_used,
        "generated_at": engine._now().isoformat(),
    }
    receipt_hash = engine._sha256(json.dumps(receipt, sort_keys=True, default=str))
    receipt["receipt_hash"] = receipt_hash

    emit(
        "child_settled",
        winner=winner.agent_id,
        tx_hash=settlement.tx_hash,
        amount=str(settlement.amount_paid_usdc),
        receipt_hash=receipt_hash,
    )

    return _render_deliverable(receipt, delivery.output_text)


def _render_deliverable(receipt: dict, child_output: str) -> str:
    """Human-readable proof bundle + machine-readable receipt JSON."""
    lines = [
        "CROON RFQ — brokered fulfilment (market re-opened for this order)",
        f"Task: {receipt['task_prompt']}",
        f"Category: {receipt['category'] or 'any'}",
        (
            f"Child budget: {receipt['effective_child_budget_usdc']} USDC"
            + (
                " (capped by spend guard from "
                f"{receipt['requested_budget_usdc']})"
                if receipt["spend_guard_applied"]
                else ""
            )
        ),
        "",
        "Competitive quotes:",
    ]
    for r in receipt["quotes"]:
        if r["excluded"]:
            mark = f"EXCLUDED ({r['exclusion_reason']})"
        else:
            mark = f"score {r['score']:.3f}"
        lines.append(
            f"  - {r['agent_name']}: {r['price_usdc']} USDC / "
            f"ETA {r['eta_seconds']}s / conf {r['confidence']} -> {mark}"
        )
    lines += [
        "",
        f"Winner: {receipt['winner_agent_name']} "
        f"@ {receipt['amount_paid_usdc']} USDC",
        f"Why: {receipt['selection_reason']}",
        f"Fallback used: {receipt['fallback_used']}",
        "",
        "On-chain settlement (child):",
        f"  child order id: {receipt['child_order_id']}",
        f"  tx hash:        {receipt['child_tx_hash']}",
        f"  output sha256:  {receipt['output_hash']}",
        f"  receipt hash:   {receipt['receipt_hash']}",
        "",
        "Delivered work product:",
        child_output,
        "",
        "--- machine-readable receipt ---",
        json.dumps(receipt, sort_keys=True, default=str),
    ]
    return "\n".join(lines)


def _no_provider_deliverable(
    *,
    task_prompt: str,
    category: str | None,
    effective_budget: Decimal,
    reason: str,
) -> str:
    """Deliverable when the market yields no eligible provider (NO spend)."""
    return "\n".join(
        [
            "CROON RFQ — brokered fulfilment (market re-opened for this order)",
            f"Task: {task_prompt}",
            f"Category: {category or 'any'}",
            f"Child budget: {effective_budget} USDC",
            "",
            "No child settlement performed — budget protected.",
            f"Reason: {reason}",
        ]
    )


def _reset_idempotency_cache() -> None:
    """Test hook: clear the parent-order idempotency cache."""
    _RESULTS.clear()
    _LOCKS.clear()
