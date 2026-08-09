"""Comprehensive test suite for conversational progressive commitment completion."""

import pytest
import datetime
from bizpulse.storage import SQLiteStorage
from bizpulse.service import BizPulseService
import bizpulse.metrics as metrics
from bizpulse.commitments.models import Commitment
from bizpulse.config import DEFAULT_TIMEZONE


def test_progressive_completion_e2e(tmp_path):
    db_file = str(tmp_path / "test_prog.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)

    conv_id = "test_prog_conv"

    # Message 1: "Arjun will pay." -> draft created
    r1 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Arjun will pay.",
        message_id="msg1",
        channel="telegram"
    )
    assert r1 is not None
    assert "What amount should I record, and when is the payment expected?" in r1

    # Verify draft exists
    drafts = storage.get_drafts_for_conversation(conv_id)
    assert len(drafts) == 1
    d = drafts[0]
    assert d["party"] == "Arjun"
    assert d["missing_field"] == "amount"

    # Message 2: "₹42,000" -> validates amount, expects deadline
    r2 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="₹42,000",
        message_id="msg2",
        channel="telegram"
    )
    assert r2 is not None
    assert "Thanks. I have ₹42,000. When is the payment expected?" in r2

    d_updated = storage.get_draft(d["draft_id"])
    assert d_updated["amount_cents"] == 4200000
    assert d_updated["missing_field"] == "deadline"

    # Message 3: "Friday" -> validates deadline, completes commitment
    r3 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Friday",
        message_id="msg3",
        channel="telegram"
    )
    assert r3 is not None
    assert "Got it. I'm tracking ₹42,000 from Arjun, due" in r3

    # Draft should be deleted, exactly 1 commitment created
    assert len(storage.get_drafts_for_conversation(conv_id)) == 0
    commitments = storage.get_unresolved_commitments(conv_id)
    assert len(commitments) == 1
    c = commitments[0]
    assert c.party == "Arjun"
    assert c.amount_cents == 4200000
    assert c.deadline_raw == "friday"


def test_multiple_missing_fields_supplied_together(tmp_path):
    db_file = str(tmp_path / "test_together.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_together_conv"

    # Incomplete start
    service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Arjun will pay.",
        message_id="m1",
        channel="telegram"
    )

    # Reply with both: "₹42,000 on Friday." -> completes commitment directly
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="₹42,000 on Friday.",
        message_id="m2",
        channel="telegram"
    )
    assert r is not None
    assert "Got it. I'm tracking ₹42,000 from Arjun, due" in r

    assert len(storage.get_drafts_for_conversation(conv_id)) == 0
    assert len(storage.get_unresolved_commitments(conv_id)) == 1


def test_invalid_amount_response(tmp_path):
    db_file = str(tmp_path / "test_invalid_amt.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_inv_amt"

    service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Arjun will pay.",
        message_id="m1",
        channel="telegram"
    )

    draft = storage.get_latest_draft(conv_id)
    draft["deadline_utc"] = "2026-08-14T18:29:00Z"
    draft["deadline_raw"] = "Friday"
    draft["missing_field"] = "amount"
    storage.save_draft(draft)

    # Send a date when amount is expected
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Friday",
        message_id="m2",
        channel="telegram"
    )
    assert "Friday looks like a date. I still need the payment amount, for example ₹42,000." in r


def test_invalid_deadline_response(tmp_path):
    db_file = str(tmp_path / "test_invalid_dl.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_inv_dl"

    # Setup draft missing deadline
    draft = {
        "conversation_id": conv_id,
        "party": "Arjun",
        "type": "payment",
        "amount_cents": 4200000,
        "currency": "INR",
        "missing_field": "deadline"
    }
    storage.save_draft(draft)

    # Send an amount when deadline is expected
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="₹42,000",
        message_id="m2",
        channel="telegram"
    )
    assert "₹42,000 looks like an amount. I still need the expected date, for example Friday or tomorrow." in r


def test_unrelated_response(tmp_path):
    db_file = str(tmp_path / "test_unrelated.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_unrel"

    # Setup draft missing deadline
    draft = {
        "conversation_id": conv_id,
        "party": "Arjun",
        "type": "payment",
        "amount_cents": 4200000,
        "currency": "INR",
        "missing_field": "deadline"
    }
    storage.save_draft(draft)

    # Send "Thanks"
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Thanks",
        message_id="m2",
        channel="telegram"
    )
    assert "I still need the expected date, for example tomorrow or Friday." in r


def test_incomplete_delivery(tmp_path):
    db_file = str(tmp_path / "test_delivery.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_deliv"

    # "Supplier will deliver the replacement motor." -> missing deadline
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Supplier will deliver the replacement motor.",
        message_id="m1",
        channel="telegram"
    )
    assert "When is the replacement motor expected?" in r

    draft = storage.get_latest_draft(conv_id)
    assert draft["party"] == "Supplier"
    assert draft["object"] == "the replacement motor"
    assert draft["missing_field"] == "deadline"

    # Answer "tomorrow" -> completes it
    r2 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="tomorrow",
        message_id="m2",
        channel="telegram"
    )
    assert "Supplier is supposed to deliver the replacement motor by tomorrow" in r2


def test_incomplete_document(tmp_path):
    db_file = str(tmp_path / "test_document.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_doc"

    # "Customer will send the GST certificate." -> missing deadline
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Customer will send the GST certificate.",
        message_id="m1",
        channel="telegram"
    )
    assert "Which document needs to be sent?" in r or "When is this due?" in r or "What exactly should be delivered" in r


def test_incomplete_service(tmp_path):
    db_file = str(tmp_path / "test_service.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_svc"

    # "Vendor will perform the annual checkup." -> missing deadline
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Vendor will perform the annual checkup.",
        message_id="m1",
        channel="telegram"
    )
    assert r is not None


def test_low_score_answer_bypasses_gate(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_bypass.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_gate_bypass"

    # Save active draft missing amount
    draft = {
        "conversation_id": conv_id,
        "party": "Arjun",
        "type": "payment",
        "missing_field": "amount"
    }
    storage.save_draft(draft)

    # Mock gate evaluation to return score = 0, passed = False
    monkeypatch.setattr("bizpulse.service.evaluate_gate", lambda text: {"score": 0, "passed": False})

    # Send "₹42,000" -> should NOT get filtered, but processed and fill the draft!
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="₹42,000",
        message_id="m2",
        channel="telegram"
    )
    assert r is not None
    assert "When is the payment expected?" in r


def test_two_step_low_signal_fallback(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_fallback.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_fallback_conv"
    storage.mark_onboarding_sent(conv_id)

    # 1. First low-signal message "Payments" -> deterministic clarification
    r1 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Payments",
        message_id="m1",
        channel="telegram"
    )
    assert "Sure. Are you trying to track a payment commitment or check an existing payment?" in r1

    # 2. Mock Gemini recovery to classify intent as list_commitments
    monkeypatch.setattr("bizpulse.service.classify_low_signal_intent", lambda text, ctx: "list_commitments")

    # Second low-signal message -> calls Gemini recovery and gets active commitments list
    r2 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Check my stuff",
        message_id="m2",
        channel="telegram"
    )
    assert "Unresolved Commitments" in r2 or "No active commitments" in r2

    # Reset counter test: a meaningful message resets low signal count
    r3 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Arjun will pay ₹42,000 by Friday.",
        message_id="m3",
        channel="telegram"
    )
    assert "Arjun is supposed to pay" in r3
    assert storage.get_low_signal_state(conv_id)["low_signal_count"] == 0


def test_multiple_drafts_resolution(tmp_path):
    db_file = str(tmp_path / "test_multidraft.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_multi"

    # Draft A: Arjun / payment / missing deadline
    draft_a = {
        "draft_id": "draft_a",
        "conversation_id": conv_id,
        "party": "Arjun",
        "type": "payment",
        "amount_cents": 4200000,
        "missing_field": "deadline"
    }
    # Draft B: Supplier / delivery / missing object
    draft_b = {
        "draft_id": "draft_b",
        "conversation_id": conv_id,
        "party": "Supplier",
        "type": "delivery",
        "missing_field": "object"
    }
    storage.save_draft(draft_a)
    storage.save_draft(draft_b)

    # Reply "Friday" -> should match Draft A (expecting deadline)
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="Friday",
        message_id="m1",
        channel="telegram"
    )
    assert "got it. i'm tracking ₹42,000 from arjun, due friday" in r.lower()


def test_email_template(tmp_path):
    db_file = str(tmp_path / "test_email.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_mail"

    # Send incomplete email "Arjun will pay."
    r = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "arjun@gmail.com"},
        text="Arjun will pay.",
        message_id="msg1",
        channel="email",
        subject="Payment inquiry"
    )
    assert r is not None
    assert "Hi," in r
    assert "I can track this commitment, but I still need:" in r
    assert "• payment amount" in r
    assert "• expected payment date" in r
    assert "Thanks,\nBizPulse" in r


def test_commands_preservation(tmp_path):
    db_file = str(tmp_path / "test_commands.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    conv_id = "test_cmd"

    # 1. /create remains deterministic (0 LLM calls)
    metrics.reset_metrics()
    r_create = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text="/create payment | Arjun | Delta Traders | 42000 | Friday",
        message_id="m1",
        channel="telegram"
    )
    assert r_create is not None
    assert "Created commitment" in r_create
    assert metrics.get_metrics()["llm_calls"] == 0

    c = storage.get_unresolved_commitments(conv_id)[0]
    orig_deadline = c.deadline_utc

    # 2. /snooze updates next_followup_at and preserves deadline_utc
    service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text=f"/snooze {c.id.replace('commitment_', '')} 2h",
        message_id="m2",
        channel="telegram"
    )
    c_updated = storage.get_commitment(c.id)
    assert c_updated.next_followup_at is not None
    assert c_updated.deadline_utc == orig_deadline

    # 3. /close transitions to fulfillment_claimed and NOT verified_fulfilled
    service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@user"},
        text=f"/close {c.id.replace('commitment_', '')}",
        message_id="m3",
        channel="telegram"
    )
    c_closed = storage.get_commitment(c.id)
    assert c_closed.status == "fulfillment_claimed"
