# -*- coding: utf-8 -*-
"""
End-to-End Regression & Production Readiness Test Suite for KrishiBazaar ASI:One Integration.
(tests/test_asi_one_production_readiness.py)

Covers all 7 requirements:
1. Text Encoding, Mojibake Prevention, ₹ Symbol, Hindi Text, Emojis, and JSON Serialization.
2. Concise and Readable Responses (no duplicate totals, clear itemized deductions).
3. Unmistakable Simulated Booking States (**SIMULATED — NOT A REAL BOOKING**).
4. Discovery Metadata & Natural-Language Intent Parsing.
5. All 7 Agents Architecture, Port Assignments (8000-8006), and Diagnostics.
6. Security and Configuration Review (secrets protection, ENABLE_PAYMENT_PROTOCOL=False).
7. Representative 8 ASI:One Conversations:
   1) Greeting
   2) Incomplete inquiry (missing crop)
   3) Complete onion advisory for Nashik
   4) Target net return (₹) and deadline
   5) Specialist timeout/failure fallback
   6) Simulated booking requiring explicit farmer approval
   7) Duplicate booking retry idempotency
   8) Payment reconciliation mock/fixture without real on-chain transaction
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    TextContent,
)
from uagents import Agent

from krishibazaar.agents.diagnostics import validate_configuration, get_agent_diagnostic_info
from krishibazaar.agents.orchestrator import (
    build_chat_response,
    build_greeting_response,
    build_introduction_response,
    create_orchestrator_agent,
    format_mandi_market_report,
    format_optimization_report,
    format_simulation_and_safety_notices,
    handle_chat_message,
)
from krishibazaar.config import settings
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    BuyerOffersResult,
    DataProvenance,
    FarmerInquiry,
    FarmerIntent,
    MandiPriceRecord,
    MandiPriceResult,
    NetReturnBreakdown,
    OptimizationResult,
    PaymentRecord,
    PaymentStatus,
    TransportQuotesResult,
    WeatherResult,
    WeatherRiskReport,
)
from krishibazaar.services.booking_service import (
    BookingActionType,
    create_booking_proposal,
    execute_simulated_booking,
    format_booking_confirmation,
    format_proposal_summary,
    parse_booking_action,
)
from uagents_core.contrib.protocols.chat.cards import (
    ReviewCardPayload,
    DetailCardPayload,
    extract_card,
)
from krishibazaar.services.card_service import (
    build_booking_confirmed_card,
    build_proposal_review_card,
)
from krishibazaar.services.extraction import (
    KNOWN_CROPS,
    _extract_via_rules,
    extract_farmer_intent_async,
)
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path) -> StorageEngine:
    db_file = tmp_path / "asi_one_production_readiness.db"
    return StorageEngine(db_path=str(db_file))


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


@pytest.fixture
def sample_breakdown() -> NetReturnBreakdown:
    return NetReturnBreakdown(
        scenario_id="scenario_lasalgaon",
        rank=1,
        channel_type="MANDI",
        destination_name="Lasalgaon APMC",
        distance_km=25.0,
        quantity_quintals=50.0,
        offered_price_per_quintal=3200.0,
        gross_value=160000.0,
        transport_cost=4500.0,
        mandi_cess_apmc=2400.0,
        loading_unloading_cost=1250.0,
        storage_cost=0.0,
        estimated_loss_cost=1850.0,
        applicable_market_charges=3650.0,
        total_deductions=10000.0,
        expected_net_return=150000.0,
        net_return_per_quintal=3000.0,
        meets_minimum_condition=True,
        is_feasible=True,
        price_provenance="Lasalgaon APMC benchmark (AGMARKNET)",
        transport_provenance="Simulated 407 Freight",
        charges_provenance="APMC 1.5% cess + labor",
        weather_loss_note="Low transit hazard with tarpaulin protection",
        recommendation_note="Optimal net payout across Nashik district",
    )


# =====================================================================
# 1. Text Encoding, Mojibake Prevention, ₹ Symbol, Hindi & Emojis
# =====================================================================

class TestTextEncodingAndMojibakePrevention:
    """Verify that all strings, responses, and serialization are valid UTF-8 without mojibake."""

    def test_rupee_symbol_and_hindi_crop_extraction(self):
        """Rule-based extractor must parse Indian Rupee symbol (₹) and Devanagari Hindi crop names."""
        # Test ₹ symbol with English
        intent_rupee = _extract_via_rules("I have 50 quintals of onions in Nashik and need ₹85000 net by tomorrow")
        assert intent_rupee.crop == "onion"
        assert intent_rupee.quantity_quintals == 50.0
        assert intent_rupee.origin_location == "Nashik"
        assert intent_rupee.minimum_net_return_inr == 85000.0

        # Test Hindi Devanagari crop aliases
        intent_hindi_onion = _extract_via_rules("मेरे पास 50 क्विंटल प्याज है नासिक में")
        assert intent_hindi_onion.crop == "onion"
        assert intent_hindi_onion.quantity_quintals == 50.0

        intent_hindi_wheat = _extract_via_rules("100 क्विंटल गेहूं इंदौर में बेचना है")
        assert intent_hindi_wheat.crop == "wheat"
        assert intent_hindi_wheat.quantity_quintals == 100.0

        intent_hindi_tomato = _extract_via_rules("टमाटर 30 quintals at Pune ₹40000")
        assert intent_hindi_tomato.crop == "tomato"
        assert intent_hindi_tomato.minimum_net_return_inr == 40000.0

    def test_zero_mojibake_in_all_system_templates(self, sample_breakdown):
        """Templates must not contain corrupted CP1252 characters like â€¢, âœ…, or ðŸ."""
        mojibake_markers = ["â€¢", "âœ…", "â‚¹", "â€”", "â†’", "âš", "â ³", "â Œ", "ðŸ"]

        greeting = build_greeting_response("hello")
        intro = build_introduction_response()

        opt_result = OptimizationResult(
            crop="onion",
            quantity_quintals=50.0,
            all_options=[sample_breakdown],
            recommended_option=sample_breakdown,
            best_channel=sample_breakdown.destination_name,
            max_net_return_inr=sample_breakdown.expected_net_return,
            target_met=True,
        )
        opt_report = "\n".join(format_optimization_report(opt_result))
        safety_notices = "\n".join(format_simulation_and_safety_notices())

        for text_output in [greeting, intro, opt_report, safety_notices]:
            for marker in mojibake_markers:
                assert marker not in text_output, f"Found corrupted mojibake marker '{marker}' in text!"

        # Must contain valid UTF-8 symbols
        assert "कृषिबाज़ार" in intro
        assert "₹" in intro or "INR" in intro
        assert "🌾" in intro

    def test_chat_message_json_serialization_preserves_unicode(self):
        """ChatMessage JSON serialization must preserve UTF-8 Hindi, ₹, and emoji."""
        text = "🌾 नमस्ते! Welcome to KrishiBazaar (कृषिबाज़ार). Your payout is ₹85,000. ✅"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text=text)])
        json_str = msg.model_dump_json()

        data = json.loads(json_str)
        extracted_text = data["content"][0]["text"]
        assert "कृषिबाज़ार" in extracted_text
        assert "₹85,000" in extracted_text
        assert "🌾" in extracted_text
        assert "✅" in extracted_text


# =====================================================================
# 2. Response Conciseness & Deductions Transparency
# =====================================================================

class TestResponseConciseness:
    """Verify that agent responses do not duplicate financial totals or flood the user with raw diagnostics."""

    def test_no_duplicate_financial_totals_in_optimization_report(self, sample_breakdown):
        """Optimization report must state financial figures clearly without repeating totals across multiple lines."""
        opt_result = OptimizationResult(
            crop="onion",
            quantity_quintals=50.0,
            all_options=[sample_breakdown],
            recommended_option=sample_breakdown,
            best_channel=sample_breakdown.destination_name,
            max_net_return_inr=sample_breakdown.expected_net_return,
            target_met=True,
        )
        report_lines = format_optimization_report(opt_result)
        report_text = "\n".join(report_lines)

        # Gross Value, Total Deductions, and Net Return should each appear only in their concise sections
        assert "Itemized Deductions" in report_text
        assert "Transport:" in report_text
        assert "APMC Statutory Cess:" in report_text
        assert "Estimated Net" in report_text
        assert "NOT a guaranteed payout" in report_text

        # Reconciled math statement should NOT be duplicated in duplicate paragraphs
        assert report_text.count("Financial Reconciliation:") == 0

    def test_specialist_diagnostics_kept_in_logs_when_healthy(self):
        """When all specialists are healthy, user-facing notice remains concise and does not dump raw HTTP statuses."""
        healthy_coord = {
            "specialist_diagnostics": {
                "market": {"agent_role": "Market Specialist", "status": "REMOTE_SUCCESS"},
                "weather": {"agent_role": "Weather Specialist", "status": "REMOTE_SUCCESS"},
                "buyer": {"agent_role": "Buyer Specialist", "status": "REMOTE_SUCCESS"},
            }
        }
        notices = format_simulation_and_safety_notices(healthy_coord)
        notices_text = "\n".join(notices)

        assert "Simulation Notice" in notices_text
        assert "Safety Gate" in notices_text
        # Does not flood user with raw technical statuses
        assert "Specialist Communication Diagnostics:" not in notices_text


# =====================================================================
# 3. Unmistakable Simulated Booking States
# =====================================================================

class TestSimulatedBookingStates:
    """Verify that simulated bookings are prominently and unmistakably labeled in all views."""

    def test_proposal_and_booking_confirmation_labels(self, sample_breakdown):
        """Both proposals and booking confirmations must be labeled 'SIMULATED — NOT A REAL BOOKING'."""
        proposal = create_booking_proposal(
            session_id="agent1_farmer",
            farmer_id="agent1_farmer",
            selected_option=sample_breakdown,
            crop="Onion",
            quantity_quintals=50.0,
            origin_location="Nashik",
            transit_date="2026-10-24",
        )

        proposal_text = "\n".join(format_proposal_summary(proposal))
        assert "SIMULATED — NOT A REAL BOOKING" in proposal_text

        booking = execute_simulated_booking(proposal)
        assert booking.is_simulated_booking is True

        confirmation_text = "\n".join(format_booking_confirmation(booking))
        assert "Simulated Booking Confirmed!" in confirmation_text
        assert "SIMULATED — NOT A REAL BOOKING" in confirmation_text

        # Verify card payload
        card_meta = build_booking_confirmed_card(booking)
        card = extract_card(card_meta)
        assert card is not None
        assert "SIMULATED — NOT A REAL BOOKING" in card.payload.title


# =====================================================================
# 4. Agentverse Discovery Metadata & Configuration
# =====================================================================

class TestDiscoveryAndAgentConfig:
    """Verify agent description includes agricultural discovery keywords and configuration is valid."""

    def test_orchestrator_discovery_description(self):
        """Orchestrator description must include essential agricultural keywords."""
        agent = create_orchestrator_agent(port=8050)
        desc = getattr(agent, "_description", "").lower()
        keywords = [
            "agriculture",
            "farming",
            "crop advisory",
            "mandi prices",
            "market analysis",
            "farmer support",
            "crop planning",
        ]
        for kw in keywords:
            assert kw in desc, f"Missing discovery keyword '{kw}' in agent description!"

    def test_seven_agents_diagnostics_validation(self):
        """Diagnostics must validate all 7 agents without port collisions or secret leaks."""
        issues = validate_configuration()
        # In default test environment without port collisions, issues should be empty or only external URL notices
        port_collisions = [i for i in issues if "collision" in i.lower()]
        assert len(port_collisions) == 0

        # Safe diagnostic info
        mock_agent = MagicMock()
        mock_agent.address = "agent1qtestaddress12345"
        diag = get_agent_diagnostic_info("Orchestrator", mock_agent, 8000, "Gateway")
        assert diag["secrets_leak_protected"] is True
        assert "seed" not in diag


# =====================================================================
# 5. Representative 8 ASI:One Conversations
# =====================================================================

class TestRepresentativeAsiOneConversations:
    """
    Executes the 8 representative ASI:One conversation flows:
    1. Greeting
    2. Missing crop details
    3. Complete onion advisory for Nashik
    4. Target net-return and deadline
    5. Specialist failure fallback
    6. Simulated booking requiring explicit farmer approval
    7. Duplicate booking retry idempotency
    8. Payment reconciliation mock/fixture without real transfer
    """

    @pytest.mark.asyncio
    async def test_conversation_1_greeting(self, temp_db, mock_ctx):
        """Conversation 1: Greeting receives warm introduction without triggering specialists."""
        sender = "agent1_conv1_farmer"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Namaste! Hello")])

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock) as mock_coord:

            await handle_chat_message(mock_ctx, sender, msg)
            assert mock_coord.call_count == 0
            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "Namaste" in reply
            assert "KrishiBazaar" in reply

    @pytest.mark.asyncio
    async def test_conversation_2_missing_crop_details(self, temp_db, mock_ctx):
        """Conversation 2: Message missing crop triggers a concise, helpful clarification."""
        sender = "agent1_conv2_farmer"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="I want to sell in Nashik")])

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock) as mock_coord:

            await handle_chat_message(mock_ctx, sender, msg)
            assert mock_coord.call_count == 0
            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "crop" in reply.lower() or "produce" in reply.lower()

    @pytest.mark.asyncio
    async def test_conversation_3_complete_onion_advisory(self, temp_db, mock_ctx, sample_breakdown):
        """Conversation 3: Complete onion advisory triggers specialist coordination and clean advisory."""
        sender = "agent1_conv3_farmer"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="I have 50 quintals of onions in Nashik")])

        prov = DataProvenance(
            source="data.gov.in (AGMARKNET)",
            retrieval_timestamp_utc="2026-10-23T10:00:00Z",
            observation_date="2026-10-23",
            freshness_status="LIVE",
        )
        mock_coord_result = {
            "mandi_result": MandiPriceResult(
                success=True,
                commodity="onion",
                records=[
                    MandiPriceRecord(
                        commodity="onion",
                        mandi_name="Lasalgaon APMC",
                        district="Nashik",
                        state="Maharashtra",
                        modal_price=3200.0,
                        min_price=2800.0,
                        max_price=3500.0,
                        arrival_date="2026-10-23",
                        data_source="data.gov.in_live",
                    )
                ],
                provenance=prov,
            ),
            "nearby_result": None,
            "weather_result": WeatherResult(
                success=True,
                location="Nashik",
                report=WeatherRiskReport(
                    location="Nashik",
                    forecast_date="2026-10-24",
                    temperature_max_c=32.0,
                    temperature_min_c=20.0,
                    precipitation_sum_mm=0.0,
                    precipitation_probability_pct=5.0,
                    wind_speed_max_kmh=10.0,
                    spoilage_hazard_level="LOW",
                    transit_advice="Safe transit conditions.",
                ),
                provenance=prov,
            ),
            "buyer_result": BuyerOffersResult(success=True, crop="onion", offers=[], provenance=prov),
            "transport_result": TransportQuotesResult(success=True, origin="Nashik", quotes={}, provenance=prov),
            "optimization_result": OptimizationResult(
                crop="onion",
                quantity_quintals=50.0,
                all_options=[sample_breakdown],
                recommended_option=sample_breakdown,
                best_channel="Lasalgaon APMC",
                max_net_return_inr=150000.0,
                target_met=True,
            ),
            "target_date": "2026-10-24",
        }

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock, return_value=mock_coord_result):

            await handle_chat_message(mock_ctx, sender, msg)
            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "Lasalgaon APMC" in reply
            assert "150,000" in reply
            assert "SIMULATED — NOT A REAL BOOKING" in reply

    @pytest.mark.asyncio
    async def test_conversation_4_target_net_return_and_deadline(self, temp_db, mock_ctx, sample_breakdown):
        """Conversation 4: Inquiry with target net return (₹) and deadline evaluates feasibility."""
        sender = "agent1_conv4_farmer"
        msg = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="I have 50 quintals of onions in Nashik, want at least ₹85,000 net by tomorrow")],
        )

        prov = DataProvenance(
            source="data.gov.in",
            retrieval_timestamp_utc="2026-10-23T10:00:00Z",
            observation_date="2026-10-23",
            freshness_status="LIVE",
        )
        mock_coord_result = {
            "mandi_result": MandiPriceResult(success=True, commodity="onion", records=[], provenance=prov),
            "nearby_result": None,
            "weather_result": WeatherResult(success=True, location="Nashik", provenance=prov),
            "buyer_result": BuyerOffersResult(success=True, crop="onion", offers=[], provenance=prov),
            "transport_result": TransportQuotesResult(success=True, origin="Nashik", quotes={}, provenance=prov),
            "optimization_result": OptimizationResult(
                crop="onion",
                quantity_quintals=50.0,
                all_options=[sample_breakdown],
                recommended_option=sample_breakdown,
                best_channel="Lasalgaon APMC",
                max_net_return_inr=150000.0,
                target_met=True,
                minimum_net_return_inr=85000.0,
            ),
            "target_date": "2026-10-24",
        }

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock, return_value=mock_coord_result):

            await handle_chat_message(mock_ctx, sender, msg)
            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "Meets Target" in reply
            assert "SIMULATED — NOT A REAL BOOKING" in reply

    @pytest.mark.asyncio
    async def test_conversation_5_specialist_timeout_fallback(self, temp_db, mock_ctx, sample_breakdown):
        """Conversation 5: Unavailable specialist triggers local fallback without inventing data or failing."""
        sender = "agent1_conv5_farmer"
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="I have 50 quintals of onions in Nashik")])

        prov = DataProvenance(
            source="Local Fallback Benchmark",
            retrieval_timestamp_utc="2026-10-23T10:00:00Z",
            observation_date="2026-10-23",
            freshness_status="SIMULATED_SAMPLE",
        )
        mock_coord_result = {
            "mandi_result": MandiPriceResult(
                success=True,
                commodity="onion",
                records=[
                    MandiPriceRecord(
                        commodity="onion",
                        mandi_name="Nashik Mandi (Fallback)",
                        district="Nashik",
                        state="Maharashtra",
                        modal_price=2200.0,
                        min_price=1900.0,
                        max_price=2400.0,
                        arrival_date="2026-10-23",
                        data_source="official_benchmark_cache",
                    )
                ],
                provenance=prov,
            ),
            "nearby_result": None,
            "weather_result": WeatherResult(success=False, location="Nashik", error_message="HTTP 504 Timeout", provenance=prov),
            "buyer_result": BuyerOffersResult(success=True, crop="onion", offers=[], provenance=prov),
            "transport_result": TransportQuotesResult(success=True, origin="Nashik", quotes={}, provenance=prov),
            "optimization_result": OptimizationResult(
                crop="onion",
                quantity_quintals=50.0,
                all_options=[sample_breakdown],
                recommended_option=sample_breakdown,
                best_channel="Nashik Mandi",
                max_net_return_inr=150000.0,
                target_met=True,
            ),
            "specialist_diagnostics": {
                "weather": {"agent_role": "Weather Specialist", "status": "FALLBACK_USED", "error_detail": "HTTP 504 Timeout"}
            },
            "target_date": "2026-10-24",
        }

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock, return_value=mock_coord_result):

            await handle_chat_message(mock_ctx, sender, msg)
            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "Specialist Communication Diagnostics:" in reply
            assert "Remote query unavailable" in reply
            assert "Used verified local fallback data" in reply

    @pytest.mark.asyncio
    async def test_conversation_6_simulated_booking_requires_explicit_approval(self, temp_db, mock_ctx, sample_breakdown):
        """Conversation 6: Booking proposal requires explicit approval; creates simulated booking upon approval."""
        sender = "agent1_conv6_farmer"
        session_key = sender

        # Create active proposal
        proposal = create_booking_proposal(
            session_id=session_key,
            farmer_id=sender,
            selected_option=sample_breakdown,
            crop="Onion",
            quantity_quintals=50.0,
            origin_location="Nashik",
            transit_date="2026-10-24",
        )
        temp_db.save_proposal(proposal)
        temp_db.save_session(
            session_id=session_key,
            farmer_id=sender,
            state="AWAITING_APPROVAL",
            active_proposal_id=proposal.proposal_id,
        )

        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Confirm booking")])

        with patch("krishibazaar.agents.orchestrator.storage", temp_db):
            await handle_chat_message(mock_ctx, sender, msg)
            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "Simulated Booking Confirmed!" in reply
            assert "SIMULATED — NOT A REAL BOOKING" in reply

            # Verify persisted state
            sess = temp_db.get_session(session_key)
            assert sess["state"] == "BOOKING_CONFIRMED"
            assert sess["active_booking_id"] is not None

    @pytest.mark.asyncio
    async def test_conversation_7_duplicate_booking_retry_idempotency(self, temp_db, mock_ctx, sample_breakdown):
        """Conversation 7: Duplicate booking confirmation retry returns existing booking without creating duplicate."""
        sender = "agent1_conv7_farmer"
        session_key = sender

        # Prepopulate session with confirmed booking
        proposal = create_booking_proposal(
            session_id=session_key,
            farmer_id=sender,
            selected_option=sample_breakdown,
            crop="Onion",
            quantity_quintals=50.0,
            origin_location="Nashik",
            transit_date="2026-10-24",
        )
        temp_db.save_proposal(proposal)
        temp_db.consume_proposal(proposal.proposal_id)
        booking = execute_simulated_booking(proposal)
        temp_db.create_booking(booking)
        temp_db.save_session(
            session_id=session_key,
            farmer_id=sender,
            state="BOOKING_CONFIRMED",
            active_proposal_id=proposal.proposal_id,
            active_booking_id=booking.booking_id,
        )

        # Retry confirmation
        msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="Confirm booking")])

        with patch("krishibazaar.agents.orchestrator.storage", temp_db):
            await handle_chat_message(mock_ctx, sender, msg)
            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "Duplicate Approval" in reply or "Already Confirmed" in reply
            assert booking.booking_id in reply
            assert "No duplicate booking was created" in reply

            # Booking count remains exactly 1
            bookings = temp_db.list_bookings(farmer_id=sender)
            assert len(bookings) == 1

    @pytest.mark.asyncio
    async def test_conversation_8_payment_reconciliation_mock_disabled_safety(self, temp_db, mock_ctx):
        """Conversation 8: Payment requests with ENABLE_PAYMENT_PROTOCOL=False trigger safety gate without funds debit."""
        sender = "agent1_conv8_farmer"

        from uagents_core.contrib.protocols.chat.cards import create_card_response_content
        auth_resp = create_card_response_content(
            selection={
                "action": "authorize_payment",
                "reference": "KB-PAY-CONV8",
                "amount": 50000000000000000,
                "currency": "atestfet",
                "recipient": "fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
            }
        )
        msg = ChatMessage(msg_id=uuid4(), content=[auth_resp])

        with patch("krishibazaar.agents.orchestrator.storage", temp_db):
            await handle_chat_message(mock_ctx, sender, msg)
            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "Payment Protocol Safety Gate Active" in reply
            assert "ENABLE_PAYMENT_PROTOCOL=false" in reply
            assert "NO testnet tokens or funds have been transferred" in reply
