"""Natural Language Trip Query Parser and Follow-up Question Generator.

Parses free-text trip requests into structured TripQuery objects.
Preserves existing conversation fields and generates single targeted follow-up questions
for missing fields without re-asking already answered details.
"""

import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class TripQuery:
    """Structured trip inquiry parameters."""

    source: str | None = None
    destination: str | None = None
    depart_time: str | None = None
    max_travel_time_hours: float | None = None
    budget: float | None = None
    concession_pct: float | None = None

    def is_complete(self) -> bool:
        """Check if all minimum required fields (source, destination, budget) are present."""
        return bool(self.source and self.destination and self.budget is not None)

    def to_dict(self) -> dict[str, Any]:
        """Convert query to dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TripQuery":
        """Construct TripQuery from dictionary."""
        return cls(**data)


def parse_trip_request(
    text: str, existing: dict[str, Any] | None = None
) -> TripQuery:
    """Parse user text for travel fields while incorporating any existing collected fields."""
    base = existing.copy() if existing else {}

    source = base.get("source")
    destination = base.get("destination")
    depart_time = base.get("depart_time")
    max_travel_time_hours = base.get("max_travel_time_hours")
    budget = base.get("budget")
    concession_pct = base.get("concession_pct")

    # Clean input
    clean_text = text.strip()

    # Budget Extraction (e.g. ₹1200, Rs 1500, 1200 INR, 1500 rupees, budget 2000, under 1000)
    budget_patterns = [
        r'(?:₹|rs\.?|inr|budget(?: of)?|under|max|around)\s*([0-9,]+)',
        r'([0-9,]+)\s*(?:₹|rs\.?|inr|rupees?|bucks)',
    ]
    for pattern in budget_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            raw_val = match.group(1).replace(',', '')
            try:
                val = float(raw_val)
                if val > 50:  # Ignore low numbers like 5% or dates
                    budget = val
                    break
            except ValueError:
                pass

    # Direct number input when budget is missing (e.g. user replies "1200" or "₹800")
    if budget is None and re.match(r'^(?:₹|rs\.?|rupees?)?\s*([0-9,]+)\s*(?:rs\.?|rupees?)?$', clean_text, re.IGNORECASE):
        match = re.match(r'^(?:₹|rs\.?|rupees?)?\s*([0-9,]+)\s*(?:rs\.?|rupees?)?$', clean_text, re.IGNORECASE)
        if match:
            budget = float(match.group(1).replace(',', ''))

    # Concession percentage extraction (e.g. 5%, 10%, 8% overshoot)
    concession_match = re.search(r'([0-9]+)\s*%\s*(?:concession|overshoot|over|extra)?', clean_text, re.IGNORECASE)
    if concession_match:
        try:
            val = float(concession_match.group(1)) / 100.0
            if 0.0 <= val <= 0.25:
                concession_pct = val
        except ValueError:
            pass

    # Source & Destination Extraction (e.g. "from Delhi to Jaipur", "Delhi to Jaipur", "Mumbai -> Pune")
    from_to_match = re.search(
        r'\bfrom\s+([a-zA-Z\s]+?)\s+(?:to|->|-)\s+([a-zA-Z\s]+?)(?:\s+(?:tomorrow|today|at|under|budget|for|on|with|in|rs|rupees|\d)|$)',
        clean_text,
        re.IGNORECASE,
    )
    if from_to_match:
        src_candidate = from_to_match.group(1).strip()
        dst_candidate = from_to_match.group(2).strip()
        source = src_candidate.title()
        destination = dst_candidate.title()
    else:
        route_match = re.search(
            r'\b([a-zA-Z]{3,})\s+(?:to|->|-)\s+([a-zA-Z]{3,})\b',
            clean_text,
            re.IGNORECASE,
        )
        if route_match:
            src_candidate = route_match.group(1).strip()
            dst_candidate = route_match.group(2).strip()
            invalid_words = {'trip', 'book', 'budget', 'can', 'please', 'want', 'need', 'like', 'from'}
            if src_candidate.lower() not in invalid_words and dst_candidate.lower() not in invalid_words:
                if not source:
                    source = src_candidate.title()
                if not destination:
                    destination = dst_candidate.title()

    # Time Window Extraction (e.g. "tomorrow 8am", "6:00 AM", "morning", "7pm")
    time_match = re.search(
        r'(\b(?:tomorrow|today|morning|evening|afternoon|night|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b)',
        clean_text,
        re.IGNORECASE,
    )
    if time_match and not depart_time:
        depart_time = time_match.group(1).strip()

    # Maximum Travel Duration Extraction (e.g. "under 6 hours", "max 5 hrs travel time", "duration under 8h")
    duration_match = re.search(
        r'(?:under|max|within|less than|travel time under|duration under|take at most)\s*([0-9.]+)\s*(?:hours?|hrs?|h)\b',
        clean_text,
        re.IGNORECASE,
    )
    if not duration_match:
        duration_match = re.search(
            r'([0-9.]+)\s*(?:hours?|hrs?|h)\s*(?:max|travel time|duration|limit)',
            clean_text,
            re.IGNORECASE,
        )
    if duration_match and not max_travel_time_hours:
        try:
            max_travel_time_hours = float(duration_match.group(1))
        except ValueError:
            pass

    return TripQuery(
        source=source,
        destination=destination,
        depart_time=depart_time,
        max_travel_time_hours=max_travel_time_hours,
        budget=budget,
        concession_pct=concession_pct,
    )


def generate_followup_question(query: TripQuery) -> str | None:
    """Generate a single targeted follow-up question for missing required fields."""
    if not query.source and not query.destination:
        return "Where are you traveling from and to? (e.g. Delhi to Jaipur)"
    if not query.source:
        return f"Got it! Where will you be departing from to reach {query.destination}?"
    if not query.destination:
        return f"Got it, starting from {query.source}! Where is your destination?"
    if query.budget is None:
        return f"Understood! What is your maximum budget and preferred departure/travel duration limit for the trip from {query.source} to {query.destination}? (e.g. ₹1200, tomorrow morning, under 6 hours)"

    return None
