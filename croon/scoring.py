"""Scoring & selection — the transparent heart of the mini-RFQ (spec §6).

    score = w_price * price_score + w_rep * reputation_score + w_speed * speed_score

- price_score : normalized so CHEAPER (still under budget) scores higher.
- speed_score : normalized so LOWER ETA scores higher.
- reputation_score : MVP placeholder = the quote's self-reported confidence.
                     (Deliberately NOT a reputation oracle — out of scope, §6.)
- HARD RULE: any quote with price > budget_per_run is EXCLUDED before scoring.
- Weights are configurable via .env (CROON_W_PRICE / _W_REP / _W_SPEED).

Everything here is pure + deterministic so the demo UI can explain exactly
WHY a winner won (human-readable `selection_reason`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from croon.config import get_settings
from croon.schemas import Quote, QuoteRecord


@dataclass
class SelectionResult:
    """Outcome of scoring a round of quotes."""

    winner: QuoteRecord | None
    scored_quotes: list[QuoteRecord]  # ALL quotes, incl. excluded, with scores
    reason: str


def _normalize_lower_is_better(value: float, lo: float, hi: float) -> float:
    """Map a value into 0..1 where the MINIMUM in range scores 1.0.

    If all candidates are equal (hi == lo), everyone gets a neutral 1.0.
    """
    if hi <= lo:
        return 1.0
    return (hi - value) / (hi - lo)


def score_quotes(
    quotes: list[Quote],
    budget_per_run_usdc: Decimal,
    *,
    w_price: float | None = None,
    w_rep: float | None = None,
    w_speed: float | None = None,
) -> SelectionResult:
    """Score a round of quotes and pick the winner under budget.

    Returns every quote annotated with its score (or exclusion reason), plus
    the winner and a human-readable explanation.
    """
    settings = get_settings()
    w_price = settings.w_price if w_price is None else w_price
    w_rep = settings.w_rep if w_rep is None else w_rep
    w_speed = settings.w_speed if w_speed is None else w_speed

    records: list[QuoteRecord] = []

    # 1) Hard budget filter (spec §6): over-budget quotes are excluded up front.
    eligible: list[Quote] = []
    for q in quotes:
        if q.price_usdc > budget_per_run_usdc:
            records.append(
                QuoteRecord(
                    agent_id=q.agent_id,
                    agent_name=q.agent_name,
                    price_usdc=q.price_usdc,
                    eta_seconds=q.eta_seconds,
                    confidence=q.confidence,
                    is_base_agent=q.is_base_agent,
                    score=None,
                    excluded=True,
                    exclusion_reason=(
                        f"price {q.price_usdc} > budget {budget_per_run_usdc}"
                    ),
                )
            )
        else:
            eligible.append(q)

    if not eligible:
        return SelectionResult(
            winner=None,
            scored_quotes=records,
            reason="no eligible quotes under budget",
        )

    # 2) Normalization ranges over the ELIGIBLE set.
    prices = [float(q.price_usdc) for q in eligible]
    etas = [float(q.eta_seconds) for q in eligible]
    lo_price, hi_price = min(prices), max(prices)
    lo_eta, hi_eta = min(etas), max(etas)

    # 3) Score each eligible quote.
    for q in eligible:
        price_score = _normalize_lower_is_better(float(q.price_usdc), lo_price, hi_price)
        speed_score = _normalize_lower_is_better(float(q.eta_seconds), lo_eta, hi_eta)
        rep_score = max(0.0, min(1.0, q.confidence))  # MVP placeholder

        score = w_price * price_score + w_rep * rep_score + w_speed * speed_score
        records.append(
            QuoteRecord(
                agent_id=q.agent_id,
                agent_name=q.agent_name,
                price_usdc=q.price_usdc,
                eta_seconds=q.eta_seconds,
                confidence=q.confidence,
                is_base_agent=q.is_base_agent,
                score=round(score, 4),
                excluded=False,
            )
        )

    # 4) Winner = highest score among eligible (records order: excluded first,
    #    then eligible — so pick the max over non-excluded).
    scored_eligible = [r for r in records if not r.excluded and r.score is not None]
    winner = max(scored_eligible, key=lambda r: r.score)  # type: ignore[arg-type]

    reason = (
        f"best score under budget: score {winner.score}, "
        f"price {winner.price_usdc} USDC, eta {winner.eta_seconds}s, "
        f"rep {round(winner.confidence, 2)} "
        f"(weights price={w_price}, rep={w_rep}, speed={w_speed})"
    )

    return SelectionResult(winner=winner, scored_quotes=records, reason=reason)
