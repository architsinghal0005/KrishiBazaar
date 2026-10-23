"""
Integration tests for KrishiBazaar Phase 7:
ASI:One Interactive Cards and Structured Farmer Interaction.
Validates end-to-end multi-turn flows through handle_chat_message:
1. Complete produce inquiry -> CarouselCard recommendations + markdown fallback
2. Option selection via Card CTA -> ReviewCard proposal + explicit safety gate
3. Option selection via text fallback ('select option 1', '1') -> ReviewCard proposal
4. Invalid option selection -> Error DetailCard + helpful guidance (no proposal created)
5. Explicit approval via Card CTA -> Simulated BookingRecord + DetailCard confirmation
6. Explicit approval via text fallback -> Confirmation
7. Casual message during AWAITING_APPROVAL -> Blocked by safety gate
8. Duplicate approval -> Blocked (idempotent, no duplicate booking)
9. Expired proposal -> Blocked (>15 min expiration)
10. Cancellation -> Proposal cancelled
11. Incomplete inquiry -> FormCard with missing fields
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from uuid import uuid4

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    MetadataContent,
    TextContent,
)
from uagents_core.contrib.protocols.chat.cards import (
    CarouselCardPayload,
    DetailCardPayload,
    FormCardPayload,
    ReviewCardPayload,
    create_card_response_content,
    extract_card,
)

from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    BuyerOffer,
    BuyerOffersResult,
    DataProvenance,
    FarmerInquiry,
    FarmerIntent,
    MandiPriceRecord,
    MandiPriceResult,
    NetReturnBreakdown,
    OptimizationResult,
    TransportQuote,
    TransportQuotesResult,
    WeatherResult,
    WeatherRiskReport,
)
from krishibazaar.storage import StorageEngine, storage


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Ensure clean isolated database state for each test run without touching runtime db."""
    test_db = StorageEngine(db_path=tmp_path / "test_phase7.db")
    monkeypatch.setattr("krishibazaar.storage.storage", test_db)
    monkeypatch.setattr("krishibazaar.agents.orchestrator.storage", test_db)
    monkeypatch.setattr("tests.test_phase7_interaction.storage", test_db)
    yield test_db


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


@pytest.fixture
def sample_complete_intent():
    return FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Nashik, Maharashtra",
        selling_deadline="tomorrow",
        minimum_net_return_inr=90000.0,
        is_complete=True,
        extraction_source="asi1",
    )


@pytest.fixture
def sample_coord_results():
    prov = DataProvenance(
        source="data.gov.in (AGMARKNET)",
        retrieval_timestamp_utc="2026-10-21T00:00:00Z",
        observation_date="2026-10-20",
        freshness_status="LIVE",
        is_live=True,
    )
    mandi_res = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[
            MandiPriceRecord(
                mandi_name="Nashik Mandi",
                district="Nashik",
                state="Maharashtra",
                commodity="Onion",
                variety="Red",
                arrival_date="2026-10-20",
                min_price=1800.0,
                max_price=2400.0,
                modal_price=2200.0,
                data_source="data.gov.in_live",
                is_suspicious=False,
            )
        ],
        provenance=prov,
    )
    weather_res = WeatherResult(
        success=True,
        location="Nashik, Maharashtra",
        report=WeatherRiskReport(
            location="Nashik, Maharashtra",
            forecast_date="2026-10-22",
            temperature_max_c=31.0,
            temperature_min_c=21.0,
            precipitation_sum_mm=2.0,
            precipitation_probability_pct=25.0,
            wind_speed_max_kmh=12.0,
            spoilage_hazard_level="LOW",
            transit_advice="Favorable conditions for transport.",
        ),
        provenance=prov,
    )
    buyer_res = BuyerOffersResult(
        success=True,
        crop="onion",
        offers=[
            BuyerOffer(
                offer_id="offer_buyer_a",
                buyer_id="buyer_a",
                buyer_name="AgriExport Corp",
                buyer_type="Export Aggregator",
                crop="onion",
                quantity_quintals=50.0,
                offered_price_per_quintal=2150.0,
                farmgate_pickup=False,
                payment_terms="T+1",
                is_simulated=True,
            )
        ],
        provenance=DataProvenance(
            source="KrishiBazaar Buyer Network",
            retrieval_timestamp_utc="2026-10-21T00:00:00Z",
            observation_date="2026-10-20",
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
        ),
    )
    transport_res = TransportQuotesResult(
        success=True,
        origin="Nashik, Maharashtra",
        quotes={
            "Nashik Mandi": TransportQuote(
                quote_id="quote_nashik_mandi",
                transporter_id="trans_1",
                transporter_name="Krishi Logistics",
                vehicle_type="Tata 407",
                capacity_quintals=60.0,
                route_origin="Nashik, Maharashtra",
                route_destination="Nashik Mandi",
                distance_km=15.0,
                base_fare=2500.0,
                rate_per_km=30.0,
                weather_surcharge=0.0,
                total_transport_cost=2950.0,
                estimated_transit_hours=1.0,
                is_simulated=True,
            )
        },
        provenance=DataProvenance(
            source="KrishiBazaar Transport Network",
            retrieval_timestamp_utc="2026-10-21T00:00:00Z",
            observation_date="2026-10-20",
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
        ),
    )
    opt1 = NetReturnBreakdown(
        scenario_id="opt_1",
        rank=1,
        channel_type="MANDI",
        destination_name="Nashik Mandi",
        distance_km=15.0,
        quantity_quintals=50.0,
        offered_price_per_quintal=2200.0,
        gross_value=110000.0,
        transport_cost=2950.0,
        applicable_market_charges=2750.0,
        mandi_cess_apmc=1650.0,
        loading_unloading_cost=1100.0,
        storage_cost=0.0,
        estimated_loss_cost=500.0,
        total_deductions=6200.0,
        expected_net_return=103800.0,
        net_return_per_quintal=2076.0,
        price_provenance="data.gov.in (AGMARKNET)",
        is_price_simulated=False,
        meets_minimum_condition=True,
        recommendation_note="Nashik Mandi offers optimal net payout.",
    )
    opt2 = NetReturnBreakdown(
        scenario_id="opt_2",
        rank=2,
        channel_type="DIRECT_BUYER",
        destination_name="AgriExport Corp",
        distance_km=20.0,
        quantity_quintals=50.0,
        offered_price_per_quintal=2150.0,
        gross_value=107500.0,
        transport_cost=0.0,
        applicable_market_charges=0.0,
        storage_cost=0.0,
        estimated_loss_cost=500.0,
        total_deductions=500.0,
        expected_net_return=107000.0,
        net_return_per_quintal=2140.0,
        price_provenance="krishibazaar_buyer_a",
        is_price_simulated=True,
        meets_minimum_condition=True,
        recommendation_note="AgriExport direct pickup.",
    )
    opt_res = OptimizationResult(
        crop="Onion",
        quantity_quintals=50.0,
        minimum_net_return_inr=90000.0,
        all_options=[opt1, opt2],
        feasible_options=[opt1, opt2],
        recommended_option=opt1,
        summary_explanation="2 channels evaluated. Nashik Mandi ranked #1.",
    )

    return {
        "mandi_result": mandi_res,
        "nearby_result": None,
        "weather_result": weather_res,
        "buyer_result": buyer_res,
        "transport_result": transport_res,
        "optimization_result": opt_res,
        "target_date": "2026-10-22",
    }

    return {
        "mandi_result": mandi_res,
        "nearby_result": None,
        "weather_result": weather_res,
        "buyer_result": buyer_res,
        "transport_result": transport_res,
        "optimization_result": opt_res,
        "target_date": "2026-10-22",
    }


@pytest.mark.asyncio
async def test_full_inquiry_flow_generates_carousel_card(
    mock_ctx, sample_complete_intent, sample_coord_results
):
    """Test complete produce inquiry dispatches CarouselCard and rich text fallback."""
    farmer_id = "agent1_farmer_nashik"
    inbound_msg = ChatMessage(
        content=[TextContent(text="I have 50 quintals of onions in Nashik, selling tomorrow")]
    )

    with (
        patch(
            "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
            new=AsyncMock(return_value=sample_complete_intent),
        ),
        patch(
            "krishibazaar.agents.orchestrator.coordinate_specialists_async",
            new=AsyncMock(return_value=sample_coord_results),
        ),
    ):
        await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    assert mock_ctx.send.call_count == 2  # Acknowledgement + ChatMessage
    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    assert isinstance(reply_msg, ChatMessage)

    # 1. Verify text fallback
    text_content = reply_msg.content[0]
    assert isinstance(text_content, TextContent)
    assert "KrishiBazaar: Request Understood & Optimized!" in text_content.text
    assert "Nashik Mandi" in text_content.text
    assert "Channel Ranking" in text_content.text or "Rank 1" in text_content.text

    # 2. Verify interactive card payload
    card_meta = reply_msg.content[1]
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, CarouselCardPayload)
    assert len(card.payload.items) == 2
    assert card.payload.items[0].primary_cta.selection["rank"] == 1


@pytest.mark.asyncio
async def test_option_selection_via_card_cta_generates_review_card(
    mock_ctx, sample_coord_results
):
    """Test clicking option 1 CTA on the CarouselCard generates a ReviewCard proposal."""
    farmer_id = "agent1_farmer_nashik"
    opt_res = sample_coord_results["optimization_result"]
    storage.save_session(
        session_id=farmer_id,
        farmer_id=farmer_id,
        state="OFFERS_OPTIMIZED",
        inquiry=FarmerInquiry(
            farmer_id=farmer_id,
            crop="Onion",
            quantity_quintals=50.0,
            location_district="Nashik",
            selling_deadline="2026-10-22",
        ),
        active_options=opt_res.all_options,
    )

    # Simulate card CTA click
    cta_resp = create_card_response_content(
        selection={"action": "select_channel", "rank": 1, "option_id": "opt_mandi_1"}
    )
    inbound_msg = ChatMessage(content=[TextContent(text=""), cta_resp])

    await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_fallback = reply_msg.content[0].text
    assert "PROPOSAL SUMMARY" in text_fallback or "Proposal" in text_fallback
    assert "Nashik Mandi" in text_fallback
    assert "SAFETY GATE" in text_fallback or "Explicit Approval Required" in text_fallback

    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, ReviewCardPayload)
    assert card.payload.approve_cta.selection["action"] == "approve_booking"
    assert card.payload.reject_cta.selection["action"] == "reject_booking"


@pytest.mark.asyncio
async def test_option_selection_via_text_fallback_digit(
    mock_ctx, sample_coord_results
):
    """Test selecting option via plain text '1' or 'select option 1' generates ReviewCard."""
    farmer_id = "agent1_farmer_nashik"
    opt_res = sample_coord_results["optimization_result"]
    storage.save_session(
        session_id=farmer_id,
        farmer_id=farmer_id,
        state="OFFERS_OPTIMIZED",
        inquiry=FarmerInquiry(
            farmer_id=farmer_id,
            crop="Onion",
            quantity_quintals=50.0,
            location_district="Nashik",
            selling_deadline="2026-10-22",
        ),
        active_options=opt_res.all_options,
    )

    inbound_msg = ChatMessage(content=[TextContent(text="1")])
    await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, ReviewCardPayload)


@pytest.mark.asyncio
async def test_invalid_option_selection_error_handling(
    mock_ctx, sample_coord_results
):
    """Test selecting non-existent rank (e.g. 99) returns error DetailCard and does not create proposal."""
    farmer_id = "agent1_farmer_nashik"
    opt_res = sample_coord_results["optimization_result"]
    storage.save_session(
        session_id=farmer_id,
        farmer_id=farmer_id,
        state="OFFERS_OPTIMIZED",
        active_options=opt_res.all_options,
    )

    inbound_msg = ChatMessage(content=[TextContent(text="select option 99")])
    await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = reply_msg.content[0].text
    assert "Invalid Channel Selection" in text_content
    assert "Option #99 does not exist" in text_content

    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, DetailCardPayload)
    assert "Invalid" in card.payload.title

    # Confirm no proposal was created in storage
    prop = storage.get_pending_proposal_for_session(farmer_id)
    assert prop is None


@pytest.mark.asyncio
async def test_explicit_approval_via_card_cta(mock_ctx, sample_coord_results):
    """Test clicking Approve CTA on ReviewCard generates simulated booking record and confirmation DetailCard."""
    farmer_id = "agent1_farmer_nashik"
    opt = sample_coord_results["optimization_result"].all_options[0]
    now = datetime.now(timezone.utc)
    proposal = BookingProposal(
        proposal_id="PROP-20261021-NASHIK",
        session_id=farmer_id,
        farmer_id=farmer_id,
        selected_option=opt,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-22",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(minutes=15)).isoformat(),
        is_consumed=False,
    )
    storage.save_proposal(proposal)
    storage.save_session(
        session_id=farmer_id,
        farmer_id=farmer_id,
        state="AWAITING_APPROVAL",
        active_options=[opt],
    )

    # Send card approval response
    appr_resp = create_card_response_content(
        selection={"action": "approve_booking", "proposal_id": proposal.proposal_id}
    )
    inbound_msg = ChatMessage(content=[appr_resp])
    await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_fallback = reply_msg.content[0].text
    assert "BOOKING CONFIRMATION RECEIPT" in text_fallback or "CONFIRMED" in text_fallback

    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, DetailCardPayload)
    assert "Booking Confirmed" in card.payload.title

    # Verify SQLite persistence
    bookings = storage.list_bookings(farmer_id=farmer_id)
    assert len(bookings) == 1
    assert bookings[0].crop == proposal.crop
    assert bookings[0].offer_id == proposal.selected_option.scenario_id
    assert bookings[0].is_simulated_booking is True


@pytest.mark.asyncio
async def test_casual_message_blocked_by_approval_safety_gate(
    mock_ctx, sample_coord_results
):
    """Test sending casual greeting ('ok', 'hi') does not confirm booking and re-prompts ReviewCard."""
    farmer_id = "agent1_farmer_nashik"
    opt = sample_coord_results["optimization_result"].all_options[0]
    now = datetime.now(timezone.utc)
    proposal = BookingProposal(
        proposal_id="PROP-20261021-GATE",
        session_id=farmer_id,
        farmer_id=farmer_id,
        selected_option=opt,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-22",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(minutes=15)).isoformat(),
        is_consumed=False,
    )
    storage.save_proposal(proposal)
    storage.save_session(
        session_id=farmer_id,
        farmer_id=farmer_id,
        state="AWAITING_APPROVAL",
    )

    inbound_msg = ChatMessage(content=[TextContent(text="ok")])
    await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = reply_msg.content[0].text
    assert "Pending Proposal Awaiting Explicit Confirmation" in text_content
    assert "Casual messages or ambiguous acknowledgements do NOT confirm bookings" in text_content

    # Ensure booking was NOT created
    bookings = storage.list_bookings(farmer_id=farmer_id)
    assert len(bookings) == 0


@pytest.mark.asyncio
async def test_duplicate_approval_attempt_blocked(mock_ctx, sample_coord_results):
    """Test duplicate approval is rejected and does not create duplicate booking."""
    farmer_id = "agent1_farmer_nashik"
    opt = sample_coord_results["optimization_result"].all_options[0]
    now = datetime.now(timezone.utc)
    proposal = BookingProposal(
        proposal_id="PROP-20261021-DUP",
        session_id=farmer_id,
        farmer_id=farmer_id,
        selected_option=opt,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-22",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(minutes=15)).isoformat(),
        is_consumed=True,  # Already consumed!
    )
    storage.save_proposal(proposal)

    inbound_msg = ChatMessage(content=[TextContent(text="approve PROP-20261021-DUP")])
    await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = reply_msg.content[0].text
    assert "Duplicate Approval / Already Processed" in text_content

    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert card is not None
    assert "Already Processed" in card.payload.title


@pytest.mark.asyncio
async def test_expired_proposal_blocked(mock_ctx, sample_coord_results):
    """Test expired proposal (>15 minutes) is rejected and marked consumed."""
    farmer_id = "agent1_farmer_nashik"
    opt = sample_coord_results["optimization_result"].all_options[0]
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    proposal = BookingProposal(
        proposal_id="PROP-20261021-EXP",
        session_id=farmer_id,
        farmer_id=farmer_id,
        selected_option=opt,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-22",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=(past - timedelta(minutes=15)).isoformat(),
        expires_at_utc=past.isoformat(),
        is_consumed=False,
    )
    storage.save_proposal(proposal)

    inbound_msg = ChatMessage(content=[TextContent(text="approve PROP-20261021-EXP")])
    await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = reply_msg.content[0].text
    assert "Proposal Expired" in text_content

    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert card is not None
    assert "Expired" in card.payload.title


@pytest.mark.asyncio
async def test_proposal_cancellation_flow(mock_ctx, sample_coord_results):
    """Test cancelling a proposal cancels it in storage."""
    farmer_id = "agent1_farmer_nashik"
    opt = sample_coord_results["optimization_result"].all_options[0]
    now = datetime.now(timezone.utc)
    proposal = BookingProposal(
        proposal_id="PROP-20261021-CANCEL",
        session_id=farmer_id,
        farmer_id=farmer_id,
        selected_option=opt,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-22",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(minutes=15)).isoformat(),
        is_consumed=False,
    )
    storage.save_proposal(proposal)
    storage.save_session(
        session_id=farmer_id,
        farmer_id=farmer_id,
        state="AWAITING_APPROVAL",
    )

    inbound_msg = ChatMessage(content=[TextContent(text="cancel")])
    await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    assert "Booking Cancelled" in reply_msg.content[0].text


@pytest.mark.asyncio
async def test_incomplete_inquiry_returns_form_card(mock_ctx):
    """Test incomplete inquiry returns FormCard and follow-up question."""
    farmer_id = "agent1_farmer_incomplete"
    incomplete_intent = FarmerIntent(
        crop="tomato",
        quantity_quintals=None,
        origin_location="Pune",
        selling_deadline=None,
        is_complete=False,
        follow_up_question="How many quintals of tomatoes do you have?",
    )

    mandi_res = MandiPriceResult(
        success=True,
        commodity="tomato",
        records=[],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET)",
            retrieval_timestamp_utc="2026-10-21T00:00:00Z",
            observation_date="2026-10-20",
            freshness_status="LIVE",
            is_live=True,
        ),
    )

    inbound_msg = ChatMessage(content=[TextContent(text="Tomato prices in Pune")])

    with (
        patch(
            "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
            new=AsyncMock(return_value=incomplete_intent),
        ),
        patch(
            "krishibazaar.agents.orchestrator.get_mandi_prices_async",
            new=AsyncMock(return_value=mandi_res),
        ),
    ):
        await handle_chat_message(mock_ctx, farmer_id, inbound_msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = reply_msg.content[0].text
    assert "Mandi Market Enquiry" in text_content
    assert "How many quintals" in text_content

    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, FormCardPayload)
    field_names = [f.name for f in card.payload.fields]
    assert "quantity_quintals" in field_names


@pytest.mark.asyncio
async def test_pending_proposal_conversation_state_multi_turn_regression(
    mock_ctx, sample_complete_intent, sample_coord_results
):
    """
    End-to-end regression test for Phase 7 pending-proposal conversation state:
    1. Channel selected -> ReviewCard generated with Proposal ID (e.g. PROP-...).
    2. Farmer sends 'ok' -> Proposal is retained, Orchestrator explains explicit approval is required,
       re-displays ReviewCard, does NOT confirm booking, and does NOT ask for crop details.
    3. Farmer sends 'sounds good' -> Same: proposal retained, explicit approval explained, ReviewCard re-displayed.
    4. Farmer sends with mention prefix '@agent1_orch ok' -> Handled identically without misclassifying.
    5. Farmer sends explicit approval 'Confirm booking' -> Booking record created in ledger, DetailCard confirmation.
    6. Farmer sends duplicate approval -> Duplicate approval blocked, no second booking created.
    """
    farmer_id = "agent1_farmer_nashik"
    opt_res = sample_coord_results["optimization_result"]

    # Step 1: Initial optimization session state
    storage.save_session(
        session_id=farmer_id,
        farmer_id=farmer_id,
        state="OFFERS_OPTIMIZED",
        inquiry=FarmerInquiry(
            farmer_id=farmer_id,
            crop="Onion",
            quantity_quintals=50.0,
            location_district="Nashik",
            selling_deadline="2026-10-22",
        ),
        active_options=opt_res.all_options,
    )

    # Step 2: Farmer selects option 1 (e.g. "Select option 1")
    mock_ctx.send.reset_mock()
    msg_select = ChatMessage(content=[TextContent(text="Select option 1")])
    await handle_chat_message(mock_ctx, farmer_id, msg_select)

    assert mock_ctx.send.call_count == 2
    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    assert "PROPOSAL SUMMARY" in reply_msg.content[0].text or "Proposal" in reply_msg.content[0].text
    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert isinstance(card.payload, ReviewCardPayload)

    # Verify pending proposal in storage
    pending = storage.get_pending_proposal_for_session(farmer_id)
    assert pending is not None
    assert pending.is_consumed is False
    proposal_id = pending.proposal_id

    # Step 3: Farmer sends "ok"
    mock_ctx.send.reset_mock()
    msg_ok = ChatMessage(content=[TextContent(text="ok")])
    await handle_chat_message(mock_ctx, farmer_id, msg_ok)

    reply_ok = mock_ctx.send.call_args_list[1][0][1]
    ok_text = reply_ok.content[0].text
    assert "Pending Proposal Awaiting Explicit Confirmation" in ok_text
    assert proposal_id in ok_text
    assert "Casual messages or ambiguous acknowledgements do NOT confirm bookings" in ok_text
    # Verify it does NOT ask again for crop, quantity, or location
    assert "Please specify this detail" not in ok_text
    assert "How many quintals" not in ok_text
    # Verify ReviewCard is redisplayed
    ok_card = extract_card(reply_ok.content[1])
    assert isinstance(ok_card.payload, ReviewCardPayload)
    # Verify no booking was created
    assert len(storage.list_bookings(farmer_id=farmer_id)) == 0
    # Verify proposal is STILL unconsumed
    pending = storage.get_pending_proposal_for_session(farmer_id)
    assert pending is not None
    assert pending.is_consumed is False

    # Step 4: Farmer sends "sounds good"
    mock_ctx.send.reset_mock()
    msg_sg = ChatMessage(content=[TextContent(text="sounds good")])
    await handle_chat_message(mock_ctx, farmer_id, msg_sg)

    reply_sg = mock_ctx.send.call_args_list[1][0][1]
    sg_text = reply_sg.content[0].text
    assert "Pending Proposal Awaiting Explicit Confirmation" in sg_text
    assert proposal_id in sg_text
    assert len(storage.list_bookings(farmer_id=farmer_id)) == 0
    assert storage.get_pending_proposal_for_session(farmer_id).is_consumed is False

    # Step 5: Farmer sends message with Agentverse mention prefix "@agent1_orch ok"
    mock_ctx.send.reset_mock()
    msg_mention = ChatMessage(content=[TextContent(text="@agent1_orch ok")])
    await handle_chat_message(mock_ctx, farmer_id, msg_mention)

    reply_mention = mock_ctx.send.call_args_list[1][0][1]
    mention_text = reply_mention.content[0].text
    assert "Pending Proposal Awaiting Explicit Confirmation" in mention_text
    assert len(storage.list_bookings(farmer_id=farmer_id)) == 0

    # Step 6: Farmer sends another variation "@agent1_orch sounds good"
    mock_ctx.send.reset_mock()
    msg_mention_sg = ChatMessage(content=[TextContent(text="@agent1_orch sounds good")])
    await handle_chat_message(mock_ctx, farmer_id, msg_mention_sg)

    reply_mention_sg = mock_ctx.send.call_args_list[1][0][1]
    assert "Pending Proposal Awaiting Explicit Confirmation" in reply_mention_sg.content[0].text
    assert len(storage.list_bookings(farmer_id=farmer_id)) == 0

    # Step 7: Farmer sends explicit approval with proposal ID
    mock_ctx.send.reset_mock()
    msg_approve = ChatMessage(content=[TextContent(text=f"Approve proposal {proposal_id}")])
    await handle_chat_message(mock_ctx, farmer_id, msg_approve)

    reply_appr = mock_ctx.send.call_args_list[1][0][1]
    appr_text = reply_appr.content[0].text
    assert "BOOKING CONFIRMATION RECEIPT" in appr_text or "CONFIRMED" in appr_text
    appr_card = extract_card(reply_appr.content[1])
    assert isinstance(appr_card.payload, DetailCardPayload)
    assert "Booking Confirmed" in appr_card.payload.title

    # Verify exactly 1 booking was created
    bookings = storage.list_bookings(farmer_id=farmer_id)
    assert len(bookings) == 1
    assert bookings[0].is_simulated_booking is True
    # Verify proposal is now consumed
    assert storage.get_proposal(proposal_id).is_consumed is True

    # Step 8: Farmer sends approval again (duplicate approval guard)
    mock_ctx.send.reset_mock()
    msg_dup = ChatMessage(content=[TextContent(text=f"Approve proposal {proposal_id}")])
    await handle_chat_message(mock_ctx, farmer_id, msg_dup)

    reply_dup = mock_ctx.send.call_args_list[1][0][1]
    dup_text = reply_dup.content[0].text
    assert "Duplicate Approval / Already Processed" in dup_text
    # Ensure no second booking was created
    assert len(storage.list_bookings(farmer_id=farmer_id)) == 1


@pytest.mark.asyncio
async def test_proposal_approval_with_mismatched_id_rejected(
    mock_ctx, sample_coord_results
):
    """Test approving with mismatched proposal ID is rejected and retains pending proposal."""
    farmer_id = "agent1_farmer_nashik"
    opt = sample_coord_results["optimization_result"].all_options[0]
    now = datetime.now(timezone.utc)
    proposal = BookingProposal(
        proposal_id="PROP-20261021-ACTIVE",
        session_id=farmer_id,
        farmer_id=farmer_id,
        selected_option=opt,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-22",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(minutes=15)).isoformat(),
        is_consumed=False,
    )
    storage.save_proposal(proposal)

    # Attempt approval with mismatched proposal ID
    msg_mismatch = ChatMessage(content=[TextContent(text="approve proposal PROP-20261021-WRONG")])
    await handle_chat_message(mock_ctx, farmer_id, msg_mismatch)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = reply_msg.content[0].text
    assert "Proposal ID Mismatch" in text_content
    assert "PROP-20261021-WRONG" in text_content
    assert "PROP-20261021-ACTIVE" in text_content

    # Ensure no booking was created and proposal remains unconsumed
    assert len(storage.list_bookings(farmer_id=farmer_id)) == 0
    active_prop = storage.get_pending_proposal_for_session(farmer_id)
    assert active_prop is not None
    assert active_prop.is_consumed is False


@pytest.mark.asyncio
async def test_casual_acknowledgements_never_create_booking_variations(
    mock_ctx, sample_coord_results
):
    """Test that all common casual acknowledgement phrases never create bookings."""
    farmer_id = "agent1_farmer_nashik"
    opt = sample_coord_results["optimization_result"].all_options[0]
    now = datetime.now(timezone.utc)
    proposal = BookingProposal(
        proposal_id="PROP-20261021-TESTACK",
        session_id=farmer_id,
        farmer_id=farmer_id,
        selected_option=opt,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-22",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(minutes=15)).isoformat(),
        is_consumed=False,
    )
    storage.save_proposal(proposal)

    phrases = [
        "ok",
        "yes",
        "sounds good",
        "thanks",
        "looks good",
        "okay",
        "sure",
        "cool",
        "k",
        "fine",
        "ok sounds good",
        "yes thanks",
        "@agent1_orch ok",
        "<@agent1_orch> sounds good",
    ]

    for phrase in phrases:
        mock_ctx.send.reset_mock()
        msg = ChatMessage(content=[TextContent(text=phrase)])
        await handle_chat_message(mock_ctx, farmer_id, msg)

        reply_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = reply_msg.content[0].text
        assert "Pending Proposal Awaiting Explicit Confirmation" in text_content, f"Failed for phrase '{phrase}'"
        assert len(storage.list_bookings(farmer_id=farmer_id)) == 0, f"Booking incorrectly created for '{phrase}'"

    # Confirm proposal is STILL unconsumed and awaiting approval
    pending = storage.get_pending_proposal_for_session(farmer_id)
    assert pending is not None
    assert pending.is_consumed is False


@pytest.mark.asyncio
async def test_expired_proposal_blocked_on_casual_acknowledgement(
    mock_ctx, sample_coord_results
):
    """Test that sending 'ok' when a pending proposal is expired notifies of expiration, consumes it, and creates no booking."""
    farmer_id = "agent1_farmer_nashik"
    opt = sample_coord_results["optimization_result"].all_options[0]
    past = datetime.now(timezone.utc) - timedelta(minutes=25)
    proposal = BookingProposal(
        proposal_id="PROP-20261021-EXPACK",
        session_id=farmer_id,
        farmer_id=farmer_id,
        selected_option=opt,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-22",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=(past - timedelta(minutes=15)).isoformat(),
        expires_at_utc=past.isoformat(),
        is_consumed=False,
    )
    storage.save_proposal(proposal)

    msg = ChatMessage(content=[TextContent(text="ok")])
    await handle_chat_message(mock_ctx, farmer_id, msg)

    reply_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = reply_msg.content[0].text
    assert "Proposal Expired" in text_content
    card_meta = reply_msg.content[1]
    card = extract_card(card_meta)
    assert card is not None
    assert "Expired" in card.payload.title

    # Confirm proposal is now marked consumed and no booking was created
    assert storage.get_proposal("PROP-20261021-EXPACK").is_consumed is True
    assert len(storage.list_bookings(farmer_id=farmer_id)) == 0
