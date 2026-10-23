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
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    FarmerInquiry,
    NetReturnBreakdown,
    PaymentRecord,
    PaymentStatus,
)


class StorageEngine:
    """Thread-safe SQLite storage engine for local agent persistence."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is not None:
            p = Path(db_path)
            if not p.is_absolute():
                from krishibazaar.config import PROJECT_ROOT
                p = (PROJECT_ROOT / p).resolve()
            self.db_path = str(p)
        else:
            self.db_path = str(Path(settings.database_path).resolve())
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
                    pending_payment_reference TEXT,
                    active_proposal_id TEXT,
                    active_booking_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            try:
                cursor.execute("ALTER TABLE farmer_sessions ADD COLUMN pending_payment_reference TEXT")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE farmer_sessions ADD COLUMN active_proposal_id TEXT")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE farmer_sessions ADD COLUMN active_booking_id TEXT")
            except Exception:
                pass

            # Pending booking proposals (awaiting explicit confirmation)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    farmer_id TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    is_consumed INTEGER NOT NULL DEFAULT 0
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

            # Independent payment ledger (isolated from booking ledger)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_records (
                    payment_reference TEXT PRIMARY KEY,
                    service_inquiry_id TEXT,
                    purpose TEXT NOT NULL,
                    payer_agent_address TEXT NOT NULL,
                    payer_wallet_address TEXT NOT NULL,
                    payee_agent_address TEXT NOT NULL,
                    payee_wallet_address TEXT NOT NULL,
                    amount_base_units INTEGER NOT NULL,
                    amount_display TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    network TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    tx_height INTEGER,
                    actual_fee_base_units INTEGER,
                    actual_fee_display TEXT,
                    deadline_seconds INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    verified_at_utc TEXT,
                    rejection_reason TEXT,
                    cancellation_reason TEXT,
                    metadata_json TEXT
                )
                """
            )
            try:
                cursor.execute("ALTER TABLE payment_records ADD COLUMN actual_fee_base_units INTEGER")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE payment_records ADD COLUMN actual_fee_display TEXT")
            except Exception:
                pass
            conn.commit()

    @staticmethod
    def _row_to_payment_record(row: sqlite3.Row) -> PaymentRecord:
        """Convert a database row to a PaymentRecord model instance."""
        row_keys = row.keys()
        return PaymentRecord(
            payment_reference=row["payment_reference"],
            service_inquiry_id=row["service_inquiry_id"],
            purpose=row["purpose"],
            payer_agent_address=row["payer_agent_address"],
            payer_wallet_address=row["payer_wallet_address"],
            payee_agent_address=row["payee_agent_address"],
            payee_wallet_address=row["payee_wallet_address"],
            amount_base_units=row["amount_base_units"],
            amount_display=row["amount_display"],
            currency=row["currency"],
            network=row["network"],
            status=PaymentStatus(row["status"]),
            tx_hash=row["tx_hash"],
            tx_height=row["tx_height"],
            actual_fee_base_units=row["actual_fee_base_units"] if "actual_fee_base_units" in row_keys else None,
            actual_fee_display=row["actual_fee_display"] if "actual_fee_display" in row_keys else None,
            deadline_seconds=row["deadline_seconds"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
            verified_at_utc=row["verified_at_utc"],
            rejection_reason=row["rejection_reason"],
            cancellation_reason=row["cancellation_reason"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def save_session(
        self,
        session_id: str,
        farmer_id: str,
        state: str,
        inquiry: FarmerInquiry | None = None,
        active_options: list[NetReturnBreakdown] | None = None,
        pending_payment_reference: str | None = None,
        active_proposal_id: str | None = None,
        active_booking_id: str | None = None,
        clear_pending_payment: bool = False,
    ) -> None:
        """Persist or update farmer session state."""
        now = datetime.now(timezone.utc).isoformat()
        inquiry_json = inquiry.model_dump_json() if inquiry else None
        options_json = (
            json.dumps([opt.model_dump() for opt in active_options])
            if active_options
            else None
        )
        if pending_payment_reference == "":
            clear_pending_payment = True
            pending_payment_reference = None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO farmer_sessions (session_id, farmer_id, state, current_inquiry_json, active_options_json, pending_payment_reference, active_proposal_id, active_booking_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    farmer_id = excluded.farmer_id,
                    state = excluded.state,
                    current_inquiry_json = COALESCE(excluded.current_inquiry_json, farmer_sessions.current_inquiry_json),
                    active_options_json = COALESCE(excluded.active_options_json, farmer_sessions.active_options_json),
                    pending_payment_reference = CASE
                        WHEN ? THEN NULL
                        ELSE COALESCE(excluded.pending_payment_reference, farmer_sessions.pending_payment_reference)
                    END,
                    active_proposal_id = COALESCE(excluded.active_proposal_id, farmer_sessions.active_proposal_id),
                    active_booking_id = COALESCE(excluded.active_booking_id, farmer_sessions.active_booking_id),
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    farmer_id,
                    state,
                    inquiry_json,
                    options_json,
                    pending_payment_reference,
                    active_proposal_id,
                    active_booking_id,
                    now,
                    now,
                    1 if clear_pending_payment else 0,
                ),
            )
            conn.commit()

    def clear_session_payment_reference(self, session_id: str) -> bool:
        """Explicitly clear pending_payment_reference in farmer_sessions for session_id or farmer_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE farmer_sessions SET pending_payment_reference = NULL, updated_at = ? WHERE session_id = ? OR farmer_id = ?",
                (datetime.now(timezone.utc).isoformat(), session_id, session_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve farmer session state by session_id or farmer_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT session_id, farmer_id, state, current_inquiry_json, active_options_json, pending_payment_reference, active_proposal_id, active_booking_id, created_at, updated_at
                FROM farmer_sessions
                WHERE session_id = ? OR farmer_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (session_id, session_id),
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
                "pending_payment_reference": row["pending_payment_reference"] if "pending_payment_reference" in row.keys() else None,
                "active_proposal_id": row["active_proposal_id"] if "active_proposal_id" in row.keys() else None,
                "active_booking_id": row["active_booking_id"] if "active_booking_id" in row.keys() else None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def get_pending_payment_for_payer(self, payer_agent_address: str) -> PaymentRecord | None:
        """Retrieve active pending payment record for a given payer agent or session."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM payment_records
                WHERE (payer_agent_address = ? OR service_inquiry_id = ?)
                  AND status IN ('REQUESTED', 'AWAITING_AUTHORIZATION')
                ORDER BY created_at_utc DESC LIMIT 1
                """,
                (payer_agent_address, payer_agent_address),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_payment_record(row)

    def clear_session(self, session_id: str) -> None:
        """Reset a farmer session by session_id or farmer_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM farmer_sessions WHERE session_id = ? OR farmer_id = ?", (session_id, session_id))
            conn.commit()

    def save_proposal(self, proposal: BookingProposal) -> None:
        """Persist a pending booking proposal, superseding previous unconsumed proposals for the same session or farmer."""
        proposal_json = proposal.model_dump_json()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Mark previous unconsumed proposals for this session/farmer as superseded
            cursor.execute(
                "UPDATE pending_proposals SET is_consumed = 1 WHERE (session_id = ? OR farmer_id = ?) AND is_consumed = 0",
                (proposal.session_id, proposal.farmer_id),
            )
            # Insert or replace this proposal
            cursor.execute(
                """
                INSERT INTO pending_proposals (
                    proposal_id, session_id, farmer_id, proposal_json,
                    created_at, expires_at, is_consumed
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    proposal_json = excluded.proposal_json,
                    expires_at = excluded.expires_at,
                    is_consumed = excluded.is_consumed
                """,
                (
                    proposal.proposal_id,
                    proposal.session_id,
                    proposal.farmer_id,
                    proposal_json,
                    proposal.created_at_utc,
                    proposal.expires_at_utc,
                    1 if proposal.is_consumed else 0,
                ),
            )
            conn.commit()

    def get_proposal(self, proposal_id: str) -> BookingProposal | None:
        """Retrieve a specific proposal by ID (case-insensitive)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT proposal_json, is_consumed FROM pending_proposals WHERE UPPER(proposal_id) = UPPER(?)",
                (proposal_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            prop = BookingProposal.model_validate_json(row["proposal_json"])
            prop.is_consumed = bool(row["is_consumed"])
            return prop

    def get_pending_proposal_for_session(self, session_id: str) -> BookingProposal | None:
        """Retrieve the currently active unconsumed proposal for a session or farmer."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT proposal_json, is_consumed FROM pending_proposals
                WHERE (session_id = ? OR farmer_id = ?) AND is_consumed = 0
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id, session_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            prop = BookingProposal.model_validate_json(row["proposal_json"])
            prop.is_consumed = bool(row["is_consumed"])
            return prop

    def consume_proposal(self, proposal_id: str) -> bool:
        """
        Atomically mark a proposal as consumed.
        Returns True if the proposal was successfully transitioned from unconsumed (0) to consumed (1).
        Returns False if the proposal does not exist or was already consumed (idempotency guard).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pending_proposals SET is_consumed = 1 WHERE proposal_id = ? AND is_consumed = 0",
                (proposal_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def cancel_pending_proposals(self, session_id: str) -> int:
        """Cancel and mark all pending proposals for a session or farmer as consumed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pending_proposals SET is_consumed = 1 WHERE (session_id = ? OR farmer_id = ?) AND is_consumed = 0",
                (session_id, session_id),
            )
            conn.commit()
            return cursor.rowcount

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
        """Retrieve a specific booking record (case-insensitive)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT details_json FROM booking_ledger WHERE UPPER(booking_id) = UPPER(?)",
                (booking_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return BookingRecord.model_validate_json(row["details_json"])

    def get_booking_by_proposal(self, proposal_id: str) -> BookingRecord | None:
        """Retrieve a booking record associated with a proposal ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT details_json FROM booking_ledger
                WHERE UPPER(offer_id) = UPPER(?) OR details_json LIKE ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (proposal_id, f"%{proposal_id}%"),
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

    def save_payment_record(self, record: PaymentRecord) -> bool:
        """Insert or replace a payment record in the payment ledger."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO payment_records (
                    payment_reference, service_inquiry_id, purpose,
                    payer_agent_address, payer_wallet_address,
                    payee_agent_address, payee_wallet_address,
                    amount_base_units, amount_display, currency, network,
                    status, tx_hash, tx_height, actual_fee_base_units, actual_fee_display,
                    deadline_seconds, created_at_utc, updated_at_utc, verified_at_utc,
                    rejection_reason, cancellation_reason, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.payment_reference,
                    record.service_inquiry_id,
                    record.purpose,
                    record.payer_agent_address,
                    record.payer_wallet_address,
                    record.payee_agent_address,
                    record.payee_wallet_address,
                    record.amount_base_units,
                    record.amount_display,
                    record.currency,
                    record.network,
                    record.status.value if isinstance(record.status, PaymentStatus) else str(record.status),
                    record.tx_hash,
                    record.tx_height,
                    record.actual_fee_base_units,
                    record.actual_fee_display,
                    record.deadline_seconds,
                    record.created_at_utc,
                    record.updated_at_utc,
                    record.verified_at_utc,
                    record.rejection_reason,
                    record.cancellation_reason,
                    json.dumps(record.metadata),
                ),
            )
            conn.commit()
            return True

    def get_payment_record(self, reference: str) -> PaymentRecord | None:
        """Retrieve a specific payment record by its unique reference (case-insensitive)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM payment_records WHERE UPPER(payment_reference) = UPPER(?)",
                (reference,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_payment_record(row)

    def update_payment_status(
        self,
        reference: str,
        status: PaymentStatus,
        tx_hash: str | None = None,
        tx_height: int | None = None,
        actual_fee_base_units: int | None = None,
        actual_fee_display: str | None = None,
        rejection_reason: str | None = None,
        cancellation_reason: str | None = None,
        verified_at_utc: str | None = None,
    ) -> bool:
        """Update payment lifecycle status and transaction metadata."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE payment_records
                SET status = ?,
                    tx_hash = COALESCE(?, tx_hash),
                    tx_height = COALESCE(?, tx_height),
                    actual_fee_base_units = COALESCE(?, actual_fee_base_units),
                    actual_fee_display = COALESCE(?, actual_fee_display),
                    rejection_reason = COALESCE(?, rejection_reason),
                    cancellation_reason = COALESCE(?, cancellation_reason),
                    verified_at_utc = COALESCE(?, verified_at_utc),
                    updated_at_utc = ?
                WHERE UPPER(payment_reference) = UPPER(?)
                """,
                (
                    status.value if isinstance(status, PaymentStatus) else str(status),
                    tx_hash,
                    tx_height,
                    actual_fee_base_units,
                    actual_fee_display,
                    rejection_reason,
                    cancellation_reason,
                    verified_at_utc,
                    now,
                    reference,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def transition_payment_status_atomic(
        self,
        reference: str,
        from_statuses: list[PaymentStatus | str],
        to_status: PaymentStatus | str,
        tx_hash: str | None = None,
        tx_height: int | None = None,
        actual_fee_base_units: int | None = None,
        actual_fee_display: str | None = None,
        verified_at_utc: str | None = None,
        rejection_reason: str | None = None,
    ) -> bool:
        """
        Atomically transition payment status only if current status is in `from_statuses`.
        Provides strong idempotency / concurrency locking before broadcasting transactions.
        Returns True if the record was successfully transitioned.
        Returns False if the record does not exist or was already in another status (e.g. SUBMITTING, COMMITTED, SETTLED).
        """
        now = datetime.now(timezone.utc).isoformat()
        from_vals = [s.value if isinstance(s, PaymentStatus) else str(s) for s in from_statuses]
        placeholders = ",".join("?" for _ in from_vals)
        to_val = to_status.value if isinstance(to_status, PaymentStatus) else str(to_status)

        query = f"""
            UPDATE payment_records
            SET status = ?,
                tx_hash = COALESCE(?, tx_hash),
                tx_height = COALESCE(?, tx_height),
                actual_fee_base_units = COALESCE(?, actual_fee_base_units),
                actual_fee_display = COALESCE(?, actual_fee_display),
                verified_at_utc = COALESCE(?, verified_at_utc),
                rejection_reason = COALESCE(?, rejection_reason),
                updated_at_utc = ?
            WHERE UPPER(payment_reference) = UPPER(?) AND status IN ({placeholders})
        """
        params = [
            to_val,
            tx_hash,
            tx_height,
            actual_fee_base_units,
            actual_fee_display,
            verified_at_utc,
            rejection_reason,
            now,
            reference,
        ] + from_vals

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0

    def list_payment_records(self, limit: int = 50) -> list[PaymentRecord]:
        """List recent payment records ordered by creation date."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM payment_records ORDER BY created_at_utc DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            return [self._row_to_payment_record(row) for row in rows]

    def get_payment_record_by_inquiry(
        self, inquiry_id: str, purpose: str | None = None
    ) -> PaymentRecord | None:
        """
        Retrieve the payment record associated with a specific booking or inquiry reference (case-insensitive),
        optionally filtered by service purpose.
        Prioritizes SETTLED and active/in-progress records over failed or cancelled ones.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if purpose:
                cursor.execute(
                    """
                    SELECT * FROM payment_records 
                    WHERE UPPER(service_inquiry_id) = UPPER(?) AND purpose = ?
                    ORDER BY 
                        CASE 
                            WHEN status = 'SETTLED' THEN 1
                            WHEN status IN ('AWAITING_RECONCILIATION', 'PENDING_SETTLEMENT', 'COMMITTED', 'SUBMITTING') THEN 2
                            WHEN status IN ('AWAITING_AUTHORIZATION', 'REQUESTED') THEN 3
                            ELSE 4
                        END,
                        created_at_utc DESC 
                    LIMIT 1
                    """,
                    (inquiry_id, purpose),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM payment_records 
                    WHERE UPPER(service_inquiry_id) = UPPER(?)
                    ORDER BY 
                        CASE 
                            WHEN status = 'SETTLED' THEN 1
                            WHEN status IN ('AWAITING_RECONCILIATION', 'PENDING_SETTLEMENT', 'COMMITTED', 'SUBMITTING') THEN 2
                            WHEN status IN ('AWAITING_AUTHORIZATION', 'REQUESTED') THEN 3
                            ELSE 4
                        END,
                        created_at_utc DESC 
                    LIMIT 1
                    """,
                    (inquiry_id,),
                )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_payment_record(row)


# Global storage instance
storage = StorageEngine()
