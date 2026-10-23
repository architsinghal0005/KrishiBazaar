"""
Unit tests for KrishiBazaar Transport Specialist Service (tests/test_transport_service.py).
Verifies:
- Vehicle type selection matches harvest payload capacity
- Route distance calculation using known pairs and default fallback
- Weather hazard surcharge logic during rain
- All quotes are strictly labeled with is_simulated=True and disclaimers
- Negative/zero quantity input validation
"""

import pytest
from krishibazaar.models import WeatherRiskReport
from krishibazaar.services.transport_service import (
    estimate_route_distance_km,
    generate_simulated_transport_quotes,
    get_transport_quotes,
    get_transport_quotes_async,
    select_vehicle_for_quantity,
)


def test_select_vehicle_for_quantity():
    """Verify vehicle capacity matching for various harvest payloads."""
    # <= 10 qtl: Mini Pickup
    v_type, cap, base, rate = select_vehicle_for_quantity(8.0)
    assert "Tata Ace" in v_type
    assert cap >= 8.0

    # 10 - 35 qtl: Bolero / 3-Tonne
    v_type, cap, base, rate = select_vehicle_for_quantity(25.0)
    assert "Bolero" in v_type
    assert cap >= 25.0

    # 35 - 75 qtl: Eicher 6-Tonne
    v_type, cap, base, rate = select_vehicle_for_quantity(50.0)
    assert "Eicher" in v_type
    assert cap >= 50.0

    # > 75 qtl: Multi-Axle
    v_type, cap, base, rate = select_vehicle_for_quantity(100.0)
    assert "Multi-Axle" in v_type
    assert cap >= 100.0


def test_estimate_route_distance_km():
    """Verify distance lookup from known registry and default fallback."""
    # Known pairwise distance
    dist = estimate_route_distance_km("Nashik", "Pimpalgaon")
    assert dist == 32.0

    dist_rev = estimate_route_distance_km("Pimpalgaon", "Nashik")
    assert dist_rev == 32.0

    # Same town / local mandi
    dist_local = estimate_route_distance_km("Nashik APMC", "Nashik")
    assert dist_local == 18.0

    # Unknown route defaults to standard conservative distance
    dist_unknown = estimate_route_distance_km("Unknown Village", "Unknown Mandi")
    assert dist_unknown == 35.0


def test_generate_simulated_transport_quotes_with_weather_surcharge():
    """Verify freight quote generation and weather protection surcharge during rain hazard."""
    weather_hazard = WeatherRiskReport(
        location="Nashik",
        forecast_date="2026-10-18",
        temperature_max_c=29.0,
        temperature_min_c=19.0,
        precipitation_sum_mm=15.0,
        precipitation_probability_pct=85.0,
        wind_speed_max_kmh=18.0,
        spoilage_hazard_level="HIGH",
        transit_advice="Heavy rain hazard. Tarpaulin mandatory.",
        data_source="Open-Meteo",
    )

    destinations = ["Pimpalgaon", "Lasalgaon", "Pune"]
    result = generate_simulated_transport_quotes(
        origin="Nashik",
        destinations=destinations,
        quantity_quintals=50.0,
        weather_report=weather_hazard,
    )

    assert result.success is True
    assert len(result.quotes) == 3

    for dest, quote in result.quotes.items():
        assert quote.is_simulated is True
        assert "simulated" in quote.disclaimer.lower()
        assert quote.weather_surcharge == 250.0  # High weather hazard surcharge
        assert quote.tarp_cover_included is True
        assert quote.total_transport_cost > quote.base_fare


def test_transport_service_invalid_quantity():
    """Verify invalid quantity returns typed failure."""
    result = generate_simulated_transport_quotes(
        origin="Nashik",
        destinations=["Pune"],
        quantity_quintals=-5.0,
    )
    assert result.success is False
    assert "greater than zero" in result.error_message


@pytest.mark.asyncio
async def test_async_and_sync_transport_service_wrappers():
    """Verify async and sync wrappers return consistent results."""
    sync_res = get_transport_quotes(
        origin="Pune",
        destinations=["Mumbai"],
        quantity_quintals=30.0,
    )
    async_res = await get_transport_quotes_async(
        origin="Pune",
        destinations=["Mumbai"],
        quantity_quintals=30.0,
    )

    assert sync_res.success is True
    assert async_res.success is True
    assert "Mumbai" in sync_res.quotes
    assert "Mumbai" in async_res.quotes
