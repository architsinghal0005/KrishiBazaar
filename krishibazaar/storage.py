"""
SQLite Persistence Layer for KrishiBazaar.
Ensures session data and booking records persist across agent invocations.
"""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from krishibazaar.config import settings
from krishibazaar.models import BookingRecord, FarmerInquiry, NetReturnBreakdown


class StorageEngine:
    """Thread-safe SQLite storage engine for local agent persistence."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or settings.database_path)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Establish a connection to the SQLite database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Initialize database tables idempotently."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Farmer session states (conversation memory)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS farmer_sessions (
                    session_id TEXT PRIMARY KEY,
                    farmer_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_inquiry_json TEXT,
                    active_options_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # Immutable booking ledger
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS booking_ledger (
                    booking_id TEXT PRIMARY KEY,
                    farmer_id TEXT NOT NULL,
                    offer_id TEXT NOT NULL,
                    crop TEXT NOT NULL,
                    quantity_quintals REAL NOT NULL,
                    net_payout REAL NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_session(
        self,
        session_id: str,
        farmer_id: str,
        state: str,
        inquiry: FarmerInquiry | None = None,
        active_options: list[NetReturnBreakdown] | None = None,
    ) -> None:
        """Persist or update farmer session state."""
        now = datetime.now(timezone.utc).isoformat()
        inquiry_json = inquiry.model_dump_json() if inquiry else None
        options_json = (
            json.dumps([opt.model_dump() for opt in active_options])
            if active_options
            else None
        )

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO farmer_sessions (session_id, farmer_id, state, current_inquiry_json, active_options_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    farmer_id = excluded.farmer_id,
                    state = excluded.state,
                    current_inquiry_json = COALESCE(excluded.current_inquiry_json, farmer_sessions.current_inquiry_json),
                    active_options_json = COALESCE(excluded.active_options_json, farmer_sessions.active_options_json),
                    updated_at = excluded.updated_at
                """,
                (session_id, farmer_id, state, inquiry_json, options_json, now, now),
            )
            conn.commit()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve farmer session state."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT session_id, farmer_id, state, current_inquiry_json, active_options_json, created_at, updated_at FROM farmer_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            inquiry = (
                FarmerInquiry.model_validate_json(row["current_inquiry_json"])
                if row["current_inquiry_json"]
                else None
            )
            options = None
            if row["active_options_json"]:
                raw_opts = json.loads(row["active_options_json"])
                options = [NetReturnBreakdown.model_validate(opt) for opt in raw_opts]

            return {
                "session_id": row["session_id"],
                "farmer_id": row["farmer_id"],
                "state": row["state"],
                "inquiry": inquiry,
                "active_options": options,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def clear_session(self, session_id: str) -> None:
        """Reset a farmer session."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM farmer_sessions WHERE session_id = ?", (session_id,))
            conn.commit()

    def create_booking(self, booking: BookingRecord) -> bool:
        """Record an approved booking in the immutable ledger."""
        now = datetime.now(timezone.utc).isoformat()
        details_json = booking.model_dump_json()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO booking_ledger (
                    booking_id, farmer_id, offer_id, crop, quantity_quintals,
                    net_payout, status, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    booking.booking_id,
                    booking.farmer_id,
                    booking.offer_id,
                    booking.crop,
                    booking.quantity_quintals,
                    booking.net_payout,
                    booking.status,
                    details_json,
                    now,
                ),
            )
            conn.commit()
            return True

    def get_booking(self, booking_id: str) -> BookingRecord | None:
        """Retrieve a specific booking record."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT details_json FROM booking_ledger WHERE booking_id = ?",
                (booking_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return BookingRecord.model_validate_json(row["details_json"])

    def list_bookings(self, farmer_id: str | None = None) -> list[BookingRecord]:
        """List bookings, optionally filtered by farmer_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if farmer_id:
                cursor.execute(
                    "SELECT details_json FROM booking_ledger WHERE farmer_id = ? ORDER BY created_at DESC",
                    (farmer_id,),
                )
            else:
                cursor.execute(
                    "SELECT details_json FROM booking_ledger ORDER BY created_at DESC"
                )
            rows = cursor.fetchall()
            return [BookingRecord.model_validate_json(r["details_json"]) for r in rows]


# Global storage instance
storage = StorageEngine()
