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
    assert evaluate_gate("I will pay ₹42,000 Friday")["passed"] is True
    # Should pass (score >= 3: action verb + obligation language + temporal)
    assert evaluate_gate("Please send the GST certificate tomorrow")["passed"] is True
    # Should pass: "Waiting for Arjun's approval" (waiting = +2, approve = +2) -> score 4
    assert evaluate_gate("Waiting for Arjun's approval")["passed"] is True
    # Should pass: payment transfer
    assert evaluate_gate("I'll transfer ₹42,000 by Friday")["passed"] is True
    # Should pass: "Supplier still hasn't sent the motor."
    assert evaluate_gate("Supplier still hasn't sent the motor.")["passed"] is True
    
    # Should not pass (score < 3)
    assert evaluate_gate("Okay, noted.")["passed"] is False
    assert evaluate_gate("Okay, noted. Thanks.")["passed"] is False
    assert evaluate_gate("Okay, thanks.")["passed"] is False
    assert evaluate_gate("Let's meet sometime.")["passed"] is False
    assert evaluate_gate("Hello world")["passed"] is False


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


def test_directionality():
    now_utc = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
    res = extract_offline("Can you send me the invoice by Friday?", now_utc, "Asia/Kolkata")
    # Should be skipped because it's a request to the receiver, not a sender commitment
    assert res["has_commitment"] is False


def test_create_command_deterministic(tmp_path, monkeypatch):
    from bizpulse.storage import SQLiteStorage
    from bizpulse.service import BizPulseService
    import bizpulse.metrics as metrics

    metrics.reset_metrics()

    # If the LLM extractor is called, raise an error
    def mock_extract(*args, **kwargs):
        raise AssertionError("Gemini extractor should not be called on the command path!")
    monkeypatch.setattr("bizpulse.service.extract_commitment", mock_extract)

    db_file = str(tmp_path / "test_commands.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)

    conv_id = "test_conv_cmd"

    # Valid command
    resp = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@owner"},
        text="/create payment | Arjun | Delta Traders | 42000 | Friday",
        message_id="cmd_001",
        channel="telegram"
    )
    assert resp is not None
    assert "Created commitment" in resp
    assert "Arjun" in resp
    assert "₹42,000" in resp

    # Check database state
    commitments = storage.get_unresolved_commitments(conv_id)
    assert len(commitments) == 1
    c = commitments[0]
    assert c.party == "Arjun"
    assert c.organization == "Delta Traders"
    assert c.amount_cents == 4200000
    assert c.extraction_method == "offline"

    # Invalid command
    resp_err = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@owner"},
        text="/create payment | Arjun",
        message_id="cmd_002",
        channel="telegram"
    )
    assert "Missing required fields" in resp_err


def test_gate_filtering_token_behavior(tmp_path, monkeypatch):
    from bizpulse.storage import SQLiteStorage
    from bizpulse.service import BizPulseService
    import bizpulse.metrics as metrics

    metrics.reset_metrics()

    def mock_extract(*args, **kwargs):
        raise AssertionError("Gemini extractor should not be called if gate fails!")
    monkeypatch.setattr("bizpulse.service.extract_commitment", mock_extract)

    db_file = str(tmp_path / "test_gate_filter.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)

    resp = service.handle_user_message(
        conversation_id="gate_conv",
        sender={"address": "@user"},
        text="Okay, thanks.",
        message_id="msg_gate",
        channel="telegram"
    )
    assert resp is None  # Filtered by gate
    
    # Check metrics
    m = metrics.get_metrics()
    assert m["messages_seen"] == 1
    assert m["messages_filtered"] == 1
    assert m["llm_calls"] == 0


def test_list_snooze_close_commands(tmp_path):
    from bizpulse.storage import SQLiteStorage
    from bizpulse.service import BizPulseService

    db_file = str(tmp_path / "test_list_snooze_close.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)

    conv_id = "test_conv_lsc"

    # 1. Create commitment
    service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@owner"},
        text="/create payment | Arjun | Delta Traders | 42000 | Friday",
        message_id="cmd_001",
        channel="telegram"
    )

    commitments = storage.get_unresolved_commitments(conv_id)
    assert len(commitments) == 1
    c = commitments[0]
    short_id = c.id.replace("commitment_", "")

    # 2. List commitments
    resp_list = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@owner"},
        text="/list",
        message_id="cmd_002",
        channel="telegram"
    )
    assert resp_list is not None
    assert "Unresolved Commitments" in resp_list
    assert short_id in resp_list

    # 3. Snooze commitment
    orig_deadline = c.deadline_utc
    resp_snooze = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@owner"},
        text=f"/snooze {short_id} 2d",
        message_id="cmd_003",
        channel="telegram"
    )
    assert resp_snooze is not None
    assert "Commitment snoozed" in resp_snooze

    c_snoozed = storage.get_commitment(c.id)
    assert c_snoozed.next_followup_at is not None
    assert c_snoozed.deadline_utc == orig_deadline  # invariant: deadline_utc is unchanged!

    # 4. Close commitment
    resp_close = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@owner"},
        text=f"/close {short_id}",
        message_id="cmd_004",
        channel="telegram"
    )
    assert resp_close is not None
    assert "Closed Commitment" in resp_close

    c_closed = storage.get_commitment(c.id)
    assert c_closed.status == "fulfillment_claimed"  # closed -> fulfillment_claimed, never verified_fulfilled!
