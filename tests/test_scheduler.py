"""Unit tests for BizPulse CommitmentScheduler."""

import time
import pytest
from datetime import datetime, timedelta, timezone
from bizpulse.scheduler import CommitmentScheduler

FIRED_COUNTS = {"count": 0}


def sample_callback(commitment_id):
    FIRED_COUNTS["count"] += 1


def test_schedule_and_fire(tmp_path):
    db_file = str(tmp_path / "test_scheduler.db")
    scheduler = CommitmentScheduler(db_path=db_file)
    scheduler.start()

    FIRED_COUNTS["count"] = 0

    # Schedule for 1 second in the future
    run_date = datetime.now(timezone.utc) + timedelta(seconds=1)
    job_id = scheduler.schedule_deadline_alert(
        commitment_id="c_123",
        run_date=run_date,
        callback=sample_callback
    )

    assert job_id.startswith("deadline_c_123")

    # Wait for job to fire
    time.sleep(1.5)

    assert FIRED_COUNTS["count"] == 1
    scheduler.shutdown()


def test_schedule_and_cancel_before_fire(tmp_path):
    db_file = str(tmp_path / "test_scheduler_cancel.db")
    scheduler = CommitmentScheduler(db_path=db_file)
    scheduler.start()

    FIRED_COUNTS["count"] = 0

    # Schedule for 2 seconds in the future
    run_date = datetime.now(timezone.utc) + timedelta(seconds=2)
    job_id = scheduler.schedule_deadline_alert(
        commitment_id="c_456",
        run_date=run_date,
        callback=sample_callback
    )

    # Cancel immediately before it fires
    canceled = scheduler.cancel_job(job_id)
    assert canceled is True

    # Wait past the 2s mark
    time.sleep(2.5)

    # Must NOT have fired
    assert FIRED_COUNTS["count"] == 0
    scheduler.shutdown()
