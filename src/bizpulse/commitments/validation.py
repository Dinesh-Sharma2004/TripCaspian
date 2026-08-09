"""BizPulse Response Validation Layer.

Provides deterministic validation for commitment fields (amount, deadline, party, object)
and semantic fallback validation via Gemini API.
"""

import re
import os
import logging
import datetime
from typing import Any

import httpx

from bizpulse.config import DEFAULT_TIMEZONE
from bizpulse.commitments.extractor import extract_amount_from_text, resolve_relative_deadline, GEMINI_API_KEY

logger = logging.getLogger(__name__)


def validate_field_locally(field: str, text: str, commitment_type: str) -> dict[str, Any]:
    """Deterministically validates a single field value from text.
    
    Returns:
        dict: {"valid": bool, "value": normalized_value, "reason": str}
    """
    lower_text = text.lower().strip()
    
    # 1. AMOUNT validation
    if field == "amount":
        amt_cents, curr = extract_amount_from_text(lower_text)
        if amt_cents is not None:
            return {
                "valid": True,
                "value": {
                    "amount_cents": amt_cents,
                    "currency": curr or "INR"
                },
                "reason": None
            }
        
        # Check standard digits/numbers
        num_match = re.search(r'\b\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b|\b\d{3,}(?:\.\d{2})?\b', lower_text)
        if num_match:
            val = int(num_match.group(0).replace(",", "").split(".")[0])
            return {
                "valid": True,
                "value": {
                    "amount_cents": val * 100,
                    "currency": "INR"
                },
                "reason": None
            }
            
        # Check if they sent a date/time instead (to provide detailed invalid feedback)
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week", "today"]:
            if day in lower_text:
                return {
                    "valid": False,
                    "value": None,
                    "reason": f"{text} looks like a date. I still need the payment amount, for example ₹42,000."
                }
        return {
            "valid": False,
            "value": None,
            "reason": "That doesn't provide the amount I need. Please provide amount."
        }

    # 2. DEADLINE validation
    if field == "deadline":
        # Check if they sent an amount instead
        amt_cents, _ = extract_amount_from_text(lower_text)
        if amt_cents is not None:
            return {
                "valid": False,
                "value": None,
                "reason": f"{text} looks like an amount. I still need the expected date, for example Friday or tomorrow."
            }
            
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week", "today"]:
            if day in lower_text:
                try:
                    d_utc = resolve_relative_deadline(now_utc, day, DEFAULT_TIMEZONE)
                    return {
                        "valid": True,
                        "value": {
                            "deadline_utc": d_utc.isoformat(),
                            "deadline_raw": day
                        },
                        "reason": None
                    }
                except Exception as e:
                    logger.warning("Relative date resolution failed locally: %s", e)
        
        # Check for standard date pattern (e.g. YYYY-MM-DD)
        date_match = re.search(r'\b\d{4}-\d{2}-\d{2}\b', lower_text)
        if date_match:
            try:
                d_utc = datetime.datetime.strptime(date_match.group(0), "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
                return {
                    "valid": True,
                    "value": {
                        "deadline_utc": d_utc.isoformat(),
                        "deadline_raw": date_match.group(0)
                    },
                    "reason": None
                }
            except Exception:
                pass
                
        return {
            "valid": False,
            "value": None,
            "reason": "That doesn't provide the deadline I need. Please provide deadline."
        }

    # 3. PARTY validation
    if field == "party":
        # Block dates and money from becoming party names
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week", "today"]:
            if day in lower_text:
                return {
                    "valid": False,
                    "value": None,
                    "reason": f"{text} looks like a date. Who is making this promise?"
                }
        amt_cents, _ = extract_amount_from_text(lower_text)
        if amt_cents is not None:
            return {
                "valid": False,
                "value": None,
                "reason": f"{text} looks like an amount. Who is making this promise?"
            }
            
        # Filter noise words
        if lower_text in ("hi", "hello", "thanks", "ok", "yes", "no"):
            return {
                "valid": False,
                "value": None,
                "reason": "That doesn't name a person or company. Who is making this promise?"
            }
            
        # Treat Capitalized / word response as valid
        clean_party = text.strip()
        if len(clean_party) > 1:
            return {
                "valid": True,
                "value": clean_party,
                "reason": None
            }
        return {
            "valid": False,
            "value": None,
            "reason": "Please provide a valid person or organization name."
        }

    # 4. OBJECT validation
    if field == "object":
        # Block dates and money from becoming object names
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week", "today"]:
            if day in lower_text:
                return {
                    "valid": False,
                    "value": None,
                    "reason": f"{text} looks like a date. What exactly should be delivered/sent/completed?"
                }
        amt_cents, _ = extract_amount_from_text(lower_text)
        if amt_cents is not None:
            return {
                "valid": False,
                "value": None,
                "reason": f"{text} looks like an amount. What exactly should be delivered/sent/completed?"
            }
            
        if lower_text in ("hi", "hello", "thanks", "ok", "yes", "no"):
            return {
                "valid": False,
                "value": None,
                "reason": "Please specify the item, document, or action required."
            }
            
        clean_obj = text.strip()
        if len(clean_obj) > 1:
            return {
                "valid": True,
                "value": clean_obj,
                "reason": None
            }
        return {
            "valid": False,
            "value": None,
            "reason": "Please provide a valid object/document name."
        }

    return {"valid": False, "value": None, "reason": f"Unknown validation field: {field}"}


def validate_field_via_llm(field: str, expected_type: str, text: str) -> dict[str, Any]:
    """Uses Gemini API to validate and normalize a field value from text."""
    import bizpulse.metrics as metrics
    
    if not GEMINI_API_KEY:
        # Fallback to local rule evaluation if key not set
        res = validate_field_locally(field, text, expected_type)
        return {
            "valid": res["valid"],
            "field": field,
            "value": res["value"],
            "reason": res["reason"]
        }

    system_prompt = f"""You are a validator for business commitment parameters.
Analyze the user's input to extract and normalize the requested field.

Requested field: "{field}" (Expects: {expected_type})
User input: "{text}"

Return JSON only — no markdown, no prose.
If the input provides a valid value for this field:
{{
  "valid": true,
  "field": "{field}",
  "value": "normalized_value_string_or_number",
  "reason": null
}}

If the input is invalid or unrelated:
{{
  "valid": false,
  "field": "{field}",
  "value": null,
  "reason": "brief explanation of why it is invalid or unrelated"
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
    metrics.increment("api_attempts")
    metrics.increment("llm_calls")
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=10.0)
        resp.raise_for_status()
        resp_json = resp.json()
        raw_output = resp_json["candidates"][0]["content"]["parts"][0]["text"]
        raw_output = raw_output.replace("```json", "").replace("```", "").strip()
        
        import json
        return json.loads(raw_output)
    except Exception as e:
        metrics.increment("api_errors")
        logger.warning("Gemini field validation call failed: %s", e)
        # fallback
        res = validate_field_locally(field, text, expected_type)
        return {
            "valid": res["valid"],
            "field": field,
            "value": res["value"],
            "reason": res["reason"]
        }
