"""Background Commitment Watcher Daemon.

Polls active commitments and transitions them to overdue state if they miss their deadlines,
sending proactive alerts via Caspian client.
"""

import logging
import time
import threading
import datetime
from typing import Any

from bizpulse.storage import SQLiteStorage
from bizpulse.commitments.models import Commitment
from bizpulse.commitments.templates import get_overdue_alert_blocks, format_amount

logger = logging.getLogger(__name__)


class CommitmentWatcher:
    """Watcher daemon that polls active commitments and triggers overdue transitions."""

    def __init__(
        self,
        storage: SQLiteStorage,
        caspian_client: Any = None,
        poll_interval: int = 30,
    ):
        self.storage = storage
        self.client = caspian_client
        self.poll_interval = poll_interval
        self._running = False
        self._thread: threading.Thread | None = None

    def start_daemon(self, client: Any = None) -> None:
        """Start the background watcher thread."""
        if client:
            self.client = client

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="CommitmentWatcher")
        self._thread.start()
        logger.info("CommitmentWatcher daemon started (poll_interval=%ds).", self.poll_interval)

    def stop(self) -> None:
        """Stop the background watcher thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            logger.info("CommitmentWatcher daemon stopped.")

    def _run_loop(self) -> None:
        """Daemon polling execution loop."""
        while self._running:
            try:
                self.check_all_commitments()
            except Exception:
                logger.exception("Error during watcher polling execution loop.")
            time.sleep(self.poll_interval)

    def check_all_commitments(self) -> None:
        """Check all commitments in database for overdue deadlines."""
        commitments = self.storage.get_all_commitments()
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        for c in commitments:
            # Watcher checks commitments that are pending or rescheduled with deadlines in the past
            if c.status in ("pending", "rescheduled") and c.deadline_utc <= now_utc:
                if c.next_followup_at and c.next_followup_at > now_utc:
                    continue
                logger.info("Commitment %s deadline reached or passed (deadline=%s, now=%s)", c.id, c.deadline_utc, now_utc)
                self.trigger_overdue(c)

    def trigger_overdue(self, commitment: Commitment) -> None:
        """Transition commitment to overdue state and send Caspian alert."""
        try:
            # 1. Update commitment properties
            commitment.transition_to("overdue")
            commitment.followup_count += 1
            commitment.last_followup_at = datetime.datetime.now(datetime.timezone.utc)
            
            # 2. Save back to database
            self.storage.save_commitment(commitment)
            logger.info(
                "Commitment %s marked overdue (followup count: %d)",
                commitment.id, commitment.followup_count
            )

            # 3. Send proactive alert via Caspian CommClient if available
            if self.client:
                alert_text = (
                    f"⚠️ **{commitment.type.capitalize()} commitment overdue**\n"
                    f"{format_amount(commitment.amount_cents)} from {commitment.party} was expected "
                    f"{commitment.deadline_raw or commitment.deadline_utc.strftime('%Y-%m-%d')}."
                )
                alert_blocks = get_overdue_alert_blocks(commitment)
                
                try:
                    self.client.send_message(
                        conversation_id=commitment.conversation_id,
                        text=alert_text,
                        blocks=alert_blocks
                    )
                    logger.info("Sent proactive overdue message for commitment %s", commitment.id)
                except Exception:
                    logger.exception("Failed to send proactive overdue alert for commitment %s", commitment.id)
        except Exception:
            logger.exception("Error while triggering overdue on commitment %s", commitment.id)
