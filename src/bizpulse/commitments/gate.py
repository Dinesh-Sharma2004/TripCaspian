"""BizPulse Gate Module.

Heuristic signal scoring to determine if a message should be analyzed by the LLM.
"""

import re

ACTION_SIGNALS = {
    "pay", "payment", "send", "deliver", "delivery", "submit", "submission",
    "provide", "provision", "ship", "shipment", "dispatch", "approve", "approval",
    "confirm", "confirmation", "complete", "completion", "sign", "signature",
    "refund", "transfer", "review", "finish"
}

TEMPORAL_SIGNALS = {
    "today", "tomorrow", "tonight", "friday", "monday", "tuesday",
    "wednesday", "thursday", "saturday", "sunday", "next week",
    "by", "before", "until", "eod", "end of day", "january", "february",
    "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december"
}

MONEY_SIGNALS = [
    r"[₹$€£]\s*[\d,]+",
    r"\d[\d,]*\s*(rupees?|inr|usd|dollars?|euros?|pounds?)"
]

OBLIGATION_SIGNALS = {
    "will", "shall", "promised", "agreed", "committed", "owe",
    "pending", "waiting", "expecting", "due", "supposed to", "should have"
}

STATUS_SIGNALS = {
    "confirmed", "received", "done", "completed", "paid",
    "dispatched", "sent", "delivered"
}


def evaluate_gate(text: str) -> bool:
    """Evaluate text and return True if it scores >= 3, meaning it should go to the LLM."""
    if not text:
        return False

    lower_text = text.lower()
    score = 0

    # 1. Action verb match (+2)
    # Check word boundaries for the signal words
    if any(re.search(rf"\b{word}\b", lower_text) for word in ACTION_SIGNALS):
        score += 2

    # 2. Temporal expression match (+2)
    # E.g. "by Friday" or word boundaries
    if any(re.search(rf"\b{word}\b", lower_text) for word in TEMPORAL_SIGNALS):
        score += 2

    # 3. Money signal (+2)
    if any(re.search(pattern, lower_text) for pattern in MONEY_SIGNALS):
        score += 2

    # 4. Obligation language match (+2)
    if any(re.search(rf"\b{word}\b", lower_text) for word in OBLIGATION_SIGNALS):
        score += 2

    # 5. Status language match (+1)
    if any(re.search(rf"\b{word}\b", lower_text) for word in STATUS_SIGNALS):
        score += 1

    return score >= 3
