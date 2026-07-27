"""Unit tests for TripCaspian BookingScheduler."""

import time
import pytest
from tripcaspian.scheduler import BookingScheduler

FIRED_COUNTS = {"count": 0}


def sample_callback(conv_id):
    FIRED_COUNTS["count"] += 1


def test_schedule_and_fire(tmp_path):
    db_file = str(tmp_path / "test_scheduler.db")
    scheduler = BookingScheduler(db_path=db_file)
    scheduler.start()

    FIRED_COUNTS["count"] = 0

    job_id = scheduler.schedule_auto_booking(
        conversation_id="conv_123",
        delay_seconds=1,
        callback=sample_callback,
        conv_id="conv_123",
    )

    assert job_id.startswith("job_conv_123")

    # Wait for job to fire
    time.sleep(1.5)

    assert FIRED_COUNTS["count"] == 1
    scheduler.shutdown()


def test_schedule_and_cancel_before_fire(tmp_path):
    db_file = str(tmp_path / "test_scheduler_cancel.db")
    scheduler = BookingScheduler(db_path=db_file)
    scheduler.start()

    FIRED_COUNTS["count"] = 0

    job_id = scheduler.schedule_auto_booking(
        conversation_id="conv_456",
        delay_seconds=2,
        callback=sample_callback,
        conv_id="conv_456",
    )

    # Cancel immediately before it fires
    canceled = scheduler.cancel_job(job_id)
    assert canceled is True

    # Wait past the 2s mark
    time.sleep(2.5)

    # Must NOT have fired
    assert FIRED_COUNTS["count"] == 0
    scheduler.shutdown()
