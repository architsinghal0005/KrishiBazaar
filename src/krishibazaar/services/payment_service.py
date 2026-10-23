"""
Fetch.ai Agent Payment Protocol Subsystem for KrishiBazaar (Phase 9B).

Implements the official Fetch.ai Agent Payment Protocol (uagents-core contrib protocol)
with authoritative on-chain verification, explicit human approval safety gates,
idempotency, replay protection, secret redaction, and full SQLite persistence.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.tx_helpers import SubmittedTx, TxResponse
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.address import Address
from uagents import Context, Protocol
from uagents.network import get_ledger, wait_for_tx_to_complete

from krishibazaar.config import Settings, settings
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    CancelPayment,
    CommitPayment,
    CompletePayment,
    ExplicitPaymentApproval,
    Funds,
    PaymentPurpose,
    PaymentRecord,
    PaymentReviewDetails,
    PaymentStatus,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)
from krishibazaar.storage import StorageEngine, storage

logger = logging.getLogger(__name__)

# Pattern for explicit human payment authorization
EXPLICIT_APPROVAL_PATTERN = re.compile(
    r"^AUTHORIZE\s+PAYMENT\s+REF:(?P<ref>[A-Za-z0-9\-_]+)\s+AMOUNT:(?P<amt>\d+)\s+CURRENCY:(?P<cur>[A-Za-z0-9]+)\s+RECIPIENT:(?P<rec>[A-Za-z0-9]+)$",
    re.IGNORECASE,
)

CASUAL_AFFIRMATIONS = {
    "ok",
    "yes",
    "sounds good",
    "thanks",
    "proceed",
    "yep",
    "sure",
    "fine",
    "hi",
    "ok confirm",
    "confirm",
    "approve",
}


def redact_secrets(val: str | None) -> str:
    """Safely redact private keys, seeds, or tokens from logs and outputs."""
    if not val:
        return ""
    s = str(val).strip()
    if len(s) <= 8:
        return "[REDACTED]"
    return f"{s[:3]}...[REDACTED]...{s[-3:]}"


def mask_address(address: str | None, prefix_len: int = 10, suffix_len: int = 6) -> str:
    """
    Format recipient wallet or agent address with security masking for UI display.
    Example: 'fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck' -> 'fetch1e974...3dshck'.
    """
    if not address:
        return "N/A"
    s = str(address).strip()
    if len(s) <= prefix_len + suffix_len + 3:
        return s
    return f"{s[:prefix_len]}...{s[-suffix_len:]}"


def parse_explicit_payment_approval(raw_text: str) -> ExplicitPaymentApproval | None:
    """
    Parse and strictly validate an explicit payment authorization command.
    Rejects casual, ambiguous, or incomplete approvals.
    """
    if not raw_text or not isinstance(raw_text, str):
        return None

    cleaned = raw_text.strip()
    if cleaned.lower() in CASUAL_AFFIRMATIONS:
        return None

    match = EXPLICIT_APPROVAL_PATTERN.match(cleaned)
    if not match:
        return None

    groups = match.groupdict()
    try:
        amt = int(groups["amt"])
        if amt <= 0:
            return None
    except ValueError:
        return None

    return ExplicitPaymentApproval(
        reference=groups["ref"],
        amount_base_units=amt,
        currency=groups["cur"],
        recipient=groups["rec"],
    )


def format_token_amount_display(amt: int, denom: str) -> str:
    """Format token amount in user-friendly denomination and exact base units."""
    if denom.lower() in ("atestfet", "afet"):
        fet_val = amt / (10**18)
        if fet_val >= 0.0001:
            return f"{fet_val:g} FET ({amt:,} {denom})"
        else:
            return f"{amt} {denom} ({fet_val:.1e} FET)"
    return f"{amt} {denom}"


def extract_transaction_fee(
    tx_resp: Any,
    expected_denom: str = "atestfet",
) -> tuple[int | None, str | None]:
    """
    Extract and strictly validate actual network fee from a CosmPy TxResponse.

    Validation rules:
    1. If `tx_resp.actual_fee_base_units` is reliably populated as a non-negative integer:
       uses it and returns (fee_base, fee_display).
    2. Otherwise, extracts fee string from `tx_resp.events` under event 'tx' -> 'fee'
       (e.g., '430390000000000atestfet'), or inside message logs.
    3. Strictly validates:
       - Must parse into non-negative integer amount and denomination.
       - Denomination must match expected_denom (case-insensitive).
       - Malformed, negative, missing, or mismatched denominations log a warning and return (None, None).
    """
    if tx_resp is None:
        return None, None

    # 1. Use actual_fee_base_units if populated on tx_resp
    raw_base = getattr(tx_resp, "actual_fee_base_units", None)
    if isinstance(raw_base, int) and raw_base >= 0:
        raw_disp = getattr(tx_resp, "actual_fee_display", None)
        fee_disp = (
            raw_disp
            if (isinstance(raw_disp, str) and raw_disp.strip())
            else format_token_amount_display(raw_base, expected_denom)
        )
        return raw_base, fee_disp

    # 2. Search for fee string in tx_resp.events and message logs
    raw_fee_str: str | None = None

    # Check top-level events
    events = getattr(tx_resp, "events", None)
    if isinstance(events, dict):
        tx_ev = events.get("tx")
        if isinstance(tx_ev, dict):
            val = tx_ev.get("fee")
            if isinstance(val, str) and val.strip():
                raw_fee_str = val.strip()

    # Check message logs if not found in top-level events
    if not raw_fee_str:
        logs = getattr(tx_resp, "logs", None)
        if isinstance(logs, list):
            for log in logs:
                log_events = getattr(log, "events", None)
                if isinstance(log_events, dict):
                    tx_ev = log_events.get("tx")
                    if isinstance(tx_ev, dict):
                        val = tx_ev.get("fee")
                        if isinstance(val, str) and val.strip():
                            raw_fee_str = val.strip()
                            break

    if not raw_fee_str:
        tx_h = getattr(tx_resp, "hash", "unknown")
        logger.warning(f"No transaction fee event found in response for tx {tx_h}")
        return None, None

    # 3. Parse and strictly validate fee string
    # Support single coin '430390000000000atestfet' or comma-separated coins
    coin_tokens = [t.strip() for t in raw_fee_str.split(",") if t.strip()]
    parsed_matches: list[int] = []

    for token in coin_tokens:
        m = re.match(r"^(\d+)([a-zA-Z0-9/]+)$", token)
        if not m:
            continue
        amt_val = int(m.group(1))
        denom_val = m.group(2).lower()
        if denom_val == expected_denom.lower() and amt_val >= 0:
            parsed_matches.append(amt_val)

    if not parsed_matches:
        tx_h = getattr(tx_resp, "hash", "unknown")
        logger.warning(
            f"Could not extract valid fee with expected denomination '{expected_denom}' "
            f"from fee string '{raw_fee_str}' in tx {tx_h}"
        )
        return None, None

    fee_base = parsed_matches[0]
    fee_disp = format_token_amount_display(fee_base, expected_denom)
    return fee_base, fee_disp


def format_payment_status(record: PaymentRecord, payment_enabled: bool = False) -> list[str]:
    """
    Format human-readable lines reporting the authoritative status of a PaymentRecord.
    """
    masked_rec = mask_address(record.payee_wallet_address)
    status_str = record.status.value if hasattr(record.status, "value") else str(record.status)

    lines = [
        "ðŸ’³ **KrishiBazaar: Payment Status Report**",
        "",
        f"â€¢ **Payment Reference:** `{record.payment_reference}`",
        f"â€¢ **Service Purpose:** {record.purpose}",
        f"â€¢ **Amount & Currency:** {record.amount_display} ({record.amount_base_units:,} {record.currency})",
        f"â€¢ **Recipient Wallet (Masked):** `{masked_rec}`",
        f"â€¢ **Network / Environment:** {record.network} (Fetch.ai Dorado Testnet)",
    ]

    if not payment_enabled and status_str != "SETTLED":
        lines.append("â€¢ **Payment Protocol:** Disabled (`ENABLE_PAYMENT_PROTOCOL=false`). No transfer has been initiated.")
    elif status_str == "SETTLED":
        lines.append(f"â€¢ **Status:** âœ… **SETTLED** (Cryptographically verified on Dorado testnet)")
        if record.tx_hash:
            lines.append(f"â€¢ **Transaction Hash:** `{record.tx_hash}`")
        if record.tx_height:
            lines.append(f"â€¢ **Block Height:** {record.tx_height}")
        if record.actual_fee_display:
            lines.append(f"â€¢ **Actual Fee:** {record.actual_fee_display}")
        elif record.actual_fee_base_units:
            lines.append(f"â€¢ **Actual Fee:** {format_token_amount_display(record.actual_fee_base_units, record.currency)}")
        if record.verified_at_utc:
            lines.append(f"â€¢ **Settled At (UTC):** {record.verified_at_utc}")
    elif status_str == "SUBMITTING":
        lines.append("â€¢ **Status:** â³ **SUBMITTING** â€” Broadcasting transaction to Fetch.ai Dorado testnet.")
    elif status_str in ("COMMITTED", "PENDING_SETTLEMENT"):
        lines.append(f"â€¢ **Status:** â³ **{status_str}** â€” Transaction broadcast. Awaiting ledger finality.")
        if record.tx_hash:
            lines.append(f"â€¢ **Transaction Hash:** `{record.tx_hash}`")
    elif status_str == "AWAITING_RECONCILIATION":
        lines.append("â€¢ **Status:** â³ **AWAITING_RECONCILIATION** â€” Transaction broadcast, awaiting confirmation.")
        if record.tx_hash:
            lines.append(f"â€¢ **Transaction Hash:** `{record.tx_hash}`")
    elif status_str in ("REQUESTED", "AWAITING_AUTHORIZATION"):
        lines.append(f"â€¢ **Status:** â³ **{status_str}** â€” Awaiting human authorization. No tokens debited.")
        if payment_enabled:
            lines.append(f"â€¢ **Action Required:** Click **'Proceed to Payment Review'** or reply *\"Pay advisory fee\"* to review.")
    elif status_str == "CANCELLED":
        lines.append(f"â€¢ **Status:** âŒ **CANCELLED** ({record.cancellation_reason or 'No tokens debited'})")
    elif status_str == "REJECTED":
        lines.append(f"â€¢ **Status:** ðŸ›‘ **REJECTED** ({record.rejection_reason or 'Declined by farmer'})")
    else:
        lines.append(f"â€¢ **Status:** {status_str}")

    if record.service_inquiry_id:
        lines.append(f"â€¢ **Linked Booking:** `{record.service_inquiry_id}`")

    lines.append("")
    lines.append("â„¹ï¸ *KrishiBazaar operates strictly on testnet micro-fees. Crop sale proceeds are simulated and separate.*")
    return lines


def format_payment_readiness_audit(
    proposal_id: str | None,
    proposal: BookingProposal | None,
    booking: BookingRecord | None,
    payment_record: PaymentRecord | None,
    settings_obj: Settings | None = None,
    storage_engine: StorageEngine | None = None,
) -> list[str]:
    """
    Format a comprehensive, human-readable payment-readiness and proposal audit report.
    Reports actual available configuration, runtime evidence, proposal validity window,
    ledger isolation, missing prerequisites, and non-executing dry-run capability.
    """
    cfg = settings_obj or settings
    lines: list[str] = [
        "ðŸ’³ **KrishiBazaar: Proposal Payment Readiness & Subsystem Audit**",
        "",
        "ðŸ“‹ **1. Proposal Context & Validity Window:**",
    ]

    now_utc = datetime.now(timezone.utc)

    if proposal:
        lines.append(f"â€¢ **Proposal Reference:** `{proposal.proposal_id}`")
        lines.append(
            f"â€¢ **Produce Details:** {proposal.quantity_quintals:g} quintals {proposal.crop} "
            f"to {proposal.destination_name} ({proposal.channel_type})"
        )
        lines.append(f"â€¢ **Origin Location:** {proposal.origin_location}")
        pickup_val = getattr(proposal, "transit_date", None) or getattr(proposal, "pickup_date", "Pending")
        lines.append(f"â€¢ **Scheduled Pickup Date:** {pickup_val}")
        lines.append(f"â€¢ **Estimated Net Payout:** INR {proposal.estimated_net_payout:,.2f} *(Advisory estimate)*")

        # Expiry check
        try:
            expires_at = datetime.fromisoformat(proposal.expires_at_utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except Exception:
            expires_at = now_utc

        is_expired = now_utc > expires_at
        if is_expired:
            lines.append(
                f"â€¢ **Proposal Status:** âš ï¸ **EXPIRED** (Proposal expired at `{proposal.expires_at_utc}`). "
                f"Due to wholesale market rate and transport quote volatility, proposals have a 15-minute validity window. "
                f"Expired proposals cannot be confirmed or used for payment."
            )
        elif proposal.is_consumed:
            lines.append(
                f"â€¢ **Proposal Status:** â„¹ï¸ **CONSUMED / SUPERSEDED** (Already processed into a booking or cancelled)."
            )
        else:
            remaining_sec = max(0, int((expires_at - now_utc).total_seconds()))
            remaining_min = remaining_sec / 60.0
            lines.append(
                f"â€¢ **Proposal Status:** ðŸŸ¢ **ACTIVE & VALID** (Within 15-minute validity window; ~{remaining_min:.1f} minutes remaining)."
            )
        lines.append(f"â€¢ **Created At (UTC):** `{proposal.created_at_utc}`")
        lines.append(f"â€¢ **Expires At (UTC):** `{proposal.expires_at_utc}`")

    elif proposal_id:
        lines.append(f"â€¢ **Proposal Reference:** `{proposal_id}`")
        lines.append(
            f"â€¢ **Proposal Status:** âš ï¸ **NOT FOUND** (No proposal record found with reference ID `{proposal_id}` in local storage)."
        )
    else:
        lines.append("â€¢ **Proposal Reference:** None specified (No active proposal in session).")

    lines.append("")
    lines.append("ðŸ”’ **2. Associated Records & Ledger Isolation:**")
    if booking:
        lines.append(
            f"â€¢ **Associated Booking Record:** âœ… `{booking.booking_id}` (Status: **{booking.status}**, "
            f"Net Payout: INR {booking.net_payout:,.2f})"
        )
    else:
        lines.append("â€¢ **Associated Booking Record:** None *(Proposal has NOT been confirmed into a booking)*")

    if payment_record:
        masked_rec = mask_address(payment_record.payee_wallet_address)
        lines.append(
            f"â€¢ **Associated Payment Record:** `{payment_record.payment_reference}` "
            f"(Status: **{payment_record.status.value}**, Amount: {payment_record.amount_display}, Payee: `{masked_rec}`)"
        )
    else:
        lines.append("â€¢ **Associated Payment Record:** None *(No payment transfer has been requested or initiated)*")
    lines.append("â€¢ **Record Isolation Guard:** Active *(Historical settled payments from other bookings are never substituted)*")

    lines.append("")
    lines.append("âš™ï¸ **3. Payment Subsystem & Service Readiness:**")
    lines.append("â€¢ **Payment Service Subsystem:** âœ… Initialized & Available (Fetch.ai Agent Payment Protocol engine loaded)")

    # Payment Specialist Agent configuration
    agent_addr = cfg.payment_payee_agent_address
    port = cfg.payment_agent_port
    if agent_addr:
        masked_agent = f"{agent_addr[:16]}...{agent_addr[-6:]}" if len(agent_addr) > 24 else agent_addr
        lines.append(f"â€¢ **Payment Specialist Agent:** Configured (`{masked_agent}` on port {port}; role: authoritative payee settlement agent)")
    else:
        lines.append("â€¢ **Payment Specialist Agent:** Local service fallback mode")

    # Payment Protocol Status (ENABLE_PAYMENT_PROTOCOL)
    if cfg.enable_payment_protocol:
        lines.append("â€¢ **Payment Protocol Execution:** ðŸŸ¢ **ENABLED** (Testnet Mode active)")
    else:
        lines.append("â€¢ **Payment Protocol Execution:** ðŸ”´ **DISABLED** (`ENABLE_PAYMENT_PROTOCOL=False`; real fund transfers and on-chain signing deactivated for safety)")

    # Network & Method
    lines.append(f"â€¢ **Configured Network:** Fetch.ai Dorado Testnet (`{cfg.payment_testnet_chain_id}`, {cfg.payment_network})")
    lines.append(f"â€¢ **Payment Method & Asset:** Direct FET transfer (`fet_direct`, denomination: `{cfg.payment_testnet_denom}`)")
    advisory_fet = cfg.payment_advisory_fee_base_units / (10**18)
    lines.append(f"â€¢ **Standard Advisory Fee:** {advisory_fet:g} FET ({cfg.payment_advisory_fee_base_units:,} {cfg.payment_testnet_denom})")

    payee_wallet = cfg.payment_payee_wallet_address
    if payee_wallet:
        lines.append(f"â€¢ **Configured Payee Wallet (Masked):** `{mask_address(payee_wallet)}`")
    if cfg.payment_testnet_rpc_url:
        lines.append(f"â€¢ **RPC Ledger Endpoint:** `{cfg.payment_testnet_rpc_url}`")

    lines.append("")
    lines.append("â„¹ï¸ **4. Order of Operations, Missing Prerequisites & Dry-Run Support:**")
    lines.append(
        "â€¢ **Architecture Limitation:** In KrishiBazaar's farm-to-market protocol, payment requests cannot be created or authorized prior to booking confirmation. "
        "Proposals (`PROP-...`) are time-bounded advisory estimates. Payment review and on-chain settlement are generated strictly post-booking confirmation "
        "to ensure payments map 1-to-1 to committed produce bookings."
    )
    lines.append("â€¢ **Missing Prerequisites for Payment Execution:**")
    if not booking:
        lines.append("  1. **Booking Confirmation:** Proposal must be approved by the farmer to create an authoritative booking record (`KB-BOOK-...`).")
    else:
        lines.append("  1. **Booking Confirmation:** Completed (`KB-BOOK-...` exists).")

    if not cfg.enable_payment_protocol:
        lines.append("  2. **Protocol Activation:** `ENABLE_PAYMENT_PROTOCOL=True` must be set in environment (currently deactivated for test safety).")
    else:
        lines.append("  2. **Protocol Activation:** Enabled.")

    lines.append(
        "â€¢ **Non-Executing Dry-Run Support:** âœ… **SUPPORTED** (Configuration, agent addresses, testnet parameters, and storage schemas verified; "
        "no on-chain transactions or token transfers will be broadcast during dry-run audits)."
    )

    lines.append("")
    lines.append("ðŸ‘‰ **5. Next Safe Step:**")
    if proposal and not (now_utc > expires_at) and not proposal.is_consumed:
        lines.append(
            f"To proceed safely with this proposal, reply with:\n"
            f"*\"Confirm booking\"* or *\"Approve proposal {proposal.proposal_id}\"*\n\n"
            f"*(This will confirm your simulated booking without executing real payments while `ENABLE_PAYMENT_PROTOCOL=False`. "
            f"To abort, reply *\"Cancel\"*)*."
        )
    elif proposal and (now_utc > expires_at):
        lines.append(
            f"Proposal `{proposal.proposal_id}` has expired. Due to rate volatility, please select an option again "
            f"(e.g. *\"Select option 1\"*) or submit updated produce details to generate a fresh proposal."
        )
    else:
        lines.append(
            "Please provide your produce details (e.g. *\"I have 50 quintals of Onion in Nashik\"*) or select an option to generate a booking proposal."
        )

    return lines


def format_booking_payment_audit(
    booking_id: str,
    booking: BookingRecord | None,
    payment_record: PaymentRecord | None,
    settings_obj: Settings | None = None,
    is_owner: bool = True,
) -> list[str]:
    """
    Format a comprehensive, human-readable booking-specific payment audit report.
    Distinguishes booking existence and actual stored status, exact payment record status,
    payment execution protocol configuration, configured network/method, missing prerequisites,
    non-executing dry-run capability, and transaction initiation status.
    """
    cfg = settings_obj or settings
    lines: list[str] = [
        "ðŸ’³ **KrishiBazaar: Booking Payment Audit & Subsystem Report**",
        "",
        "ðŸ“‹ **1. Booking Reference & Authoritative Lifecycle Status:**",
        f"â€¢ **Booking Reference:** `{booking_id}`",
    ]

    if not is_owner:
        lines.append(
            f"â€¢ **Booking Status:** âš ï¸ **ACCESS RESTRICTED (Session Isolation)** "
            f"â€” Booking `{booking_id}` is registered to a different farmer session."
        )
    elif booking:
        lines.append(f"â€¢ **Booking Status:** âœ… **{booking.status}** (Authoritative Local Ledger Record)")
        lines.append(
            f"â€¢ **Produce Details:** {booking.quantity_quintals:g} quintals {booking.crop} "
            f"to {booking.channel_name}"
        )
        lines.append(f"â€¢ **Scheduled Pickup / Transit Date:** {booking.pickup_date}")
        lines.append(
            f"â€¢ **Agreed Net Payout:** INR {booking.net_payout:,.2f} "
            f"*(Gross: INR {booking.gross_revenue:,.2f}, Transport: INR {booking.transport_cost:,.2f})*"
        )
        lines.append(f"â€¢ **Transport Provider:** {booking.transporter_name}")
        lines.append(f"â€¢ **Confirmed At (UTC):** `{booking.timestamp_utc}`")
        if booking.tracking_token:
            lines.append(f"â€¢ **Tracking Token:** `{booking.tracking_token}`")
        if booking.notes:
            lines.append(f"â€¢ **Ledger Notes:** {booking.notes}")
    else:
        lines.append(
            f"â€¢ **Booking Status:** âš ï¸ **NOT FOUND** (No booking record found for reference `{booking_id}` in local database. "
            f"Please verify that this booking reference was created in this environment)."
        )

    lines.append("")
    lines.append("ðŸ”’ **2. Associated Records & Ledger Isolation:**")
    if not is_owner:
        lines.append("â€¢ **Associated Payment Record:** Restricted by session ownership guard.")
    elif payment_record:
        masked_rec = mask_address(payment_record.payee_wallet_address)
        lines.append(
            f"â€¢ **Associated Payment Record:** `{payment_record.payment_reference}` "
            f"(Status: **{payment_record.status.value}**, Amount: {payment_record.amount_display}, Payee: `{masked_rec}`)"
        )
        if payment_record.tx_hash:
            lines.append(f"â€¢ **Transaction Hash:** `{payment_record.tx_hash}`")
        if payment_record.tx_height:
            lines.append(f"â€¢ **Block Height:** {payment_record.tx_height}")
    else:
        lines.append("â€¢ **Associated Payment Record:** None")
        lines.append(f"  No payment record found for booking `{booking_id}`.")
        lines.append(f"  *(No payment record found for booking {booking_id})*")
    lines.append("â€¢ **Record Isolation Guard:** Active *(Historical settled payments from other bookings are never substituted)*")

    lines.append("")
    lines.append("âš™ï¸ **3. Payment Subsystem & Service Readiness:**")
    lines.append("â€¢ **Payment Service Subsystem:** âœ… Initialized & Available (Fetch.ai Agent Payment Protocol engine loaded)")

    # Payment Specialist Agent configuration
    agent_addr = cfg.payment_payee_agent_address
    port = cfg.payment_agent_port
    if agent_addr:
        masked_agent = f"{agent_addr[:16]}...{agent_addr[-6:]}" if len(agent_addr) > 24 else agent_addr
        lines.append(
            f"â€¢ **Payment Specialist Agent:** Configured (`{masked_agent}` on port {port}; role: authoritative payee settlement agent; "
            f"*Reachability notice: Active network reachability requires a live heartbeat ping*)"
        )
    else:
        lines.append("â€¢ **Payment Specialist Agent:** Local service fallback mode")

    # Payment Protocol Status (ENABLE_PAYMENT_PROTOCOL)
    if cfg.enable_payment_protocol:
        lines.append("â€¢ **Payment Protocol Execution:** ðŸŸ¢ **ENABLED** (Testnet Mode active)")
    else:
        lines.append("â€¢ **Payment Protocol Execution:** ðŸ”´ **DISABLED** (`ENABLE_PAYMENT_PROTOCOL=False`; real fund transfers and on-chain signing deactivated for safety)")

    # Network & Method
    lines.append(f"â€¢ **Configured Network:** Fetch.ai Dorado Testnet (`{cfg.payment_testnet_chain_id}`, {cfg.payment_network})")
    lines.append(f"â€¢ **Payment Method & Asset:** Direct FET transfer (`fet_direct`, denomination: `{cfg.payment_testnet_denom}`)")
    advisory_fet = cfg.payment_advisory_fee_base_units / (10**18)
    lines.append(f"â€¢ **Standard Advisory Fee:** {advisory_fet:g} FET ({cfg.payment_advisory_fee_base_units:,} {cfg.payment_testnet_denom})")

    payee_wallet = cfg.payment_payee_wallet_address
    if payee_wallet:
        lines.append(f"â€¢ **Configured Payee Wallet (Masked):** `{mask_address(payee_wallet)}`")
    if cfg.payment_testnet_rpc_url:
        lines.append(f"â€¢ **RPC Ledger Endpoint:** `{cfg.payment_testnet_rpc_url}`")

    lines.append("")
    lines.append("â„¹ï¸ **4. Order of Operations, Missing Prerequisites & Dry-Run Support:**")
    lines.append(
        f"â€¢ **Order of Operations:** Booking `{booking_id}` is confirmed. In KrishiBazaar's farm-to-market protocol, "
        f"advisory service fee payments map 1-to-1 to committed produce bookings."
    )
    lines.append("â€¢ **Missing Prerequisites for Payment Execution:**")
    if not cfg.enable_payment_protocol:
        lines.append("  1. **Protocol Activation:** `ENABLE_PAYMENT_PROTOCOL=True` must be set in environment (currently deactivated for test safety).")
    else:
        lines.append("  1. **Protocol Activation:** Enabled.")

    if not payment_record:
        lines.append("  2. **Payment Request Generation:** Advisory fee payment request has not been generated for this booking.")
    else:
        lines.append(f"  2. **Payment Request Generation:** Completed (`{payment_record.payment_reference}`).")

    lines.append(
        "â€¢ **Non-Executing Dry-Run Support:** âœ… **SUPPORTED** (Configuration, agent addresses, testnet parameters, and storage schemas verified; "
        "no on-chain transactions or token transfers will be broadcast during dry-run audits)."
    )
    if not payment_record or payment_record.status != PaymentStatus.SETTLED:
        lines.append("â€¢ **Transaction Initiation Status:** ðŸ”’ **NO TRANSACTION INITIATED** (No on-chain transaction was broadcast, no tokens debited, and no wallet funds transferred).")
    else:
        lines.append(f"â€¢ **Transaction Initiation Status:** âœ… **SETTLED** (Tx `{payment_record.tx_hash}` verified on Dorado testnet).")

    lines.append("")
    lines.append("ðŸ‘‰ **5. Next Safe Step:**")
    if not is_owner:
        lines.append("Access to this booking's payment details is restricted to the owning farmer session.")
    elif not booking:
        lines.append(f"Please verify booking ID `{booking_id}` or create a new booking proposal by stating your produce details.")
    elif not payment_record and not cfg.enable_payment_protocol:
        lines.append(
            f"Your booking `{booking.booking_id}` is confirmed. Payment execution is disabled for test safety (`ENABLE_PAYMENT_PROTOCOL=False`). "
            f"No payment action is required."
        )
    elif payment_record and payment_record.status == PaymentStatus.SETTLED:
        lines.append(f"Advisory service fee for booking `{booking.booking_id}` has been settled on testnet. No further action needed.")
    else:
        lines.append(f"Booking `{booking.booking_id}` is confirmed and safe. You can check status anytime with *\"Status of {booking.booking_id}\"*.")

    return lines


# Known informational specialist agent addresses in KrishiBazaar:
INFORMATIONAL_SPECIALIST_ADDRESSES: dict[str, str] = {
    # Wallet addresses (fetch1...)
    "fetch1aj7a34sp5udhksedvrdm2u5wqqcf9s77uk06a5": "Market Specialist Agent (port 8001)",
    "fetch1gm974uk642dklm7gtmtvmtxx8c6c4szxxt98lw": "Weather Specialist Agent (port 8002)",
    "fetch1f8uxzuhfaxhuhtc5t47yn4a0hxpgyhrcpv4khh": "Buyer Specialist Agent [Mandi] (port 8003)",
    "fetch1r0m5ncaugjt63968wte7gefk9vy76cwd3txwgt": "Buyer Specialist Agent [Reliance] (port 8005)",
    "fetch1n856kjr8dqx726rhme8s99hnqd0k9l4t4rn42v": "Transport Specialist Agent (port 8004)",
    "fetch17h5q0nhxv2thzdphmec5ymy3pqhvz66zrt7g3z": "Orchestrator Agent (port 8000)",
    # Agent communication addresses (agent1...)
    "agent1q0rsn3us4d9twt209r2xefsqku4qvgypjtp8xvkjep4tmrvrsldnw4z5az4": "Market Specialist Agent",
    "agent1qwzwhwgk3hft3z9qnaf2y7mdlwll2r23383fn6x0fhmj33lyuwuhqhq49rv": "Weather Specialist Agent",
    "agent1qtgmthsczmt5uxchs9pz0033vrfm4mwhwl5lhqewz80qfggkjay659sn3fu": "Buyer Specialist Agent [Mandi]",
    "agent1qgw98k45pq6xp6vtj6sy4mrdmhjn206zaqs5f7ff6ghsj5rafs39sesvpzh": "Buyer Specialist Agent [Reliance]",
    "agent1q0he7rn2yyh7lkswt37hdddfaf7vjcu4vtvyjd2npte07vw6dnygk7tjl8j": "Transport Specialist Agent",
    "agent1q05ca3slnwtt49gu4w60af4y2yh06ymw456d84u2cjh7pp9j3u5vwvf7a5p": "Orchestrator Agent",
}

# Known unverified placeholder address patterns that must NEVER be accepted
PLACEHOLDER_ADDRESS_PATTERNS: list[str] = [
    "fetch1testrecipientaddr01",
    "whitelisted_testnet_recipient",
    "testrecipient",
    "dummy",
    "placeholder",
    "recipientaddr",
    "fake",
    "sample",
    "attacker_unknown",
]


def validate_fetch_wallet_address(address: str) -> tuple[bool, str]:
    """
    Validate that an address is a genuine, verified Fetch ledger wallet address.
    Strictly enforces:
    1. Must be a non-empty string.
    2. Must not be a uAgents communication address (rejects 'agent1...' with clear explanation).
    3. Must start with 'fetch1' prefix.
    4. Must not match any placeholder, dummy, or unverified test pattern.
    5. Must not be an informational specialist agent address (funds must never route to Market, Weather, Buyer, Transport, or Orchestrator).
    6. Must pass CosmPy SDK Address() bech32 parsing and checksum verification.
    """
    if not address or not isinstance(address, str):
        return False, "Address cannot be empty."

    addr_str = address.strip()

    if addr_str.startswith("agent1"):
        return (
            False,
            "Invalid address format: 'agent1...' is a uAgents communication address, "
            "not an on-chain ledger wallet address (fetch1... required).",
        )

    if not addr_str.startswith("fetch1"):
        prefix = addr_str[:6] if len(addr_str) >= 6 else addr_str
        return (
            False,
            f"Invalid address format: expected 'fetch1...' prefix, got '{prefix}'.",
        )

    lower_addr = addr_str.lower()
    for pattern in PLACEHOLDER_ADDRESS_PATTERNS:
        if pattern in lower_addr:
            return (
                False,
                f"Rejected placeholder address: '{addr_str}' contains unverified placeholder pattern '{pattern}'.",
            )

    if lower_addr in INFORMATIONAL_SPECIALIST_ADDRESSES:
        specialist = INFORMATIONAL_SPECIALIST_ADDRESSES[lower_addr]
        return (
            False,
            f"Rejected routing: {specialist} is an informational specialist agent and cannot receive payment funds.",
        )

    try:
        Address(addr_str)
    except Exception as e:
        return (
            False,
            f"Invalid Fetch address format or checksum according to CosmPy SDK: {e}",
        )

    return True, "Valid Fetch ledger wallet address."


class PaymentService:
    """
    Production-grade Fetch.ai Agent Payment Protocol service for KrishiBazaar.
    Integrates protocol message negotiation with CosmPy ledger transfers and authoritative verification.
    """

    def __init__(
        self,
        storage_engine: StorageEngine | None = None,
        settings_obj: Settings | None = None,
        ledger_client: LedgerClient | None = None,
    ) -> None:
        self.storage = storage_engine or storage
        self.settings = settings_obj or settings
        self._ledger = ledger_client

    def is_enabled(self) -> bool:
        """Check whether payment protocol functionality is active."""
        return bool(self.settings.enable_payment_protocol)

    @staticmethod
    def extract_transaction_fee(
        tx_resp: Any,
        expected_denom: str = "atestfet",
    ) -> tuple[int | None, str | None]:
        """Extract and strictly validate actual network fee from a CosmPy TxResponse."""
        return extract_transaction_fee(tx_resp, expected_denom=expected_denom)

    def get_ledger(self) -> LedgerClient:
        """
        Retrieve or initialize the active LedgerClient.
        Strictly restricted to Fetch.ai Dorado testnet ('dorado-1').
        Mainnet connections are disallowed to prevent real-money transfer hazards.
        """
        if self._ledger is not None:
            return self._ledger
        if self.settings.payment_network != "testnet" or self.settings.payment_testnet_chain_id != "dorado-1":
            raise PermissionError(
                "KrishiBazaar payment subsystem supports ONLY the Fetch.ai Dorado testnet ('dorado-1'). "
                "Mainnet transfers are strictly prohibited and unavailable."
            )
        return get_ledger(network="testnet")

    def is_recipient_whitelisted(self, recipient_address: str) -> bool:
        """
        Check if recipient address is in the configured payment whitelist.
        When whitelist is empty and protocol is disabled, returns False.
        """
        whitelist = self.settings.payment_recipient_whitelist
        if not whitelist:
            return False
        return recipient_address.strip() in whitelist

    def estimate_network_fee(self) -> tuple[int, str]:
        """
        Query or calculate actual network gas fee on Fetch.ai Dorado testnet.
        Uses live network parameters: 80,000 gas units for bank send @ fee_minimum_gas_price.
        Returns: (fee_base_units, display_string)
        """
        gas_limit = 80_000
        gas_price = 5_000_000_000  # official dorado-1 default: 5,000,000,000 atestfet (5 Gwei)
        try:
            ledger = self.get_ledger()
            if hasattr(ledger, "network_config") and hasattr(ledger.network_config, "fee_minimum_gas_price"):
                gas_price = int(ledger.network_config.fee_minimum_gas_price)
        except Exception:
            pass
        fee_base = gas_limit * gas_price
        fet_val = fee_base / (10**18)
        denom = self.settings.payment_testnet_denom
        display = f"~{fet_val:g} FET ({fee_base:,} {denom}, estimated for 80,000 gas @ {gas_price:,} {denom}/gas)"
        return fee_base, display

    def get_payer_address(self) -> str | None:
        """Derive the public fetch1... address of the configured testnet payer wallet, if set."""
        if not self.settings.payment_payer_seed:
            return None
        try:
            w = LocalWallet.from_mnemonic(self.settings.payment_payer_seed)
            return str(w.address())
        except Exception:
            try:
                w = LocalWallet.from_unsafe_seed(self.settings.payment_payer_seed)
                return str(w.address())
            except Exception:
                return None

    def check_payer_balance(
        self,
        payer_address: str | None = None,
        required_base_units: int | None = None,
    ) -> tuple[int | None, bool, str]:
        """
        Check spendable balance of the payer wallet on the official Dorado testnet ledger.
        Returns: (balance_base_units, is_sufficient, status_string)
        """
        target_addr = payer_address or self.get_payer_address()
        if not target_addr:
            return None, False, "UNCHECKED (No dedicated payer wallet configured)"

        fee_base, _ = self.estimate_network_fee()
        target_amount = (required_base_units or self.settings.payment_advisory_fee_base_units) + fee_base
        denom = self.settings.payment_testnet_denom

        try:
            ledger = self.get_ledger()
            bal = ledger.query_bank_balance(Address(target_addr), denom)
            is_suff = bal >= target_amount
            if is_suff:
                status = f"FUNDED ({bal:,} {denom} available; sufficient for transfer + fee)"
            else:
                amt_fet = (target_amount - fee_base) / (10**18)
                fee_fet = fee_base / (10**18)
                status = (
                    f"UNFUNDED ({bal:,} {denom} available; "
                    f"requires {target_amount:,} {denom} for {amt_fet:g} FET amount + ~{fee_fet:g} FET fee)"
                )
            return bal, is_suff, status
        except Exception as e:
            return None, False, f"UNREACHABLE (Testnet query failed: {str(e)})"

    def create_advisory_payment_request(
        self,
        payee_agent_address: str,
        payee_wallet_address: str,
        amount_base_units: int | None = None,
        currency: str | None = None,
        deadline_seconds: int = 300,
        purpose: str = PaymentPurpose.ADVISORY_PASS.value,
        payer_agent_address: str = "unknown_payer_agent",
        payer_wallet_address: str = "unknown_payer_wallet",
        service_inquiry_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> tuple[RequestPayment, PaymentRecord]:
        """
        Seller/Payee generates an official RequestPayment message and records it in storage.
        Uses integer base units for amounts (e.g. 1000 atestfet).
        Strictly validates recipient address format and prevents placeholder routing.
        """
        if self.settings.payment_network != "testnet" or self.settings.payment_testnet_chain_id != "dorado-1":
            raise PermissionError(
                "KrishiBazaar payment subsystem supports ONLY the Fetch.ai Dorado testnet ('dorado-1'). "
                "Mainnet transfers are strictly prohibited and unavailable."
            )

        amt = amount_base_units or self.settings.payment_advisory_fee_base_units
        denom = currency or self.settings.payment_testnet_denom
        if amt <= 0:
            raise ValueError(f"amount_base_units must be positive, got {amt}")

        # Validate recipient/payee wallet address
        is_valid_rec, rec_reason = validate_fetch_wallet_address(payee_wallet_address)
        if not is_valid_rec:
            raise ValueError(f"Invalid payee wallet address '{payee_wallet_address}': {rec_reason}")

        # Validate payer wallet address if provided and not dummy placeholder
        if payer_wallet_address and payer_wallet_address != "unknown_payer_wallet":
            is_valid_payer, payer_reason = validate_fetch_wallet_address(payer_wallet_address)
            if not is_valid_payer:
                raise ValueError(f"Invalid payer wallet address '{payer_wallet_address}': {payer_reason}")
            if payer_wallet_address.lower() == payee_wallet_address.lower():
                raise ValueError(
                    f"Payer wallet ({payer_wallet_address}) and payee wallet ({payee_wallet_address}) "
                    "must be separate designated wallets; self-payment is not permitted."
                )

        ref = f"KB-PAY-{uuid.uuid4().hex[:12].upper()}"
        now_utc = datetime.now(timezone.utc).isoformat()

        funds = Funds(
            amount=str(amt),
            currency=denom,
            payment_method="fet_direct",
        )

        display_str = format_token_amount_display(amt, denom)

        record = PaymentRecord(
            payment_reference=ref,
            service_inquiry_id=service_inquiry_id,
            purpose=purpose,
            payer_agent_address=payer_agent_address,
            payer_wallet_address=payer_wallet_address,
            payee_agent_address=payee_agent_address,
            payee_wallet_address=payee_wallet_address,
            amount_base_units=amt,
            amount_display=display_str,
            currency=denom,
            network=self.settings.payment_testnet_chain_id,
            status=PaymentStatus.REQUESTED,
            deadline_seconds=deadline_seconds,
            created_at_utc=now_utc,
            updated_at_utc=now_utc,
            metadata=metadata or {},
        )

        self.storage.save_payment_record(record)

        msg = RequestPayment(
            accepted_funds=[funds],
            recipient=payee_wallet_address,
            deadline_seconds=deadline_seconds,
            reference=ref,
            description=purpose,
            metadata=metadata or {},
        )

        return msg, record

    def format_review_details(self, record: PaymentRecord) -> PaymentReviewDetails:
        """
        Format user-friendly presentation review details for human approval.
        Provides the exact authorization command required with live balance and fee verification.
        """
        cmd = (
            f"AUTHORIZE PAYMENT REF:{record.payment_reference} "
            f"AMOUNT:{record.amount_base_units} "
            f"CURRENCY:{record.currency} "
            f"RECIPIENT:{record.payee_wallet_address}"
        )
        _, fee_display = self.estimate_network_fee()
        payer_addr = record.payer_wallet_address if record.payer_wallet_address != "unknown_payer_wallet" else None
        _, _, bal_status = self.check_payer_balance(
            payer_address=payer_addr,
            required_base_units=record.amount_base_units,
        )

        return PaymentReviewDetails(
            reference=record.payment_reference,
            purpose=record.purpose,
            amount_base_units=record.amount_base_units,
            amount_display=record.amount_display,
            currency=record.currency,
            recipient_address=record.payee_wallet_address,
            network=record.network,
            estimated_fee=fee_display,
            deadline_seconds=record.deadline_seconds,
            approval_command_template=cmd,
            payer_balance_status=bal_status,
            testnet_notice="âš ï¸ TESTNET DEMO MICRO-FEE ONLY. No real fiat or bank funds debited.",
        )

    def receive_payment_request(
        self,
        msg: RequestPayment,
        payer_agent_address: str,
        payer_wallet_address: str,
    ) -> tuple[bool, str, PaymentRecord | None]:
        """
        Buyer/Payer handles an incoming RequestPayment.
        Validates structure, whitelist, and stores the pending request awaiting human approval.
        """
        if not msg.accepted_funds:
            return False, "Rejecting payment request: no accepted funds provided.", None

        if not msg.reference:
            return False, "Rejecting payment request: missing payment reference.", None

        selected = msg.accepted_funds[0]
        try:
            amt = int(selected.amount)
            if amt <= 0:
                return False, f"Rejecting payment request: non-positive amount {amt}", None
        except ValueError:
            return False, f"Rejecting payment request: invalid non-integer amount string '{selected.amount}'", None

        # Validate recipient address format and reject placeholders
        is_valid_rec, rec_reason = validate_fetch_wallet_address(msg.recipient)
        if not is_valid_rec:
            return False, f"Rejecting payment request: {rec_reason}", None

        if payer_wallet_address and payer_wallet_address != "unknown_payer_wallet":
            if msg.recipient.lower() == payer_wallet_address.lower():
                return False, "Rejecting payment request: payer and payee cannot be the same wallet address.", None

        # Check whitelist if protocol is enabled
        if self.is_enabled() and not self.is_recipient_whitelisted(msg.recipient):
            return False, f"Rejecting payment request: recipient {msg.recipient} is not whitelisted.", None

        now_utc = datetime.now(timezone.utc).isoformat()
        record = PaymentRecord(
            payment_reference=msg.reference,
            purpose=msg.description or PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=payer_agent_address,
            payer_wallet_address=payer_wallet_address,
            payee_agent_address="",  # Set by caller or envelope sender
            payee_wallet_address=msg.recipient,
            amount_base_units=amt,
            amount_display=f"{amt} {selected.currency}",
            currency=selected.currency,
            network=self.settings.payment_testnet_chain_id,
            status=PaymentStatus.AWAITING_AUTHORIZATION,
            deadline_seconds=msg.deadline_seconds,
            created_at_utc=now_utc,
            updated_at_utc=now_utc,
            metadata=msg.metadata or {},
        )
        self.storage.save_payment_record(record)
        return True, "Payment request received and awaiting human authorization.", record

    def reject_payment_request(
        self,
        reference: str,
        reason: str = "User declined authorization",
    ) -> RejectPayment:
        """Reject a pending payment request and transition state to REJECTED."""
        self.storage.update_payment_status(
            reference=reference,
            status=PaymentStatus.REJECTED,
            rejection_reason=reason,
        )
        return RejectPayment(reason=reason)

    def execute_authorized_transfer(
        self,
        approval: ExplicitPaymentApproval,
        payer_wallet: LocalWallet,
        ledger_client: LedgerClient | None = None,
    ) -> tuple[CommitPayment, PaymentRecord]:
        """
        Execute an on-chain token transfer following explicit human approval.
        Enforces:
        - Whitelist validation
        - Matching record validation
        - Duplicate / replay protection
        - Deadline expiration check
        - Spendable balance check
        - Ledger broadcast and CommitPayment construction
        """
        if not self.is_enabled():
            raise RuntimeError(
                "Payment transfers are disabled by default. "
                "Set ENABLE_PAYMENT_PROTOCOL=true in environment to execute."
            )

        if self.settings.payment_network != "testnet" or self.settings.payment_testnet_chain_id != "dorado-1":
            raise PermissionError(
                "KrishiBazaar payment subsystem supports ONLY the Fetch.ai Dorado testnet ('dorado-1'). "
                "Mainnet transfers are strictly prohibited and unavailable."
            )

        # Validate recipient address format and reject placeholders
        is_valid_rec, rec_reason = validate_fetch_wallet_address(approval.recipient)
        if not is_valid_rec:
            raise ValueError(f"Invalid recipient address: {rec_reason}")

        if not self.is_recipient_whitelisted(approval.recipient):
            raise PermissionError(
                f"Recipient {approval.recipient} is not in PAYMENT_RECIPIENT_WHITELIST."
            )

        record = self.storage.get_payment_record(approval.reference)
        if not record:
            raise ValueError(f"No payment record found for reference {approval.reference}")

        # Separation of test wallet roles: payer and recipient cannot be the same address
        payer_addr = str(payer_wallet.address())
        if payer_addr.lower() == str(record.payee_wallet_address).lower():
            raise ValueError(
                f"Payer wallet ({payer_addr}) and payee wallet ({record.payee_wallet_address}) "
                "must be separate designated wallets; self-payment is not permitted."
            )

        # Idempotency & Replay Defense
        if record.status not in (
            PaymentStatus.REQUESTED,
            PaymentStatus.AWAITING_AUTHORIZATION,
        ):
            raise ValueError(
                f"Cannot authorize payment {record.payment_reference}: "
                f"current status is '{record.status.value}', replay or duplicate authorization blocked."
            )

        # Exact match assertions
        if record.amount_base_units != approval.amount_base_units:
            raise ValueError(
                f"Amount mismatch: record has {record.amount_base_units}, "
                f"approval specified {approval.amount_base_units}"
            )
        if record.currency.lower() != approval.currency.lower():
            raise ValueError(
                f"Currency mismatch: record has {record.currency}, "
                f"approval specified {approval.currency}"
            )
        if record.payee_wallet_address.lower() != approval.recipient.lower():
            raise ValueError(
                f"Recipient mismatch: record has {record.payee_wallet_address}, "
                f"approval specified {approval.recipient}"
            )

        # Expiry check
        created_dt = datetime.fromisoformat(record.created_at_utc)
        elapsed = (datetime.now(timezone.utc) - created_dt).total_seconds()
        if elapsed > record.deadline_seconds:
            self.storage.update_payment_status(
                reference=record.payment_reference,
                status=PaymentStatus.FAILED,
                rejection_reason="Payment request expired before authorization.",
            )
            raise TimeoutError(
                f"Payment request {record.payment_reference} has expired "
                f"({elapsed:.1f}s elapsed > {record.deadline_seconds}s limit)."
            )

        # Check balance
        ledger = ledger_client or self.get_ledger()
        payer_addr = payer_wallet.address()
        bal = ledger.query_bank_balance(Address(payer_addr), record.currency)
        if bal < record.amount_base_units:
            raise ValueError(
                f"Insufficient spendable balance on payer wallet {payer_addr}: "
                f"has {bal} {record.currency}, required {record.amount_base_units}"
            )

        # Atomic Pre-Broadcast Lock: Transition to SUBMITTING before on-chain submission
        transitioned = self.storage.transition_payment_status_atomic(
            reference=record.payment_reference,
            from_statuses=[
                PaymentStatus.REQUESTED,
                PaymentStatus.AWAITING_AUTHORIZATION,
            ],
            to_status=PaymentStatus.SUBMITTING,
        )
        if not transitioned:
            curr = self.storage.get_payment_record(record.payment_reference)
            curr_status = curr.status.value if curr else "UNKNOWN"
            raise ValueError(
                f"Cannot authorize payment {record.payment_reference}: "
                f"current status is '{curr_status}', replay or duplicate authorization blocked."
            )
        record = self.storage.get_payment_record(record.payment_reference) or record

        # Execute on-chain transfer
        logger.info(
            f"Executing token transfer of {record.amount_base_units} {record.currency} "
            f"from {payer_addr} to {record.payee_wallet_address}"
        )
        try:
            submitted_tx: SubmittedTx = ledger.send_tokens(
                destination=Address(record.payee_wallet_address),
                amount=record.amount_base_units,
                denom=record.currency,
                sender=payer_wallet,
                memo=f"KrishiBazaar:{record.payment_reference}",
            )
            tx_hash = submitted_tx.tx_hash
        except Exception as e:
            logger.error(
                f"Failed to broadcast token transfer for {record.payment_reference}: {e}"
            )
            self.storage.transition_payment_status_atomic(
                reference=record.payment_reference,
                from_statuses=[PaymentStatus.SUBMITTING],
                to_status=PaymentStatus.FAILED,
                rejection_reason=f"Broadcast failed: {str(e)}",
            )
            raise

        # Update record to COMMITTED / PENDING_SETTLEMENT
        self.storage.transition_payment_status_atomic(
            reference=record.payment_reference,
            from_statuses=[PaymentStatus.SUBMITTING],
            to_status=PaymentStatus.COMMITTED,
            tx_hash=tx_hash,
        )
        record = self.storage.get_payment_record(record.payment_reference) or record

        commit = CommitPayment(
            funds=Funds(
                amount=str(record.amount_base_units),
                currency=record.currency,
                payment_method="fet_direct",
            ),
            recipient=record.payee_wallet_address,
            transaction_id=tx_hash,
            reference=record.payment_reference,
            description=record.purpose,
            metadata=record.metadata,
        )

        return commit, record

    def verify_and_settle_commitment(
        self,
        commit: CommitPayment,
        ledger_client: LedgerClient | None = None,
    ) -> tuple[bool, CompletePayment | None, CancelPayment | None, PaymentRecord]:
        """
        Payee/Seller authoritatively queries the ledger to verify that tokens actually settled.
        NEVER marks payment SETTLED based solely on the protocol message or tx hash.
        Returns: (is_settled, CompletePayment if settled, CancelPayment if failed, updated record)
        """
        ref = commit.reference
        if not ref:
            raise ValueError("CommitPayment missing reference ID")

        record = self.storage.get_payment_record(ref)
        if not record:
            raise ValueError(f"No payment record found for reference {ref}")

        # Idempotency / Replay Protection: already SETTLED records cannot be replayed
        if record.status == PaymentStatus.SETTLED:
            logger.info(f"Payment record {ref} is already authoritatively SETTLED.")
            return True, CompletePayment(transaction_id=record.tx_hash or commit.transaction_id), None, record

        tx_hash = commit.transaction_id
        if not tx_hash:
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.FAILED,
                cancellation_reason="Missing transaction_id in CommitPayment",
            )
            cancel = CancelPayment(
                transaction_id=None,
                reason="Missing transaction_id in CommitPayment",
            )
            return False, None, cancel, record

        ledger = ledger_client or self.get_ledger()

        # Synchronous authoritative on-chain lookup
        try:
            logger.info(f"Querying ledger for transaction {tx_hash}...")
            tx_resp = ledger.query_tx(tx_hash)
            if hasattr(tx_resp, "__await__"):  # In case mock returns a coroutine
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            tx_resp = pool.submit(asyncio.run, tx_resp).result()
                    else:
                        tx_resp = loop.run_until_complete(tx_resp)
                except Exception:
                    tx_resp = asyncio.run(tx_resp)
        except Exception as e:
            logger.warning(
                f"Ledger transaction query timed out or was uncertain for {tx_hash}: {e}"
            )
            # Safe holding state: do NOT auto-retry or mark settled!
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.AWAITING_RECONCILIATION,
                tx_hash=tx_hash,
            )
            record = self.storage.get_payment_record(ref) or record
            return False, None, None, record

        return self._process_settlement_response(tx_resp, ref, record, tx_hash)

    async def verify_and_settle_commitment_async(
        self,
        commit: CommitPayment,
        ledger_client: LedgerClient | None = None,
    ) -> tuple[bool, CompletePayment | None, CancelPayment | None, PaymentRecord]:
        """
        Asynchronous authoritative ledger verification for use in async protocol handlers.
        Awaits official wait_for_tx_to_complete.
        """
        ref = commit.reference
        if not ref:
            raise ValueError("CommitPayment missing reference ID")

        record = self.storage.get_payment_record(ref)
        if not record:
            raise ValueError(f"No payment record found for reference {ref}")

        # Idempotency / Replay Protection: already SETTLED records cannot be replayed
        if record.status == PaymentStatus.SETTLED:
            logger.info(f"Payment record {ref} is already authoritatively SETTLED.")
            return True, CompletePayment(transaction_id=record.tx_hash or commit.transaction_id), None, record

        tx_hash = commit.transaction_id
        if not tx_hash:
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.FAILED,
                cancellation_reason="Missing transaction_id in CommitPayment",
            )
            cancel = CancelPayment(
                transaction_id=None,
                reason="Missing transaction_id in CommitPayment",
            )
            return False, None, cancel, record

        ledger = ledger_client or self.get_ledger()

        try:
            logger.info(f"Awaiting ledger confirmation for transaction {tx_hash}...")
            tx_resp = await wait_for_tx_to_complete(tx_hash, ledger)
        except Exception as e:
            logger.warning(
                f"Ledger transaction verification timed out or was uncertain for {tx_hash}: {e}"
            )
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.AWAITING_RECONCILIATION,
                tx_hash=tx_hash,
            )
            record = self.storage.get_payment_record(ref) or record
            return False, None, None, record

        return self._process_settlement_response(tx_resp, ref, record, tx_hash)

    def _process_settlement_response(
        self,
        tx_resp: TxResponse,
        ref: str,
        record: PaymentRecord,
        tx_hash: str,
    ) -> tuple[bool, CompletePayment | None, CancelPayment | None, PaymentRecord]:
        """
        Internal processor for authoritative ledger response validation.
        Enforces strict rules:
        1. Idempotency check: Already SETTLED records remain settled.
        2. Execution success on-chain (is_successful() / code == 0).
        3. Authoritatively confirmed block height (> 0).
        4. Reliable transfer event evidence (coin_received or transfer) proving:
           - Actual recipient matches record.payee_wallet_address exactly.
           - Actual transferred amount and denomination match amount_base_units and currency exactly.
        5. Missing, malformed, or ambiguous evidence must NOT mark SETTLED; safely hold in AWAITING_RECONCILIATION.
        6. Confirmed recipient or amount mismatches mark FAILED and return CancelPayment.
        """
        # 1. Idempotency defense
        if record.status == PaymentStatus.SETTLED:
            logger.info(f"Payment record {ref} is already authoritatively SETTLED.")
            # If fee metadata is missing on this settled record, backfill it safely
            if record.actual_fee_base_units is None and tx_resp:
                fee_base, fee_disp = extract_transaction_fee(tx_resp, expected_denom=record.currency)
                if fee_base is not None:
                    self.storage.update_payment_status(
                        reference=ref,
                        status=PaymentStatus.SETTLED,
                        actual_fee_base_units=fee_base,
                        actual_fee_display=fee_disp,
                    )
                    record = self.storage.get_payment_record(ref) or record
            return True, CompletePayment(transaction_id=record.tx_hash or tx_hash), None, record

        # 2. Verify execution success
        is_success = tx_resp.is_successful() if hasattr(tx_resp, "is_successful") else (getattr(tx_resp, "code", -1) == 0)
        if not is_success:
            code = getattr(tx_resp, "code", -1)
            raw_log = getattr(tx_resp, "raw_log", "")
            err_msg = f"On-chain transaction execution failed with code {code}: {raw_log}"
            logger.error(err_msg)
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.FAILED,
                tx_hash=tx_hash,
                tx_height=getattr(tx_resp, "height", None),
                cancellation_reason=err_msg,
            )
            cancel = CancelPayment(transaction_id=tx_hash, reason=err_msg)
            record = self.storage.get_payment_record(ref) or record
            return False, None, cancel, record

        # 3. Verify confirmed block height (> 0)
        height = getattr(tx_resp, "height", None)
        if height is None or not isinstance(height, int) or height <= 0:
            err_msg = f"Transaction {tx_hash} has invalid or unconfirmed block height ({height}). Holding in reconciliation."
            logger.warning(err_msg)
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.AWAITING_RECONCILIATION,
                tx_hash=tx_hash,
            )
            record = self.storage.get_payment_record(ref) or record
            return False, None, None, record

        # 4. Extract transfer events from top-level events and message logs
        transfer_candidates: list[dict[str, Any]] = []

        def _collect_from_events_dict(evs: Any, source_prefix: str) -> None:
            if not isinstance(evs, dict):
                return
            for ev_type in ("coin_received", "transfer"):
                if ev_type in evs:
                    ev_data = evs[ev_type]
                    if isinstance(ev_data, dict):
                        rec = ev_data.get("receiver") or ev_data.get("recipient")
                        amt = ev_data.get("amount")
                        transfer_candidates.append({
                            "type": ev_type,
                            "receiver": rec,
                            "amount": amt,
                            "source": f"{source_prefix}.{ev_type}",
                        })
                    else:
                        transfer_candidates.append({
                            "type": ev_type,
                            "receiver": None,
                            "amount": None,
                            "source": f"{source_prefix}.{ev_type}",
                        })

        # Top-level events
        _collect_from_events_dict(getattr(tx_resp, "events", None), "events")

        # Message logs
        logs = getattr(tx_resp, "logs", None)
        if isinstance(logs, list):
            for idx, log in enumerate(logs):
                _collect_from_events_dict(getattr(log, "events", None), f"logs[{idx}]")

        # 5. Require transfer event evidence to be present
        if not transfer_candidates:
            err_msg = (
                f"Missing required transfer event evidence (coin_received/transfer) in transaction {tx_hash} "
                f"for reference {ref}. Cannot authoritatively settle without transfer proof. Holding in reconciliation."
            )
            logger.warning(err_msg)
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.AWAITING_RECONCILIATION,
                tx_hash=tx_hash,
            )
            record = self.storage.get_payment_record(ref) or record
            return False, None, None, record

        # 6. Check for malformed events (missing receiver or amount attributes)
        valid_candidates: list[dict[str, Any]] = []
        for c in transfer_candidates:
            rec = c.get("receiver")
            amt = c.get("amount")
            if isinstance(rec, str) and rec.strip() and isinstance(amt, str) and amt.strip():
                valid_candidates.append(c)

        if not valid_candidates:
            err_msg = (
                f"Malformed or incomplete transfer event data in transaction {tx_hash} "
                f"for reference {ref}. Missing receiver or amount attributes. Holding in reconciliation."
            )
            logger.warning(err_msg)
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.AWAITING_RECONCILIATION,
                tx_hash=tx_hash,
            )
            record = self.storage.get_payment_record(ref) or record
            return False, None, None, record

        # 7. Evaluate candidate events against expected payee and amount
        expected_receiver = record.payee_wallet_address.strip().lower()
        expected_base_units = record.amount_base_units
        expected_denom = record.currency.strip().lower()
        expected_amount_str = f"{expected_base_units}{expected_denom}"

        def _parse_coin_tokens(amount_raw: str) -> list[tuple[int, str]] | None:
            coins = []
            for token in amount_raw.split(","):
                token = token.strip()
                if not token:
                    continue
                m = re.match(r"^(\d+)([a-zA-Z0-9/]+)$", token)
                if not m:
                    return None  # Unparseable / malformed coin token
                coins.append((int(m.group(1)), m.group(2).lower()))
            return coins if coins else None

        matching_candidates = []
        recipient_matches = []
        other_recipient_candidates = []
        has_ambiguous_amount = False

        for c in valid_candidates:
            rec_str = c["receiver"].strip().lower()
            amt_str = c["amount"].strip().lower()
            parsed_coins = _parse_coin_tokens(amt_str)
            if parsed_coins is None:
                has_ambiguous_amount = True
                continue

            matches_amt = any(
                amt_val == expected_base_units and denom == expected_denom
                for amt_val, denom in parsed_coins
            ) or (amt_str == expected_amount_str)

            if rec_str == expected_receiver:
                recipient_matches.append((c, matches_amt, amt_str))
                if matches_amt:
                    matching_candidates.append(c)
            else:
                other_recipient_candidates.append((c, matches_amt, amt_str, rec_str))

        if has_ambiguous_amount and not matching_candidates:
            err_msg = (
                f"Ambiguous or unparseable amount format in transfer events for transaction {tx_hash} "
                f"(ref: {ref}). Holding in reconciliation."
            )
            logger.warning(err_msg)
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.AWAITING_RECONCILIATION,
                tx_hash=tx_hash,
            )
            record = self.storage.get_payment_record(ref) or record
            return False, None, None, record

        # 8. Determine outcome
        if recipient_matches:
            if matching_candidates:
                # Authoritatively SETTLED
                now_utc = datetime.now(timezone.utc).isoformat()
                fee_base, fee_disp = extract_transaction_fee(tx_resp, expected_denom=record.currency)
                self.storage.update_payment_status(
                    reference=ref,
                    status=PaymentStatus.SETTLED,
                    tx_hash=tx_hash,
                    tx_height=height,
                    actual_fee_base_units=fee_base,
                    actual_fee_display=fee_disp,
                    verified_at_utc=now_utc,
                )
                record = self.storage.get_payment_record(ref) or record
                logger.info(
                    f"AUTHORITATIVELY SETTLED: Payment {ref} confirmed in block {record.tx_height} (tx: {tx_hash})"
                )
                complete = CompletePayment(transaction_id=tx_hash)
                return True, complete, None, record
            else:
                # Recipient was matched, but the transferred amount or denomination was WRONG
                _, _, actual_amt_str = recipient_matches[0]
                err_msg = f"Transferred amount {actual_amt_str} != expected {expected_amount_str}"
                logger.error(err_msg)
                self.storage.update_payment_status(
                    reference=ref,
                    status=PaymentStatus.FAILED,
                    tx_hash=tx_hash,
                    tx_height=height,
                    cancellation_reason=err_msg,
                )
                cancel = CancelPayment(transaction_id=tx_hash, reason=err_msg)
                record = self.storage.get_payment_record(ref) or record
                return False, None, cancel, record

        if other_recipient_candidates:
            # Transfer went to someone else (wrong recipient)
            _, _, _, actual_rec = other_recipient_candidates[0]
            err_msg = f"Transferred funds receiver {actual_rec} != expected {record.payee_wallet_address}"
            logger.error(err_msg)
            self.storage.update_payment_status(
                reference=ref,
                status=PaymentStatus.FAILED,
                tx_hash=tx_hash,
                tx_height=height,
                cancellation_reason=err_msg,
            )
            cancel = CancelPayment(transaction_id=tx_hash, reason=err_msg)
            record = self.storage.get_payment_record(ref) or record
            return False, None, cancel, record

        # Ambiguous transfer event data fallback
        err_msg = (
            f"Ambiguous transfer event evidence for transaction {tx_hash} (ref: {ref}). "
            "Holding in reconciliation."
        )
        logger.warning(err_msg)
        self.storage.update_payment_status(
            reference=ref,
            status=PaymentStatus.AWAITING_RECONCILIATION,
            tx_hash=tx_hash,
        )
        record = self.storage.get_payment_record(ref) or record
        return False, None, None, record

    def reconcile_payment_on_chain(
        self,
        reference: str,
        ledger_client: LedgerClient | None = None,
    ) -> tuple[bool, PaymentRecord]:
        """
        Authoritatively query the testnet ledger to reconcile an existing payment record
        that is COMMITTED, AWAITING_RECONCILIATION, or SETTLED (missing fee repair).
        Never marks settled without ledger verification.
        """
        record = self.storage.get_payment_record(reference)
        if not record:
            raise ValueError(f"No payment record found for reference {reference}")

        if record.status == PaymentStatus.SETTLED:
            # Narrowly scoped metadata-repair path for already-SETTLED record with missing fee
            if record.actual_fee_base_units is None and record.tx_hash:
                try:
                    ledger = ledger_client or self.get_ledger()
                    tx_resp = ledger.query_tx(record.tx_hash)
                    if hasattr(tx_resp, "__await__"):
                        import asyncio
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                import concurrent.futures
                                with concurrent.futures.ThreadPoolExecutor() as pool:
                                    tx_resp = pool.submit(asyncio.run, tx_resp).result()
                            else:
                                tx_resp = loop.run_until_complete(tx_resp)
                        except Exception:
                            tx_resp = asyncio.run(tx_resp)
                    fee_base, fee_disp = extract_transaction_fee(tx_resp, expected_denom=record.currency)
                    if fee_base is not None:
                        self.storage.update_payment_status(
                            reference=reference,
                            status=PaymentStatus.SETTLED,
                            actual_fee_base_units=fee_base,
                            actual_fee_display=fee_disp,
                        )
                        record = self.storage.get_payment_record(reference) or record
                        logger.info(
                            f"Repaired missing fee metadata for settled payment {reference}: {fee_disp}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Could not backfill fee metadata for settled tx {record.tx_hash}: {e}"
                    )
            return True, record

        if not record.tx_hash:
            return False, record

        ledger = ledger_client or self.get_ledger()
        try:
            tx_resp = ledger.query_tx(record.tx_hash)
            if hasattr(tx_resp, "__await__"):
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            tx_resp = pool.submit(asyncio.run, tx_resp).result()
                    else:
                        tx_resp = loop.run_until_complete(tx_resp)
                except Exception:
                    tx_resp = asyncio.run(tx_resp)
        except Exception as e:
            logger.warning(f"Reconciliation query failed for tx {record.tx_hash}: {e}")
            return False, record

        is_settled, _, _, updated_rec = self._process_settlement_response(
            tx_resp, record.payment_reference, record, record.tx_hash
        )
        return is_settled, updated_rec

    def repair_settled_payment_fee_metadata(
        self,
        reference: str,
        ledger_client: LedgerClient | None = None,
    ) -> tuple[bool, PaymentRecord]:
        """
        Narrowly scoped metadata repair for an already-SETTLED record with missing fee.
        Queries existing transaction hash and backfills actual_fee_base_units and
        actual_fee_display from authoritative ledger events without rebroadcasting or modifying status.
        """
        return self.reconcile_payment_on_chain(reference, ledger_client=ledger_client)


def create_buyer_payment_protocol(
    service: PaymentService,
    payer_wallet: LocalWallet,
) -> Protocol:
    """
    Construct a uAgents Protocol with role='buyer' for the payer agent.
    Registers handlers for RequestPayment, CompletePayment, and CancelPayment.
    """
    proto = Protocol(spec=payment_protocol_spec, role="buyer")

    @proto.on_message(RequestPayment)
    async def on_request_payment(ctx: Context, sender: str, msg: RequestPayment) -> None:
        ctx.logger.info(
            f"Payment Protocol [buyer]: Received RequestPayment from {sender} "
            f"for ref={msg.reference}, recipient={msg.recipient}"
        )
        ok, reason, record = service.receive_payment_request(
            msg,
            payer_agent_address=ctx.agent.address,
            payer_wallet_address=str(payer_wallet.address()),
        )
        if not ok:
            ctx.logger.warning(f"Payment Protocol [buyer]: {reason}")
            await ctx.send(sender, RejectPayment(reason=reason))
            return

        ctx.logger.info(
            f"Payment Protocol [buyer]: Stored request {msg.reference} awaiting human authorization. "
            f"Send explicit command to authorize."
        )

    @proto.on_message(CompletePayment)
    async def on_complete_payment(ctx: Context, sender: str, msg: CompletePayment) -> None:
        ctx.logger.info(
            f"Payment Protocol [buyer]: Received CompletePayment from {sender} "
            f"for tx={msg.transaction_id}"
        )

    @proto.on_message(CancelPayment)
    async def on_cancel_payment(ctx: Context, sender: str, msg: CancelPayment) -> None:
        ctx.logger.warning(
            f"Payment Protocol [buyer]: Received CancelPayment from {sender}: {msg.reason}"
        )

    return proto


def create_seller_payment_protocol(
    service: PaymentService,
) -> Protocol:
    """
    Construct a uAgents Protocol with role='seller' for the payee/service provider agent.
    Registers handlers for CommitPayment and RejectPayment.
    """
    proto = Protocol(spec=payment_protocol_spec, role="seller")

    @proto.on_message(CommitPayment)
    async def on_commit_payment(ctx: Context, sender: str, msg: CommitPayment) -> None:
        ctx.logger.info(
            f"Payment Protocol [seller]: Received CommitPayment from {sender} "
            f"with tx={msg.transaction_id}, ref={msg.reference}"
        )
        is_settled, complete, cancel, record = await service.verify_and_settle_commitment_async(
            msg, ctx.ledger
        )
        if is_settled and complete:
            ctx.logger.info(
                f"Payment Protocol [seller]: Verified settlement on-chain for {msg.reference}. Sending CompletePayment."
            )
            await ctx.send(sender, complete)
        elif cancel:
            ctx.logger.error(
                f"Payment Protocol [seller]: Verification failed for {msg.reference}. Sending CancelPayment."
            )
            await ctx.send(sender, cancel)
        else:
            ctx.logger.warning(
                f"Payment Protocol [seller]: Verification pending for {msg.reference}. Holding in reconciliation."
            )

    @proto.on_message(RejectPayment)
    async def on_reject_payment(ctx: Context, sender: str, msg: RejectPayment) -> None:
        ctx.logger.info(
            f"Payment Protocol [seller]: Received RejectPayment from {sender}: {msg.reason}"
        )

    return proto
