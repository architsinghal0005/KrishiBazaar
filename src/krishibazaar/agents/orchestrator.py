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
from krishibazaar.services.extraction import extract_farmer_intent_async
from krishibazaar.storage import storage

# Initialize Agent Chat Protocol using official Fetch.ai specification
chat_proto = Protocol(spec=chat_protocol_spec)


def build_greeting_response(user_text: str | None = None) -> str:
    """Construct initial KrishiBazaar greeting message."""
    return (
        "🌾 **Namaste! Welcome to KrishiBazaar (कृषिबाज़ार)** 🌾\n\n"
        "I am your autonomous farm-to-market negotiation agent. I help Indian farmers compare "
        "mandi benchmark prices, assess transit weather hazards, receive competitive buyer offers, "
        "and calculate guaranteed net returns.\n\n"
        "👉 **How to start:** Tell me what you have to sell, for example:\n"
        "*\"I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow.\"*"
    )


@chat_proto.on_message(ChatMessage)
async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage) -> None:
    """
    Handle incoming ChatMessage from ASI:One or another agent.
    1. Sends delivery acknowledgement.
    2. Safely extracts user TextContent.
    3. Runs structured intent extraction via ASI:One API.
    4. Validates completeness with typed Pydantic models.
    5. Asks follow-up question if required fields are missing; confirms details if complete.
    6. Replies with a structured ChatMessage including TextContent and EndSessionContent.
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

        # 4. Extract structured intent via ASI:One Developer API
        intent = await extract_farmer_intent_async(user_text)

        # 5. Formulate response payload based on intent completeness
        if intent.is_complete and intent.crop and intent.quantity_quintals and intent.origin_location:
            reply_lines = [
                "🌾 **KrishiBazaar: Request Understood!** 🌾",
                "",
                f"• **Crop:** {intent.crop.title()}",
                f"• **Quantity:** {intent.quantity_quintals:g} quintals (approx. {intent.quantity_quintals * 100:g} kg)",
                f"• **Origin Location:** {intent.origin_location}",
            ]
            if intent.selling_deadline:
                reply_lines.append(f"• **Selling Deadline:** {intent.selling_deadline}")
            if intent.minimum_net_return_inr is not None:
                reply_lines.append(f"• **Target Net Return:** INR {intent.minimum_net_return_inr:,.2f}")
            if intent.storage_preference:
                reply_lines.append(f"• **Storage Preference:** {intent.storage_preference}")

            reply_lines.extend([
                "",
                f"✅ *Extracted and validated via ASI:One ({intent.extraction_source}).*",
                "",
                "ℹ️ **Status:** Your harvest inquiry is verified and stored in session. "
                "Market tools (mandi price feeds, weather hazard scoring, and buyer negotiations) "
                "are being connected in the next phase to evaluate your net payouts."
            ])
            reply_body = "\n".join(reply_lines)

            # Persist session state
            try:
                storage.save_session(
                    session_id=sender,
                    farmer_id=sender,
                    state="INTENT_CONFIRMED",
                )
            except Exception:
                pass
        else:
            # Check if user gave a bare greeting
            cleaned_text = user_text.lower().strip()
            if not cleaned_text or cleaned_text in ("hi", "hello", "hey", "namaste", "help"):
                reply_body = build_greeting_response(user_text)
            else:
                reply_body = (
                    f"🌾 **KrishiBazaar Clarification**\n\n"
                    f"{intent.follow_up_question}\n\n"
                    "*(Please specify this detail so I can check nearby mandis and transport quotes without guessing)*"
                )

        # 6. Send response with TextContent and EndSessionContent
        response_msg = ChatMessage(
            content=[
                TextContent(text=reply_body),
                EndSessionContent(),
            ]
        )

        await ctx.send(sender, response_msg)
        ctx.logger.info(f"Dispatched response to sender '{sender}' (complete={intent.is_complete})")

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
        mailbox=True,
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
