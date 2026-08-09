"""BizPulse Commitment Resolution Module.

Matches extracted commitments against existing unresolved commitments in the same conversation.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Tuple

from bizpulse.commitments.models import Commitment

logger = logging.getLogger(__name__)


def calculate_match_score(candidate: dict[str, Any], existing: Commitment) -> int:
    """Score a candidate matching against an existing commitment.

    Heuristics:
    - Party Match: +4 points
    - Organization Match: +3 points
    - Type Match: +3 points
    - Amount Match: +2 points
    """
    score = 0
    
    # 1. Party match (+4)
    c_party = (candidate.get("party") or "").strip().lower()
    e_party = (existing.party or "").strip().lower()
    if c_party and e_party:
        if c_party in e_party or e_party in c_party:
            score += 4
            
    # 2. Organization match (+3)
    c_org = (candidate.get("organization") or "").strip().lower()
    e_org = (existing.organization or "").strip().lower()
    if c_org and e_org:
        if c_org in e_org or e_org in c_org:
            score += 3
            
    # 3. Type match (+3)
    c_type = candidate.get("type")
    if c_type and c_type == existing.type:
        score += 3
        
    # 4. Amount match (+2)
    c_amount = candidate.get("amount_cents")
    if c_amount is not None and c_amount == existing.amount_cents:
        score += 2
        
    return score


def resolve_commitment(
    candidate: dict[str, Any],
    unresolved_commitments: list[Commitment],
    conversation_id: str,
    message_id: str,
    channel: str,
    source_text: str,
    tz_name: str
) -> Tuple[Commitment | None, str]:
    """Resolve an extracted candidate against database commitments.

    Returns:
        (Commitment | None, action)
        where action in ('create', 'update', 'none')
    """
    intent = candidate.get("intent", "irrelevant")
    if intent == "irrelevant":
        return None, "none"

    # Heuristic shortcut: if same conversation, same party, and exactly 1 unresolved commitment,
    # resolve directly to it if concrete safety signal is met.
    if intent in ("reschedule", "fulfillment", "dispute") and len(unresolved_commitments) == 1:
        existing = unresolved_commitments[0]
        gate_score = candidate.get("gate_score", 0)
        c_party = (candidate.get("party") or "").strip().lower()
        e_party = (existing.party or "").strip().lower()
        
        party_matched = c_party and e_party and (c_party in e_party or e_party in c_party) and (c_party != "counterparty")
        
        c_amount = candidate.get("amount_cents")
        amount_matched = False
        if c_amount is not None and existing.amount_cents is not None and existing.amount_cents > 0:
            diff = abs(c_amount - existing.amount_cents)
            if diff <= (0.10 * existing.amount_cents):
                amount_matched = True
                
        if gate_score >= 4 or party_matched or amount_matched:
            logger.info("Deterministic local shortcut: matching to single unresolved commitment %s", existing.id)
            return existing, "update"

    # Standard entity matching scoring
    best_match: Commitment | None = None
    best_score = 0

    for existing in unresolved_commitments:
        score = calculate_match_score(candidate, existing)
        if score > best_score:
            best_score = score
            best_match = existing

    logger.info("Best match commitment %s scored %d", getattr(best_match, 'id', None), best_score)

    if intent == "new":
        # Check duplicate
        for existing in unresolved_commitments:
            # If same party, amount, and deadline already exists, treat as duplicate (ignore)
            c_amount = candidate.get("amount_cents")
            if (existing.party.lower() == (candidate.get("party") or "").lower() and
                    existing.amount_cents == c_amount and
                    existing.deadline_raw == candidate.get("deadline_raw")):
                logger.info("Duplicate commitment detected. Ignoring.")
                return None, "none"

        # Create new commitment
        deadline_utc_str = candidate.get("deadline_utc")
        if deadline_utc_str:
            if deadline_utc_str.endswith('Z'):
                deadline_utc_str = deadline_utc_str[:-1] + '+00:00'
            deadline_utc = datetime.fromisoformat(deadline_utc_str)
        else:
            # Fallback to 3 days from now
            deadline_utc = datetime.now(timezone.utc)
            
        new_commitment = Commitment(
            id=f"commitment_{uuid.uuid4().hex[:6]}",
            conversation_id=conversation_id,
            party=candidate.get("party") or "Counterparty",
            organization=candidate.get("organization"),
            type=candidate.get("type") or "other",
            action=candidate.get("action") or "promise",
            object=candidate.get("object"),
            amount_cents=candidate.get("amount_cents"),
            residual_cents=candidate.get("amount_cents"),
            currency=candidate.get("currency") or "INR",
            deadline_utc=deadline_utc,
            deadline_raw=candidate.get("deadline_raw"),
            timezone=tz_name,
            status="pending",
            source_message_id=message_id,
            source_channel=channel,
            source_text=source_text,
            notes=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            confidence=candidate.get("confidence", 1.0),
            extraction_method=candidate.get("extraction_method", "offline")
        )
        return new_commitment, "create"

    elif intent in ("reschedule", "fulfillment", "dispute"):
        if best_match and best_score >= 5:
            return best_match, "update"
        else:
            # Genuinely ambiguous: call LLM ambiguity resolution if multiple commitments exist
            if len(unresolved_commitments) > 1:
                logger.info("Resolver ambiguity detected. Invoking Gemini resolution.")
                from bizpulse.commitments.extractor import resolve_ambiguity_via_gemini
                matched_id = resolve_ambiguity_via_gemini(source_text, unresolved_commitments)
                if matched_id:
                    for c in unresolved_commitments:
                        if c.id == matched_id:
                            logger.info("Ambiguity successfully resolved to: %s", c.id)
                            return c, "update"
            
            logger.warning(
                "Unresolved match for intent '%s' (best score %d < 5). Logging warning.",
                intent, best_score
            )
            return None, "none"

    return None, "none"
