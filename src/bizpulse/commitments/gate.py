import re
from typing import Any

ACTION_SIGNALS = {
    "pay", "paid", "payment", "send", "sent", "deliver", "delivery", "submit", "submission",
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
    r"\d[\d,]*\s*(rupees?|inr|usd|dollars?|euros?|pounds?)",
    r"\b(payment|paid|refund|wire|transfer)\b"
]

OBLIGATION_SIGNALS = {
    "will", "shall", "promised", "agreed", "committed", "owe",
    "pending", "waiting", "expecting", "due", "supposed to", "should have", "hasn't", "haven't",
    "actually", "sorry", "reschedule", "instead"
}

STATUS_SIGNALS = {
    "confirmed", "received", "done", "completed", "paid",
    "dispatched", "sent", "delivered"
}

COUNTERPARTY_SIGNALS = {
    "supplier", "vendor", "client", "customer", "partner", "merchant", "provider", "buyer", "seller"
}


def evaluate_gate(text: str) -> dict[str, Any]:
    """Evaluate text and return a dictionary of signal analysis metadata."""
    if not text:
        return {"passed": False, "score": 0, "matched_categories": []}

    lower_text = text.lower()
    score = 0
    matched_categories = []

    # 1. Action verb match (+2)
    if any(re.search(rf"\b{word}\b", lower_text) for word in ACTION_SIGNALS):
        score += 2
        matched_categories.append("action")

    # 2. Temporal expression match (+2)
    if any(re.search(rf"\b{word}\b", lower_text) for word in TEMPORAL_SIGNALS):
        score += 2
        matched_categories.append("temporal")

    # 3. Money signal (+2)
    if any(re.search(pattern, lower_text) for pattern in MONEY_SIGNALS):
        score += 2
        matched_categories.append("money")

    # 4. Obligation language match (+2)
    if any(re.search(rf"\b{word}\b", lower_text) for word in OBLIGATION_SIGNALS):
        score += 2
        matched_categories.append("obligation")

    # 5. Status language match (+1)
    if any(re.search(rf"\b{word}\b", lower_text) for word in STATUS_SIGNALS):
        score += 1
        matched_categories.append("status")

    # 6. Counterparty match (+1)
    if any(re.search(rf"\b{word}\b", lower_text) for word in COUNTERPARTY_SIGNALS):
        score += 1
        matched_categories.append("counterparty")

    # Threshold for worth semantic analysis is 3
    passed = score >= 3
    return {
        "passed": passed,
        "score": score,
        "matched_categories": matched_categories
    }
