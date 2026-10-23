"""
Unit tests for KrishiBazaar Inter-Agent Communication (tests/test_inter_agent_communication.py).
Verifies:
- Typed query message sending to registered agent addresses (Market, Weather, Buyer A, Buyer B, Transport)
- Dual messaging support: Context.send_and_receive and standalone send_sync_message
- Handling of missing agent addresses: transparent direct-service fallback (local dev) vs disabled fallback
- Partial-failure resilience when specialist agents time out or return delivery failures
- Multi-buyer coordination combining Buyer A and Buyer B offers
- ChatMessage integration ensuring Context is passed down to specialist communications
"""

from unittest.mock import AsyncMock, MagicMock, patch, ANY
from uuid import uuid4
import pytest
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    EndSessionContent,
)

from krishibazaar.agents.orchestrator import (
    coordinate_specialists_async,
    handle_chat_message,
    query_specialist_agent_async,
)
from krishibazaar.config import settings
from krishibazaar.models import (
    BuyerOffer,
    BuyerOffersResult,
    BuyerQuery,
    DataProvenance,
    FarmerIntent,
    MandiPriceRecord,
    MandiPriceResult,
    MarketQuery,
    TransportQuote,
    TransportQuotesResult,
    TransportQuery,
    WeatherResult,
    WeatherRiskReport,
    WeatherQuery,
)


@pytest.mark.asyncio
async def test_query_specialist_agent_async_via_ctx_send_and_receive():
    """Verify query_specialist_agent_async uses ctx.send_and_receive when available."""
    mock_ctx = MagicMock()
    mock_ctx.send_and_receive = AsyncMock()

    mock_result = MandiPriceResult(
        success=True,
        commodity="Onion",
        records=[],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET)",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
            is_live=True,
        ),
    )
    mock_status = MagicMock()
    mock_status.status = "delivered"
    mock_ctx.send_and_receive.return_value = (mock_result, mock_status)

    query_msg = MarketQuery(commodity="Onion", district="Nashik", state="Maharashtra")
    res, err = await query_specialist_agent_async(
        destination="agent1qmarket_destination",
        query_msg=query_msg,
        response_type=MandiPriceResult,
        specialist_name="Market Specialist",
        ctx=mock_ctx,
        timeout=5.0,
    )

    assert res == mock_result
    assert err is None
    mock_ctx.send_and_receive.assert_awaited_once_with(
        destination="agent1qmarket_destination",
        message=query_msg,
        response_type=MandiPriceResult,
        timeout=5,
    )


@pytest.mark.asyncio
async def test_query_specialist_agent_async_via_send_sync_message_fallback():
    """Verify query_specialist_agent_async falls back to send_sync_message when ctx is None."""
    mock_result = WeatherResult(
        success=True,
        location="Nashik",
        report=WeatherRiskReport(
            location="Nashik",
            forecast_date="2026-10-19",
            temperature_max_c=31.0,
            temperature_min_c=20.0,
            precipitation_sum_mm=0.0,
            precipitation_probability_pct=5.0,
            wind_speed_max_kmh=10.0,
            spoilage_hazard_level="LOW",
            transit_advice="Clear conditions.",
        ),
        provenance=DataProvenance(
            source="Open-Meteo API",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-19",
            freshness_status="LIVE",
        ),
    )

    query_msg = WeatherQuery(location="Nashik", target_date="2026-10-19")
    with patch(
        "uagents.communication.send_sync_message",
        new=AsyncMock(return_value=mock_result),
    ) as mock_send_sync:
        res, err = await query_specialist_agent_async(
            destination="agent1qweather_destination",
            query_msg=query_msg,
            response_type=WeatherResult,
            specialist_name="Weather Specialist",
            ctx=None,
            timeout=5.0,
        )

        assert res == mock_result
        assert err is None
        mock_send_sync.assert_awaited_once_with(
            destination="agent1qweather_destination",
            message=query_msg,
            response_type=WeatherResult,
            resolver=ANY,
            timeout=5,
        )


@pytest.mark.asyncio
async def test_orchestrator_sends_to_all_configured_specialist_addresses():
    """Verify coordinate_specialists_async dispatches typed queries to configured specialist agent addresses."""
    # Configure specialist agent addresses
    original_market = settings.market_agent_address
    original_weather = settings.weather_agent_address
    original_buyer = settings.buyer_agent_address
    original_buyer_b = settings.buyer_b_agent_address
    original_transport = settings.transport_agent_address

    try:
        settings.market_agent_address = "agent1qmarket_registered_address"
        settings.weather_agent_address = "agent1qweather_registered_address"
        settings.buyer_agent_address = "agent1qbuyer_a_registered_address"
        settings.buyer_b_agent_address = "agent1qbuyer_b_registered_address"
        settings.transport_agent_address = "agent1qtransport_registered_address"

        mock_mandi = MandiPriceResult(
            success=True,
            commodity="Onion",
            records=[
                MandiPriceRecord(
                    mandi_name="Nashik Mandi",
                    district="Nashik",
                    state="Maharashtra",
                    commodity="Onion",
                    variety="Red",
                    arrival_date="2026-10-18",
                    min_price=2800.0,
                    max_price=3600.0,
                    modal_price=3300.0,
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
                forecast_date="2026-10-19",
                temperature_max_c=30.0,
                temperature_min_c=18.0,
                precipitation_sum_mm=0.0,
                precipitation_probability_pct=5.0,
                wind_speed_max_kmh=12.0,
                spoilage_hazard_level="LOW",
                transit_advice="Safe transit conditions.",
            ),
            provenance=DataProvenance(
                source="Open-Meteo API",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-19",
                freshness_status="LIVE",
            ),
        )

        mock_buyer_a = BuyerOffersResult(
            success=True,
            crop="Onion",
            quantity_quintals=50.0,
            offers=[
                BuyerOffer(
                    offer_id="BO-A-01",
                    buyer_id="BUYER-FPO-01",
                    buyer_name="Sahyadri FPO",
                    buyer_type="FPO Aggregator",
                    crop="Onion",
                    quantity_quintals=50.0,
                    offered_price_per_quintal=3200.0,
                    farmgate_pickup=True,
                    is_simulated=True,
                    disclaimer="Simulated benchmark offer from Buyer A.",
                )
            ],
            provenance=DataProvenance(
                source="Buyer A Network",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="SIMULATED_SAMPLE",
            ),
        )

        mock_buyer_b = BuyerOffersResult(
            success=True,
            crop="Onion",
            quantity_quintals=50.0,
            offers=[
                BuyerOffer(
                    offer_id="BO-B-01",
                    buyer_id="BUYER-EXP-01",
                    buyer_name="Kalyani Agro-Exports Pvt Ltd",
                    buyer_type="Export Aggregator",
                    crop="Onion",
                    quantity_quintals=50.0,
                    offered_price_per_quintal=3500.0,
                    farmgate_pickup=False,
                    is_simulated=True,
                    disclaimer="Simulated benchmark offer from Buyer B.",
                )
            ],
            provenance=DataProvenance(
                source="Buyer B Network",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="SIMULATED_SAMPLE",
            ),
        )

        mock_transport = TransportQuotesResult(
            success=True,
            origin="Nashik",
            quantity_quintals=50.0,
            quotes={
                "Nashik Mandi": TransportQuote(
                    quote_id="TQ-01",
                    transporter_id="TRANS-01",
                    transporter_name="Maha Logistics",
                    vehicle_type="3-Tonne Mini-Truck",
                    capacity_quintals=60.0,
                    route_origin="Nashik",
                    route_destination="Nashik Mandi",
                    distance_km=25.0,
                    base_fare=500.0,
                    rate_per_km=18.0,
                    total_transport_cost=950.0,
                    estimated_transit_hours=1.5,
                    is_simulated=True,
                )
            },
            provenance=DataProvenance(
                source="Simulated Logistics Network",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="SIMULATED_SAMPLE",
            ),
        )

        dispatched_queries = []

        async def fake_query_specialist(destination, query_msg, response_type, specialist_name, ctx=None, timeout=8.0):
            dispatched_queries.append((destination, query_msg, response_type))
            if destination == "agent1qmarket_registered_address":
                return mock_mandi, None
            elif destination == "agent1qweather_registered_address":
                return mock_weather, None
            elif destination == "agent1qbuyer_a_registered_address":
                return mock_buyer_a, None
            elif destination == "agent1qbuyer_b_registered_address":
                return mock_buyer_b, None
            elif destination == "agent1qtransport_registered_address":
                return mock_transport, None
            return None, "Unknown destination"

        with patch(
            "krishibazaar.agents.orchestrator.query_specialist_agent_async",
            side_effect=fake_query_specialist,
        ):
            coord = await coordinate_specialists_async(
                crop="onion",
                quantity_quintals=50.0,
                origin_location="Nashik, Maharashtra",
                selling_deadline="tomorrow",
                minimum_net_return_inr=80000.0,
            )

        # Assert all 5 specialist agent addresses received queries
        destinations = [item[0] for item in dispatched_queries]
        assert "agent1qmarket_registered_address" in destinations
        assert "agent1qweather_registered_address" in destinations
        assert "agent1qbuyer_a_registered_address" in destinations
        assert "agent1qbuyer_b_registered_address" in destinations
        assert "agent1qtransport_registered_address" in destinations

        # Assert query message types
        for dest, msg, r_type in dispatched_queries:
            if dest == "agent1qmarket_registered_address":
                assert isinstance(msg, MarketQuery)
                assert msg.commodity.lower() == "onion"
            elif dest == "agent1qweather_registered_address":
                assert isinstance(msg, WeatherQuery)
                assert "Nashik" in msg.location
            elif dest == "agent1qbuyer_a_registered_address":
                assert isinstance(msg, BuyerQuery)
                assert msg.quantity_quintals == 50.0
            elif dest == "agent1qbuyer_b_registered_address":
                assert isinstance(msg, BuyerQuery)
            elif dest == "agent1qtransport_registered_address":
                assert isinstance(msg, TransportQuery)
                assert msg.quantity_quintals == 50.0

        # Assert buyer coordination merged offers from Buyer A and Buyer B
        buyer_res: BuyerOffersResult = coord["buyer_result"]
        assert buyer_res.success is True
        assert len(buyer_res.offers) == 2
        buyer_names = [o.buyer_name for o in buyer_res.offers]
        assert "Sahyadri FPO" in buyer_names
        assert "Kalyani Agro-Exports Pvt Ltd" in buyer_names

        # Assert optimizer received and ranked the options
        opt_res = coord["optimization_result"]
        assert len(opt_res.all_options) >= 1
        assert opt_res.recommended_option is not None

    finally:
        settings.market_agent_address = original_market
        settings.weather_agent_address = original_weather
        settings.buyer_agent_address = original_buyer
        settings.buyer_b_agent_address = original_buyer_b
        settings.transport_agent_address = original_transport


@pytest.mark.asyncio
async def test_orchestrator_handles_missing_addresses_when_fallback_disabled():
    """Verify coordinate_specialists_async fails gracefully when addresses are unconfigured and fallback is disabled."""
    orig_fallback = settings.allow_direct_service_fallback
    orig_market = settings.market_agent_address
    orig_weather = settings.weather_agent_address
    orig_buyer = settings.buyer_agent_address
    orig_buyer_b = settings.buyer_b_agent_address
    orig_transport = settings.transport_agent_address

    try:
        settings.allow_direct_service_fallback = False
        settings.market_agent_address = None
        settings.weather_agent_address = None
        settings.buyer_agent_address = None
        settings.buyer_b_agent_address = None
        settings.transport_agent_address = None

        coord = await coordinate_specialists_async(
            crop="wheat",
            quantity_quintals=25.0,
            origin_location="Pune",
        )

        mandi_res: MandiPriceResult = coord["mandi_result"]
        weather_res: WeatherResult = coord["weather_result"]
        buyer_res: BuyerOffersResult = coord["buyer_result"]
        transport_res: TransportQuotesResult = coord["transport_result"]

        assert mandi_res.success is False
        assert "not configured" in mandi_res.error_message.lower()

        assert weather_res.success is False
        assert "not configured" in weather_res.error_message.lower()

        assert buyer_res.success is False
        assert "not configured" in buyer_res.error_message.lower()

        assert transport_res.success is False
        assert "not configured" in transport_res.error_message.lower()

    finally:
        settings.allow_direct_service_fallback = orig_fallback
        settings.market_agent_address = orig_market
        settings.weather_agent_address = orig_weather
        settings.buyer_agent_address = orig_buyer
        settings.buyer_b_agent_address = orig_buyer_b
        settings.transport_agent_address = orig_transport


@pytest.mark.asyncio
async def test_orchestrator_handles_specialist_timeout_with_partial_failure_resilience():
    """Verify orchestrator preserves timeout error from a configured agent when direct fallback is disabled."""
    orig_market = settings.market_agent_address
    orig_weather = settings.weather_agent_address
    orig_fallback = settings.allow_direct_service_fallback

    try:
        settings.allow_direct_service_fallback = False
        settings.market_agent_address = "agent1qmarket_timeout"
        settings.weather_agent_address = "agent1qweather_ok"

        mock_weather = WeatherResult(
            success=True,
            location="Nashik",
            report=WeatherRiskReport(
                location="Nashik",
                forecast_date="2026-10-18",
                temperature_max_c=30.0,
                temperature_min_c=20.0,
                precipitation_sum_mm=0.0,
                precipitation_probability_pct=0.0,
                wind_speed_max_kmh=10.0,
                spoilage_hazard_level="LOW",
                transit_advice="Safe.",
            ),
            provenance=DataProvenance(
                source="Open-Meteo",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="LIVE",
            ),
        )

        async def mock_query(destination, query_msg, response_type, specialist_name, ctx=None, timeout=8.0):
            if destination == "agent1qmarket_timeout":
                return None, f"Agent '{specialist_name}' ({destination}) query timed out after 4.0s."
            elif destination == "agent1qweather_ok":
                return mock_weather, None
            return None, "Unexpected"

        with patch(
            "krishibazaar.agents.orchestrator.query_specialist_agent_async",
            side_effect=mock_query,
        ):
            coord = await coordinate_specialists_async(
                crop="onion",
                quantity_quintals=50.0,
                origin_location="Nashik, Maharashtra",
            )

        mandi_res: MandiPriceResult = coord["mandi_result"]
        weather_res: WeatherResult = coord["weather_result"]

        # Market timed out
        assert mandi_res.success is False
        assert "timed out" in mandi_res.error_message.lower()

        # Weather succeeded despite market failure
        assert weather_res.success is True
        assert weather_res.report.spoilage_hazard_level == "LOW"

    finally:
        settings.allow_direct_service_fallback = orig_fallback
        settings.market_agent_address = orig_market
        settings.weather_agent_address = orig_weather


@pytest.mark.asyncio
async def test_orchestrator_handles_specialist_delivery_error():
    """Verify orchestrator reports delivery error when a specialist address is unreachable and fallback is disabled."""
    orig_transport = settings.transport_agent_address
    orig_fallback = settings.allow_direct_service_fallback

    try:
        settings.allow_direct_service_fallback = False
        settings.transport_agent_address = "agent1qunreachable_transport"

        async def mock_query(destination, query_msg, response_type, specialist_name, ctx=None, timeout=8.0):
            if destination == "agent1qunreachable_transport":
                return None, f"Agent '{specialist_name}' ({destination}) communication failed: Failed to resolve destination address"
            return None, "Unexpected"

        with patch(
            "krishibazaar.agents.orchestrator.query_specialist_agent_async",
            side_effect=mock_query,
        ):
            coord = await coordinate_specialists_async(
                crop="onion",
                quantity_quintals=50.0,
                origin_location="Nashik, Maharashtra",
            )

        transport_res: TransportQuotesResult = coord["transport_result"]
        assert transport_res.success is False
        assert "communication failed" in transport_res.error_message.lower()

    finally:
        settings.allow_direct_service_fallback = orig_fallback
        settings.transport_agent_address = orig_transport


@pytest.mark.asyncio
async def test_handle_chat_message_passes_ctx_and_dispatches_to_configured_addresses():
    """Verify handle_chat_message passes ctx to coordination and sends requests to configured agent addresses."""
    orig_market = settings.market_agent_address
    orig_weather = settings.weather_agent_address
    orig_buyer = settings.buyer_agent_address
    orig_transport = settings.transport_agent_address

    try:
        settings.market_agent_address = "agent1qmarket_chat_test"
        settings.weather_agent_address = "agent1qweather_chat_test"
        settings.buyer_agent_address = "agent1qbuyer_chat_test"
        settings.transport_agent_address = "agent1qtransport_chat_test"

        mock_ctx = MagicMock()
        mock_ctx.send = AsyncMock()
        mock_ctx.logger = MagicMock()

        mock_intent = FarmerIntent(
            crop="onion",
            quantity_quintals=50.0,
            origin_location="Nashik, Maharashtra",
            minimum_net_return_inr=85000.0,
            selling_deadline="tomorrow",
            is_complete=True,
        )

        mock_mandi = MandiPriceResult(
            success=True,
            commodity="Onion",
            records=[],
            provenance=DataProvenance(
                source="data.gov.in (AGMARKNET)",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="LIVE_NO_RECORDS",
            ),
        )

        mock_weather = WeatherResult(
            success=True,
            location="Nashik",
            report=WeatherRiskReport(
                location="Nashik",
                forecast_date="2026-10-19",
                temperature_max_c=30.0,
                temperature_min_c=20.0,
                precipitation_sum_mm=0.0,
                precipitation_probability_pct=0.0,
                wind_speed_max_kmh=10.0,
                spoilage_hazard_level="LOW",
                transit_advice="Safe.",
            ),
            provenance=DataProvenance(
                source="Open-Meteo",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-19",
                freshness_status="LIVE",
            ),
        )

        mock_buyer = BuyerOffersResult(
            success=True,
            crop="Onion",
            quantity_quintals=50.0,
            offers=[],
            provenance=DataProvenance(
                source="Buyer Network",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="SIMULATED_SAMPLE",
            ),
        )

        mock_transport = TransportQuotesResult(
            success=True,
            origin="Nashik",
            quantity_quintals=50.0,
            quotes={},
            provenance=DataProvenance(
                source="Simulated Logistics Network",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="SIMULATED_SAMPLE",
            ),
        )

        dispatched_destinations = []

        async def mock_query(destination, query_msg, response_type, specialist_name, ctx=None, timeout=8.0):
            assert ctx == mock_ctx, "Context must be passed to query_specialist_agent_async"
            dispatched_destinations.append(destination)
            if destination == "agent1qmarket_chat_test":
                return mock_mandi, None
            elif destination == "agent1qweather_chat_test":
                return mock_weather, None
            elif destination == "agent1qbuyer_chat_test":
                return mock_buyer, None
            elif destination == "agent1qtransport_chat_test":
                return mock_transport, None
            return None, "Unexpected"

        with patch(
            "krishibazaar.agents.orchestrator.extract_farmer_intent_async",
            new=AsyncMock(return_value=mock_intent),
        ), patch(
            "krishibazaar.agents.orchestrator.query_specialist_agent_async",
            side_effect=mock_query,
        ):
            chat_msg = ChatMessage(
                content=[TextContent(text="I have 50 quintals of onions in Nashik, want 85000 by tomorrow")]
            )
            await handle_chat_message(mock_ctx, "agent1farmer", chat_msg)

        # Assert acknowledgement and response were sent
        assert mock_ctx.send.await_count == 2
        ack_call = mock_ctx.send.call_args_list[0]
        assert isinstance(ack_call[0][1], ChatAcknowledgement)

        resp_call = mock_ctx.send.call_args_list[1]
        assert isinstance(resp_call[0][1], ChatMessage)

        # Assert queries were dispatched to configured addresses
        assert "agent1qmarket_chat_test" in dispatched_destinations
        assert "agent1qweather_chat_test" in dispatched_destinations
        assert "agent1qbuyer_chat_test" in dispatched_destinations
        assert "agent1qtransport_chat_test" in dispatched_destinations

    finally:
        settings.market_agent_address = orig_market
        settings.weather_agent_address = orig_weather
        settings.buyer_agent_address = orig_buyer
        settings.transport_agent_address = orig_transport


@pytest.mark.asyncio
async def test_query_specialist_agent_async_invalid_reply_type():
    """Verify query_specialist_agent_async rejects reply with unexpected type."""
    mock_ctx = MagicMock()
    mock_ctx.send_and_receive = AsyncMock()

    # Specialist returns a TransportQuotesResult when MandiPriceResult was expected
    unexpected_reply = TransportQuotesResult(
        success=True,
        origin="Pune",
        quantity_quintals=50.0,
        quotes={},
        provenance=DataProvenance(
            source="Logistics Service",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="SIMULATED_SAMPLE",
        ),
    )
    mock_ctx.send_and_receive.return_value = (unexpected_reply, None)

    res, err = await query_specialist_agent_async(
        destination="agent1qmarket_invalid_test",
        query_msg=MarketQuery(commodity="Onion", district="Pune", state="Maharashtra"),
        response_type=MandiPriceResult,
        specialist_name="Market Specialist",
        ctx=mock_ctx,
        timeout=3.0,
    )
    assert res is None
    assert err is not None
    assert "unexpected response: TransportQuotesResult" in err


@pytest.mark.asyncio
async def test_coordinate_specialists_fallback_when_specialist_times_out():
    """Verify that when a configured specialist agent times out and fallback is allowed, local fallback is invoked and accurately labeled."""
    orig_weather = settings.weather_agent_address
    orig_fallback = settings.allow_direct_service_fallback

    try:
        settings.weather_agent_address = "agent1qweather_timeout_target"
        settings.allow_direct_service_fallback = True

        async def mock_query(destination, query_msg, response_type, specialist_name, ctx=None, timeout=8.0):
            if destination == "agent1qweather_timeout_target":
                return None, f"Agent '{specialist_name}' ({destination}) query timed out after 3.0s."
            return None, "Unexpected"

        mock_fallback_weather = WeatherResult(
            success=True,
            location="Nashik",
            report=WeatherRiskReport(
                location="Nashik",
                forecast_date="2026-10-18",
                temperature_max_c=31.0,
                temperature_min_c=19.0,
                precipitation_sum_mm=0.0,
                precipitation_probability_pct=0.0,
                wind_speed_max_kmh=10.0,
                spoilage_hazard_level="LOW",
                transit_advice="Safe.",
            ),
            provenance=DataProvenance(
                source="Open-Meteo API",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="LIVE",
            ),
        )

        with patch(
            "krishibazaar.agents.orchestrator.query_specialist_agent_async",
            side_effect=mock_query,
        ), patch(
            "krishibazaar.agents.orchestrator.get_transit_weather_async",
            new=AsyncMock(return_value=mock_fallback_weather),
        ):
            coord = await coordinate_specialists_async(
                crop="onion",
                quantity_quintals=50.0,
                origin_location="Nashik, Maharashtra",
            )

        w_res: WeatherResult = coord["weather_result"]
        assert w_res.success is True
        # Must accurately label provenance source and disclaimer as fallback
        assert "(Local Fallback)" in w_res.provenance.source
        assert "Local service fallback used" in w_res.provenance.disclaimer
        assert "timed out" in w_res.provenance.disclaimer

    finally:
        settings.weather_agent_address = orig_weather
        settings.allow_direct_service_fallback = orig_fallback


@pytest.mark.asyncio
async def test_coordinate_specialists_real_agent_messaging_provenance_labeling():
    """Verify real agent responses are accurately labeled with agent messaging provenance and not as local fallbacks."""
    orig_market = settings.market_agent_address
    orig_transport = settings.transport_agent_address

    try:
        settings.market_agent_address = "agent1qmarket_real"
        settings.transport_agent_address = "agent1qtransport_real"

        mock_mandi = MandiPriceResult(
            success=True,
            commodity="Onion",
            records=[
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
            ],
            provenance=DataProvenance(
                source="data.gov.in (AGMARKNET)",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="LIVE",
                is_live=True,
            ),
        )

        mock_transport = TransportQuotesResult(
            success=True,
            origin="Nashik",
            quantity_quintals=50.0,
            quotes={
                "Lasalgaon APMC": TransportQuote(
                    quote_id="TQ-REAL-01",
                    transporter_id="T-REAL",
                    transporter_name="Real Agent Carrier",
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
                )
            },
            provenance=DataProvenance(
                source="Logistics Agent",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="SIMULATED_SAMPLE",
            ),
        )

        async def mock_query(destination, query_msg, response_type, specialist_name, ctx=None, timeout=8.0):
            if destination == "agent1qmarket_real":
                return mock_mandi, None
            elif destination == "agent1qtransport_real":
                return mock_transport, None
            return None, "Unexpected"

        with patch(
            "krishibazaar.agents.orchestrator.query_specialist_agent_async",
            side_effect=mock_query,
        ):
            coord = await coordinate_specialists_async(
                crop="onion",
                quantity_quintals=50.0,
                origin_location="Nashik, Maharashtra",
            )

        m_res: MandiPriceResult = coord["mandi_result"]
        t_res: TransportQuotesResult = coord["transport_result"]

        # Both must come from agent messaging
        assert m_res.success is True
        assert "via Market Agent [agent1qmarket_real]" in m_res.provenance.source
        assert "(Local Fallback)" not in m_res.provenance.source
        assert "real agent messaging" in m_res.provenance.disclaimer.lower()

        assert t_res.success is True
        assert "via Transport Agent [agent1qtransport_real]" in t_res.provenance.source
        assert "(Local Fallback)" not in t_res.provenance.source
        assert "real agent messaging" in t_res.provenance.disclaimer.lower()

    finally:
        settings.market_agent_address = orig_market
        settings.transport_agent_address = orig_transport


@pytest.mark.asyncio
async def test_buyer_b_timeout_with_buyer_a_partial_success():
    """Verify that when Buyer B times out after 10s, Buyer A offers are kept and disclaimer explicitly records Buyer B timeout."""
    orig_buyer_a = settings.buyer_agent_address
    orig_buyer_b = settings.buyer_b_agent_address
    try:
        settings.buyer_agent_address = "agent1qbuyer_a_addr"
        settings.buyer_b_agent_address = "agent1qbuyer_b_addr"

        mock_buyer_a = BuyerOffersResult(
            success=True,
            crop="Onion",
            quantity_quintals=50.0,
            offers=[
                BuyerOffer(
                    offer_id="BO-A-01",
                    buyer_id="B-01",
                    buyer_name="Agro Buyer A",
                    buyer_type="Wholesale Aggregator",
                    crop="Onion",
                    quantity_quintals=50.0,
                    offered_price_per_quintal=3300.0,
                    farmgate_pickup=True,
                    is_simulated=True,
                )
            ],
            provenance=DataProvenance(
                source="Buyer A",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="SIMULATED_SAMPLE",
            ),
        )

        async def mock_query(destination, query_msg, response_type, specialist_name, ctx=None, timeout=None):
            if destination == "agent1qbuyer_a_addr":
                return mock_buyer_a, None
            elif destination == "agent1qbuyer_b_addr":
                return None, f"Agent '{specialist_name}' ({destination}) query timed out after 10.0s."
            return None, "Skipped"

        with patch(
            "krishibazaar.agents.orchestrator.query_specialist_agent_async",
            side_effect=mock_query,
        ):
            coord = await coordinate_specialists_async(
                crop="onion",
                quantity_quintals=50.0,
                origin_location="Nashik",
            )

        b_res: BuyerOffersResult = coord["buyer_result"]
        assert b_res.success is True
        assert len(b_res.offers) == 1
        assert b_res.offers[0].buyer_name == "Agro Buyer A"
        assert "Buyer B" in b_res.error_message
        assert "timed out" in b_res.error_message
        assert "Buyer A" in b_res.provenance.source
        assert "Buyer B failed" in b_res.provenance.disclaimer
    finally:
        settings.buyer_agent_address = orig_buyer_a
        settings.buyer_b_agent_address = orig_buyer_b


@pytest.mark.asyncio
async def test_transport_resolution_failure_with_explicit_local_fallback():
    """Verify that when Transport address resolution fails (Almanac API timeout + contract resolution error), fallback is explicitly labeled."""
    orig_transport = settings.transport_agent_address
    orig_fallback = settings.allow_direct_service_fallback
    try:
        settings.transport_agent_address = "agent1qtransport_resolution_fail"
        settings.allow_direct_service_fallback = True

        resolution_err = (
            "Agent 'Transport Specialist' (agent1qtransport_resolution_fail) communication failed: "
            "Almanac API lookup timed out, followed by an Almanac contract resolution error"
        )

        async def mock_query(destination, query_msg, response_type, specialist_name, ctx=None, timeout=None):
            if destination == "agent1qtransport_resolution_fail":
                return None, resolution_err
            return None, "Skipped"

        mock_fallback_transport = TransportQuotesResult(
            success=True,
            origin="Nashik",
            quantity_quintals=50.0,
            quotes={
                "Mandi 1": TransportQuote(
                    quote_id="TQ-FB-1",
                    transporter_id="T-FB",
                    transporter_name="Fallback Transporter",
                    vehicle_type="Truck",
                    capacity_quintals=50.0,
                    route_origin="Nashik",
                    route_destination="Mandi 1",
                    distance_km=30.0,
                    base_fare=500.0,
                    rate_per_km=15.0,
                    total_transport_cost=950.0,
                    estimated_transit_hours=1.0,
                    is_simulated=True,
                )
            },
            provenance=DataProvenance(
                source="Simulated Logistics Network",
                retrieval_timestamp_utc="2026-10-18T10:00:00Z",
                observation_date="2026-10-18",
                freshness_status="SIMULATED_SAMPLE",
            ),
        )

        with patch(
            "krishibazaar.agents.orchestrator.query_specialist_agent_async",
            side_effect=mock_query,
        ), patch(
            "krishibazaar.agents.orchestrator.get_transport_quotes_async",
            new=AsyncMock(return_value=mock_fallback_transport),
        ):
            coord = await coordinate_specialists_async(
                crop="onion",
                quantity_quintals=50.0,
                origin_location="Nashik",
            )

        t_res: TransportQuotesResult = coord["transport_result"]
        assert t_res.success is True
        assert "(Local Fallback)" in t_res.provenance.source
        assert "Local service fallback used" in t_res.provenance.disclaimer
        assert "Almanac" in t_res.provenance.disclaimer
    finally:
        settings.transport_agent_address = orig_transport
        settings.allow_direct_service_fallback = orig_fallback


@pytest.mark.asyncio
async def test_agentverse_503_retry_succeeds():
    """Verify bounded retry when Agentverse mailbox returns HTTP 503."""
    mock_ctx = MagicMock()
    mock_ctx.send_and_receive = AsyncMock()

    mock_status_503 = MagicMock()
    mock_status_503.detail = "HTTP 503: Service Unavailable from Agentverse mailbox"

    mock_result = MandiPriceResult(
        success=True,
        commodity="Onion",
        records=[],
        provenance=DataProvenance(
            source="data.gov.in",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE",
        ),
    )
    mock_status_200 = MagicMock()
    mock_status_200.detail = "Delivered"

    # Attempt 1 returns (None, 503), Attempt 2 returns (mock_result, 200)
    mock_ctx.send_and_receive.side_effect = [
        (None, mock_status_503),
        (mock_result, mock_status_200),
    ]

    res, err = await query_specialist_agent_async(
        destination="agent1qmarket_test_503",
        query_msg=MarketQuery(commodity="Onion", district="Nashik", state="Maharashtra"),
        response_type=MandiPriceResult,
        specialist_name="Market Specialist",
        ctx=mock_ctx,
        timeout=5.0,
        backoff_seconds=0.01,
    )

    assert mock_ctx.send_and_receive.await_count == 2
    assert res == mock_result
    assert err is None


@pytest.mark.asyncio
async def test_agentverse_522_persistent_failure():
    """Verify persistent HTTP 522 returns clean error after bounded retry."""
    mock_ctx = MagicMock()
    mock_ctx.send_and_receive = AsyncMock()

    mock_status_522 = MagicMock()
    mock_status_522.detail = "HTTP 522: Connection timed out to Agentverse mailbox"

    # Both attempts fail with 522
    mock_ctx.send_and_receive.return_value = (None, mock_status_522)

    res, err = await query_specialist_agent_async(
        destination="agent1qmarket_test_522",
        query_msg=MarketQuery(commodity="Onion", district="Nashik", state="Maharashtra"),
        response_type=MandiPriceResult,
        specialist_name="Market Specialist",
        ctx=mock_ctx,
        timeout=5.0,
        backoff_seconds=0.01,
    )

    assert mock_ctx.send_and_receive.await_count == 2
    assert res is None
    assert "522" in err


@pytest.mark.asyncio
async def test_krishibazaar_resolver_resolves_known_specialists_immediately():
    """Verify KrishiBazaarResolver directly maps configured agents without Almanac API lookup."""
    from krishibazaar.agents.resolver import KrishiBazaarResolver

    mock_fallback = MagicMock()
    mock_fallback.resolve = AsyncMock()

    resolver = KrishiBazaarResolver(fallback_resolver=mock_fallback)
    addr = settings.transport_agent_address or "agent1q0he7rn2yyh7lkswt37hdddfaf7vjcu4vtvyjd2npte07vw6dnygk7tjl8j"
    orig_transport = settings.transport_agent_address
    try:
        settings.transport_agent_address = addr

        resolved_addr, endpoints = await resolver.resolve(addr)

        assert resolved_addr == addr
        assert len(endpoints) == 2
        assert f"http://127.0.0.1:{settings.transport_agent_port}/submit" == endpoints[0]
        assert "https://agentverse.ai/v2/agents/mailbox/submit" in endpoints[1]
        # Fallback Almanac resolver was NOT touched for known specialist
        mock_fallback.resolve.assert_not_called()
    finally:
        settings.transport_agent_address = orig_transport


# ==============================================================================
# Comprehensive Schema Compatibility & Live Inter-Agent Communication Tests
# ==============================================================================

@pytest.mark.parametrize(
    "query_cls,result_cls,query_kwargs,result_kwargs",
    [
        (
            MarketQuery,
            MandiPriceResult,
            {"commodity": "Onion", "district": "Nashik", "state": "Maharashtra"},
            {
                "success": True,
                "commodity": "Onion",
                "records": [],
                "provenance": DataProvenance(
                    source="AGMARKNET",
                    retrieval_timestamp_utc="2026-10-20T00:00:00Z",
                    observation_date="2026-10-20",
                    freshness_status="LIVE",
                    is_live=True,
                ),
            },
        ),
        (
            WeatherQuery,
            WeatherResult,
            {"location": "Nashik", "target_date": "2026-10-21"},
            {
                "success": True,
                "location": "Nashik",
                "report": None,
                "provenance": DataProvenance(
                    source="Open-Meteo",
                    retrieval_timestamp_utc="2026-10-20T00:00:00Z",
                    observation_date="2026-10-20",
                    freshness_status="LIVE",
                    is_live=True,
                ),
            },
        ),
        (
            BuyerQuery,
            BuyerOffersResult,
            {"crop": "Onion", "quantity_quintals": 50.0, "origin_location": "Nashik"},
            {
                "success": True,
                "crop": "Onion",
                "quantity_quintals": 50.0,
                "offers": [],
                "provenance": DataProvenance(
                    source="Buyer Network",
                    retrieval_timestamp_utc="2026-10-20T00:00:00Z",
                    observation_date="2026-10-20",
                    freshness_status="SIMULATED_SAMPLE",
                    is_live=False,
                ),
            },
        ),
        (
            TransportQuery,
            TransportQuotesResult,
            {"origin": "Nashik", "destinations": ["Lasalgaon Mandi"], "quantity_quintals": 50.0},
            {
                "success": True,
                "origin": "Nashik",
                "quantity_quintals": 50.0,
                "quotes": {},
                "provenance": DataProvenance(
                    source="Logistics Network",
                    retrieval_timestamp_utc="2026-10-20T00:00:00Z",
                    observation_date="2026-10-20",
                    freshness_status="SIMULATED_SAMPLE",
                    is_live=False,
                ),
            },
        ),
    ],
)
def test_all_specialist_message_models_schema_digests_and_serialization(
    query_cls, result_cls, query_kwargs, result_kwargs
):
    """Verify schema digest computation, serialization roundtrip, and request_id propagation for all specialist pairs."""
    from uagents import Model

    test_req_id = f"REQ-TEST-{uuid4().hex[:6].upper()}"

    # 1. Query model
    query_obj = query_cls(request_id=test_req_id, **query_kwargs)
    q_digest = Model.build_schema_digest(query_cls)
    assert q_digest.startswith("model:"), f"Invalid schema digest: {q_digest}"

    q_json = query_obj.model_dump_json()
    q_restored = query_cls.model_validate_json(q_json)
    assert q_restored.request_id == test_req_id

    # 2. Result model
    result_obj = result_cls(request_id=test_req_id, **result_kwargs)
    r_digest = Model.build_schema_digest(result_cls)
    assert r_digest.startswith("model:"), f"Invalid schema digest: {r_digest}"

    r_json = result_obj.model_dump_json()
    r_restored = result_cls.model_validate_json(r_json)
    assert r_restored.request_id == test_req_id
    assert r_restored.success is True


def test_orchestrator_agent_models_registry_includes_all_specialist_schemas():
    """Verify Orchestrator agent registers all specialist response models so no unrecognized digest warnings occur."""
    from uagents import Model
    from krishibazaar.agents.orchestrator import orchestrator_agent

    expected_models = [
        MandiPriceResult,
        WeatherResult,
        BuyerOffersResult,
        TransportQuotesResult,
    ]

    for model_cls in expected_models:
        digest = Model.build_schema_digest(model_cls)
        assert digest in orchestrator_agent._models, (
            f"Schema digest '{digest}' for {model_cls.__name__} not registered in Orchestrator agent._models. "
            f"Registered models: {list(orchestrator_agent._models.keys())}"
        )
        assert orchestrator_agent._models[digest] == model_cls


@pytest.mark.asyncio
async def test_specialist_agent_handlers_receive_query_and_reply_exact_types():
    """Verify each specialist handler receives its query, propagates request_id, and replies with exact expected type."""
    from krishibazaar.agents.market_agent import handle_market_query
    from krishibazaar.agents.weather_agent import handle_weather_query
    from krishibazaar.agents.buyer_agent import handle_buyer_query
    from krishibazaar.agents.transport_agent import handle_transport_query

    test_req_id = "REQ-TRACE-9999"

    # 1. Market Agent Handler
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    m_query = MarketQuery(commodity="Onion", district="Nashik", state="Maharashtra", request_id=test_req_id)
    await handle_market_query(mock_ctx, "agent1q_orchestrator", m_query)
    mock_ctx.send.assert_awaited_once()
    m_reply = mock_ctx.send.await_args[0][1]
    assert isinstance(m_reply, MandiPriceResult)
    assert m_reply.request_id == test_req_id

    # 2. Weather Agent Handler
    mock_ctx.reset_mock()
    w_query = WeatherQuery(location="Nashik", target_date="2026-10-21", request_id=test_req_id)
    await handle_weather_query(mock_ctx, "agent1q_orchestrator", w_query)
    mock_ctx.send.assert_awaited_once()
    w_reply = mock_ctx.send.await_args[0][1]
    assert isinstance(w_reply, WeatherResult)
    assert w_reply.request_id == test_req_id

    # 3. Buyer Agent Handler (Profile A)
    mock_ctx.reset_mock()
    ba_query = BuyerQuery(crop="Onion", quantity_quintals=50.0, origin_location="Nashik", request_id=test_req_id)
    await handle_buyer_query(mock_ctx, "agent1q_orchestrator", ba_query, profile="buyer_a")
    mock_ctx.send.assert_awaited_once()
    ba_reply = mock_ctx.send.await_args[0][1]
    assert isinstance(ba_reply, BuyerOffersResult)
    assert ba_reply.request_id == test_req_id

    # 4. Buyer Agent Handler (Profile B)
    mock_ctx.reset_mock()
    bb_query = BuyerQuery(crop="Onion", quantity_quintals=50.0, origin_location="Nashik", request_id=test_req_id)
    await handle_buyer_query(mock_ctx, "agent1q_orchestrator", bb_query, profile="buyer_b")
    mock_ctx.send.assert_awaited_once()
    bb_reply = mock_ctx.send.await_args[0][1]
    assert isinstance(bb_reply, BuyerOffersResult)
    assert bb_reply.request_id == test_req_id

    # 5. Transport Agent Handler
    mock_ctx.reset_mock()
    t_query = TransportQuery(
        origin="Nashik",
        destinations=["Lasalgaon Mandi"],
        quantity_quintals=50.0,
        weather_hazard_level="LOW",
        request_id=test_req_id,
    )
    await handle_transport_query(mock_ctx, "agent1q_orchestrator", t_query)
    mock_ctx.send.assert_awaited_once()
    t_reply = mock_ctx.send.await_args[0][1]
    assert isinstance(t_reply, TransportQuotesResult)
    assert t_reply.request_id == test_req_id


@pytest.mark.asyncio
async def test_live_in_memory_inter_agent_communication_orchestrator_to_specialists():
    """
    Integration test verifying real async message passing between Orchestrator and each specialist agent
    using uAgents in-memory local dispatch (no network or Agentverse dependency).
    """
    import asyncio
    from krishibazaar.agents.orchestrator import create_orchestrator_agent
    from krishibazaar.agents.market_agent import create_market_agent
    from krishibazaar.agents.weather_agent import create_weather_agent
    from krishibazaar.agents.buyer_agent import create_buyer_agent
    from krishibazaar.agents.transport_agent import create_transport_agent

    from uagents.crypto import Identity
    from krishibazaar.agents.resolver import KrishiBazaarResolver

    orch_addr = Identity.from_seed("seed_orch_test_seed_12345", 0).address
    market_addr = Identity.from_seed("seed_market_test_seed_12345", 0).address
    weather_addr = Identity.from_seed("seed_weather_test_seed_12345", 0).address
    buyer_addr = Identity.from_seed("seed_buyer_test_seed_12345", 0).address
    transport_addr = Identity.from_seed("seed_transport_test_seed_12345", 0).address

    custom_map = {
        orch_addr: ["http://127.0.0.1:8000/submit"],
        market_addr: ["http://127.0.0.1:8001/submit"],
        weather_addr: ["http://127.0.0.1:8002/submit"],
        buyer_addr: ["http://127.0.0.1:8003/submit"],
        transport_addr: ["http://127.0.0.1:8005/submit"],
    }
    shared_resolver = KrishiBazaarResolver(custom_endpoints=custom_map)

    # Create distinct in-memory agents
    orch = create_orchestrator_agent(name="test_orch", seed="seed_orch_test_seed_12345", mailbox=False, publish_agent_details=False, resolve=shared_resolver)
    market = create_market_agent(name="test_market", seed="seed_market_test_seed_12345", mailbox=False, publish_agent_details=False, resolve=shared_resolver)
    weather = create_weather_agent(name="test_weather", seed="seed_weather_test_seed_12345", mailbox=False, publish_agent_details=False, resolve=shared_resolver)
    buyer = create_buyer_agent(name="test_buyer", seed="seed_buyer_test_seed_12345", mailbox=False, publish_agent_details=False, resolve=shared_resolver)
    transport = create_transport_agent(name="test_transport", seed="seed_transport_test_seed_12345", mailbox=False, publish_agent_details=False, resolve=shared_resolver)

    # Message queue background worker for all 4 specialists
    async def process_agent_queue(agent_obj):
        while True:
            try:
                msg = await agent_obj._message_queue.get()
                schema_digest, sender, message, session = msg
                await agent_obj._process_single_message(schema_digest, sender, message, session)
                agent_obj._message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    tasks = [
        asyncio.create_task(process_agent_queue(market)),
        asyncio.create_task(process_agent_queue(weather)),
        asyncio.create_task(process_agent_queue(buyer)),
        asyncio.create_task(process_agent_queue(transport)),
    ]

    try:
        ctx = orch._build_context()

        # 1. Real Market Specialist Query
        m_res, m_err = await query_specialist_agent_async(
            destination=market.address,
            query_msg=MarketQuery(commodity="Onion", district="Nashik", state="Maharashtra"),
            response_type=MandiPriceResult,
            specialist_name="Market Specialist",
            ctx=ctx,
            timeout=20.0,
        )
        assert m_err is None
        assert isinstance(m_res, MandiPriceResult)
        assert m_res.success is True
        assert m_res.provenance is not None

        # 2. Real Weather Specialist Query
        w_res, w_err = await query_specialist_agent_async(
            destination=weather.address,
            query_msg=WeatherQuery(location="Nashik", target_date="2026-10-21"),
            response_type=WeatherResult,
            specialist_name="Weather Specialist",
            ctx=ctx,
            timeout=20.0,
        )
        assert w_err is None
        assert isinstance(w_res, WeatherResult)
        assert w_res.success is True

        # 3. Real Buyer Specialist Query
        b_res, b_err = await query_specialist_agent_async(
            destination=buyer.address,
            query_msg=BuyerQuery(crop="Onion", quantity_quintals=50.0, origin_location="Nashik"),
            response_type=BuyerOffersResult,
            specialist_name="Buyer Specialist",
            ctx=ctx,
            timeout=20.0,
        )
        assert b_err is None
        assert isinstance(b_res, BuyerOffersResult)
        assert b_res.success is True
        assert len(b_res.offers) > 0

        # 4. Real Transport Specialist Query
        t_res, t_err = await query_specialist_agent_async(
            destination=transport.address,
            query_msg=TransportQuery(
                origin="Nashik",
                destinations=["Lasalgaon Mandi"],
                quantity_quintals=50.0,
            ),
            response_type=TransportQuotesResult,
            specialist_name="Transport Specialist",
            ctx=ctx,
            timeout=20.0,
        )
        assert t_err is None
        assert isinstance(t_res, TransportQuotesResult)
        assert t_res.success is True
        assert len(t_res.quotes) > 0

    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_inter_agent_protocol_metadata_and_envelope_digests():
    """
    Asserts protocol metadata across all KrishiBazaar agents:
    - Protocol digests for Market, Weather, Buyer A, Buyer B, and Transport protocols
    - Message schema digests for queries and replies
    - Schema registration inside Orchestrator specialist_response_proto
    - Envelope serialization and protocol metadata properties
    """
    from uagents import Model
    from krishibazaar.agents.market_agent import market_proto
    from krishibazaar.agents.weather_agent import weather_proto
    from krishibazaar.agents.buyer_agent import buyer_proto
    from krishibazaar.agents.buyer_b_agent import buyer_b_proto
    from krishibazaar.agents.transport_agent import transport_proto
    from krishibazaar.agents.orchestrator import specialist_response_proto, chat_proto

    # 1. Assert protocol digests exist and have expected 'proto:' prefix
    for name, proto in [
        ("Market", market_proto),
        ("Weather", weather_proto),
        ("Buyer A", buyer_proto),
        ("Buyer B", buyer_b_proto),
        ("Transport", transport_proto),
        ("Orchestrator Specialist Responses", specialist_response_proto),
        ("Orchestrator ACP Chat", chat_proto),
    ]:
        assert proto.digest is not None, f"{name} protocol digest must not be None"
        assert proto.digest.startswith("proto:"), f"{name} protocol digest must start with 'proto:', got {proto.digest}"

    # 2. Assert all query model schema digests match expected schema format
    query_models = [MarketQuery, WeatherQuery, BuyerQuery, TransportQuery]
    for model_cls in query_models:
        digest = Model.build_schema_digest(model_cls)
        assert digest.startswith("model:"), f"Schema digest for {model_cls.__name__} must start with 'model:'"

    # 3. Assert all response model schema digests match expected schema format
    response_models = [MandiPriceResult, WeatherResult, BuyerOffersResult, TransportQuotesResult]
    for model_cls in response_models:
        digest = Model.build_schema_digest(model_cls)
        assert digest.startswith("model:"), f"Schema digest for {model_cls.__name__} must start with 'model:'"

    # 4. Assert Orchestrator specialist_response_proto has registered handlers for all 4 response models
    expected_response_digests = {Model.build_schema_digest(m) for m in response_models}
    registered_handlers = specialist_response_proto.signed_message_handlers | specialist_response_proto.unsigned_message_handlers
    for digest in expected_response_digests:
        assert digest in registered_handlers, f"Schema digest {digest} must be registered in Orchestrator specialist response protocol"


@pytest.mark.asyncio
async def test_real_multiprocess_http_agent_to_agent_communication_all_specialists():
    """
    REAL MULTI-PROCESS HTTP INTEGRATION TEST (distinct from in-memory tests).
    Connects to independently running specialist agent processes on ports 8001-8005 over HTTP /submit.
    Verifies that:
    1. Outbound envelope is delivered via real HTTP POST with x-uagents-connection: sync
    2. Real external specialist processes execute their respective message handlers
    3. Exactly typed Pydantic responses are returned synchronously over the HTTP connection
    4. Outbound request IDs are strictly preserved and correlated in each response
    """
    import socket
    from uagents.communication import send_sync_message
    from krishibazaar.agents.resolver import get_krishibazaar_resolver

    resolver = get_krishibazaar_resolver()
    ports_map = {
        "market": (settings.market_agent_address, settings.market_agent_port, MarketQuery(commodity="Wheat", district="Alwar", state="Rajasthan"), MandiPriceResult),
        "weather": (settings.weather_agent_address, settings.weather_agent_port, WeatherQuery(location="Alwar, Rajasthan", target_date="2026-10-21"), WeatherResult),
        "buyer_a": (settings.buyer_agent_address, settings.buyer_agent_port, BuyerQuery(crop="Wheat", quantity_quintals=50.0, origin_location="Alwar, Rajasthan"), BuyerOffersResult),
        "buyer_b": (settings.buyer_b_agent_address, settings.buyer_b_agent_port, BuyerQuery(crop="Wheat", quantity_quintals=50.0, origin_location="Alwar, Rajasthan"), BuyerOffersResult),
        "transport": (settings.transport_agent_address, settings.transport_agent_port, TransportQuery(origin="Alwar, Rajasthan", destinations=["Jaipur, Rajasthan"], quantity_quintals=50.0, crop="Wheat"), TransportQuotesResult),
    }

    # Verify if real agent processes are listening
    closed_ports = []
    for name, (_, port, _, _) in ports_map.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            closed_ports.append(f"{name}:{port}")
        s.close()

    if closed_ports:
        pytest.skip(
            f"Real multi-process HTTP test skipped: Agent ports {closed_ports} not active. "
            "Start all six agent processes under .venv313 to run live multi-process verification."
        )

    # 1. Market Specialist
    req_m = "REQ-LIVE-HTTP-MARKET-01"
    query_m = MarketQuery(commodity="Wheat", district="Alwar", state="Rajasthan", request_id=req_m)
    res_m = await send_sync_message(
        destination=settings.market_agent_address,
        message=query_m,
        response_type=MandiPriceResult,
        resolver=resolver,
        timeout=20,
    )
    assert isinstance(res_m, MandiPriceResult), f"Expected MandiPriceResult, got {type(res_m)}"
    assert res_m.success is True
    assert res_m.request_id == req_m

    # 2. Weather Specialist
    req_w = "REQ-LIVE-HTTP-WEATHER-01"
    query_w = WeatherQuery(location="Alwar, Rajasthan", target_date="2026-10-21", request_id=req_w)
    res_w = await send_sync_message(
        destination=settings.weather_agent_address,
        message=query_w,
        response_type=WeatherResult,
        resolver=resolver,
        timeout=10,
    )
    assert isinstance(res_w, WeatherResult), f"Expected WeatherResult, got {type(res_w)}"
    assert res_w.success is True
    assert res_w.request_id == req_w

    # 3. Buyer A Specialist
    req_ba = "REQ-LIVE-HTTP-BUYERA-01"
    query_ba = BuyerQuery(crop="Wheat", quantity_quintals=50.0, origin_location="Alwar, Rajasthan", request_id=req_ba)
    res_ba = await send_sync_message(
        destination=settings.buyer_agent_address,
        message=query_ba,
        response_type=BuyerOffersResult,
        resolver=resolver,
        timeout=10,
    )
    assert isinstance(res_ba, BuyerOffersResult), f"Expected BuyerOffersResult, got {type(res_ba)}"
    assert res_ba.success is True
    assert len(res_ba.offers) > 0
    assert res_ba.request_id == req_ba

    # 4. Buyer B Specialist
    req_bb = "REQ-LIVE-HTTP-BUYERB-01"
    query_bb = BuyerQuery(crop="Wheat", quantity_quintals=50.0, origin_location="Alwar, Rajasthan", request_id=req_bb)
    res_bb = await send_sync_message(
        destination=settings.buyer_b_agent_address,
        message=query_bb,
        response_type=BuyerOffersResult,
        resolver=resolver,
        timeout=10,
    )
    assert isinstance(res_bb, BuyerOffersResult), f"Expected BuyerOffersResult, got {type(res_bb)}"
    assert res_bb.success is True
    assert len(res_bb.offers) > 0
    assert res_bb.request_id == req_bb

    # 5. Transport Specialist
    req_t = "REQ-LIVE-HTTP-TRANSPORT-01"
    query_t = TransportQuery(
        origin="Alwar, Rajasthan",
        destinations=["Jaipur, Rajasthan"],
        quantity_quintals=50.0,
        crop="Wheat",
        request_id=req_t,
    )
    res_t = await send_sync_message(
        destination=settings.transport_agent_address,
        message=query_t,
        response_type=TransportQuotesResult,
        resolver=resolver,
        timeout=10,
    )
    assert isinstance(res_t, TransportQuotesResult), f"Expected TransportQuotesResult, got {type(res_t)}"
    assert res_t.success is True
    assert len(res_t.quotes) > 0
    assert res_t.request_id == req_t
