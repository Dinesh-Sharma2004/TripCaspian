"""BizPulse Message Templates.

Provides formatted text and Caspian rich blocks for alerts and notifications.
"""

from typing import Any
from caspian_sdk import blocks as b
from bizpulse.commitments.models import Commitment


def format_amount(cents: int | None) -> str:
    """Format cents to a readable currency string."""
    if cents is None:
        return "N/A"
    return f"₹{cents / 100:,.0f}"


def get_due_alert_blocks(commitment: Commitment) -> list[dict[str, Any]]:
    """Generate Caspian rich blocks for a commitment due today alert."""
    amount_str = format_amount(commitment.amount_cents)
    deadline_str = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
    org_str = f" ({commitment.organization})" if commitment.organization else ""
    
    return [
        b.heading("📌 Commitment Due Today"),
        b.text(f"The following {commitment.type} commitment from **{commitment.party}**{org_str} is due today."),
        b.fields([
            {"label": "Type", "value": commitment.type.capitalize()},
            {"label": "Party", "value": commitment.party},
            {"label": "Amount", "value": amount_str},
            {"label": "Deadline", "value": deadline_str},
        ]),
        b.buttons([
            {"label": "Remind", "value": f"remind:{commitment.id}"},
            {"label": "Mark Paid", "value": f"mark_paid:{commitment.id}"},
        ])
    ]


def get_overdue_alert_blocks(commitment: Commitment) -> list[dict[str, Any]]:
    """Generate Caspian rich blocks for a commitment overdue alert."""
    amount_str = format_amount(commitment.amount_cents)
    deadline_str = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
    org_str = f" ({commitment.organization})" if commitment.organization else ""
    
    return [
        b.heading("⚠️ Commitment Overdue"),
        b.text(f"The {commitment.type} commitment from **{commitment.party}**{org_str} is now **overdue**."),
        b.fields([
            {"label": "Amount", "value": amount_str},
            {"label": "Expected", "value": deadline_str},
            {"label": "Follow-ups", "value": str(commitment.followup_count)},
        ]),
        b.buttons([
            {"label": f"Remind {commitment.party}", "value": f"remind:{commitment.id}"},
            {"label": "Snooze (24h)", "value": f"snooze:{commitment.id}"},
            {"label": "Escalate", "value": f"escalate:{commitment.id}"},
        ])
    ]


def get_rescheduled_blocks(commitment: Commitment, old_deadline_raw: str | None) -> list[dict[str, Any]]:
    """Generate Caspian blocks for a commitment rescheduling notification."""
    old_raw = old_deadline_raw or "Previous"
    new_raw = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
    amount_str = format_amount(commitment.amount_cents)
    org_str = f" ({commitment.organization})" if commitment.organization else ""
    
    return [
        b.heading("Commitment Updated"),
        b.text(f"The {commitment.type} commitment from **{commitment.party}**{org_str} has been rescheduled."),
        b.fields([
            {"label": "Amount", "value": amount_str},
            {"label": "Timeline Change", "value": f"{old_raw} ➡️ {new_raw}"},
            {"label": "Status", "value": commitment.status.capitalize()},
        ])
    ]
