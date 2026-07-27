"""APScheduler wrapper with SQLiteJobStore for TripCaspian persistent auto-booking handoffs.

Allows scheduling delayed booking handoff notifications ("book in 30m", "book at 7pm")
and canceling active jobs before execution.
"""

from datetime import datetime, timedelta
import logging
from typing import Callable, Any
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class BookingScheduler:
    """Wrapper around APScheduler using SQLAlchemyJobStore with SQLite for job persistence."""

    def __init__(self, db_path: str = "tripcaspian.db"):
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
            jobstores=jobstores, executors=executors, job_defaults=job_defaults
        )

    def start(self) -> None:
        """Start the background scheduler loop."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("BookingScheduler started with SQLiteJobStore.")

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)

    def schedule_auto_booking(
        self,
        conversation_id: str,
        delay_seconds: int,
        callback: Callable[..., Any],
        *args,
        **kwargs,
    ) -> str:
        """Schedule a delayed booking handoff job.

        Args:
            conversation_id: The Caspian conversation ID.
            delay_seconds: Delay in seconds before firing.
            callback: The function to execute when the timer fires.

        Returns:
            job_id string.
        """
        run_date = datetime.now() + timedelta(seconds=delay_seconds)
        job_id = f"job_{conversation_id}_{int(run_date.timestamp())}"

        self.scheduler.add_job(
            func=callback,
            trigger='date',
            run_date=run_date,
            args=args,
            kwargs=kwargs,
            id=job_id,
            replace_existing=True,
        )
        logger.info("Scheduled auto-booking job %s for conversation %s at %s", job_id, conversation_id, run_date)
        return job_id

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a scheduled job by job_id.

        Returns:
            True if job existed and was canceled, False otherwise.
        """
        try:
            self.scheduler.remove_job(job_id)
            logger.info("Canceled auto-booking job %s", job_id)
            return True
        except Exception:
            logger.warning("Attempted to cancel job %s, but job was not found.", job_id)
            return False
