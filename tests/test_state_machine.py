"""Unit tests for BizPulse resolver correctness, partial fulfillment, duplicate checks, and timezones."""

import pytest
import datetime
from zoneinfo import ZoneInfo
from bizpulse.storage import SQLiteStorage
from bizpulse.service import BizPulseService
from bizpulse.commitments.extractor import resolve_relative_deadline


def test_timezone_relative_deadline_resolution():
    # Thursday Aug 13, 2026 at 11:00 PM IST (17:30 UTC)
    now_utc = datetime.datetime(2026, 8, 13, 17, 30, 0, tzinfo=datetime.timezone.utc)
    
    # "by Friday" should resolve to Friday Aug 14, 2026 at 23:59:00 IST
    deadline_utc = resolve_relative_deadline(now_utc, "by Friday", "Asia/Kolkata")
    
    # Friday EOD local is Aug 14 at 23:59:00 IST -> Aug 14 at 18:29:00 UTC
    expected_utc = datetime.datetime(2026, 8, 14, 18, 29, 0, tzinfo=datetime.timezone.utc)
    assert deadline_utc == expected_utc


def test_resolver_correctness_and_duplicate_prevention(tmp_path):
    db_file = str(tmp_path / "test_resolver.db")
    storage = SQLiteStorage(db_path=db_file)
    service = BizPulseService(storage=storage)
    
    conv_id = "chat_123"
    
    # 1. Send initial commitment message
    r1 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@arjun_user", "channel": "telegram"},
        text="Arjun from Delta Traders said he'll pay ₹42,000 by Friday.",
        message_id="msg_001",
        channel="telegram"
    )
    assert r1 is not None
    assert "New Commitment Detected" in r1
    
    commitments = storage.get_unresolved_commitments(conv_id)
    assert len(commitments) == 1
    c1 = commitments[0]
    assert c1.party == "Arjun"
    assert c1.amount_cents == 4200000
    assert c1.status == "pending"

    # 2. Duplicate message check (same message_id should be ignored)
    r_dup = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@arjun_user", "channel": "telegram"},
        text="Arjun from Delta Traders said he'll pay ₹42,000 by Friday.",
        message_id="msg_001",
        channel="telegram"
    )
    assert r_dup is None  # Ignored by deduplication
    
    # 3. Reschedule the existing commitment
    r2 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@arjun_user", "channel": "telegram"},
        text="Sorry, I'll pay Monday.",
        message_id="msg_002",
        channel="telegram"
    )
    assert r2 is not None
    assert "Commitment Rescheduled" in r2
    
    commitments_after_reschedule = storage.get_unresolved_commitments(conv_id)
    assert len(commitments_after_reschedule) == 1  # Still 1 commitment (not a new one)
    c1_updated = commitments_after_reschedule[0]
    assert c1_updated.status == "rescheduled"
    assert c1_updated.deadline_raw == "monday"

    # 4. Partial fulfillment check ("paid half")
    r3 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@arjun_user", "channel": "telegram"},
        text="I paid ₹21,000 (half).",
        message_id="msg_003",
        channel="telegram"
    )
    assert r3 is not None
    assert "Claimed partial payment" in r3
    
    c1_fulfilled = storage.get_commitment(c1.id)
    assert c1_fulfilled.status == "fulfillment_claimed"
    assert c1_fulfilled.residual_cents == 2100000  # Remaining residual ₹21,000
    assert "Remaining residual" in c1_fulfilled.notes

    # 5. Unrelated commitment check (creates a separate one)
    r4 = service.handle_user_message(
        conversation_id=conv_id,
        sender={"address": "@arjun_user", "channel": "telegram"},
        text="I will send the GST certificate tomorrow.",
        message_id="msg_004",
        channel="telegram"
    )
    assert r4 is not None
    assert "New Commitment Detected" in r4
    
    all_unresolved = storage.get_unresolved_commitments(conv_id)
    assert len(all_unresolved) == 2  # The payment one (fulfillment_claimed) and the document one (pending)
