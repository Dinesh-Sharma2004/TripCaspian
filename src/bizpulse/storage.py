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
from datetime import datetime, timezone

from bizpulse.commitments.models import Commitment
from bizpulse.config import DATABASE_PATH

logger = logging.getLogger(__name__)

DB_LOCKS: dict[str, threading.Lock] = {}
GLOBAL_LOCK = threading.Lock()


def get_conversation_lock(conversation_id: str) -> threading.RLock:
    """Get or create a thread lock specific to a conversation ID."""
    with GLOBAL_LOCK:
        if conversation_id not in DB_LOCKS:
            DB_LOCKS[conversation_id] = threading.RLock()
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
                    next_followup_at TEXT,
                    extraction_method TEXT,
                    followup_count INTEGER DEFAULT 0,
                    confidence REAL,
                    active_job_id TEXT
                );
            """)
            
            # Create drafts table with migration check
            cursor = conn.execute("PRAGMA table_info(drafts);")
            columns = [row["name"] for row in cursor.fetchall()]
            
            if columns and "draft_id" not in columns:
                logger.info("Migrating drafts table to new schema...")
                conn.execute("ALTER TABLE drafts RENAME TO drafts_old;")
                conn.execute("""
                    CREATE TABLE drafts (
                        draft_id TEXT PRIMARY KEY,
                        conversation_id TEXT,
                        party TEXT,
                        organization TEXT,
                        type TEXT,
                        action TEXT,
                        object TEXT,
                        amount_cents INTEGER,
                        currency TEXT,
                        deadline_utc TEXT,
                        deadline_raw TEXT,
                        intent TEXT,
                        source_message_id TEXT,
                        source_channel TEXT,
                        source_text TEXT,
                        missing_field TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_conversation_id ON drafts(conversation_id);")
                conn.execute("""
                    INSERT INTO drafts (
                        draft_id, conversation_id, party, organization, type, action, object,
                        amount_cents, currency, deadline_utc, deadline_raw, intent,
                        source_message_id, source_channel, source_text, missing_field,
                        created_at, updated_at
                    )
                    SELECT 
                        'draft_' || conversation_id, conversation_id, party, organization, type, action, object,
                        amount_cents, currency, deadline_utc, deadline_raw, intent,
                        source_message_id, source_channel, source_text, missing_field,
                        created_at, updated_at
                    FROM drafts_old;
                """)
                conn.execute("DROP TABLE drafts_old;")
                logger.info("Successfully migrated drafts table.")
            elif not columns:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS drafts (
                        draft_id TEXT PRIMARY KEY,
                        conversation_id TEXT,
                        party TEXT,
                        organization TEXT,
                        type TEXT,
                        action TEXT,
                        object TEXT,
                        amount_cents INTEGER,
                        currency TEXT,
                        deadline_utc TEXT,
                        deadline_raw TEXT,
                        intent TEXT,
                        source_message_id TEXT,
                        source_channel TEXT,
                        source_text TEXT,
                        missing_field TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_conversation_id ON drafts(conversation_id);")

            # Create onboarding_status table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS onboarding_status (
                    conversation_id TEXT PRIMARY KEY,
                    onboarding_sent INTEGER DEFAULT 0
                );
            """)

            # Create low_signal_states table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS low_signal_states (
                    conversation_id TEXT PRIMARY KEY,
                    low_signal_count INTEGER DEFAULT 0,
                    last_low_signal_at TEXT,
                    clarification_topic TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Dynamic migration for existing databases
            cursor = conn.execute("PRAGMA table_info(commitments);")
            columns = [row["name"] for row in cursor.fetchall()]
            if "next_followup_at" not in columns:
                conn.execute("ALTER TABLE commitments ADD COLUMN next_followup_at TEXT;")
            if "extraction_method" not in columns:
                conn.execute("ALTER TABLE commitments ADD COLUMN extraction_method TEXT;")
            
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
                        notes, created_at, updated_at, last_followup_at, next_followup_at,
                        extraction_method, followup_count, confidence, active_job_id
                    ) VALUES (
                        :id, :conversation_id, :party, :organization, :type, :action, :object,
                        :amount_cents, :residual_cents, :currency, :deadline_utc, :deadline_raw,
                        :timezone, :status, :source_message_id, :source_channel, :source_text,
                        :notes, :created_at, :updated_at, :last_followup_at, :next_followup_at,
                        :extraction_method, :followup_count, :confidence, :active_job_id
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
                        next_followup_at = excluded.next_followup_at,
                        extraction_method = excluded.extraction_method,
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

    def save_draft(self, draft: dict[str, Any]) -> None:
        """Save or update a commitment draft in persistent storage."""
        if not draft.get("draft_id"):
            import uuid
            draft["draft_id"] = f"draft_{uuid.uuid4().hex[:6]}"
            
        full_draft = {
            "draft_id": None,
            "conversation_id": None,
            "party": None,
            "organization": None,
            "type": None,
            "action": None,
            "object": None,
            "amount_cents": None,
            "currency": None,
            "deadline_utc": None,
            "deadline_raw": None,
            "intent": None,
            "source_message_id": None,
            "source_channel": None,
            "source_text": None,
            "missing_field": None,
        }
        full_draft.update(draft)
        draft = full_draft
            
        lock = get_conversation_lock(draft["conversation_id"])
        with lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO drafts (
                        draft_id, conversation_id, party, organization, type, action, object,
                        amount_cents, currency, deadline_utc, deadline_raw, intent,
                        source_message_id, source_channel, source_text, missing_field,
                        updated_at
                    ) VALUES (
                        :draft_id, :conversation_id, :party, :organization, :type, :action, :object,
                        :amount_cents, :currency, :deadline_utc, :deadline_raw, :intent,
                        :source_message_id, :source_channel, :source_text, :missing_field,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT(draft_id) DO UPDATE SET
                        conversation_id = excluded.conversation_id,
                        party = excluded.party,
                        organization = excluded.organization,
                        type = excluded.type,
                        action = excluded.action,
                        object = excluded.object,
                        amount_cents = excluded.amount_cents,
                        currency = excluded.currency,
                        deadline_utc = excluded.deadline_utc,
                        deadline_raw = excluded.deadline_raw,
                        intent = excluded.intent,
                        source_message_id = excluded.source_message_id,
                        source_channel = excluded.source_channel,
                        source_text = excluded.source_text,
                        missing_field = excluded.missing_field,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    draft,
                )

    def get_draft(self, id_or_conv_id: str) -> dict[str, Any] | None:
        """Retrieve a draft by draft_id OR conversation_id (fallback to latest draft)."""
        lock = get_conversation_lock(id_or_conv_id)
        with lock:
            conn = self._get_connection()
            cursor = conn.execute("SELECT * FROM drafts WHERE draft_id = ?", (id_or_conv_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            cursor = conn.execute("SELECT * FROM drafts WHERE conversation_id = ? ORDER BY updated_at DESC LIMIT 1", (id_or_conv_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_latest_draft(self, conversation_id: str) -> dict[str, Any] | None:
        """Retrieve the most recently updated draft for a conversation ID."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT * FROM drafts WHERE conversation_id = ? ORDER BY updated_at DESC LIMIT 1",
                (conversation_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_drafts_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """Retrieve all drafts for a conversation ID."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT * FROM drafts WHERE conversation_id = ? ORDER BY updated_at DESC",
                (conversation_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_draft(self, id_or_conv_id: str) -> None:
        """Delete a draft by draft_id or fallback to deleting all drafts for a conversation_id."""
        lock = get_conversation_lock(id_or_conv_id)
        with lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("DELETE FROM drafts WHERE draft_id = ?", (id_or_conv_id,))
                if cursor.rowcount == 0:
                    conn.execute("DELETE FROM drafts WHERE conversation_id = ?", (id_or_conv_id,))

    def has_sent_onboarding(self, conversation_id: str) -> bool:
        """Check if onboarding was already sent in this conversation."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT onboarding_sent FROM onboarding_status WHERE conversation_id = ?",
                (conversation_id,)
            )
            row = cursor.fetchone()
            return bool(row and row["onboarding_sent"])

    def mark_onboarding_sent(self, conversation_id: str) -> None:
        """Mark onboarding as sent for this conversation."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO onboarding_status (conversation_id, onboarding_sent)
                    VALUES (?, 1)
                    ON CONFLICT(conversation_id) DO UPDATE SET onboarding_sent = 1
                    """,
                    (conversation_id,)
                )

    def get_low_signal_state(self, conversation_id: str) -> dict[str, Any]:
        """Retrieve low signal state for a specific conversation ID."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT low_signal_count, last_low_signal_at, clarification_topic FROM low_signal_states WHERE conversation_id = ?",
                (conversation_id,)
            )
            row = cursor.fetchone()
            if not row:
                return {"low_signal_count": 0, "last_low_signal_at": None, "clarification_topic": None}
            
            last_at_str = row["last_low_signal_at"]
            if last_at_str:
                try:
                    last_at = datetime.fromisoformat(last_at_str)
                    if (datetime.now(timezone.utc) - last_at).total_seconds() > 3600:
                        conn.execute("DELETE FROM low_signal_states WHERE conversation_id = ?", (conversation_id,))
                        return {"low_signal_count": 0, "last_low_signal_at": None, "clarification_topic": None}
                except Exception:
                    pass
            
            return {
                "low_signal_count": row["low_signal_count"],
                "last_low_signal_at": row["last_low_signal_at"],
                "clarification_topic": row["clarification_topic"]
            }

    def increment_low_signal_count(self, conversation_id: str, topic: str = None) -> int:
        """Increment low signal count and update last timestamp and topic."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            state = self.get_low_signal_state(conversation_id)
            new_count = state["low_signal_count"] + 1
            now_str = datetime.now(timezone.utc).isoformat()
            
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO low_signal_states (conversation_id, low_signal_count, last_low_signal_at, clarification_topic, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        low_signal_count = excluded.low_signal_count,
                        last_low_signal_at = excluded.last_low_signal_at,
                        clarification_topic = excluded.clarification_topic,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (conversation_id, new_count, now_str, topic)
                )
            return new_count

    def reset_low_signal_count(self, conversation_id: str) -> None:
        """Reset/delete low signal count state."""
        lock = get_conversation_lock(conversation_id)
        with lock:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM low_signal_states WHERE conversation_id = ?", (conversation_id,))
