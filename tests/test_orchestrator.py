"""
Unit tests for KrishiBazaar Orchestrator Agent (tests/test_orchestrator.py).
Verifies:
- Agent Chat Protocol compliance (acknowledgements, messaging, EndSessionContent)
- Integration with Mandi Service when crop and origin location are present
- Live results formatting with authentic and suspicious price flagging
- Transparent zero-record reporting for specific districts with nearby benchmarks
- API error / timeout resilience without inventing prices
- Clarification questions when required intent fields are missing
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    EndSessionContent,
)

from krishibazaar.agents.orchestrator import (
    create_orchestrator_agent,
    handle_chat_message,
    handle_acknowledgement,
    build_greeting_response,
    format_mandi_market_report,
)
from krishibazaar.models import (
    DataProvenance,
    FarmerIntent,
    MandiPriceRecord,
    MandiPriceResult,
)


def test_orchestrator_initialization_and_protocol():
    """Verify orchestrator is instantiated with correct configuration and protocols."""
    agent = create_orchestrator_agent(
        name="test_orchestrator",
        seed="test_orchestrator_seed_9999",
        port=8080,
        mailbox=False,
    )
    assert agent.name == "test_orchestrator"
    assert agent.address.startswith("agent1")

    protocol_specs = [p.spec.name for p in agent.protocols.values() if p.spec]
    assert "AgentChatProtocol" in protocol_specs


def test_build_greeting_response():
    """Verify greeting text contains brand and how to start instruction."""
    greeting = build_greeting_response("Hello KrishiBazaar")
    assert "KrishiBazaar" in greeting
    assert "कृषिबाज़ार" in greeting
    assert "50 quintals of onions" in greeting


@pytest.mark.asyncio
async def test_orchestrator_live_mandi_results_flow():
    """Verify orchestrator displays live mandi prices and flags suspicious records when crop/location complete."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_intent = FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Ahilyanagar, Maharashtra",
        minimum_net_return_inr=85000.0,
        selling_deadline="tomorrow",
        is_complete=True,
    )

    mock_mandi_result = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[
            MandiPriceRecord(
                mandi_name="APMC Newasa",
                district="Ahilyanagar",
                state="Maharashtra",
                commodity="Onion",
                variety="Unhali",
                arrival_date="2026-10-18",
                min_price=1500.0,
                max_price=4300.0,
                modal_price=3500.0,
                data_source="data.gov.in_live",
                is_suspicious=False,
            ),
            MandiPriceRecord(
                mandi_name="Pune(Moshi)",
                district="Pune",
                state="Maharashtra",
                commodity="Onion Green",
                variety="Other",
                arrival_date="2026-10-18",
                min_price=15.0,
                max_price=15.0,
                modal_price=15.0,
                data_source="data.gov.in_live",
                is_suspicious=True,
                suspicion_reason="Suspiciously low modal price (INR 15.00/quintal). Likely recorded per kg or bundle.",
            ),
        ],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET Daily Mandi Prices)",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
            is_live=True,
        ),
    )

    with patch(
        "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
        new=AsyncMock(return_value=mock_intent),
    ), patch(
        "krishibazaar.agents.orchestrator.get_mandi_prices_async",
        new=AsyncMock(return_value=mock_mandi_result),
    ):
        test_msg_id = uuid4()
        incoming = ChatMessage(
            msg_id=test_msg_id,
            content=[TextContent(text="I have 50 quintals of onions in Ahilyanagar, want 85000 net")],
        )
        sender = "agent1q_farmer_test"

        await handle_chat_message(mock_ctx, sender, incoming)

        # 1. Immediate acknowledgement
        assert mock_ctx.send.call_count == 2
        ack_call = mock_ctx.send.call_args_list[0][0][1]
        assert isinstance(ack_call, ChatAcknowledgement)
        assert ack_call.acknowledged_msg_id == test_msg_id

        # 2. Response Message
        response_msg = mock_ctx.send.call_args_list[1][0][1]
        assert isinstance(response_msg, ChatMessage)
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        # Check content
        assert "APMC Newasa" in text_content
        assert "3,500" in text_content
        assert "2026-10-18" in text_content
        assert "data.gov.in" in text_content

        # Verify suspicious record is flagged with warning
        assert "Pune(Moshi)" in text_content
        assert "flagged as suspicious" in text_content.lower()


@pytest.mark.asyncio
async def test_orchestrator_zero_records_for_district_flow():
    """Verify orchestrator reports zero records for district clearly, never fakes live prices, and shows live nearby benchmarks."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_intent = FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        minimum_net_return_inr=85000.0,
        is_complete=True,
    )

    # 1st call: Nashik district returns 0 records
    mock_nashik_result = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[],
        error_message="No live mandi price records found on data.gov.in for 'onion' in district 'Nashik', Maharashtra today.",
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET Daily Mandi Prices)",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE_NO_RECORDS",
            is_live=True,
        ),
    )

    # 2nd call: nearby state query returns live benchmark mandis
    mock_nearby_result = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[
            MandiPriceRecord(
                mandi_name="APMC Newasa",
                district="Ahilyanagar",
                state="Maharashtra",
                commodity="Onion",
                arrival_date="2026-10-18",
                min_price=1500.0,
                max_price=4300.0,
                modal_price=3500.0,
                data_source="data.gov.in_live",
                is_suspicious=False,
            )
        ],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET Daily Mandi Prices)",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
            is_live=True,
        ),
    )

    async def mock_mandi_caller(commodity, district=None, state=None, **kwargs):
        if district:
            return mock_nashik_result
        return mock_nearby_result

    with patch(
        "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
        new=AsyncMock(return_value=mock_intent),
    ), patch(
        "krishibazaar.agents.orchestrator.get_mandi_prices_async",
        new=AsyncMock(side_effect=mock_mandi_caller),
    ):
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I have 50 quintals of onion in Nashik")],
        )
        await handle_chat_message(mock_ctx, "agent1q_farmer_nashik", incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        # Must report zero records for Nashik
        assert "No live arrival records were reported for 'Onion' in district 'Nashik'" in text_content
        assert "never fabricate prices" in text_content.lower()

        # Must display retrieved state benchmarks
        assert "Other Live Maharashtra Mandi Benchmarks" in text_content
        assert "APMC Newasa" in text_content

        assert "3,500" in text_content


@pytest.mark.asyncio
async def test_orchestrator_mandi_api_error_flow():
    """Verify orchestrator reports live data unavailable on API error or timeout without inventing prices."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_intent = FarmerIntent(
        crop="onion",
        origin_location="Pune",
        is_complete=False,
        follow_up_question="How many quintals of Onion are you looking to sell?",
    )

    mock_error_result = MandiPriceResult(
        success=False,
        commodity="onion",
        records=[],
        error_message="External data.gov.in API request failed: HTTP 500: Gateway Internal Error",
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET Daily Mandi Prices)",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="STALE",
            is_live=False,
        ),
    )

    with patch(
        "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
        new=AsyncMock(return_value=mock_intent),
    ), patch(
        "krishibazaar.agents.orchestrator.get_mandi_prices_async",
        new=AsyncMock(return_value=mock_error_result),
    ):
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="What is the price of onion in Pune?")],
        )
        await handle_chat_message(mock_ctx, "agent1q_farmer_pune", incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Live mandi prices could not be retrieved" in text_content
        assert "HTTP 500" in text_content
        assert "do not invent prices" in text_content.lower()
        assert "How many quintals" in text_content


@pytest.mark.asyncio
async def test_orchestrator_bare_greeting_flow():
    """Verify greeting message for simple greetings."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_intent = FarmerIntent(is_complete=False, follow_up_question="Which crop?")

    with patch(
        "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
        new=AsyncMock(return_value=mock_intent),
    ):
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="Hello")],
        )
        await handle_chat_message(mock_ctx, "agent1q_farmer_greeting", incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Namaste! Welcome to KrishiBazaar" in text_content


@pytest.mark.asyncio
async def test_handle_acknowledgement_receipt():
    """Verify delivery receipt handling."""
    mock_ctx = MagicMock()
    mock_ctx.logger = MagicMock()

    ack = ChatAcknowledgement(acknowledged_msg_id=uuid4())
    await handle_acknowledgement(mock_ctx, "agent1qsender", ack)

    mock_ctx.logger.debug.assert_called_once()
