"""Main-service brokerage - fulfil an inbound PAID order for CROON RFQ itself.

When a buyer hires the MAIN CROON RFQ service on the Store (spec: the product is
sold, not just its base agents), the deliverable is NOT a static report. CROON
must honour its one-line pitch and RE-OPEN THE MARKET for that buyer:

    discover candidates -> quote -> score -> select winner under budget
    -> HIRE + PAY a downstream (child) agent via CAP
    -> collect the child's delivery
    -> return a PROOF-BUNDLED deliverable (every bidder, the winner, the child
       tx hash, the child output hash, and a receipt hash over the whole bundle)

This is the supply-side counterpart to the standing-order engine: same CapClient,
same scoring, same fallback discipline - but driven by an inbound paid order
instead of the scheduler, and it produces a text deliverable (what the buyer
receives) rather than a persisted Run row.

Two hard safety properties (both tested):

  * SPEND GUARD (CROON_MAX_CHILD_SPEND_USDC): the child settlement is capped
    OFF-CHAIN regardless of the buyer-supplied budget, so a single paid order can
    never drain CROON's agent wallet. The effective budget is
    min(buyer_budget, CROON_MAX_CHILD_SPEND_USDC).

  * IDEMPOTENCY (parent order id, DURABLE): paying a child spends real USDC, and
    a WS reconnect can replay ORDER_PAID - including ACROSS A PROVIDER RESTART.
    An in-memory cache cannot survive a restart, so idempotency is anchored in
    SQLite via the `BrokerageOrder` table. Each parent order transitions
    claimed -> settled (child paid, tx recorded) -> completed (deliverable
    stored). A replay at any stage NEVER pays a second child:
      - completed -> return the stored deliverable verbatim
      - settled   -> the child was already paid before a crash; re-fetch its
                     delivery and rebuild the deliverable WITHOUT paying again
      - claimed/failed -> no spend occurred; safe to (re)run the cycle
    An in-process per-parent lock additionally serialises concurrent replays
    within a single process.

All CAP interaction still goes exclusively through CapClient (spec sec.4/sec.13); this
module orchestrates, it never touches the SDK directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

from sqlmodel import Session

from croon import engine
from croon.cap_client import CapClient, build_cap_client
from croon.config import Settings, get_settings
from croon.db import engine as _db_engine
from croon.models import BrokerageOrder
from croon.schemas import TaskSpec

from croon.scoring import score_quotes

logger = logging.getLogger("croon.brokerage")

# agent_id of the MAIN service - excluded from its own candidate set so CROON can
# never hire itself in a loop. Imported lazily (agents.provider pulls in croon.*).
_MAIN_AGENT_ID = "croon_recurring_rfq"


# --- In-process serialisation (per parent order) ----------------------------
# The DURABLE idempotency key is the `BrokerageOrder` row (survives restarts).
# This lock only serialises concurrent replays WITHIN one process so two coroutines
# can't both read "not completed" and both start a child cycle before either
# commits its claim.
_LOCKS: dict[str, asyncio.Lock] = {}
_REGISTRY_LOCK = asyncio.Lock()


async def _lock_for(parent_order_id: str) -> asyncio.Lock:
    async with _REGISTRY_LOCK:
        lock = _LOCKS.get(parent_order_id)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[parent_order_id] = lock
        return lock


# --- Durable idempotency store (SQLite via BrokerageOrder) -------------------
def _load_order(parent_order_id: str) -> BrokerageOrder | None:
    with Session(_db_engine) as session:
        return session.get(BrokerageOrder, parent_order_id)


def _claim_order(parent_order_id: str) -> None:
    """Ensure a `claimed` row exists for this parent (no spend implied)."""
    with Session(_db_engine) as session:
        row = session.get(BrokerageOrder, parent_order_id)
        if row is None:
            row = BrokerageOrder(parent_order_id=parent_order_id, status="claimed")
        else:
            row.status = "claimed"
            row.updated_at = engine._now()
        session.add(row)
        session.commit()


def _mark_settled(
    parent_order_id: str, child_order_id: str, child_tx_hash: str | None
) -> None:
    """Persist that the child was HIRED + PAID, BEFORE assembling delivery.

    This is the crash-safety linchpin: once this commits, a replay can never pay
    a second child - it recovers via `settled` instead.
    """
    with Session(_db_engine) as session:
        row = session.get(BrokerageOrder, parent_order_id) or BrokerageOrder(
            parent_order_id=parent_order_id
        )
        row.status = "settled"
        row.child_order_id = child_order_id
        row.child_tx_hash = child_tx_hash
        row.updated_at = engine._now()
        session.add(row)
        session.commit()


def _mark_completed(parent_order_id: str, deliverable: str) -> None:
    with Session(_db_engine) as session:
        row = session.get(BrokerageOrder, parent_order_id) or BrokerageOrder(
            parent_order_id=parent_order_id
        )
        row.status = "completed"
        row.deliverable = deliverable
        row.updated_at = engine._now()
        session.add(row)
        session.commit()


def _mark_failed(parent_order_id: str) -> None:
    with Session(_db_engine) as session:
        row = session.get(BrokerageOrder, parent_order_id) or BrokerageOrder(
            parent_order_id=parent_order_id
        )
        row.status = "failed"
        row.updated_at = engine._now()
        session.add(row)
        session.commit()


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
    receipt). DURABLY idempotent on `parent_order_id`: the child is hired+paid at
    most once even across a provider restart; a replay returns the stored
    deliverable (or rebuilds it from an already-settled child without re-paying).
    """
    settings = settings or get_settings()
    params = dict(params or {})

    lock = await _lock_for(parent_order_id)
    async with lock:
        existing = _load_order(parent_order_id)

        if existing is not None and existing.status == "completed":
            logger.info(
                "brokerage: parent order=%s already completed; returning stored "
                "deliverable (no re-payment)",
                parent_order_id,
            )
            return existing.deliverable

        owns_cap = cap is None
        cap = cap or build_cap_client(settings)
        try:
            if existing is not None and existing.status == "settled":
                # Child was PAID before a crash/replay. Recover the deliverable
                # WITHOUT paying again.
                logger.warning(
                    "brokerage: parent order=%s found in 'settled' state "
                    "(child_order=%s tx=%s) - recovering deliverable without "
                    "re-payment",
                    parent_order_id,
                    existing.child_order_id,
                    existing.child_tx_hash,
                )
                deliverable = await _recover_settled(existing, task_prompt, cap)
                _mark_completed(parent_order_id, deliverable)
                return deliverable

            # No spend has occurred yet (new | claimed | failed): safe to run.
            _claim_order(parent_order_id)
            deliverable, spent = await _run_child_cycle(
                parent_order_id=parent_order_id,
                task_prompt=task_prompt,
                params=params,
                cap=cap,
                settings=settings,
            )
            if spent:
                _mark_completed(parent_order_id, deliverable)
            else:
                _mark_failed(parent_order_id)
            return deliverable
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


async def _recover_settled(
    row: BrokerageOrder, task_prompt: str, cap: CapClient
) -> str:
    """Rebuild a deliverable for a child that was already paid (crash recovery).

    We do NOT re-run discovery/scoring/payment - the spend already happened and
    is proven by `row.child_tx_hash`. We simply re-fetch the child's delivery and
    emit a truthful recovery deliverable anchored to the recorded settlement.
    """
    child_output = ""
    if row.child_order_id:
        try:
            delivery = await cap.get_delivery(row.child_order_id)
            child_output = delivery.output_text
        except Exception:  # noqa: BLE001 - delivery may be unavailable post-crash
            logger.debug(
                "brokerage: could not re-fetch delivery for %s",
                row.child_order_id,
                exc_info=True,
            )
    output_hash = engine._sha256(child_output) if child_output else ""
    return "\n".join(
        [
            "CROON RFQ - brokered fulfilment (recovered after interruption)",
            f"Task: {task_prompt}",
            "",
            "This order's child agent was already hired and PAID before an "
            "interruption; it was NOT paid again on recovery.",
            "",
            "On-chain settlement (child):",
            f"  child order id: {row.child_order_id}",
            f"  tx hash:        {row.child_tx_hash}",
            f"  output sha256:  {output_hash or '(delivery unavailable)'}",
            "",
            "Delivered work product:",
            child_output or "(delivery could not be re-fetched)",
        ]
    )


async def _run_child_cycle(
    *,
    parent_order_id: str,
    task_prompt: str,
    params: dict,
    cap: CapClient,
    settings: Settings,
) -> tuple[str, bool]:
    """Return (deliverable, spent). `spent` is True iff a child was hired+paid."""
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
            return (
                _no_provider_deliverable(
                    task_prompt=task_prompt,
                    category=category,
                    effective_budget=effective_budget,
                    reason=(
                        "no live bids and no capability-appropriate fallback "
                        f"provider for category '{category}' - no spend"
                    ),
                ),
                False,
            )
        emit("fallback_triggered", reason="no live bids")
        quotes = await engine._collect_quotes(
            cap, capable_fallbacks, task, settings.rfq_timeout_seconds, emit,
            is_fallback=True,
        )
        fallback_used = True
        if len(quotes) < 1:
            return (
                _no_provider_deliverable(
                    task_prompt=task_prompt,
                    category=category,
                    effective_budget=effective_budget,
                    reason="capability-matched fallback did not respond - no spend",
                ),
                False,
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
        return (
            _no_provider_deliverable(
                task_prompt=task_prompt,
                category=category,
                effective_budget=effective_budget,
                reason=(
                    "no eligible quote under the capped child budget "
                    f"({effective_budget} USDC) - no spend"
                ),
            ),
            False,
        )

    winner = selection.winner

    # --- Hire + PAY the winning child via CAP --------------------------------
    winner_info = engine._find_agent(candidates + fallback_roster, winner.agent_id)
    settlement = await cap.hire_and_pay(winner_info, task, winner.price_usdc)

    # DURABILITY: record the spend IMMEDIATELY, before we do anything else. If we
    # crash after this line, a replay recovers via the 'settled' path and never
    # pays a second child.
    _mark_settled(parent_order_id, settlement.order_id, settlement.tx_hash)

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

    return _render_deliverable(receipt, delivery.output_text), True


def _render_deliverable(receipt: dict, child_output: str) -> str:
    """Human-readable proof bundle + machine-readable receipt JSON."""
    lines = [
        "CROON RFQ - brokered fulfilment (market re-opened for this order)",
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
            "CROON RFQ - brokered fulfilment (market re-opened for this order)",
            f"Task: {task_prompt}",
            f"Category: {category or 'any'}",
            f"Child budget: {effective_budget} USDC",
            "",
            "No child settlement performed - budget protected.",
            f"Reason: {reason}",
        ]
    )


def _reset_idempotency_cache() -> None:
    """Test hook: clear the durable parent-order idempotency store + locks."""
    _LOCKS.clear()
    with Session(_db_engine) as session:
        for row in session.exec(
            __import__("sqlmodel").select(BrokerageOrder)
        ).all():
            session.delete(row)
        session.commit()
