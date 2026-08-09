"""Telegram and Email Integration Test Suite for BizPulse."""

import os
import pytest
from datetime import datetime, timezone, timedelta
from bizpulse.service import BizPulseService
from bizpulse.storage import SQLiteStorage


def test_bizpulse_conversation_flow(tmp_path):
    """Simulate complete BizPulse conversation lifecycle."""
    db_file = str(tmp_path / "telegram_test.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)

    conv_id = "telegram_chat_test_12345"

    # Step 1: Ingest commitment
    r1 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@traveler_user", "channel": "telegram"},
        text="Arjun from Delta Traders said he'll pay ₹42,000 by Friday.",
        message_id="m_001",
        channel="telegram"
    )
    assert r1 is not None
    assert "Arjun is supposed to pay" in r1
    assert "Arjun" in r1
    assert "₹42,000" in r1

    # Verify database state
    commitments = storage.get_unresolved_commitments(conv_id)
    assert len(commitments) == 1
    c = commitments[0]
    assert c.status == "pending"

    # Step 2: Simulate deadline reached (advance time in DB)
    c.deadline_utc = datetime.now(timezone.utc) - timedelta(minutes=5)
    storage.save_commitment(c)
    
    # Run Watcher check
    service.watcher.check_all_commitments()

    # Verify commitment is now overdue
    c_updated = storage.get_commitment(c.id)
    assert c_updated.status == "overdue"
    assert c_updated.followup_count == 1

    # Step 3: Reschedule
    r2 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@traveler_user", "channel": "telegram"},
        text="Sorry, I'll pay Monday.",
        message_id="m_002",
        channel="telegram"
    )
    assert r2 is not None
    assert "Commitment Rescheduled" in r2
    assert "monday" in r2.lower()
    c_rescheduled = storage.get_commitment(c.id)
    assert c_rescheduled.status == "rescheduled"

    # Step 4: Fulfillment claim
    r3 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@traveler_user", "channel": "telegram"},
        text="Payment sent.",
        message_id="m_003",
        channel="telegram"
    )
    assert r3 is not None
    assert "Fulfillment Claimed" in r3

    c_fulfilled = storage.get_commitment(c.id)
    assert c_fulfilled.status == "fulfillment_claimed"
