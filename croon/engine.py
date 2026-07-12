"""Mini-RFQ engine - Layers C + D (the differentiator + settlement).

On EVERY run we RE-OPEN THE MARKET (this is why it's not a cron job):
  1. discover 2-3 candidate agents            (Layer C)
  2. request a quote from each, in parallel    (Layer C)
  3. score + select winner under budget        (Layer C, scoring.py)
  4. hire winner via CAP + pay USDC on Base    (Layer D, CapClient)
  5. fetch delivery, assemble a signed receipt (Layer D)
  6. append the Run to history                 (Layer A)

Fallback (spec sec.7): if < 1 valid quote returns within the RFQ timeout, we do
NOT crash - we route to one of OUR base agents, hire+pay it normally, and mark
`fallback_used = True`. This is budget-protecting risk management AND makes the
base agents part of the product.

Live progress is published to an in-memory event bus so the demo UI can render
the mini-RFQ moment (quotes arriving -> scoring -> winner -> payment -> receipt).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Session


from croon.cap_client import CapClient
from croon.config import get_settings
from croon.events import EVENT_BUS
from croon.models import Run, StandingOrder
from croon.schemas import AgentInfo, Quote, QuoteRecord, TaskSpec
from croon.scoring import score_quotes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# Base-agent fallback roster for MOCK mode (ids match MockCapClient).
# Ordered by preference; first available wins.
_MOCK_FALLBACK_AGENTS = [
    AgentInfo(
        agent_id="base_listing_copy",
        name="CROON Listing Copy Agent",
        category="research",
        listed_price_usdc=Decimal("0.05"),
        reputation=0.60,
        is_base_agent=True,
    ),
    AgentInfo(
        agent_id="base_gas_oracle",
        name="CROON Base Gas Oracle",
        category="infra",
        listed_price_usdc=Decimal("0.01"),
        reputation=0.55,
        is_base_agent=True,
    ),
]


# Which task categories each base fallback agent can ACTUALLY fulfill.
# A fallback provider must be capability-appropriate: routing a "risk brief" to
# a gas oracle is wrong-capability routing and would (rightly) lose execution
# points. If nothing here matches the task category we refuse and spend nothing.
_FALLBACK_CAPABILITIES: dict[str, set[str]] = {
    "base_listing_copy": {
        "research", "listing", "content", "copywriting", "marketing",
    },
    "base_gas_oracle": {"infra", "gas", "cost", "oracle"},
}


def _capable_fallbacks(
    roster: list[AgentInfo], category: str | None
) -> list[AgentInfo]:
    """Filter the fallback roster to providers that can DO this task.

    Matching is by task category against each provider's declared capabilities
    (or its own category as a fallback). If `category` is None we cannot verify
    capability, so we refuse to guess (returns []). The caller then marks the
    run `no_provider_available` and spends no budget (sec.7 risk management).
    """
    if not category:
        return []
    cat = category.strip().lower()
    capable: list[AgentInfo] = []
    for a in roster:
        caps = _FALLBACK_CAPABILITIES.get(a.agent_id)
        if caps and cat in caps:
            capable.append(a)
        elif a.category and a.category.strip().lower() == cat:
            capable.append(a)
    return capable


def _fallback_agents(settings) -> list[AgentInfo]:

    """Resolve the fallback provider roster (sec.7) for the active CAP mode.

    - mock : the built-in base agents modeled in MockCapClient.
    - live : OUR base agent identified by CROON_FALLBACK_* env vars. It must be
             a real, hireable CAP service (has a Store service_id), otherwise
             hire_and_pay cannot negotiate it.
    """
    if not settings.is_live:
        return _MOCK_FALLBACK_AGENTS
    if not settings.fallback_service_id or not settings.fallback_agent_id:
        return []  # no configured fallback -> engine surfaces a clear RunError
    return [
        AgentInfo(
            agent_id=settings.fallback_agent_id,
            name=settings.fallback_agent_name,
            category=None,
            listed_price_usdc=Decimal("0.05"),
            listed_eta_seconds=15,
            reputation=0.60,
            is_base_agent=True,
            service_id=settings.fallback_service_id,
        )
    ]



class RunError(Exception):
    """Raised when a run cannot complete (e.g. budget exhausted)."""


async def execute_run(
    order: StandingOrder,
    session: Session,
    cap: CapClient,
) -> Run:
    """Execute ONE full mini-RFQ + settlement cycle for a standing order."""
    settings = get_settings()
    # Reset per-run failover tracking so a previous run's fallback state can't
    # leak into this one (see FailoverCapClient.begin_run / paid_via_mock).
    if hasattr(cap, "begin_run"):
        cap.begin_run()
    run = Run(
        standing_order_id=order.id,
        status="running",
        started_at=_now(),
        mode="live" if settings.is_live else "mock",
    )


    session.add(run)
    session.commit()
    session.refresh(run)

    def emit(event_type: str, **data) -> None:
        EVENT_BUS.publish(order.id, {"run_id": run.id, "type": event_type, **data})

    emit(
        "run_started",
        order_name=order.name,
        budget_per_run=str(order.budget_per_run_usdc),
    )

    # --- Budget guard (Layer A accounting) --------------------------------
    remaining = order.max_total_budget_usdc - order.total_spent_usdc
    if remaining < order.budget_per_run_usdc:
        order.status = "budget_exhausted"
        session.add(order)
        run.status = "failed"
        run.selection_reason = "total budget exhausted"
        run.finished_at = _now()
        session.add(run)
        session.commit()
        session.refresh(run)
        emit("run_failed", reason="total budget exhausted")
        return run

    task = TaskSpec(
        task_prompt=order.task_prompt,
        category=order.category,
        acceptance_criteria=order.acceptance_criteria,
    )

    fallback_roster = _fallback_agents(settings)
    # Only fallbacks that can actually DO this task category are eligible.
    capable_fallbacks = _capable_fallbacks(fallback_roster, order.category)

    def _no_provider(reason: str) -> Run:
        """Mark the run as no_provider_available and spend NO budget (sec.7)."""
        run.status = "no_provider_available"
        run.selection_reason = reason
        run.amount_paid_usdc = Decimal("0")
        run.fallback_used = True
        run.finished_at = _now()
        session.add(run)
        session.commit()
        session.refresh(run)
        emit("no_provider_available", reason=reason)
        emit("run_completed", status=run.status)
        return run


    try:

        # --- Layer C: discover candidates ---------------------------------
        candidates = await cap.discover_agents(
            category=order.category, limit=order.max_agents_to_query
        )
        emit(
            "candidates_discovered",
            agents=[{"agent_id": a.agent_id, "name": a.name} for a in candidates],
        )

        # --- Layer C: request quotes in parallel, under a strict timeout --
        quotes = await _collect_quotes(
            cap, candidates, task, settings.rfq_timeout_seconds, emit
        )

        fallback_used = False

        # --- Fallback (sec.7): fewer than 1 valid quote -> base agent --------
        if len(quotes) < 1:
            # A fallback must be CAPABILITY-appropriate. If no base agent can do
            # this task category, we refuse and spend NOTHING (risk management).
            if not capable_fallbacks:
                return _no_provider(
                    "no live bids and no capability-appropriate fallback "
                    f"provider for category '{order.category}' - budget "
                    "protected, no spend"
                )
            emit(
                "fallback_triggered",
                message=(
                    "No live bids received - budget protection active - "
                    "routing to capability-matched fallback provider."
                ),
            )
            quotes = await _collect_quotes(
                cap, capable_fallbacks, task, settings.rfq_timeout_seconds, emit,
                is_fallback=True,
            )
            fallback_used = True
            if len(quotes) < 1:
                return _no_provider(
                    "capability-matched fallback provider did not respond - "
                    "budget protected, no spend"
                )


        # --- Layer C: score + select --------------------------------------
        selection = score_quotes(quotes, order.budget_per_run_usdc)
        emit(
            "quotes_scored",
            quotes=[_qr_json(r) for r in selection.scored_quotes],
        )

        if selection.winner is None:
            # All quotes over budget. Try a capability-matched fallback once.
            if not fallback_used and capable_fallbacks:
                emit(
                    "fallback_triggered",
                    message=(
                        "All quotes exceeded per-run budget - routing to "
                        "capability-matched fallback provider."
                    ),
                )
                fb_quotes = await _collect_quotes(
                    cap, capable_fallbacks, task,
                    settings.rfq_timeout_seconds, emit, is_fallback=True,
                )

                fallback_used = True
                selection = score_quotes(fb_quotes, order.budget_per_run_usdc)
                emit(
                    "quotes_scored",
                    quotes=[_qr_json(r) for r in selection.scored_quotes],
                )
            if selection.winner is None:
                # Nothing eligible and no capable fallback under budget.
                return _no_provider(
                    "no eligible quote under budget and no capability-matched "
                    "fallback under budget - budget protected, no spend"
                )


        winner = selection.winner
        emit(
            "winner_selected",
            winner={
                "agent_id": winner.agent_id,
                "agent_name": winner.agent_name,
                "price_usdc": str(winner.price_usdc),
                "score": winner.score,
            },
            reason=selection.reason,
        )

        # --- Layer D: hire winner + pay USDC on Base ----------------------
        winner_info = _find_agent(candidates + fallback_roster, winner.agent_id)

        emit("payment_pending", agent_name=winner.agent_name)
        try:
            settlement = await cap.hire_and_pay(
                winner_info, task, winner.price_usdc
            )
        except Exception as hire_exc:  # noqa: BLE001
            # sec.7 (hire-time): the selected live provider refused to transact
            # (negotiation REJECTED/EXPIRED or timed out). A non-cooperative
            # provider is a supply failure just like a no-bid - so we DON'T
            # crash. We route to a capability-matched base agent (which we
            # control and which auto-accepts), spending nothing on the reject.
            eligible_fb = [
                a for a in capable_fallbacks if a.agent_id != winner.agent_id
            ]
            if fallback_used or not eligible_fb:
                raise  # no cooperative fallback left -> outer handler
            emit(
                "fallback_triggered",
                message=(
                    f"Winner '{winner.agent_name}' declined to transact "
                    f"({hire_exc}). Budget protection active - routing to "
                    "capability-matched fallback provider."
                ),
            )
            fb_quotes = await _collect_quotes(
                cap, eligible_fb, task,
                settings.rfq_timeout_seconds, emit, is_fallback=True,
            )
            fallback_used = True
            selection = score_quotes(fb_quotes, order.budget_per_run_usdc)
            emit("quotes_scored", quotes=[_qr_json(r) for r in selection.scored_quotes])
            if selection.winner is None:
                return _no_provider(
                    "winner declined and no capability-matched fallback under "
                    "budget - budget protected, no spend"
                )
            winner = selection.winner
            winner_info = _find_agent(candidates + fallback_roster, winner.agent_id)
            emit(
                "winner_selected",
                winner={
                    "agent_id": winner.agent_id,
                    "agent_name": winner.agent_name,
                    "price_usdc": str(winner.price_usdc),
                    "score": winner.score,
                },
                reason=selection.reason,
            )
            emit("payment_pending", agent_name=winner.agent_name)
            settlement = await cap.hire_and_pay(
                winner_info, task, winner.price_usdc
            )
        emit(
            "payment_completed",
            tx_hash=settlement.tx_hash,
            amount_usdc=str(settlement.amount_paid_usdc),
        )


        # --- Truthful labeling (sec. integrity) ------------------------------
        # If the LIVE settlement silently failed over to mock, the tx_hash is a
        # FAKE, BaseScan-invalid hash. We MUST NOT present such a run as a real
        # on-chain live payment. Relabel it as a degraded/mock settlement so the
        # ledger and the UI tell the truth.
        paid_via_mock = getattr(cap, "paid_via_mock", False)
        if paid_via_mock:
            run.mode = "mock"
            emit(
                "settlement_degraded",
                message=(
                    "Live settlement failed - payment completed on the mock "
                    "network. tx_hash is NOT a real on-chain transaction."
                ),
            )
        # A SECOND, independent integrity check: the live path may report SUCCESS
        # (no failover) yet return a tx_hash we could NOT confirm on the Base RPC
        # (settlement.tx_verified is False). That is exactly the silent failure
        # paid_via_mock cannot catch - the SDK "succeeded" but nothing is on
        # chain. Do NOT present it as a verified live payment.
        elif settlement.tx_verified is False:
            run.mode = "unverified"
            emit(
                "settlement_unverified",
                message=(
                    "Payment reported by the SDK but tx_hash was NOT found on "
                    "the configured Base RPC. Marked UNVERIFIED - not a "
                    "confirmable on-chain settlement."
                ),
                tx_hash=settlement.tx_hash,
            )


        # --- Layer D: fetch delivery --------------------------------------
        delivery = await cap.get_delivery(settlement.order_id)
        output_hash = _sha256(delivery.output_text)


        # --- Layer D: assemble the signed receipt bundle ------------------
        receipt = _build_receipt(
            order=order,
            run=run,
            selection_reason=selection.reason,
            scored_quotes=selection.scored_quotes,
            winner=winner,
            tx_hash=settlement.tx_hash,
            amount_paid=settlement.amount_paid_usdc,
            output_text=delivery.output_text,
            output_hash=output_hash,
            fallback_used=fallback_used,
        )
        receipt_hash = _sha256(json.dumps(receipt, sort_keys=True, default=str))

        # --- Persist the completed run + update Layer A accounting --------
        run.status = "fallback_used" if fallback_used else "completed"
        run.quotes_json = json.dumps([_qr_json(r) for r in selection.scored_quotes])
        run.winner_agent_id = winner.agent_id
        run.selection_reason = selection.reason
        run.amount_paid_usdc = settlement.amount_paid_usdc
        run.tx_hash = settlement.tx_hash
        run.output_ref = delivery.output_text
        run.output_hash = output_hash
        run.receipt_hash = receipt_hash
        run.fallback_used = fallback_used
        run.finished_at = _now()
        session.add(run)

        order.total_spent_usdc = order.total_spent_usdc + settlement.amount_paid_usdc
        if order.max_total_budget_usdc - order.total_spent_usdc < order.budget_per_run_usdc:
            order.status = "budget_exhausted"
        session.add(order)
        session.commit()
        session.refresh(run)

        emit(
            "receipt_generated",
            receipt_hash=receipt_hash,
            status=run.status,
            total_spent=str(order.total_spent_usdc),
        )
        emit("run_completed", status=run.status)
        return run

    except Exception as exc:  # noqa: BLE001 - graceful failure, never crash loop
        run.status = "failed"
        run.selection_reason = f"error: {exc}"
        run.finished_at = _now()
        session.add(run)
        session.commit()
        session.refresh(run)
        emit("run_failed", reason=str(exc))
        return run


async def _collect_quotes(
    cap: CapClient,
    agents: list[AgentInfo],
    task: TaskSpec,
    timeout_s: int,
    emit,
    *,
    is_fallback: bool = False,
) -> list[Quote]:
    """Request quotes from all agents in parallel under a strict timeout.

    Any agent that times out or returns None is simply dropped - the round
    proceeds with whatever valid quotes arrived.
    """

    async def _one(agent: AgentInfo) -> Quote | None:
        try:
            q = await asyncio.wait_for(
                cap.request_quote(agent, task, timeout_s), timeout=timeout_s
            )
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            q = None
        if q is not None:
            emit(
                "quote_received",
                is_fallback=is_fallback,
                quote={
                    "agent_id": q.agent_id,
                    "agent_name": q.agent_name,
                    "price_usdc": str(q.price_usdc),
                    "eta_seconds": q.eta_seconds,
                    "confidence": q.confidence,
                    "is_base_agent": q.is_base_agent,
                },
            )
        else:
            emit("quote_missed", agent_id=agent.agent_id, is_fallback=is_fallback)
        return q

    results = await asyncio.gather(*[_one(a) for a in agents])
    return [q for q in results if q is not None]


def _find_agent(agents: list[AgentInfo], agent_id: str) -> AgentInfo:
    for a in agents:
        if a.agent_id == agent_id:
            return a
    raise RunError(f"winner agent {agent_id} not found in candidate set")


def _qr_json(r: QuoteRecord) -> dict:
    return {
        "agent_id": r.agent_id,
        "agent_name": r.agent_name,
        "price_usdc": str(r.price_usdc),
        "eta_seconds": r.eta_seconds,
        "confidence": r.confidence,
        "is_base_agent": r.is_base_agent,
        "score": r.score,
        "excluded": r.excluded,
        "exclusion_reason": r.exclusion_reason,
    }


def _build_receipt(
    *,
    order: StandingOrder,
    run: Run,
    selection_reason: str,
    scored_quotes: list[QuoteRecord],
    winner: QuoteRecord,
    tx_hash: str | None,
    amount_paid: Decimal,
    output_text: str,
    output_hash: str,
    fallback_used: bool,
) -> dict:
    """The full, verifiable receipt bundle (hashed into receipt_hash)."""
    return {
        "run_id": run.id,
        "standing_order_id": order.id,
        "standing_order_name": order.name,
        "task_prompt": order.task_prompt,
        "quotes": [_qr_json(r) for r in scored_quotes],
        "winner_agent_id": winner.agent_id,
        "winner_agent_name": winner.agent_name,
        "selection_reason": selection_reason,
        "amount_paid_usdc": str(amount_paid),
        "tx_hash": tx_hash,
        "output_hash": output_hash,
        "fallback_used": fallback_used,
        "started_at": run.started_at.isoformat(),
        "generated_at": _now().isoformat(),
    }
