"""
Unit tests for Mandi Price Service (tests/test_mandi_service.py).
Verifies:
- data.gov.in AGMARKNET API integration with browser headers
- Live records parsing and date normalization
- Transparent reporting of zero matching records (e.g. Nashik) without fake substitutions
- Suspicious price detection and anomaly flagging (e.g. INR 13 or INR 15 per quintal)
- Sample benchmark fallback provenance tagging (never labeled as live)
- Typed failures on HTTP errors and timeouts
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
import httpx
from krishibazaar.services.mandi_service import (
    get_mandi_prices,
    get_mandi_prices_async,
    validate_mandi_price,
    normalize_arrival_date,
    _get_benchmark_fallback,
    DATAGOV_MANDI_RESOURCE_URL,
    DATAGOV_REQUEST_HEADERS,
)


@pytest.mark.asyncio
async def test_mandi_service_live_api_mock():
    """Verify parsing, date normalization, and provenance when data.gov.in returns 200 OK with live records."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "records": [
            {
                "state": "Maharashtra",
                "district": "Ahilyanagar",
                "market": "APMC Newasa ",
                "commodity": "Onion",
                "variety": "Unhali",
                "grade": "Local",
                "arrival_date": "18/10/2026",
                "min_price": 1500,
                "max_price": 4300,
                "modal_price": 3500,
            }
        ]
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    result = await get_mandi_prices_async(
        commodity="Onion",
        district="Ahilyanagar",
        api_key="valid_test_key_123",
        client=mock_client,
    )

    assert result.success is True
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.mandi_name == "APMC Newasa"  # Trailing whitespace stripped
    assert rec.modal_price == 3500.0
    assert rec.arrival_date == "2026-10-18"  # Normalized to ISO YYYY-MM-DD
    assert rec.data_source == "data.gov.in_live"
    assert rec.is_suspicious is False
    assert result.provenance.is_live is True
    assert result.provenance.freshness_status == "LIVE"
    assert "data.gov.in" in result.provenance.source


@pytest.mark.asyncio
async def test_mandi_service_suspicious_price_flagging():
    """Verify that anomalous prices (e.g. INR 13 or INR 15/quintal from bundle entries) are flagged."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "records": [
            {
                "state": "Maharashtra",
                "district": "Pune",
                "market": "Pune(Moshi) ",
                "commodity": "Onion Green",
                "variety": "Other",
                "arrival_date": "18/10/2026",
                "min_price": 15,
                "max_price": 15,
                "modal_price": 15,  # Unit mismatch: INR 15 per bundle/kg, NOT quintal
            },
            {
                "state": "Maharashtra",
                "district": "Pune",
                "market": "APMC Pune ",
                "commodity": "Onion Green",
                "variety": "Other",
                "arrival_date": "18/10/2026",
                "min_price": 6,
                "max_price": 20,
                "modal_price": 13,  # Unit mismatch: INR 13 per bundle/kg
            },
            {
                "state": "Maharashtra",
                "district": "Ahilyanagar",
                "market": "APMC Rahata",
                "commodity": "Onion",
                "variety": "Unhali",
                "arrival_date": "18/10/2026",
                "min_price": 1800,
                "max_price": 3800,
                "modal_price": 3400,  # Authentic wholesale price
            },
        ]
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    result = await get_mandi_prices_async(
        commodity="Onion",
        state="Maharashtra",
        api_key="valid_key",
        client=mock_client,
    )

    assert result.success is True
    assert len(result.records) == 3

    # Check that INR 15 and INR 13 records are flagged as suspicious
    moshi_rec = next(r for r in result.records if "Moshi" in r.mandi_name)
    assert moshi_rec.is_suspicious is True
    assert moshi_rec.suspicion_reason is not None
    assert "low modal price" in moshi_rec.suspicion_reason.lower()

    pune_rec = next(r for r in result.records if "APMC Pune" in r.mandi_name)
    assert pune_rec.is_suspicious is True
    assert "low modal price" in pune_rec.suspicion_reason.lower()

    # Legitimate record must NOT be flagged
    rahata_rec = next(r for r in result.records if "Rahata" in r.mandi_name)
    assert rahata_rec.is_suspicious is False
    assert rahata_rec.suspicion_reason is None


@pytest.mark.asyncio
async def test_mandi_service_zero_matching_records_reports_no_live_records():
    """Verify that when live API returns 0 records (e.g. Nashik today), it reports 0 records and NEVER substitutes sample data."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "ok",
        "total": 0,
        "count": 0,
        "records": [],
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    result = await get_mandi_prices_async(
        commodity="Onion",
        district="Nashik",
        state="Maharashtra",
        api_key="valid_key",
        client=mock_client,
    )

    assert result.success is True
    assert len(result.records) == 0  # Does NOT substitute sample data!
    assert result.provenance.is_live is True
    assert result.provenance.freshness_status == "LIVE_NO_RECORDS"
    assert result.error_message is not None
    assert "Nashik" in result.error_message
    assert "No live mandi price records found" in result.error_message


@pytest.mark.asyncio
async def test_mandi_service_api_error_returns_typed_failure():
    """Verify that HTTP 500 or 401 returns typed failure without inventing records."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error in data.gov.in gateway"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    result = await get_mandi_prices_async(
        commodity="Wheat",
        district="Khanna",
        api_key="valid_key",
        client=mock_client,
    )

    assert result.success is False
    assert len(result.records) == 0
    assert result.provenance.is_live is False
    assert result.provenance.freshness_status == "STALE"
    assert result.error_message is not None
    assert "HTTP 500" in result.error_message


@pytest.mark.asyncio
async def test_mandi_service_timeout_returns_typed_failure():
    """Verify that network timeout returns typed failure when no sample fallback requested."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.TimeoutException("Connection timed out to data.gov.in")

    result = await get_mandi_prices_async(
        commodity="Soybean",
        district="Indore",
        api_key="valid_key",
        client=mock_client,
        allow_sample_fallback_on_error=False,
    )

    assert result.success is False
    assert len(result.records) == 0
    assert result.provenance.is_live is False
    assert result.error_message is not None
    assert "timed out" in result.error_message.lower()


@pytest.mark.asyncio
async def test_mandi_service_explicit_sample_fallback_provenance():
    """Verify that absent API key explicitly provides sample benchmark dataset clearly labeled."""
    result = await get_mandi_prices_async(
        commodity="Onion",
        district="Nashik",
        api_key="",  # Explicit empty key
    )

    assert result.success is True
    assert len(result.records) >= 1
    assert result.provenance.is_live is False
    assert result.provenance.freshness_status == "SIMULATED_SAMPLE"
    assert "benchmark" in result.provenance.source.lower()
    assert "Sample benchmark" in result.provenance.disclaimer
    assert result.provenance.observation_date == "2026-10-17"


def test_validate_mandi_price_function():
    """Unit test for validate_mandi_price rule matrix."""
    # Normal price
    susp, reason = validate_mandi_price("Onion", 2800, 2000, 3500)
    assert susp is False
    assert reason is None

    # Suspicious low price (< 100)
    susp, reason = validate_mandi_price("Onion", 13.0, 6.0, 20.0)
    assert susp is True
    assert "low modal price" in reason.lower()

    # Inverted min/max
    susp, reason = validate_mandi_price("Tomato", 1200, 1500, 1000)
    assert susp is True
    assert "inverted price range" in reason.lower()

    # Non-positive price
    susp, reason = validate_mandi_price("Potato", 0.0, 0.0, 500.0)
    assert susp is True
    assert "non-positive" in reason.lower()


def test_normalize_arrival_date():
    """Verify date conversion from Indian DD/MM/YYYY to standard YYYY-MM-DD."""
    assert normalize_arrival_date("18/10/2026") == "2026-10-18"
    assert normalize_arrival_date("01/05/2026") == "2026-05-01"
    assert normalize_arrival_date("2026-10-18") == "2026-10-18"


def test_mandi_service_unknown_commodity_returns_typed_failure():
    """Verify that querying an unknown commodity returns typed failure result."""
    result = _get_benchmark_fallback("dragonfruit", district="Nowhere")
    assert result.success is False
    assert len(result.records) == 0
    assert result.error_message is not None
    assert "No mandi price records found" in result.error_message


def test_mandi_service_sync_wrapper_offline():
    """Verify synchronous get_mandi_prices wrapper with empty key."""
    result = get_mandi_prices("Potato", district="Indore", api_key="")
    assert result.success is True
    assert len(result.records) >= 1
    assert result.records[0].commodity == "Potato"
    assert result.provenance.is_live is False
