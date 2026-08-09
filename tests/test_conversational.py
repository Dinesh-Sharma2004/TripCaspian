"""Regression tests for conversational BizPulse improvements."""

import pytest
from datetime import datetime, timezone
from bizpulse.storage import SQLiteStorage
from bizpulse.service import BizPulseService
import bizpulse.metrics as metrics
from bizpulse.commitments.extractor import extract_offline


def test_onboarding_trigger(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_onboarding.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    # Send "Hi" to trigger onboarding
    r1 = service.handle_user_message(
        conversation_id="conv_onb",
        sender={"address": "@user"},
        text="Hi",
        message_id="m1",
        channel="telegram"
    )
    assert r1 is not None
    assert "Hi! I'm BizPulse." in r1
    
    # Send "Hi" again, should NOT trigger onboarding again but return Stage 1 clarification
    r2 = service.handle_user_message(
        conversation_id="conv_onb",
        sender={"address": "@user"},
        text="Hi",
        message_id="m2",
        channel="telegram"
    )
    assert r2 is not None
    assert "I can help keep track of business promises" in r2
    
    # Mock low-signal classification to return help
    monkeypatch.setattr("bizpulse.service.classify_low_signal_intent", lambda *args, **kwargs: "help")
    
    # Send "Hi" a third time -> triggers Stage 2 LLM recovery (returns help text)
    r3 = service.handle_user_message(
        conversation_id="conv_onb",
        sender={"address": "@user"},
        text="Hi",
        message_id="m3",
        channel="telegram"
    )
    assert r3 is not None
    assert "I'm BizPulse, your conversational assistant" in r3


def test_complete_natural_language_commitment(tmp_path):
    db_file = str(tmp_path / "test_complete.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    # "Arjun will pay ₹42,000 by Friday."
    r = service.handle_user_message(
        conversation_id="conv_comp",
        sender={"address": "@user"},
        text="Arjun will pay ₹42,000 by Friday.",
        message_id="m1",
        channel="telegram"
    )
    assert r is not None
    assert "arjun is supposed to pay ₹42,000 by friday" in r.lower()
    
    commitments = storage.get_unresolved_commitments("conv_comp")
    assert len(commitments) == 1
    c = commitments[0]
    assert c.party == "Arjun"
    assert c.type == "payment"
    assert c.amount_cents == 4200000
    assert c.currency == "INR"
    assert c.deadline_raw == "friday"
    assert c.status == "pending"


def test_bare_amount_extraction(tmp_path):
    db_file = str(tmp_path / "test_bare_amt.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    # "Arjun will pay 42,000 Friday."
    r = service.handle_user_message(
        conversation_id="conv_bare",
        sender={"address": "@user"},
        text="Arjun will pay 42,000 Friday.",
        message_id="m1",
        channel="telegram"
    )
    assert r is not None
    assert "arjun is supposed to pay ₹42,000 by friday" in r.lower()
    
    c = storage.get_unresolved_commitments("conv_bare")[0]
    assert c.amount_cents == 4200000


def test_missing_amount_and_clarification(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_clarification.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    metrics.reset_metrics()

    # Step 1: Send commitment missing amount: "Arjun will pay Friday."
    r1 = service.handle_user_message(
        conversation_id="conv_clar",
        sender={"address": "@user"},
        text="Arjun will pay Friday.",
        message_id="m1",
        channel="telegram"
    )
    assert r1 is not None
    assert "How much is he supposed to pay?" in r1
    
    # Verify draft exists in DB
    draft = storage.get_draft("conv_clar")
    assert draft is not None
    assert draft["party"] == "Arjun"
    assert draft["missing_field"] == "amount"
    
    # Spy to ensure no LLM call is made for deterministic reply handling
    def mock_extract(*args, **kwargs):
        raise AssertionError("LLM should not be called for deterministic clarification replies!")
    monkeypatch.setattr("bizpulse.service.extract_commitment", mock_extract)
    
    # Step 2: Answer the missing amount: "42000"
    r2 = service.handle_user_message(
        conversation_id="conv_clar",
        sender={"address": "@user"},
        text="42000",
        message_id="m2",
        channel="telegram"
    )
    assert r2 is not None
    assert "arjun is supposed to pay ₹42,000 by friday" in r2.lower()
    
    # Draft must be cleaned up
    assert storage.get_draft("conv_clar") is None
    
    # Commitment must be created
    commitments = storage.get_unresolved_commitments("conv_clar")
    assert len(commitments) == 1
    assert commitments[0].amount_cents == 4200000


def test_missing_deadline_clarification(tmp_path):
    db_file = str(tmp_path / "test_deadline_clar.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    # Arjun will pay ₹42,000. (missing deadline)
    r1 = service.handle_user_message(
        conversation_id="conv_dl",
        sender={"address": "@user"},
        text="Arjun will pay ₹42,000.",
        message_id="m1",
        channel="telegram"
    )
    assert r1 is not None
    assert "When is this due?" in r1
    
    draft = storage.get_draft("conv_dl")
    assert draft["missing_field"] == "deadline"
    
    # Answer "Friday"
    r2 = service.handle_user_message(
        conversation_id="conv_dl",
        sender={"address": "@user"},
        text="Friday",
        message_id="m2",
        channel="telegram"
    )
    assert r2 is not None
    assert "arjun is supposed to pay ₹42,000 by friday" in r2.lower()
    
    assert storage.get_draft("conv_dl") is None
    commitments = storage.get_unresolved_commitments("conv_dl")
    assert len(commitments) == 1
    assert commitments[0].deadline_raw == "friday"


def test_quantity_not_interpreted_as_money(tmp_path):
    db_file = str(tmp_path / "test_qty.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    # "Supplier will deliver 42 units Friday."
    r = service.handle_user_message(
        conversation_id="conv_qty",
        sender={"address": "@user"},
        text="Supplier will deliver 42 units Friday.",
        message_id="m1",
        channel="telegram"
    )
    assert r is not None
    assert "supplier is supposed to deliver 42 units by friday" in r.lower()
    
    c = storage.get_unresolved_commitments("conv_qty")[0]
    assert c.amount_cents is None
    assert c.object == "42 units"


def test_token_behavior_metrics(tmp_path, monkeypatch):
    from bizpulse.storage import SQLiteStorage
    from bizpulse.service import BizPulseService
    import bizpulse.metrics as metrics
    
    metrics.reset_metrics()
    
    def mock_extract(*args, **kwargs):
        raise AssertionError("Gemini extractor should not be called!")
    monkeypatch.setattr("bizpulse.service.extract_commitment", mock_extract)
    
    db_file = str(tmp_path / "test_tokens.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    # 1. "Hi" -> filtered, onboarding returned, 0 LLM calls
    r1 = service.handle_user_message("c1", {"address": "@u"}, "Hi", "m1", "tel")
    assert "Hi! I'm BizPulse." in r1
    
    # 2. "Okay, thanks." -> filtered, Stage 1 clarification response, 0 LLM calls
    r2 = service.handle_user_message("c1", {"address": "@u"}, "Okay, thanks.", "m2", "tel")
    assert r2 is not None
    assert "I can help keep track" in r2
    
    # 3. Create command -> 0 LLM calls
    r3 = service.handle_user_message("c1", {"address": "@u"}, "/create payment | Arjun | Delta Traders | 42000 | Friday", "m3", "tel")
    assert "Created commitment" in r3
    
    m = metrics.get_metrics()
    assert m["llm_calls"] == 0
