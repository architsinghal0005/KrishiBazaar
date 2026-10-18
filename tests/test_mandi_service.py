"""
Unit tests for Mandi Price Service (tests/test_mandi_service.py).
Verifies data.gov.in AGMARKNET API integration, provenance tracking, benchmark fallbacks, and error handling.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
import httpx
from krishibazaar.services.mandi_service import (
    get_mandi_prices,
    get_mandi_prices_async,
    _get_benchmark_fallback,
    DATAGOV_MANDI_RESOURCE_URL,
)


@pytest.mark.asyncio
async def test_mandi_service_live_api_mock():
    """Verify parsing and provenance when data.gov.in returns 200 OK with live records."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "records": [
            {
                "state": "Maharashtra",
                "district": "Nashik",
                "market": "Lasalgaon",
                "commodity": "Onion",
                "variety": "Red",
                "arrival_date": "2026-10-18",
                "min_price": "1900",
                "max_price": "2400",
                "modal_price": "2200",
            }
        ]
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    result = await get_mandi_prices_async(
        commodity="Onion",
        district="Nashik",
        api_key="valid_test_key_123",
        client=mock_client,
    )

    assert result.success is True
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.mandi_name == "Lasalgaon"
    assert rec.modal_price == 2200.0
    assert rec.data_source == "data.gov.in_live"
    assert result.provenance.is_live is True
    assert result.provenance.freshness_status == "LIVE"
    assert "data.gov.in" in result.provenance.source


@pytest.mark.asyncio
async def test_mandi_service_missing_api_key_serves_sample_benchmark():
    """Verify that absent API key serves transparent benchmark records without crashing."""
    result = await get_mandi_prices_async(commodity="Onion", district="Nashik", api_key=None)

    assert result.success is True
    assert len(result.records) >= 1
    assert result.provenance.is_live is False
    assert result.provenance.freshness_status == "SIMULATED_SAMPLE"
    assert "benchmark" in result.provenance.source.lower()
    assert "Sample benchmark" in result.provenance.disclaimer


@pytest.mark.asyncio
async def test_mandi_service_api_zero_records_falls_back():
    """Verify that when live API returns 0 records, benchmark records are supplied."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"records": []}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    result = await get_mandi_prices_async(
        commodity="Tomato",
        district="Pune",
        api_key="valid_key",
        client=mock_client,
    )

    assert result.success is True
    assert len(result.records) >= 1
    assert result.provenance.is_live is False
    assert result.provenance.freshness_status == "SIMULATED_SAMPLE"


@pytest.mark.asyncio
async def test_mandi_service_api_error_and_timeout_fallback():
    """Verify that HTTP 500 or timeout triggers graceful fallback to benchmark records."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.TimeoutException("Mandi API connection timed out")

    result = await get_mandi_prices_async(
        commodity="Wheat",
        district="Khanna",
        api_key="valid_key",
        client=mock_client,
    )

    assert result.success is True
    assert len(result.records) >= 1
    assert result.records[0].commodity == "Wheat"
    assert result.provenance.freshness_status == "SIMULATED_SAMPLE"


def test_mandi_service_unknown_commodity_returns_typed_failure():
    """Verify that querying an unknown commodity returns typed failure result."""
    result = _get_benchmark_fallback("dragonfruit", district="Nowhere")
    assert result.success is False
    assert len(result.records) == 0
    assert result.error_message is not None
    assert "No mandi price records found" in result.error_message


def test_mandi_service_sync_wrapper():
    """Verify synchronous get_mandi_prices wrapper."""
    result = get_mandi_prices("Potato", district="Indore")
    assert result.success is True
    assert len(result.records) >= 1
    assert result.records[0].commodity == "Potato"
