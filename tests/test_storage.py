"""
Unit tests for KrishiBazaar SQLite storage engine (tests/test_storage.py).
"""

import pytest
from pathlib import Path
from krishibazaar.storage import StorageEngine
from krishibazaar.models import (
    FarmerInquiry,
    NetReturnBreakdown,
    BookingRecord,
)


@pytest.fixture
def temp_storage(tmp_path: Path):
    db_file = tmp_path / "test_krishibazaar.db"
    return StorageEngine(db_path=db_file)


def test_session_save_and_retrieve(temp_storage: StorageEngine):
    session_id = "test_session_1"
    farmer_id = "farmer_123"

    inquiry = FarmerInquiry(
        farmer_id=farmer_id,
        crop="Onion",
        quantity_quintals=40.0,
        location_district="Nashik",
    )

    options = [
        NetReturnBreakdown(
            scenario_id="SC-1",
            channel_type="MANDI",
            destination_name="Lasalgaon Mandi",
            distance_km=25.0,
            gross_revenue=80000.0,
            offered_price_per_quintal=2000.0,
            transport_cost=2000.0,
            mandi_cess_apmc=1200.0,
            loading_unloading_cost=800.0,
            spoilage_risk_penalty=0.0,
            total_deductions=4000.0,
            net_return=76000.0,
            net_return_per_quintal=1900.0,
            rank=1,
            recommendation_note="Best local option",
        )
    ]

    temp_storage.save_session(
        session_id=session_id,
        farmer_id=farmer_id,
        state="COMPARISON_READY",
        inquiry=inquiry,
        active_options=options,
    )

    retrieved = temp_storage.get_session(session_id)
    assert retrieved is not None
    assert retrieved["session_id"] == session_id
    assert retrieved["farmer_id"] == farmer_id
    assert retrieved["state"] == "COMPARISON_READY"
    assert retrieved["inquiry"].crop == "Onion"
    assert retrieved["inquiry"].quantity_quintals == 40.0
    assert len(retrieved["active_options"]) == 1
    assert retrieved["active_options"][0].net_return == 76000.0


def test_session_update_and_clear(temp_storage: StorageEngine):
    session_id = "test_session_2"
    farmer_id = "farmer_456"

    temp_storage.save_session(
        session_id=session_id,
        farmer_id=farmer_id,
        state="AWAITING_INPUT",
    )
    s1 = temp_storage.get_session(session_id)
    assert s1["state"] == "AWAITING_INPUT"

    # Update state
    temp_storage.save_session(
        session_id=session_id,
        farmer_id=farmer_id,
        state="PROCESSING",
    )
    s2 = temp_storage.get_session(session_id)
    assert s2["state"] == "PROCESSING"

    # Clear
    temp_storage.clear_session(session_id)
    assert temp_storage.get_session(session_id) is None


def test_booking_create_and_retrieve(temp_storage: StorageEngine):
    booking = BookingRecord(
        booking_id="KB-TEST-001",
        timestamp_utc="2026-10-18T10:00:00Z",
        status="CONFIRMED",
        farmer_id="farmer_999",
        offer_id="OFFER-99",
        quote_id="QUOTE-99",
        channel_name="Agro Hub Nashik",
        transporter_name="Jai Kisan Fleet",
        crop="Tomato",
        quantity_quintals=25.0,
        gross_revenue=50000.0,
        transport_cost=1500.0,
        other_deductions=500.0,
        net_payout=48000.0,
        pickup_date="2026-10-19",
        tracking_token="TOKEN-SECURE-999",
        is_simulated_booking=True,
        notes="Pickup at farmgate morning 8 AM",
    )

    success = temp_storage.create_booking(booking)
    assert success is True

    record = temp_storage.get_booking("KB-TEST-001")
    assert record is not None
    assert record.booking_id == "KB-TEST-001"
    assert record.crop == "Tomato"
    assert record.net_payout == 48000.0
    assert record.is_simulated_booking is True

    # List bookings
    all_bookings = temp_storage.list_bookings()
    assert len(all_bookings) == 1
    assert all_bookings[0].booking_id == "KB-TEST-001"

    # Filtered
    farmer_bookings = temp_storage.list_bookings(farmer_id="farmer_999")
    assert len(farmer_bookings) == 1

    unknown_bookings = temp_storage.list_bookings(farmer_id="other_farmer")
    assert len(unknown_bookings) == 0


def test_proposal_lifecycle_and_atomic_consumption(temp_storage: StorageEngine):
    """Test saving, retrieving, superseding, and atomic consumption of proposals."""
    from datetime import datetime, timezone, timedelta
    from krishibazaar.models import BookingProposal, NetReturnBreakdown

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=15)

    option = NetReturnBreakdown(
        scenario_id="SC-1",
        channel_type="MANDI",
        destination_name="Lasalgaon Mandi",
        distance_km=25.0,
        gross_revenue=80000.0,
        offered_price_per_quintal=2000.0,
        transport_cost=2000.0,
        mandi_cess_apmc=1200.0,
        loading_unloading_cost=800.0,
        spoilage_risk_penalty=0.0,
        total_deductions=4000.0,
        net_return=76000.0,
        net_return_per_quintal=1900.0,
        rank=1,
    )

    prop1 = BookingProposal(
        proposal_id="PROP-20261018-TEST01",
        session_id="session_prop_1",
        farmer_id="farmer_prop_1",
        selected_option=option,
        crop="Onion",
        quantity_quintals=40.0,
        origin_location="Nashik",
        destination_name="Lasalgaon Mandi",
        channel_type="MANDI",
        transit_date="2026-10-19",
        estimated_gross_value=80000.0,
        itemized_deductions=4000.0,
        estimated_net_payout=76000.0,
        created_at_utc=now.isoformat(),
        expires_at_utc=expires.isoformat(),
        is_consumed=False,
    )

    # 1. Save and retrieve
    temp_storage.save_proposal(prop1)
    retrieved = temp_storage.get_proposal("PROP-20261018-TEST01")
    assert retrieved is not None
    assert retrieved.proposal_id == "PROP-20261018-TEST01"
    assert retrieved.estimated_net_payout == 76000.0
    assert retrieved.is_consumed is False

    # Pending for session
    pending = temp_storage.get_pending_proposal_for_session("session_prop_1")
    assert pending is not None
    assert pending.proposal_id == "PROP-20261018-TEST01"

    # 2. Superseding: Saving a new proposal marks prior unconsumed proposal consumed
    prop2 = BookingProposal(
        proposal_id="PROP-20261018-TEST02",
        session_id="session_prop_1",
        farmer_id="farmer_prop_1",
        selected_option=option,
        crop="Onion",
        quantity_quintals=40.0,
        origin_location="Nashik",
        destination_name="Lasalgaon Mandi",
        channel_type="MANDI",
        transit_date="2026-10-19",
        estimated_gross_value=80000.0,
        itemized_deductions=4000.0,
        estimated_net_payout=76000.0,
        created_at_utc=now.isoformat(),
        expires_at_utc=expires.isoformat(),
        is_consumed=False,
    )
    temp_storage.save_proposal(prop2)

    # Prior proposal is now consumed/superseded
    old_p = temp_storage.get_proposal("PROP-20261018-TEST01")
    assert old_p.is_consumed is True

    # Active pending proposal is now prop2
    active_p = temp_storage.get_pending_proposal_for_session("session_prop_1")
    assert active_p is not None
    assert active_p.proposal_id == "PROP-20261018-TEST02"

    # 3. Atomic consumption (Idempotency test)
    first_consume = temp_storage.consume_proposal("PROP-20261018-TEST02")
    assert first_consume is True

    # Second consume must return False (already consumed)
    second_consume = temp_storage.consume_proposal("PROP-20261018-TEST02")
    assert second_consume is False

    # Non-existent proposal consumption returns False
    fake_consume = temp_storage.consume_proposal("NON_EXISTENT")
    assert fake_consume is False

    # Now no pending proposal for session
    assert temp_storage.get_pending_proposal_for_session("session_prop_1") is None
