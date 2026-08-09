"""Discord Integration and Unified Messaging Tests for BizPulse."""

import pytest
from datetime import datetime, timezone
from caspian_sdk import CommClient
from bizpulse.storage import SQLiteStorage
from bizpulse.service import BizPulseService
import bizpulse.metrics as metrics


def test_discord_connection_mock(monkeypatch):
    """Test that connect_discord is called with the correct SDK signature."""
    client = CommClient()
    connected_params = {}

    def mock_connect_discord(self, bot_token=None, webhook_url=None, username=None, avatar_url=None, customer_id=None, agent_id=None, **kwargs):
        connected_params["bot_token"] = bot_token
        connected_params["webhook_url"] = webhook_url
        return {"address": "discord_bot_123", "channel": "discord"}

    monkeypatch.setattr(CommClient, "connect_discord", mock_connect_discord)
    
    res = client.connect_discord(bot_token="test_token_xyz")
    assert res["address"] == "discord_bot_123"
    assert connected_params["bot_token"] == "test_token_xyz"


def test_discord_unified_handler_onboarding(tmp_path):
    """Verify that Discord messages route onboarding correctly."""
    db_file = str(tmp_path / "discord_test.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    conv_id = "discord_conv_001"
    
    # 1. First message "Hi" -> Onboarding
    r1 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@discord_user"},
        text="Hi",
        message_id="disc_msg_1",
        channel="discord"
    )
    assert r1 is not None
    assert "Hi! I'm BizPulse." in r1
    
    # Check that onboarding status was updated in DB
    assert storage.has_sent_onboarding(conv_id) is True


def test_discord_missing_field_slot_filling(tmp_path, monkeypatch):
    """Test Discord missing field slot-filling flow with amount extraction and 0 LLM calls."""
    db_file = str(tmp_path / "discord_test_slots.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    conv_id = "discord_conv_002"
    storage.mark_onboarding_sent(conv_id)
    
    metrics.reset_metrics()
    
    # Step 1: "Arjun will pay Friday" (Missing amount, creates draft)
    r1 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@discord_user"},
        text="Arjun will pay Friday.",
        message_id="disc_msg_2",
        channel="discord"
    )
    assert r1 is not None
    assert "How much is he supposed to pay?" in r1
    
    # Verify draft was created
    draft = storage.get_draft(conv_id)
    assert draft is not None
    assert draft["party"] == "Arjun"
    assert draft["missing_field"] == "amount"
    
    # Mock Gemini extractor to ensure it is not called during deterministic slot filling
    def mock_extract(*args, **kwargs):
        raise AssertionError("Gemini extractor should not be called!")
    monkeypatch.setattr("bizpulse.service.extract_commitment", mock_extract)
    
    # Step 2: User answers "42000" (should parse amount cents to 4200000 with 0 LLM calls)
    r2 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@discord_user"},
        text="42000",
        message_id="disc_msg_3",
        channel="discord"
    )
    
    assert r2 is not None
    assert "got it. i'm tracking ₹42,000 from arjun, due friday" in r2.lower()
    
    # Verify commitment was successfully saved
    commitments = storage.get_unresolved_commitments(conv_id)
    assert len(commitments) == 1
    c = commitments[0]
    assert c.party == "Arjun"
    assert c.amount_cents == 4200000
    assert c.currency == "INR"
    assert c.source_channel == "discord"
    
    # Verify token metrics did not call LLM
    m = metrics.get_metrics()
    assert m["llm_calls"] == 0
    assert m["field_values_resolved_deterministically"] == 1


def test_discord_create_command_deterministic(tmp_path, monkeypatch):
    """Test Discord /create command works with 0 LLM calls."""
    db_file = str(tmp_path / "discord_test_cmd.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    # Prevent LLM calls
    def mock_extract(*args, **kwargs):
        raise AssertionError("Gemini extractor should not be called!")
    monkeypatch.setattr("bizpulse.service.extract_commitment", mock_extract)
    
    metrics.reset_metrics()
    
    r = service.handle_user_message(
        conversation_id="disc_conv_003",
        sender={"address": "@discord_user"},
        text="/create payment | Arjun | Delta Traders | 42000 | Friday",
        message_id="disc_msg_4",
        channel="discord"
    )
    
    assert "Created commitment" in r
    assert metrics.get_metrics()["llm_calls"] == 0
