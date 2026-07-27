# TripCaspian 🧳 — Multi-Channel AI Trip-Planning & Booking Agent

> **Built for the Caspian Internship Track ("Build a Real Agent")**
> Reachable via **Telegram**, **Discord**, and **Email** using a single unified `on_message` handler powered by [Caspian SDK](https://github.com/TryCaspian/caspian-sdk).

---

## 1. Problem It Solves

Travelers frequently waste hours jumping between IRCTC (trains), redBus (buses), and Uber/Ola (intercity cabs) to find options that fit both their time window and budget. **TripCaspian** acts as a unified conversational travel assistant. It accepts free-text travel requests, queries multiple transport providers simultaneously, scores every option on price and duration, applies intelligent 5–10% budget concessions when exact budget matches are missing, schedules delayed booking handoffs with one-click cancellation, and proactively alerts travelers when seats run low.

---

## 2. Architecture & How Caspian Fits

```
              ┌─────────────────────────────────────────────────────────┐
              │           Connected Messaging Platforms                 │
              │     Telegram Bot   │  Discord Bot  │  Email Inbox       │
              └────────────────────────────┬────────────────────────────┘
                                           │
                                           ▼
                              caspian_sdk.CommClient
                                           │
                                           ▼
                           @client.on_message (Single Handler)
                                           │
                                           ▼
                             tripcaspian.service.TripService
 ┌─────────────────────────────────────────┼─────────────────────────────────────────┐
 │                                         │                                         │
 ▼                                         ▼                                         ▼
tripcaspian.intake               tripcaspian.optimizer                     tripcaspian.providers
(NLU & Follow-ups)               (Normalized Scoring &                      (IRCTC, redBus, Uber SDK,
                                 Budget Concession)                         Mock Fixture Data)
 │                                         │                                         │
 └─────────────────────────────────────────┼─────────────────────────────────────────┘
                                           │
                                           ▼
                       tripcaspian.storage.SQLiteStorage
                       (WAL Mode + Conversation Mutex Locks)
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
            tripcaspian.scheduler                     tripcaspian.watcher
        (APScheduler + SQLiteJobStore)           (Background Availability Poller)
                        │                                     │
                        └──────────────────┬──────────────────┘
                                           │
                                           ▼
                       client.send_message(conversation_id, text)
                       (Proactive Outbound Alerts & Delayed Handoffs)
```

### Why Caspian?
- **Single Identity Across Channels**: All incoming messages flow through one `@client.on_message` callback keyed by `message.conversation_id`.
- **Zero Per-Platform Boilerplate**: Telegram, Discord, and Email are connected with 3 lines of code (`connect_telegram`, `connect_discord`/`install_discord`, `connect_email`).
- **Proactive Outbound Messaging**: Uses `client.send_message(conversation_id, text)` to trigger proactive seat alerts and delayed handoffs across any channel without waiting for a user prompt.

---

## 3. Provider Integration & Honest Scoping

### Provider Integration Overview
- **IRCTC (Train)**: Offers fare and train schedule lookups via partner API or mock fixtures. Checkout uses prefilled IRCTC deep links.
- **redBus (Bus)**: Uses `parse_apis.redBus_Bus_and_Train_API` when present or mock bus travel fixtures.
- **Uber (Cab)**: Integrates the official **`uber-rides` Python SDK** (`UberRidesClient`).

### Official Uber Rides SDK Integration
- **Search & Estimates**: Uses `client.get_products()` and `client.get_price_estimates()` to fetch real-time product options and fares.
- **OAuth Rider Authentication**: Built-in helpers (`create_auth_url`, `exchange_code`, `save_credentials`, `load_credentials`) manage `AuthorizationCodeGrant` flows.
- **Booking & Management**: `initiate_booking()` calls `client.request_ride()`, `get_booking_status()` calls `client.get_ride_details()`, and `cancel_booking()` calls `client.cancel_ride()`.
- **Sandbox Mode**: Configured via `UBER_SANDBOX=true`.
- **Scope Limitations Note**: Autonomous ride booking (`request_ride`) requires Uber's privileged `request` OAuth scope, which requires Uber developer app approval. When unauthenticated, TripCaspian cleanly provides prefilled Uber deep links.

---

## 4. Setup & Environment Variables

### Installation

```bash
# Clone the repository
git clone https://github.com/Dinesh-Sharma2004/CodeRunner.git
cd tripcaspian

# Install dependencies including official uber-rides SDK
pip install -e .
pip install uber-rides
```

### Configuration (`.env`)

Copy `.env.example` to `.env` and configure your credentials:

```bash
cp .env.example .env
```

```env
# Caspian API Configuration
CASPIAN_API_KEY=your_caspian_api_key_here
CASPIAN_BASE_URL=https://api.trycaspianai.com

# Channel Bot Credentials
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...
DISCORD_BOT_TOKEN=
DISCORD_DISPLAY_NAME=TripCaspian
EMAIL_USERNAME=tripcaspian

# Provider Mode (mock | live)
PROVIDER_MODE=mock
IRCTC_PARTNER_KEY=
REDBUS_PARTNER_KEY=

# Official Uber Rides Python SDK Configuration
UBER_SERVER_TOKEN=your_server_token_here
UBER_CLIENT_ID=your_client_id_here
UBER_CLIENT_SECRET=your_client_secret_here
UBER_REDIRECT_URI=http://localhost:8000/oauth/callback
UBER_SANDBOX=true

# Storage & Polling Settings
DATABASE_PATH=tripcaspian.db
WATCHER_POLL_INTERVAL=30
```

---

## 5. How to Run & Test

### Start the Agent

```bash
python -m tripcaspian.agent
```

### Run Unit Test Suite

```bash
py -m pytest --basetemp=./.pytest_tmp -v
```

All unit test suites (including `tests/test_uber_provider.py`) execute cleanly.

---

## 6. Evaluation Criteria Write-Up

### Problem Solved
TripCaspian solves multi-modal travel planning friction in India. Travelers comparing trains, buses, and cabs across different budgets and schedules no longer need to check multiple apps manually. TripCaspian standardizes route discovery, applies intelligent budget concessions, and automates tracking in chat.

### Code Quality & Engineering
Built with modular Python architecture:
- **`service.py`**: State machine facade isolating Caspian infrastructure from domain logic.
- **`storage.py`**: Thread-safe SQLite engine with WAL mode and per-conversation locks.
- **`optimizer.py`**: Pure function scoring with deterministic 5-tier tie-breaking.
- **`scheduler.py`**: APScheduler backed by `SQLiteJobStore` for persistent delayed handoffs.
- **`watcher.py`**: Subscription-based background polling thread for seat & price alerts.
- **`cab_ola_uber.py`**: Official `uber-rides` Python SDK integration with `AuthorizationCodeGrant` and `ProviderError` handling.

### Adoption & Practicality
Ready for real-world deployment across Telegram, Discord, and Email. The deep-link handoff and OAuth strategy respects provider ToS and security constraints while providing prefilled routes directly to users.

### How Caspian Fits
Caspian serves as the central communication backbone. Rather than building per-platform bot integrations or webhook endpoints, TripCaspian connects multiple messaging platforms to a single `on_message` handler and leverages `client.send_message()` for proactive background notifications.
