"""BizPulse Agent — Single multi-channel handler using Caspian SDK.

Connects Telegram and Email channels to a single `@client.on_message` handler.
Launches background scheduler and watcher daemons before blocking on `client.listen()`.
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from caspian_sdk import CommClient
from bizpulse.storage import SQLiteStorage
from bizpulse.scheduler import CommitmentScheduler
from bizpulse.watcher import CommitmentWatcher
from bizpulse.service import BizPulseService
from bizpulse.config import DATABASE_PATH

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bizpulse")


def validate_environment() -> tuple[str, str]:
    """Pre-flight environment variable validation."""
    caspian_key = os.environ.get("CASPIAN_API_KEY")
    if not caspian_key or caspian_key.strip() == "your_caspian_api_key_here":
        print("\n❌ Error: CASPIAN_API_KEY is missing or invalid in .env")
        print("   Fix: Set CASPIAN_API_KEY in .env\n")
        sys.exit(1)

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        print("\n❌ Error: TELEGRAM_BOT_TOKEN is missing in .env")
        print("   Fix: Add TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here to .env\n")
        sys.exit(1)

    return caspian_key, telegram_token


# Initialize global components
storage = SQLiteStorage(db_path=DATABASE_PATH)
scheduler = CommitmentScheduler(db_path=DATABASE_PATH)
watcher = CommitmentWatcher(storage=storage, poll_interval=int(os.environ.get("WATCHER_POLL_INTERVAL", "30")))
service = BizPulseService(storage=storage, scheduler=scheduler, watcher=watcher)


def main():
    """Main entry point to initialize Caspian channels and start listening loop."""
    caspian_key, telegram_token = validate_environment()

    print("[OK] Environment loaded (.env)")
    print(f"[OK] SQLite database initialized ({DATABASE_PATH})")

    # Start CommitmentScheduler background engine
    scheduler.start()
    print("[OK] Scheduler daemon started")

    # Create Caspian CommClient instance
    client = CommClient()
    service.set_caspian_client(client)
    print("[OK] Caspian CommClient initialized")

    # Connect Telegram Bot channel
    try:
        conn = client.connect_telegram(bot_token=telegram_token)
        bot_addr = conn.get("address", "@bizpulse_bot")
        print(f"[OK] Telegram connected ({bot_addr})")
    except Exception as e:
        if "409" in str(e) or "already connected" in str(e):
            print(f"[OK] Telegram bot already connected to Caspian Gateway")
        else:
            print(f"[ERROR] Failed to connect Telegram bot: {e}")
            sys.exit(1)

    # Optional Email Connection
    email_user = os.environ.get("EMAIL_USERNAME", "bizpulse")
    try:
        client.connect_email(username=email_user)
        print(f"[OK] Email connected ({email_user}@agents.trycaspianai.com)")
    except Exception as e:
        logger.debug("Email connection notice: %s", e)

    # Start Watcher daemon thread BEFORE calling blocking client.listen()
    watcher.start_daemon(client=client)
    print("[OK] Watcher daemon started")

    # Single on_message handler across ALL connected channels
    @client.on_message
    def on_message(message):
        logger.info(
            "Received message on channel '%s' (conversation_id: %s): %s",
            message.channel,
            message.conversation_id,
            message.text,
        )
        reply_text = service.handle_user_message(
            conversation_id=message.conversation_id,
            sender=message.sender,
            text=message.text or "",
            message_id=message.id,
            channel=message.channel,
            subject=getattr(message, "subject", None),
        )
        if reply_text:
            message.reply(text=reply_text)

    # Single on_interaction handler for button click callbacks
    @client.on_interaction
    def on_interaction(interaction):
        logger.info("Received interaction value: %s", interaction.value)
        reply_text, blocks = service.handle_interaction(interaction.value)
        if reply_text:
            interaction.reply(text=reply_text, blocks=blocks)

    print("[OK] Caspian listening for messages\n")
    print("==========================================================================")
    print("BizPulse is online. Send a message to your bot on Telegram or Email to begin testing.")
    print("==========================================================================\n")

    client.listen(ack="Analyzing conversation...")


if __name__ == "__main__":
    main()
