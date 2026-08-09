"""Unit tests for SQLiteStorage and Commitment state transitions."""

import pytest
from datetime import datetime, timedelta, timezone
from bizpulse.storage import SQLiteStorage
from bizpulse.commitments.models import Commitment, InvalidStateTransitionError


def test_commitment_lifecycle_and_transitions():
    conv_id = "test_conv_001"
    
    # 1. Create commitment
    c = Commitment(
        id="c_1",
        conversation_id=conv_id,
        party="Arjun",
        organization="Delta Traders",
        type="payment",
        action="pay",
        object="money",
        amount_cents=4200000,
        residual_cents=4200000,
        currency="INR",
        deadline_utc=datetime.now(timezone.utc) + timedelta(days=2),
        deadline_raw="Friday",
        timezone="Asia/Kolkata",
        status="pending",
        source_message_id="msg_001",
        source_channel="telegram",
        source_text="Arjun said he'll pay ₹42,000 Friday",
        confidence=0.95
    )

    # 2. Test valid transitions
    c.transition_to("overdue")
    assert c.status == "overdue"

    c.transition_to("fulfillment_claimed")
    assert c.status == "fulfillment_claimed"

    c.transition_to("verified_fulfilled")
    assert c.status == "verified_fulfilled"

    # 3. Test invalid transition (verified_fulfilled is terminal, should raise error)
    with pytest.raises(InvalidStateTransitionError):
        c.transition_to("pending")


def test_storage_crud(tmp_path):
    db_file = str(tmp_path / "test_storage.db")
    storage = SQLiteStorage(db_path=db_file)
    
    conv_id = "test_conv_002"
    c = Commitment(
        id="c_2",
        conversation_id=conv_id,
        party="Arjun",
        organization="Delta Traders",
        type="payment",
        action="pay",
        object="money",
        amount_cents=4200000,
        residual_cents=4200000,
        currency="INR",
        deadline_utc=datetime.now(timezone.utc) + timedelta(days=2),
        deadline_raw="Friday",
        timezone="Asia/Kolkata",
        status="pending",
        source_message_id="msg_002",
        source_channel="telegram",
        source_text="Arjun said he'll pay ₹42,000 Friday",
        confidence=0.95
    )

    # Save
    storage.save_commitment(c)

    # Retrieve
    retrieved = storage.get_commitment("c_2")
    assert retrieved is not None
    assert retrieved.party == "Arjun"
    assert retrieved.amount_cents == 4200000
    assert retrieved.status == "pending"

    # Update
    retrieved.transition_to("overdue")
    storage.save_commitment(retrieved)

    updated = storage.get_commitment("c_2")
    assert updated.status == "overdue"

    # Unresolved commitments lookup
    unresolved = storage.get_unresolved_commitments(conv_id)
    assert len(unresolved) == 1
    assert unresolved[0].id == "c_2"

    # Mark as verified_fulfilled (resolved)
    updated.transition_to("fulfillment_claimed")
    updated.transition_to("verified_fulfilled")
    storage.save_commitment(updated)

    unresolved_after = storage.get_unresolved_commitments(conv_id)
    assert len(unresolved_after) == 0
