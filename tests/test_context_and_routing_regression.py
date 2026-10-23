"""
Regression and Integration Tests for Bug Fix:
KrishiBazaar Repeatedly Asks for Farmer Details (tests/test_context_and_routing_regression.py).

Verifies:
1. End-to-end sequence:
   Farmer request -> Recommendation -> Proposal -> Booking Confirmation -> Booking status lookup -> Payment status lookup.
2. Context preservation:
   Crop (Onion), quantity (50 quintals), origin (Nashik), target return (INR 85,000), proposal ID, booking ID,
   and pending payment reference remain intact in session and storage.
3. No repeated onboarding questions:
   "Which crop do you want to sell..." NEVER appears after booking is confirmed, during status lookup,
   during payment queries, or during conversational follow-ups.
4. Status routing:
   "Status of KB-BOOK-..." and "Track KB-BOOK-..." route directly to status lookup.
5. Payment routing:
   "Status of KB-PAY-..." routes directly to payment record lookup and does NOT trigger new onboarding
   or create duplicate payments.
6. Safe new session restoration:
   When an external / new session references an existing booking or payment ID, produce context is
   restored into the new session.
7. Multi-turn intent accumulation:
   Partial details across turns (Turn 1: crop & quantity; Turn 2: location & target return) are merged seamlessly.
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
    FarmerInquiry,
    NetReturnBreakdown,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.services.booking_service import (
    create_booking_proposal,
    execute_simulated_booking,
)
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path) -> StorageEngine:
    db_file = tmp_path / "context_routing_regression.db"
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


ONBOARDING_PROMPT = "Which crop do you want to sell, the approximate quantity, and your village or district?"


@pytest.mark.asyncio
async def test_full_lifecycle_no_repeated_onboarding_questions(temp_db: StorageEngine, mock_ctx, sample_option):
    """
    Regression Test for the full lifecycle:
    1. Farmer receives booking confirmation for KB-BOOK-20261022-BA2D27.
    2. Farmer sends follow-up conversational message ("What should I do now?").
    3. Farmer sends "Status of KB-BOOK-20261022-BA2D27".
    4. Farmer sends "Track KB-BOOK-20261022-BA2D27".
    5. Farmer sends "Status of KB-PAY-928966AECE8A".

    Verifies:
    - NO repeated onboarding questions appear in ANY step.
    - Existing booking details remain intact in session and database.
    - No duplicate payments or transfers are initiated.
    """
    farmer_sender = "agent1q_farmer_lifecycle_01"
    booking_id = "KB-BOOK-20261022-BA2D27"
    proposal_id = "PROP-20261022-8484FB"
    payment_ref = "KB-PAY-928966AECE8A"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    # Create proposal and booking using canonical helper
    proposal = create_booking_proposal(
        session_id=farmer_sender,
        farmer_id=farmer_sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    proposal.proposal_id = proposal_id
    proposal.is_consumed = True
    temp_db.save_proposal(proposal)

    booking = execute_simulated_booking(proposal)
    booking.booking_id = booking_id
    temp_db.create_booking(booking)

    payment = PaymentRecord(
        payment_reference=payment_ref,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer_sender,
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

    inquiry_obj = FarmerInquiry(
        farmer_id=farmer_sender,
        crop="Onion",
        quantity_quintals=50.0,
        location_district="Nashik",
        origin_location="Nashik, Maharashtra",
        minimum_net_return_inr=85000.0,
        selling_deadline=valid_transit,
    )
    temp_db.save_session(
        session_id=farmer_sender,
        farmer_id=farmer_sender,
        state="BOOKING_CONFIRMED",
        inquiry=inquiry_obj,
        active_booking_id=booking_id,
        active_proposal_id=proposal_id,
        pending_payment_reference=payment_ref,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Step 2: Follow-up conversational message ("What should I do now?")
        mock_ctx.send.reset_mock()
        follow_up_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="What should I do now?")])
        await handle_chat_message(mock_ctx, farmer_sender, follow_up_msg)

        reply2 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert ONBOARDING_PROMPT not in reply2
        assert "Which crop do you want to sell" not in reply2
        assert booking_id in reply2
        assert "Your Booking is Confirmed" in reply2 or "produce details are already saved" in reply2

        # Step 3: Status lookup ("Status of KB-BOOK-20261022-BA2D27")
        mock_ctx.send.reset_mock()
        status_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Status of {booking_id}")])
        await handle_chat_message(mock_ctx, farmer_sender, status_msg)

        reply3 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert ONBOARDING_PROMPT not in reply3
        assert "Which crop do you want to sell" not in reply3
        assert "Simulated Booking Status" in reply3
        assert booking_id in reply3
        assert "CONFIRMED" in reply3
        assert "Onion" in reply3
        assert "50 quintals" in reply3

        # Step 4: Track lookup ("Track KB-BOOK-20261022-BA2D27")
        mock_ctx.send.reset_mock()
        track_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Track {booking_id}")])
        await handle_chat_message(mock_ctx, farmer_sender, track_msg)

        reply4 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert ONBOARDING_PROMPT not in reply4
        assert "Which crop do you want to sell" not in reply4
        assert "Simulated Booking Status" in reply4
        assert booking_id in reply4

        # Step 5: Payment status lookup ("Status of KB-PAY-928966AECE8A")
        mock_ctx.send.reset_mock()
        pay_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Status of {payment_ref}")])
        await handle_chat_message(mock_ctx, farmer_sender, pay_msg)

        reply5 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert ONBOARDING_PROMPT not in reply5
        assert "Which crop do you want to sell" not in reply5
        assert payment_ref in reply5
        assert "REQUESTED" in reply5 or "Payment Status" in reply5

        # Verify no duplicate payment was created
        all_payments = temp_db.list_payment_records()
        assert len(all_payments) == 1
        assert all_payments[0].payment_reference == payment_ref

        # Verify booking details remain completely intact in DB
        db_booking = temp_db.get_booking(booking_id)
        assert db_booking is not None
        assert db_booking.crop == "Onion"
        assert db_booking.quantity_quintals == 50.0
        assert db_booking.net_payout == 85000.0
        assert db_booking.status == "CONFIRMED"


@pytest.mark.asyncio
async def test_new_session_safe_context_restoration(temp_db: StorageEngine, mock_ctx, sample_option):
    """
    Requirement 5: If ASI:One starts a genuinely new session, restore existing context
    using the booking or payment reference.
    """
    original_farmer = "agent1q_original_farmer_session"
    new_asi_session = "agent1q_asi_one_new_session_9999"
    booking_id = "KB-BOOK-20261022-BA2D27"
    proposal_id = "PROP-20261022-8484FB"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    proposal = create_booking_proposal(
        session_id=original_farmer,
        farmer_id=original_farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    proposal.proposal_id = proposal_id
    booking = execute_simulated_booking(proposal)
    booking.booking_id = booking_id
    temp_db.create_booking(booking)

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # The new session sends a query referencing the booking
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Status of {booking_id}")])
        await handle_chat_message(mock_ctx, new_asi_session, msg)

        # 1. Booking status is returned without asking onboarding details
        reply1 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Simulated Booking Status" in reply1
        assert booking_id in reply1
        assert "Which crop do you want to sell" not in reply1

        # 2. Context was restored into new_asi_session
        sess = temp_db.get_session(new_asi_session)
        assert sess is not None
        assert sess["active_booking_id"] == booking_id
        assert sess["state"] == "BOOKING_CONFIRMED"
        assert sess["inquiry"].crop == "Onion"
        assert sess["inquiry"].quantity_quintals == 50.0

        # 3. Subsequent conversational query from new session remembers produce details
        mock_ctx.send.reset_mock()
        follow_up = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Thank you! When is pickup?")])
        await handle_chat_message(mock_ctx, new_asi_session, follow_up)

        reply2 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Which crop do you want to sell" not in reply2
        assert booking_id in reply2
        assert "Your Booking is Confirmed" in reply2 or "produce details are already saved" in reply2


@pytest.mark.asyncio
async def test_payment_status_query_does_not_create_duplicate_payment(temp_db: StorageEngine, mock_ctx):
    """
    Requirement 3: A payment-status query for KB-PAY-928966AECE8A must look up
    the existing payment record and ledger status. It must NOT begin a new farmer
    request or initiate another transfer.
    """
    farmer = "agent1q_farmer_pay_lookup"
    payment_ref = "KB-PAY-928966AECE8A"
    now_utc = datetime.now(timezone.utc)

    payment = PaymentRecord(
        payment_reference=payment_ref,
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
    temp_db.save_payment_record(payment)

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Farmer asks payment status via natural query
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Check payment status for {payment_ref}")])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Which crop do you want to sell" not in reply
        assert payment_ref in reply
        assert "REQUESTED" in reply or "Payment Status" in reply

        # Verify no duplicate payment record was inserted
        records = temp_db.list_payment_records()
        assert len(records) == 1
        assert records[0].payment_reference == payment_ref


@pytest.mark.asyncio
async def test_multi_turn_partial_intent_accumulation(temp_db: StorageEngine, mock_ctx):
    """
    Requirement 4: Avoid unnecessary clarification.
    Turn 1: Farmer says "I want to sell 50 quintals of onion".
            System asks for origin location.
    Turn 2: Farmer says "I am in Nashik, Maharashtra and want at least 85000 net".
            System reuses crop=Onion and quantity=50, does NOT ask for crop/quantity again.
    """
    farmer = "agent1q_farmer_multiturn_01"

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "allow_direct_service_fallback", True):

        # Turn 1: Partial details (crop and quantity only, no location)
        msg1 = ChatMessage(msg_id=uuid4(), content=[TextContent(text="I want to sell 50 quintals of onion")])
        await handle_chat_message(mock_ctx, farmer, msg1)

        reply1 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        reply1_lower = reply1.lower()
        assert "district or village" in reply1_lower or "village" in reply1_lower or "location" in reply1_lower or "origin" in reply1_lower

        # Check session has stored partial inquiry
        sess = temp_db.get_session(farmer)
        assert sess is not None
        assert sess["inquiry"].crop == "Onion"
        assert sess["inquiry"].quantity_quintals == 50.0

        # Turn 2: Farmer provides location and target return
        mock_ctx.send.reset_mock()
        msg2 = ChatMessage(msg_id=uuid4(), content=[TextContent(text="I am in Nashik, Maharashtra and want at least 85000 net")])
        await handle_chat_message(mock_ctx, farmer, msg2)

        reply2 = mock_ctx.send.call_args_list[1][0][1].content[0].text
        # Does NOT ask for crop or quantity again!
        assert "Which crop do you want to sell" not in reply2
        # Successfully processed request using merged crop, quantity, and location
        assert "Onion" in reply2
        assert "50 quintals" in reply2 or "50" in reply2
        assert "Nashik" in reply2


@pytest.mark.asyncio
async def test_explicit_new_crop_allowed_when_booking_confirmed(temp_db: StorageEngine, mock_ctx, sample_option):
    """
    Verify that if a farmer with a confirmed booking explicitly requests a quote
    for a DIFFERENT crop (e.g. Tomato or Wheat), the orchestrator allows starting
    the new inquiry rather than locking them into the old booking forever.
    """
    farmer = "agent1q_farmer_new_crop"
    booking_id = "KB-BOOK-20261022-BA2D27"
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
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "allow_direct_service_fallback", True):

        # Farmer explicitly says they have 100 quintals of tomatoes in Pune
        new_inquiry_msg = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I now want to sell 100 quintals of tomato in Pune")],
        )
        await handle_chat_message(mock_ctx, farmer, new_inquiry_msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        # Processes the new tomato inquiry
        assert "Tomato" in reply
        assert "100" in reply
        assert "Pune" in reply


@pytest.mark.asyncio
async def test_general_introduction_request_with_existing_booking_context(temp_db: StorageEngine, mock_ctx, sample_option):
    """
    Regression Test for Observed Bug:
    A user with a confirmed booking sends a generic informational request:
    'I am a farmer growing onions in Nashik. Please introduce KrishiBazaar and explain how it helps farmers.'

    Verifies:
    1. Returns a comprehensive KrishiBazaar introduction/overview explaining how it helps farmers.
    2. Does NOT return 'âœ… **Your Booking is Confirmed:**' or booking details as primary response.
    3. Does NOT send the repetitive onboarding form question.
    4. Confirmed booking record remains safe and unaltered in database.
    """
    farmer_sender = "agent1qfzx4stz83vx8nh4y2jh25ek6v5aag6xmps0ryt3kqfze0425a942v8ev4d"
    booking_id = "KB-BOOK-20261022-4C1CE8"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    proposal = create_booking_proposal(
        session_id=farmer_sender,
        farmer_id=farmer_sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    proposal.proposal_id = "PROP-20261022-4C1CE8"
    proposal.is_consumed = True
    temp_db.save_proposal(proposal)

    booking = execute_simulated_booking(proposal)
    booking.booking_id = booking_id
    temp_db.create_booking(booking)

    inquiry_obj = FarmerInquiry(
        farmer_id=farmer_sender,
        crop="Onion",
        quantity_quintals=50.0,
        location_district="Nashik",
        origin_location="Nashik, Maharashtra",
        minimum_net_return_inr=85000.0,
        selling_deadline=valid_transit,
    )
    temp_db.save_session(
        session_id=farmer_sender,
        farmer_id=farmer_sender,
        state="BOOKING_CONFIRMED",
        inquiry=inquiry_obj,
        active_booking_id=booking_id,
        active_proposal_id=proposal.proposal_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        mock_ctx.send.reset_mock()
        intro_query = "I am a farmer growing onions in Nashik. Please introduce KrishiBazaar and explain how it helps farmers."
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=intro_query)])
        await handle_chat_message(mock_ctx, farmer_sender, msg)

        assert mock_ctx.send.call_count >= 2
        # Message 0: ChatAcknowledgement
        ack_call = mock_ctx.send.call_args_list[0]
        assert isinstance(ack_call[0][1], ChatAcknowledgement)

        # Message 1: ChatMessage response
        resp_call = mock_ctx.send.call_args_list[1]
        reply_text = resp_call[0][1].content[0].text

        # Must introduce KrishiBazaar and explain how it helps
        assert "Welcome to KrishiBazaar" in reply_text
        assert "How KrishiBazaar Helps Farmers" in reply_text
        assert "Live APMC Mandi" in reply_text or "mandi" in reply_text.lower()
        assert "Net Return" in reply_text

        # Must NOT return stale booking confirmation
        assert "âœ… **Your Booking is Confirmed:**" not in reply_text
        assert "Destination Channel:" not in reply_text
        assert "Estimated Net Payout:" not in reply_text

        # Must NOT return repetitive onboarding questionnaire
        assert ONBOARDING_PROMPT not in reply_text
        assert "Which crop do you want to sell" not in reply_text

        # Database booking must remain intact and confirmed
        db_booking = temp_db.get_booking(booking_id)
        assert db_booking is not None
        assert db_booking.status == "CONFIRMED"
        assert db_booking.crop == "Onion"


@pytest.mark.asyncio
async def test_explicit_request_for_existing_booking_status(temp_db: StorageEngine, mock_ctx, sample_option):
    """
    Ensure a previous booking is shown when the user explicitly requests its status
    or references its booking ID.
    """
    farmer_sender = "agent1q_farmer_status_query"
    booking_id = "KB-BOOK-20261022-4C1CE8"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    proposal = create_booking_proposal(
        session_id=farmer_sender,
        farmer_id=farmer_sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    booking = execute_simulated_booking(proposal)
    booking.booking_id = booking_id
    temp_db.create_booking(booking)

    temp_db.save_session(
        session_id=farmer_sender,
        farmer_id=farmer_sender,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Sub-test A: Explicit status query with booking ID
        mock_ctx.send.reset_mock()
        msg_a = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Status of {booking_id}")])
        await handle_chat_message(mock_ctx, farmer_sender, msg_a)
        reply_a = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Simulated Booking Status" in reply_a
        assert booking_id in reply_a
        assert "CONFIRMED" in reply_a
        assert "Onion" in reply_a

        # Sub-test B: Natural phrasing "What is my booking status?"
        mock_ctx.send.reset_mock()
        msg_b = ChatMessage(msg_id=uuid4(), content=[TextContent(text="What is my booking status?")])
        await handle_chat_message(mock_ctx, farmer_sender, msg_b)
        reply_b = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Simulated Booking Status" in reply_b
        assert booking_id in reply_b
        assert "CONFIRMED" in reply_b

        # Sub-test C: Follow-up question about pickup
        mock_ctx.send.reset_mock()
        msg_c = ChatMessage(msg_id=uuid4(), content=[TextContent(text="When is pickup for my booking?")])
        await handle_chat_message(mock_ctx, farmer_sender, msg_c)
        reply_c = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert booking_id in reply_c
        assert "Simulated Booking Status" in reply_c or "Your Booking is Confirmed" in reply_c
        assert "Scheduled Date" in reply_c or "Scheduled Pickup" in reply_c


@pytest.mark.asyncio
async def test_explicit_new_crop_inquiry_when_prior_booking_exists(temp_db: StorageEngine, mock_ctx, sample_option):
    """
    Ensure that when a user has a confirmed booking for Onion, they can:
    1. Start a new inquiry for the SAME crop (Onion) with fresh quantity/location.
    2. Start a new inquiry for a DIFFERENT crop (Tomato or Potato).
    In both cases, the system does NOT lock them into the stale booking.
    """
    farmer_sender = "agent1q_farmer_new_inquiry"
    prior_booking_id = "KB-BOOK-20261022-4C1CE8"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    proposal = create_booking_proposal(
        session_id=farmer_sender,
        farmer_id=farmer_sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    booking = execute_simulated_booking(proposal)
    booking.booking_id = prior_booking_id
    temp_db.create_booking(booking)

    temp_db.save_session(
        session_id=farmer_sender,
        farmer_id=farmer_sender,
        state="BOOKING_CONFIRMED",
        active_booking_id=prior_booking_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "allow_direct_service_fallback", True):

        # Case 1: Same crop (Onion), but fresh explicit inquiry to sell
        mock_ctx.send.reset_mock()
        msg_same_crop = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I want to sell 100 quintals of onion in Nashik and need fresh quotes")],
        )
        await handle_chat_message(mock_ctx, farmer_sender, msg_same_crop)
        reply_same = mock_ctx.send.call_args_list[1][0][1].content[0].text
        # Must NOT return stale booking confirmation
        assert f"âœ… **Your Booking is Confirmed:** `{prior_booking_id}`" not in reply_same
        # Should process as fresh market / selling inquiry
        assert "Onion" in reply_same
        assert "100" in reply_same

        # Case 2: Different crop (Potato)
        mock_ctx.send.reset_mock()
        msg_diff_crop = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I want to sell 75 quintals of potato in Agra")],
        )
        await handle_chat_message(mock_ctx, farmer_sender, msg_diff_crop)
        reply_diff = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert f"âœ… **Your Booking is Confirmed:** `{prior_booking_id}`" not in reply_diff
        assert "Potato" in reply_diff
        assert "75" in reply_diff


@pytest.mark.asyncio
async def test_separate_senders_and_sessions_isolation(temp_db: StorageEngine, mock_ctx, sample_option):
    """
    Requirement 5: Verify session isolation.
    Do not expose one sender's booking or personal information to another sender
    due to shared or incorrectly derived session keys.
    """
    sender_alice = "agent1q_farmer_alice_legitimate"
    sender_bob = "agent1q_farmer_bob_different"
    booking_alice_id = "KB-BOOK-20261022-ALICE1"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    # Set up Alice's confirmed booking
    proposal_alice = create_booking_proposal(
        session_id=sender_alice,
        farmer_id=sender_alice,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        transit_date=valid_transit,
    )
    booking_alice = execute_simulated_booking(proposal_alice)
    booking_alice.booking_id = booking_alice_id
    temp_db.create_booking(booking_alice)

    temp_db.save_session(
        session_id=sender_alice,
        farmer_id=sender_alice,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_alice_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # 1. Bob sends generic introduction request:
        # Bob must NOT see Alice's booking reference or details
        mock_ctx.send.reset_mock()
        intro_msg = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I am a farmer growing onions in Nashik. Please introduce KrishiBazaar and explain how it helps farmers.")],
        )
        await handle_chat_message(mock_ctx, sender_bob, intro_msg)
        reply_bob_intro = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Welcome to KrishiBazaar" in reply_bob_intro
        assert booking_alice_id not in reply_bob_intro
        assert "ALICE1" not in reply_bob_intro

        # 2. Bob sends generic status query "What is my booking status?"
        # Bob must NOT get Alice's booking!
        mock_ctx.send.reset_mock()
        status_msg = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="What is my booking status?")],
        )
        await handle_chat_message(mock_ctx, sender_bob, status_msg)
        reply_bob_status = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Booking Not Found" in reply_bob_status or "No simulated booking record found" in reply_bob_status
        assert booking_alice_id not in reply_bob_status

        # 3. Bob attempts session spoofing via interactive card payload with Alice's session_id
        mock_ctx.send.reset_mock()
        spoofed_metadata = create_card_response_content(
            selection={
                "action": "status_lookup",
                "session_id": sender_alice,  # Spoofed session ID!
            }
        )
        spoofed_msg = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Status"), spoofed_metadata],
        )
        await handle_chat_message(mock_ctx, sender_bob, spoofed_msg)
        reply_bob_spoofed = mock_ctx.send.call_args_list[1][0][1].content[0].text
        # Spoofed session_id must be rejected by get_session_key; Bob must NOT receive Alice's booking
        assert booking_alice_id not in reply_bob_spoofed
        assert "Booking Not Found" in reply_bob_spoofed or "No simulated booking record found" in reply_bob_spoofed
