"""Background Seat Availability and Price Alert Watcher Daemon for TripCaspian.

Polls active route watch subscriptions stored in SQLite and sends proactive notifications
via `client.send_message(conversation_id, text)` when seat counts drop below threshold
or prices fluctuate.
"""

import logging
import time
import threading
from typing import Any
from tripcaspian.storage import SQLiteStorage
from tripcaspian.providers import IRCTCProvider, RedBusProvider, CabProvider

logger = logging.getLogger(__name__)


class AvailabilityWatcher:
    """Background polling worker for active watch subscriptions."""

    def __init__(
        self,
        storage: SQLiteStorage,
        caspian_client: Any = None,
        poll_interval: int = 30,
        seat_threshold: int = 5,
    ):
        self.storage = storage
        self.client = caspian_client
        self.poll_interval = poll_interval
        self.seat_threshold = seat_threshold
        self._running = False
        self._thread: threading.Thread | None = None
        self.providers = {
            "irctc": IRCTCProvider(),
            "train": IRCTCProvider(),
            "redbus": RedBusProvider(),
            "bus": RedBusProvider(),
            "cab": CabProvider(),
        }

    def start_daemon(self, client: Any = None) -> None:
        """Start the background watcher thread."""
        if client:
            self.client = client

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TripCaspianWatcher")
        self._thread.start()
        logger.info("AvailabilityWatcher daemon started (poll_interval=%ds).", self.poll_interval)

    def stop(self) -> None:
        """Stop the background watcher thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _run_loop(self) -> None:
        """Daemon polling execution loop."""
        while self._running:
            try:
                self.check_all_subscriptions()
            except Exception:
                logger.exception("Error during watcher polling execution loop.")
            time.sleep(self.poll_interval)

    def check_all_subscriptions(self) -> None:
        """Poll active subscriptions and send alerts if seat count or price changes."""
        subscriptions = self.storage.get_active_watch_subscriptions()
        for sub in subscriptions:
            conv_id = sub["conversation_id"]
            opt_id = sub["option_id"]
            provider_key = sub["provider_name"].lower()

            provider = self.providers.get(provider_key) or self.providers["train"]
            status = provider.check_availability(opt_id)

            seats = status.get("seats_left", 10)
            price = status.get("price", sub.get("last_price", 0.0))

            last_seats = sub.get("last_seats_left")
            last_price = sub.get("last_price")

            alert_reasons = []

            # Check seat threshold alert
            if seats < self.seat_threshold and (last_seats is None or seats < last_seats):
                alert_reasons.append(f"⚠️ SEAT ALERT: Only {seats} seats remaining!")

            # Check price drop alert
            if last_price and price < last_price:
                alert_reasons.append(f"📉 PRICE DROP ALERT: Fare dropped from ₹{last_price:,.0f} to ₹{price:,.0f}!")

            if alert_reasons and self.client:
                alert_text = (
                    f"🔔 **TripCaspian Route Alert** ({sub['source']} ➡️ {sub['destination']})\n"
                    + "\n".join(alert_reasons)
                    + f"\n\nReply 'book option' or 'book now' to secure your booking handoff link!"
                )
                try:
                    self.client.send_message(conversation_id=conv_id, text=alert_text)
                    logger.info("Sent watcher alert for conversation %s", conv_id)
                except Exception:
                    logger.exception("Failed to send watcher alert for conversation %s", conv_id)

            # Update recorded status in database
            self.storage.set_watch_subscription(
                conversation_id=conv_id,
                option_id=opt_id,
                provider_name=sub["provider_name"],
                source=sub["source"],
                destination=sub["destination"],
                watching=True,
                last_seats_left=seats,
                last_price=price,
            )
