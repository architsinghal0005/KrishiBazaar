"""
Unit tests for KrishiBazaar Phase 6: Explicit Approval and Simulated Booking Workflow (tests/test_booking.py).

Verifies:
1. Option selection: Ranked option parsing and itemized proposal summary with cost deductions and provenance.
2. Explicit approval: Strictly unambiguous confirmation required; casual greetings or ambiguous words rejected.
3. Pending state: 15-minute expiry, proposal storage in SQLite, superseding prior proposals.
4. Simulated confirmation: Persistent simulated BookingRecord created in SQLite ledger with tracking token and is_simulated_booking=True.
5. Status verification: Querying simulated booking reference returns valid status.
6. Safety and reliability: Rejection of approval when no pending proposal exists, when expired, or when already consumed (idempotency).
7. Transparency: Mandatory simulation notices and advisory estimate warnings (never guaranteed returns).
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from pathlib import Path

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    TextContent,
    EndSessionContent,
)

from krishibazaar.agents.orchestrator import (
    handle_chat_message,
    format_mandi_market_report,
    format_weather_report,
)
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    DataProvenance,
    FarmerInquiry,
    MandiPriceRecord,
    MandiPriceResult,
    NetReturnBreakdown,
    WeatherResult,
    WeatherRiskReport,
)
from krishibazaar.services.booking_service import (
    BookingActionType,
    create_booking_proposal,
    execute_simulated_booking,
    format_booking_confirmation,
    format_booking_status,
    format_proposal_summary,
    parse_booking_action,
    is_casual_acknowledgement,
)
from krishibazaar.services.weather_service import resolve_planned_transit_date
from krishibazaar.storage import StorageEngine, storage

VALID_TRANSIT_DATE = (
    datetime.now(timezone(timedelta(hours=5, minutes=30))) + timedelta(days=2)
).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def isolate_test_db(tmp_path, monkeypatch):
    """Ensure isolated temporary database for test_booking without touching runtime db."""
    test_db = StorageEngine(db_path=tmp_path / "test_booking.db")
    monkeypatch.setattr("krishibazaar.storage.storage", test_db)
    monkeypatch.setattr("krishibazaar.agents.orchestrator.storage", test_db)
    monkeypatch.setattr("tests.test_booking.storage", test_db)
    yield test_db


@pytest.fixture
def mock_ctx():
    """Mock uAgents context with send and logger."""
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


@pytest.fixture
def sample_option():
    """Sample net return breakdown option for testing."""
    return NetReturnBreakdown(
        scenario_id="SCENARIO-APMC-01",
        channel_type="MANDI",
        destination_name="Lasalgaon APMC",
        distance_km=25.0,
        gross_value=160000.0,
        gross_revenue=160000.0,
        offered_price_per_quintal=3200.0,
        price_provenance="Official data.gov.in AGMARKNET modal price benchmark",
        transport_cost=3500.0,
        transport_provenance="Simulated 3-Tonne Mini-Truck quote",
        mandi_cess_apmc=2400.0,
        loading_unloading_cost=1250.0,
        storage_cost=0.0,
        estimated_loss_cost=800.0,
        spoilage_risk_penalty=800.0,
        total_deductions=7950.0,
        applicable_market_charges=3650.0,
        charges_provenance="APMC cess (1.5%) + Hamali (INR 25/qtl) + Weather risk",
        expected_net_return=152050.0,
        net_return=152050.0,
        net_return_per_quintal=3041.0,
        rank=1,
        meets_minimum_condition=True,
        recommendation_note="Highest net payout across local wholesale mandis",
        crop="Onion",
        quantity_quintals=50.0,
    )


# =====================================================================
# 1. Action Parser Unit Tests
# =====================================================================

def test_parse_booking_action_option_selection():
    """Verify various expressions of option selection are parsed accurately."""
    assert parse_booking_action("Select option 1") == (BookingActionType.SELECT_OPTION, "1")
    assert parse_booking_action("Choose rank 2") == (BookingActionType.SELECT_OPTION, "2")
    assert parse_booking_action("option 1") == (BookingActionType.SELECT_OPTION, "1")
    assert parse_booking_action("Rank 3") == (BookingActionType.SELECT_OPTION, "3")
    assert parse_booking_action("Book option 1") == (BookingActionType.SELECT_OPTION, "1")
    assert parse_booking_action("select top option") == (BookingActionType.SELECT_OPTION, "1")
    assert parse_booking_action("Select Lasalgaon APMC") == (BookingActionType.SELECT_OPTION, "lasalgaon apmc")


def test_parse_booking_action_explicit_approval():
    """Verify explicit affirmative approvals are detected."""
    assert parse_booking_action("Confirm booking") == (BookingActionType.APPROVE, None)
    assert parse_booking_action("Approve") == (BookingActionType.APPROVE, None)
    assert parse_booking_action("Approve booking") == (BookingActionType.APPROVE, None)
    assert parse_booking_action("yes confirm") == (BookingActionType.APPROVE, None)
    assert parse_booking_action("proceed with booking") == (BookingActionType.APPROVE, None)
    assert parse_booking_action("Approve proposal PROP-20261018-ABCD12") == (
        BookingActionType.APPROVE,
        "PROP-20261018-ABCD12",
    )
    assert parse_booking_action("Confirm proposal PROP-20261018-998877") == (
        BookingActionType.APPROVE,
        "PROP-20261018-998877",
    )


def test_parse_booking_action_cancellation():
    """Verify explicit cancellation commands are detected."""
    assert parse_booking_action("Cancel") == (BookingActionType.CANCEL, None)
    assert parse_booking_action("Cancel booking") == (BookingActionType.CANCEL, None)
    assert parse_booking_action("Reject") == (BookingActionType.CANCEL, None)
    assert parse_booking_action("Do not book") == (BookingActionType.CANCEL, None)
    assert parse_booking_action("No, cancel") == (BookingActionType.CANCEL, None)


def test_parse_booking_action_status_lookup():
    """Verify status query commands and booking IDs are parsed."""
    assert parse_booking_action("Status of KB-BOOK-20261018-ABC123") == (
        BookingActionType.STATUS_LOOKUP,
        "KB-BOOK-20261018-ABC123",
    )
    assert parse_booking_action("Track KB-BOOK-20261018-XYZ789") == (
        BookingActionType.STATUS_LOOKUP,
        "KB-BOOK-20261018-XYZ789",
    )
    assert parse_booking_action("KB-BOOK-20261018-112233") == (
        BookingActionType.STATUS_LOOKUP,
        "KB-BOOK-20261018-112233",
    )
    assert parse_booking_action("Booking status") == (BookingActionType.STATUS_LOOKUP, None)


def test_parse_booking_action_casual_or_unrelated_returns_none():
    """Verify greetings, casual words, or regular produce inquiries return NONE."""
    assert parse_booking_action("Hello KrishiBazaar")[0] == BookingActionType.NONE
    assert parse_booking_action("Hi")[0] == BookingActionType.NONE
    assert parse_booking_action("ok")[0] == BookingActionType.NONE
    assert parse_booking_action("thanks")[0] == BookingActionType.NONE
    assert parse_booking_action("I have 50 quintals of onions in Nashik")[0] == BookingActionType.NONE


# =====================================================================
# 2. Proposal & Booking Model Formatting Tests
# =====================================================================

def test_create_booking_proposal_and_expiry(sample_option):
    """Verify pending proposal creation includes 15-minute expiry and itemized values."""
    proposal = create_booking_proposal(
        session_id="sess_farmer_01",
        farmer_id="farmer_01",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
        validity_minutes=15,
    )
    assert proposal.proposal_id.startswith("PROP-")
    assert proposal.is_consumed is False
    assert proposal.destination_name == "Lasalgaon APMC"
    assert proposal.estimated_gross_value == 160000.0
    assert proposal.itemized_deductions == 7950.0
    assert proposal.estimated_net_payout == 152050.0

    created_dt = datetime.fromisoformat(proposal.created_at_utc)
    expires_dt = datetime.fromisoformat(proposal.expires_at_utc)
    diff_minutes = (expires_dt - created_dt).total_seconds() / 60
    assert 14.9 <= diff_minutes <= 15.1


def test_format_proposal_summary_itemization_and_provenance(sample_option):
    """Verify proposal summary presents itemized deductions and explicit approval instruction."""
    proposal = create_booking_proposal(
        session_id="sess_01",
        farmer_id="farmer_01",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    lines = format_proposal_summary(proposal)
    text = "\n".join(lines)

    assert "Booking Proposal & Confirmation Request" in text
    assert "Estimated Gross Value" in text
    assert "160,000.00" in text
    assert "Transport Freight: INR 3,500.00" in text
    assert "APMC Statutory Cess: INR 2,400.00" in text
    assert "Hamali/Labor: INR 1,250.00" in text
    assert "152,050.00" in text
    assert "Estimated Net In-Pocket Return" in text
    assert "NOT a guaranteed payout" in text
    assert "Explicit Approval Required" in text
    assert "Confirm booking" in text
    assert "Cancel" in text
    assert "simulated test booking" in text


def test_execute_simulated_booking_fields(sample_option):
    """Verify simulated booking record creates unique ID, tracking token, and simulation flag."""
    proposal = create_booking_proposal(
        session_id="sess_01",
        farmer_id="farmer_01",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    booking = execute_simulated_booking(proposal)

    assert booking.booking_id.startswith("KB-BOOK-")
    assert booking.tracking_token.startswith("KB-TRK-")
    assert booking.status == "CONFIRMED"
    assert booking.is_simulated_booking is True
    assert booking.crop == "Onion"
    assert booking.quantity_quintals == 50.0
    assert booking.net_payout == 152050.0
    assert "Simulated test booking" in booking.notes


def test_format_booking_confirmation_and_status(sample_option):
    """Verify confirmation and status formatting include simulation notice and reference lookup."""
    proposal = create_booking_proposal(
        session_id="sess_01",
        farmer_id="farmer_01",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    booking = execute_simulated_booking(proposal)

    conf_text = "\n".join(format_booking_confirmation(booking))
    assert "Simulated Booking Confirmed!" in conf_text
    assert booking.booking_id in conf_text
    assert booking.tracking_token in conf_text
    assert "Simulation Notice:" in conf_text
    assert "No real commercial buyer contract" in conf_text
    assert "Status of" in conf_text

    status_text = "\n".join(format_booking_status(booking))
    assert "Simulated Booking Status" in status_text
    assert booking.booking_id in status_text
    assert booking.tracking_token in status_text
    assert "CONFIRMED" in status_text
    assert "Audit Notice:" in status_text


# =====================================================================
# 3. Orchestrator End-to-End Chat Workflow Tests
# =====================================================================

@pytest.mark.asyncio
async def test_orchestrator_select_option_flow(mock_ctx, sample_option):
    """Verify farmer selecting an option receives a structured proposal summary and enters AWAITING_APPROVAL."""
    sender = "agent1q_farmer_select_opt"

    # Pre-seed session with active options
    inquiry = FarmerInquiry(
        farmer_id=sender,
        crop="Onion",
        quantity_quintals=50.0,
        location_district="Nashik",
        selling_deadline=VALID_TRANSIT_DATE,
    )
    storage.save_session(
        session_id=sender,
        farmer_id=sender,
        state="OFFERS_OPTIMIZED",
        inquiry=inquiry,
        active_options=[sample_option],
    )

    msg = ChatMessage(
        msg_id=uuid4(),
        content=[TextContent(text="Select option 1")],
    )

    await handle_chat_message(mock_ctx, sender, msg)

    # Inspect reply
    response_msg = mock_ctx.send.call_args_list[1][0][1]
    reply_text = next(c.text for c in response_msg.content if isinstance(c, TextContent))

    assert "Booking Proposal & Confirmation Request" in reply_text
    assert "Lasalgaon APMC" in reply_text
    assert "INR 152,050.00" in reply_text
    assert "Confirm booking" in reply_text

    # Session state must be AWAITING_APPROVAL
    sess = storage.get_session(sender)
    assert sess["state"] == "AWAITING_APPROVAL"

    # Pending proposal must exist in DB
    pending = storage.get_pending_proposal_for_session(sender)
    assert pending is not None
    assert pending.destination_name == "Lasalgaon APMC"


@pytest.mark.asyncio
async def test_orchestrator_explicit_approval_accepted_flow(mock_ctx, sample_option):
    """Verify explicit approval confirms booking, records persistent ledger entry, and returns tracking reference."""
    sender = "agent1q_farmer_approval_flow"

    # Create and save a pending proposal
    proposal = create_booking_proposal(
        session_id=sender,
        farmer_id=sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    storage.save_proposal(proposal)
    storage.save_session(
        session_id=sender,
        farmer_id=sender,
        state="AWAITING_APPROVAL",
    )

    msg = ChatMessage(
        msg_id=uuid4(),
        content=[TextContent(text="Confirm booking")],
    )

    await handle_chat_message(mock_ctx, sender, msg)

    response_msg = mock_ctx.send.call_args_list[1][0][1]
    reply_text = next(c.text for c in response_msg.content if isinstance(c, TextContent))

    assert "Simulated Booking Confirmed!" in reply_text
    assert "KB-BOOK-" in reply_text
    assert "KB-TRK-" in reply_text
    assert "Simulation Notice:" in reply_text
    assert "No real commercial buyer contract" in reply_text

    # Proposal must now be marked consumed in DB
    consumed_prop = storage.get_proposal(proposal.proposal_id)
    assert consumed_prop.is_consumed is True

    # No pending proposal remaining for session
    assert storage.get_pending_proposal_for_session(sender) is None

    # Persistent booking ledger entry exists in DB
    bookings = storage.list_bookings(farmer_id=sender)
    assert len(bookings) >= 1
    latest_booking = bookings[0]
    assert latest_booking.status == "CONFIRMED"
    assert latest_booking.is_simulated_booking is True
    assert latest_booking.net_payout == 152050.0


@pytest.mark.asyncio
async def test_orchestrator_duplicate_approval_rejected_idempotency(mock_ctx, sample_option):
    """Verify sending repeated approval messages rejects duplicate creation and preserves idempotency."""
    sender = "agent1q_farmer_duplicate_approval"

    proposal = create_booking_proposal(
        session_id=sender,
        farmer_id=sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    storage.save_proposal(proposal)
    storage.save_session(session_id=sender, farmer_id=sender, state="AWAITING_APPROVAL")

    msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Confirm booking")])

    # 1. First approval: succeeds
    await handle_chat_message(mock_ctx, sender, msg)
    assert "Simulated Booking Confirmed!" in mock_ctx.send.call_args_list[1][0][1].content[0].text

    initial_bookings_count = len(storage.list_bookings(farmer_id=sender))
    assert initial_bookings_count >= 1

    # Reset mock ctx for second message
    mock_ctx.send.reset_mock()

    # 2. Duplicate approval message: must be rejected with Duplicate Approval warning
    msg2 = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Confirm booking")])
    await handle_chat_message(mock_ctx, sender, msg2)

    dup_reply = mock_ctx.send.call_args_list[1][0][1].content[0].text
    assert "Duplicate Approval" in dup_reply or "No Pending Proposal" in dup_reply

    # Ledger count must NOT increase
    new_bookings_count = len(storage.list_bookings(farmer_id=sender))
    assert new_bookings_count == initial_bookings_count


@pytest.mark.asyncio
async def test_orchestrator_expired_proposal_rejected(mock_ctx, sample_option):
    """Verify proposal past its expiration time is rejected on approval attempt."""
    sender = "agent1q_farmer_expired_prop"

    # Create a proposal that expired 5 minutes ago
    past_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    past_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)

    expired_prop = BookingProposal(
        proposal_id="PROP-EXPIRED-999",
        session_id=sender,
        farmer_id=sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        destination_name="Lasalgaon APMC",
        channel_type="MANDI",
        transit_date=VALID_TRANSIT_DATE,
        estimated_gross_value=160000.0,
        itemized_deductions=7950.0,
        estimated_net_payout=152050.0,
        created_at_utc=past_time.isoformat(),
        expires_at_utc=past_expiry.isoformat(),
        is_consumed=False,
    )
    storage.save_proposal(expired_prop)
    storage.save_session(session_id=sender, farmer_id=sender, state="AWAITING_APPROVAL")

    msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Confirm booking")])
    await handle_chat_message(mock_ctx, sender, msg)

    reply_text = mock_ctx.send.call_args_list[1][0][1].content[0].text
    assert "Proposal Expired" in reply_text
    assert "15 minutes" in reply_text

    # No booking created
    assert len(storage.list_bookings(farmer_id=sender)) == 0


@pytest.mark.asyncio
async def test_orchestrator_approval_rejected_when_no_pending_proposal(mock_ctx):
    """Verify approving without an active proposal returns rejection notice."""
    sender = "agent1q_farmer_no_proposal"
    storage.clear_session(sender)

    msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Confirm booking")])
    await handle_chat_message(mock_ctx, sender, msg)

    reply_text = mock_ctx.send.call_args_list[1][0][1].content[0].text
    assert "Approval Rejected: No Pending Proposal" in reply_text
    assert "select a sales channel" in reply_text


@pytest.mark.asyncio
async def test_orchestrator_ambiguous_or_casual_message_rejected_in_awaiting_approval(
    mock_ctx, sample_option
):
    """Verify casual greetings or ambiguous messages do not count as approval in AWAITING_APPROVAL state."""
    sender = "agent1q_farmer_casual_rejection"

    proposal = create_booking_proposal(
        session_id=sender,
        farmer_id=sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    storage.save_proposal(proposal)
    storage.save_session(session_id=sender, farmer_id=sender, state="AWAITING_APPROVAL")

    casual_phrases = [
        "hello", "hi", "ok", "okay", "cool", "looks good", "thanks", "ok cool",
        "sounds good", "all looks good", "sounds good to me", "looks fine",
    ]
    for casual_word in casual_phrases:
        mock_ctx.send.reset_mock()
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=casual_word)])
        await handle_chat_message(mock_ctx, sender, msg)

        reply_text = mock_ctx.send.call_args_list[1][0][1].content[0].text
        assert "Pending Proposal Awaiting Explicit Confirmation" in reply_text
        assert "Casual messages or ambiguous acknowledgements do NOT confirm bookings" in reply_text
        assert proposal.proposal_id in reply_text

        # No booking created!
        assert len(storage.list_bookings(farmer_id=sender)) == 0


@pytest.mark.asyncio
async def test_orchestrator_cancel_pending_proposal(mock_ctx, sample_option):
    """Verify farmer can explicitly cancel a pending proposal without creating a booking."""
    sender = "agent1q_farmer_cancel_flow"

    proposal = create_booking_proposal(
        session_id=sender,
        farmer_id=sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    storage.save_proposal(proposal)
    storage.save_session(session_id=sender, farmer_id=sender, state="AWAITING_APPROVAL")

    msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Cancel booking")])
    await handle_chat_message(mock_ctx, sender, msg)

    reply_text = mock_ctx.send.call_args_list[1][0][1].content[0].text
    assert "Booking Cancelled" in reply_text
    assert proposal.proposal_id in reply_text

    # Proposal must be consumed / cleared
    assert storage.get_pending_proposal_for_session(sender) is None
    assert len(storage.list_bookings(farmer_id=sender)) == 0


@pytest.mark.asyncio
async def test_orchestrator_status_lookup_flow(mock_ctx, sample_option):
    """Verify status inquiry returns formatted status for existing booking, or not found notice."""
    sender = "agent1q_farmer_status_flow"

    # Create a confirmed booking in storage
    proposal = create_booking_proposal(
        session_id=sender,
        farmer_id=sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    booking = execute_simulated_booking(proposal)
    storage.create_booking(booking)

    # 1. Successful status lookup by booking reference
    msg = ChatMessage(
        msg_id=uuid4(),
        content=[TextContent(text=f"Status of {booking.booking_id}")],
    )
    await handle_chat_message(mock_ctx, sender, msg)

    reply_text = mock_ctx.send.call_args_list[1][0][1].content[0].text
    assert "Simulated Booking Status" in reply_text
    assert booking.booking_id in reply_text
    assert booking.tracking_token in reply_text
    assert "CONFIRMED" in reply_text
    assert "Audit Notice:" in reply_text

    # 2. Status lookup for unknown reference
    mock_ctx.send.reset_mock()
    msg_unknown = ChatMessage(
        msg_id=uuid4(),
        content=[TextContent(text="Status of KB-BOOK-99999999-FAKE00")],
    )
    await handle_chat_message(mock_ctx, sender, msg_unknown)

    reply_unknown = mock_ctx.send.call_args_list[1][0][1].content[0].text
    assert "Booking Not Found" in reply_unknown
    assert "KB-BOOK-99999999-FAKE00" in reply_unknown


# =====================================================================
# 4. Hackathon Audit Regression Tests
# =====================================================================

def test_reject_past_pickup_date(sample_option):
    """Verify create_booking_proposal strictly rejects pickup dates in the past."""
    past_date = "2026-10-10"
    with pytest.raises(ValueError, match="Cannot generate booking proposal with pickup date in the past"):
        create_booking_proposal(
            session_id="sess_past",
            farmer_id="farmer_past",
            selected_option=sample_option,
            crop="Onion",
            quantity_quintals=50.0,
            origin_location="Nashik",
            transit_date=past_date,
        )

    # When allow_past_date is True (for testing historical scenarios), it succeeds
    prop = create_booking_proposal(
        session_id="sess_past",
        farmer_id="farmer_past",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=past_date,
        allow_past_date=True,
    )
    assert prop.transit_date == past_date


def test_resolve_planned_transit_date():
    """Verify resolve_planned_transit_date validates and advances past dates."""
    base = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    # Future date: preserved
    date_val, was_adj, orig = resolve_planned_transit_date("2026-10-25", base_date=base)
    assert date_val == "2026-10-25"
    assert was_adj is False
    assert orig is None

    # Past date: clamped to tomorrow with adjustment flag
    date_val, was_adj, orig = resolve_planned_transit_date("2026-10-18", base_date=base)
    assert date_val == "2026-10-23"
    assert was_adj is True
    assert orig == "2026-10-18"

    # Today: advanced to tomorrow for dispatch
    date_val, was_adj, orig = resolve_planned_transit_date("2026-10-22", base_date=base)
    assert date_val == "2026-10-23"
    assert was_adj is True


def test_mandi_market_report_retrieval_timestamp_and_staleness_warning():
    """Verify format_mandi_market_report includes retrieval timestamp and staleness warnings."""
    # Stale result
    stale_prov = DataProvenance(
        source="data.gov.in (AGMARKNET)",
        retrieval_timestamp_utc="2026-10-22T02:00:00Z",
        observation_date="2026-10-17",
        freshness_status="STALE",
        is_live=False,
    )
    rec = MandiPriceRecord(
        state="Maharashtra",
        district="Nashik",
        mandi_name="Lasalgaon",
        commodity="Onion",
        modal_price=3200.0,
        min_price=2800.0,
        max_price=3500.0,
        arrival_date="2026-10-17",
        data_source="data.gov.in_live",
        is_suspicious=False,
    )
    mandi_res = MandiPriceResult(
        success=True,
        commodity="Onion",
        records=[rec],
        provenance=stale_prov,
    )

    report_lines = format_mandi_market_report("Onion", "Nashik", "Maharashtra", mandi_res)
    text = "\n".join(report_lines)

    assert "Retrieved (UTC): 2026-10-22T02:00:00Z" in text
    assert "Mandi Data Freshness Warning" in text
    assert "Lasalgaon" in text


def test_weather_report_date_alignment_and_past_date_adjustment():
    """Verify format_weather_report states forecast date and notes past date adjustment."""
    prov = DataProvenance(
        source="Open-Meteo API",
        retrieval_timestamp_utc="2026-10-22T02:00:00Z",
        observation_date="2026-10-23",
        freshness_status="LIVE",
        is_live=True,
    )
    rep = WeatherRiskReport(
        location="Nashik",
        forecast_date="2026-10-23",
        target_date_requested="2026-10-23",
        is_target_date_matched=True,
        temperature_min_c=20.0,
        temperature_max_c=31.0,
        precipitation_sum_mm=0.0,
        precipitation_probability_pct=10.0,
        wind_speed_max_kmh=12.0,
        spoilage_hazard_level="LOW",
        transit_advice="Safe dry conditions.",
        risk_score=0.1,
    )
    weather_res = WeatherResult(
        success=True,
        location="Nashik",
        report=rep,
        provenance=prov,
    )

    # 1. Normal future forecast
    normal_lines = format_weather_report(weather_res, target_date="2026-10-23")
    normal_text = "\n".join(normal_lines)
    assert "Forecast aligned with requested selling/transit date: 2026-10-23" in normal_text

    # 2. Adjusted from past date
    adj_lines = format_weather_report(
        weather_res,
        target_date="2026-10-23",
        date_was_past=True,
        original_past_date="2026-10-18",
    )
    adj_text = "\n".join(adj_lines)
    assert "Requested pickup date '2026-10-18' was in the past" in adj_text
    assert "Weather forecast corresponds to the planned future transit date: 2026-10-23" in adj_text


def test_financial_reconciliation_total_deductions_includes_transport(sample_option):
    """Verify Gross - Total Deductions = Net Return, and transport is never omitted from total deductions."""
    proposal = create_booking_proposal(
        session_id="sess_recon",
        farmer_id="farmer_recon",
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )

    # Check proposal math
    assert proposal.estimated_gross_value == 160000.0
    assert proposal.itemized_deductions == 7950.0
    assert proposal.estimated_net_payout == 152050.0
    assert round(proposal.estimated_gross_value - proposal.itemized_deductions, 2) == proposal.estimated_net_payout

    # Execute booking
    booking = execute_simulated_booking(proposal)
    assert booking.gross_revenue == 160000.0
    assert booking.transport_cost == 3500.0
    assert booking.other_deductions == 4450.0  # 7950 - 3500
    total_deductions = round(booking.transport_cost + booking.other_deductions, 2)
    assert total_deductions == 7950.0
    assert round(booking.gross_revenue - total_deductions, 2) == booking.net_payout

    # Confirmation text includes reconciliation
    conf_text = "\n".join(format_booking_confirmation(booking))
    assert "Total Deductions: INR 7,950.00" in conf_text
    assert "Freight Transport: INR 3,500.00" in conf_text
    assert "Statutory/Labor/Risk: INR 4,450.00" in conf_text
    assert "Net Return (Gross - Total Deductions): INR 152,050.00" in conf_text


@pytest.mark.asyncio
async def test_orchestrator_approval_rejected_on_proposal_id_mismatch(mock_ctx, sample_option):
    """Verify approving with an incorrect proposal ID is rejected and does not confirm."""
    sender = "agent1q_farmer_mismatch_test"

    proposal = create_booking_proposal(
        session_id=sender,
        farmer_id=sender,
        selected_option=sample_option,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date=VALID_TRANSIT_DATE,
    )
    storage.save_proposal(proposal)
    storage.save_session(session_id=sender, farmer_id=sender, state="AWAITING_APPROVAL")

    msg = ChatMessage(
        msg_id=uuid4(),
        content=[TextContent(text="Approve proposal PROP-WRONG-999999")],
    )
    await handle_chat_message(mock_ctx, sender, msg)

    reply_text = mock_ctx.send.call_args_list[1][0][1].content[0].text
    assert "Approval Rejected: Proposal ID Mismatch" in reply_text
    assert "PROP-WRONG-999999" in reply_text
    assert proposal.proposal_id in reply_text

    # No booking created!
    assert len(storage.list_bookings(farmer_id=sender)) == 0
