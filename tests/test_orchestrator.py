"""
Unit tests for KrishiBazaar Orchestrator Agent (tests/test_orchestrator.py).
Verifies Agent Chat Protocol compliance, message handling, acknowledgements, and session termination.
"""

from unittest.mock import AsyncMock, MagicMock
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
)
from krishibazaar.config import settings


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

    # Check included protocol
    protocol_specs = [p.spec.name for p in agent.protocols.values() if p.spec]
    assert "AgentChatProtocol" in protocol_specs


def test_build_greeting_response():
    """Verify greeting text contains brand and tool status explanation."""
    greeting = build_greeting_response("Hello KrishiBazaar")
    assert "KrishiBazaar" in greeting
    assert "कृषिबाज़ार" in greeting
    assert "not connected yet" in greeting
    assert "Phase 2" in greeting


@pytest.mark.asyncio
async def test_handle_chat_message_flow():
    """
    Test incoming ChatMessage triggers:
    1. ChatAcknowledgement matching msg_id.
    2. Response ChatMessage with TextContent and EndSessionContent.
    """
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()
    mock_ctx.logger = MagicMock()

    test_msg_id = uuid4()
    incoming_msg = ChatMessage(
        msg_id=test_msg_id,
        content=[TextContent(text="What is the onion mandi price in Nashik?")],
    )
    sender = "agent1quser_farmer_test_client_address"

    await handle_chat_message(mock_ctx, sender, incoming_msg)

    # Must send exactly 2 messages: Acknowledgement, then Response
    assert mock_ctx.send.call_count == 2

    # 1. Verify acknowledgement
    first_call_target, first_call_msg = mock_ctx.send.call_args_list[0][0]
    assert first_call_target == sender
    assert isinstance(first_call_msg, ChatAcknowledgement)
    assert first_call_msg.acknowledged_msg_id == test_msg_id

    # 2. Verify response payload
    second_call_target, second_call_msg = mock_ctx.send.call_args_list[1][0]
    assert second_call_target == sender
    assert isinstance(second_call_msg, ChatMessage)

    # Check contents of response
    has_text = any(isinstance(c, TextContent) for c in second_call_msg.content)
    has_end_session = any(isinstance(c, EndSessionContent) for c in second_call_msg.content)
    assert has_text, "Response must include TextContent"
    assert has_end_session, "Response must include EndSessionContent"

    # Verify no unhandled error logs
    mock_ctx.logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_handle_acknowledgement_receipt():
    """Verify delivery receipt handling."""
    mock_ctx = MagicMock()
    mock_ctx.logger = MagicMock()

    ack = ChatAcknowledgement(acknowledged_msg_id=uuid4())
    await handle_acknowledgement(mock_ctx, "agent1qsender", ack)

    mock_ctx.logger.debug.assert_called_once()
