"""Unit tests for TripCaspian Optimizer and Scoring Engine."""

import pytest
from tripcaspian.providers.base import RouteOption
from tripcaspian.optimizer import rank_route_options, score_options, MAX_CONCESSION, CONCESSION_STEP


def make_option(opt_id: str, price: float, duration: int, mode: str = "train", operator: str = "Test Operator", depart: str = "08:00 AM") -> RouteOption:
    return RouteOption(
        id=opt_id,
        mode=mode,
        operator=operator,
        price=price,
        depart=depart,
        arrive="12:00 PM",
        duration_minutes=duration,
        seats_left=10,
        deep_link="http://example.com",
        source="Delhi",
        destination="Jaipur",
        is_mock=True,
    )


def test_score_options_normalization():
    opt1 = make_option("1", price=500.0, duration=300)
    opt2 = make_option("2", price=1000.0, duration=150)

    scores = score_options([opt1, opt2], w_price=0.6, w_time=0.4)
    # opt1 has min price (norm 0.0) and max time (norm 1.0) -> score 0.4
    # opt2 has max price (norm 1.0) and min time (norm 0.0) -> score 0.6
    assert scores["1"] == 0.4
    assert scores["2"] == 0.6


def test_concession_widening_5_percent():
    # Options priced at 1040 and 1080. Target budget = 1000.
    opt1 = make_option("1", price=1040.0, duration=200) # 4% over budget (within 5% cutoff <= 1050)
    opt2 = make_option("2", price=1080.0, duration=200) # 8% over budget

    ranked, cutoff, is_widened = rank_route_options([opt1, opt2], budget=1000.0)
    assert is_widened is True
    assert cutoff == 1050.0
    assert len(ranked) == 1
    assert ranked[0].option.id == "1"


def test_concession_widening_10_percent():
    # Option priced at 1080. Target budget = 1000.
    opt1 = make_option("1", price=1080.0, duration=200) # 8% over budget (requires 10% cutoff <= 1100)

    ranked, cutoff, is_widened = rank_route_options([opt1, opt2 := make_option("2", price=1150.0, duration=100)], budget=1000.0)
    assert is_widened is True
    assert cutoff == 1100.0
    assert len(ranked) == 1
    assert ranked[0].option.id == "1"


def test_strict_10_percent_ceiling():
    # Option priced at 1150 (15% over budget 1000). Max concession ceiling is 10% (1100).
    opt1 = make_option("1", price=1150.0, duration=200)

    ranked, cutoff, is_widened = rank_route_options([opt1], budget=1000.0)
    assert len(ranked) == 0


def test_deterministic_tie_breaking():
    # Two options with identical price and duration, different operator names
    opt_a = make_option("a", price=500.0, duration=200, operator="B Operator")
    opt_b = make_option("b", price=500.0, duration=200, operator="A Operator")

    ranked, _, _ = rank_route_options([opt_a, opt_b], budget=600.0)
    assert len(ranked) == 2
    # Tie break by operator name (alphabetical) -> "A Operator" comes first
    assert ranked[0].option.operator == "A Operator"
    assert ranked[1].option.operator == "B Operator"
