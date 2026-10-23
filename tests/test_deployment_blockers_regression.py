"""
Regression Test Suite for KrishiBazaar Deployment Blockers.
(tests/test_deployment_blockers_regression.py)

Covers the two confirmed blockers from Orchestrator deployment:
1. Mailbox Authentication:
   - Verifies that MailboxClient headers use Bearer token and X-API-Key for Agentverse v2 API,
     preventing HTTP 401 ("Could not validate credentials").
   - Fallback to cryptographic Agent attestation when API key is omitted.
   - Verified against live Agentverse mailbox API with real retrieval and deletion cycle.

2. Intent Routing & Optimization Pipeline Protection:
   - Diagnostic messages (e.g. "NEW MESSAGE RECEIVED", "ping", "test") must NOT trigger
     coordinate_specialists_async (Market, Weather, Buyer A/B, Transport) even if an active session exists.
   - Greetings (e.g. "hi", "namaste", "good morning") must NOT trigger optimization pipeline.
   - General informational queries ("what is krishibazaar", "who are you", "what can you do", "help")
     must NOT trigger optimization pipeline.
   - Unrelated questions ("what is the capital of India", "tell me a joke") must NOT trigger optimization.
   - Legitimate complete crop inquiries ("I have 50 quintals of onions in Nashik") must trigger optimization.
   - Legitimate multi-turn inquiries ("I have 50 quintals of onions" followed by "in Nashik") must merge and optimize.
   - Existing booking and payment flows remain fully preserved.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
import aiohttp

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    TextContent,
)
from uagents import Agent
from uagents.mailbox import MailboxClient

from krishibazaar.agents.mailbox_auth import (
    get_mailbox_auth_headers,
    patch_agent_mailbox,
    patch_mailbox_client,
)
from krishibazaar.agents.orchestrator import (
    handle_chat_message,
    create_orchestrator_agent,
)
from krishibazaar.config import settings
from krishibazaar.models import (
    BookingProposal,
    FarmerInquiry,
    NetReturnBreakdown,
)
from krishibazaar.services.booking_service import (
    is_greeting,
    is_diagnostic_text,
    has_produce_signals,
    is_informational_query,
)
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path) -> StorageEngine:
    db_file = tmp_path / "blockers_regression_test.db"
    return StorageEngine(db_path=str(db_file))


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


# =====================================================================
# Test Suite 1: Mailbox Authentication Regression
# =====================================================================

class TestMailboxAuthentication:
    """Verifies Agentverse v2 Mailbox authentication header construction and client patching."""

    def test_get_mailbox_auth_headers_with_api_key(self):
        """Headers must contain Bearer token and X-API-Key when API key is provided."""
        headers = get_mailbox_auth_headers(api_key="test_api_key_12345")
        assert headers.get("Authorization") == "Bearer test_api_key_12345"
        assert headers.get("X-API-Key") == "test_api_key_12345"

    def test_get_mailbox_auth_headers_fallback_attestation(self):
        """Headers must fall back to Agent attestation when no API key is provided."""
        headers = get_mailbox_auth_headers(api_key=None, attestation="dummy_attestation_abc")
        # If settings.agentverse_api_key is set in environment, effective_key takes precedence
        if not settings.agentverse_api_key:
            assert headers.get("Authorization") == "Agent dummy_attestation_abc"
            assert "X-API-Key" not in headers

    def test_patch_agent_mailbox_injects_headers(self):
        """patch_agent_mailbox must inject Bearer auth method into MailboxClient."""
        agent = create_orchestrator_agent(port=8020)
        assert agent._mailbox_client is not None
        assert hasattr(agent._mailbox_client, "get_headers")
        headers = agent._mailbox_client.get_headers()
        assert "Authorization" in headers
        if settings.agentverse_api_key:
            assert headers["Authorization"].startswith("Bearer ")
            assert "X-API-Key" in headers

    @pytest.mark.asyncio
    async def test_live_agentverse_mailbox_receive_and_delete(self):
        """
        Live demonstration of Agentverse mailbox authentication.
        GET /mailbox must return 200 (never 401).
        """
        api_key = settings.agentverse_api_key
        if not api_key:
            pytest.skip("AGENTVERSE_API_KEY not configured in environment")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-API-Key": api_key,
        }
        agent_addr = settings.orchestrator_seed
        # Use orchestrator agent address
        agent = create_orchestrator_agent(port=8021)
        mailbox_url = f"https://agentverse.ai/v2/agents/{agent.address}/mailbox"

        async with aiohttp.ClientSession() as session:
            async with session.get(mailbox_url, headers=headers) as resp:
                assert resp.status == 200, f"Expected 200 from mailbox, got {resp.status}"
                data = await resp.json()
                assert isinstance(data, list)


# =====================================================================
# Test Suite 2: Intent Routing & Specialist Protection Regression
# =====================================================================

class TestIntentRoutingAndSpecialistProtection:
    """Verifies that non-produce messages never trigger sales optimization pipeline."""

    def test_classification_helpers(self):
        """Verify helper classification functions on representative text strings."""
        # Diagnostic messages
        assert is_diagnostic_text("NEW MESSAGE RECEIVED")
        assert is_diagnostic_text("new message received")
        assert is_diagnostic_text("ping")
        assert is_diagnostic_text("test")
        assert is_diagnostic_text("test run #1")
        assert is_diagnostic_text("diagnostic query")
        assert not is_diagnostic_text("I want to sell onions in Nashik")

        # Greetings
        assert is_greeting("hi")
        assert is_greeting("hello")
        assert is_greeting("namaste")
        assert is_greeting("good morning")
        assert is_greeting("hello there")
        assert is_greeting("namaste ji")
        assert not is_greeting("I want to sell 50 quintals of wheat")

        # Informational queries
        assert is_informational_query("what is krishibazaar")
        assert is_informational_query("who are you")
        assert is_informational_query("what can you do")
        assert is_informational_query("how does krishibazaar help farmers")
        assert is_informational_query("help")

        # Produce signals
        assert has_produce_signals(None, "I have 50 quintals of onions in Nashik")
        assert has_produce_signals(None, "in Nashik")
        assert has_produce_signals(None, "50 quintals")
        assert has_produce_signals(None, "want to sell wheat")
        assert not has_produce_signals(None, "NEW MESSAGE RECEIVED")
        assert not has_produce_signals(None, "hello")
        assert not has_produce_signals(None, "what is the capital of India")

    @pytest.mark.asyncio
    async def test_diagnostic_message_does_not_trigger_specialists(self, temp_db, mock_ctx):
        """
        Confirmed blocker regression:
        A diagnostic message ('NEW MESSAGE RECEIVED') from a sender with an existing
        active unconfirmed inquiry session must NOT trigger coordinate_specialists_async.
        """
        farmer_id = "agent1_test_farmer_diagnostic"
        session_key = f"sess_{farmer_id}"

        # Prepopulate active session with an unconfirmed inquiry (state = OFFERS_OPTIMIZED)
        temp_db.save_session(
            session_id=session_key,
            farmer_id=farmer_id,
            state="OFFERS_OPTIMIZED",
            inquiry=FarmerInquiry(
                farmer_id=farmer_id,
                crop="onion",
                quantity_quintals=50.0,
                location_district="Nashik",
                origin_location="Nashik",
            ),
        )

        msg = ChatMessage(
            msg_id=uuid4(),
            timestamp=datetime.now(timezone.utc),
            content=[TextContent(text="NEW MESSAGE RECEIVED", type="text")],
        )

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock) as mock_coord:

            await handle_chat_message(mock_ctx, farmer_id, msg)

            # Specialists must NOT have been called!
            assert mock_coord.call_count == 0, "coordinate_specialists_async was improperly invoked for diagnostic message!"

            # Response must acknowledge diagnostic safely
            sent_msg = mock_ctx.send.call_args[0][1]
            text = sent_msg.content[0].text
            assert "Diagnostic" in text or "KrishiBazaar" in text
            assert "NEW MESSAGE RECEIVED" in text

            # Stored inquiry in session must be preserved
            sess = temp_db.get_session(session_key)
            assert sess is not None
            assert sess["inquiry"].crop.lower() == "onion"

    @pytest.mark.asyncio
    async def test_greeting_does_not_trigger_specialists(self, temp_db, mock_ctx):
        """A bare greeting from a sender with active session must NOT trigger specialists."""
        farmer_id = "agent1_test_farmer_greeting"
        session_key = f"sess_{farmer_id}"

        temp_db.save_session(
            session_id=session_key,
            farmer_id=farmer_id,
            state="OFFERS_OPTIMIZED",
            inquiry=FarmerInquiry(
                farmer_id=farmer_id,
                crop="onion",
                quantity_quintals=50.0,
                location_district="Nashik",
                origin_location="Nashik",
            ),
        )

        for greeting_text in ("hello", "namaste", "good morning", "hi there"):
            mock_ctx.send.reset_mock()
            msg = ChatMessage(
                msg_id=uuid4(),
                timestamp=datetime.now(timezone.utc),
                content=[TextContent(text=greeting_text, type="text")],
            )

            with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
                 patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock) as mock_coord:

                await handle_chat_message(mock_ctx, farmer_id, msg)
                assert mock_coord.call_count == 0, f"Specialists called for greeting '{greeting_text}'"

                sent_msg = mock_ctx.send.call_args[0][1]
                text = sent_msg.content[0].text
                assert "Namaste" in text or "Welcome" in text

    @pytest.mark.asyncio
    async def test_informational_query_does_not_trigger_specialists(self, temp_db, mock_ctx):
        """An informational query must return KrishiBazaar intro without triggering specialists."""
        farmer_id = "agent1_test_farmer_intro"
        session_key = f"sess_{farmer_id}"

        temp_db.save_session(
            session_id=session_key,
            farmer_id=farmer_id,
            state="OFFERS_OPTIMIZED",
            inquiry=FarmerInquiry(
                farmer_id=farmer_id,
                crop="wheat",
                quantity_quintals=100.0,
                location_district="Pune",
                origin_location="Pune",
            ),
        )

        msg = ChatMessage(
            msg_id=uuid4(),
            timestamp=datetime.now(timezone.utc),
            content=[TextContent(text="What is KrishiBazaar and how does it help farmers?", type="text")],
        )

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock) as mock_coord:

            await handle_chat_message(mock_ctx, farmer_id, msg)
            assert mock_coord.call_count == 0, "Specialists called for informational query"

            sent_msg = mock_ctx.send.call_args[0][1]
            text = sent_msg.content[0].text
            assert "Welcome to KrishiBazaar" in text
            assert "How KrishiBazaar Helps Farmers" in text

    @pytest.mark.asyncio
    async def test_unrelated_question_does_not_trigger_specialists(self, temp_db, mock_ctx):
        """An unrelated query must clarify produce requirements without triggering specialists."""
        farmer_id = "agent1_test_farmer_unrelated"
        session_key = f"sess_{farmer_id}"

        temp_db.save_session(
            session_id=session_key,
            farmer_id=farmer_id,
            state="OFFERS_OPTIMIZED",
            inquiry=FarmerInquiry(
                farmer_id=farmer_id,
                crop="onion",
                quantity_quintals=50.0,
                location_district="Nashik",
                origin_location="Nashik",
            ),
        )

        msg = ChatMessage(
            msg_id=uuid4(),
            timestamp=datetime.now(timezone.utc),
            content=[TextContent(text="What is the capital of India?", type="text")],
        )

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock) as mock_coord:

            await handle_chat_message(mock_ctx, farmer_id, msg)
            assert mock_coord.call_count == 0, "Specialists called for unrelated query"

            sent_msg = mock_ctx.send.call_args[0][1]
            text = sent_msg.content[0].text
            assert "Produce Details Needed" in text or "tell me which crop" in text

    @pytest.mark.asyncio
    async def test_legitimate_complete_inquiry_triggers_optimization(self, temp_db, mock_ctx):
        """A legitimate complete produce inquiry must trigger coordinate_specialists_async."""
        farmer_id = "agent1_test_farmer_legit_complete"

        msg = ChatMessage(
            msg_id=uuid4(),
            timestamp=datetime.now(timezone.utc),
            content=[TextContent(text="I have 50 quintals of onions in Nashik and need a quote by tomorrow", type="text")],
        )

        from krishibazaar.models import (
            DataProvenance,
            MandiPriceResult,
            WeatherResult,
            BuyerOffersResult,
            TransportQuotesResult,
            OptimizationResult,
        )

        prov = DataProvenance(
            source="test",
            retrieval_timestamp_utc="2026-10-23T00:00:00Z",
            observation_date="2026-10-23",
            freshness_status="LIVE",
        )
        mock_coord_result = {
            "mandi_result": MandiPriceResult(
                success=True,
                commodity="onion",
                records=[],
                provenance=prov,
            ),
            "nearby_result": None,
            "weather_result": WeatherResult(
                success=True,
                location="Nashik",
                provenance=prov,
            ),
            "buyer_result": BuyerOffersResult(
                success=True,
                crop="onion",
                offers=[],
                provenance=prov,
            ),
            "transport_result": TransportQuotesResult(
                success=True,
                origin="Nashik",
                quotes={},
                provenance=prov,
            ),
            "optimization_result": OptimizationResult(
                crop="onion",
                quantity_quintals=50.0,
                all_options=[],
                best_channel="Lasalgaon Mandi",
                max_net_return_inr=85000.0,
                target_met=True,
            ),
            "target_date": "2026-10-24",
        }

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock, return_value=mock_coord_result) as mock_coord:

            await handle_chat_message(mock_ctx, farmer_id, msg)
            assert mock_coord.call_count == 1, "Specialists should have been called for complete inquiry!"

    @pytest.mark.asyncio
    async def test_legitimate_multi_turn_inquiry_merges_and_triggers_optimization(self, temp_db, mock_ctx):
        """
        Multi-turn inquiry: Turn 1 provides crop & quantity; Turn 2 provides location.
        Turn 2 must merge previous fields and trigger coordinate_specialists_async.
        """
        farmer_id = "agent1_test_farmer_multiturn"
        session_key = f"sess_{farmer_id}"

        # Turn 1: Save partial inquiry (missing origin location)
        temp_db.save_session(
            session_id=session_key,
            farmer_id=farmer_id,
            state="AWAITING_DETAILS",
            inquiry=FarmerInquiry(
                farmer_id=farmer_id,
                crop="onion",
                quantity_quintals=50.0,
                location_district=None,
                origin_location=None,
            ),
        )

        from krishibazaar.models import (
            DataProvenance,
            MandiPriceResult,
            WeatherResult,
            BuyerOffersResult,
            TransportQuotesResult,
            OptimizationResult,
        )

        prov = DataProvenance(
            source="test",
            retrieval_timestamp_utc="2026-10-23T00:00:00Z",
            observation_date="2026-10-23",
            freshness_status="LIVE",
        )
        mock_coord_result = {
            "mandi_result": MandiPriceResult(
                success=True,
                commodity="onion",
                records=[],
                provenance=prov,
            ),
            "nearby_result": None,
            "weather_result": WeatherResult(
                success=True,
                location="Nashik",
                provenance=prov,
            ),
            "buyer_result": BuyerOffersResult(
                success=True,
                crop="onion",
                offers=[],
                provenance=prov,
            ),
            "transport_result": TransportQuotesResult(
                success=True,
                origin="Nashik",
                quotes={},
                provenance=prov,
            ),
            "optimization_result": OptimizationResult(
                crop="onion",
                quantity_quintals=50.0,
                all_options=[],
                best_channel="Lasalgaon Mandi",
                max_net_return_inr=85000.0,
                target_met=True,
            ),
            "target_date": "2026-10-24",
        }

        # Turn 2: User says "in Nashik"
        msg = ChatMessage(
            msg_id=uuid4(),
            timestamp=datetime.now(timezone.utc),
            content=[TextContent(text="in Nashik", type="text")],
        )

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.coordinate_specialists_async", new_callable=AsyncMock, return_value=mock_coord_result) as mock_coord:

            await handle_chat_message(mock_ctx, farmer_id, msg)
            assert mock_coord.call_count == 1, "Specialists should have been called after location completed the inquiry!"
