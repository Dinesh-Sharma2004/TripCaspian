# BizPulse 💼 — Business Commitment & Obligation Agent

> **Built for the Caspian Hackathon Submission**
> Reachable via **Telegram** and **Email** using a single unified `on_message` and `on_interaction` handler powered by [Caspian SDK](https://github.com/TryCaspian/caspian-sdk).

---

## 1. Problem Statement & Context

In daily business operations, promises and commitments are frequently made during conversations:
* *"I'll pay ₹42,000 by Friday."*
* *"The shipment will arrive Wednesday."*
* *"I'll send the GST certificate tomorrow."*

These obligations often get buried in chat histories, email threads, or messaging channels, requiring manual follow-ups, calendars, or spreadsheet trackers. **BizPulse** solves this by converting natural language commitments directly into structured business obligations, tracking their lifecycle deterministically, and proactively alerting the relevant parties when action is required.

---

## 2. Architecture & Pipeline

BizPulse uses a multi-stage deterministic pipeline to process messages. The LLM is invoked only when semantic language understanding is strictly required, keeping token usage minimal.

```
                  ┌─────────────────────────────────────────┐
                  │      Caspian Inbound Channels           │
                  │       Telegram  │  Email Inbox          │
                  └────────────────────┬────────────────────┘
                                       │
                                       ▼
                             caspian_sdk.CommClient
                                       │
                                       ▼
                       @client.on_message (Single Handler)
                                       │
                                       ▼
                       bizpulse.service.BizPulseService
  ┌────────────────────────────────────┼────────────────────────────────────┐
  │                                    │                                    │
  ▼                                    ▼                                    ▼
1. Deduplication                    2. Normalizer                        3. Signal Gate
(Channel + Message ID)              (Strips quotes/signatures)           (Deterministic Scoring)
  │                                    │                                    │
  └────────────────────────────────────┼────────────────────────────────────┘
                                       │
                                       ▼
                                4. LLM Extractor
                             (Gemini 2.5 Flash / Rule-based)
                                       │
                                       ▼
                                 5. Resolver
                            (Matches active commitments)
                                       │
                                       ▼
                           bizpulse.storage.SQLiteStorage
                                       │
                    ┌──────────────────┴──────────────────┐
                    ▼                                     ▼
          bizpulse.scheduler                      bizpulse.watcher
     (APScheduler + SQLiteJobStore)          (Resilient Overdue Poller)
                    │                                     │
                    └──────────────────┬──────────────────┘
                                       │
                                       ▼
                        client.send_message (outbound)
```

### Ingestion Pipeline Stages:
1. **Deduplicate**: Deduplicates incoming messages by `(channel, message_id)` keys to prevent double-processing.
2. **Normalize**: Extracts the core text from raw emails/messages, stripping signatures, quoted email replies, HTML tags, and collapsing whitespace. Hard capped at 2,000 characters.
3. **Gate**: Deterministically scores signals in the normalized text (action verbs, money terms, obligation phrases, status words). If the score is `< 3`, the message is ignored, costing **0 tokens**.
4. **Extraction**: Invokes `gemini-2.5-flash` with a structured schema to parse commitment fields (or falls back to a deterministic rule-based offline extractor if `GEMINI_API_KEY` is missing).
5. **Resolve**: Scores candidates against unresolved commitments for the same conversation ID. Matches with score $\ge 5$ update existing commitments; otherwise, new commitments are created.
6. **Lifecycle Update & Scheduling**: Saves to SQLite, schedules exact deadline alerts using APScheduler, and manages background watcher safety nets.

---

## 3. Supported Ingestion Channels

BizPulse supports Caspian-native channels:
* **Telegram**: Direct messaging with bot credentials.
* **Email**: Caspian-hosted custom email inboxes (`username@agents.trycaspianai.com`).

*BizPulse does NOT claim to automatically monitor WhatsApp, Slack, bank accounts, UPI gateways, or CRM systems. All fulfillments must be conversational claims or manually verified.*

---

## 4. Commitment Lifecycle

Every commitment goes through a strict state machine:
* **pending** — Created, awaiting deadline.
* **due** — Deadline reached, not yet overdue.
* **overdue** — Past deadline, unresolved.
* **rescheduled** — Deadline updated by counterparty (cancels old scheduler jobs, creates new ones).
* **fulfillment_claimed** — Counterparty claims to have completed the commitment (e.g. *"payment sent"*).
* **verified_fulfilled** — Manually confirmed by owner.
* **disputed** — Obligation contested by counterparty.
* **escalated** — Escalated after repeated failures.
* **abandoned** — Explicitly dropped.

---

## 5. Setup & Running the Agent

### Installation

```bash
# Clone the repository
git clone https://github.com/Dinesh-Sharma2004/CodeRunner.git
cd tripcaspian

# Install package and dependencies in editable mode
pip install -e .
```

### Configuration (`.env`)

Configure the following variables in `.env`:

```env
# Caspian API Configuration
CASPIAN_API_KEY=your_caspian_key_here
CASPIAN_BASE_URL=https://api.trycaspianai.com

# Channel Bot Credentials
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
EMAIL_USERNAME=bizpulse

# Gemini API Key (Optional. If omitted, falls back to deterministic rule extractor)
GEMINI_API_KEY=your_gemini_key_here

# Storage Settings
DATABASE_PATH=tripcaspian.db
WATCHER_POLL_INTERVAL=30
```

### How to Run

To run the agent and start listening to connected Caspian channels:

```bash
.venv\Scripts\python -m bizpulse.agent
```

### How to Test

To run the test suite (all tests execute locally and use the fallback rule extractor, requiring zero external API keys):

```bash
.venv\Scripts\pytest --basetemp=.pytest_tmp
```

---

## 6. Known Limitations & Future Roadmap

### Known Limitations:
1. **Conversation Scope Only**: Commitments are resolved and matched within the same `conversation_id`. Rescheduling via email will not automatically link to a Telegram commitment in v0.1 (cross-channel entity mapping is deferred).
2. **Smallest Unit Integer Amounts**: Amounts are stored in cents/paise (integer values) to avoid floating point math errors.
3. **No Auto-verification**: UPI/bank account verification does not exist. Statuses change to `fulfillment_claimed` upon conversational statement.
