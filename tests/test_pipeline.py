"""Unit tests for BizPulse Normalizer, Gate, and Extractor modules."""

import pytest
from datetime import datetime, timezone
from bizpulse.commitments.normalizer import normalize_message
from bizpulse.commitments.gate import evaluate_gate
from bizpulse.commitments.extractor import extract_offline, validate_extraction


def test_normalizer():
    # Quoted email removal
    raw_email = (
        "Hi, we'll clear the remaining ₹42,000 by Friday.\n"
        "\n"
        "Regards,\n"
        "Arjun\n"
        "\n"
        "> From: owner@business.com\n"
        "> Sent: Thursday\n"
        "> Hey Arjun, when will you pay?"
    )
    normalized = normalize_message(raw_email)
    assert "Hi, we'll clear the remaining" in normalized
    assert "owner@business.com" not in normalized
    assert "Arjun" not in normalized  # signature removed


def test_gate_evaluation():
    # Should pass (score >= 3)
    assert evaluate_gate("I will pay ₹42,000 Friday") is True
    # Should pass (score >= 3: action verb + obligation language + temporal)
    assert evaluate_gate("Please send the GST certificate tomorrow") is True
    # Should pass: "Waiting for Arjun's approval" (waiting = +2, approve = +2) -> score 4
    assert evaluate_gate("Waiting for Arjun's approval") is True
    # Should pass: payment transfer
    assert evaluate_gate("I'll transfer ₹42,000 by Friday") is True
    
    # Should not pass (score < 3)
    assert evaluate_gate("Okay, noted.") is False
    assert evaluate_gate("Okay, noted. Thanks.") is False
    assert evaluate_gate("Hello world") is False


def test_offline_extractor():
    now_utc = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc) # Thursday
    
    # 1. New commitment
    res1 = extract_offline("Arjun from Delta Traders said he'll pay ₹42,000 by Friday.", now_utc, "Asia/Kolkata")
    assert res1["has_commitment"] is True
    assert res1["intent"] == "new"
    assert res1["party"] == "Arjun"
    assert res1["organization"] == "Delta Traders"
    assert res1["amount_cents"] == 4200000
    assert res1["currency"] == "INR"
    assert res1["deadline_raw"] == "friday"
    
    # 2. Reschedule commitment
    res2 = extract_offline("Sorry, I'll pay Monday.", now_utc, "Asia/Kolkata")
    assert res2["has_commitment"] is True
    assert res2["intent"] == "reschedule"
    assert res2["party"] == "Counterparty"
    assert res2["deadline_raw"] == "monday"
    
    # 3. Fulfillment claim
    res3 = extract_offline("Payment sent.", now_utc, "Asia/Kolkata")
    assert res3["has_commitment"] is True
    assert res3["intent"] == "fulfillment"
    
    # 4. Dispute
    res4 = extract_offline("I never said I'd pay Friday.", now_utc, "Asia/Kolkata")
    assert res4["has_commitment"] is True
    assert res4["intent"] == "dispute"


def test_extraction_validation():
    # Accepted extraction
    valid_res = {
        "has_commitment": True,
        "intent": "new",
        "type": "payment",
        "party": "Arjun",
        "action": "pay",
        "deadline_utc": "2026-08-14T23:59:00+00:00",
        "confidence": 0.95
    }
    assert validate_extraction(valid_res) == "accepted"

    # Needs review: missing party
    invalid_res = valid_res.copy()
    invalid_res["party"] = None
    assert validate_extraction(invalid_res) == "needs_review"

    # Needs review: low confidence
    low_conf = valid_res.copy()
    low_conf["confidence"] = 0.5
    assert validate_extraction(low_conf) == "needs_review"
