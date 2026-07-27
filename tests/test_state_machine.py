"""Unit tests for TripService multi-turn state machine logic."""

import pytest
from tripcaspian.storage import SQLiteStorage
from tripcaspian.service import TripService


def test_full_state_machine_flow(tmp_path):
    db_file = str(tmp_path / "test_sm.db")
    storage = SQLiteStorage(db_path=db_file)
    service = TripService(storage=storage)

    conv_id = "sm_conv_100"

    # Turn 1: Partial prompt
    reply1 = service.handle_user_message(conv_id, None, "I want to go to Jaipur")
    assert "Where" in reply1 and "traveling" in reply1

    # Turn 2: Provide source
    reply2 = service.handle_user_message(conv_id, None, "from Delhi to Jaipur")
    assert "What is your maximum budget" in reply2

    # Turn 3: Provide budget -> search & rank results returned!
    reply3 = service.handle_user_message(conv_id, None, "₹1200")
    assert "Top Travel Routes for Delhi ➡️ Jaipur" in reply3
    assert "1." in reply3
    assert "2." in reply3

    # Turn 4: Select option 1
    reply4 = service.handle_user_message(conv_id, None, "book option 1")
    assert "You selected Option 1" in reply4
    assert "book now" in reply4

    # Turn 5: Immediate handoff
    reply5 = service.handle_user_message(conv_id, None, "book now")
    assert "Your Trip Booking Handoff is Ready!" in reply5
    assert "Click here to book now" in reply5
