"""
KrishiBazaar Orchestrator Agent.
Primary ASI:One-facing conversational front-door implementing Fetch.ai Agent Chat Protocol (ACP).
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    EndSessionContent,
    chat_protocol_spec,
)

from krishibazaar.config import settings

# Initialize Agent Chat Protocol using official Fetch.ai specification
chat_proto = Protocol(spec=chat_protocol_spec)


def build_greeting_response(user_text: str | None = None) -> str:
    """Construct the initial KrishiBazaar greeting message."""
    return (
        "🌾 **Namaste! Welcome to KrishiBazaar (कृषिबाज़ार)** 🌾\n\n"
        "I am your autonomous farm-to-market negotiation agent. I help Indian farmers compare "
        "mandi benchmark prices, assess transit weather hazards, receive competitive buyer offers, "
        "and calculate guaranteed net returns.\n\n"
        "ℹ️ **Current Status (Phase 2):**\n"
        "The KrishiBazaar Orchestrator communication channel is active and verified over "
        "the Agent Chat Protocol (ACP). However, market tools (live mandi price feeds, Open-Meteo weather models, "
        "and buyer negotiation agents) are currently being assembled and are not connected yet.\n\n"
        "Full autonomous mandi price intelligence and buyer negotiation will be unlocked in upcoming phases!"
    )


@chat_proto.on_message(ChatMessage)
async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage) -> None:
    """
    Handle incoming ChatMessage from ASI:One or another agent.
    1. Sends delivery acknowledgement.
    2. Safely extracts user TextContent.
    3. Logs incoming request without exposing secrets.
    4. Replies with a structured ChatMessage including TextContent and EndSessionContent.
    """
    try:
        # 1. Immediate Delivery Acknowledgement matching msg_id
        ack = ChatAcknowledgement(acknowledged_msg_id=msg.msg_id)
        await ctx.send(sender, ack)

        # 2. Extract TextContent safely
        user_text = ""
        for item in msg.content:
            if isinstance(item, TextContent):
                user_text += item.text
            elif getattr(item, "type", None) == "text" and hasattr(item, "text"):
                user_text += getattr(item, "text", "")

        # 3. Log incoming request safely without exposing secret seeds or keys
        ctx.logger.info(
            f"Received ChatMessage from sender '{sender}' "
            f"(msg_id: {msg.msg_id}, length: {len(user_text)} chars)"
        )

        # 4. Formulate response payload
        reply_body = build_greeting_response(user_text)
        response_msg = ChatMessage(
            content=[
                TextContent(text=reply_body),
                EndSessionContent(),
            ]
        )

        await ctx.send(sender, response_msg)
        ctx.logger.info(f"Dispatched greeting and status response to sender '{sender}'")

    except Exception as ex:
        # Safe error logging without secret exposure
        ctx.logger.error(f"Error handling ChatMessage from {sender}: {type(ex).__name__} - {str(ex)}")


@chat_proto.on_message(ChatAcknowledgement)
async def handle_acknowledgement(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
    """Handle delivery acknowledgement receipts."""
    ctx.logger.debug(
        f"Delivery receipt from sender '{sender}' for message: {msg.acknowledged_msg_id}"
    )


def create_orchestrator_agent(
    name: str = "krishibazaar_orchestrator",
    seed: str | None = None,
    port: int | None = None,
    mailbox: bool | None = None,
) -> Agent:
    """
    Factory creating the KrishiBazaar Orchestrator Agent.
    Configures seed from environment, Agentverse mailbox, and chat protocol manifest.
    """
    # Ensure asyncio event loop exists in current thread (Python 3.12/3.14 compatibility)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    agent_seed = seed or settings.orchestrator_seed
    agent_port = port or settings.orchestrator_port
    use_mailbox = mailbox if mailbox is not None else settings.use_mailbox

    agentverse_config = settings.agentverse_url
    if settings.agentverse_api_key:
        agentverse_config = f"{settings.agentverse_api_key}@{settings.agentverse_url}"

    agent = Agent(
        name=name,
        seed=agent_seed,
        port=agent_port,
        mailbox=use_mailbox,
        agentverse=agentverse_config,
        publish_agent_details=True,
        description="KrishiBazaar Orchestrator: Autonomous Farm-to-Market Negotiation Agent for Indian Farmers",
    )

    # Attach the official Agent Chat Protocol with manifest publication
    agent.include(chat_proto, publish_manifest=True)
    return agent


# Default agent instance
orchestrator_agent = create_orchestrator_agent()


if __name__ == "__main__":
    print(f"[*] Starting KrishiBazaar Orchestrator Agent on port {settings.orchestrator_port}...")
    print(f"[*] Agent Address: {orchestrator_agent.address}")
    orchestrator_agent.run()
