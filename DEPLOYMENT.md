# BizPulse Deployment Guide for Northflank

This document outlines the deployment configuration, build steps, and requirements for running BizPulse as a long-running background service on Northflank.

## 1. Architecture Overview
BizPulse runs as a persistent multi-channel worker listening for incoming events using the Caspian SDK. It does not expose public HTTP endpoints.
* **Service Type:** Long-running service (worker/daemon)
* **Ports:** None (no public HTTP port required)

---

## 2. Build Settings
Northflank can automatically build the service from the repository Dockerfile.

* **Build Source:** `Dockerfile` (located in `tripcaspian/Dockerfile`)
* **Base Image:** `python:3.11-slim`
* **Working Directory:** `/app`

---

## 3. Startup Command
The service starts and stays in the foreground listening for Caspian Gateway messages:
```bash
python -m bizpulse.agent
```

---

## 4. Required Environment Variables
Configure the following environment variables in your Northflank service settings:

### Critical/Required Credentials
* `CASPIAN_API_KEY`: Your Caspian SDK Gateway access credential.
* `TELEGRAM_BOT_TOKEN`: The API token for your Telegram integration bot.

### Optional Integrations / Fallbacks
* `DISCORD_BOT_TOKEN`: The bot token for optional Discord channel integration.
* `EMAIL_USERNAME`: The localpart for the unified Caspian email address (defaults to `bizpulse`).
* `GEMINI_API_KEY`: API credential used for progressive draft classification and fallback semantic validation.

### Configuration Controls
* `DATABASE_PATH`: Relative or absolute path to the persistent SQLite database file (defaults to `tripcaspian.db`). For Northflank, this should point to a file inside the persistent mount directory (e.g. `/data/tripcaspian.db`).
* `BIZPULSE_TIMEZONE`: Default system timezone (defaults to `Asia/Kolkata`).
* `CONFIDENCE_THRESHOLD`: Probability limit for LLM commitment classification (defaults to `0.65`).
* `WATCHER_POLL_INTERVAL`: Periodicity in seconds for checking upcoming commitment deadlines (defaults to `30`).
* `PYTHONUNBUFFERED`: Should be set to `1` in the Dockerfile/environment to ensure container output logs are flushed immediately to the Northflank logs.

---

## 5. Persistent Storage & Volumes
BizPulse uses an SQLite database to store drafts and persistent obligations. Northflank must mount a persistent volume to preserve data across container restarts.

* **Volume Source:** Persistent Volume (SSD/HDD block storage volume on Northflank)
* **Mount Path in Container:** `/data`
* **Recommended `DATABASE_PATH` Environment Value:** `/data/tripcaspian.db`
