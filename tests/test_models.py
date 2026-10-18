"""
Unit tests for KrishiBazaar domain models (tests/test_models.py).
"""

import pytest
from pydantic import ValidationError
from krishibazaar.models import (
    FarmerInquiry,
    MandiPriceRecord,
    WeatherRiskReport,
    BuyerOffer,
    TransportQuote,
    NetReturnBreakdown,
    BookingRequest,
    BookingRecord,
)


def test_farmer_inquiry_valid_and_normalized():
    inquiry = FarmerInquiry(
        farmer_id="farmer_101",
        crop="  onion  ",
        quantity_quintals=50.0,
        location_district="nashik",
        state="Maharashtra",
    )
    assert inquiry.crop == "Onion"
    assert inquiry.location_district == "Nashik"
    assert inquiry.quantity_quintals == 50.0


def test_farmer_inquiry_invalid_quantity():
    with pytest.raises(ValidationError):
        FarmerInquiry(
            farmer_id="farmer_101",
            crop="Wheat",
            quantity_quintals=-5.0,
            location_district="Nashik",
        )


def test_mandi_price_record_valid():
    record = MandiPriceRecord(
        mandi_name="Lasalgaon APMC",
        district="Nashik",
        state="Maharashtra",
        commodity="Onion",
        variety="Red Onion",
        arrival_date="2026-10-17",
        min_price=1800.0,
        max_price=2400.0,
        modal_price=2150.0,
        data_source="official_benchmark_cache",
    )
    assert record.modal_price == 2150.0
    assert record.data_source == "official_benchmark_cache"


def test_weather_risk_report():
    weather = WeatherRiskReport(
        location="Nashik",
        forecast_date="2026-10-18",
        temperature_max_c=31.5,
        temperature_min_c=19.2,
        precipitation_sum_mm=12.4,
        precipitation_probability_pct=65.0,
        wind_speed_max_kmh=18.0,
        spoilage_hazard_level="MEDIUM",
        transit_advice="Use waterproof tarpaulin cover to protect produce from rain.",
    )
    assert weather.spoilage_hazard_level == "MEDIUM"
    assert weather.precipitation_sum_mm == 12.4


def test_buyer_offer_simulation_disclaimer():
    offer = BuyerOffer(
        offer_id="BO-001",
        buyer_id="B-KISSAN-MART",
        buyer_name="Kissan Fresh Mart Hub",
        buyer_type="Retail Fresh Chain",
        crop="Tomato",
        quantity_quintals=30.0,
        offered_price_per_quintal=1950.0,
        farmgate_pickup=True,
    )
    assert offer.is_simulated is True
    assert "Simulated" in offer.disclaimer
    assert offer.farmgate_pickup is True


def test_transport_quote_calculation():
    quote = TransportQuote(
        quote_id="TQ-100",
        transporter_id="T-MAH-LOGISTICS",
        transporter_name="Jai Kisan Agro Logistics",
        vehicle_type="1-Tonne Pickup",
        capacity_quintals=25.0,
        route_origin="Niphad",
        route_destination="Lasalgaon APMC",
        distance_km=25.0,
        base_fare=500.0,
        rate_per_km=20.0,
        total_transport_cost=1000.0,
        estimated_transit_hours=1.5,
    )
    assert quote.total_transport_cost == 1000.0
    assert quote.distance_km == 25.0


def test_net_return_breakdown():
    breakdown = NetReturnBreakdown(
        scenario_id="SCENARIO-1",
        channel_type="MANDI",
        destination_name="Lasalgaon APMC",
        distance_km=30.0,
        gross_revenue=100000.0,
        offered_price_per_quintal=2000.0,
        transport_cost=2500.0,
        mandi_cess_apmc=1500.0,
        loading_unloading_cost=1000.0,
        spoilage_risk_penalty=0.0,
        total_deductions=5000.0,
        net_return=95000.0,
        net_return_per_quintal=1900.0,
        rank=1,
    )
    assert breakdown.net_return == 95000.0
    assert breakdown.net_return_per_quintal == 1900.0


def test_booking_models_and_approval():
    req = BookingRequest(
        farmer_id="farmer_42",
        offer_id="BO-001",
        crop="Onion",
        quantity_quintals=50.0,
        pickup_address="Village Pimpalgaon, Nashik",
        delivery_destination="Lasalgaon APMC",
        expected_net_payout=95000.0,
        agreed_by_farmer=True,
    )
    assert req.agreed_by_farmer is True

    record = BookingRecord(
        booking_id="KB-BOOK-20261018-001",
        timestamp_utc="2026-10-18T00:00:00Z",
        status="CONFIRMED",
        farmer_id="farmer_42",
        offer_id="BO-001",
        channel_name="Lasalgaon Mandi",
        transporter_name="Jai Kisan Transport",
        crop="Onion",
        quantity_quintals=50.0,
        gross_revenue=100000.0,
        transport_cost=2500.0,
        other_deductions=2500.0,
        net_payout=95000.0,
        pickup_date="2026-10-19",
        tracking_token="SECURE-TOKEN-XYZ",
        is_simulated_booking=True,
    )
    assert record.is_simulated_booking is True
    assert record.status == "CONFIRMED"


def test_farmer_intent_model_validation_and_followup():
    from krishibazaar.models import FarmerIntent

    # Complete intent
    intent = FarmerIntent(
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        minimum_net_return_inr=85000.0,
    ).evaluate_completeness()
    assert intent.crop == "onion"
    assert intent.quantity_quintals == 50.0
    assert intent.origin_location == "Nashik"
    assert intent.is_complete is True
    assert intent.follow_up_question is None

    # Incomplete intent: missing location and quantity
    partial = FarmerIntent(crop="Tomato").evaluate_completeness()
    assert partial.is_complete is False
    assert "quantity_quintals" in partial.missing_fields
    assert "origin_location" in partial.missing_fields
    assert partial.follow_up_question is not None
    assert "Tomato" in partial.follow_up_question

