"""
Regression tests for Fresh ASI:One Agent Responses, Financial Itemization,
Net-Return Calculation Accuracy, and Session State Isolation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from uagents import Context
from uagents_core.contrib.protocols.chat.cards import (
    CarouselCardPayload,
    DetailCardPayload,
    ReviewCardPayload,
    extract_card,
)
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    TextContent,
    MetadataContent,
)

import agentverse_agent as ava
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    FarmerInquiry,
    NetReturnBreakdown,
    OptimizationResult,
)
from krishibazaar.services.card_service import (
    build_channel_recommendations_card,
    build_proposal_review_card,
)
from krishibazaar.storage import storage


# ---------------------------------------------------------------------------
# Test 1: Exact 50 Quintals Onion Nashik Net-Return Calculations
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_50_quintals_onion_nashik_calculation_accuracy():
    """
    Verify the complete arithmetic pipeline for 50 quintals onion in Nashik with ₹85,000 target:
    - Sahyadri: gross ₹108,562.50 (₹2,171.25/qtl), freight ₹0, cess ₹0, hamali ₹400, net ₹108,162.50.
    - Lasalgaon: gross ₹112,500.00 (₹2,250/qtl), freight ₹7,875, cess ₹1,687.50, hamali ₹750, net ₹102,187.50.
    - Pune: gross ₹105,000.00 (₹2,100/qtl), freight ₹44,100, cess ₹1,575, hamali ₹750, net ₹58,575.00.
    """
    intent = ava.FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        selling_deadline="Tomorrow",
        minimum_net_return_inr=85000.0,
        is_complete=True,
    )

    opt = await ava.calculate_channel_recommendations(intent)
    assert len(opt.options) == 3

    # Ranking must be strictly descending by net return
    assert opt.options[0].rank == 1
    assert opt.options[1].rank == 2
    assert opt.options[2].rank == 3
    assert opt.options[0].net_return_inr > opt.options[1].net_return_inr > opt.options[2].net_return_inr

    # Option 1: Sahyadri Agro Producer Co. (Doorstep)
    sahyadri = opt.options[0]
    assert "Sahyadri" in sahyadri.channel_name
    assert sahyadri.gross_rate_per_quintal == 2171.25
    assert sahyadri.gross_value_inr == 108562.50
    assert sahyadri.transport_cost == 0.0
    assert sahyadri.mandi_tax_and_cess == 0.0
    assert sahyadri.handling_and_loading == 400.00
    assert sahyadri.estimated_weather_loss == 0.00
    assert sahyadri.total_deductions == 400.00
    assert sahyadri.net_return_inr == 108162.50
    assert sahyadri.net_rate_per_quintal == 2163.25
    assert sahyadri.meets_target is True
    assert sahyadri.is_simulated is True

    # Option 2: Lasalgaon APMC
    lasalgaon = opt.options[1]
    assert "Lasalgaon" in lasalgaon.channel_name
    assert lasalgaon.gross_rate_per_quintal == 2250.00
    assert lasalgaon.gross_value_inr == 112500.00
    assert lasalgaon.transport_cost == 7875.00  # 35 km * 4.5 * 50
    assert lasalgaon.mandi_tax_and_cess == 1687.50  # 1.5% of 112,500
    assert lasalgaon.handling_and_loading == 750.00  # 50 * 15
    assert lasalgaon.estimated_weather_loss == 0.00
    assert lasalgaon.total_deductions == 10312.50
    assert lasalgaon.net_return_inr == 102187.50
    assert lasalgaon.net_rate_per_quintal == 2043.75
    assert lasalgaon.meets_target is True
    assert lasalgaon.is_simulated is False

    # Option 3: Pune APMC
    pune = opt.options[2]
    assert "Pune" in pune.channel_name
    assert pune.gross_rate_per_quintal == 2100.00
    assert pune.gross_value_inr == 105000.00
    assert pune.transport_cost == 44100.00  # 210 km * 4.2 * 50
    assert pune.mandi_tax_and_cess == 1575.00  # 1.5% of 105,000
    assert pune.handling_and_loading == 750.00  # 50 * 15
    assert pune.estimated_weather_loss == 0.00
    assert pune.total_deductions == 46425.00
    assert pune.net_return_inr == 58575.00
    assert pune.net_rate_per_quintal == 1171.50
    assert pune.meets_target is False  # 58,575 < 85,000
    assert pune.is_simulated is False


# ---------------------------------------------------------------------------
# Test 2: Itemized Cost Breakdown in Carousel and Review Cards
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_itemized_cost_breakdown_in_cards():
    """
    Verify cards clearly separate APMC cess from hamali/labor and label zero freight as farmgate pickup.
    """
    intent = ava.FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        selling_deadline="Tomorrow",
        minimum_net_return_inr=85000.0,
        is_complete=True,
    )

    opt = await ava.calculate_channel_recommendations(intent)
    card_meta = ava.build_channel_carousel_card(opt)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, CarouselCardPayload)

    # Item 1: Sahyadri (Doorstep farmgate)
    item_sahyadri = card.payload.items[0]
    assert "Top In-Pocket Payout" in [b.label for b in item_sahyadri.badges]
    assert "Farmgate Zero-Freight" in [b.label for b in item_sahyadri.badges]
    assert "Simulated Buyer Offer" in [b.label for b in item_sahyadri.badges]
    # Check separate itemization of freight, cess, and hamali
    assert "Freight: INR 0.00 (Farmgate Pickup)" in item_sahyadri.secondary_text
    assert "APMC Cess: INR 0.00 (Assumed Direct-Trade Exemption - Unverified)" in item_sahyadri.secondary_text
    assert "Hamali/Labor: INR 400.00" in item_sahyadri.secondary_text
    assert "Total Deductions: INR 400.00" in item_sahyadri.secondary_text

    # Item 2: Lasalgaon APMC
    item_lasalgaon = card.payload.items[1]
    assert "Mandi Auction Benchmark" in [b.label for b in item_lasalgaon.badges]
    assert "Target Met" in [b.label for b in item_lasalgaon.badges]
    assert "Freight: INR 7,875.00" in item_lasalgaon.secondary_text
    assert "APMC Cess: INR 1,687.50" in item_lasalgaon.secondary_text
    assert "Hamali/Labor: INR 750.00" in item_lasalgaon.secondary_text
    assert "Total Deductions: INR 10,312.50" in item_lasalgaon.secondary_text

    # Item 3: Pune APMC
    item_pune = card.payload.items[2]
    assert "Target Shortfall" in [b.label for b in item_pune.badges]
    assert "Freight: INR 44,100.00" in item_pune.secondary_text
    assert "APMC Cess: INR 1,575.00" in item_pune.secondary_text
    assert "Hamali/Labor: INR 750.00" in item_pune.secondary_text
    assert "Total Deductions: INR 46,425.00" in item_pune.secondary_text


@pytest.mark.asyncio
async def test_proposal_review_card_itemization():
    """
    Verify build_proposal_review_card presents distinct rows for Freight, Cess, Hamali, Weather loss.
    """
    prop_data = {
        "proposal_id": "PROP-TEST-001",
        "crop": "Onion",
        "quantity_quintals": 50.0,
        "channel_name": "Sahyadri Agro Producer Co. (Doorstep)",
        "channel_type": "DIRECT_BUYER_FARMGATE",
        "gross_amount": 108562.50,
        "transport_cost": 0.0,
        "mandi_tax_and_cess": 0.0,
        "handling_and_loading": 400.0,
        "estimated_weather_loss": 0.0,
        "total_deductions": 400.0,
        "estimated_net_payout": 108162.50,
        "pickup_date": "2026-10-25 (08:00 AM)",
        "expires_at_utc": "2026-10-25T08:15:00Z",
        "data_provenance": "Simulated Buyer Benchmark (Direct Farmgate)",
        "is_consumed": False,
    }

    card_meta = ava.build_proposal_review_card(prop_data)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, ReviewCardPayload)

    row_dict = {r.label: r.value for r in card.payload.summary_rows}
    assert "PROP-TEST-001" in row_dict["Proposal Reference"]
    assert "50 quintals Onion" in row_dict["Crop & Quantity"]
    assert "INR 108,562.50" in row_dict["Gross Value"]
    assert "INR 0.00 (Farmgate Pickup)" in row_dict["Transport Freight"]
    assert "INR 0.00 (Assumed Direct-Trade Exemption - Unverified)" in row_dict["APMC Market Cess"]
    assert "INR 400.00" in row_dict["Hamali / Labour Charges"]
    assert "INR 400.00" in row_dict["Itemized Deductions"]
    assert "INR 108,162.50" in row_dict["Estimated Net In-Pocket Payout"]


# ---------------------------------------------------------------------------
# Test 3: Fresh Chat Intent Parsing and Routing Safety
# ---------------------------------------------------------------------------
def test_farmer_intent_extraction_complete_inquiry():
    """
    Test extraction of the user's complete prompt in a fresh chat.
    Must extract crop, quantity, location, target, deadline immediately.
    """
    msg = (
        "I am a farmer in Nashik with 50 quintals of onions to sell tomorrow. "
        "My minimum target is ₹85,000 net. Compare mandi prices and all available selling options. "
        "Show gross value, transport freight, market charges (cess and hamali separately), "
        "estimated weather loss, total deductions, and estimated net return. "
        "Clearly distinguish mandi price benchmarks from confirmed buyer offers, "
        "label simulated offers, and disclose unavailable data. "
        "Do not create a booking or initiate payment yet."
    )

    intent = ava.parse_farmer_intent(msg)
    assert intent.crop == "onion"
    assert intent.quantity_quintals == 50.0
    assert intent.origin_location == "Nashik"
    assert intent.minimum_net_return_inr == 85000.0
    assert intent.is_complete is True


@pytest.mark.asyncio
async def test_fresh_chat_complete_inquiry_does_not_return_welcome():
    """
    Verify sending a complete inquiry in a fresh session immediately returns
    ranked channels, NOT a generic welcome or onboarding prompt.
    """
    mock_ctx = MagicMock(spec=Context)
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    sender = f"agent1test_fresh_{uuid4().hex[:6]}"
    user_msg_text = (
        "I am a farmer in Nashik with 50 quintals of onions to sell tomorrow. "
        "My minimum target is ₹85,000 net. Compare mandi prices and all available selling options."
    )
    msg = ChatMessage(content=[TextContent(text=user_msg_text)])

    await ava.handle_chat_message(mock_ctx, sender, msg)

    assert mock_ctx.send.called
    sent_msg = mock_ctx.send.call_args[0][1]
    sent_text = "".join(item.text for item in sent_msg.content if isinstance(item, TextContent))

    # Must NOT be a welcome message
    assert "Welcome to KrishiBazaar" not in sent_text
    # Must contain the optimized ranked options
    assert "Ranked Channel Analysis" in sent_text
    assert "Sahyadri Agro Producer Co." in sent_text
    assert "Lasalgaon APMC" in sent_text
    assert "Pune APMC" in sent_text


@pytest.mark.asyncio
async def test_negation_guard_prevents_accidental_booking_or_payment():
    """
    Verify text stating 'Do not create a booking or initiate payment yet'
    does NOT trigger approve_booking or authorize_payment actions.
    """
    mock_ctx = MagicMock(spec=Context)
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    sender = f"agent1test_negation_{uuid4().hex[:6]}"
    # Pre-populate session with an active proposal
    ava.SESSIONS[sender] = {
        "active_proposal_id": "PROP-OLD-123",
        "last_optimization": {"crop": "Onion", "quantity_quintals": 50.0, "options": []},
    }
    ava.PROPOSALS["PROP-OLD-123"] = {
        "proposal_id": "PROP-OLD-123",
        "crop": "Onion",
        "quantity_quintals": 50.0,
        "is_consumed": False,
        "expires_at_utc": "2099-01-01T00:00:00Z",
    }

    user_msg_text = (
        "I have 50 quintals of onion in Nashik. "
        "Do not confirm booking yet and do not authorize payment."
    )
    msg = ChatMessage(content=[TextContent(text=user_msg_text)])

    await ava.handle_chat_message(mock_ctx, sender, msg)

    # Proposal must NOT have been marked consumed
    assert ava.PROPOSALS["PROP-OLD-123"]["is_consumed"] is False
    # No booking should have been created
    assert not any(b.get("farmer_id") == sender for b in ava.BOOKINGS.values())


# ---------------------------------------------------------------------------
# Test 4: Session Isolation Across Inquiries
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fresh_inquiry_clears_prior_session_proposal_and_booking():
    """
    Ensure that when a fresh produce inquiry arrives, prior proposal/booking
    references in memory and storage are purged, preventing old quotes from leaking.
    """
    sender = f"agent1test_session_iso_{uuid4().hex[:6]}"
    sess = ava.SESSIONS.setdefault(sender, {})
    sess["active_proposal_id"] = "PROP-OLD-STALE"
    sess["active_booking_id"] = "KB-BOOK-OLD-STALE"
    sess["pending_payment_reference"] = "PAY-OLD-STALE"

    mock_ctx = MagicMock(spec=Context)
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    new_inquiry = "I have 40 quintals of Tomato in Pune tomorrow."
    msg = ChatMessage(content=[TextContent(text=new_inquiry)])

    await ava.handle_chat_message(mock_ctx, sender, msg)

    # Stale IDs must be purged from session
    assert "active_proposal_id" not in sess
    assert "active_booking_id" not in sess
    assert "pending_payment_reference" not in sess
    # New optimization must be for Tomato
    assert sess["last_optimization"]["crop"].lower() == "tomato"
    assert sess["last_optimization"]["quantity_quintals"] == 40.0


def test_storage_session_clear_flags():
    """
    Verify storage.save_session properly clears active_proposal_id and active_booking_id
    when clear_active_proposal and clear_active_booking are passed.
    """
    session_id = f"test_sess_{uuid4().hex[:6]}"
    farmer_id = "test_farmer_01"

    # 1. Save initial session with proposal and booking
    storage.save_session(
        session_id=session_id,
        farmer_id=farmer_id,
        state="BOOKING_CONFIRMED",
        active_proposal_id="PROP-INITIAL-1",
        active_booking_id="KB-BOOK-INITIAL-1",
    )

    s1 = storage.get_session(session_id)
    assert s1 is not None
    assert s1["active_proposal_id"] == "PROP-INITIAL-1"
    assert s1["active_booking_id"] == "KB-BOOK-INITIAL-1"

    # 2. Save fresh optimization inquiry with clear flags
    storage.save_session(
        session_id=session_id,
        farmer_id=farmer_id,
        state="OFFERS_OPTIMIZED",
        clear_active_proposal=True,
        clear_active_booking=True,
        clear_pending_payment=True,
    )

    s2 = storage.get_session(session_id)
    assert s2 is not None
    assert s2["active_proposal_id"] is None
    assert s2["active_booking_id"] is None
    assert s2["pending_payment_reference"] is None
    assert s2["state"] == "OFFERS_OPTIMIZED"
