"""
Regression & Unit Tests for KrishiBazaar Proposal-Specific Payment Readiness Routing
(tests/test_proposal_payment_readiness_routing.py).

Verifies:
1. Proposal-specific payment-readiness queries (e.g. 'Check payment readiness for proposal PROP-20261023-2FCD0E.').
2. Repeated audit requests without asking for crop details again.
3. Expired proposal references explicitly reported as expired without silently restarting workflow.
4. Missing / unknown proposal references explicitly reported as not found.
5. Queries about payment agent operational status and payment execution enabled without proposal reference.
6. Queries about whether payment readiness can be checked before booking confirmation.
7. Correct reporting of disabled payment protocol (ENABLE_PAYMENT_PROTOCOL=False), network, and method.
8. Strict ledger isolation: historical settled payments from previous bookings are never substituted.
9. Read-only safety: payment readiness queries do not create bookings, payments, or broadcast transactions.
10. Existing session isolation and approval-gate behavior remain intact.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    MetadataContent,
)

from krishibazaar.agents.orchestrator import handle_chat_message
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
    is_payment_readiness_query,
    extract_proposal_reference,
    parse_booking_action,
)
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path) -> StorageEngine:
    db_file = tmp_path / "payment_readiness_test.db"
    return StorageEngine(db_path=str(db_file))


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


@pytest.fixture
def sample_option():
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
def active_proposal(sample_option):
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")
    prop = create_booking_proposal(
        session_id="agent1q_farmer_audit_test",
        farmer_id="agent1q_farmer_audit_test",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    prop.proposal_id = "PROP-20261023-2FCD0E"
    return prop


# ==============================================================================
# TEST 1: Natural-Language Intent and Reference Extraction Parsing
# ==============================================================================
def test_payment_readiness_intent_parsing():
    """Verify that all user-specified payment-readiness phrases are parsed correctly."""
    # Proposal-specific queries
    action, prop = parse_booking_action("Check payment readiness for proposal PROP-20261023-2FCD0E.")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop == "PROP-20261023-2FCD0E"

    action, prop = parse_booking_action("Check payment readiness for PROP-20261023-2FCD0E")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop == "PROP-20261023-2FCD0E"

    action, prop = parse_booking_action("Payment readiness audit for proposal PROP-20261023-2FCD0E")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop == "PROP-20261023-2FCD0E"

    action, prop = parse_booking_action("Is payment ready for proposal PROP-20261023-2FCD0E?")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop == "PROP-20261023-2FCD0E"

    # Subsystem / Agent operational queries without proposal reference
    action, prop = parse_booking_action("Is the payment agent operational?")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop is None

    action, prop = parse_booking_action("Is payment execution enabled?")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop is None

    action, prop = parse_booking_action("Can payment readiness be checked for this proposal without confirming a booking?")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop is None

    action, prop = parse_booking_action("Is payment protocol active?")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop is None

    action, prop = parse_booking_action("Check payment readiness")
    assert action == BookingActionType.PAYMENT_READINESS_AUDIT
    assert prop is None

    # Verify helper directly
    assert is_payment_readiness_query("Check payment readiness for proposal PROP-20261023-2FCD0E.") is True
    assert extract_proposal_reference("Check payment readiness for proposal PROP-20261023-2FCD0E.") == "PROP-20261023-2FCD0E"


# ==============================================================================
# TEST 2: Proposal-Specific Payment Readiness Query Success
# ==============================================================================
@pytest.mark.asyncio
async def test_proposal_specific_payment_readiness_query_success(
    temp_db: StorageEngine, mock_ctx, active_proposal: BookingProposal
):
    """
    Farmer asks: 'Check payment readiness for proposal PROP-20261023-2FCD0E.'
    Orchestrator must:
    1. NOT ask for crop/quantity clarification.
    2. Report proposal context (PROP-20261023-2FCD0E, 50 qtl Onion, Lasalgaon APMC).
    3. Report proposal status as ACTIVE & VALID within 15-minute validity window.
    4. Report payment protocol as disabled (ENABLE_PAYMENT_PROTOCOL=False).
    5. Report payment service initialized and payment specialist agent configured.
    6. Report associated booking: None, associated payment: None.
    7. State dry-run is supported.
    """
    temp_db.save_proposal(active_proposal)
    farmer = active_proposal.farmer_id

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        query = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Check payment readiness for proposal PROP-20261023-2FCD0E.")],
        )
        await handle_chat_message(mock_ctx, farmer, query)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        # Must NOT ask the onboarding crop question
        assert "Could you please tell me which crop you want to sell" not in reply
        assert "approximate quantity in quintals" not in reply

        # Must report proposal context
        assert "PROP-20261023-2FCD0E" in reply
        assert "Onion" in reply
        assert "50 quintals" in reply
        assert "ACTIVE & VALID" in reply
        assert "15-minute validity window" in reply

        # Must report payment protocol disabled
        assert "DISABLED" in reply
        assert "ENABLE_PAYMENT_PROTOCOL=False" in reply

        # Must report payment subsystem status
        assert "Payment Service Subsystem" in reply
        assert "Payment Specialist Agent" in reply
        assert "Fetch.ai Dorado Testnet" in reply
        assert "dorado-1" in reply
        assert "fet_direct" in reply

        # Must report associated records as None
        assert "Associated Booking Record:** None" in reply
        assert "Associated Payment Record:** None" in reply

        # Must state dry-run support and next safe step
        assert "SUPPORTED" in reply
        assert "Confirm booking" in reply


# ==============================================================================
# TEST 3: Repeated Audit Requests Do Not Ask Crop Details Again
# ==============================================================================
@pytest.mark.asyncio
async def test_repeated_audit_requests_do_not_ask_crop_details(
    temp_db: StorageEngine, mock_ctx, active_proposal: BookingProposal
):
    """
    Farmer sends repeated payment readiness requests in the same session.
    The Orchestrator must NEVER fall back to asking for crop details.
    """
    temp_db.save_proposal(active_proposal)
    farmer = active_proposal.farmer_id

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        for turn in range(3):
            mock_ctx.send.reset_mock()
            query = ChatMessage(
                msg_id=uuid4(),
                content=[TextContent(text="Check payment readiness for proposal PROP-20261023-2FCD0E.")],
            )
            await handle_chat_message(mock_ctx, farmer, query)

            reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
            assert "Could you please tell me which crop you want to sell" not in reply
            assert "PROP-20261023-2FCD0E" in reply
            assert "Payment Readiness" in reply


# ==============================================================================
# TEST 4: Expired Proposal References Explicitly Reported as Expired
# ==============================================================================
@pytest.mark.asyncio
async def test_expired_proposal_payment_readiness_audit(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    If a proposal is older than 15 minutes, the audit must explicitly report that it has expired.
    It must NOT silently restart the farmer workflow or invent a new proposal reference.
    """
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    expired_proposal = create_booking_proposal(
        session_id="agent1q_farmer_expired_test",
        farmer_id="agent1q_farmer_expired_test",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    expired_proposal.proposal_id = "PROP-20261023-EXPIRED1"
    expired_proposal.created_at_utc = (now_utc - timedelta(minutes=20)).isoformat()
    expired_proposal.expires_at_utc = (now_utc - timedelta(minutes=5)).isoformat()
    temp_db.save_proposal(expired_proposal)
    farmer = expired_proposal.farmer_id

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        query = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Check payment readiness for proposal PROP-20261023-EXPIRED1.")],
        )
        await handle_chat_message(mock_ctx, farmer, query)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        # Must explicitly report proposal has EXPIRED
        assert "EXPIRED" in reply
        assert "PROP-20261023-EXPIRED1" in reply
        assert "15-minute validity window" in reply
        # Must not ask onboarding crop questions
        assert "Could you please tell me which crop you want to sell" not in reply


# ==============================================================================
# TEST 5: Missing / Unknown Proposal References Explicitly Reported as Not Found
# ==============================================================================
@pytest.mark.asyncio
async def test_missing_proposal_payment_readiness_audit(
    temp_db: StorageEngine, mock_ctx
):
    """
    When user asks about an unknown proposal ID, report NOT FOUND.
    Do NOT fall back to older proposals or bookings.
    """
    farmer = "agent1q_farmer_missing_test"

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        query = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Check payment readiness for proposal PROP-20261023-NOTFOUND.")],
        )
        await handle_chat_message(mock_ctx, farmer, query)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "NOT FOUND" in reply
        assert "PROP-20261023-NOTFOUND" in reply
        assert "Associated Booking Record:** None" in reply
        assert "Could you please tell me which crop you want to sell" not in reply


# ==============================================================================
# TEST 6: General Payment Readiness Queries Without Proposal Reference
# ==============================================================================
@pytest.mark.asyncio
async def test_general_payment_readiness_queries_without_proposal_reference(
    temp_db: StorageEngine, mock_ctx
):
    """
    Queries like 'Is the payment agent operational?' and 'Is payment execution enabled?'
    must report configuration and operational status without asking crop questions.
    """
    farmer = "agent1q_farmer_general_audit"

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Sub-query 1: Is the payment agent operational?
        query1 = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Is the payment agent operational?")])
        await handle_chat_message(mock_ctx, farmer, query1)
        reply1 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Payment Specialist Agent" in reply1
        assert "Payment Service Subsystem" in reply1
        assert "Could you please tell me which crop you want to sell" not in reply1

        # Sub-query 2: Is payment execution enabled?
        mock_ctx.send.reset_mock()
        query2 = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Is payment execution enabled?")])
        await handle_chat_message(mock_ctx, farmer, query2)
        reply2 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Payment Protocol Execution" in reply2
        assert "ENABLE_PAYMENT_PROTOCOL=False" in reply2
        assert "Could you please tell me which crop you want to sell" not in reply2


# ==============================================================================
# TEST 7: Check Readiness Without Confirming Booking Resolves Session Proposal
# ==============================================================================
@pytest.mark.asyncio
async def test_check_readiness_without_confirming_booking_with_session_proposal(
    temp_db: StorageEngine, mock_ctx, active_proposal: BookingProposal
):
    """
    When farmer has an active proposal in session and asks:
    'Can payment readiness be checked for this proposal without confirming a booking?'
    The Orchestrator must:
    1. Resolve the session's active proposal.
    2. Explain the implementation limitation (payment requests are created post-booking confirmation).
    3. State that dry-run is supported.
    4. Provide the next safe step (reply 'Confirm booking' or 'Approve proposal').
    5. NOT confirm the booking automatically.
    """
    temp_db.save_proposal(active_proposal)
    farmer = active_proposal.farmer_id

    # Save session with active proposal
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="AWAITING_APPROVAL",
        active_proposal_id=active_proposal.proposal_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        query = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Can payment readiness be checked for this proposal without confirming a booking?")],
        )
        await handle_chat_message(mock_ctx, farmer, query)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        assert "PROP-20261023-2FCD0E" in reply
        assert "Architecture Limitation" in reply or "cannot be created or authorized prior to booking confirmation" in reply
        assert "SUPPORTED" in reply
        assert "Confirm booking" in reply

        # SAFETY GATE VERIFICATION: NO booking record must have been created!
        bookings = temp_db.list_bookings(farmer_id=farmer)
        assert len(bookings) == 0


# ==============================================================================
# TEST 8: Strict Record Isolation: Historical Settled Payments Never Substituted
# ==============================================================================
@pytest.mark.asyncio
async def test_strict_record_isolation_historical_payment_never_substituted(
    temp_db: StorageEngine, mock_ctx, active_proposal: BookingProposal, sample_option: NetReturnBreakdown
):
    """
    Storage contains a historical settled payment (KB-PAY-HISTORICAL-999) from another booking.
    When checking payment readiness for a new unconfirmed proposal, the audit must report
    Associated Payment Record: None, and MUST NOT substitute the historical settled payment.
    """
    temp_db.save_proposal(active_proposal)
    farmer = active_proposal.farmer_id

    # Create historical settled booking and payment
    old_proposal = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=(datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d"),
    )
    old_booking = execute_simulated_booking(old_proposal)
    old_booking.booking_id = "KB-BOOK-OLD-888888"
    temp_db.create_booking(old_booking)

    hist_pay = PaymentRecord(
        payment_reference="KB-PAY-HISTORICAL-999",
        service_inquiry_id="KB-BOOK-OLD-888888",
        purpose="Farmer Produce Advisory Service Fee",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1payer_wallet",
        payee_agent_address=settings.payment_payee_agent_address or "agent1payee",
        payee_wallet_address="fetch1payee_wallet",
        amount_base_units=50_000_000_000_000_000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="testnet",
        deadline_seconds=300,
        status=PaymentStatus.SETTLED,
        tx_hash="0xHISTORICAL_TX_HASH_888",
        tx_height=123456,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        updated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    temp_db.save_payment_record(hist_pay)

    # Session contains historical reference in pending slot (simulating stale session)
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="AWAITING_APPROVAL",
        active_proposal_id=active_proposal.proposal_id,
        pending_payment_reference="KB-PAY-HISTORICAL-999",
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        query = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Check payment readiness for proposal PROP-20261023-2FCD0E.")],
        )
        await handle_chat_message(mock_ctx, farmer, query)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        # Must NOT show historical payment as associated payment
        assert "KB-PAY-HISTORICAL-999" not in reply
        assert "Associated Payment Record:** None" in reply
        assert "Associated Booking Record:** None" in reply


# ==============================================================================
# TEST 9: Read-Only Audit Never Creates Booking or Payment Records
# ==============================================================================
@pytest.mark.asyncio
async def test_read_only_audit_never_creates_booking_or_payment(
    temp_db: StorageEngine, mock_ctx, active_proposal: BookingProposal
):
    """
    Verify read-only safety: Performing a payment readiness audit must NOT create
    any records in booking_ledger or payment_records.
    """
    temp_db.save_proposal(active_proposal)
    farmer = active_proposal.farmer_id

    bookings_before = len(temp_db.list_bookings())
    payments_before = len(temp_db.list_payment_records())

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        query = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Check payment readiness for proposal PROP-20261023-2FCD0E.")],
        )
        await handle_chat_message(mock_ctx, farmer, query)

        bookings_after = len(temp_db.list_bookings())
        payments_after = len(temp_db.list_payment_records())

        assert bookings_after == bookings_before == 0
        assert payments_after == payments_before == 0


# ==============================================================================
# TEST 10: Approval Gate Unaffected: Explicit Approval Confirms Booking
# ==============================================================================
@pytest.mark.asyncio
async def test_approval_gate_unaffected_explicit_approval_confirms_booking(
    temp_db: StorageEngine, mock_ctx, active_proposal: BookingProposal
):
    """
    After auditing payment readiness, when the farmer explicitly replies 'Confirm booking',
    the proposal MUST be approved and recorded into booking_ledger.
    """
    temp_db.save_proposal(active_proposal)
    farmer = active_proposal.farmer_id
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="AWAITING_APPROVAL",
        active_proposal_id=active_proposal.proposal_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Turn 1: Payment readiness audit
        audit_query = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Check payment readiness for proposal PROP-20261023-2FCD0E.")],
        )
        await handle_chat_message(mock_ctx, farmer, audit_query)
        assert len(temp_db.list_bookings(farmer_id=farmer)) == 0

        # Turn 2: Explicit confirmation
        mock_ctx.send.reset_mock()
        confirm_query = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Confirm booking")],
        )
        await handle_chat_message(mock_ctx, farmer, confirm_query)

        reply2 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Simulated Booking Confirmed" in reply2 or "Booking Confirmed" in reply2

        # Exactly 1 booking now recorded in booking ledger
        bookings = temp_db.list_bookings(farmer_id=farmer)
        assert len(bookings) == 1
        assert "PROP-20261023-2FCD0E" in bookings[0].notes
        assert bookings[0].crop == "Onion"
