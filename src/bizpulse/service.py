"""BizPulse Service layer orchestrator.

Implements the multi-stage message processing pipeline and handles button interactions.
"""

import logging
import datetime
from typing import Any, Tuple

from bizpulse.config import DEFAULT_TIMEZONE
from bizpulse.storage import SQLiteStorage
from bizpulse.scheduler import CommitmentScheduler
from bizpulse.watcher import CommitmentWatcher
from bizpulse.commitments.models import Commitment
from bizpulse.commitments.normalizer import normalize_message
from bizpulse.commitments.gate import evaluate_gate
from bizpulse.commitments.extractor import extract_commitment, validate_extraction
from bizpulse.commitments.resolver import resolve_commitment
from bizpulse.commitments.lifecycle import apply_reschedule, apply_fulfillment, apply_dispute
from bizpulse.commitments.templates import get_rescheduled_blocks, format_amount

logger = logging.getLogger(__name__)


class BizPulseService:
    """Core orchestrator for BizPulse pipeline and interactions."""

    def __init__(
        self,
        storage: SQLiteStorage | None = None,
        scheduler: CommitmentScheduler | None = None,
        watcher: CommitmentWatcher | None = None,
        caspian_client: Any = None,
    ):
        self.storage = storage or SQLiteStorage()
        self.scheduler = scheduler or CommitmentScheduler()
        self.watcher = watcher or CommitmentWatcher(storage=self.storage, caspian_client=caspian_client)
        self.client = caspian_client
        self._processed_messages: set[Tuple[str, str]] = set()  # (channel, message_id)

    def set_caspian_client(self, client: Any) -> None:
        """Bind Caspian SDK client instance."""
        self.client = client
        self.watcher.client = client

    def handle_user_message(
        self,
        conversation_id: str,
        sender: dict | None,
        text: str,
        message_id: str,
        channel: str,
        subject: str | None = None,
    ) -> str | None:
        """Process incoming messages through the BizPulse pipeline.

        Returns:
            Clarification request or confirmation text, or None if message is ignored.
        """
        # Stage 1: Deduplicate
        dedup_key = (channel, message_id)
        if dedup_key in self._processed_messages:
            logger.info("Duplicate message %s ignored.", message_id)
            return None
        self._processed_messages.add(dedup_key)

        # Stage 2: Normalize
        normalized_text = normalize_message(text, subject)
        logger.info("Normalized message text: '%s'", normalized_text)

        # Stage 3: Gate
        if not evaluate_gate(normalized_text):
            logger.info("Message did not pass signal gate. Ignoring (0 tokens spent).")
            return None
        logger.info("Message passed signal gate. Proceeding to extraction.")

        # Stage 4: Extraction
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        extracted = extract_commitment(normalized_text, now_utc, DEFAULT_TIMEZONE)
        logger.info("Extracted payload: %s", extracted)

        # Stage 5: Validate
        validation_status = validate_extraction(extracted)
        if validation_status == "ignore":
            logger.info("Extraction validation result: ignore.")
            return None
        elif validation_status == "needs_review":
            logger.warning("Extraction validation failed or confidence low. Requesting clarification.")
            return (
                "⚠️ I heard you mention a commitment, but I couldn't capture all the details. "
                "Could you please clarify who is promising what, and by when? (E.g. 'Arjun will pay ₹42,000 by Friday')"
            )

        # Stage 6: Resolve
        unresolved = self.storage.get_unresolved_commitments(conversation_id)
        commitment, action = resolve_commitment(
            candidate=extracted,
            unresolved_commitments=unresolved,
            conversation_id=conversation_id,
            message_id=message_id,
            channel=channel,
            source_text=text,
            tz_name=DEFAULT_TIMEZONE
        )

        # Stage 7 & 8: Lifecycle update and Schedule/Watcher
        if action == "create" and commitment:
            # Save commitment to database
            self.storage.save_commitment(commitment)
            
            # Schedule deadline job
            if self.scheduler:
                job_id = self.scheduler.schedule_deadline_alert(
                    commitment_id=commitment.id,
                    run_date=commitment.deadline_utc,
                    callback=self.on_deadline_reached
                )
                commitment.active_job_id = job_id
                self.storage.save_commitment(commitment)
                
            deadline_str = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
            amount_str = format_amount(commitment.amount_cents)
            
            logger.info("Created new commitment %s", commitment.id)
            return (
                f"✅ **New Commitment Detected**\n"
                f"• **Party**: {commitment.party}\n"
                f"• **Type**: {commitment.type.capitalize()}\n"
                f"• **Amount**: {amount_str}\n"
                f"• **Deadline**: {deadline_str}\n"
                f"• **Status**: {commitment.status.capitalize()}"
            )

        elif action == "update" and commitment:
            intent = extracted.get("intent")
            
            if intent == "reschedule":
                old_raw = commitment.deadline_raw
                deadline_utc_str = extracted.get("deadline_utc")
                if deadline_utc_str:
                    if deadline_utc_str.endswith('Z'):
                        deadline_utc_str = deadline_utc_str[:-1] + '+00:00'
                    new_deadline_utc = datetime.datetime.fromisoformat(deadline_utc_str)
                else:
                    new_deadline_utc = datetime.datetime.utcnow() + datetime.timedelta(days=3)
                    
                apply_reschedule(
                    commitment=commitment,
                    new_deadline_utc=new_deadline_utc,
                    new_deadline_raw=extracted.get("deadline_raw"),
                    scheduler=self.scheduler,
                    callback_fn=self.on_deadline_reached
                )
                self.storage.save_commitment(commitment)
                
                new_raw = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
                logger.info("Rescheduled commitment %s to %s", commitment.id, new_deadline_utc)
                return (
                    f"🔄 **Commitment Rescheduled**\n"
                    f"• **Party**: {commitment.party}\n"
                    f"• **Change**: {old_raw or 'Previous'} ➡️ {new_raw}\n"
                    f"• **Status**: {commitment.status.capitalize()}"
                )
                
            elif intent == "fulfillment":
                apply_fulfillment(commitment, text)
                self.storage.save_commitment(commitment)
                
                logger.info("Fulfillment claimed for commitment %s", commitment.id)
                if commitment.residual_cents and commitment.residual_cents > 0:
                    paid_str = format_amount(commitment.amount_cents - commitment.residual_cents)
                    residual_str = format_amount(commitment.residual_cents)
                    return (
                        f"Claimed partial payment of {paid_str}.\n"
                        f"Remaining residual amount outstanding is **{residual_str}**.\n"
                        f"Status: {commitment.status.capitalize()}"
                    )
                else:
                    return (
                        f"👍 **Fulfillment Claimed**\n"
                        f"{commitment.party} claims to have fulfilled their commitment.\n"
                        f"Status: {commitment.status.capitalize()} (awaiting verification)"
                    )
                    
            elif intent == "dispute":
                apply_dispute(commitment, text)
                self.storage.save_commitment(commitment)
                
                logger.info("Disputed commitment %s", commitment.id)
                return (
                    f"⚠️ **Commitment Disputed**\n"
                    f"Counterparty {commitment.party} has disputed this obligation.\n"
                    f"Status: {commitment.status.capitalize()}"
                )

        return None

    def handle_interaction(self, value: str) -> Tuple[str, list[dict[str, Any]] | None]:
        """Handle button click interaction callback.

        Returns:
            (reply_text, blocks)
        """
        if ":" not in value:
            return "Invalid interaction payload.", None
            
        action, commitment_id = value.split(":", 1)
        commitment = self.storage.get_commitment(commitment_id)
        if not commitment:
            return "Error: Commitment not found.", None

        if action == "remind":
            amount_str = format_amount(commitment.amount_cents)
            deadline_str = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
            
            # Follow-up outreach message
            outreach = (
                f"Hi {commitment.party}, following up on the {amount_str} payment "
                f"expected by {deadline_str}. Could you share the status?"
            )
            return outreach, None

        elif action == "snooze":
            # Extend deadline by 24h
            old_deadline = commitment.deadline_utc
            new_deadline = old_deadline + datetime.timedelta(hours=24)
            
            # Transition back to pending
            commitment.status = "pending"
            commitment.deadline_utc = new_deadline
            commitment.deadline_raw = f"{commitment.deadline_raw or ''} (snoozed)".strip()
            
            # Reschedule alert job
            if commitment.active_job_id:
                try:
                    self.scheduler.cancel_job(commitment.active_job_id)
                except Exception:
                    pass
            if self.scheduler:
                job_id = self.scheduler.schedule_deadline_alert(
                    commitment_id=commitment.id,
                    run_date=new_deadline,
                    callback=self.on_deadline_reached
                )
                commitment.active_job_id = job_id
                
            self.storage.save_commitment(commitment)
            logger.info("Snoozed commitment %s by 24 hours to %s", commitment.id, new_deadline)
            
            return f"⏳ **Commitment snoozed for 24 hours.** New deadline: {new_deadline.strftime('%Y-%m-%d %H:%M:%S UTC')}", None

        elif action == "escalate":
            commitment.status = "escalated"
            self.storage.save_commitment(commitment)
            logger.info("Escalated commitment %s", commitment.id)
            
            return f"⚠️ **Obligation escalated.** Counterparty: {commitment.party} ({commitment.organization or 'No Org'})", None

        elif action == "mark_paid":
            commitment.status = "verified_fulfilled"
            self.storage.save_commitment(commitment)
            logger.info("Commitment %s verified as fulfilled.", commitment.id)
            
            return f"✅ **Verified Fulfilled.** Commitment #{commitment.id} is closed.", None

        return "Unknown action.", None

    def on_deadline_reached(self, commitment_id: str) -> None:
        """Callback invoked by scheduler when a commitment deadline is reached."""
        commitment = self.storage.get_commitment(commitment_id)
        if not commitment:
            logger.warning("Scheduled alert fired for missing commitment %s", commitment_id)
            return

        # Trigger overdue logic if still pending or rescheduled
        if commitment.status in ("pending", "rescheduled"):
            if self.watcher:
                self.watcher.trigger_overdue(commitment)
