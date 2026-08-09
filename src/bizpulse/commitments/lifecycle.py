"""BizPulse Commitment Lifecycle Management.

Enforces state machine rules, handles scheduler cancellations, and partial fulfillments.
"""

import logging
import re
from datetime import datetime
from typing import Any

from bizpulse.commitments.models import Commitment

logger = logging.getLogger(__name__)


def apply_reschedule(
    commitment: Commitment,
    new_deadline_utc: datetime,
    new_deadline_raw: str | None,
    scheduler: Any,
    callback_fn: Any
) -> None:
    """Reschedule an existing commitment, canceling the old job and scheduling a new one."""
    # Enforce transition guard
    commitment.transition_to("rescheduled")
    
    commitment.deadline_utc = new_deadline_utc
    if new_deadline_raw:
        commitment.deadline_raw = new_deadline_raw

    # Cancel existing scheduler job
    if commitment.active_job_id:
        try:
            scheduler.cancel_job(commitment.active_job_id)
            logger.info("Canceled existing job %s for commitment %s", commitment.active_job_id, commitment.id)
        except Exception as e:
            logger.warning(
                "Job %s cancel failed (may have already fired or expired): %s",
                commitment.active_job_id, e
            )
            
    # Schedule new job
    if scheduler:
        try:
            # We schedule it to fire at the new deadline
            job_id = scheduler.schedule_deadline_alert(
                commitment_id=commitment.id,
                run_date=new_deadline_utc,
                callback=callback_fn
            )
            commitment.active_job_id = job_id
            logger.info("Scheduled new job %s for commitment %s at %s", job_id, commitment.id, new_deadline_utc)
        except Exception:
            logger.exception("Failed to schedule reschedule job for commitment %s", commitment.id)
            commitment.active_job_id = None


def apply_fulfillment(commitment: Commitment, text: str) -> None:
    """Transition commitment to fulfillment_claimed, checking for partial fulfillment amounts."""
    commitment.transition_to("fulfillment_claimed")
    
    # Check if a partial payment amount was mentioned in the text
    # e.g., "paid ₹20,000", "sent ₹8,000"
    lower_text = text.lower()
    money_match = re.search(r'(?:₹|rs\.?|inr|usd|\$)\s*([0-9,]+)', lower_text)
    
    paid_cents = None
    if money_match:
        try:
            val = int(money_match.group(1).replace(",", ""))
            paid_cents = val * 100
        except ValueError:
            pass
            
    if paid_cents and commitment.amount_cents and paid_cents < commitment.amount_cents:
        residual = commitment.amount_cents - paid_cents
        commitment.residual_cents = residual
        commitment.notes = f"Partial fulfillment claimed. Paid ₹{paid_cents/100:,.0f} of ₹{commitment.amount_cents/100:,.0f}. Remaining residual: ₹{residual/100:,.0f}."
        logger.info(
            "Partial fulfillment detected. Paid: %d, Residual: %d cents",
            paid_cents, residual
        )
    else:
        commitment.residual_cents = 0
        commitment.notes = "Fulfillment claimed for the full amount."
        logger.info("Full fulfillment claimed for commitment %s", commitment.id)


def apply_dispute(commitment: Commitment, text: str) -> None:
    """Transition commitment to disputed status and record evidence text."""
    commitment.transition_to("disputed")
    commitment.notes = f"Disputed by counterparty: '{text}'"
    logger.info("Commitment %s marked as disputed.", commitment.id)
