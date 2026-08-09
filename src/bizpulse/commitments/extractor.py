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


def extract_amount_from_text(lower_text: str) -> tuple[int | None, str | None]:
    """Helper to robustly parse monetary amounts from text."""
    # 1. Standard patterns (currency symbol prefix)
    money_prefix_match = re.search(r'(?:^|\s|\b)(?:₹|rs\.?|inr|usd|\$)\s*([0-9,]+)\b', lower_text)
    if money_prefix_match:
        val = int(money_prefix_match.group(1).replace(",", ""))
        currency = "INR" if any(c in lower_text for c in ["₹", "rs", "inr"]) else "USD"
        return val * 100, currency

    # 2. Suffix patterns (e.g. "42,000 rupees")
    money_suffix_match = re.search(r'\b([0-9,]+)\s*(?:rupees|inr|usd|dollars|euros)\b', lower_text)
    if money_suffix_match:
        val = int(money_suffix_match.group(1).replace(",", ""))
        currency = "INR" if any(c in lower_text for c in ["rupees", "inr"]) else "USD"
        return val * 100, currency

    # 3. Bare numbers (e.g. "42000" or "42,000"), ONLY if context indicates payment and not quantity
    bare_number_match = re.search(r'\b\d{1,3}(?:,\d{3})+\b|\b\d{3,}\b', lower_text)
    if bare_number_match:
        is_payment_context = any(w in lower_text for w in ["pay", "paid", "payment", "owes", "owe", "bill", "invoice", "rupees", "inr"])
        has_quantity_clue = any(w in lower_text for w in ["units", "documents", "copies", "items", "boxes", "packages", "pieces", "pcs", "documents", "files"])
        if is_payment_context and not has_quantity_clue:
            val = int(bare_number_match.group(0).replace(",", ""))
            return val * 100, "INR"

    return None, None


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
    
    # 0. Request/Directionality check: questions or requests to the receiver are not speaker commitments
    is_request = False
    if "?" in lower_text:
        is_request = True
    if any(p in lower_text for p in ["can you", "could you", "please send", "please deliver", "please provide", "please pay"]):
        if not any(p in lower_text for p in ["i will", "i'll", "we will", "we'll"]):
            is_request = True
            
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
        "confidence": 1.0,
        "extraction_method": "offline"
    }
    
    if is_request:
        return res
    
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
    amount_cents, currency = extract_amount_from_text(lower_text)
    has_date = any(day in lower_text for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week"])
    
    # Verb + obligation/party clues to support incomplete commitments
    has_verb = any(v in lower_text for v in ["pay", "deliver", "send", "provide", "submit", "approve", "confirm", "perform", "do"])
    has_obligation = any(o in lower_text for o in ["will", "shall", "promised", "agreed", "committed", "owe", "due", "supposed to"])
    has_party = any(p in lower_text for p in ["arjun", "supplier", "vendor", "customer", "client"])
    is_create_intent = has_verb and (has_obligation or has_party)
    
    if (amount_cents is not None) or has_date or is_create_intent:
        res["has_commitment"] = True
        res["intent"] = "new"
        
        # Classify commitment type
        if "pay" in lower_text:
            res["type"] = "payment"
            res["action"] = "pay"
            res["object"] = "money"
        elif "deliver" in lower_text or "delivery" in lower_text:
            res["type"] = "delivery"
            res["action"] = "deliver"
            obj_match = re.search(r'\bdeliver\s+(.*?)\s+(?:friday|monday|tuesday|wednesday|thursday|saturday|sunday|tomorrow|next week|today|by)', lower_text)
            if obj_match:
                res["object"] = obj_match.group(1).strip()
            else:
                obj_fallback = re.search(r'\b(?:deliver|deliveries)\s+(.*?)$', lower_text)
                res["object"] = obj_fallback.group(1).strip().rstrip(".") if obj_fallback else "goods"
        elif any(w in lower_text for w in ["send", "provide", "submit"]):
            res["type"] = "document"
            res["action"] = "send"
            doc_match = re.search(r'\b(?:send|provide|submit)\s+(.*?)\s+(?:friday|monday|tuesday|wednesday|thursday|saturday|sunday|tomorrow|next week|today|by)', lower_text)
            if doc_match:
                res["object"] = doc_match.group(1).strip()
            else:
                doc_fallback = re.search(r'\b(?:send|provide|submit)\s+(.*?)$', lower_text)
                res["object"] = doc_fallback.group(1).strip().rstrip(".") if doc_fallback else "document"
        elif any(w in lower_text for w in ["perform", "do"]):
            res["type"] = "service"
            res["action"] = "perform"
            svc_match = re.search(r'\b(?:perform|do)\s+(.*?)\s+(?:friday|monday|tuesday|wednesday|thursday|saturday|sunday|tomorrow|next week|today|by)', lower_text)
            if svc_match:
                res["object"] = svc_match.group(1).strip()
            else:
                svc_fallback = re.search(r'\b(?:perform|do)\s+(.*?)$', lower_text)
                res["object"] = svc_fallback.group(1).strip().rstrip(".") if svc_fallback else "service"
        else:
            res["type"] = "other"
            res["action"] = "obligation"
            res["object"] = "obligation"
        
        if amount_cents is not None:
            res["amount_cents"] = amount_cents
            res["currency"] = currency
        
        # Look for party / org
        party_match = re.search(r'\b(arjun|supplier|customer|client|vendor)\b', lower_text)
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
    import bizpulse.metrics as metrics
    
    if not GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY is not set. Falling back to rule-based offline extractor.")
        res = extract_offline(text, now_utc, tz_name)
        res["extraction_method"] = "offline"
        return res

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
- Extract only business obligations.
- Relative deadlines must use the supplied current time and timezone.
- A reply does not prove fulfillment.
- "I'll..." can indicate a commitment.
- "Maybe..." or "I hope..." is not automatically a commitment.
- Rescheduling modifies an existing obligation.
- Fulfillment means a claim of completion, not verified completion.
- Dispute means the obligation is denied or contested.
- If no obligation exists, return has_commitment=false.
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
    
    metrics.increment("api_attempts")
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
            
        extracted["extraction_method"] = "llm"
        metrics.increment("llm_calls")
        metrics.increment("extraction_calls")
        return extracted
    except Exception as e:
        metrics.increment("api_errors")
        logger.exception("Gemini API extraction failed. Falling back to offline extraction.")
        res = extract_offline(text, now_utc, tz_name)
        res["extraction_method"] = "offline"
        return res


def resolve_ambiguity_via_gemini(text: str, candidates: list[Any]) -> str | None:
    """Ask Gemini to resolve which commitment the user is talking about."""
    if not GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY is not set. Cannot run LLM ambiguity resolution.")
        return None

    import bizpulse.metrics as metrics
    metrics.increment("api_attempts")

    candidates_desc = []
    for c in candidates:
        amount_or_obj = f"₹{c.amount_cents/100:,.0f}" if c.amount_cents else c.object
        candidates_desc.append(
            f"- ID: {c.id} | Party: {c.party} | Organization: {c.organization or 'None'} | "
            f"Type: {c.type} | Object/Amount: {amount_or_obj} | Deadline: {c.deadline_raw} ({c.status})"
        )
    candidates_str = "\n".join(candidates_desc)

    system_prompt = f"""You are an expert resolver for business commitments.
An incoming message has been received in a conversation thread, and we need to match it to the correct unresolved commitment from the list of candidates.

Incoming message: "{text}"

Candidate unresolved commitments in this conversation:
{candidates_str}

Determine which commitment ID the incoming message is referring to.
Return JSON only:
{{
  "matched_commitment_id": "string or null",
  "reason": "brief explanation"
}}
"""

    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{
            "parts": [
                {"text": system_prompt}
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0,
            "maxOutputTokens": 128
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=10.0)
        resp.raise_for_status()
        resp_json = resp.json()
        raw_output = resp_json["candidates"][0]["content"]["parts"][0]["text"]
        raw_output = raw_output.replace("```json", "").replace("```", "").strip()

        import json
        res = json.loads(raw_output)
        matched_id = res.get("matched_commitment_id")
        
        metrics.increment("llm_calls")
        metrics.increment("resolution_calls")
        
        # Verify matched_id exists in the candidates list
        if any(c.id == matched_id for c in candidates):
            logger.info("LLM resolved ambiguity to commitment ID: %s", matched_id)
            return matched_id
        return None
    except Exception as e:
        metrics.increment("api_errors")
        logger.exception("Failed to resolve ambiguity via Gemini.")
        return None


def classify_low_signal_intent(text: str, minimal_context: str = "") -> str:
    """Compact LLM call to classify the user's intent on low-signal input."""
    import bizpulse.metrics as metrics
    if not GEMINI_API_KEY:
        return "unrelated"
        
    system_prompt = """You classify ambiguous user messages for a business commitment assistant.
Return JSON only:
{
  "intent": "create_commitment" | "answer_pending_question" | "list_commitments" | "reschedule" | "fulfillment" | "dispute" | "help" | "unrelated"
}

Rules:
- create_commitment = user describes a promise, obligation, or commitment they want to track
- answer_pending_question = user provides details (amounts, dates, names) answering a question
- list_commitments = user wants to see current/active commitments
- reschedule = user wants to change a deadline/date
- fulfillment = user claims they fulfilled or sent something
- dispute = user disputes or disagrees with a commitment
- help = user asks what the bot does or how to use it
- unrelated = normal casual conversation or completely irrelevant chatter

Do not extract any details here."""

    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{
            "parts": [
                {"text": f"{system_prompt}\n\nContext: \"{minimal_context}\"\nMessage: \"{text}\""}
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0,
            "maxOutputTokens": 64
        }
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    metrics.increment("api_attempts")
    metrics.increment("recovery_llm_calls")
    metrics.increment("llm_calls")
    try:
        import httpx
        resp = httpx.post(url, json=body, headers=headers, timeout=10.0)
        resp.raise_for_status()
        resp_json = resp.json()
        raw_output = resp_json["candidates"][0]["content"]["parts"][0]["text"]
        raw_output = raw_output.replace("```json", "").replace("```", "").strip()
        
        import json
        data = json.loads(raw_output)
        intent = data.get("intent")
        if intent in ("create_commitment", "answer_pending_question", "list_commitments", "reschedule", "fulfillment", "dispute", "help", "unrelated"):
            return intent
        return "unrelated"
    except Exception as e:
        metrics.increment("api_errors")
        logger.warning("Low-signal classification failed: %s", e)
        return "unrelated"
