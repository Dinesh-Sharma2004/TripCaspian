"""Pure Optimization and Scoring Engine for TripCaspian.

Ranks RouteOption objects using a price/duration weighted score and multi-tier budget concession logic.
Pure functions only — zero provider-specific logic inside.
"""

from typing import NamedTuple
from tripcaspian.providers.base import RouteOption

DEFAULT_W_PRICE = 0.6
DEFAULT_W_TIME = 0.4
MAX_CONCESSION = 0.10
CONCESSION_STEP = 0.05


class RankedOption(NamedTuple):
    option: RouteOption
    score: float
    reason: str


def score_options(
    options: list[RouteOption],
    w_price: float = DEFAULT_W_PRICE,
    w_time: float = DEFAULT_W_TIME,
) -> dict[str, float]:
    """Calculate min-max normalized weighted scores for options. Lower score is better."""
    if not options:
        return {}

    prices = [opt.price for opt in options]
    durations = [opt.duration_minutes for opt in options]

    min_price, max_price = min(prices), max(prices)
    min_time, max_time = min(durations), max(durations)

    price_range = max_price - min_price if max_price > min_price else 1.0
    time_range = max_time - min_time if max_time > min_time else 1.0

    scores = {}
    for opt in options:
        norm_price = (opt.price - min_price) / price_range
        norm_time = (opt.duration_minutes - min_time) / time_range
        score = (w_price * norm_price) + (w_time * norm_time)
        scores[opt.id] = round(score, 4)

    return scores


def rank_route_options(
    options: list[RouteOption],
    budget: float,
    custom_concession: float | None = None,
    w_price: float = DEFAULT_W_PRICE,
    w_time: float = DEFAULT_W_TIME,
) -> tuple[list[RankedOption], float, bool]:
    """Rank route options applying weighted scoring and budget concession widening.

    Returns:
        (ranked_options_list, effective_budget_cutoff, is_widened)
    """
    if not options:
        return [], budget, False

    max_concession = custom_concession if custom_concession is not None else MAX_CONCESSION

    # Concession Tier 1: Strict budget
    eligible = [opt for opt in options if opt.price <= budget]
    effective_cutoff = budget
    is_widened = False

    # Concession Tier 2: Budget * 1.05 (5% concession)
    if not eligible and max_concession >= CONCESSION_STEP:
        effective_cutoff = budget * (1.0 + CONCESSION_STEP)
        eligible = [opt for opt in options if opt.price <= effective_cutoff]
        if eligible:
            is_widened = True

    # Concession Tier 3: Budget * (1 + max_concession) (up to 10% concession)
    if not eligible and max_concession > CONCESSION_STEP:
        effective_cutoff = budget * (1.0 + max_concession)
        eligible = [opt for opt in options if opt.price <= effective_cutoff]
        if eligible:
            is_widened = True

    if not eligible:
        return [], effective_cutoff, False

    # Compute normalized scores for eligible items
    scores = score_options(eligible, w_price=w_price, w_time=w_time)

    # Deterministic sort key:
    # 1. score (ascending)
    # 2. price (ascending)
    # 3. duration_minutes (ascending)
    # 4. depart (alphabetical/time string)
    # 5. operator (alphabetical)
    def sort_key(opt: RouteOption):
        return (
            scores.get(opt.id, 1.0),
            opt.price,
            opt.duration_minutes,
            opt.depart,
            opt.operator,
        )

    sorted_options = sorted(eligible, key=sort_key)

    # Determine special annotations for top recommendations
    min_price_opt = min(sorted_options, key=lambda x: x.price)
    min_time_opt = min(sorted_options, key=lambda x: x.duration_minutes)

    ranked_results: list[RankedOption] = []
    for idx, opt in enumerate(sorted_options):
        score = scores.get(opt.id, 1.0)
        reasons = []
        if opt.id == min_price_opt.id:
            reasons.append("Cheapest option")
        if opt.id == min_time_opt.id:
            reasons.append("Fastest route")

        if opt.price > budget:
            pct_over = round(((opt.price - budget) / budget) * 100, 1)
            reasons.append(f"{pct_over}% over target budget (within concession)")
        elif not reasons:
            reasons.append("Best overall value within budget")

        reason_str = " | ".join(reasons)
        ranked_results.append(RankedOption(option=opt, score=score, reason=reason_str))

    return ranked_results, effective_cutoff, is_widened
