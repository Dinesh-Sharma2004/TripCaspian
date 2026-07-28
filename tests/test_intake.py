"""Unit tests for TripCaspian Intake NLU parser and follow-up question generator."""

import pytest
from tripcaspian.intake import parse_trip_request, generate_followup_question, TripQuery


def test_full_query_extraction():
    text = "I want to go from Delhi to Jaipur tomorrow morning with a budget of ₹1200 and 5% concession"
    query = parse_trip_request(text)

    assert query.source == "Delhi"
    assert query.destination == "Jaipur"
    assert query.budget == 1200.0
    assert query.concession_pct == 0.05
    assert query.is_complete() is True

    followup = generate_followup_question(query)
    assert followup is None


def test_partial_query_triggers_single_followup():
    # Only source and destination given
    text = "Delhi to Jaipur"
    query = parse_trip_request(text)

    assert query.source == "Delhi"
    assert query.destination == "Jaipur"
    assert query.budget is None
    assert query.is_complete() is False

    followup = generate_followup_question(query)
    assert followup == "Understood! What is your maximum budget and preferred departure/travel duration limit for the trip from Delhi to Jaipur? (e.g. ₹1200, tomorrow morning, under 6 hours)"


def test_preserving_existing_fields():
    # User previously answered route: Delhi to Jaipur
    existing = {"source": "Delhi", "destination": "Jaipur"}
    user_reply = "My budget is 1500 rupees"

    query = parse_trip_request(user_reply, existing=existing)

    assert query.source == "Delhi" # preserved!
    assert query.destination == "Jaipur" # preserved!
    assert query.budget == 1500.0
    assert query.is_complete() is True


def test_single_number_budget_reply():
    existing = {"source": "Mumbai", "destination": "Pune"}
    query = parse_trip_request("800", existing=existing)

    assert query.source == "Mumbai"
    assert query.destination == "Pune"
    assert query.budget == 800.0
    assert query.is_complete() is True
