"""
Unit tests for ASI:One structured intent extraction (tests/test_extraction.py).
Mocks HTTP API calls to verify model parsing, error handling, rate limits, timeouts, and fallbacks.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx
from krishibazaar.services.extraction import (
    extract_farmer_intent,
    extract_farmer_intent_async,
    _extract_via_rules,
    _sanitize_and_parse_json,
)
from krishibazaar.models import FarmerIntent


@pytest.mark.asyncio
async def test_extract_intent_asi1_success_mock():
    """Verify successful extraction of all fields when ASI:One returns 200 OK."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"crop": "onion", "quantity_quintals": 50.0, "origin_location": "Nashik", '
                        '"selling_deadline": "tomorrow", "minimum_net_return_inr": 85000.0, '
                        '"storage_preference": null}'
                    )
                }
            }
        ]
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    text = "I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."
    intent = await extract_farmer_intent_async(text, api_key="test_api_key_123", client=mock_client)

    assert intent.crop == "onion"
    assert intent.quantity_quintals == 50.0
    assert intent.origin_location == "Nashik"
    assert intent.selling_deadline == "tomorrow"
    assert intent.minimum_net_return_inr == 85000.0
    assert intent.is_complete is True
    assert intent.follow_up_question is None
    assert intent.extraction_source == "asi1"


@pytest.mark.asyncio
async def test_extract_intent_incomplete_asks_followup():
    """Verify that missing required fields trigger is_complete=False and a targeted follow-up question."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"crop": "wheat", "quantity_quintals": null, "origin_location": null, '
                        '"selling_deadline": null, "minimum_net_return_inr": null, "storage_preference": null}'
                    )
                }
            }
        ]
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    text = "I want to sell wheat"
    intent = await extract_farmer_intent_async(text, api_key="test_api_key_123", client=mock_client)

    assert intent.crop == "wheat"
    assert intent.quantity_quintals is None
    assert intent.origin_location is None
    assert intent.is_complete is False
    assert "quantity_quintals" in intent.missing_fields
    assert "origin_location" in intent.missing_fields
    assert intent.follow_up_question is not None
    assert "Wheat" in intent.follow_up_question


@pytest.mark.asyncio
async def test_extract_intent_markdown_fenced_json():
    """Verify that markdown code-fenced responses are safely sanitized and parsed."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "```json\n{\"crop\": \"tomato\", \"quantity_quintals\": 35.0, \"origin_location\": \"Pune\"}\n```"
                }
            }
        ]
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    intent = await extract_farmer_intent_async("35 quintals tomato in Pune", api_key="dummy_key", client=mock_client)

    assert intent.crop == "tomato"
    assert intent.quantity_quintals == 35.0
    assert intent.origin_location == "Pune"
    assert intent.is_complete is True


@pytest.mark.asyncio
async def test_extract_intent_missing_credentials_fallback():
    """Verify that missing API key triggers the deterministic rule-based fallback without errors."""
    with patch("krishibazaar.config.settings.asi_one_api_key", None):
        text = "I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."
        intent = await extract_farmer_intent_async(text, api_key=None)

        assert intent.crop == "onion"
        assert intent.quantity_quintals == 50.0
        assert intent.origin_location == "Nashik"
        assert intent.minimum_net_return_inr == 85000.0
        assert intent.selling_deadline == "tomorrow"
        assert intent.extraction_source == "rule_based_fallback"
        assert intent.is_complete is True


@pytest.mark.asyncio
async def test_extract_intent_api_error_401():
    """Verify that 401 Unauthorized returns rule-based fallback gracefully."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    text = "50 quintals of onions in Nashik"
    intent = await extract_farmer_intent_async(text, api_key="bad_key", client=mock_client)

    assert intent.crop == "onion"
    assert intent.quantity_quintals == 50.0
    assert intent.origin_location == "Nashik"
    assert intent.extraction_source == "rule_based_fallback"


@pytest.mark.asyncio
async def test_extract_intent_api_rate_limit_429():
    """Verify that 429 Rate Limit returns rule-based fallback gracefully."""
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Rate limit exceeded"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_response

    text = "40 quintals of tomatoes in Pune"
    intent = await extract_farmer_intent_async(text, api_key="test_key", client=mock_client)

    assert intent.crop == "tomato"
    assert intent.quantity_quintals == 40.0
    assert intent.origin_location == "Pune"
    assert intent.extraction_source == "rule_based_fallback"


@pytest.mark.asyncio
async def test_extract_intent_timeout():
    """Verify that request timeouts are caught and fallback safely executes."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = httpx.TimeoutException("Connection timed out")

    text = "10 tons of potatoes in Indore"
    intent = await extract_farmer_intent_async(text, api_key="test_key", client=mock_client)

    # 10 tons = 100 quintals
    assert intent.crop == "potato"
    assert intent.quantity_quintals == 100.0
    assert intent.origin_location == "Indore"
    assert intent.extraction_source == "rule_based_fallback"


def test_sanitize_and_parse_json():
    """Test JSON cleaning helper."""
    assert _sanitize_and_parse_json(None) is None
    assert _sanitize_and_parse_json("") is None
    assert _sanitize_and_parse_json("not json") is None
    assert _sanitize_and_parse_json('{"crop": "wheat"}') == {"crop": "wheat"}
    assert _sanitize_and_parse_json('```json\n{"crop": "onion"}\n```') == {"crop": "onion"}


def test_sync_extract_farmer_intent():
    """Test synchronous convenience wrapper."""
    text = "I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."
    intent = extract_farmer_intent(text)
    assert intent.crop == "onion"
    assert intent.quantity_quintals == 50.0
    assert intent.origin_location == "Nashik"
    assert intent.minimum_net_return_inr == 85000.0


@pytest.mark.asyncio
async def test_extract_intent_asi1_http_500_retry_succeeds():
    """Verify that transient HTTP 500 triggers retry and succeeds on subsequent attempt."""
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    mock_resp_500.text = "Internal Server Error"

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"crop": "onion", "quantity_quintals": 50.0, "origin_location": "Nashik", '
                        '"selling_deadline": "tomorrow", "minimum_net_return_inr": 85000.0, '
                        '"storage_preference": null}'
                    )
                }
            }
        ]
    }

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    # First call returns 500, second call returns 200
    mock_client.post.side_effect = [mock_resp_500, mock_resp_200]

    text = "I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."
    intent = await extract_farmer_intent_async(
        text, api_key="test_api_key_123", client=mock_client, backoff_seconds=0.01
    )

    assert mock_client.post.await_count == 2
    assert intent.crop == "onion"
    assert intent.quantity_quintals == 50.0
    assert intent.origin_location == "Nashik"
    assert intent.extraction_source == "asi1"
    assert intent.is_complete is True


@pytest.mark.asyncio
async def test_extract_intent_asi1_http_500_persistent_fallback():
    """Verify that persistent HTTP 500 falls back gracefully to rule-based parser with explicit provenance."""
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    mock_resp_500.text = "Internal Server Error"

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    # Both calls return 500
    mock_client.post.side_effect = [mock_resp_500, mock_resp_500]

    text = "50 quintals of onions in Nashik"
    intent = await extract_farmer_intent_async(
        text, api_key="test_api_key_123", client=mock_client, backoff_seconds=0.01
    )

    assert mock_client.post.await_count == 2
    assert intent.crop == "onion"
    assert intent.quantity_quintals == 50.0
    assert intent.origin_location == "Nashik"
    assert "rule_based_fallback" in intent.extraction_source
    assert "500" in intent.extraction_source
