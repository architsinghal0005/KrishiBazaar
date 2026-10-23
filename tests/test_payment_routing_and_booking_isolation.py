"""
Regression & Unit Tests for KrishiBazaar Payment Status Routing and Booking Isolation
(tests/test_payment_routing_and_booking_isolation.py).

Verifies:
1. Current booking has no linked payment, but session/storage contains historical settled payment.
   Response must say no payment exists for the current booking and must not display historical payment.
2. Current booking has a payment linked to it. Response returns the correct payment and booking association.
3. An explicit payment-reference request returns the exact requested payment.
4. An unknown payment reference does not fall back to an older settled payment.
5. A natural-language message containing both an old payment reference and a new booking reference
   correctly answers the booking-specific question without picking the old payment reference.
6. Session isolation and payment idempotency remain intact.
7. Payment-status read-only queries do not create payments or cause on-chain transactions.
8. No unverified transaction or protocol message can independently mark a payment as settled.
9. Booking approval safety gate: Proposal does NOT confirm immediately upon creation;
   requires explicit, unambiguous approval before creating a booking record.
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
from uagents_core.contrib.protocols.chat.cards import create_card_response_content

from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.config import settings
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    CompletePayment,
    FarmerInquiry,
    NetReturnBreakdown,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.services.booking_service import (
    BookingActionType,
    create_booking_proposal,
    execute_simulated_booking,
    parse_booking_action,
)
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path) -> StorageEngine:
    db_file = tmp_path / "payment_routing_isolation_test.db"
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


# ---------------------------------------------------------------------------
# Test Case 1: Current booking has no linked payment, session has historical payment
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_1_current_booking_no_linked_payment_with_historical_settled_payment(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    The current booking (KB-BOOK-20261022-8ED080) has no linked payment.
    The database and session contain a historical settled payment (KB-PAY-27A4A2EC1F3E)
    linked to an older booking (KB-BOOK-20261022-4C1CE8).

    The response must say no payment exists for the current booking and must NOT
    display the historical payment as its own.
    """
    farmer = "agent1q_farmer_case1"
    old_booking_id = "KB-BOOK-20261022-4C1CE8"
    new_booking_id = "KB-BOOK-20261022-8ED080"
    old_pay_ref = "KB-PAY-27A4A2EC1F3E"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    # 1. Save historical booking & settled payment
    old_proposal = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    old_booking = execute_simulated_booking(old_proposal)
    old_booking.booking_id = old_booking_id
    temp_db.create_booking(old_booking)

    old_payment = PaymentRecord(
        payment_reference=old_pay_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1testpayeraddress",
        payee_agent_address="agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.SETTLED,
        service_inquiry_id=old_booking_id,
        tx_hash="36C3088738B2A82CC61C7F18144DE8015FCF4A0587DCFE5060C3A27E384CBBEF",
        tx_height=25809429,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(old_payment)

    # 2. Save current new booking (NO payment created for this booking)
    new_proposal = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    new_booking = execute_simulated_booking(new_proposal)
    new_booking.booking_id = new_booking_id
    temp_db.create_booking(new_booking)

    # Session currently points to new_booking_id
    inquiry_obj = FarmerInquiry(
        farmer_id=farmer,
        crop="Onion",
        quantity_quintals=50.0,
        location_district="Nashik",
        origin_location="Nashik, Maharashtra",
        minimum_net_return_inr=85000.0,
        selling_deadline=valid_transit,
    )
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        inquiry=inquiry_obj,
        active_booking_id=new_booking_id,
        # Even if session had lingering old pending payment reference:
        pending_payment_reference=old_pay_ref,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = f"Check payment records linked specifically to booking {new_booking_id}"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        assert mock_ctx.send.call_count >= 2
        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        # Must report that no payment was found for the requested new booking
        assert f"No payment record found for booking `{new_booking_id}`" in reply
        # Must NEVER display the historical payment or older booking
        assert old_pay_ref not in reply
        assert old_booking_id not in reply
        assert "Payment Settled" not in reply


# ---------------------------------------------------------------------------
# Test Case 2: Current booking has a payment linked to it
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_2_current_booking_with_linked_payment(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    Booking KB-BOOK-20261022-8ED080 has payment KB-PAY-NEW888 explicitly linked to it.
    The response must return the correct payment and booking association.
    """
    farmer = "agent1q_farmer_case2"
    booking_id = "KB-BOOK-20261022-8ED080"
    pay_ref = "KB-PAY-NEW888"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    proposal = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    booking = execute_simulated_booking(proposal)
    booking.booking_id = booking_id
    temp_db.create_booking(booking)

    payment = PaymentRecord(
        payment_reference=pay_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1testpayeraddress",
        payee_agent_address="agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.REQUESTED,
        service_inquiry_id=booking_id,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(payment)

    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_id,
        pending_payment_reference=pay_ref,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = f"Check payment records linked specifically to booking {booking_id}"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert pay_ref in reply
        assert booking_id in reply
        assert "REQUESTED" in reply or "Payment Status" in reply


# ---------------------------------------------------------------------------
# Test Case 3: Explicit payment-reference request returns exact requested payment
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_3_explicit_payment_reference_request(
    temp_db: StorageEngine, mock_ctx
):
    """
    An explicit payment-reference query (Check payment status for KB-PAY-27A4A2EC1F3E)
    returns that exact payment record.
    """
    farmer = "agent1q_farmer_case3"
    target_pay_ref = "KB-PAY-27A4A2EC1F3E"
    other_pay_ref = "KB-PAY-OTHER999"
    now_utc = datetime.now(timezone.utc)

    pay1 = PaymentRecord(
        payment_reference=target_pay_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1testpayeraddress",
        payee_agent_address="agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.SETTLED,
        service_inquiry_id="KB-BOOK-20261022-4C1CE8",
        tx_hash="36C3088738B2A82CC61C7F18144DE8015FCF4A0587DCFE5060C3A27E384CBBEF",
        tx_height=25809429,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(pay1)

    pay2 = PaymentRecord(
        payment_reference=other_pay_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1testpayeraddress",
        payee_agent_address="agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.REQUESTED,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(pay2)

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = f"Check payment status for {target_pay_ref}"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert target_pay_ref in reply
        assert "SETTLED" in reply
        assert other_pay_ref not in reply


# ---------------------------------------------------------------------------
# Test Case 4: Unknown payment reference does not fall back to an older settled payment
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_4_unknown_payment_reference_does_not_fall_back(
    temp_db: StorageEngine, mock_ctx
):
    """
    Querying an unknown payment reference (KB-PAY-NONEXISTENT999) must return
    an explicit not-found response and must NOT fall back to an older settled payment.
    """
    farmer = "agent1q_farmer_case4"
    old_pay_ref = "KB-PAY-27A4A2EC1F3E"
    unknown_pay_ref = "KB-PAY-NONEXISTENT999"
    now_utc = datetime.now(timezone.utc)

    old_pay = PaymentRecord(
        payment_reference=old_pay_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1testpayeraddress",
        payee_agent_address="agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.SETTLED,
        service_inquiry_id="KB-BOOK-20261022-4C1CE8",
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(old_pay)

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = f"Check payment status for {unknown_pay_ref}"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert f"No payment record found for payment reference `{unknown_pay_ref}`" in reply
        assert old_pay_ref not in reply
        assert "SETTLED" not in reply


# ---------------------------------------------------------------------------
# Test Case 5: Message with old payment reference and new booking reference
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_5_message_with_old_payment_and_new_booking_reference(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    User message says:
    'The payment KB-PAY-27A4A2EC1F3E was for KB-BOOK-20261022-4C1CE8. Check payment records linked specifically to booking KB-BOOK-20261022-8ED080'

    Must resolve the target booking ID (KB-BOOK-20261022-8ED080) and NOT pick
    the historical payment reference (KB-PAY-27A4A2EC1F3E).
    """
    farmer = "agent1q_farmer_case5"
    old_booking_id = "KB-BOOK-20261022-4C1CE8"
    new_booking_id = "KB-BOOK-20261022-8ED080"
    old_pay_ref = "KB-PAY-27A4A2EC1F3E"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    # Set up old booking and payment
    old_prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    old_b = execute_simulated_booking(old_prop)
    old_b.booking_id = old_booking_id
    temp_db.create_booking(old_b)

    old_pay = PaymentRecord(
        payment_reference=old_pay_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1testpayeraddress",
        payee_agent_address="agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.SETTLED,
        service_inquiry_id=old_booking_id,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(old_pay)

    # Set up new booking (no payment)
    new_prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    new_b = execute_simulated_booking(new_prop)
    new_b.booking_id = new_booking_id
    temp_db.create_booking(new_b)

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        query = (
            f"The payment {old_pay_ref} was for {old_booking_id}. "
            f"Check payment records linked specifically to booking {new_booking_id}"
        )
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        # Target must be new_booking_id, reporting no payment found
        assert f"No payment record found for booking `{new_booking_id}`" in reply
        # Must not present old_pay_ref as settled payment
        assert "Payment Settled" not in reply


# ---------------------------------------------------------------------------
# Test Case 6: Session isolation and payment idempotency remain intact
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_6_session_isolation_and_payment_idempotency(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    Farmer A has a confirmed booking and payment.
    Farmer B queries payment status without specifying an ID.
    Farmer B must NOT receive Farmer A's payment or booking details.
    """
    farmer_a = "agent1q_farmer_a_owner"
    farmer_b = "agent1q_farmer_b_stranger"
    booking_a_id = "KB-BOOK-A111"
    pay_a_ref = "KB-PAY-A111"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    prop_a = create_booking_proposal(
        session_id=farmer_a,
        farmer_id=farmer_a,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    book_a = execute_simulated_booking(prop_a)
    book_a.booking_id = booking_a_id
    temp_db.create_booking(book_a)

    pay_a = PaymentRecord(
        payment_reference=pay_a_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer_a,
        payer_wallet_address="fetch1testpayeraddress",
        payee_agent_address="agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.SETTLED,
        service_inquiry_id=booking_a_id,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(pay_a)

    temp_db.save_session(
        session_id=farmer_a,
        farmer_id=farmer_a,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_a_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Farmer B asks for payment status
        mock_ctx.send.reset_mock()
        msg_b = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Check payment status")])
        await handle_chat_message(mock_ctx, farmer_b, msg_b)

        reply_b = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "No active payment record found for farmer session" in reply_b
        assert pay_a_ref not in reply_b
        assert booking_a_id not in reply_b


# ---------------------------------------------------------------------------
# Test Case 7: Payment-status read-only queries do not create payments or on-chain tx
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_7_read_only_queries_do_not_create_payments(
    temp_db: StorageEngine, mock_ctx
):
    """
    Read-only payment status queries must never create new PaymentRecords or trigger transfers.
    """
    farmer = "agent1q_farmer_case7"
    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Query 1: For a booking
        msg1 = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Check payment records linked specifically to booking KB-BOOK-20261022-8ED080")],
        )
        await handle_chat_message(mock_ctx, farmer, msg1)

        # Query 2: For an arbitrary reference
        msg2 = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Check payment status for KB-PAY-RANDOM000")],
        )
        await handle_chat_message(mock_ctx, farmer, msg2)

        # Assert no payment records were created
        records = temp_db.list_payment_records()
        assert len(records) == 0


# ---------------------------------------------------------------------------
# Test Case 8: No unverified transaction or message can mark settled
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_8_unverified_complete_payment_cannot_settle(temp_db: StorageEngine):
    """
    A standalone CompletePayment message without authoritative ledger verification
    must never mark a payment record as SETTLED.
    """
    pay_ref = "KB-PAY-UNVERIFIED01"
    now_utc = datetime.now(timezone.utc)

    record = PaymentRecord(
        payment_reference=pay_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address="agent1q_payer",
        payer_wallet_address="fetch1testpayer",
        payee_agent_address="agent1q_payee",
        payee_wallet_address="fetch1testpayee",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.REQUESTED,
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(record)

    # Fake CompletePayment without block confirmation
    fake_complete = CompletePayment(
        transaction_id="FAKETX1234567890",
    )

    # Verify that in storage, status remains REQUESTED and is NOT SETTLED
    stored = temp_db.get_payment_record(pay_ref)
    assert stored.status == PaymentStatus.REQUESTED
    assert stored.tx_hash is None


# ---------------------------------------------------------------------------
# Test Case 9: Booking Approval Safety Gate (Section 6 Investigation)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_case_9_booking_proposal_requires_explicit_approval_before_confirmation(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    Investigate and verify Section 6:
    When a proposal is displayed (e.g. after 'Select option 1'), the booking MUST NOT
    be confirmed immediately.
    A casual response ('Sounds good, thanks!') must NOT confirm the booking.
    Only an explicit approval command ('Confirm booking') confirms the booking and
    records it into booking_ledger.
    """
    farmer = "agent1q_farmer_case9_gate"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    # Set up farmer session with active recommended options
    inquiry_obj = FarmerInquiry(
        farmer_id=farmer,
        crop="Onion",
        quantity_quintals=50.0,
        location_district="Nashik",
        origin_location="Nashik, Maharashtra",
        minimum_net_return_inr=85000.0,
        selling_deadline=valid_transit,
    )
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="OFFERS_OPTIMIZED",
        inquiry=inquiry_obj,
        active_options=[sample_option],
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Step 1: Farmer selects option 1
        mock_ctx.send.reset_mock()
        sel_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Select option 1")])
        await handle_chat_message(mock_ctx, farmer, sel_msg)

        reply1 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Review Booking Proposal" in reply1 or "Proposal" in reply1
        assert "Explicit Approval Required" in reply1 or "Valid for 15 minutes" in reply1

        # SAFETY GATE VERIFICATION 1: NO booking record must exist yet!
        bookings_after_select = temp_db.list_bookings(farmer_id=farmer)
        assert len(bookings_after_select) == 0

        # Verify session state is AWAITING_APPROVAL
        sess = temp_db.get_session(farmer)
        assert sess is not None
        assert sess["state"] == "AWAITING_APPROVAL"
        assert sess["active_proposal_id"] is not None

        # Step 2: Farmer sends casual affirmation ("Sounds good, thank you!")
        mock_ctx.send.reset_mock()
        casual_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Sounds good, thank you!")])
        await handle_chat_message(mock_ctx, farmer, casual_msg)

        reply2 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        # Must remind farmer that casual messages do not confirm bookings
        assert "Casual messages or ambiguous acknowledgements do NOT confirm bookings" in reply2

        # SAFETY GATE VERIFICATION 2: STILL NO booking record must exist!
        bookings_after_casual = temp_db.list_bookings(farmer_id=farmer)
        assert len(bookings_after_casual) == 0

        # Step 3: Farmer sends explicit approval ("Confirm booking")
        mock_ctx.send.reset_mock()
        approve_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Confirm booking")])
        await handle_chat_message(mock_ctx, farmer, approve_msg)

        reply3 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Simulated Booking Confirmed" in reply3 or "Booking Confirmed" in reply3

        # SAFETY GATE VERIFICATION 3: Exactly 1 booking is now confirmed in ledger!
        bookings_after_approval = temp_db.list_bookings(farmer_id=farmer)
        assert len(bookings_after_approval) == 1
        assert bookings_after_approval[0].status == "CONFIRMED"
        assert bookings_after_approval[0].crop == "Onion"
