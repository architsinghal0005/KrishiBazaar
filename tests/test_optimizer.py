"""
Unit tests for deterministic Net-Return Optimizer (tests/test_optimizer.py).
Verifies exact financial calculations, edge cases, minimum conditions, rankings, and explanations.
"""

import pytest
from krishibazaar.models import BuyerOffer, MandiPriceRecord, TransportQuote, WeatherRiskReport
from krishibazaar.services.optimizer import (
    calculate_channel_net_return,
    compute_weather_loss_risk,
    compute_storage_cost,
    optimize_sales_channels,
)


def test_calculate_channel_net_return_exact_math():
    """Verify exact formula: net = gross - (transport + market + storage + loss)."""
    # 50 quintals @ INR 2,000/qtl
    # Gross: 100,000
    # Transport: 2,500
    # Market charges: 1,500 (cess) + 1,250 (hamali) = 2,750
    # Storage: 1,000
    # Loss: 500
    # Total deductions: 6,750
    # Expected Net: 93,250
    # Net/qtl: 1,865.0
    res = calculate_channel_net_return(
        scenario_id="TEST-01",
        channel_type="MANDI",
        destination_name="Lasalgaon APMC",
        quantity_quintals=50.0,
        price_per_quintal=2000.0,
        distance_km=30.0,
        transport_cost=2500.0,
        applicable_market_charges=2750.0,
        storage_cost=1000.0,
        estimated_loss_cost=500.0,
        minimum_net_return_inr=90000.0,
        mandi_cess_apmc=1500.0,
        loading_unloading_cost=1250.0,
    )

    assert res.gross_value == 100000.0
    assert res.total_deductions == 6750.0
    assert res.expected_net_return == 93250.0
    assert res.net_return_per_quintal == 1865.0
    assert res.meets_minimum_condition is True
    assert res.is_feasible is True
    assert res.storage_cost == 1000.0
    assert res.estimated_loss_cost == 500.0


def test_optimizer_invalid_inputs_raise_errors():
    """Verify optimizer strictly rejects zero or negative quantities and prices."""
    with pytest.raises(ValueError, match="Quantity must be strictly positive"):
        calculate_channel_net_return(
            scenario_id="ERR-1",
            channel_type="MANDI",
            destination_name="Any Mandi",
            quantity_quintals=0.0,
            price_per_quintal=2000.0,
        )

    with pytest.raises(ValueError, match="Quantity must be strictly positive"):
        calculate_channel_net_return(
            scenario_id="ERR-2",
            channel_type="MANDI",
            destination_name="Any Mandi",
            quantity_quintals=-10.0,
            price_per_quintal=2000.0,
        )

    with pytest.raises(ValueError, match="Price per quintal must be strictly positive"):
        calculate_channel_net_return(
            scenario_id="ERR-3",
            channel_type="MANDI",
            destination_name="Any Mandi",
            quantity_quintals=50.0,
            price_per_quintal=-500.0,
        )

    with pytest.raises(ValueError, match="Cost deductions cannot be negative"):
        calculate_channel_net_return(
            scenario_id="ERR-4",
            channel_type="MANDI",
            destination_name="Any Mandi",
            quantity_quintals=50.0,
            price_per_quintal=2000.0,
            transport_cost=-100.0,
        )


def test_enforce_minimum_net_return_condition():
    """Verify that options failing the farmer's minimum threshold are flagged as not feasible."""
    # Gross: 80,000, Deductions: 5,000 -> Net: 75,000. Farmer wants at least 85,000.
    res = calculate_channel_net_return(
        scenario_id="FAIL-MIN",
        channel_type="MANDI",
        destination_name="Pune APMC",
        quantity_quintals=40.0,
        price_per_quintal=2000.0,
        transport_cost=3000.0,
        applicable_market_charges=2000.0,
        minimum_net_return_inr=85000.0,
    )

    assert res.expected_net_return == 75000.0
    assert res.meets_minimum_condition is False
    assert res.is_feasible is False


def test_compute_weather_loss_risk():
    """Verify weather loss calculations and tarp exemption."""
    weather_high = WeatherRiskReport(
        location="Nashik",
        forecast_date="2026-10-18",
        temperature_max_c=28.0,
        temperature_min_c=20.0,
        precipitation_sum_mm=18.0,
        precipitation_probability_pct=85.0,
        wind_speed_max_kmh=25.0,
        spoilage_hazard_level="HIGH",
        transit_advice="Severe rain alert.",
    )

    # If tarp cover included -> 0 loss
    assert compute_weather_loss_risk(100000.0, weather_high, tarp_cover_included=True) == 0.0

    # If uncovered in HIGH rain -> 3.5% loss penalty
    assert compute_weather_loss_risk(100000.0, weather_high, tarp_cover_included=False) == 3500.0

    # If no weather report -> 0 loss
    assert compute_weather_loss_risk(100000.0, None, tarp_cover_included=False) == 0.0


def test_compute_storage_cost():
    """Verify optional storage costs."""
    assert compute_storage_cost(50.0, None) == 0.0
    assert compute_storage_cost(50.0, "cold storage") == 2500.0  # 50 qtl * 50 INR
    assert compute_storage_cost(50.0, "warehouse") == 1000.0     # 50 qtl * 20 INR


def test_optimize_sales_channels_ranking_and_explanation():
    """Verify multi-option ranking: Direct Buyer vs Mandi."""
    mandi_records = [
        MandiPriceRecord(
            mandi_name="Lasalgaon APMC",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-17",
            min_price=1800.0,
            max_price=2400.0,
            modal_price=2200.0,
            data_source="official_benchmark_cache",
        ),
        MandiPriceRecord(
            mandi_name="Pimpalgaon APMC",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-17",
            min_price=1700.0,
            max_price=2200.0,
            modal_price=2000.0,
            data_source="official_benchmark_cache",
        ),
    ]

    buyer_offers = [
        BuyerOffer(
            offer_id="BO-FARM-PICKUP",
            buyer_id="B-KISSAN-MART",
            buyer_name="Kissan Agro Aggregator",
            buyer_type="FPO Aggregator",
            crop="onion",
            quantity_quintals=50.0,
            offered_price_per_quintal=2150.0,
            farmgate_pickup=True,  # 0 transport cost!
        )
    ]

    quotes = {
        "Lasalgaon APMC": TransportQuote(
            quote_id="TQ-1",
            transporter_id="T1",
            transporter_name="Logistics",
            vehicle_type="Mini Truck",
            capacity_quintals=60.0,
            route_origin="Nashik",
            route_destination="Lasalgaon APMC",
            distance_km=30.0,
            base_fare=500.0,
            rate_per_km=25.0,
            total_transport_cost=1250.0,
            estimated_transit_hours=1.5,
            tarp_cover_included=True,
        )
    }

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=mandi_records,
        buyer_offers=buyer_offers,
        transport_quotes=quotes,
        minimum_net_return_inr=85000.0,
    )

    assert len(result.all_options) == 3
    assert len(result.feasible_options) >= 1
    assert result.recommended_option is not None

    # Top option should be Rank 1
    top = result.recommended_option
    assert top.rank == 1
    assert top.is_feasible is True
    assert top.expected_net_return >= 85000.0
    assert "Rank 1" in result.summary_explanation


def test_optimize_sales_channels_shortfall_when_no_feasible_options():
    """Verify behavior when no options meet farmer's high threshold."""
    mandi_records = [
        MandiPriceRecord(
            mandi_name="Lasalgaon APMC",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            arrival_date="2026-10-17",
            min_price=1500.0,
            max_price=1800.0,
            modal_price=1600.0,
            data_source="official_benchmark_cache",
        )
    ]

    # 50 quintals @ 1600 = 80,000 gross. Farmer expects 95,000 net.
    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=mandi_records,
        minimum_net_return_inr=95000.0,
    )

    assert len(result.feasible_options) == 0
    assert result.recommended_option is not None
    assert result.recommended_option.meets_minimum_condition is False
    assert "None of the" in result.summary_explanation
    assert "shortfall" in result.summary_explanation


def test_optimize_sales_channels_excludes_suspicious_mandi_prices():
    """Verify that anomalous / unit-mismatched prices (e.g. INR 13 or INR 15) are excluded from recommendation."""
    mandi_records = [
        MandiPriceRecord(
            mandi_name="Pune(Moshi)",
            district="Pune",
            state="Maharashtra",
            commodity="Onion Green",
            arrival_date="2026-10-18",
            min_price=15.0,
            max_price=15.0,
            modal_price=15.0,  # Suspicious unit error: INR 15 per bundle
            data_source="data.gov.in_live",
            is_suspicious=True,
            suspicion_reason="Suspiciously low modal price (INR 15.00/quintal). Likely recorded per kg or bundle.",
        ),
        MandiPriceRecord(
            mandi_name="APMC Newasa",
            district="Ahilyanagar",
            state="Maharashtra",
            commodity="Onion",
            arrival_date="2026-10-18",
            min_price=1500.0,
            max_price=4300.0,
            modal_price=3500.0,  # Valid authentic price
            data_source="data.gov.in_live",
            is_suspicious=False,
        ),
    ]

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=mandi_records,
        minimum_net_return_inr=80000.0,
    )

    # The suspicious record at INR 15 must be excluded
    mandi_dest_names = [opt.destination_name for opt in result.all_options]
    assert "Pune(Moshi)" not in mandi_dest_names
    assert "APMC Newasa" in mandi_dest_names
    assert result.recommended_option.destination_name == "APMC Newasa"

