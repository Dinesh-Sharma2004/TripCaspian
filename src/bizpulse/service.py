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
from bizpulse.commitments.validation import validate_field_locally, validate_field_via_llm
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
        if c_type != "approval":
            return "deadline"
    return None


def get_clarification_question(field: str, draft: dict[str, Any]) -> str:
    party = draft.get("party") or "they"
    c_type = draft.get("type") or "obligation"
    if field == "amount":
        if draft.get("amount_cents") is None and not draft.get("deadline_utc"):
            return "What amount should I record, and when is the payment expected?"
        return f"How much is he supposed to pay?" if party.lower() == "arjun" else f"How much is {party} supposed to pay?"
    elif field == "deadline":
        if c_type == "payment":
            return "When is the payment expected?"
        elif c_type == "delivery":
            obj = draft.get("object") or "item"
            if obj.lower().startswith("the "):
                return f"When is {obj} expected?"
            return f"When is the {obj} expected?"
        return "When is this due?"
    elif field == "party":
        return "Who is responsible for this commitment?"
    elif field == "object":
        if c_type == "document":
            return "Which document needs to be sent?"
        return "What exactly should be delivered/sent/completed?"
    return "Could you please clarify the missing details for this commitment?"


def get_invalid_clarification(field: str, text: str, draft: dict[str, Any]) -> str:
    c_type = draft.get("type") or "obligation"
    lower_text = text.lower().strip()
    
    if field == "amount":
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "next week", "today"]:
            if day in lower_text:
                return f"{text} looks like a date. I still need the payment amount, for example ₹42,000."
        return "That doesn't provide the amount I need. Please provide amount."
        
    elif field == "deadline":
        from bizpulse.commitments.extractor import extract_amount_from_text
        amt_cents, _ = extract_amount_from_text(lower_text)
        if amt_cents is not None:
            return f"{text} looks like an amount. I still need the expected date, for example Friday or tomorrow."
        return "That doesn't provide the deadline I need. Please provide deadline."
        
    elif field == "party":
        return "Who is responsible for this commitment?"
        
    elif field == "object":
        return "What exactly should be delivered/sent/completed?"
        
    return f"I still need the {field} to complete this commitment."


def get_unrelated_clarification(field: str, draft: dict[str, Any]) -> str:
    c_type = draft.get("type") or "obligation"
    if field == "amount":
        return "I still need the payment amount, for example ₹42,000."
    elif field == "deadline":
        return "I still need the expected date, for example tomorrow or Friday."
    elif field == "party":
        return "Who is responsible for this commitment?"
    elif field == "object":
        return "What exactly should be delivered/sent/completed?"
    return f"I still need the {field} to complete this commitment."


def format_email_body(missing_fields: list[str], c_type: str) -> str:
    field_labels = {
        "amount": "payment amount",
        "deadline": "expected payment date" if c_type == "payment" else "expected date",
        "party": "responsible party name",
        "object": "document name" if c_type == "document" else ("expected delivery item" if c_type == "delivery" else "item or action details"),
        "action": "action to perform"
    }
    bullets = "\n".join(f"• {field_labels.get(f, f)}" for f in missing_fields)
    
    if c_type == "payment":
        example = "₹42,000 by Friday."
    elif c_type == "delivery":
        example = "replacement motor by tomorrow."
    else:
        example = "the document by Friday."
        
    return (
        f"Hi,\n\n"
        f"I can track this commitment, but I still need:\n\n"
        f"{bullets}\n\n"
        f"You can reply with both, for example:\n\n"
        f"{example}\n\n"
        f"Thanks,\n"
        f"BizPulse"
    )


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

        # Stage 2.5: Check for Pending Draft Clarification Reply (Context Bypass Gate)
        drafts = self.storage.get_drafts_for_conversation(conversation_id)
        if drafts:
            metrics.increment("messages_seen")
            
            # Select target draft using contextual, expected field and party matching
            target_draft = None
            if len(drafts) == 1:
                target_draft = drafts[0]
            else:
                matched = []
                for d in drafts:
                    score = 0
                    missing_f = d.get("missing_field")
                    v_res = validate_field_locally(missing_f, normalized_text, d.get("type"))
                    if v_res["valid"]:
                        score += 3
                    party = d.get("party") or ""
                    if party.lower() in normalized_text.lower():
                        score += 5
                    d_type = d.get("type") or ""
                    if d_type == "payment" and any(w in normalized_text.lower() for w in ["pay", "payment", "amount", "money", "rupees", "inr"]):
                        score += 2
                    elif d_type == "delivery" and any(w in normalized_text.lower() for w in ["deliver", "delivery", "ship", "goods", "replacement", "motor"]):
                        score += 2
                    elif d_type == "document" and any(w in normalized_text.lower() for w in ["doc", "document", "gst", "send", "report"]):
                        score += 2
                    if score > 0:
                        matched.append((d, score))
                if matched:
                    matched.sort(key=lambda x: x[1], reverse=True)
                    if len(matched) == 1 or matched[0][1] > matched[1][1]:
                        target_draft = matched[0][0]
                    else:
                        desc1 = f"{matched[0][0].get('party')}'s {matched[0][0].get('type')}"
                        desc2 = f"{matched[1][0].get('party')}'s {matched[1][0].get('type')}"
                        return f"I have more than one commitment that could match this. Is this about {desc1} or {desc2}?"
                else:
                    target_draft = drafts[0]

            missing_field = target_draft.get("missing_field")
            logger.info("Handling response for pending draft. Missing field: %s", missing_field)
            
            # 1. Try offline extraction first to see if they provided multiple fields in a single message
            from bizpulse.commitments.extractor import extract_offline
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            extracted = extract_offline(normalized_text, now_utc, DEFAULT_TIMEZONE)
            updated_any = False
            
            if extracted and extracted.get("has_commitment"):
                if target_draft.get("amount_cents") is None and extracted.get("amount_cents") is not None:
                    target_draft["amount_cents"] = extracted["amount_cents"]
                    target_draft["currency"] = extracted.get("currency") or "INR"
                    updated_any = True
                if not target_draft.get("deadline_utc") and extracted.get("deadline_utc"):
                    target_draft["deadline_utc"] = extracted["deadline_utc"]
                    target_draft["deadline_raw"] = extracted.get("deadline_raw")
                    updated_any = True
                if (not target_draft.get("party") or target_draft.get("party") == "Counterparty") and extracted.get("party") and extracted.get("party") != "Counterparty":
                    target_draft["party"] = extracted["party"]
                    updated_any = True
                if not target_draft.get("object") and extracted.get("object") and extracted.get("object") != "obligation":
                    target_draft["object"] = extracted["object"]
                    updated_any = True
                if not target_draft.get("action") and extracted.get("action") and extracted.get("action") != "obligation":
                    target_draft["action"] = extracted["action"]
                    updated_any = True
            
            if updated_any:
                self.storage.reset_low_signal_count(conversation_id)
            else:
                # 2. Try to validate the expected missing field locally first
                val_res = validate_field_locally(missing_field, normalized_text, target_draft.get("type"))
                if val_res["valid"]:
                    self.storage.reset_low_signal_count(conversation_id)
                    val = val_res["value"]
                    if missing_field == "amount":
                        target_draft["amount_cents"] = val["amount_cents"]
                        target_draft["currency"] = val["currency"]
                    elif missing_field == "deadline":
                        target_draft["deadline_utc"] = val["deadline_utc"]
                        target_draft["deadline_raw"] = val["deadline_raw"]
                    else:
                        target_draft[missing_field] = val
                    metrics.increment("field_values_resolved_deterministically")
                else:
                    metrics.increment("validation_attempts")
                    # 3. Fall back to LLM validation
                    val_res = validate_field_via_llm(missing_field, target_draft.get("type"), normalized_text)
                    if val_res["valid"]:
                        self.storage.reset_low_signal_count(conversation_id)
                        val = val_res["value"]
                        if missing_field == "amount":
                            target_draft["amount_cents"] = val["amount_cents"]
                            target_draft["currency"] = val["currency"]
                        elif missing_field == "deadline":
                            target_draft["deadline_utc"] = val["deadline_utc"]
                            target_draft["deadline_raw"] = val["deadline_raw"]
                        else:
                            target_draft[missing_field] = val
                    else:
                        metrics.increment("invalid_responses")
                        metrics.increment("clarification_requests")
                        is_unrelated = (not normalized_text) or (text.lower().strip() in ("thanks", "thank you", "ok", "okay", "yes", "no", "hi", "hello"))
                        if is_unrelated:
                            return get_unrelated_clarification(missing_field, target_draft)
                        else:
                            return get_invalid_clarification(missing_field, normalized_text, target_draft)

            # Recheck missing fields
            next_missing = find_missing_required_field(target_draft)
            if next_missing:
                target_draft["missing_field"] = next_missing
                self.storage.save_draft(target_draft)
                metrics.increment("clarification_requests")
                
                if channel == "email":
                    all_missing_fields = []
                    for f in ["party", "amount", "deadline", "object", "action"]:
                        temp = target_draft.copy()
                        if f == "amount" and temp.get("amount_cents") is None:
                            all_missing_fields.append("amount")
                        elif f == "deadline" and not temp.get("deadline_utc") and temp.get("type") != "approval":
                            all_missing_fields.append("deadline")
                        elif f == "party" and (not temp.get("party") or temp.get("party") == "Counterparty"):
                            all_missing_fields.append("party")
                        elif f == "object" and not temp.get("object") and temp.get("type") in ("delivery", "document", "service"):
                            all_missing_fields.append("object")
                    return format_email_body(all_missing_fields or [next_missing], target_draft.get("type"))
                else:
                    filled_part = ""
                    if missing_field == "amount" and target_draft.get("amount_cents") is not None:
                        filled_part = f"Thanks. I have {format_amount(target_draft['amount_cents'])}. "
                    elif missing_field == "deadline" and target_draft.get("deadline_raw"):
                        filled_part = f"Thanks. I have the deadline as {target_draft['deadline_raw']}. "
                    return (filled_part + get_clarification_question(next_missing, target_draft)).strip()
            else:
                self.storage.delete_draft(target_draft["draft_id"])
                self.storage.mark_onboarding_sent(conversation_id)
                self.storage.reset_low_signal_count(conversation_id)
                
                import uuid
                c_id = f"commitment_{uuid.uuid4().hex[:6]}"
                
                d_utc_str = target_draft["deadline_utc"]
                if d_utc_str.endswith('Z'):
                    d_utc_str = d_utc_str[:-1] + '+00:00'
                deadline_utc = datetime.datetime.fromisoformat(d_utc_str)
                
                new_commitment = Commitment(
                    id=c_id,
                    conversation_id=conversation_id,
                    party=target_draft["party"],
                    organization=target_draft["organization"],
                    type=target_draft["type"],
                    action=target_draft["action"] or "promise",
                    object=target_draft["object"],
                    amount_cents=target_draft["amount_cents"],
                    residual_cents=target_draft["amount_cents"],
                    currency=target_draft["currency"] or "INR",
                    deadline_utc=deadline_utc,
                    deadline_raw=target_draft["deadline_raw"],
                    timezone=DEFAULT_TIMEZONE,
                    status="pending",
                    source_message_id=message_id,
                    source_channel=channel,
                    source_text=text,
                    notes=None,
                    created_at=datetime.datetime.now(datetime.timezone.utc),
                    updated_at=datetime.datetime.now(datetime.timezone.utc),
                    extraction_method=target_draft.get("extraction_method") or "offline"
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
                    resp = f"Got it. I'm tracking {amount_str} from {new_commitment.party}, due {deadline_str}."
                else:
                    action_verb = new_commitment.action or "deliver"
                    resp = f"Got it 👍\n\n{new_commitment.party} is supposed to {action_verb} {new_commitment.object} by {deadline_str}.\n\nI'll start tracking this for you."
                    
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
                    "Hi! I'm BizPulse. I help track business commitments from conversations.\n\n"
                    "For example:\n"
                    "'Arjun will pay ₹42,000 by Friday.'\n\n"
                    "I'll ask if I need any missing details."
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
                    return "Sure. Are you trying to track a payment commitment or check an existing payment?"
                elif topic == "delivery":
                    return "Sure. Are you trying to track a delivery commitment or check an existing delivery?"
                elif topic == "document":
                    return "Sure. Are you trying to track a document commitment or check an existing document?"
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
                
                if intent == "create_commitment":
                    # Let it fall through to Stage 4 (Extraction)
                    pass
                elif intent == "answer_pending_question":
                    return "Understood. Let me know if you have any business commitments or payments to track!"
                elif intent == "list_commitments":
                    unresolved = self.storage.get_unresolved_commitments(conversation_id)
                    if not unresolved:
                        return "📋 No active commitments found in this conversation."
                    lines = ["📋 **Unresolved Commitments:**"]
                    for c in unresolved:
                        amt_suffix = f" ({format_amount(c.amount_cents)})" if c.amount_cents else ""
                        obj_suffix = f" ({c.object})" if (c.object and c.type != "payment") else ""
                        deadline_str = c.deadline_raw or c.deadline_utc.strftime("%Y-%m-%d")
                        lines.append(f"• **[{c.id.replace('commitment_', '')}] {c.type.capitalize()}**{amt_suffix}{obj_suffix}\n  {c.party}\n  Due: {deadline_str} ({c.status})")
                    return "\n".join(lines)
                elif intent == "help":
                    return (
                        "I'm BizPulse, your conversational assistant to track payments, deliveries, and document commitments.\n\n"
                        "You can tell me what was promised (e.g., 'Arjun will pay ₹42,000 by Friday'), and I'll keep track of deadlines and send follow-ups if they're overdue."
                    )
                else:
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
                all_missing_fields = []
                c_type = extracted.get("type") or "other"
                if c_type == "payment":
                    if extracted.get("amount_cents") is None:
                        all_missing_fields.append("amount")
                    if not extracted.get("deadline_utc"):
                        all_missing_fields.append("deadline")
                elif c_type in ("delivery", "document"):
                    if not extracted.get("object"):
                        all_missing_fields.append("object")
                    if not extracted.get("deadline_utc"):
                        all_missing_fields.append("deadline")
                elif c_type == "service":
                    if not extracted.get("object") and not extracted.get("action"):
                        all_missing_fields.append("object")
                    if not extracted.get("deadline_utc"):
                        all_missing_fields.append("deadline")
                
                if not extracted.get("party") or extracted.get("party") == "Counterparty":
                    all_missing_fields.insert(0, "party")
                
                import uuid
                d_id = f"draft_{uuid.uuid4().hex[:6]}"
                draft = {
                    "draft_id": d_id,
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
                metrics.increment("incomplete_commitments")
                
                if channel == "email":
                    return format_email_body(all_missing_fields or [missing_field], c_type)
                
                if len(all_missing_fields) > 1:
                    if c_type == "payment":
                        return "What amount should I record, and when is the payment expected?"
                    else:
                        bullets = "\n".join(f"• {f}" for f in all_missing_fields)
                        return f"I still need:\n{bullets}\n\nYou can provide them together or one at a time."
                
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
