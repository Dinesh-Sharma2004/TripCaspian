"""Thread-safe SQLite storage for TripCaspian sessions and watch subscriptions.

Features:
- Enables WAL mode (PRAGMA journal_mode=WAL) and busy timeouts for high concurrency.
- Enforces thread-local SQLite connections (never shares connections across threads).
- Implements per-conversation mutex locking to eliminate race conditions between user commands and background tasks.
- Only stores conversation state and metadata — NEVER caches stale route option search results.
"""

import json
import sqlite3
import threading
from typing import Any, Optional

DB_LOCKS: dict[str, threading.Lock] = {}
GLOBAL_LOCK = threading.Lock()


def get_conversation_lock(conversation_id: str) -> threading.Lock:
    """Get or create a thread lock specific to a conversation ID."""
    with GLOBAL_LOCK:
        if conversation_id not in DB_LOCKS:
            DB_LOCKS[conversation_id] = threading.Lock()
        return DB_LOCKS[conversation_id]


class SQLiteStorage:
    """Thread-safe SQLite storage wrapper."""

    def __init__(self, db_path: str = "tripcaspian.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        """Initialize database tables."""
        conn = self._get_connection()
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    conversation_id TEXT PRIMARY KEY,
                    state_name TEXT NOT NULL,
                    collected_fields TEXT NOT NULL,
                    selected_provider TEXT,
                    selected_option_id TEXT,
                    active_job_id TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watch_subscriptions (
                    conversation_id TEXT PRIMARY KEY,
                    option_id TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    watching INTEGER NOT NULL DEFAULT 1,
                    last_seats_left INTEGER,
                    last_price REAL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def get_session(self, conversation_id: str) -> dict[str, Any] | None:
        """Retrieve session state for a conversation ID."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE conversation_id = ?", (conversation_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            session = dict(row)
            session["collected_fields"] = json.loads(session["collected_fields"])
            return session

    def save_session(
        self,
        conversation_id: str,
        state_name: str,
        collected_fields: dict[str, Any],
        selected_provider: str | None = None,
        selected_option_id: str | None = None,
        active_job_id: str | None = None,
    ) -> None:
        """Save or update session state."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            fields_json = json.dumps(collected_fields)
            with conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        conversation_id, state_name, collected_fields,
                        selected_provider, selected_option_id, active_job_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        state_name = excluded.state_name,
                        collected_fields = excluded.collected_fields,
                        selected_provider = excluded.selected_provider,
                        selected_option_id = excluded.selected_option_id,
                        active_job_id = excluded.active_job_id,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (
                        conversation_id,
                        state_name,
                        fields_json,
                        selected_provider,
                        selected_option_id,
                        active_job_id,
                    ),
                )

    def set_watch_subscription(
        self,
        conversation_id: str,
        option_id: str,
        provider_name: str,
        source: str,
        destination: str,
        watching: bool = True,
        last_seats_left: int | None = None,
        last_price: float | None = None,
    ) -> None:
        """Add or update an active route watch subscription."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO watch_subscriptions (
                        conversation_id, option_id, provider_name, source, destination,
                        watching, last_seats_left, last_price, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        option_id = excluded.option_id,
                        provider_name = excluded.provider_name,
                        source = excluded.source,
                        destination = excluded.destination,
                        watching = excluded.watching,
                        last_seats_left = excluded.last_seats_left,
                        last_price = excluded.last_price,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (
                        conversation_id,
                        option_id,
                        provider_name,
                        source,
                        destination,
                        1 if watching else 0,
                        last_seats_left,
                        last_price,
                    ),
                )

    def get_active_watch_subscriptions(self) -> list[dict[str, Any]]:
        """Retrieve all active watch subscriptions (where watching=1)."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM watch_subscriptions WHERE watching = 1"
        )
        return [dict(row) for row in cursor.fetchall()]

    def cancel_watch_subscription(self, conversation_id: str) -> None:
        """Mark watch subscription as inactive for a conversation."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "UPDATE watch_subscriptions SET watching = 0 WHERE conversation_id = ?",
                    (conversation_id,),
                )
