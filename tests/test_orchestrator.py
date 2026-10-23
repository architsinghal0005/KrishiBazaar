"""
Unit tests for KrishiBazaar Orchestrator Agent (tests/test_orchestrator.py).
Verifies:
- Agent Chat Protocol compliance (acknowledgements, messaging, EndSessionContent)
- Integration with Mandi Service when crop and origin location are present
- Multi-specialist coordination (Market, Weather, Buyer, Transport) and deterministic optimizer
- Live results formatting with authentic and suspicious price flagging
- Transparent zero-record reporting for specific districts with nearby benchmarks
- Weather hazard forecasting and unavailable/timeout resilience
- Simulated buyer offer and transport quote disclaimers
- Farmer approval safety gate (no real booking without approval)
- Minimum net-return threshold enforcement and shortfall notices
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
    format_weather_report,
    format_optimization_report,
    coordinate_specialists_async,
)
from krishibazaar.models import (
    BuyerOffer,
    BuyerOffersResult,
    DataProvenance,
    FarmerIntent,
    MandiPriceRecord,
    MandiPriceResult,
    TransportQuote,
    TransportQuotesResult,
    WeatherResult,
    WeatherRiskReport,
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
    assert "à¤•à¥ƒà¤·à¤¿à¤¬à¤¾à¤œà¤¼à¤¾à¤°" in greeting
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

    mock_weather = WeatherResult(
        success=True,
        location="Ahilyanagar",
        report=WeatherRiskReport(
            location="Ahilyanagar",
            forecast_date="2026-10-18",
            temperature_max_c=30.0,
            temperature_min_c=20.0,
            precipitation_sum_mm=0.0,
            precipitation_probability_pct=10.0,
            wind_speed_max_kmh=12.0,
            spoilage_hazard_level="LOW",
            transit_advice="Safe transit conditions.",
        ),
        provenance=DataProvenance(
            source="Open-Meteo",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
        ),
    )

    with patch(
        "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
        new=AsyncMock(return_value=mock_intent),
    ), patch(
        "krishibazaar.agents.orchestrator.get_mandi_prices_async",
        new=AsyncMock(return_value=mock_mandi_result),
    ), patch(
        "krishibazaar.agents.orchestrator.get_transit_weather_async",
        new=AsyncMock(return_value=mock_weather),
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

    mock_weather = WeatherResult(
        success=True,
        location="Nashik",
        report=WeatherRiskReport(
            location="Nashik",
            forecast_date="2026-10-18",
            temperature_max_c=31.0,
            temperature_min_c=19.0,
            precipitation_sum_mm=0.0,
            precipitation_probability_pct=15.0,
            wind_speed_max_kmh=14.0,
            spoilage_hazard_level="LOW",
            transit_advice="Safe transit conditions.",
        ),
        provenance=DataProvenance(
            source="Open-Meteo",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
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
    ), patch(
        "krishibazaar.agents.orchestrator.get_transit_weather_async",
        new=AsyncMock(return_value=mock_weather),
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
async def test_orchestrator_multi_specialist_coordination_success():
    """Verify orchestrator coordinates Market, Weather, Buyer, Transport and Optimizer when intent is complete."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_intent = FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        minimum_net_return_inr=80000.0,
        is_complete=True,
    )

    mock_mandi = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[
            MandiPriceRecord(
                mandi_name="Lasalgaon APMC",
                district="Nashik",
                state="Maharashtra",
                commodity="Onion",
                arrival_date="2026-10-18",
                min_price=2200.0,
                max_price=3600.0,
                modal_price=3200.0,
                data_source="data.gov.in_live",
                is_suspicious=False,
            )
        ],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET)",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
            is_live=True,
        ),
    )

    mock_weather = WeatherResult(
        success=True,
        location="Nashik",
        report=WeatherRiskReport(
            location="Nashik",
            forecast_date="2026-10-18",
            temperature_max_c=31.0,
            temperature_min_c=19.5,
            precipitation_sum_mm=2.5,
            precipitation_probability_pct=45.0,
            wind_speed_max_kmh=15.0,
            spoilage_hazard_level="MEDIUM",
            transit_advice="Moderate rain showers possible. Tarpaulin cover strongly advised.",
        ),
        provenance=DataProvenance(
            source="Open-Meteo",
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
        new=AsyncMock(return_value=mock_mandi),
    ), patch(
        "krishibazaar.agents.orchestrator.get_transit_weather_async",
        new=AsyncMock(return_value=mock_weather),
    ):
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I have 50 quintals of onion in Nashik, want 80000 net")],
        )
        await handle_chat_message(mock_ctx, "agent1q_farmer_coord", incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        # 1. Market Section
        assert "Lasalgaon APMC" in text_content
        assert "3,200" in text_content

        # 2. Weather Section
        assert "Transit Weather & Spoilage Hazard" in text_content
        assert "MEDIUM HAZARD" in text_content

        # 3. Deterministic Net-Return Optimization Section
        assert "Deterministic Net-Return Optimization" in text_content
        assert "Estimated Net" in text_content
        assert "guaranteed net return" not in text_content.lower()

        # 4. Itemized Deductions
        assert "Itemized Deductions" in text_content
        assert "Transport:" in text_content
        assert "APMC Statutory Cess:" in text_content

        # 5. Simulation Notice
        assert "Simulation Notice:" in text_content
        assert "simulated benchmark models" in text_content

        # 6. Safety Gate
        assert "Safety Gate:" in text_content
        assert "No real booking, payment, or carrier dispatch has been made" in text_content
        assert "explicit approval" in text_content


@pytest.mark.asyncio
async def test_orchestrator_specialist_partial_failure_resilience():
    """Verify orchestrator handles specialist failure/timeout gracefully without crashing."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_intent = FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        is_complete=True,
    )

    mock_mandi = MandiPriceResult(
        success=False,
        commodity="onion",
        error_message="Mandi gateway timeout after 4.0s",
        provenance=DataProvenance(
            source="data.gov.in",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="STALE",
        ),
    )

    mock_weather = WeatherResult(
        success=False,
        location="Nashik",
        error_message="Weather API 503 Service Unavailable",
        provenance=DataProvenance(
            source="Open-Meteo",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="STALE",
        ),
    )

    with patch(
        "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
        new=AsyncMock(return_value=mock_intent),
    ), patch(
        "krishibazaar.agents.orchestrator.get_mandi_prices_async",
        new=AsyncMock(return_value=mock_mandi),
    ), patch(
        "krishibazaar.agents.orchestrator.get_transit_weather_async",
        new=AsyncMock(return_value=mock_weather),
    ):
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I have 50 quintals onion in Nashik")],
        )
        await handle_chat_message(mock_ctx, "agent1q_farmer_resilience", incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        # Reports transparent failures
        assert "Market Data Notice:" in text_content
        assert "Transit Weather Notice:" in text_content
        # Buyer channels still evaluate
        assert "Deterministic Net-Return Optimization" in text_content
        assert "Safety Gate:" in text_content


@pytest.mark.asyncio
async def test_orchestrator_minimum_net_return_shortfall_warning():
    """Verify orchestrator warns farmer when requested minimum net return exceeds all options."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    # Impossible target of INR 500,000 for 10 quintals
    mock_intent = FarmerIntent(
        crop="onion",
        quantity_quintals=10.0,
        origin_location="Nashik",
        minimum_net_return_inr=500000.0,
        is_complete=True,
    )

    mock_mandi = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[
            MandiPriceRecord(
                mandi_name="Lasalgaon APMC",
                district="Nashik",
                state="Maharashtra",
                commodity="Onion",
                arrival_date="2026-10-18",
                min_price=2200.0,
                max_price=3600.0,
                modal_price=3200.0,
                data_source="data.gov.in_live",
                is_suspicious=False,
            )
        ],
        provenance=DataProvenance(
            source="data.gov.in",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
        ),
    )

    mock_weather = WeatherResult(
        success=True,
        location="Nashik",
        report=WeatherRiskReport(
            location="Nashik",
            forecast_date="2026-10-18",
            temperature_max_c=30.0,
            temperature_min_c=20.0,
            precipitation_sum_mm=0.0,
            precipitation_probability_pct=10.0,
            wind_speed_max_kmh=10.0,
            spoilage_hazard_level="LOW",
            transit_advice="Safe transit.",
        ),
        provenance=DataProvenance(
            source="Open-Meteo",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
        ),
    )

    with patch(
        "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
        new=AsyncMock(return_value=mock_intent),
    ), patch(
        "krishibazaar.agents.orchestrator.get_mandi_prices_async",
        new=AsyncMock(return_value=mock_mandi),
    ), patch(
        "krishibazaar.agents.orchestrator.get_transit_weather_async",
        new=AsyncMock(return_value=mock_weather),
    ):
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I have 10 quintals of onion, want 500000 net in Nashik")],
        )
        await handle_chat_message(mock_ctx, "agent1q_farmer_shortfall", incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Below Minimum" in text_content or "Target Threshold Notice:" in text_content
        assert "500,000" in text_content


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


@pytest.mark.asyncio
async def test_orchestrator_forecast_date_alignment_and_net_return_provenance_labels():
    """Verify orchestrator aligns weather forecast with requested selling deadline (tomorrow) and formats net-return provenance labels."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_intent = FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        selling_deadline="tomorrow",
        minimum_net_return_inr=80000.0,
        is_complete=True,
    )

    mock_mandi = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[
            MandiPriceRecord(
                mandi_name="Lasalgaon APMC",
                district="Nashik",
                state="Maharashtra",
                commodity="Onion",
                arrival_date="2026-10-18",
                min_price=2200.0,
                max_price=3600.0,
                modal_price=3200.0,
                data_source="data.gov.in_live",
                is_suspicious=False,
            )
        ],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET)",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
            is_live=True,
        ),
    )

    # Weather report for tomorrow (2026-10-19)
    mock_weather = WeatherResult(
        success=True,
        location="Nashik",
        report=WeatherRiskReport(
            location="Nashik",
            forecast_date="2026-10-19",
            target_date_requested="2026-10-19",
            is_target_date_matched=True,
            temperature_max_c=32.0,
            temperature_min_c=19.0,
            precipitation_sum_mm=0.0,
            precipitation_probability_pct=10.0,
            wind_speed_max_kmh=12.0,
            spoilage_hazard_level="LOW",
            transit_advice="Clear dry skies anticipated. Suitable for uncovered or standard transit.",
        ),
        provenance=DataProvenance(
            source="Open-Meteo",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-19",
            freshness_status="LIVE",
            is_live=True,
        ),
    )

    mock_buyer_result = BuyerOffersResult(
        success=True,
        crop="onion",
        quantity_quintals=50.0,
        offers=[
            BuyerOffer(
                offer_id="BO-01",
                buyer_id="B-01",
                buyer_name="Local Trader",
                buyer_type="TRADER",
                crop="onion",
                offered_price_per_quintal=2600.0,
                quantity_quintals=50.0,
                farmgate_pickup=True,
                is_simulated=True,
            )
        ],
        provenance=DataProvenance(
            source="KrishiBazaar Simulated Buyer Network",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
        ),
    )

    with patch(
        "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
        new=AsyncMock(return_value=mock_intent),
    ), patch(
        "krishibazaar.agents.orchestrator.get_mandi_prices_async",
        new=AsyncMock(return_value=mock_mandi),
    ), patch(
        "krishibazaar.agents.orchestrator.get_buyer_offers_async",
        new=AsyncMock(return_value=mock_buyer_result),
    ), patch(
        "krishibazaar.agents.orchestrator.get_transit_weather_async",
        new=AsyncMock(return_value=mock_weather),
    ) as mock_get_weather:
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I have 50 quintals of onion in Nashik, want 80000 net by tomorrow")],
        )
        await handle_chat_message(mock_ctx, "agent1q_farmer_deadline", incoming)

        # Verify get_transit_weather_async was called with target_date
        mock_get_weather.assert_called_once()
        call_kwargs = mock_get_weather.call_args[1]
        assert call_kwargs.get("target_date") is not None

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        # 1. Weather forecast date alignment
        assert "Forecast aligned with requested selling/transit date: 2026-10-19" in text_content
        assert "2026-10-19" in text_content

        # 2. Top option APMC Mandi provenance & distinction
        assert "APMC Wholesale Mandi (Official Benchmark Modal Price â€” no buyer contract exists)" in text_content
        assert "Estimated return only; NOT a guaranteed payout" in text_content
        assert "guaranteed net return" not in text_content.lower()
        assert "not a guaranteed payout" in text_content.lower()

        # 3. Itemized deductions and assumptions
        assert "Itemized Deductions" in text_content
        assert "Assumed statutory tariff @ 1.5%" in text_content
        assert "Assumed standard labor charge @ INR 25/qtl" in text_content
        assert "Simulated freight quote" in text_content
        assert "No confirmed buyer offer exists; APMC prices depend on daily physical auction bidding" in text_content


@pytest.mark.asyncio
async def test_orchestrator_forecast_date_outside_window_warning():
    """Verify that when requested deadline is outside the forecast window, orchestrator clearly states actual forecast date."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_intent = FarmerIntent(
        crop="onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        selling_deadline="2026-11-25",
        is_complete=True,
    )

    mock_mandi = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET)",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE_NO_RECORDS",
            is_live=True,
        ),
    )

    # Weather report falls back to today because Nov 25 is outside window
    mock_weather = WeatherResult(
        success=True,
        location="Nashik",
        report=WeatherRiskReport(
            location="Nashik",
            forecast_date="2026-10-18",
            target_date_requested="2026-11-25",
            is_target_date_matched=False,
            temperature_max_c=31.0,
            temperature_min_c=19.0,
            precipitation_sum_mm=0.0,
            precipitation_probability_pct=10.0,
            wind_speed_max_kmh=12.0,
            spoilage_hazard_level="LOW",
            transit_advice="Actual forecast date: 2026-10-18. Requested transit date '2026-11-25' was outside available forecast window.",
        ),
        provenance=DataProvenance(
            source="Open-Meteo",
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
        new=AsyncMock(return_value=mock_mandi),
    ), patch(
        "krishibazaar.agents.orchestrator.get_transit_weather_async",
        new=AsyncMock(return_value=mock_weather),
    ):
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I have 50 quintals of onion in Nashik, deadline 2026-11-25")],
        )
        await handle_chat_message(mock_ctx, "agent1q_farmer_outside", incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        # Must report actual forecast date and warn against treating it as Nov 25 forecast
        assert "Actual forecast date: 2026-10-18" in text_content
        assert "outside available forecast window" in text_content
        assert "do NOT treat this as 2026-11-25's forecast" in text_content


@pytest.mark.asyncio
async def test_orchestrator_payment_authorization_safety_gate_disabled():
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()

    from uagents_core.contrib.protocols.chat.cards import create_card_response_content
    auth_resp = create_card_response_content(
        selection={
            "action": "authorize_payment",
            "reference": "KB-PAY-TEST-GATE",
            "amount": 1000,
            "currency": "atestfet",
            "recipient": "fetch1testaddr",
        }
    )
    incoming = ChatMessage(msg_id=uuid4(), content=[auth_resp])

    await handle_chat_message(mock_ctx, "agent1q_farmer_pay_gate", incoming)

    response_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

    assert "Payment Protocol Safety Gate Active" in text_content
    assert "ENABLE_PAYMENT_PROTOCOL=false" in text_content


@pytest.mark.asyncio
async def test_orchestrator_payment_rejection_handling():
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()

    from uagents_core.contrib.protocols.chat.cards import create_card_response_content
    rej_resp = create_card_response_content(
        selection={
            "action": "reject_payment",
            "reference": "KB-PAY-REJ-01",
        }
    )
    incoming = ChatMessage(msg_id=uuid4(), content=[rej_resp])

    await handle_chat_message(mock_ctx, "agent1q_farmer_pay_rej", incoming)

    response_msg = mock_ctx.send.call_args_list[1][0][1]
    text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

    assert "Payment Request Declined" in text_content
    assert "KB-PAY-REJ-01" in text_content
