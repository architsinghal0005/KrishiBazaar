"""
Regression and Verification Test Suite for KrishiBazaar Final Workflow Fixes
(tests/test_final_workflow_fixes.py)

Covers the three key requirements prior to deployment:
1. Task 1: Payment Request Linkage, Isolation & Idempotency:
   - Clicking 'Proceed to Payment Review' on a fresh booking (KB-BOOK-20261023-15A159)
     with older settled payment (KB-PAY-27A4A2EC1F3E) in session/database strictly creates
     or displays a payment tied to KB-BOOK-20261023-15A159 and NEVER surfaces older settled payment.
   - Repeated clicks are idempotent.
   - Respects ENABLE_PAYMENT_PROTOCOL='False' without claiming live testnet settlement.
   - Honest reporting of missing payment records before payment request creation.

2. Task 2: Conversational Context & Booking Information Isolation:
   - General informational & introductory queries ("introduce KrishiBazaar", "Who are you?", "What is KrishiBazaar?")
     never leak unrelated existing bookings, even if session has confirmed booking.
   - Sender & session isolation prevents cross-talk between different farmers.
   - Explicit booking status queries continue to function reliably.

3. Task 3: Recommendation Transparency & Deterministic Arithmetic:
   - Provenance disclosures for zero freight (buyer farmgate collection),
     direct-trade APMC cess exemptions, and nearby/fallback market benchmarks.
   - Weather hazard mitigation explains zero transit spoilage due to tarpaulin cover.
   - Strict mathematical reconciliation: Net Return = Gross Revenue - Total Deductions.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    TextContent,
    MetadataContent,
)
from uagents_core.contrib.protocols.chat.cards import (
    CardResponse,
    create_card_response_content,
)

from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.config import settings
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    BuyerOffer,
    FarmerInquiry,
    MandiPriceRecord,
    NetReturnBreakdown,
    PaymentPurpose,
    PaymentRecord,
    PaymentStatus,
    TransportQuote,
    WeatherRiskReport,
)
from krishibazaar.services.booking_service import (
    BookingActionType,
    create_booking_proposal,
    execute_simulated_booking,
    is_informational_query,
    parse_booking_action,
)
from krishibazaar.services.card_service import (
    extract_interaction_action,
)
from krishibazaar.services.optimizer import (
    calculate_channel_net_return,
    compute_weather_risk_details,
    optimize_sales_channels,
)
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path) -> StorageEngine:
    db_file = tmp_path / "final_workflow_fixes_test.db"
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


# ===========================================================================
# TASK 1 TESTS: Payment Request Linkage, Session Isolation & Idempotency
# ===========================================================================

@pytest.mark.asyncio
async def test_task1_proceed_to_payment_review_strictly_links_to_current_booking(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    Exact Task 1 reproduction:
    - Given historical booking KB-BOOK-20261022-4C1CE8 with settled payment KB-PAY-27A4A2EC1F3E.
    - Given new active booking KB-BOOK-20261023-15A159.
    - Session previously held pending_payment_reference = 'KB-PAY-27A4A2EC1F3E'.
    - Farmer clicks 'Proceed to Payment Review'.
    - Must NOT display older settled payment KB-PAY-27A4A2EC1F3E.
    - Must clear the stale payment reference from session.
    - Must create a new payment record specifically linked to KB-BOOK-20261023-15A159.
    - Status must be REQUESTED / AWAITING_AUTHORIZATION (never SETTLED).
    """
    farmer = "agent1q_farmer_task1"
    old_booking_id = "KB-BOOK-20261022-4C1CE8"
    new_booking_id = "KB-BOOK-20261023-15A159"
    old_pay_ref = "KB-PAY-27A4A2EC1F3E"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    # 1. Historical booking and settled payment in DB
    old_prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=valid_transit,
    )
    old_book = execute_simulated_booking(old_prop)
    old_book.booking_id = old_booking_id
    temp_db.create_booking(old_book)

    old_pay = PaymentRecord(
        payment_reference=old_pay_ref,
        service_inquiry_id=old_booking_id,
        purpose="KrishiBazaar Advisory Pass",
        payer_agent_address=farmer,
        payer_wallet_address="fetch1testpayeraddress",
        payee_agent_address="agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf",
        payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        amount_base_units=1000,
        amount_display="0.000001 FET (1000 atestfet)",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.SETTLED,
        tx_hash="36C3088738B2A82CC61C7F18144DE8015FCF4A0587DCFE5060C3A27E384CBBEF",
        created_at_utc=now_utc.isoformat(),
        updated_at_utc=now_utc.isoformat(),
    )
    temp_db.save_payment_record(old_pay)

    # 2. Fresh booking KB-BOOK-20261023-15A159
    new_prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=valid_transit,
    )
    new_book = execute_simulated_booking(new_prop)
    new_book.booking_id = new_booking_id
    temp_db.create_booking(new_book)

    # Session currently points to new_booking_id, but with stale pending_payment_reference
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=new_booking_id,
        pending_payment_reference=old_pay_ref,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # Farmer clicks 'Proceed to Payment Review' CTA or sends text
        mock_ctx.send.reset_mock()
        msg = ChatMessage(
            msg_id=uuid4(),
            content=[
                TextContent(text="Proceed to Payment Review"),
                create_card_response_content(
                    card_id=uuid4(),
                    selection={"action": "request_payment_review", "booking_id": new_booking_id},
                ),
            ],
        )

        await handle_chat_message(mock_ctx, farmer, msg)

        assert mock_ctx.send.call_count >= 2
        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        # Must NOT surface older settled payment or its transaction hash
        assert old_pay_ref not in reply
        assert "36C3088738B2A82CC61C7F18144DE8015FCF4A0587DCFE5060C3A27E384CBBEF" not in reply
        assert "Payment Already Settled" not in reply

        # Must report payment review request tied specifically to new_booking_id
        assert new_booking_id in reply
        assert "Payment Review Request" in reply

        # Check DB: a new payment record must be created for new_booking_id
        new_pay = temp_db.get_payment_record_by_inquiry(new_booking_id)
        assert new_pay is not None
        assert new_pay.payment_reference != old_pay_ref
        assert new_pay.service_inquiry_id == new_booking_id
        assert new_pay.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION)
        assert new_pay.tx_hash is None


@pytest.mark.asyncio
async def test_task1_repeated_proceed_to_payment_review_is_idempotent(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    Repeated clicks on 'Proceed to Payment Review' for the same booking ID must return
    the existing payment record without creating duplicates or altering its status.
    """
    farmer = "agent1q_farmer_idempotency"
    booking_id = "KB-BOOK-20261023-15A159"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=valid_transit,
    )
    book = execute_simulated_booking(prop)
    book.booking_id = booking_id
    temp_db.create_booking(book)
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        # First click
        msg1 = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Proceed to Payment Review for {booking_id}")])
        await handle_chat_message(mock_ctx, farmer, msg1)

        pay_rec_1 = temp_db.get_payment_record_by_inquiry(booking_id)
        assert pay_rec_1 is not None
        ref_1 = pay_rec_1.payment_reference

        # Second click
        mock_ctx.send.reset_mock()
        msg2 = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Proceed to Payment Review for {booking_id}")])
        await handle_chat_message(mock_ctx, farmer, msg2)

        pay_rec_2 = temp_db.get_payment_record_by_inquiry(booking_id)
        assert pay_rec_2 is not None
        assert pay_rec_2.payment_reference == ref_1
        assert pay_rec_2.status == PaymentStatus.REQUESTED


@pytest.mark.asyncio
async def test_task1_plain_text_proceed_to_payment_review_resolves_booking_from_session(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    If farmer types plain text 'Proceed to Payment Review' without specifying booking ID,
    it correctly resolves from session's active_booking_id.
    """
    farmer = "agent1q_farmer_session_resolve"
    booking_id = "KB-BOOK-20261023-15A159"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=valid_transit,
    )
    book = execute_simulated_booking(prop)
    book.booking_id = booking_id
    temp_db.create_booking(book)
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):

        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Proceed to Payment Review")])
        await handle_chat_message(mock_ctx, farmer, msg)

        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert booking_id in reply
        assert "Payment Review Request" in reply


# ===========================================================================
# TASK 2 TESTS: Conversational Context & Booking Information Isolation
# ===========================================================================

@pytest.mark.asyncio
async def test_task2_informational_queries_never_leak_existing_booking(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    Task 2 reproduction:
    When a farmer sends general informational or introductory queries:
    - "I am a farmer growing onions in Nashik. Please introduce KrishiBazaar and explain how it helps farmers."
    - "What is KrishiBazaar?"
    - "Who are you?"
    - "Please introduce KrishiBazaar"
    The response must NOT display or leak unrelated existing bookings, even if the session has a confirmed booking.
    """
    farmer = "agent1q_farmer_task2_intro"
    booking_id = "KB-BOOK-20261023-15A159"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    # Set up confirmed booking in session
    prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=valid_transit,
    )
    book = execute_simulated_booking(prop)
    book.booking_id = booking_id
    temp_db.create_booking(book)
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_id,
    )

    test_queries = [
        "I am a farmer growing onions in Nashik. Please introduce KrishiBazaar and explain how it helps farmers.",
        "What is KrishiBazaar?",
        "Who are you and what do you do?",
        "Please introduce KrishiBazaar",
        "How does KrishiBazaar help farmers?",
    ]

    with patch("krishibazaar.agents.orchestrator.storage", temp_db):
        for query in test_queries:
            mock_ctx.send.reset_mock()
            msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=query)])
            await handle_chat_message(mock_ctx, farmer, msg)

            assert mock_ctx.send.call_count >= 2
            reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

            # Must give helpful introduction
            assert "Welcome to KrishiBazaar" in reply
            assert "How KrishiBazaar Helps Farmers" in reply

            # Must NEVER leak booking ID
            assert booking_id not in reply
            assert "You have an existing confirmed booking" not in reply


@pytest.mark.asyncio
async def test_task2_cross_sender_isolation_farmer_b_never_sees_farmer_a_booking(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    Different simulated senders/sessions do not cross-contaminate booking information.
    Farmer A's booking is never surfaced to Farmer B.
    """
    farmer_a = "agent1q_farmer_a"
    farmer_b = "agent1q_farmer_b"
    booking_id_a = "KB-BOOK-20261023-A11111"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    prop_a = create_booking_proposal(
        session_id=farmer_a,
        farmer_id=farmer_a,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=valid_transit,
    )
    book_a = execute_simulated_booking(prop_a)
    book_a.booking_id = booking_id_a
    temp_db.create_booking(book_a)
    temp_db.save_session(
        session_id=farmer_a,
        farmer_id=farmer_a,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_id_a,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db):
        mock_ctx.send.reset_mock()
        msg_b = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Check my booking status")])
        await handle_chat_message(mock_ctx, farmer_b, msg_b)

        assert mock_ctx.send.call_count >= 2
        reply_b = mock_ctx.send.call_args_list[1][0][1].content[0].text

        # Farmer B must NOT see Farmer A's booking
        assert booking_id_a not in reply_b
        assert "Booking Not Found" in reply_b


@pytest.mark.asyncio
async def test_task2_explicit_booking_status_query_works_reliably(
    temp_db: StorageEngine, mock_ctx, sample_option
):
    """
    Explicit booking status query ("Status of booking KB-BOOK-20261023-15A159") continues
    to function and returns accurate booking records.
    """
    farmer = "agent1q_farmer_explicit_status"
    booking_id = "KB-BOOK-20261023-15A159"
    now_utc = datetime.now(timezone.utc)
    valid_transit = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")

    prop = create_booking_proposal(
        session_id=farmer,
        farmer_id=farmer,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=valid_transit,
    )
    book = execute_simulated_booking(prop)
    book.booking_id = booking_id
    temp_db.create_booking(book)
    temp_db.save_session(
        session_id=farmer,
        farmer_id=farmer,
        state="BOOKING_CONFIRMED",
        active_booking_id=booking_id,
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db):
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=f"Status of booking {booking_id}")])
        await handle_chat_message(mock_ctx, farmer, msg)

        assert mock_ctx.send.call_count >= 2
        reply = mock_ctx.send.call_args_list[1][0][1].content[0].text

        assert booking_id in reply
        assert "Lasalgaon APMC Mandi" in reply
        assert "CONFIRMED" in reply


# ===========================================================================
# TASK 3 TESTS: Recommendation Transparency & Deterministic Arithmetic
# ===========================================================================

def test_task3_weather_hazard_mitigation_explanation():
    """
    Weather hazard mitigation:
    - High precipitation hazard with tarpaulin cover included results in 0.0 loss,
      with clear explanation connecting tarpaulin mitigation to the hazard.
    - High precipitation hazard without tarpaulin incurs 3.5% loss with explanation.
    """
    gross_val = 100000.0
    weather_high = WeatherRiskReport(
        location="Nashik",
        forecast_date="2026-10-25",
        temperature_max_c=32.0,
        temperature_min_c=22.0,
        precipitation_sum_mm=45.0,
        precipitation_probability_pct=90.0,
        wind_speed_max_kmh=25.0,
        spoilage_hazard_level="HIGH",
        weather_description="Heavy Monsoon Rainfall",
        transit_advice="Cover goods with waterproof tarpaulin",
    )

    loss_tarp, note_tarp = compute_weather_risk_details(gross_val, weather_high, tarp_cover_included=True)
    assert loss_tarp == 0.0
    assert "Mitigated" in note_tarp
    assert "tarpaulin" in note_tarp.lower()
    assert "3.5%" in note_tarp

    loss_no_tarp, note_no_tarp = compute_weather_risk_details(gross_val, weather_high, tarp_cover_included=False)
    assert loss_no_tarp == 3500.0
    assert "3.5%" in note_no_tarp
    assert "uncovered" in note_no_tarp.lower()


def test_task3_zero_freight_buyer_farmgate_provenance():
    """
    When buyer provides farmgate pickup, transport cost is 0.00 and provenance
    explicitly identifies the buyer farmgate collection assumption.
    """
    crop = "Onion"
    quantity = 50.0
    buyer_farmgate = BuyerOffer(
        offer_id="OFFER-FPO-01",
        buyer_id="buyer_farmgate_1",
        buyer_name="Reliance Fresh FPO Collection Center",
        buyer_type="FPO Aggregator",
        crop="Onion",
        quantity_quintals=50.0,
        offered_price_per_quintal=2100.0,
        farmgate_pickup=True,
        is_apmc_exempt=True,
    )

    result = optimize_sales_channels(
        crop=crop,
        quantity_quintals=quantity,
        mandi_records=[],
        buyer_offers=[buyer_farmgate],
    )

    assert len(result.all_options) == 1
    opt = result.all_options[0]
    assert opt.transport_cost == 0.0
    assert "farmgate" in opt.transport_provenance.lower()
    assert opt.is_apmc_exempt is True
    assert opt.mandi_cess_apmc == 0.0
    assert opt.loading_unloading_cost == 0.0
    assert "direct trade statutory exemption" in opt.charges_provenance.lower()


def test_task3_nearby_fallback_market_provenance():
    """
    When a mandi is selected as a regional fallback because no live records were in the target district,
    the provenance explicitly documents the fallback market name, district, and arrival date.
    """
    fallback_mandi = MandiPriceRecord(
        mandi_name="Lasalgaon",
        district="Nashik",
        state="Maharashtra",
        crop="Onion",
        commodity="Onion",
        min_price=1800.0,
        max_price=2400.0,
        modal_price=2200.0,
        arrival_date="2026-10-23",
        data_source="data.gov.in_live",
    )

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=[fallback_mandi],
        target_district="Pune",  # target district differs from mandi.district (Nashik)
    )

    opt = result.all_options[0]
    assert "Nearby/State APMC Benchmark" in opt.price_provenance
    assert "Lasalgaon" in opt.price_provenance
    assert "Nashik" in opt.price_provenance
    assert "Arrival: 2026-10-23" in opt.price_provenance
    assert "regional fallback because no live records were reported in Pune" in opt.price_provenance


def test_task3_strict_deterministic_arithmetic_across_all_channels():
    """
    Every NetReturnBreakdown produced by optimize_sales_channels must satisfy
    strict mathematical reconciliation:
    expected_net_return == gross_value - total_deductions
    net_return_per_quintal == round(expected_net_return / quantity_quintals, 2)
    """
    mandis = [
        MandiPriceRecord(
            mandi_name="Lasalgaon",
            district="Nashik",
            state="Maharashtra",
            crop="Onion",
            commodity="Onion",
            min_price=1800.0,
            max_price=2200.0,
            modal_price=2000.0,
            arrival_date="2026-10-23",
            data_source="data.gov.in_live",
        ),
        MandiPriceRecord(
            mandi_name="Pimpalgaon",
            district="Nashik",
            state="Maharashtra",
            crop="Onion",
            commodity="Onion",
            min_price=1750.0,
            max_price=2150.0,
            modal_price=1950.0,
            arrival_date="2026-10-23",
            data_source="data.gov.in_live",
        ),
    ]
    buyers = [
        BuyerOffer(
            offer_id="OFFER-A",
            buyer_id="buyer_a",
            buyer_name="FPO Nashik",
            buyer_type="FPO",
            crop="Onion",
            quantity_quintals=50.0,
            offered_price_per_quintal=2100.0,
            farmgate_pickup=True,
            is_apmc_exempt=True,
        ),
        BuyerOffer(
            offer_id="OFFER-B",
            buyer_id="buyer_b",
            buyer_name="Metro Cash & Carry",
            buyer_type="Retail Chain",
            crop="Onion",
            quantity_quintals=50.0,
            offered_price_per_quintal=2250.0,
            farmgate_pickup=False,
            is_apmc_exempt=False,
        ),
    ]
    quotes = {
        "Metro Cash & Carry (Retail Chain)": TransportQuote(
            quote_id="Q-01",
            transporter_id="T-01",
            transporter_name="Nashik Agro Logistics",
            vehicle_type="Tata 407 (2.5T)",
            capacity_quintals=50.0,
            route_origin="Nashik",
            route_destination="Metro Cash & Carry",
            distance_km=40.0,
            base_fare=500.0,
            rate_per_km=22.0,
            weather_surcharge=250.0,
            total_transport_cost=1630.0,
            estimated_transit_hours=1.5,
            tarp_cover_included=True,
        ),
        "Lasalgaon": TransportQuote(
            quote_id="Q-02",
            transporter_id="T-02",
            transporter_name="Kisan Freight Express",
            vehicle_type="Tata 407 (2.5T)",
            capacity_quintals=50.0,
            route_origin="Nashik",
            route_destination="Lasalgaon",
            distance_km=30.0,
            base_fare=500.0,
            rate_per_km=22.0,
            weather_surcharge=250.0,
            total_transport_cost=1410.0,
            estimated_transit_hours=1.0,
            tarp_cover_included=True,
        ),
    }
    weather = WeatherRiskReport(
        location="Nashik",
        forecast_date="2026-10-25",
        temperature_max_c=30.0,
        temperature_min_c=20.0,
        precipitation_sum_mm=55.0,
        precipitation_probability_pct=95.0,
        wind_speed_max_kmh=30.0,
        spoilage_hazard_level="HIGH",
        weather_description="Heavy Thunderstorm Warning",
        transit_advice="Use tarp cover",
    )

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=mandis,
        buyer_offers=buyers,
        transport_quotes=quotes,
        weather_report=weather,
    )

    assert len(result.all_options) == 4
    for opt in result.all_options:
        # Check gross
        expected_gross = round(opt.quantity_quintals * opt.offered_price_per_quintal, 2)
        assert opt.gross_value == expected_gross

        # Check total deductions
        expected_deductions = round(
            opt.transport_cost + opt.applicable_market_charges + opt.storage_cost + opt.estimated_loss_cost, 2
        )
        assert opt.total_deductions == expected_deductions

        # Check net return
        expected_net = round(expected_gross - expected_deductions, 2)
        assert opt.expected_net_return == expected_net

        # Check net per quintal
        expected_per_qtl = round(expected_net / opt.quantity_quintals, 2)
        assert opt.net_return_per_quintal == expected_per_qtl

        # Check weather loss note is populated
        assert opt.weather_loss_note != ""
