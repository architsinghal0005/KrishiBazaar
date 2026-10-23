"""
Regression test suite for Booking-Specific Payment Audit Routing in KrishiBazaar.

Tests:
1. An existing confirmed booking is not reported as an unconfirmed proposal.
2. A booking-specific payment audit queries the exact booking reference.
3. No payment linked to the booking returns a clear not-found result:
   "No payment record found for booking KB-BOOK-20261023-93DB4D."
4. A linked payment is returned only if its booking association matches.
5. Historical payments from other bookings are never substituted (strict ledger isolation).
6. Expired proposals associated with confirmed bookings do not cause the booking to be reported as unconfirmed.
7. Proposal-specific payment audits (PROP-...) continue to work correctly.
8. Natural-language query variations:
   - "Check payment status for booking KB-BOOK-20261023-93DB4D"
   - "Audit payment readiness for KB-BOOK-20261023-93DB4D"
   - "Is payment enabled for booking KB-BOOK-20261023-93DB4D?"
   - "Does booking KB-BOOK-20261023-93DB4D have a payment record?"
9. Nonexistent booking reporting (KB-BOOK-NONEXISTENT).
10. The audit remains strictly read-only and never creates bookings, payments, or broadcasts transactions.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    MetadataContent,
)

from krishibazaar.config import settings
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    FarmerInquiry,
    NetReturnBreakdown,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.services.booking_service import (
    BookingActionType,
    create_booking_proposal,
    execute_simulated_booking,
    extract_booking_reference,
    extract_proposal_reference,
    is_payment_readiness_query,
    parse_booking_action,
)
from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path) -> StorageEngine:
    db_file = tmp_path / "booking_payment_audit_test.db"
    return StorageEngine(db_path=str(db_file))


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


@pytest.fixture
def sample_option() -> NetReturnBreakdown:
    return NetReturnBreakdown(
        scenario_id="scenario_lasalgaon",
        rank=1,
        channel_type="MANDI",
        destination_name="Lasalgaon APMC Mandi",
        distance_km=25.0,
        quantity_quintals=50.0,
        offered_price_per_quintal=1800.0,
        gross_value=90000.0,
        transport_cost=3000.0,
        applicable_market_charges=2000.0,
        storage_cost=0.0,
        estimated_loss_cost=0.0,
        total_deductions=5000.0,
        expected_net_return=85000.0,
        net_return_per_quintal=1700.0,
        price_provenance="agmarknet",
        is_price_simulated=True,
        meets_minimum_condition=True,
        recommendation_note="Optimal wholesale option for Onion.",
    )


@pytest.fixture
def confirmed_booking(sample_option: NetReturnBreakdown) -> BookingRecord:
    now_utc = datetime.now(timezone.utc)
    proposal = create_booking_proposal(
        session_id="agent1q_farmer_test_audit",
        farmer_id="agent1q_farmer_test_audit",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=(now_utc + timedelta(days=2)).strftime("%Y-%m-%d"),
    )
    booking = execute_simulated_booking(proposal)
    booking.booking_id = "KB-BOOK-20261023-93DB4D"
    booking.channel_name = "Kalyani Agro-Exports Pvt Ltd (Export Aggregator)"
    booking.notes = (
        "Simulated test booking created from proposal PROP-20261023-6B5C61. "
        "Channel: Kalyani Agro-Exports Pvt Ltd (Export Aggregator) (DIRECT_BUYER). "
        "Advisory estimate only; not a commercial contract."
    )
    return booking


# ==============================================================================
# TEST 1: Natural Language Variations Routing
# ==============================================================================
def test_booking_payment_audit_natural_language_variations():
    """Verify that all prompt variations extract the booking reference and route to payment check."""
    queries = [
        "Audit payment readiness for KB-BOOK-20261023-93DB4D",
        "Check payment status for booking KB-BOOK-20261023-93DB4D",
        "Is payment enabled for booking KB-BOOK-20261023-93DB4D?",
        "Does booking KB-BOOK-20261023-93DB4D have a payment record?",
        "Can payment readiness be checked for booking KB-BOOK-20261023-93DB4D?",
        "Check payment records linked specifically to booking KB-BOOK-20261023-93DB4D",
    ]
    for q in queries:
        action, ref = parse_booking_action(q)
        assert action == BookingActionType.PAYMENT_STATUS_LOOKUP
        assert ref == "KB-BOOK-20261023-93DB4D"


# ==============================================================================
# TEST 2: Confirmed Booking is NOT Reported as an Unconfirmed Proposal
# ==============================================================================
@pytest.mark.asyncio
async def test_confirmed_booking_not_reported_as_unconfirmed_proposal(
    temp_db: StorageEngine, mock_ctx, confirmed_booking: BookingRecord
):
    """
    When the farmer queries payment audit for an existing confirmed booking (KB-BOOK-20261023-93DB4D):
    The orchestrator MUST:
    - Report the booking exists and its status is CONFIRMED.
    - Explicitly report 'No payment record found for booking KB-BOOK-20261023-93DB4D.'
    - Report payment protocol is disabled (ENABLE_PAYMENT_PROTOCOL=False).
    - Report network (dorado-1) and method (fet_direct).
    - Report non-executing dry-run is supported.
    - State 'NO TRANSACTION INITIATED'.
    - NEVER say 'Proposal has NOT been confirmed into a booking'.
    - NEVER say 'Proposal Reference: None specified'.
    """
    temp_db.create_booking(confirmed_booking)
    farmer = confirmed_booking.farmer_id

    # Save session with this active booking
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=confirmed_booking.booking_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = "Audit payment readiness for KB-BOOK-20261023-93DB4D"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        assert mock_ctx.send.call_count >= 2
        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        # 1. Distinguish confirmed booking
        assert confirmed_booking.booking_id in reply
        assert "CONFIRMED" in reply
        assert "Proposal has NOT been confirmed" not in reply
        assert "Proposal Reference: None specified" not in reply
        assert "NO ACTIVE PROPOSAL" not in reply

        # 2. Precise payment not-found reporting
        assert "No payment record found for booking KB-BOOK-20261023-93DB4D" in reply

        # 3. Protocol status & runtime configuration
        assert "ENABLE_PAYMENT_PROTOCOL=False" in reply
        assert "dorado-1" in reply
        assert "fet_direct" in reply

        # 4. Dry-run and safety confirmation
        assert "SUPPORTED" in reply
        assert "NO TRANSACTION INITIATED" in reply


# ==============================================================================
# TEST 3: All 4 Prompt Variations Produce the Same Accurate Audit
# ==============================================================================
@pytest.mark.asyncio
async def test_all_prompt_variations_produce_booking_audit(
    temp_db: StorageEngine, mock_ctx, confirmed_booking: BookingRecord
):
    """Test all 4 natural-language variations mentioned in the prompt."""
    temp_db.create_booking(confirmed_booking)
    farmer = confirmed_booking.farmer_id

    queries = [
        "Check payment status for booking KB-BOOK-20261023-93DB4D",
        "Audit payment readiness for KB-BOOK-20261023-93DB4D",
        "Is payment enabled for booking KB-BOOK-20261023-93DB4D?",
        "Does booking KB-BOOK-20261023-93DB4D have a payment record?",
    ]

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        for q in queries:
            mock_ctx.send.reset_mock()
            msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=q)])
            await handle_chat_message(mock_ctx, farmer, msg)

            assert mock_ctx.send.call_count >= 2
            reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

            assert confirmed_booking.booking_id in reply
            assert "CONFIRMED" in reply
            assert "No payment record found for booking KB-BOOK-20261023-93DB4D" in reply
            assert "Proposal has NOT been confirmed" not in reply


# ==============================================================================
# TEST 4: Linked Payment is Returned When Associated Booking Matches
# ==============================================================================
@pytest.mark.asyncio
async def test_linked_payment_returned_when_booking_matches(
    temp_db: StorageEngine, mock_ctx, confirmed_booking: BookingRecord
):
    """When a payment record exists specifically for this booking, return its status."""
    temp_db.create_booking(confirmed_booking)
    farmer = confirmed_booking.farmer_id
    now_utc = datetime.now(timezone.utc)

    pay = PaymentRecord(
        payment_reference="KB-PAY-TEST-VALID-001",
        service_inquiry_id=confirmed_booking.booking_id,
        purpose="Farmer Produce Advisory Service Fee",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1payer_address",
        payee_agent_address=settings.payment_payee_agent_address or "agent1payee",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=50_000_000_000_000_000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        deadline_seconds=300,
        status=PaymentStatus.SETTLED,
        tx_hash="0xABC123VALIDHASH",
        tx_height=99999,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(pay)

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = f"Audit payment readiness for {confirmed_booking.booking_id}"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        assert mock_ctx.send.call_count >= 2
        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        assert "KB-PAY-TEST-VALID-001" in reply
        assert "0.05 FET" in reply
        assert "SETTLED" in reply
        assert "0xABC123VALIDHASH" in reply


# ==============================================================================
# TEST 5: Strict Record Isolation — Historical Payments Never Substituted
# ==============================================================================
@pytest.mark.asyncio
async def test_strict_record_isolation_historical_payments_never_substituted(
    temp_db: StorageEngine, mock_ctx, confirmed_booking: BookingRecord, sample_option: NetReturnBreakdown
):
    """
    Storage contains a historical settled payment (KB-PAY-OLD-999) from another booking (KB-BOOK-OLD-888).
    When querying payment readiness for the new booking (KB-BOOK-20261023-93DB4D),
    the system MUST report 'No payment record found for booking KB-BOOK-20261023-93DB4D.'
    and MUST NEVER substitute the historical payment.
    """
    temp_db.create_booking(confirmed_booking)
    farmer = confirmed_booking.farmer_id
    now_utc = datetime.now(timezone.utc)

    # Save old historical booking and payment
    old_booking = execute_simulated_booking(
        create_booking_proposal(
            session_id=farmer,
            farmer_id=farmer,
            selected_option=sample_option,
            crop="Onion",
            quantity_quintals=50.0,
            origin_location="Nashik, Maharashtra",
            transit_date=(now_utc + timedelta(days=2)).strftime("%Y-%m-%d"),
        )
    )
    old_booking.booking_id = "KB-BOOK-OLD-888"
    temp_db.create_booking(old_booking)

    hist_pay = PaymentRecord(
        payment_reference="KB-PAY-OLD-999",
        service_inquiry_id="KB-BOOK-OLD-888",
        purpose="Farmer Produce Advisory Service Fee",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1payer_wallet",
        payee_agent_address=settings.payment_payee_agent_address or "agent1payee",
        payee_wallet_address="fetch1payee_wallet",
        amount_base_units=50_000_000_000_000_000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        deadline_seconds=300,
        status=PaymentStatus.SETTLED,
        tx_hash="0xHISTORICAL_SETTLED_TX",
        tx_height=123456,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(hist_pay)

    # Lingering session-level reference (stale session simulation)
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=confirmed_booking.booking_id,
        pending_payment_reference="KB-PAY-OLD-999",
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = f"Check payment status for booking {confirmed_booking.booking_id}"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        # Must report no payment found for the requested booking
        assert f"No payment record found for booking {confirmed_booking.booking_id}" in reply
        # Must NEVER substitute the historical payment
        assert "KB-PAY-OLD-999" not in reply
        assert "KB-BOOK-OLD-888" not in reply
        assert "0xHISTORICAL_SETTLED_TX" not in reply


# ==============================================================================
# TEST 6: Expired Proposal Does NOT Mark Confirmed Booking as Unconfirmed
# ==============================================================================
@pytest.mark.asyncio
async def test_expired_proposal_does_not_mark_booking_unconfirmed(
    temp_db: StorageEngine, mock_ctx, confirmed_booking: BookingRecord, sample_option: NetReturnBreakdown
):
    """
    A proposal expired, but was confirmed earlier into KB-BOOK-20261023-93DB4D.
    Checking the booking payment audit must report the booking is CONFIRMED,
    not infer that it is unconfirmed because the proposal has expired.
    """
    now_utc = datetime.now(timezone.utc)
    expired_prop = create_booking_proposal(
        session_id=confirmed_booking.farmer_id,
        farmer_id=confirmed_booking.farmer_id,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=(now_utc + timedelta(days=2)).strftime("%Y-%m-%d"),
    )
    expired_prop.proposal_id = "PROP-20261023-6B5C61"
    expired_prop.created_at_utc = (now_utc - timedelta(minutes=30)).isoformat()
    expired_prop.expires_at_utc = (now_utc - timedelta(minutes=15)).isoformat()
    expired_prop.is_consumed = True
    temp_db.save_proposal(expired_prop)

    confirmed_booking.offer_id = "PROP-20261023-6B5C61"
    temp_db.create_booking(confirmed_booking)

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = f"Audit payment readiness for {confirmed_booking.booking_id}"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, confirmed_booking.farmer_id, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        assert confirmed_booking.booking_id in reply
        assert "CONFIRMED" in reply
        assert "Proposal has NOT been confirmed" not in reply
        assert "No payment record found for booking KB-BOOK-20261023-93DB4D" in reply


# ==============================================================================
# TEST 7: Nonexistent Booking Reporting
# ==============================================================================
@pytest.mark.asyncio
async def test_nonexistent_booking_reporting(temp_db: StorageEngine, mock_ctx):
    """If the booking does not exist, report NOT FOUND."""
    farmer = "agent1q_farmer_test"
    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = "Audit payment readiness for KB-BOOK-NONEXISTENT-999"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "KB-BOOK-NONEXISTENT-999" in reply
        assert "NOT FOUND" in reply


# ==============================================================================
# TEST 8: Proposal-Specific Audits (PROP-...) Continue to Work Correctly
# ==============================================================================
@pytest.mark.asyncio
async def test_proposal_specific_payment_audits_continue_to_work(
    temp_db: StorageEngine, mock_ctx, sample_option: NetReturnBreakdown
):
    """Proposal queries must route to the proposal payment audit handler."""
    now_utc = datetime.now(timezone.utc)
    farmer = "agent1q_farmer_prop_audit"
    prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=(now_utc + timedelta(days=2)).strftime("%Y-%m-%d"),
    )
    prop.proposal_id = "PROP-20261023-2FCD0E"
    temp_db.save_proposal(prop)

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = "Check payment readiness for proposal PROP-20261023-2FCD0E"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "PROP-20261023-2FCD0E" in reply
        assert "Proposal Context & Validity Window" in reply
        assert "ENABLE_PAYMENT_PROTOCOL=False" in reply


# ==============================================================================
# TEST 9: General Payment Subsystem Questions Continue to Work
# ==============================================================================
@pytest.mark.asyncio
async def test_general_payment_subsystem_queries_continue_to_work(
    temp_db: StorageEngine, mock_ctx
):
    """General readiness questions without proposal or booking reference work properly."""
    farmer = "agent1q_farmer_general_audit"
    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        queries = [
            "Is the payment agent operational?",
            "Is payment execution enabled?",
        ]
        for q in queries:
            mock_ctx.send.reset_mock()
            msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=q)])
            await handle_chat_message(mock_ctx, farmer, msg)

            reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
            assert "Payment Subsystem & Service Readiness" in reply
            assert "ENABLE_PAYMENT_PROTOCOL=False" in reply


# ==============================================================================
# TEST 10: Strictly Read-Only (No Bookings or Payments Created)
# ==============================================================================
@pytest.mark.asyncio
async def test_booking_payment_audit_is_strictly_read_only(
    temp_db: StorageEngine, mock_ctx, confirmed_booking: BookingRecord
):
    """Running payment audits must never create bookings, payments, or modify storage."""
    temp_db.create_booking(confirmed_booking)
    farmer = confirmed_booking.farmer_id

    b_before = len(temp_db.list_bookings())
    with temp_db._get_connection() as conn:
        p_before = conn.execute("SELECT COUNT(*) FROM pending_proposals").fetchone()[0]

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        for _ in range(3):
            query = f"Audit payment readiness for {confirmed_booking.booking_id}"
            msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
            await handle_chat_message(mock_ctx, farmer, msg)

    assert len(temp_db.list_bookings()) == b_before
    with temp_db._get_connection() as conn:
        p_after = conn.execute("SELECT COUNT(*) FROM pending_proposals").fetchone()[0]
    assert p_after == p_before
    assert temp_db.get_payment_record_by_inquiry(confirmed_booking.booking_id) is None
