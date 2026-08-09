"""BizPulse Commitment Domain Model."""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

VALID_TRANSITIONS = {
    'pending': {'due', 'overdue', 'rescheduled', 'disputed', 'abandoned', 'fulfillment_claimed'},
    'due': {'overdue', 'fulfillment_claimed', 'disputed', 'rescheduled'},
    'overdue': {'fulfillment_claimed', 'rescheduled', 'escalated', 'abandoned', 'disputed'},
    'rescheduled': {'due', 'overdue', 'fulfillment_claimed', 'abandoned'},
    'fulfillment_claimed': {'verified_fulfilled', 'disputed', 'overdue'},
    'escalated': {'fulfillment_claimed', 'abandoned'},
    'disputed': {'rescheduled', 'abandoned', 'fulfillment_claimed'},
    'verified_fulfilled': set(),
    'abandoned': set(),
}


class InvalidStateTransitionError(ValueError):
    """Raised when an invalid state transition is attempted on a commitment."""
    pass


@dataclass
class Commitment:
    id: str
    conversation_id: str
    party: str
    organization: str | None
    type: str  # payment | delivery | document | approval | service | other
    action: str
    object: str | None
    amount_cents: int | None
    residual_cents: int | None
    currency: str | None
    deadline_utc: datetime  # Python datetime (aware or naive UTC)
    deadline_raw: str | None
    timezone: str
    status: str  # pending | due | overdue | rescheduled | fulfillment_claimed | verified_fulfilled | disputed | escalated | abandoned
    source_message_id: str
    source_channel: str
    source_text: str
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_followup_at: datetime | None = None
    next_followup_at: datetime | None = None
    extraction_method: str | None = None  # llm | offline
    followup_count: int = 0
    confidence: float = 1.0
    active_job_id: str | None = None

    def transition_to(self, new_status: str) -> None:
        """Deterministically transition to a new status enforcing transition guards."""
        if new_status == self.status:
            return  # No-op if transitioning to the same state
        
        allowed = VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(
                f"Cannot transition commitment {self.id} from status '{self.status}' to '{new_status}'."
            )
        
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Convert Commitment instance to a dictionary for SQLite storage."""
        data = asdict(self)
        # Convert datetime fields to ISO strings for serialization
        for field_name in ["deadline_utc", "created_at", "updated_at", "last_followup_at", "next_followup_at"]:
            if data[field_name] and isinstance(data[field_name], datetime):
                data[field_name] = data[field_name].isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Commitment":
        """Reconstruct a Commitment instance from a dictionary."""
        # Make a copy to avoid mutating the original dict
        kwargs = data.copy()
        
        # Parse datetime fields
        for field_name in ["deadline_utc", "created_at", "updated_at", "last_followup_at", "next_followup_at"]:
            val = kwargs.get(field_name)
            if val:
                if isinstance(val, str):
                    dt = datetime.fromisoformat(val)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    kwargs[field_name] = dt
                elif isinstance(val, datetime):
                    if val.tzinfo is None:
                        kwargs[field_name] = val.replace(tzinfo=timezone.utc)
                    else:
                        kwargs[field_name] = val.astimezone(timezone.utc)
            else:
                kwargs[field_name] = None
        
        return cls(**kwargs)
