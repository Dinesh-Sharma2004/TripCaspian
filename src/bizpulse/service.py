"""BizPulse Service layer orchestrator.

Implements the multi-stage message processing pipeline and handles button interactions.
"""

import logging
import datetime
from typing import Any, Tuple

from bizpulse.config import DEFAULT_TIMEZONE
from bizpulse.storage import SQLiteStorage
from bizpulse.scheduler import CommitmentScheduler
from bizpulse.watcher import CommitmentWatcher
from bizpulse.commitments.models import Commitment
from bizpulse.commitments.normalizer import normalize_message
from bizpulse.commitments.gate import evaluate_gate
from bizpulse.commitments.extractor import extract_commitment, validate_extraction, classify_low_signal_intent
from bizpulse.commitments.resolver import resolve_commitment
from bizpulse.commitments.lifecycle import apply_reschedule, apply_fulfillment, apply_dispute
from bizpulse.commitments.templates import get_rescheduled_blocks, format_amount

logger = logging.getLogger(__name__)


def find_missing_required_field(data: dict[str, Any]) -> str | None:
    c_type = data.get("type") or "other"
    if not data.get("party"):
        return "party"
    if c_type == "payment":
        if data.get("amount_cents") is None:
            return "amount"
    elif c_type in ("delivery", "document"):
        if not data.get("object"):
            return "object"
    else: # approval, service, other
        if not data.get("action") and not data.get("object"):
            return "object"
            
    if not data.get("deadline_utc"):
        return "deadline"
    return None


def get_clarification_question(field: str, draft: dict[str, Any]) -> str:
    party = draft.get("party") or "they"
    c_type = draft.get("type") or "obligation"
    if field == "amount":
        if party.lower() == "arjun":
            return f"Got it 👍\nI found a payment commitment from Arjun.\n\nHow much is he supposed to pay?"
        return f"Got it 👍\nI found a payment commitment from {party}.\n\nHow much is {party} supposed to pay?"
    elif field == "deadline":
        return f"Got it 👍\nI found a {c_type} commitment from {party}.\n\nWhen is this due?"
    elif field == "party":
        return "Got it 👍\nI found a commitment.\n\nWho is making this promise?"
    elif field == "object":
        if c_type == "document":
            return f"Got it 👍\nI found a document commitment from {party}.\n\nWhich document needs to be sent?"
        else:
            return f"Got it 👍\nI found a delivery commitment from {party}.\n\nWhat item is {party} supposed to deliver?"
    elif field == "action" or field == "object":
        return f"Got it 👍\nI found a commitment from {party}.\n\nWhat item or action is {party} supposed to perform?"
    return "Could you please clarify the missing details for this commitment?"


class BizPulseService:
    """Core orchestrator for BizPulse pipeline and interactions."""

    def __init__(
        self,
        storage: SQLiteStorage | None = None,
        scheduler: CommitmentScheduler | None = None,
        watcher: CommitmentWatcher | None = None,
        caspian_client: Any = None,
    ):
        self.storage = storage or SQLiteStorage()
        self.scheduler = scheduler or CommitmentScheduler()
        self.watcher = watcher or CommitmentWatcher(storage=self.storage, caspian_client=caspian_client)
        self.client = caspian_client
        self._processed_messages: set[Tuple[str, str]] = set()  # (channel, message_id)

        if self.scheduler:
            from bizpulse.scheduler import register_callback
            register_callback(self.on_deadline_reached)

    def set_caspian_client(self, client: Any) -> None:
        """Bind Caspian SDK client instance."""
        self.client = client
        self.watcher.client = client

    def handle_user_message(
        self,
        conversation_id: str,
        sender: dict | None,
        text: str,
        message_id: str,
        channel: str,
        subject: str | None = None,
    ) -> str | None:
        """Process incoming messages through the BizPulse pipeline.

        Returns:
            Clarification request or confirmation text, or None if message is ignored.
        """
        import bizpulse.metrics as metrics

        # Stage 0: Command Handling
        if text.strip().startswith("/"):
            metrics.increment("messages_seen")
            parts = [p.strip() for p in text.split(" ") if p.strip()]
            if not parts:
                return None
            cmd = parts[0].lower()

            if cmd == "/create":
                # Splitting the original text by pipe
                subparts = [p.strip() for p in text.split("|")]
                if len(subparts) < 5:
                    return (
                        "⚠️ Missing required fields.\n"
                        "Format: `/create <type> | <party> | <organization> | <amount_or_object> | <deadline>`\n"
                        "Examples:\n"
                        "- `/create payment | Arjun | Delta Traders | 42000 | Friday`\n"
                        "- `/create delivery | Sharma Suppliers | replacement motor | Wednesday`"
                    )
                # First subpart is "/create <type>"
                type_part_raw = subparts[0]
                type_parts = [t.strip() for t in type_part_raw.split(" ") if t.strip()]
                if len(type_parts) < 2:
                    return "⚠️ Missing commitment type (e.g. `payment`, `delivery`)."
                c_type = type_parts[1].lower()

                party = subparts[1]
                if not party:
                    return "⚠️ Party name is required."

                org = subparts[2] or None
                amount_or_obj = subparts[3]
                deadline_expr = subparts[4]

                if c_type == "payment":
                    import re
                    clean_val = re.sub(r'[^\d.]', '', amount_or_obj)
                    if not clean_val:
                        return "⚠️ Valid payment amount is required."
                    try:
                        amount_cents = int(float(clean_val) * 100)
                    except ValueError:
                        return "⚠️ Valid payment amount is required."
                    obj = "money"
                    currency = "INR"
                    action = "pay"
                else:
                    amount_cents = None
                    obj = amount_or_obj
                    currency = None
                    action = "deliver"

                from bizpulse.commitments.extractor import resolve_relative_deadline
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                try:
                    deadline_utc = resolve_relative_deadline(now_utc, deadline_expr, DEFAULT_TIMEZONE)
                except Exception:
                    deadline_utc = now_utc + datetime.timedelta(days=3)

                import uuid
                commitment = Commitment(
                    id=f"commitment_{uuid.uuid4().hex[:6]}",
                    conversation_id=conversation_id,
                    party=party,
                    organization=org,
                    type=c_type,
                    action=action,
                    object=obj,
                    amount_cents=amount_cents,
                    residual_cents=amount_cents,
                    currency=currency,
                    deadline_utc=deadline_utc,
                    deadline_raw=deadline_expr,
                    timezone=DEFAULT_TIMEZONE,
                    status="pending",
                    source_message_id=message_id,
                    source_channel=channel,
                    source_text=text,
                    notes=None,
                    created_at=now_utc,
                    updated_at=now_utc,
                    extraction_method="offline"
                )

                self.storage.save_commitment(commitment)

                if self.scheduler:
                    job_id = self.scheduler.schedule_deadline_alert(
                        commitment_id=commitment.id,
                        run_date=commitment.deadline_utc,
                        callback=self.on_deadline_reached
                    )
                    commitment.active_job_id = job_id
                    self.storage.save_commitment(commitment)

                amount_str = format_amount(commitment.amount_cents)
                org_str = f" · {commitment.organization}" if commitment.organization else ""
                deadline_str = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")

                return (
                    f"Created commitment\n\n"
                    f"{commitment.type.capitalize()}\n"
                    f"{amount_str if c_type == 'payment' else commitment.object}\n"
                    f"{commitment.party}{org_str}\n"
                    f"Due {deadline_str}\n"
                    f"Status: {commitment.status.capitalize()}"
                )

            elif cmd == "/list":
                unresolved = self.storage.get_unresolved_commitments(conversation_id)
                if not unresolved:
                    return "📋 **No unresolved commitments in this conversation.**"

                lines = ["📋 **Unresolved Commitments:**"]
                for c in unresolved:
                    amount_or_obj = format_amount(c.amount_cents) if c.type == "payment" else c.object
                    org_str = f" · {c.organization}" if c.organization else ""
                    short_id = c.id.replace("commitment_", "")
                    deadline_str = c.deadline_raw or c.deadline_utc.strftime("%Y-%m-%d")
                    lines.append(
                        f"• **[{short_id}] {c.type.capitalize()}** ({amount_or_obj})\n"
                        f"  {c.party}{org_str}\n"
                        f"  Due: {deadline_str} ({c.status})"
                    )
                return "\n".join(lines)

            elif cmd == "/snooze":
                if len(parts) < 3:
                    return "⚠️ Missing arguments.\nFormat: `/snooze <commitment_id> <duration>` (e.g., `/snooze a1b2c3 2d`)"

                c_id = parts[1]
                if not c_id.startswith("commitment_"):
                    c_id = f"commitment_{c_id}"
                commitment = self.storage.get_commitment(c_id)
                if not commitment:
                    return f"⚠️ Commitment {parts[1]} not found."

                duration_str = " ".join(parts[2:])
                import re
                match = re.match(r'(\d+)\s*([a-zA-Z]*)', duration_str)
                if not match:
                    return "⚠️ Invalid duration format. Examples: `2d`, `3h`, `1 day`"
                val = int(match.group(1))
                unit = match.group(2).lower()
                if unit in ("h", "hour", "hours"):
                    delta = datetime.timedelta(hours=val)
                elif unit in ("m", "minute", "minutes"):
                    delta = datetime.timedelta(minutes=val)
                else:
                    delta = datetime.timedelta(days=val)

                new_followup = datetime.datetime.now(datetime.timezone.utc) + delta
                commitment.next_followup_at = new_followup
                self.storage.save_commitment(commitment)

                return f"⏳ **Commitment snoozed.** Next reminder set for {new_followup.strftime('%Y-%m-%d %H:%M:%S UTC')} (Deadline remains unchanged)."

            elif cmd == "/close":
                if len(parts) < 2:
                    return "⚠️ Missing argument.\nFormat: `/close <commitment_id>`"

                c_id = parts[1]
                if not c_id.startswith("commitment_"):
                    c_id = f"commitment_{c_id}"
                commitment = self.storage.get_commitment(c_id)
                if not commitment:
                    return f"⚠️ Commitment {parts[1]} not found."

                try:
                    commitment.transition_to("fulfillment_claimed")
                    commitment.notes = "Closed manually via command."

                    if commitment.active_job_id:
                        try:
                            self.scheduler.cancel_job(commitment.active_job_id)
                            commitment.active_job_id = None
                        except Exception:
                            pass

                    self.storage.save_commitment(commitment)
                    return f"✅ **Closed Commitment.** Commitment #{parts[1]} status set to `Fulfillment Claimed`."
                except Exception as e:
                    return f"⚠️ State transition failed: {e}"

            return None

        # Stage 1: Deduplicate
        dedup_key = (channel, message_id)
        if dedup_key in self._processed_messages:
            logger.info("Duplicate message %s ignored.", message_id)
            return None
        self._processed_messages.add(dedup_key)

        # Stage 2: Normalize
        normalized_text = normalize_message(text, subject)
        logger.info("Normalized message text: '%s'", normalized_text)

        # Stage 2.5: Check for Pending Draft Clarification Reply
        draft = self.storage.get_draft(conversation_id)
        if draft:
            metrics.increment("messages_seen")
            missing_field = draft.get("missing_field")
            logger.info("Handling response for pending draft. Missing field: %s", missing_field)
            
            # Deterministic parsing first
            parsed = False
            if missing_field == "amount":
                from bizpulse.commitments.extractor import extract_amount_from_text
                amt_cents, curr = extract_amount_from_text(normalized_text.lower())
                if amt_cents is not None:
                    draft["amount_cents"] = amt_cents
                    draft["currency"] = curr or "INR"
                    draft["missing_field"] = None
                    parsed = True
                else:
                    import re
                    num_match = re.search(r'\b\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b|\b\d{3,}(?:\.\d{2})?\b', normalized_text)
                    if num_match:
                        val = int(num_match.group(0).replace(",", "").split(".")[0])
                        draft["amount_cents"] = val * 100
                        draft["currency"] = "INR"
                        draft["missing_field"] = None
                        parsed = True
            elif missing_field == "deadline":
                from bizpulse.commitments.extractor import resolve_relative_deadline
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                day = None
                lower_norm = normalized_text.lower()
                for d in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week", "today"]:
                    if d in lower_norm:
                        day = d
                        break
                if day:
                    try:
                        d_utc = resolve_relative_deadline(now_utc, day, DEFAULT_TIMEZONE)
                        draft["deadline_utc"] = d_utc.isoformat()
                        draft["deadline_raw"] = day
                        draft["missing_field"] = None
                        parsed = True
                    except Exception:
                        pass
            elif missing_field == "party":
                draft["party"] = normalized_text
                draft["missing_field"] = None
                parsed = True
            elif missing_field in ("object", "action"):
                draft[missing_field] = normalized_text
                draft["missing_field"] = None
                parsed = True

            # Fall back to LLM ONLY if deterministic parsing failed
            if not parsed:
                logger.info("Deterministic parsing failed for draft response. Calling Gemini API.")
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                extracted = extract_commitment(normalized_text, now_utc, DEFAULT_TIMEZONE)
                
                if missing_field == "amount" and extracted.get("amount_cents") is not None:
                    draft["amount_cents"] = extracted["amount_cents"]
                    draft["currency"] = extracted.get("currency") or "INR"
                    draft["missing_field"] = None
                elif missing_field == "deadline" and extracted.get("deadline_utc"):
                    draft["deadline_utc"] = extracted["deadline_utc"]
                    draft["deadline_raw"] = extracted.get("deadline_raw")
                    draft["missing_field"] = None
                elif missing_field == "party" and extracted.get("party") and extracted.get("party").lower() != "counterparty":
                    draft["party"] = extracted["party"]
                    if extracted.get("organization"):
                        draft["organization"] = extracted["organization"]
                    draft["missing_field"] = None
                elif missing_field == "object" and extracted.get("object"):
                    draft["object"] = extracted["object"]
                    draft["missing_field"] = None
                elif missing_field == "action" and extracted.get("action"):
                    draft["action"] = extracted["action"]
                    draft["missing_field"] = None
            if parsed:
                metrics.increment("field_values_resolved_deterministically")

            if draft["missing_field"] is None:
                self.storage.reset_low_signal_count(conversation_id)

            # Recheck missing fields
            next_missing = find_missing_required_field(draft)
            if next_missing:
                draft["missing_field"] = next_missing
                self.storage.save_draft(draft)
                return get_clarification_question(next_missing, draft)
            else:
                self.storage.delete_draft(conversation_id)
                self.storage.mark_onboarding_sent(conversation_id)
                self.storage.reset_low_signal_count(conversation_id)
                
                import uuid
                c_id = f"commitment_{uuid.uuid4().hex[:6]}"
                
                d_utc_str = draft["deadline_utc"]
                if d_utc_str.endswith('Z'):
                    d_utc_str = d_utc_str[:-1] + '+00:00'
                deadline_utc = datetime.datetime.fromisoformat(d_utc_str)
                
                new_commitment = Commitment(
                    id=c_id,
                    conversation_id=conversation_id,
                    party=draft["party"],
                    organization=draft["organization"],
                    type=draft["type"],
                    action=draft["action"] or "promise",
                    object=draft["object"],
                    amount_cents=draft["amount_cents"],
                    residual_cents=draft["amount_cents"],
                    currency=draft["currency"] or "INR",
                    deadline_utc=deadline_utc,
                    deadline_raw=draft["deadline_raw"],
                    timezone=DEFAULT_TIMEZONE,
                    status="pending",
                    source_message_id=message_id,
                    source_channel=channel,
                    source_text=text,
                    notes=None,
                    created_at=datetime.datetime.now(datetime.timezone.utc),
                    updated_at=datetime.datetime.now(datetime.timezone.utc),
                    extraction_method=draft.get("extraction_method") or "offline"
                )
                
                self.storage.save_commitment(new_commitment)
                
                if self.scheduler:
                    job_id = self.scheduler.schedule_deadline_alert(
                        commitment_id=new_commitment.id,
                        run_date=new_commitment.deadline_utc,
                        callback=self.on_deadline_reached
                    )
                    new_commitment.active_job_id = job_id
                    self.storage.save_commitment(new_commitment)
                
                amount_str = format_amount(new_commitment.amount_cents) if new_commitment.type == "payment" else new_commitment.object
                deadline_str = new_commitment.deadline_raw or new_commitment.deadline_utc.strftime("%Y-%m-%d")
                
                if new_commitment.type == "payment":
                    resp = (
                        f"Got it 👍\n\n"
                        f"{new_commitment.party} is supposed to pay {amount_str} by {deadline_str}.\n\n"
                        f"I'll start tracking this for you."
                    )
                else:
                    action_verb = new_commitment.action or "deliver"
                    resp = (
                        f"Got it 👍\n\n"
                        f"{new_commitment.party} is supposed to {action_verb} {new_commitment.object} by {deadline_str}.\n\n"
                        f"I'll start tracking this for you."
                    )
                    
                unresolved_list = self.storage.get_unresolved_commitments(conversation_id)
                if len(unresolved_list) == 1:
                    resp += "\n\nYou can use /list anytime to see your active commitments."
                    
                return resp

        # Normal Pipeline
        metrics.increment("messages_seen")

        # Stage 3: Gate & Recovery
        gate_res = evaluate_gate(normalized_text)
        is_greeting = normalized_text.lower().strip() in ("hi", "hello", "hey", "start", "greetings")
        has_onboarded = self.storage.has_sent_onboarding(conversation_id)
        
        # If onboarding is not sent yet
        if not has_onboarded:
            if is_greeting:
                self.storage.mark_onboarding_sent(conversation_id)
                metrics.increment("onboarding_messages")
                return (
                    "👋 Hi! I'm BizPulse.\n\n"
                    "I help keep track of business promises so they don't get forgotten.\n\n"
                    "You can simply tell me something like:\n"
                    "'Arjun will pay ₹42,000 by Friday.'"
                )
            if not gate_res["passed"]:
                metrics.increment("messages_filtered")
                return None

        # If onboarding has been sent and gate fails
        if not gate_res["passed"]:
            metrics.increment("messages_filtered")
            metrics.increment("low_signal_messages")
            logger.info("Message did not pass signal gate. Processing low-signal recovery.")
            
            # Map context-sensitive topic from user's message
            topic = "generic"
            lower_text = normalized_text.lower()
            if any(w in lower_text for w in ["pay", "payment", "invoice", "bill", "rupees", "inr", "money", "cost", "price"]):
                topic = "payment"
            elif any(w in lower_text for w in ["deliver", "delivery", "ship", "shipping", "goods", "item", "items", "units", "product", "motor"]):
                topic = "delivery"
            elif any(w in lower_text for w in ["doc", "document", "pdf", "gst", "contract", "invoice", "receipt", "certificate", "file", "files"]):
                topic = "document"
            elif any(w in lower_text for w in ["followup", "follow up", "snooze", "close", "reminder", "remind"]):
                topic = "followup"
            
            low_signal_count = self.storage.increment_low_signal_count(conversation_id, topic=topic)
            
            if low_signal_count == 1:
                metrics.increment("clarification_responses")
                if topic == "payment":
                    return (
                        "I can help track payments 👍\n\n"
                        "For example:\n"
                        "'Arjun will pay ₹42,000 by Friday.'\n\n"
                        "Who is expected to pay, how much, and by when?"
                    )
                elif topic == "delivery":
                    return (
                        "I can track a delivery 👍\n\n"
                        "Tell me who is delivering it, what they're delivering, and by when."
                    )
                elif topic == "document":
                    return (
                        "I can track document commitments 👍\n\n"
                        "Tell me who needs to send the document and when it's expected."
                    )
                elif topic == "followup":
                    return (
                        "Sure. Tell me who you're waiting on, what they're supposed to do, and when you expected it."
                    )
                else:
                    return (
                        "I can help keep track of business promises like payments, deliveries, and documents.\n\n"
                        "Tell me what promise was made, who made it, and when it is expected."
                    )
            
            else: # low_signal_count >= 2
                logger.info("Executing Stage 2 low-signal recovery LLM call.")
                self.storage.reset_low_signal_count(conversation_id)
                
                # Fetch recent unresolved commitments as minimal context
                unresolved = self.storage.get_unresolved_commitments(conversation_id)
                context_lines = []
                for c in unresolved[:3]:
                    amt_str = f" ({format_amount(c.amount_cents)})" if c.amount_cents else ""
                    context_lines.append(f"- ID: {c.id}, Party: {c.party}, Type: {c.type}{amt_str}, Status: {c.status}")
                minimal_context = "\n".join(context_lines)
                
                intent = classify_low_signal_intent(normalized_text, minimal_context)
                logger.info("Recovery LLM classified intent: %s", intent)
                
                if intent == "commitment":
                    # Let it fall through to Stage 4 (Extraction)
                    pass
                elif intent == "field_value":
                    if self.storage.get_draft(conversation_id):
                        return self.handle_user_message(conversation_id, sender, text, message_id, channel, subject)
                    else:
                        return "Understood. Let me know if you have any business commitments or payments to track!"
                elif intent == "help":
                    return (
                        "I'm BizPulse, your conversational assistant to track payments, deliveries, and document commitments.\n\n"
                        "You can tell me what was promised (e.g., 'Arjun will pay ₹42,000 by Friday'), and I'll keep track of deadlines and send follow-ups if they're overdue."
                    )
                elif intent == "update" and unresolved:
                    # Let it fall through to Stage 4 (Extraction)
                    pass
                else:
                    # casual / irrelevant
                    return "Understood. Let me know if you have any business commitments or payments to track!"

        # Stage 4: Extraction
        self.storage.reset_low_signal_count(conversation_id)
        logger.info("Proceeding to extraction.")
        
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        extracted = extract_commitment(normalized_text, now_utc, DEFAULT_TIMEZONE)
        extracted["gate_score"] = gate_res["score"]
        logger.info("Extracted payload: %s", extracted)

        if not extracted.get("has_commitment"):
            return None

        # Stage 5: Validate & Draft Check
        if extracted.get("intent") == "new":
            missing_field = find_missing_required_field(extracted)
            if missing_field:
                draft = {
                    "conversation_id": conversation_id,
                    "party": extracted.get("party"),
                    "organization": extracted.get("organization"),
                    "type": extracted.get("type"),
                    "action": extracted.get("action"),
                    "object": extracted.get("object"),
                    "amount_cents": extracted.get("amount_cents"),
                    "currency": extracted.get("currency"),
                    "deadline_utc": extracted.get("deadline_utc"),
                    "deadline_raw": extracted.get("deadline_raw"),
                    "intent": extracted.get("intent"),
                    "source_message_id": message_id,
                    "source_channel": channel,
                    "source_text": text,
                    "missing_field": missing_field,
                    "extraction_method": extracted.get("extraction_method") or "offline"
                }
                self.storage.save_draft(draft)
                self.storage.mark_onboarding_sent(conversation_id)
                return get_clarification_question(missing_field, draft)
        else:
            validation_status = validate_extraction(extracted)
            if validation_status == "ignore":
                logger.info("Extraction validation result: ignore.")
                return None
            elif validation_status == "needs_review":
                logger.warning("Extraction validation failed or confidence low. Requesting clarification.")
                return (
                    "⚠️ I heard you mention a commitment, but I couldn't capture all the details. "
                    "Could you please clarify who is promising what, and by when? (E.g. 'Arjun will pay ₹42,000 by Friday')"
                )

        # Stage 6: Resolve
        unresolved = self.storage.get_unresolved_commitments(conversation_id)
        commitment, action = resolve_commitment(
            candidate=extracted,
            unresolved_commitments=unresolved,
            conversation_id=conversation_id,
            message_id=message_id,
            channel=channel,
            source_text=text,
            tz_name=DEFAULT_TIMEZONE
        )

        # Stage 7 & 8: Lifecycle update and Schedule/Watcher
        if action == "create" and commitment:
            self.storage.save_commitment(commitment)
            self.storage.mark_onboarding_sent(conversation_id)
            
            if self.scheduler:
                job_id = self.scheduler.schedule_deadline_alert(
                    commitment_id=commitment.id,
                    run_date=commitment.deadline_utc,
                    callback=self.on_deadline_reached
                )
                commitment.active_job_id = job_id
                self.storage.save_commitment(commitment)
                
            amount_str = format_amount(commitment.amount_cents) if commitment.type == "payment" else commitment.object
            deadline_str = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
            
            logger.info("Created new commitment %s", commitment.id)
            
            if commitment.type == "payment":
                resp = (
                    f"Got it 👍\n\n"
                    f"{commitment.party} is supposed to pay {amount_str} by {deadline_str}.\n\n"
                    f"I'll start tracking this for you."
                )
            else:
                action_verb = commitment.action or "deliver"
                resp = (
                    f"Got it 👍\n\n"
                    f"{commitment.party} is supposed to {action_verb} {commitment.object} by {deadline_str}.\n\n"
                    f"I'll start tracking this for you."
                )
                
            unresolved_list = self.storage.get_unresolved_commitments(conversation_id)
            if len(unresolved_list) == 1:
                resp += "\n\nYou can use /list anytime to see your active commitments."
                
            return resp

        elif action == "update" and commitment:
            intent = extracted.get("intent")
            
            if intent == "reschedule":
                old_raw = commitment.deadline_raw
                deadline_utc_str = extracted.get("deadline_utc")
                if deadline_utc_str:
                    if deadline_utc_str.endswith('Z'):
                        deadline_utc_str = deadline_utc_str[:-1] + '+00:00'
                    new_deadline_utc = datetime.datetime.fromisoformat(deadline_utc_str)
                else:
                    new_deadline_utc = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)
                    
                apply_reschedule(
                    commitment=commitment,
                    new_deadline_utc=new_deadline_utc,
                    new_deadline_raw=extracted.get("deadline_raw"),
                    scheduler=self.scheduler,
                    callback_fn=self.on_deadline_reached
                )
                self.storage.save_commitment(commitment)
                
                new_raw = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
                logger.info("Rescheduled commitment %s to %s", commitment.id, new_deadline_utc)
                return (
                    f"🔄 **Commitment Rescheduled**\n"
                    f"• **Party**: {commitment.party}\n"
                    f"• **Change**: {old_raw or 'Previous'} ➡️ {new_raw}\n"
                    f"• **Status**: {commitment.status.capitalize()}"
                )
                
            elif intent == "fulfillment":
                apply_fulfillment(commitment, text)
                self.storage.save_commitment(commitment)
                
                logger.info("Fulfillment claimed for commitment %s", commitment.id)
                if commitment.residual_cents and commitment.residual_cents > 0:
                    paid_str = format_amount(commitment.amount_cents - commitment.residual_cents)
                    residual_str = format_amount(commitment.residual_cents)
                    return (
                        f"Claimed partial payment of {paid_str}.\n"
                        f"Remaining residual amount outstanding is **{residual_str}**.\n"
                        f"Status: {commitment.status.capitalize()}"
                    )
                else:
                    return (
                        f"👍 **Fulfillment Claimed**\n"
                        f"{commitment.party} claims to have fulfilled their commitment.\n"
                        f"Status: {commitment.status.capitalize()} (awaiting verification)"
                    )
                    
            elif intent == "dispute":
                apply_dispute(commitment, text)
                self.storage.save_commitment(commitment)
                
                logger.info("Disputed commitment %s", commitment.id)
                return (
                    f"⚠️ **Commitment Disputed**\n"
                    f"Counterparty {commitment.party} has disputed this obligation.\n"
                    f"Status: {commitment.status.capitalize()}"
                )

        return None

    def handle_interaction(self, value: str) -> Tuple[str, list[dict[str, Any]] | None]:
        """Handle button click interaction callback.

        Returns:
            (reply_text, blocks)
        """
        if ":" not in value:
            return "Invalid interaction payload.", None
            
        action, commitment_id = value.split(":", 1)
        commitment = self.storage.get_commitment(commitment_id)
        if not commitment:
            return "Error: Commitment not found.", None

        if action == "remind":
            amount_str = format_amount(commitment.amount_cents)
            deadline_str = commitment.deadline_raw or commitment.deadline_utc.strftime("%Y-%m-%d")
            
            # Follow-up outreach message
            outreach = (
                f"Hi {commitment.party}, following up on the {amount_str} payment "
                f"expected by {deadline_str}. Could you share the status?"
            )
            return outreach, None

        elif action == "snooze":
            # Extend deadline by 24h
            old_deadline = commitment.deadline_utc
            new_deadline = old_deadline + datetime.timedelta(hours=24)
            
            # Transition back to pending
            commitment.status = "pending"
            commitment.deadline_utc = new_deadline
            commitment.deadline_raw = f"{commitment.deadline_raw or ''} (snoozed)".strip()
            
            # Reschedule alert job
            if commitment.active_job_id:
                try:
                    self.scheduler.cancel_job(commitment.active_job_id)
                except Exception:
                    pass
            if self.scheduler:
                job_id = self.scheduler.schedule_deadline_alert(
                    commitment_id=commitment.id,
                    run_date=new_deadline,
                    callback=self.on_deadline_reached
                )
                commitment.active_job_id = job_id
                
            self.storage.save_commitment(commitment)
            logger.info("Snoozed commitment %s by 24 hours to %s", commitment.id, new_deadline)
            
            return f"⏳ **Commitment snoozed for 24 hours.** New deadline: {new_deadline.strftime('%Y-%m-%d %H:%M:%S UTC')}", None

        elif action == "escalate":
            commitment.status = "escalated"
            self.storage.save_commitment(commitment)
            logger.info("Escalated commitment %s", commitment.id)
            
            return f"⚠️ **Obligation escalated.** Counterparty: {commitment.party} ({commitment.organization or 'No Org'})", None

        elif action == "mark_paid":
            commitment.status = "verified_fulfilled"
            self.storage.save_commitment(commitment)
            logger.info("Commitment %s verified as fulfilled.", commitment.id)
            
            return f"✅ **Verified Fulfilled.** Commitment #{commitment.id} is closed.", None

        return "Unknown action.", None

    def on_deadline_reached(self, commitment_id: str) -> None:
        """Callback invoked by scheduler when a commitment deadline is reached."""
        commitment = self.storage.get_commitment(commitment_id)
        if not commitment:
            logger.warning("Scheduled alert fired for missing commitment %s", commitment_id)
            return

        # Trigger overdue logic if still pending or rescheduled
        if commitment.status in ("pending", "rescheduled"):
            if self.watcher:
                self.watcher.trigger_overdue(commitment)
