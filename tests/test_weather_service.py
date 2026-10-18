"""
Unit tests for Open-Meteo Weather Service (tests/test_weather_service.py).
Verifies API integration, coordinate validation, missing field handling, timeouts, hazard scoring, and fallbacks.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
import httpx
from krishibazaar.services.weather_service import (
    get_transit_weather,
    get_transit_weather_async,
    resolve_coordinates,
    _compute_transit_hazard,
    _get_weather_fallback,
)


@pytest.mark.asyncio
async def test_weather_service_live_api_mock():
    """Verify Open-Meteo response parsing, hazard evaluation, and provenance."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "daily": {
            "time": ["2026-10-18", "2026-10-19"],
            "temperature_2m_max": [31.5, 32.0],
            "temperature_2m_min": [19.0, 19.5],
            "precipitation_sum": [15.2, 0.0],  # 15.2 mm rain -> HIGH hazard
            "precipitation_probability_max": [80.0, 10.0],
            "windspeed_10m_max": [22.0, 14.0],
        }
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    result = await get_transit_weather_async(
        location="Nashik",
        client=mock_client,
    )

    assert result.success is True
    assert result.report is not None
    assert result.report.temperature_max_c == 31.5
    assert result.report.precipitation_sum_mm == 15.2
    assert result.report.spoilage_hazard_level == "HIGH"
    assert "tarpaulin" in result.report.transit_advice.lower()
    assert result.provenance.is_live is True
    assert result.provenance.freshness_status == "LIVE"


@pytest.mark.asyncio
async def test_weather_service_invalid_coordinates():
    """Verify coordinate boundary validation strictly rejects out-of-range inputs."""
    result = await get_transit_weather_async(
        location="InvalidLocation",
        latitude=150.0,  # Invalid latitude (>90)
        longitude=200.0,  # Invalid longitude (>180)
    )

    assert result.success is False
    assert result.error_message is not None
    assert "Invalid coordinates" in result.error_message


@pytest.mark.asyncio
async def test_weather_service_missing_fields_fallback():
    """Verify that Open-Meteo response missing daily fields triggers clean fallback."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"daily": {}}  # Empty daily block

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    result = await get_transit_weather_async("Pune", client=mock_client)

    assert result.success is True
    assert result.report is not None
    assert result.provenance.freshness_status == "SIMULATED_SAMPLE"


@pytest.mark.asyncio
async def test_weather_service_timeout_fallback():
    """Verify that network timeout produces fallback without unhandled exception."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.TimeoutException("Open-Meteo timed out")

    result = await get_transit_weather_async("Indore", client=mock_client)

    assert result.success is True
    assert result.report is not None
    assert result.report.location == "Indore"


def test_resolve_coordinates():
    """Verify known Indian district coordinate lookup."""
    nashik = resolve_coordinates("Nashik")
    assert nashik == (19.9975, 73.7898)

    pune = resolve_coordinates("Pune Mandi")
    assert pune == (18.5204, 73.8567)

    custom = resolve_coordinates("Custom", latitude=25.0, longitude=80.0)
    assert custom == (25.0, 80.0)


def test_compute_transit_hazard_ratings():
    """Verify hazard score thresholds."""
    # Heavy rain: HIGH
    hazard_high, _ = _compute_transit_hazard(precip_mm=12.0, precip_prob_pct=85.0, temp_max_c=30.0)
    assert hazard_high == "HIGH"

    # Moderate rain: MEDIUM
    hazard_med, _ = _compute_transit_hazard(precip_mm=3.5, precip_prob_pct=45.0, temp_max_c=30.0)
    assert hazard_med == "MEDIUM"

    # Extreme heat: MEDIUM
    hazard_heat, _ = _compute_transit_hazard(precip_mm=0.0, precip_prob_pct=10.0, temp_max_c=42.0)
    assert hazard_heat == "MEDIUM"

    # Dry mild weather: LOW
    hazard_low, _ = _compute_transit_hazard(precip_mm=0.0, precip_prob_pct=10.0, temp_max_c=30.0)
    assert hazard_low == "LOW"


def test_sync_weather_wrapper():
    """Verify synchronous get_transit_weather wrapper."""
    result = get_transit_weather("Nashik")
    assert result.success is True
    assert result.report is not None
