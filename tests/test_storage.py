"""Unit tests for SQLiteStorage and thread safety."""

import pytest
from tripcaspian.storage import SQLiteStorage


def test_session_lifecycle(tmp_path):
    db_file = str(tmp_path / "test_storage.db")
    storage = SQLiteStorage(db_path=db_file)

    conv_id = "test_conv_001"
    fields = {"source": "Delhi", "destination": "Jaipur", "budget": 1200.0}

    # Save session
    storage.save_session(conv_id, "COLLECTING", fields)

    session = storage.get_session(conv_id)
    assert session is not None
    assert session["state_name"] == "COLLECTING"
    assert session["collected_fields"] == fields

    # Update session
    storage.save_session(conv_id, "RESULTS_SHOWN", fields, selected_provider="train", selected_option_id="opt_99")
    updated = storage.get_session(conv_id)
    assert updated["state_name"] == "RESULTS_SHOWN"
    assert updated["selected_provider"] == "train"
    assert updated["selected_option_id"] == "opt_99"


def test_watch_subscription_lifecycle(tmp_path):
    db_file = str(tmp_path / "test_watch.db")
    storage = SQLiteStorage(db_path=db_file)

    conv_id = "test_conv_002"
    storage.set_watch_subscription(
        conversation_id=conv_id,
        option_id="opt_123",
        provider_name="bus",
        source="Delhi",
        destination="Jaipur",
        watching=True,
        last_seats_left=4,
        last_price=750.0,
    )

    subs = storage.get_active_watch_subscriptions()
    assert len(subs) == 1
    assert subs[0]["conversation_id"] == conv_id
    assert subs[0]["last_seats_left"] == 4

    storage.cancel_watch_subscription(conv_id)
    subs_after = storage.get_active_watch_subscriptions()
    assert len(subs_after) == 0
