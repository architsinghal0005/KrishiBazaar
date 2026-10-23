"""
Unit tests for KrishiBazaar Interactive Card Service (Phase 7).
Tests card payload construction, Card Protocol V1 compliance, interaction extraction,
selection validation, and text fallback generation.
"""

from datetime import datetime, timezone
import pytest
from uuid import uuid4

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    EndSessionContent,
    MetadataContent,
    TextContent,
)
from uagents_core.contrib.protocols.chat.cards import (
    CarouselCardPayload,
    DetailCardPayload,
    FormCardPayload,
    ReviewCardPayload,
    create_card_content,
    create_card_response_content,
    extract_card,
    extract_card_response,
)

from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    DataProvenance,
    FarmerIntent,
    NetReturnBreakdown,
    OptimizationResult,
    PaymentRecord,
    PaymentReviewDetails,
    PaymentStatus,
)
from krishibazaar.services.booking_service import BookingActionType
from krishibazaar.services.card_service import (
    build_booking_confirmed_card,
    build_channel_recommendations_card,
    build_chat_response,
    build_duplicate_approval_card,
    build_expired_proposal_card,
    build_farmer_inquiry_card,
    build_incomplete_inquiry_form_card,
    build_invalid_selection_card,
    build_proposal_review_card,
    build_payment_review_card,
    build_payment_confirmed_card,
    extract_interaction_action,
)


@pytest.fixture
def sample_intent() -> FarmerIntent:
    return FarmerIntent(
        crop="wheat",
        quantity_quintals=50.0,
        origin_location="Indore",
        selling_deadline="2026-10-25",
        minimum_net_return_inr=2200.0,
        storage_preference="no_storage",
        is_complete=True,
        extraction_source="asi1",
    )


@pytest.fixture
def sample_optimization_result() -> OptimizationResult:
    opt1 = NetReturnBreakdown(
        scenario_id="opt_1",
        rank=1,
        channel_type="MANDI",
        destination_name="Indore Mandi",
        distance_km=15.0,
        quantity_quintals=50.0,
        offered_price_per_quintal=2450.0,
        gross_value=122500.0,
        transport_cost=3750.0,
        applicable_market_charges=3087.5,
        mandi_cess_apmc=1837.5,
        loading_unloading_cost=1250.0,
        storage_cost=0.0,
        estimated_loss_cost=500.0,
        total_deductions=7337.5,
        expected_net_return=115162.5,
        net_return_per_quintal=2303.25,
        price_provenance="data.gov.in (AGMARKNET)",
        is_price_simulated=False,
        meets_minimum_condition=True,
        recommendation_note="Indore Mandi offers the highest net return.",
    )

    opt2 = NetReturnBreakdown(
        scenario_id="opt_2",
        rank=2,
        channel_type="DIRECT_BUYER",
        destination_name="AgriProcure FPO",
        distance_km=25.0,
        quantity_quintals=50.0,
        offered_price_per_quintal=2380.0,
        gross_value=119000.0,
        transport_cost=4500.0,
        applicable_market_charges=0.0,
        storage_cost=0.0,
        estimated_loss_cost=500.0,
        total_deductions=5000.0,
        expected_net_return=114000.0,
        net_return_per_quintal=2280.0,
        price_provenance="krishibazaar_buyer_a",
        is_price_simulated=True,
        meets_minimum_condition=True,
        recommendation_note="AgriProcure FPO direct procurement.",
    )

    return OptimizationResult(
        crop="Wheat",
        quantity_quintals=50.0,
        minimum_net_return_inr=2200.0,
        all_options=[opt1, opt2],
        feasible_options=[opt1, opt2],
        recommended_option=opt1,
        summary_explanation="2 channels evaluated. Indore Mandi ranked #1.",
    )


@pytest.fixture
def sample_proposal(sample_optimization_result: OptimizationResult) -> BookingProposal:
    opt = sample_optimization_result.all_options[0]
    now = datetime.now(timezone.utc)
    return BookingProposal(
        proposal_id="PROP-20261021-TEST01",
        session_id="test_session_1",
        farmer_id="agent1_test_farmer",
        selected_option=opt,
        crop="Wheat",
        quantity_quintals=50.0,
        origin_location="Indore",
        destination_name=opt.destination_name,
        channel_type=opt.channel_type,
        transit_date="2026-10-25",
        estimated_gross_value=opt.gross_value,
        itemized_deductions=opt.total_deductions,
        estimated_net_payout=opt.expected_net_return,
        created_at_utc=now.isoformat(),
        expires_at_utc=now.isoformat(),
        is_consumed=False,
    )


@pytest.fixture
def sample_booking(sample_proposal: BookingProposal) -> BookingRecord:
    return BookingRecord(
        booking_id="KB-BOOK-20261021-TEST01",
        timestamp_utc="2026-10-21T00:05:00Z",
        status="CONFIRMED",
        farmer_id="agent1_test_farmer",
        offer_id="opt_1",
        channel_name=sample_proposal.destination_name,
        transporter_name="Simulated freight tariff",
        crop=sample_proposal.crop,
        quantity_quintals=sample_proposal.quantity_quintals,
        gross_revenue=sample_proposal.estimated_gross_value,
        transport_cost=3750.0,
        other_deductions=3587.5,
        net_payout=sample_proposal.estimated_net_payout,
        pickup_date=sample_proposal.transit_date,
        tracking_token="KB-TRK-TEST01",
        is_simulated_booking=True,
    )


def test_build_farmer_inquiry_card(sample_intent: FarmerIntent):
    card_meta = build_farmer_inquiry_card(sample_intent)
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, DetailCardPayload)
    assert "Wheat" in card.payload.title
    row_labels = [r.label for r in card.payload.summary_rows]
    assert "Crop" in row_labels
    assert "Produce Quantity" in row_labels
    assert "Origin Location" in row_labels
    assert "Target Net Return" in row_labels
    assert len(card.payload.ctas) == 1
    assert card.payload.ctas[0].selection == {"action": "view_channels"}


def test_build_channel_recommendations_card(sample_optimization_result: OptimizationResult):
    card_meta = build_channel_recommendations_card(sample_optimization_result)
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, CarouselCardPayload)
    assert len(card.payload.items) == 2

    # Check Rank 1
    item1 = card.payload.items[0]
    assert "Rank #1" in item1.title
    assert "Indore Mandi" in item1.title
    assert "MANDI" in item1.title
    assert item1.primary_cta.selection["rank"] == 1
    assert item1.primary_cta.primary is True
    badge_labels = [b.label for b in item1.badges]
    assert "Top Recommendation" in badge_labels
    assert "Mandi Auction Benchmark" in badge_labels

    # Check Rank 2
    item2 = card.payload.items[1]
    assert "Rank #2" in item2.title
    assert "AgriProcure FPO" in item2.title
    assert item2.primary_cta.selection["rank"] == 2
    assert item2.primary_cta.primary is False
    badge_labels2 = [b.label for b in item2.badges]
    assert "Simulated Buyer Offer" in badge_labels2


def test_build_proposal_review_card(sample_proposal: BookingProposal):
    card_meta = build_proposal_review_card(sample_proposal)
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, ReviewCardPayload)
    assert "Review Booking Proposal" in card.payload.title
    assert card.payload.approve_cta.selection["action"] == "approve_booking"
    assert card.payload.approve_cta.selection["proposal_id"] == sample_proposal.proposal_id
    assert card.payload.approve_cta.primary is True
    assert card.payload.reject_cta is not None
    assert card.payload.reject_cta.selection["action"] == "reject_booking"

    rows = {r.label: r.value for r in card.payload.summary_rows}
    assert rows["Proposal Reference"] == sample_proposal.proposal_id
    assert "50 Quintals Wheat" in rows["Produce & Quantity"]
    assert "AWAITING EXPLICIT FARMER APPROVAL" in rows["Safety Gate Status"]


def test_build_booking_confirmed_card(sample_booking: BookingRecord):
    card_meta = build_booking_confirmed_card(sample_booking)
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    assert card.is_terminal is True
    assert isinstance(card.payload, DetailCardPayload)
    assert sample_booking.booking_id in card.payload.title

    rows = {r.label: r.value for r in card.payload.summary_rows}
    assert rows["Booking Reference"] == sample_booking.booking_id
    assert rows["Tracking Number"] == sample_booking.tracking_token
    assert "SIMULATED" in rows["Audit Status"].upper()
    assert len(card.payload.ctas) == 2
    assert card.payload.ctas[0].selection["action"] == "status_lookup"
    assert card.payload.ctas[1].selection["action"] == "request_payment_review"


def test_build_incomplete_inquiry_form_card():
    partial_intent = FarmerIntent(
        crop="onions",
        origin_location=None,
        quantity_quintals=None,
        is_complete=False,
    )
    card_meta = build_incomplete_inquiry_form_card(partial_intent)
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, FormCardPayload)
    field_names = [f.name for f in card.payload.fields]
    assert "quantity_quintals" in field_names
    assert "origin_location" in field_names
    assert card.payload.submit_cta.selection["action"] == "submit_details"


def test_build_invalid_selection_card():
    card_meta = build_invalid_selection_card(
        "Option #99 does not exist", [1, 2]
    )
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, DetailCardPayload)
    assert "Invalid Channel Selection" in card.payload.title
    assert len(card.payload.ctas) == 2


def test_build_expired_and_duplicate_cards():
    exp_meta = build_expired_proposal_card("PROP-123", "2026-10-21T00:00:00Z")
    exp_card = extract_card(exp_meta)
    assert exp_card is not None
    assert "Expired" in exp_card.payload.title

    dup_meta = build_duplicate_approval_card("PROP-123")
    dup_card = extract_card(dup_meta)
    assert dup_card is not None
    assert "Already Processed" in dup_card.payload.title


def test_extract_interaction_action_card_response():
    # 1. Card selection response
    card_resp = create_card_response_content(
        selection={"action": "select_channel", "rank": 1, "option_id": "opt_mandi_1"}
    )
    msg = ChatMessage(content=[TextContent(text="I clicked option 1"), card_resp])
    act, param, sel = extract_interaction_action(msg, "I clicked option 1")
    assert act == BookingActionType.SELECT_OPTION
    assert param == "1"
    assert sel is not None
    assert sel["option_id"] == "opt_mandi_1"

    # 2. Card approval response
    appr_resp = create_card_response_content(
        selection={"action": "approve_booking", "proposal_id": "PROP-20261021-ABC"}
    )
    msg_appr = ChatMessage(content=[appr_resp])
    act, param, sel = extract_interaction_action(msg_appr, "")
    assert act == BookingActionType.APPROVE
    assert param == "PROP-20261021-ABC"

    # 3. Card cancelled
    cancel_resp = create_card_response_content(cancelled=True)
    msg_cancel = ChatMessage(content=[cancel_resp])
    act, param, _ = extract_interaction_action(msg_cancel, "")
    assert act == BookingActionType.CANCEL


def test_extract_interaction_action_json_text_and_fallback():
    # 1. JSON string text fallback
    json_text = '{"action": "select_channel", "rank": 2}'
    msg = ChatMessage(content=[TextContent(text=json_text)])
    act, param, sel = extract_interaction_action(msg, json_text)
    assert act == BookingActionType.SELECT_OPTION
    assert param == "2"

    # 2. Natural language fallback
    nl_text = "Select option 1"
    msg_nl = ChatMessage(content=[TextContent(text=nl_text)])
    act, param, _ = extract_interaction_action(msg_nl, nl_text)
    assert act == BookingActionType.SELECT_OPTION
    assert param == "1"

    # 3. Bare rank number fallback
    bare_digit = "2"
    msg_digit = ChatMessage(content=[TextContent(text=bare_digit)])
    act, param, _ = extract_interaction_action(msg_digit, bare_digit)
    assert act == BookingActionType.SELECT_OPTION
    assert param == "2"


def test_build_chat_response():
    card_meta = build_expired_proposal_card("PROP-001", "2026-10-21T00:00:00Z")
    resp_msg = build_chat_response("Proposal has expired.", card_meta)
    assert len(resp_msg.content) == 3
    assert isinstance(resp_msg.content[0], TextContent)
    assert resp_msg.content[0].text == "Proposal has expired."
    assert isinstance(resp_msg.content[1], MetadataContent)
    assert isinstance(resp_msg.content[2], EndSessionContent)
    assert resp_msg.text() == "Proposal has expired."


def test_build_payment_review_card():
    details = PaymentReviewDetails(
        reference="KB-PAY-TEST-99",
        purpose="Market Advisory Micro-Fee",
        amount_base_units=1000,
        amount_display="1000 atestfet",
        currency="atestfet",
        recipient_address="fetch1testrecipientaddr99",
        network="dorado-1",
        estimated_fee="~0.001 FET (standard testnet gas fee)",
        deadline_seconds=300,
        approval_command_template="AUTHORIZE PAYMENT REF:KB-PAY-TEST-99 AMOUNT:1000 CURRENCY:atestfet RECIPIENT:fetch1testrecipientaddr99",
    )
    card_meta = build_payment_review_card(details)
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    payload = card.payload
    assert isinstance(payload, ReviewCardPayload)
    assert "KB-PAY-TEST-99" in str(payload.summary_rows)
    assert "1000 atestfet" in str(payload.summary_rows)
    assert "fetch1testrecipientaddr99" in str(payload.summary_rows)
    assert "TESTNET DEMO MICRO-FEE ONLY" in str(payload.summary_rows)
    assert payload.approve_cta is not None
    assert payload.approve_cta.selection["action"] == "authorize_payment"
    assert payload.approve_cta.selection["reference"] == "KB-PAY-TEST-99"
    assert payload.approve_cta.selection["amount"] == 1000
    assert payload.approve_cta.selection["currency"] == "atestfet"
    assert payload.approve_cta.selection["recipient"] == "fetch1testrecipientaddr99"
    assert payload.reject_cta is not None
    assert payload.reject_cta.selection["action"] == "reject_payment"


def test_build_payment_confirmed_card():
    record = PaymentRecord(
        payment_reference="KB-PAY-SETTLED-01",
        purpose="Market Advisory Micro-Fee",
        payer_agent_address="agent1q_payer",
        payer_wallet_address="fetch1payer",
        payee_agent_address="agent1q_payee",
        payee_wallet_address="fetch1payee",
        amount_base_units=1000,
        amount_display="1000 atestfet",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.SETTLED,
        deadline_seconds=300,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        updated_at_utc=datetime.now(timezone.utc).isoformat(),
        tx_hash="0xABCDEF1234567890",
        tx_height=123456,
        verified_at_utc="2026-10-21T22:00:00Z",
    )
    card_meta = build_payment_confirmed_card(record)
    assert isinstance(card_meta, MetadataContent)
    card = extract_card(card_meta)
    assert card is not None
    assert isinstance(card.payload, DetailCardPayload)
    assert "SETTLED" in str(card.payload.summary_rows)
    assert "0xABCDEF1234567890" in str(card.payload.summary_rows)
    assert "123456" in str(card.payload.summary_rows)


def test_extract_interaction_action_payment_cta_and_text():
    # 1. Card CTA authorize_payment
    auth_resp = create_card_response_content(
        selection={
            "action": "authorize_payment",
            "reference": "KB-PAY-TEST-99",
            "amount": 1000,
            "currency": "atestfet",
            "recipient": "fetch1testrecipientaddr99",
        }
    )
    msg_auth = ChatMessage(content=[auth_resp])
    act, param, sel = extract_interaction_action(msg_auth, "")
    assert act == BookingActionType.AUTHORIZE_PAYMENT
    assert param == "KB-PAY-TEST-99"
    assert sel is not None
    assert sel["amount"] == 1000

    # 2. Card CTA reject_payment
    rej_resp = create_card_response_content(
        selection={
            "action": "reject_payment",
            "reference": "KB-PAY-TEST-99",
        }
    )
    msg_rej = ChatMessage(content=[rej_resp])
    act, param, _ = extract_interaction_action(msg_rej, "")
    assert act == BookingActionType.REJECT_PAYMENT
    assert param == "KB-PAY-TEST-99"

    # 3. Explicit text command
    text_cmd = "AUTHORIZE PAYMENT REF:KB-PAY-TEXT-01 AMOUNT:2000 CURRENCY:atestfet RECIPIENT:fetch1recipientaddr01"
    msg_text = ChatMessage(content=[TextContent(text=text_cmd)])
    act, param, sel = extract_interaction_action(msg_text, text_cmd)
    assert act == BookingActionType.AUTHORIZE_PAYMENT
    assert param == "KB-PAY-TEXT-01"
    assert sel is not None
    assert sel["amount_base_units"] == 2000
    assert sel["currency"] == "atestfet"
