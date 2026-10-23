"""
Unit tests for KrishiBazaar Specialist Agents (tests/test_specialist_agents.py).
Verifies:
- Agent factory instantiation for Market, Weather, Buyer, and Transport agents
- Deterministic seed configuration and protocol inclusion
- Protocol message handler execution with typed request/response models
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from krishibazaar.agents.buyer_agent import create_buyer_agent, handle_buyer_query
from krishibazaar.agents.buyer_b_agent import create_buyer_b_agent, buyer_b_agent, buyer_b_proto
from krishibazaar.agents.market_agent import create_market_agent, handle_market_query
from krishibazaar.agents.transport_agent import create_transport_agent, handle_transport_query
from krishibazaar.agents.weather_agent import create_weather_agent, handle_weather_query
from krishibazaar.agents.orchestrator import create_orchestrator_agent
from krishibazaar.config import settings
from krishibazaar.models import (
    BuyerOffersResult,
    BuyerQuery,
    DataProvenance,
    MandiPriceResult,
    MarketQuery,
    TransportQuotesResult,
    TransportQuery,
    WeatherQuery,
    WeatherResult,
    WeatherRiskReport,
)


def test_specialist_agents_factory_creation():
    """Verify all specialist agents can be instantiated with valid configuration and protocols."""
    market = create_market_agent(name="test_market", seed="test_market_seed_123", port=8011)
    weather = create_weather_agent(name="test_weather", seed="test_weather_seed_123", port=8012)
    buyer = create_buyer_agent(name="test_buyer", seed="test_buyer_seed_123", port=8013)
    buyer_b = create_buyer_b_agent(name="test_buyer_b", seed="test_buyer_b_seed_123", port=8015)
    transport = create_transport_agent(name="test_transport", seed="test_transport_seed_123", port=8014)

    assert market.name == "test_market"
    assert weather.name == "test_weather"
    assert buyer.name == "test_buyer"
    assert buyer_b.name == "test_buyer_b"
    assert transport.name == "test_transport"

    for agent in (market, weather, buyer, buyer_b, transport):
        assert agent.address.startswith("agent1")
        assert len(agent.protocols) >= 1


@pytest.mark.asyncio
async def test_market_agent_message_handler():
    """Verify market agent responds to MarketQuery with MandiPriceResult."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_result = MandiPriceResult(
        success=True,
        commodity="onion",
        records=[],
        provenance=DataProvenance(
            source="test",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="LIVE_NO_RECORDS",
        ),
    )

    with patch("krishibazaar.agents.market_agent.get_mandi_prices_async", new=AsyncMock(return_value=mock_result)):
        query = MarketQuery(commodity="onion", district="Nashik", state="Maharashtra")
        await handle_market_query(mock_ctx, "agent1sender", query)

        mock_ctx.send.assert_awaited_once_with("agent1sender", mock_result)


@pytest.mark.asyncio
async def test_weather_agent_message_handler():
    """Verify weather agent responds to WeatherQuery with WeatherResult."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_result = WeatherResult(
        success=True,
        location="Nashik",
        report=WeatherRiskReport(
            location="Nashik",
            forecast_date="2026-10-18",
            temperature_max_c=31.0,
            temperature_min_c=19.0,
            precipitation_sum_mm=0.0,
            precipitation_probability_pct=10.0,
            wind_speed_max_kmh=12.0,
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

    with patch("krishibazaar.agents.weather_agent.get_transit_weather_async", new=AsyncMock(return_value=mock_result)):
        query = WeatherQuery(location="Nashik")
        await handle_weather_query(mock_ctx, "agent1sender", query)

        mock_ctx.send.assert_awaited_once_with("agent1sender", mock_result)


@pytest.mark.asyncio
async def test_buyer_agent_message_handler():
    """Verify buyer agent responds to BuyerQuery with BuyerOffersResult."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_result = BuyerOffersResult(
        success=True,
        crop="Onion",
        quantity_quintals=50.0,
        offers=[],
        provenance=DataProvenance(
            source="Simulated Buyer Network",
            retrieval_timestamp_utc="2026-10-18T10:00:00Z",
            observation_date="2026-10-18",
            freshness_status="SIMULATED_SAMPLE",
        ),
    )

    with patch("krishibazaar.agents.buyer_agent.get_buyer_offers_async", new=AsyncMock(return_value=mock_result)):
        query = BuyerQuery(crop="Onion", quantity_quintals=50.0, origin_location="Nashik")
        await handle_buyer_query(mock_ctx, "agent1sender", query)

        mock_ctx.send.assert_awaited_once_with("agent1sender", mock_result)


@pytest.mark.asyncio
async def test_buyer_b_agent_message_handler():
    """Verify Buyer B specialist responds with simulated institutional/export offers and disclaimers."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    query = BuyerQuery(crop="Onion", quantity_quintals=50.0, origin_location="Nashik")
    await handle_buyer_query(mock_ctx, "agent1sender", query, profile="buyer_b")

    mock_ctx.send.assert_awaited_once()
    sent_result = mock_ctx.send.call_args[0][1]
    assert isinstance(sent_result, BuyerOffersResult)
    assert sent_result.success is True
    assert len(sent_result.offers) == 3
    for offer in sent_result.offers:
        assert offer.is_simulated is True
        assert "simulated" in offer.disclaimer.lower()
        assert "buyer b" in offer.disclaimer.lower()
    buyer_names = [o.buyer_name for o in sent_result.offers]
    assert "Kalyani Agro-Exports Pvt Ltd" in buyer_names
    assert "ITC Choupal Saagar Rural Procurement" in buyer_names


@pytest.mark.asyncio
async def test_transport_agent_message_handler():
    """Verify transport agent responds to TransportQuery with TransportQuotesResult."""
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    mock_result = TransportQuotesResult(
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

    with patch("krishibazaar.agents.transport_agent.get_transport_quotes_async", new=AsyncMock(return_value=mock_result)):
        query = TransportQuery(
            origin="Nashik",
            destinations=["Pune"],
            quantity_quintals=50.0,
            weather_hazard_level="LOW",
        )
        await handle_transport_query(mock_ctx, "agent1sender", query)

        mock_ctx.send.assert_awaited_once_with("agent1sender", mock_result)


def test_specialist_agents_mailbox_and_publish_configuration():
    """Verify mailbox and publish_agent_details toggles work consistently across all agents."""
    # 1. Explicit mailbox=True with publish_agent_details=True
    m_true = create_market_agent(
        name="m_true", seed="m_seed_true_111", port=8021, mailbox=True, publish_agent_details=True
    )
    assert m_true._use_mailbox is True
    assert m_true._publish_agent_details is True

    # 2. Explicit mailbox=False with publish_agent_details=False (Local-only dev)
    w_false = create_weather_agent(
        name="w_false", seed="w_seed_false_222", port=8022, mailbox=False, publish_agent_details=False
    )
    assert w_false._use_mailbox is False
    assert w_false._publish_agent_details is False

    # 3. Buyer agent honors mailbox=True
    b_true = create_buyer_agent(name="b_true", seed="b_seed_true_333", port=8023, mailbox=True)
    assert b_true._use_mailbox is True

    # 4. Buyer B agent honors mailbox=True and mailbox=False
    bb_true = create_buyer_b_agent(name="bb_true", seed="bb_seed_true_555", port=8026, mailbox=True)
    assert bb_true._use_mailbox is True
    bb_false = create_buyer_b_agent(name="bb_false", seed="bb_seed_false_666", port=8027, mailbox=False)
    assert bb_false._use_mailbox is False

    # 5. Transport agent honors mailbox=False
    t_false = create_transport_agent(name="t_false", seed="t_seed_false_444", port=8024, mailbox=False)
    assert t_false._use_mailbox is False

    # 6. Fallback to settings.use_mailbox when mailbox is None
    m_default = create_market_agent(name="m_def", seed="m_seed_def_555", port=8025, mailbox=None)
    assert m_default._use_mailbox == settings.use_mailbox


def test_specialist_agents_report_events_configuration():
    """Verify report_events is disabled by default across all 6 agents, and can be overridden."""
    # 1. By default, report_events is False according to settings
    orch = create_orchestrator_agent(name="orch_rep_test", seed="seed_orch_rep_1", port=8030)
    market = create_market_agent(name="m_rep_test", seed="seed_m_rep_1", port=8031)
    weather = create_weather_agent(name="w_rep_test", seed="seed_w_rep_1", port=8032)
    buyer = create_buyer_agent(name="b_rep_test", seed="seed_b_rep_1", port=8033)
    buyer_b = create_buyer_b_agent(name="bb_rep_test", seed="seed_bb_rep_1", port=8035)
    transport = create_transport_agent(name="t_rep_test", seed="seed_t_rep_1", port=8034)

    for agent in [orch, market, weather, buyer, buyer_b, transport]:
        assert agent._report_events is False, f"{agent.name} must have _report_events=False by default"
        assert agent._events_enabled is False, f"{agent.name} must have _events_enabled=False"
        assert agent._events_dispatcher is None, f"{agent.name} must have _events_dispatcher=None"

    # 2. Explicit report_events=True override
    orch_on = create_orchestrator_agent(
        name="orch_rep_on", seed="seed_orch_rep_on", port=8036, report_events=True
    )
    market_on = create_market_agent(
        name="m_rep_on", seed="seed_m_rep_on", port=8037, report_events=True
    )
    buyer_b_on = create_buyer_b_agent(
        name="bb_rep_on", seed="seed_bb_rep_on", port=8038, report_events=True
    )

    assert orch_on._report_events is True
    assert market_on._report_events is True
    assert buyer_b_on._report_events is True


def test_independent_agent_identities_and_ports():
    """Verify all 6 agents (Orchestrator + 5 specialists) have unique seeds, ports, and addresses."""
    # Verify settings have unique seeds and ports
    all_seeds = [
        settings.orchestrator_seed,
        settings.market_agent_seed,
        settings.weather_agent_seed,
        settings.buyer_agent_seed,
        settings.buyer_b_agent_seed,
        settings.transport_agent_seed,
    ]
    assert len(all_seeds) == len(set(all_seeds)), "All agent seeds must be unique"

    all_ports = [
        settings.orchestrator_port,
        settings.market_agent_port,
        settings.weather_agent_port,
        settings.buyer_agent_port,
        settings.buyer_b_agent_port,
        settings.transport_agent_port,
    ]
    assert len(all_ports) == len(set(all_ports)), "All agent ports must be unique"

    # Instantiate all 6 agents using default settings
    orch = create_orchestrator_agent(mailbox=False)
    market = create_market_agent(mailbox=False)
    weather = create_weather_agent(mailbox=False)
    buyer = create_buyer_agent(mailbox=False)
    buyer_b = create_buyer_b_agent(mailbox=False)
    transport = create_transport_agent(mailbox=False)

    agents = [orch, market, weather, buyer, buyer_b, transport]
    names = [a.name for a in agents]
    addresses = [a.address for a in agents]

    assert len(names) == 6
    assert len(set(names)) == 6, f"Agent names must be distinct: {names}"

    assert len(addresses) == 6
    assert len(set(addresses)) == 6, f"Agent addresses must be distinct: {addresses}"

    for addr in addresses:
        assert addr.startswith("agent1"), f"Address '{addr}' must be a valid uAgents address"

    # Ensure no specialist reuses the orchestrator's seed or address
    for spec in (market, weather, buyer, buyer_b, transport):
        assert spec.address != orch.address
        assert spec.name != orch.name

    # Ensure Buyer A and Buyer B are completely distinct
    assert buyer.address != buyer_b.address
    assert buyer.name != buyer_b.name
    assert settings.buyer_agent_port != settings.buyer_b_agent_port
    assert settings.buyer_agent_seed != settings.buyer_b_agent_seed


def test_secrets_not_exposed_in_agent_representation():
    """Verify private seeds and API keys are not exposed in agent representation or description."""
    market = create_market_agent(mailbox=False)
    weather = create_weather_agent(mailbox=False)
    buyer = create_buyer_agent(mailbox=False)
    buyer_b = create_buyer_b_agent(mailbox=False)
    transport = create_transport_agent(mailbox=False)
    orch = create_orchestrator_agent(mailbox=False)

    for agent in (orch, market, weather, buyer, buyer_b, transport):
        desc = agent._description or ""
        if settings.agentverse_api_key:
            assert settings.agentverse_api_key not in desc
            assert settings.agentverse_api_key not in repr(agent)
        assert settings.orchestrator_seed not in desc
        assert settings.buyer_b_agent_seed not in desc
