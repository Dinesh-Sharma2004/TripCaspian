"""Thread-safe SQLite storage for BizPulse commitments.

Features:
- Enables WAL mode and busy timeouts for high concurrency.
- Enforces thread-local SQLite connections.
- Implements per-conversation mutex locking to prevent race conditions.
"""

import json
import sqlite3
import threading
import logging
from typing import Any, Optional
from datetime import datetime

from bizpulse.commitments.models import Commitment
from bizpulse.config import DATABASE_PATH

logger = logging.getLogger(__name__)

DB_LOCKS: dict[str, threading.Lock] = {}
GLOBAL_LOCK = threading.Lock()


def get_conversation_lock(conversation_id: str) -> threading.Lock:
    """Get or create a thread lock specific to a conversation ID."""
    with GLOBAL_LOCK:
        if conversation_id not in DB_LOCKS:
            DB_LOCKS[conversation_id] = threading.Lock()
        return DB_LOCKS[conversation_id]


class SQLiteStorage:
    """Thread-safe SQLite storage wrapper for BizPulse."""

    def __init__(self, db_path: str = DATABASE_PATH):
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
        """Initialize commitments database table and clear old tables if any."""
        conn = self._get_connection()
        with conn:
            # Create commitments table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS commitments (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    party TEXT NOT NULL,
                    organization TEXT,
                    type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    object TEXT,
                    amount_cents INTEGER,
                    residual_cents INTEGER,
                    currency TEXT,
                    deadline_utc TEXT NOT NULL,
                    deadline_raw TEXT,
                    timezone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    source_channel TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_followup_at TEXT,
                    followup_count INTEGER DEFAULT 0,
                    confidence REAL,
                    active_job_id TEXT
                );
            """)
            
            # Dropped the sessions and watch_subscriptions tables
            conn.execute("DROP TABLE IF EXISTS sessions;")
            conn.execute("DROP TABLE IF EXISTS watch_subscriptions;")

    def save_commitment(self, commitment: Commitment) -> None:
        """Save or update a commitment in the database."""
        lock = get_conversation_lock(commitment.conversation_id)
        with lock:
            conn = self._get_connection()
            data = commitment.to_dict()
            with conn:
                conn.execute(
                    """
                    INSERT INTO commitments (
                        id, conversation_id, party, organization, type, action, object,
                        amount_cents, residual_cents, currency, deadline_utc, deadline_raw,
                        timezone, status, source_message_id, source_channel, source_text,
                        notes, created_at, updated_at, last_followup_at, followup_count,
                        confidence, active_job_id
                    ) VALUES (
                        :id, :conversation_id, :party, :organization, :type, :action, :object,
                        :amount_cents, :residual_cents, :currency, :deadline_utc, :deadline_raw,
                        :timezone, :status, :source_message_id, :source_channel, :source_text,
                        :notes, :created_at, :updated_at, :last_followup_at, :followup_count,
                        :confidence, :active_job_id
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        conversation_id = excluded.conversation_id,
                        party = excluded.party,
                        organization = excluded.organization,
                        type = excluded.type,
                        action = excluded.action,
                        object = excluded.object,
                        amount_cents = excluded.amount_cents,
                        residual_cents = excluded.residual_cents,
                        currency = excluded.currency,
                        deadline_utc = excluded.deadline_utc,
                        deadline_raw = excluded.deadline_raw,
                        timezone = excluded.timezone,
                        status = excluded.status,
                        source_message_id = excluded.source_message_id,
                        source_channel = excluded.source_channel,
                        source_text = excluded.source_text,
                        notes = excluded.notes,
                        updated_at = excluded.updated_at,
                        last_followup_at = excluded.last_followup_at,
                        followup_count = excluded.followup_count,
                        confidence = excluded.confidence,
                        active_job_id = excluded.active_job_id;
                    """,
                    data,
                )

    def get_commitment(self, commitment_id: str) -> Commitment | None:
        """Retrieve a specific commitment by its ID."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return Commitment.from_dict(dict(row))

    def get_unresolved_commitments(self, conversation_id: str) -> list[Commitment]:
        """Retrieve all active/unresolved commitments for a specific conversation ID."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            # Unresolved states: anything except verified_fulfilled and abandoned
            cursor = conn.execute(
                """
                SELECT * FROM commitments 
                WHERE conversation_id = ? 
                AND status NOT IN ('verified_fulfilled', 'abandoned')
                ORDER BY updated_at DESC
                """,
                (conversation_id,),
            )
            return [Commitment.from_dict(dict(row)) for row in cursor.fetchall()]

    def get_all_commitments(self) -> list[Commitment]:
        """Retrieve all commitments in the database."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM commitments ORDER BY updated_at DESC")
        return [Commitment.from_dict(dict(row)) for row in cursor.fetchall()]

    def delete_commitment(self, commitment_id: str) -> None:
        """Delete a commitment by its ID."""
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM commitments WHERE id = ?", (commitment_id,))
