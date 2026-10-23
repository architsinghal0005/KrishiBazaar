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


def test_provenance_and_assumption_labels_in_optimization():
    """Verify input provenance, assumption vs verified charges, and advisory net return language."""
    mandi_records = [
        MandiPriceRecord(
            mandi_name="Lasalgaon APMC",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            arrival_date="2026-10-18",
            min_price=2500.0,
            max_price=3500.0,
            modal_price=3200.0,
            data_source="data.gov.in_live",
            is_suspicious=False,
        )
    ]
    buyer_offers = [
        BuyerOffer(
            offer_id="BUY-01",
            buyer_id="B-AGRO-01",
            buyer_name="AgroProcess Direct",
            buyer_type="PROCESSOR",
            crop="Onion",
            offered_price_per_quintal=3000.0,
            quantity_quintals=50.0,
            is_simulated=True,
            farmgate_pickup=True,
            payment_terms="Immediate",
        )
    ]
    transport_quotes = {
        "Lasalgaon APMC": TransportQuote(
            quote_id="TR-01",
            transporter_id="T-01",
            transporter_name="MahaKisan Logistics (Simulated)",
            vehicle_type="Eicher 14ft",
            capacity_quintals=60.0,
            route_origin="Nashik",
            route_destination="Lasalgaon APMC",
            distance_km=45.0,
            base_fare=1000.0,
            rate_per_km=25.0,
            total_transport_cost=3500.0,
            estimated_transit_hours=2.0,
            is_simulated=True,
        )
    }

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=mandi_records,
        buyer_offers=buyer_offers,
        transport_quotes=transport_quotes,
        minimum_net_return_inr=80000.0,
    )

    top = result.recommended_option
    assert top is not None
    assert top.destination_name == "Lasalgaon APMC"
    assert top.channel_type == "MANDI"

    # 1. Price provenance
    assert "Official APMC Mandi Modal Price" in top.price_provenance
    assert top.is_price_simulated is False

    # 2. Transport provenance
    assert "Simulated freight quote" in top.transport_provenance

    # 3. Cost deductions breakdown distinguishes assumptions
    assert "Statutory APMC market cess" in top.charges_provenance
    assert "hamali labor" in top.charges_provenance
    assert "assumption" in top.charges_provenance

    # 4. Net return terminology: must strictly be advisory estimated net return, not guaranteed
    assert "not a guaranteed payout" in top.recommendation_note.lower()
    assert "not a guaranteed payout" in result.summary_explanation.lower()
    assert "estimated net return" in top.recommendation_note.lower()

    # 5. Must not imply an actual buyer offer exists when option is based on mandi benchmark
    assert "buyer purchase offer" in top.recommendation_note.lower()


def test_direct_buyer_exempt_trade_disclaimer_and_exact_math():
    """Verify direct-buyer exempt trade reports INR 0 cess/labor with explicit unverified assumption disclaimer and exact math."""
    buyer_offers = [
        BuyerOffer(
            offer_id="BUY-EXEMPT-01",
            buyer_id="B-01",
            buyer_name="FarmGate Direct Buyer (Exempt)",
            buyer_type="PROCESSOR",
            crop="Onion",
            offered_price_per_quintal=3000.0,
            quantity_quintals=50.0,
            is_simulated=True,
            farmgate_pickup=True,
            is_apmc_exempt=True,
        )
    ]

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=[],
        buyer_offers=buyer_offers,
        transport_quotes={},
        minimum_net_return_inr=100000.0,
    )

    assert len(result.all_options) == 1
    top = result.recommended_option
    assert top is not None
    assert top.channel_type == "DIRECT_BUYER"
    assert top.is_apmc_exempt is True

    # 1. Zero charges for APMC cess and labor
    assert top.mandi_cess_apmc == 0.0
    assert top.loading_unloading_cost == 0.0
    assert top.applicable_market_charges == 0.0

    # 2. Explicit unverified assumption disclaimer (not legally verified)
    assert "unverified assumption" in top.charges_provenance.lower()
    assert "not legally verified" in top.charges_provenance.lower()
    assert "outside market yard" in top.charges_provenance.lower()

    # 3. Exact arithmetic: Gross - Deductions == Net
    assert top.gross_value == 150000.0  # 50 qtl * 3000
    assert top.total_deductions == 0.0   # farmgate pickup + 0 storage + 0 loss + 0 cess/labor
    assert top.expected_net_return == 150000.0
    assert round(top.gross_value - top.total_deductions, 2) == top.expected_net_return

    # 4. Displayed per-quintal amount
    expected_per_qtl = round(top.expected_net_return / top.quantity_quintals, 2)
    assert top.net_return_per_quintal == expected_per_qtl
    assert top.net_return_per_quintal == 3000.0

    # 5. Summary explanation resolves contradiction: displays INR 0.00 with explicit assumption
    summary = result.summary_explanation
    assert "Statutory APMC Market Cess: INR 0.00" in summary
    assert "Assumed direct-buyer trade exemption outside APMC yard" in summary
    assert "unverified assumption, not legally verified" in summary
    assert "Mandi Hamali/Labor: INR 0.00" in summary


def test_direct_buyer_non_exempt_trade_deductions_and_exact_math():
    """Verify direct-buyer non-exempt trade consistently deduces 1.5% APMC cess and INR 25/qtl labor with exact math."""
    buyer_offers = [
        BuyerOffer(
            offer_id="BUY-NONEXEMPT-01",
            buyer_id="B-02",
            buyer_name="Regulated Yard Buyer (Non-Exempt)",
            buyer_type="TRADER",
            crop="Onion",
            offered_price_per_quintal=3200.0,
            quantity_quintals=50.0,
            is_simulated=True,
            farmgate_pickup=False,
            is_apmc_exempt=False,
        )
    ]
    transport_quotes = {
        "Regulated Yard Buyer (Non-Exempt)": TransportQuote(
            quote_id="TQ-NE-01",
            transporter_id="T-NE",
            transporter_name="Simulated Carrier",
            vehicle_type="Truck",
            capacity_quintals=60.0,
            route_origin="Nashik",
            route_destination="Regulated Yard Buyer (Non-Exempt)",
            distance_km=40.0,
            base_fare=1000.0,
            rate_per_km=25.0,
            total_transport_cost=2000.0,
            estimated_transit_hours=1.5,
            is_simulated=True,
        )
    }

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=[],
        buyer_offers=buyer_offers,
        transport_quotes=transport_quotes,
        minimum_net_return_inr=100000.0,
    )

    top = result.recommended_option
    assert top is not None
    assert top.channel_type == "DIRECT_BUYER"
    assert top.is_apmc_exempt is False

    # 1. Deductions consistently calculated: Gross = 50 * 3200 = 160,000
    expected_gross = 160000.0
    expected_cess = round(expected_gross * 0.015, 2)  # 2400.0
    expected_hamali = round(50.0 * 25.0, 2)          # 1250.0
    expected_market = expected_cess + expected_hamali # 3650.0
    expected_transport = 2000.0
    expected_deductions = expected_transport + expected_market  # 5650.0
    expected_net = round(expected_gross - expected_deductions, 2)  # 154350.0

    assert top.gross_value == expected_gross
    assert top.mandi_cess_apmc == expected_cess
    assert top.loading_unloading_cost == expected_hamali
    assert top.applicable_market_charges == expected_market
    assert top.transport_cost == expected_transport
    assert top.total_deductions == expected_deductions
    assert top.expected_net_return == expected_net

    # 2. Exact arithmetic: Gross - Deductions == Net
    assert round(top.gross_value - top.total_deductions, 2) == top.expected_net_return

    # 3. Displayed per-quintal amount
    expected_per_qtl = round(top.expected_net_return / top.quantity_quintals, 2)
    assert top.net_return_per_quintal == expected_per_qtl
    assert top.net_return_per_quintal == 3087.0

    # 4. Summary explanation displays statutory deductions
    summary = result.summary_explanation
    assert f"Statutory APMC Market Cess: INR {expected_cess:,.2f}" in summary
    assert "1.5% statutory tariff" in summary
    assert f"Mandi Hamali/Labor: INR {expected_hamali:,.2f}" in summary
    assert "INR 25/qtl standard labor charge" in summary


def test_mandi_channel_non_exempt_statutory_deductions_and_exact_math():
    """Verify Mandi channels always apply 1.5% APMC cess and INR 25/qtl labor with exact math."""
    mandi_records = [
        MandiPriceRecord(
            mandi_name="Pune APMC",
            district="Pune",
            state="Maharashtra",
            commodity="Onion",
            arrival_date="2026-10-18",
            min_price=2000.0,
            max_price=3000.0,
            modal_price=2800.0,
            data_source="data.gov.in_live",
            is_suspicious=False,
        )
    ]
    transport_quotes = {
        "Pune APMC": TransportQuote(
            quote_id="TQ-PUNE-01",
            transporter_id="T-PUNE",
            transporter_name="Simulated Carrier",
            vehicle_type="Truck",
            capacity_quintals=60.0,
            route_origin="Nashik",
            route_destination="Pune APMC",
            distance_km=100.0,
            base_fare=1000.0,
            rate_per_km=25.0,
            total_transport_cost=3500.0,
            estimated_transit_hours=4.0,
            is_simulated=True,
        )
    }

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=40.0,
        mandi_records=mandi_records,
        buyer_offers=[],
        transport_quotes=transport_quotes,
    )

    top = result.recommended_option
    assert top is not None
    assert top.channel_type == "MANDI"
    assert top.is_apmc_exempt is False

    # Gross: 40 * 2800 = 112,000
    expected_gross = 112000.0
    expected_cess = round(expected_gross * 0.015, 2)  # 1680.0
    expected_hamali = round(40.0 * 25.0, 2)          # 1000.0
    expected_market = expected_cess + expected_hamali # 2680.0
    expected_deductions = 3500.0 + expected_market    # 6180.0
    expected_net = round(expected_gross - expected_deductions, 2)  # 105820.0

    assert top.gross_value == expected_gross
    assert top.mandi_cess_apmc == expected_cess
    assert top.loading_unloading_cost == expected_hamali
    assert top.applicable_market_charges == expected_market
    assert top.total_deductions == expected_deductions
    assert top.expected_net_return == expected_net

    # Exact arithmetic
    assert round(top.gross_value - top.total_deductions, 2) == top.expected_net_return
    assert top.net_return_per_quintal == round(expected_net / 40.0, 2)


def test_all_channels_exact_arithmetic_consistency_across_mixed_options():
    """Verify that every single option satisfies gross_value - total_deductions == expected_net_return exactly."""
    mandi_records = [
        MandiPriceRecord(
            mandi_name="Lasalgaon APMC",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            arrival_date="2026-10-18",
            min_price=2200.0,
            max_price=3400.0,
            modal_price=3100.0,
            data_source="data.gov.in_live",
            is_suspicious=False,
        ),
        MandiPriceRecord(
            mandi_name="Pimpalgaon APMC",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            arrival_date="2026-10-18",
            min_price=2100.0,
            max_price=3300.0,
            modal_price=2950.0,
            data_source="data.gov.in_live",
            is_suspicious=False,
        ),
    ]
    buyer_offers = [
        BuyerOffer(
            offer_id="BO-MIX-01",
            buyer_id="B-MIX-1",
            buyer_name="Agro Direct (Exempt)",
            buyer_type="PROCESSOR",
            crop="Onion",
            offered_price_per_quintal=3050.0,
            quantity_quintals=50.0,
            farmgate_pickup=True,
            is_apmc_exempt=True,
            is_simulated=True,
        ),
        BuyerOffer(
            offer_id="BO-MIX-02",
            buyer_id="B-MIX-2",
            buyer_name="Wholesale Yard (Non-Exempt)",
            buyer_type="TRADER",
            crop="Onion",
            offered_price_per_quintal=3300.0,
            quantity_quintals=50.0,
            farmgate_pickup=False,
            is_apmc_exempt=False,
            is_simulated=True,
        ),
    ]
    transport_quotes = {
        "Lasalgaon APMC": TransportQuote(
            quote_id="TQ-L-01",
            transporter_id="T-1",
            transporter_name="Simulated Carrier 1",
            vehicle_type="Truck",
            capacity_quintals=60.0,
            route_origin="Nashik",
            route_destination="Lasalgaon APMC",
            distance_km=45.0,
            base_fare=1000.0,
            rate_per_km=25.0,
            total_transport_cost=2125.0,
            estimated_transit_hours=2.0,
            is_simulated=True,
        ),
        "Pimpalgaon APMC": TransportQuote(
            quote_id="TQ-P-01",
            transporter_id="T-2",
            transporter_name="Simulated Carrier 2",
            vehicle_type="Truck",
            capacity_quintals=60.0,
            route_origin="Nashik",
            route_destination="Pimpalgaon APMC",
            distance_km=30.0,
            base_fare=1000.0,
            rate_per_km=25.0,
            total_transport_cost=1750.0,
            estimated_transit_hours=1.0,
            is_simulated=True,
        ),
        "Wholesale Yard (Non-Exempt)": TransportQuote(
            quote_id="TQ-W-01",
            transporter_id="T-3",
            transporter_name="Simulated Carrier 3",
            vehicle_type="Truck",
            capacity_quintals=60.0,
            route_origin="Nashik",
            route_destination="Wholesale Yard (Non-Exempt)",
            distance_km=50.0,
            base_fare=1000.0,
            rate_per_km=25.0,
            total_transport_cost=2250.0,
            estimated_transit_hours=2.0,
            is_simulated=True,
        ),
    }

    result = optimize_sales_channels(
        crop="Onion",
        quantity_quintals=50.0,
        mandi_records=mandi_records,
        buyer_offers=buyer_offers,
        transport_quotes=transport_quotes,
        minimum_net_return_inr=120000.0,
    )

    assert len(result.all_options) == 4
    for opt in result.all_options:
        # Check market charges composition
        assert opt.applicable_market_charges == round(opt.mandi_cess_apmc + opt.loading_unloading_cost, 2)
        # Check total deductions composition
        assert opt.total_deductions == round(
            opt.transport_cost + opt.applicable_market_charges + opt.storage_cost + opt.estimated_loss_cost, 2
        )
        # Check exact net return arithmetic
        assert round(opt.gross_value - opt.total_deductions, 2) == opt.expected_net_return
        # Check displayed per-quintal amount
        assert opt.net_return_per_quintal == round(opt.expected_net_return / opt.quantity_quintals, 2)

        # Clearly labelled simulated bids & quotes
        if opt.channel_type == "DIRECT_BUYER":
            assert opt.is_price_simulated is True
            assert "simulated" in opt.price_provenance.lower()
        if opt.transport_cost > 0:
            assert "simulated" in opt.transport_provenance.lower()
