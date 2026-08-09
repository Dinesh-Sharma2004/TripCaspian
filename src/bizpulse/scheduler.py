"""APScheduler wrapper with SQLiteJobStore for BizPulse persistent commitment deadline alerts.

Allows scheduling alerts when commitments reach their deadline.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Any
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

from bizpulse.config import DATABASE_PATH

logger = logging.getLogger(__name__)


class CommitmentScheduler:
    """Wrapper around APScheduler using SQLAlchemyJobStore with SQLite for job persistence."""

    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        jobstores = {
            'default': SQLAlchemyJobStore(url=f"sqlite:///{db_path}", tablename='apscheduler_jobs')
        }
        executors = {
            'default': ThreadPoolExecutor(5)
        }
        job_defaults = {
            'coalesce': False,
            'max_instances': 3
        }
        self.scheduler = BackgroundScheduler(
            jobstores=jobstores, executors=executors, job_defaults=job_defaults,
            timezone='UTC'
        )

    def start(self) -> None:
        """Start the background scheduler loop."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("CommitmentScheduler started with SQLiteJobStore.")

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)
            logger.info("CommitmentScheduler shut down.")

    def schedule_deadline_alert(
        self,
        commitment_id: str,
        run_date: datetime,
        callback: Callable[..., Any],
        *args,
        **kwargs,
    ) -> str:
        """Schedule a job to fire at the commitment deadline.

        Args:
            commitment_id: The ID of the commitment.
            run_date: Datetime in UTC when the job should fire.
            callback: Function to run on deadline.

        Returns:
            job_id string.
        """
        # Ensure run_date is timezone-naive UTC for APScheduler or timezone-aware matching the trigger
        # SQLite SQLAlchemyJobStore requires consistent datetime formats. Let's make sure it is naive UTC if timezone info is stripped or handled.
        if run_date.tzinfo is not None:
            # Convert to UTC and strip timezone info to avoid serialization mismatch in some sqlite configurations
            run_date = run_date.astimezone(timezone.utc).replace(tzinfo=None)

        job_id = f"deadline_{commitment_id}_{int(run_date.timestamp())}"

        self.scheduler.add_job(
            func=callback,
            trigger='date',
            run_date=run_date,
            args=[commitment_id] + list(args),
            kwargs=kwargs,
            id=job_id,
            replace_existing=True,
        )
        logger.info("Scheduled deadline alert job %s for commitment %s at %s", job_id, commitment_id, run_date)
        return job_id

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a scheduled job by job_id.

        Returns:
            True if job existed and was canceled, False otherwise.
        """
        try:
            self.scheduler.remove_job(job_id)
            logger.info("Canceled scheduled job %s", job_id)
            return True
        except Exception:
            logger.warning("Attempted to cancel job %s, but job was not found.", job_id)
            return False
