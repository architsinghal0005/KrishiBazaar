"""
Unit and Regression Tests for Phase 8: Production Readiness & Hackathon Demo Hardening.
Tests startup diagnostics, configuration validation, specialist communication failure reporting,
data provenance and freshness labeling, and the non-booking smoke test runner.
"""

from __future__ import annotations
import pytest
from unittest.mock import MagicMock, patch

from krishibazaar.config import settings
from krishibazaar.agents.diagnostics import (
    validate_configuration,
    get_agent_diagnostic_info,
    print_startup_banner,
)
from krishibazaar.models import (
    DataProvenance,
    MandiPriceRecord,
    BuyerOffer,
    TransportQuote,
    MandiPriceResult,
    BuyerOffersResult,
    TransportQuotesResult,
    WeatherResult,
)
from krishibazaar.agents.orchestrator import format_simulation_and_safety_notices
from krishibazaar.services.optimizer import optimize_sales_channels
from smoke_test import check_port_listening, run_in_memory_smoke_test, run_live_pipeline_smoke_test


def test_validate_configuration_default_valid():
    """Verify that default settings pass configuration validation with zero issues."""
    issues = validate_configuration()
    assert len(issues) == 0, f"Expected 0 issues in default configuration, got: {issues}"


def test_validate_configuration_detects_port_collision():
    """Verify that port collisions between agents are detected."""
    with patch.object(settings, "market_agent_port", 8000):
        with patch.object(settings, "orchestrator_port", 8000):
            issues = validate_configuration()
            assert any("Port collision" in msg for msg in issues)


def test_validate_configuration_detects_invalid_port_range():
    """Verify that out-of-range ports are flagged."""
    with patch.object(settings, "weather_agent_port", 80):
        issues = validate_configuration()
        assert any("Invalid port for Weather Specialist" in msg for msg in issues)


def test_validate_configuration_detects_invalid_address_format():
    """Verify that invalid uAgents addresses are detected."""
    with patch.object(settings, "market_agent_address", "invalid_address_format"):
        issues = validate_configuration()
        assert any("Invalid uAgents address format" in msg for msg in issues)


def test_get_agent_diagnostic_info_protects_secrets():
    """Verify that diagnostic info excludes private seeds and raw API keys."""
    mock_agent = MagicMock()
    mock_agent.address = "agent1q0rsn3us4d9twt209r2xefsqku4qvgypjtp8xvkjep4tmrvrsldnw4z5az4"

    info = get_agent_diagnostic_info(
        agent_name="KrishiBazaar Market Specialist",
        agent=mock_agent,
        port=8001,
        role="Live APMC Mandi Wholesale Price Intelligence",
        extra_details={"API Status": "Configured"},
    )

    assert info["agent_name"] == "KrishiBazaar Market Specialist"
    assert info["address"] == mock_agent.address
    assert info["port"] == 8001
    assert info["local_endpoint"] == "http://127.0.0.1:8001/submit"
    assert info["secrets_leak_protected"] is True

    # Convert entire dict to string and verify no secret keys or seeds exist
    info_str = str(info)
    assert settings.orchestrator_seed not in info_str
    assert settings.market_agent_seed not in info_str
    if settings.agentverse_api_key:
        assert settings.agentverse_api_key not in info_str
    if settings.asi_one_api_key:
        assert settings.asi_one_api_key not in info_str


def test_print_startup_banner_executes_cleanly(capsys):
    """Verify that print_startup_banner prints without errors."""
    mock_agent = MagicMock()
    mock_agent.address = "agent1q0rsn3us4d9twt209r2xefsqku4qvgypjtp8xvkjep4tmrvrsldnw4z5az4"

    print_startup_banner(
        agent_name="Test Specialist",
        agent=mock_agent,
        port=8099,
        role="Testing Diagnostics",
        extra_details={"Data Source": "Synthetic Testing Engine"},
    )

    captured = capsys.readouterr().out
    assert "KrishiBazaar Agent: Test Specialist [Testing Diagnostics]" in captured
    assert "agent1q0rsn3us4d9twt209r2xefsqku4qvgypjtp8xvkjep4tmrvrsldnw4z5az4" in captured
    assert "8099" in captured
    assert "Synthetic Testing Engine" in captured


def test_format_simulation_and_safety_notices_all_remote_success():
    """Verify that format_simulation_and_safety_notices confirms all remote agents when successful."""
    coord = {
        "specialist_diagnostics": {
            "market": {"agent_role": "Market Specialist", "status": "REMOTE_SUCCESS"},
            "weather": {"agent_role": "Weather Specialist", "status": "REMOTE_SUCCESS"},
            "buyers": {"agent_role": "Buyer Network", "status": "REMOTE_SUCCESS"},
            "transport": {"agent_role": "Transport Specialist", "status": "REMOTE_SUCCESS"},
        }
    }
    notices = format_simulation_and_safety_notices(coord)
    notice_text = "\n".join(notices)

    assert "Multi-Agent Network Verified" in notice_text
    assert "Safety Gate:" in notice_text
    assert "Simulation Notice:" in notice_text


def test_format_simulation_and_safety_notices_reports_failures_by_agent():
    """Verify that communication failures are reported by agent with useful error details."""
    coord = {
        "specialist_diagnostics": {
            "market": {
                "agent_role": "Market Specialist",
                "status": "FALLBACK_USED",
                "error_detail": "Agent query timed out after 10.0s.",
            },
            "weather": {"agent_role": "Weather Specialist", "status": "REMOTE_SUCCESS"},
            "buyers": {"agent_role": "Buyer Network", "status": "REMOTE_SUCCESS"},
            "transport": {
                "agent_role": "Transport Specialist",
                "status": "FALLBACK_USED",
                "error_detail": "Connection refused on port 8004",
            },
        }
    }
    notices = format_simulation_and_safety_notices(coord)
    notice_text = "\n".join(notices)

    assert "Specialist Communication Diagnostics:" in notice_text
    assert "Market Specialist:" in notice_text
    assert "Agent query timed out after 10.0s" in notice_text
    assert "Transport Specialist:" in notice_text
    assert "Connection refused on port 8004" in notice_text
    assert "Remote agent messaging verified for: Weather Specialist, Buyer Network" in notice_text


@pytest.mark.asyncio
async def test_smoke_test_in_memory_validation():
    """Verify that run_in_memory_smoke_test executes successfully and returns True."""
    query = "I have 40 quintals of wheat in Jaipur, looking for Rs 90000 net by tomorrow"
    result = await run_in_memory_smoke_test(query)
    assert result is True


def test_check_port_listening_helper():
    """Verify check_port_listening returns boolean without raising unhandled socket errors."""
    # Testing localhost port 1 (unlikely to be bound)
    res = check_port_listening("127.0.0.1", 1, timeout=0.1)
    assert isinstance(res, bool)


def test_optimizer_uses_live_specialist_responses_ranking_dindori():
    """Verify that when live mandi records (including Dindori Vani @ 4400) and buyer offers (including
    Reliance Fresh @ 3400) are supplied, the optimizer considers all channels and ranks Dindori Vani
    as #1, demonstrating that live specialist responses are not bypassed."""
    mandis = [
        MandiPriceRecord(
            mandi_name="Dindori (Vani)",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-21",
            min_price=3800.0,
            max_price=4600.0,
            modal_price=4400.0,
            data_source="data.gov.in_live",
        ),
        MandiPriceRecord(
            mandi_name="Nashik Mandi",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-21",
            min_price=2000.0,
            max_price=2400.0,
            modal_price=2200.0,
            data_source="data.gov.in_live",
        ),
        MandiPriceRecord(
            mandi_name="Lasalgaon",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-21",
            min_price=3000.0,
            max_price=3500.0,
            modal_price=3200.0,
            data_source="data.gov.in_live",
        ),
        MandiPriceRecord(
            mandi_name="Pimpalgaon",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-21",
            min_price=3100.0,
            max_price=3600.0,
            modal_price=3300.0,
            data_source="data.gov.in_live",
        ),
        MandiPriceRecord(
            mandi_name="Yeola",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-21",
            min_price=2900.0,
            max_price=3400.0,
            modal_price=3150.0,
            data_source="data.gov.in_live",
        ),
        MandiPriceRecord(
            mandi_name="Kalwan",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-21",
            min_price=3500.0,
            max_price=4100.0,
            modal_price=3900.0,
            data_source="data.gov.in_live",
        ),
    ]

    buyer_offers = [
        BuyerOffer(
            offer_id="BO-A-01",
            buyer_id="BUYER-A-01",
            buyer_name="Reliance Fresh Direct Procurement",
            offered_price_per_quintal=3400.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="T+1 Bank Transfer",
            farmgate_pickup=False,
            buyer_type="Retail Chain",
        ),
        BuyerOffer(
            offer_id="BO-A-02",
            buyer_id="BUYER-A-02",
            buyer_name="Sahyadri Farmers Producer Co",
            offered_price_per_quintal=3100.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="Immediate UPI",
            farmgate_pickup=False,
            buyer_type="FPO Aggregator",
        ),
        BuyerOffer(
            offer_id="BO-A-03",
            buyer_id="BUYER-A-03",
            buyer_name="BigBasket B2B Sourcing Hub",
            offered_price_per_quintal=3250.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="T+2 Bank Transfer",
            farmgate_pickup=False,
            buyer_type="E-Commerce",
        ),
        BuyerOffer(
            offer_id="BO-B-01",
            buyer_id="BUYER-B-01",
            buyer_name="Mahindra Agri Fresh Direct",
            offered_price_per_quintal=3350.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="T+2 Direct Deposit",
            farmgate_pickup=False,
            buyer_type="Agri-Corporate",
        ),
        BuyerOffer(
            offer_id="BO-B-02",
            buyer_id="BUYER-B-02",
            buyer_name="ITC e-Choupal Sourcing Hub",
            offered_price_per_quintal=3200.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="Instant Digital Transfer",
            farmgate_pickup=False,
            buyer_type="Corporate Agribusiness",
        ),
        BuyerOffer(
            offer_id="BO-B-03",
            buyer_id="BUYER-B-03",
            buyer_name="DeHaat Farmer Procurement Centre",
            offered_price_per_quintal=3150.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="Same-day IMPS",
            farmgate_pickup=True,
            buyer_type="Agri-Tech Platform",
        ),
    ]

    transport_quotes = {
        "Dindori (Vani)": TransportQuote(
            quote_id="TQ-DINDORI",
            transporter_id="TR-01",
            transporter_name="Nashik Kisan Logistics",
            vehicle_type="407 Tempo (3.5T)",
            capacity_quintals=60.0,
            route_origin="Nashik, Maharashtra",
            route_destination="Dindori (Vani)",
            distance_km=32.0,
            base_fare=2200.0,
            rate_per_km=20.0,
            total_transport_cost=2840.0,
            estimated_transit_hours=1.5,
        ),
        "Reliance Fresh Direct Procurement": TransportQuote(
            quote_id="TQ-RF",
            transporter_id="TR-HUB",
            transporter_name="Nashik Hub Logistics",
            vehicle_type="Tata 407",
            capacity_quintals=50.0,
            route_origin="Nashik, Maharashtra",
            route_destination="Reliance Fresh Direct Procurement",
            distance_km=25.0,
            base_fare=500.0,
            rate_per_km=28.0,
            total_transport_cost=1200.0,
            estimated_transit_hours=1.0,
        ),
    }

    opt = optimize_sales_channels(
        crop="onion",
        quantity_quintals=50.0,
        mandi_records=mandis,
        buyer_offers=buyer_offers,
        transport_quotes=transport_quotes,
        minimum_net_return_inr=85000.0,
    )

    # 6 mandis + 6 buyers = 12 channels evaluated
    assert len(opt.all_options) == 12
    assert opt.recommended_option is not None
    top = opt.recommended_option
    # Dindori (Vani) wins Rank #1 with expected return ~ 213,519.25
    assert top.destination_name == "Dindori (Vani)"
    assert top.rank == 1
    assert top.channel_type == "MANDI"
    assert top.expected_net_return > 210000.0
    assert abs(top.expected_net_return - 212610.0) < 1.0

    # Verify Reliance Fresh is present in the ranked channels at its known price
    rf_channel = next(c for c in opt.all_options if "Reliance Fresh" in c.destination_name)
    assert rf_channel.channel_type == "DIRECT_BUYER"
    assert rf_channel.expected_net_return == 168800.0
    assert rf_channel.rank > 1


def test_in_memory_offline_benchmark_yields_reliance_fresh():
    """Verify that in the offline benchmark fixture with 1 low-price mandi (@ 2200) and 3 buyers,
    the optimizer produces 4 channels with Reliance Fresh as Rank #1 at 168,800 INR."""
    mandis = [
        MandiPriceRecord(
            mandi_name="Nashik Mandi",
            district="Nashik",
            state="Maharashtra",
            commodity="Onion",
            variety="Red",
            arrival_date="2026-10-21",
            min_price=2000.0,
            max_price=2400.0,
            modal_price=2200.0,
            data_source="official_benchmark_cache",
        )
    ]
    buyer_offers = [
        BuyerOffer(
            offer_id="BO-A-01",
            buyer_id="BUYER-A-01",
            buyer_name="Reliance Fresh Direct Procurement",
            offered_price_per_quintal=3400.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="T+1 Bank Transfer",
            farmgate_pickup=False,
            buyer_type="Retail Chain",
        ),
        BuyerOffer(
            offer_id="BO-A-02",
            buyer_id="BUYER-A-02",
            buyer_name="Sahyadri Farmers Producer Co",
            offered_price_per_quintal=3100.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="Immediate UPI",
            farmgate_pickup=False,
            buyer_type="FPO Aggregator",
        ),
        BuyerOffer(
            offer_id="BO-A-03",
            buyer_id="BUYER-A-03",
            buyer_name="BigBasket B2B Sourcing Hub",
            offered_price_per_quintal=3250.0,
            quantity_quintals=50.0,
            crop="onion",
            payment_terms="T+2 Bank Transfer",
            farmgate_pickup=False,
            buyer_type="E-Commerce",
        ),
    ]

    transport_quotes = {
        "Reliance Fresh Direct Procurement": TransportQuote(
            quote_id="TQ-RF-BENCH",
            transporter_id="TR-01",
            transporter_name="Hub Freight",
            vehicle_type="Mini Truck",
            capacity_quintals=50.0,
            route_origin="Nashik",
            route_destination="Reliance Fresh Direct Procurement",
            distance_km=25.0,
            base_fare=500.0,
            rate_per_km=28.0,
            total_transport_cost=1200.0,
            estimated_transit_hours=1.0,
        )
    }

    opt = optimize_sales_channels(
        crop="onion",
        quantity_quintals=50.0,
        mandi_records=mandis,
        buyer_offers=buyer_offers,
        transport_quotes=transport_quotes,
        minimum_net_return_inr=85000.0,
    )

    assert len(opt.all_options) == 4
    top = opt.recommended_option
    assert top is not None
    assert "Reliance Fresh Direct Procurement" in top.destination_name
    assert top.expected_net_return == 168800.0
    assert top.rank == 1


@pytest.mark.asyncio
async def test_run_live_pipeline_smoke_test_execution():
    """Verify that run_live_pipeline_smoke_test executes successfully when given specialist responses."""
    prov = DataProvenance(
        source="data.gov.in (AGMARKNET)",
        retrieval_timestamp_utc="2026-10-21T12:00:00Z",
        observation_date="2026-10-21",
        freshness_status="LIVE",
        is_live=True,
    )
    mock_market = MandiPriceResult(
        success=True,
        commodity="Onion",
        records=[
            MandiPriceRecord(
                mandi_name="Dindori (Vani)",
                district="Nashik",
                state="Maharashtra",
                commodity="Onion",
                variety="Red",
                arrival_date="2026-10-21",
                min_price=3800.0,
                max_price=4600.0,
                modal_price=4400.0,
                data_source="data.gov.in_live",
            )
        ],
        provenance=prov,
    )
    mock_buyer_a = BuyerOffersResult(
        success=True,
        crop="onion",
        offers=[
            BuyerOffer(
                offer_id="BO-01",
                buyer_id="B-01",
                buyer_name="Reliance Fresh",
                offered_price_per_quintal=3400.0,
                quantity_quintals=50.0,
                crop="onion",
                payment_terms="Instant",
                farmgate_pickup=True,
                buyer_type="Retail",
            )
        ],
        provenance=DataProvenance(
            source="Buyer Network A (Institutional / FPO)",
            retrieval_timestamp_utc="2026-10-21T12:00:00Z",
            observation_date="2026-10-21",
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
        ),
    )
    mock_buyer_b = BuyerOffersResult(
        success=True,
        crop="onion",
        offers=[],
        provenance=DataProvenance(
            source="Buyer Network B (Corporate / Agri-Exporters)",
            retrieval_timestamp_utc="2026-10-21T12:00:00Z",
            observation_date="2026-10-21",
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
        ),
    )
    mock_weather = WeatherResult(
        success=True,
        location="Nashik",
        report=None,
        provenance=DataProvenance(
            source="Open-Meteo Weather API",
            retrieval_timestamp_utc="2026-10-21T12:00:00Z",
            observation_date="2026-10-21",
            freshness_status="LIVE",
            is_live=True,
        ),
    )
    mock_transport = TransportQuotesResult(
        success=True,
        origin="Nashik",
        quotes={},
        provenance=DataProvenance(
            source="Transport Fleet Network",
            retrieval_timestamp_utc="2026-10-21T12:00:00Z",
            observation_date="2026-10-21",
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
        ),
    )

    results = {
        "market": mock_market,
        "buyer_a": mock_buyer_a,
        "buyer_b": mock_buyer_b,
        "weather": mock_weather,
        "transport": mock_transport,
    }

    success = await run_live_pipeline_smoke_test("50 quintals of onions in Nashik", results)
    assert success is True
