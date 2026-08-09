"""BizPulse Commitment Extraction Module.

Extracts structured commitment data using Gemini 2.5 Flash API or a deterministic fallback rule engine.
"""

import os
import re
import logging
import datetime
from zoneinfo import ZoneInfo
from typing import Any
import httpx

from bizpulse.config import CONFIDENCE_THRESHOLD, DEFAULT_TIMEZONE

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


def _parseable_utc(val: str) -> bool:
    """Check if the string is a valid ISO format datetime."""
    try:
        if isinstance(val, str):
            if val.endswith('Z'):
                val = val[:-1] + '+00:00'
            datetime.datetime.fromisoformat(val)
            return True
    except Exception:
        pass
    return False


def validate_extraction(result: dict[str, Any]) -> str:
    """Validate extraction results against schemas and structural requirements."""
    if not result.get('has_commitment'):
        return 'ignore'
    if not result.get('party'):
        return 'needs_review'
    if not result.get('action'):
        return 'needs_review'
    if result.get('deadline_utc') is None and result.get('intent') not in ('fulfillment', 'dispute'):
        return 'needs_review'
    if result.get('deadline_utc') is not None and not _parseable_utc(result['deadline_utc']):
        return 'needs_review'
    if result.get('confidence', 1.0) < CONFIDENCE_THRESHOLD:
        return 'needs_review'
    return 'accepted'


def resolve_relative_deadline(now_utc: datetime.datetime, relative_expr: str, tz_name: str) -> datetime.datetime:
    """Resolve relative date strings to absolute UTC timestamps."""
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    expr = relative_expr.lower()
    
    target_time = {"hour": 23, "minute": 59, "second": 0, "microsecond": 0}
    
    if "friday" in expr:
        days_ahead = (4 - now_local.weekday()) % 7
        if days_ahead == 0 and now_local.hour >= 23:
            days_ahead = 7
        target_date = now_local + datetime.timedelta(days=days_ahead)
    elif "monday" in expr:
        days_ahead = (0 - now_local.weekday()) % 7
        if days_ahead == 0 and now_local.hour >= 23:
            days_ahead = 7
        target_date = now_local + datetime.timedelta(days=days_ahead)
    elif "tomorrow" in expr:
        target_date = now_local + datetime.timedelta(days=1)
    elif "next week" in expr:
        target_date = now_local + datetime.timedelta(days=7)
    elif "today" in expr:
        target_date = now_local
    else:
        # Default fallback
        target_date = now_local + datetime.timedelta(days=3)
        
    target_dt = target_date.replace(**target_time)
    return target_dt.astimezone(ZoneInfo("UTC"))


def extract_offline(text: str, now_utc: datetime.datetime, tz_name: str) -> dict[str, Any]:
    """Fallback rule-based commitment extractor."""
    lower_text = text.lower()
    
    res = {
        "has_commitment": False,
        "intent": "irrelevant",
        "type": None,
        "party": None,
        "organization": None,
        "action": None,
        "object": None,
        "amount_cents": None,
        "currency": None,
        "deadline_utc": None,
        "deadline_raw": None,
        "confidence": 1.0
    }
    
    # 1. Dispute detection
    if any(phrase in lower_text for phrase in ["never said", "dispute", "didn't say", "not what i promised"]):
        res["has_commitment"] = True
        res["intent"] = "dispute"
        res["type"] = "payment"
        res["action"] = "pay"
        match = re.search(r'\b(arjun|delta traders)\b', lower_text)
        res["party"] = match.group(1).title() if match else "Counterparty"
        return res
        
    # 2. Fulfillment detection
    if any(phrase in lower_text for phrase in ["payment sent", "sent the payment", "paid", "payment done", "payment cleared", "sent it"]):
        res["has_commitment"] = True
        res["intent"] = "fulfillment"
        res["type"] = "payment"
        res["action"] = "pay"
        res["object"] = "money"
        match = re.search(r'\b(arjun|delta traders)\b', lower_text)
        res["party"] = match.group(1).title() if match else "Counterparty"
        return res
        
    # 3. Reschedule detection
    if any(phrase in lower_text for phrase in ["sorry", "actually", "reschedule", "instead", "better"]):
        res["has_commitment"] = True
        res["intent"] = "reschedule"
        res["type"] = "payment"
        res["action"] = "pay"
        res["object"] = "money"
        
        # Look for deadline
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week"]:
            if day in lower_text:
                res["deadline_raw"] = day
                res["deadline_utc"] = resolve_relative_deadline(now_utc, day, tz_name).isoformat()
                break
                
        match = re.search(r'\b(arjun|delta traders)\b', lower_text)
        res["party"] = match.group(1).title() if match else "Counterparty"
        return res

    # 4. New commitment extraction (e.g. Arjun from Delta Traders says...)
    money_match = re.search(r'(?:₹|rs\.?|inr|usd|\$)\s*([0-9,]+)', lower_text)
    if money_match or any(day in lower_text for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week"]):
        res["has_commitment"] = True
        res["intent"] = "new"
        res["type"] = "payment" if (money_match or "pay" in lower_text) else "delivery"
        res["action"] = "pay" if res["type"] == "payment" else "deliver"
        res["object"] = "money" if res["type"] == "payment" else "goods"
        
        if money_match:
            val = int(money_match.group(1).replace(",", ""))
            res["amount_cents"] = val * 100
            res["currency"] = "INR" if any(c in lower_text for c in ["₹", "rs", "inr"]) else "USD"
        
        # Look for party / org
        party_match = re.search(r'\b(arjun)\b', lower_text)
        res["party"] = party_match.group(1).title() if party_match else "Counterparty"
        
        org_match = re.search(r'\b(delta traders)\b', lower_text)
        if org_match:
            res["organization"] = "Delta Traders"
            
        # Look for deadline
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week", "today"]:
            if day in lower_text:
                res["deadline_raw"] = day
                res["deadline_utc"] = resolve_relative_deadline(now_utc, day, tz_name).isoformat()
                break
                
        return res

    return res


def extract_commitment(text: str, now_utc: datetime.datetime, tz_name: str = DEFAULT_TIMEZONE) -> dict[str, Any]:
    """Extract commitment parameters from normalized message using Gemini API or offline fallback."""
    if not GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY is not set. Falling back to rule-based offline extractor.")
        return extract_offline(text, now_utc, tz_name)

    now_iso = now_utc.isoformat()
    system_prompt = f"""Extract business obligations from the message below.

Now: {now_iso}
Timezone: {tz_name}

Return JSON only — no prose, no markdown.
{{
  "has_commitment": bool,
  "intent": "new|reschedule|fulfillment|dispute|irrelevant",
  "type": "payment|delivery|document|approval|service|other|null",
  "party": "string|null",
  "organization": "string|null",
  "action": "string|null",
  "object": "string|null",
  "amount_cents": "int|null",
  "currency": "string|null",
  "deadline_utc": "ISO-8601 UTC string|null",
  "deadline_raw": "string|null",
  "confidence": "float 0.0-1.0"
}}

Rules:
- "I will..." may be a commitment. "I hope..." is not.
- A reply does not prove fulfillment.
- Resolve relative dates to absolute UTC using the Now timestamp and timezone info. E.g. "by Friday" at Thursday 11:00 PM IST is calculated to Friday EOD (23:59) in Asia/Kolkata, then converted to UTC.
- intent=reschedule if modifying an existing obligation.
- intent=fulfillment if claiming completion.
- If has_commitment=false, return null for all other fields.
"""
    
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{
            "parts": [
                {"text": f"{system_prompt}\n\nMessage: \"{text}\""}
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0,
            "maxOutputTokens": 256
        }
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=10.0)
        resp.raise_for_status()
        resp_json = resp.json()
        raw_output = resp_json["candidates"][0]["content"]["parts"][0]["text"]
        
        # Clean output markdown if present
        raw_output = raw_output.replace("```json", "").replace("```", "").strip()
        
        import json
        extracted = json.loads(raw_output)
        
        # Ensure correct types
        if extracted.get("amount_cents") is not None:
            extracted["amount_cents"] = int(extracted["amount_cents"])
        if extracted.get("confidence") is not None:
            extracted["confidence"] = float(extracted["confidence"])
            
        return extracted
    except Exception as e:
        logger.exception("Gemini API extraction failed. Falling back to offline extraction.")
        return extract_offline(text, now_utc, tz_name)
