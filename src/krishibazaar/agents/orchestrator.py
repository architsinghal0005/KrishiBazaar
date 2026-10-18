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
from krishibazaar.models import MandiPriceResult
from krishibazaar.services.extraction import extract_farmer_intent_async
from krishibazaar.services.mandi_service import (
    get_mandi_prices_async,
    resolve_district_state,
)
from krishibazaar.storage import storage

# Initialize Agent Chat Protocol using official Fetch.ai specification
chat_proto = Protocol(spec=chat_protocol_spec)


def build_greeting_response(user_text: str | None = None) -> str:
    """Construct initial KrishiBazaar greeting message."""
    return (
        "🌾 **Namaste! Welcome to KrishiBazaar (कृषिबाज़ार)** 🌾\n\n"
        "I am your autonomous farm-to-market negotiation agent. I help Indian farmers compare "
        "mandi benchmark prices, assess transit weather hazards, receive competitive buyer offers, "
        "and calculate estimated net returns.\n\n"
        "👉 **How to start:** Tell me what you have to sell, for example:\n"
        "*\"I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow.\"*"
    )


def format_mandi_market_report(
    crop: str,
    district: str,
    state: str | None,
    mandi_result: MandiPriceResult,
    nearby_result: MandiPriceResult | None = None,
) -> list[str]:
    """Format transparent mandi price report, zero-record notice, or error explanation."""
    lines: list[str] = []

    # Case 1: Live records found for target district
    if mandi_result.success and mandi_result.records:
        lines.append(f"📊 **Live Mandi Wholesale Prices ({crop.title()} in {district}):**")
        lines.append(f"*Source: {mandi_result.provenance.source} | Date: {mandi_result.provenance.observation_date}*")

        has_authentic = False
        for rec in mandi_result.records:
            if rec.is_suspicious:
                lines.append(
                    f"• ⚠️ **{rec.mandi_name}** ({rec.district}): Modal Price INR {rec.modal_price:,.2f}/quintal — "
                    f"*Flagged as suspicious ({rec.suspicion_reason}) and excluded from recommendation.*"
                )
            else:
                has_authentic = True
                price_range = (
                    f"(Min: INR {rec.min_price:,.2f}, Max: INR {rec.max_price:,.2f})"
                    if rec.min_price and rec.max_price
                    else ""
                )
                lines.append(
                    f"• **{rec.mandi_name}** ({rec.district}): Modal Price **INR {rec.modal_price:,.2f}**/quintal {price_range} — "
                    f"Arrival Date: {rec.arrival_date}"
                )

        if not has_authentic:
            lines.append("⚠️ *All reported prices for this district were flagged as suspicious (e.g. bundle/kg entries).*")

    # Case 2: Zero live records for target district
    elif mandi_result.success and not mandi_result.records:
        loc_str = f"district '{district}'" + (f", {state}" if state else "")
        lines.append(
            f"ℹ️ **Live Mandi Status:** No live arrival records were reported for '{crop.title()}' in {loc_str} on data.gov.in (AGMARKNET) today."
        )
        lines.append("*We never fabricate prices or substitute sample benchmark data as live prices.*")

        # Offer clearly labelled state live benchmarks if actually retrieved
        if nearby_result and nearby_result.success and nearby_result.records:
            lines.append("")
            state_label = f"{state} " if state else ""
            lines.append(f"📍 **Other Live {state_label}Mandi Benchmarks:**")
            lines.append(f"*Retrieved live from data.gov.in (AGMARKNET) on {nearby_result.provenance.observation_date}:*")
            shown = 0
            for rec in nearby_result.records:
                if rec.is_suspicious:
                    lines.append(
                        f"• ⚠️ **{rec.mandi_name}** ({rec.district}): Modal Price INR {rec.modal_price:,.2f}/quintal — "
                        f"*Flagged as suspicious ({rec.suspicion_reason})*"
                    )
                else:
                    lines.append(
                        f"• **{rec.mandi_name}** ({rec.district}): Modal Price **INR {rec.modal_price:,.2f}**/quintal — "
                        f"Arrival Date: {rec.arrival_date}"
                    )
                shown += 1
                if shown >= 5:
                    break

    # Case 3: API failed or timed out
    else:
        err_msg = mandi_result.error_message or "API connection unavailable"
        lines.append(f"⚠️ **Market Data Notice:** Live mandi prices could not be retrieved from data.gov.in at this moment ({err_msg}).")
        lines.append("*We do not invent prices or make unverified assumptions.*")

    return lines



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
        if intent.crop and intent.origin_location:
            # Resolve district and state from farmer location
            district, state = resolve_district_state(intent.origin_location)

            # Query live mandi prices for the specific district
            mandi_result = await get_mandi_prices_async(
                commodity=intent.crop,
                district=district,
                state=state,
            )

            # If zero live records for requested district and state is known, query nearby state mandis
            nearby_result: MandiPriceResult | None = None
            if mandi_result.success and not mandi_result.records and state:
                nearby_result = await get_mandi_prices_async(
                    commodity=intent.crop,
                    state=state,
                    district=None,
                )

            reply_lines: list[str] = []

            if intent.is_complete and intent.quantity_quintals:
                reply_lines.extend([
                    "🌾 **KrishiBazaar: Request Understood!** 🌾",
                    "",
                    f"• **Crop:** {intent.crop.title()}",
                    f"• **Quantity:** {intent.quantity_quintals:g} quintals (approx. {intent.quantity_quintals * 100:g} kg)",
                    f"• **Origin Location:** {intent.origin_location} ({district}{f', {state}' if state else ''})",
                ])
                if intent.selling_deadline:
                    reply_lines.append(f"• **Selling Deadline:** {intent.selling_deadline}")
                if intent.minimum_net_return_inr is not None:
                    reply_lines.append(f"• **Target Net Return:** INR {intent.minimum_net_return_inr:,.2f}")
                if intent.storage_preference:
                    reply_lines.append(f"• **Storage Preference:** {intent.storage_preference}")
                reply_lines.append("")
                reply_lines.append(f"✅ *Extracted and validated via ASI:One ({intent.extraction_source}).*")
                reply_lines.append("")
            else:
                reply_lines.extend([
                    "🌾 **KrishiBazaar: Mandi Market Enquiry** 🌾",
                    "",
                    f"• **Crop:** {intent.crop.title()}",
                    f"• **Origin Location:** {intent.origin_location} ({district}{f', {state}' if state else ''})",
                    "",
                ])

            # Append live mandi market report
            mandi_report = format_mandi_market_report(
                crop=intent.crop,
                district=district,
                state=state,
                mandi_result=mandi_result,
                nearby_result=nearby_result,
            )
            reply_lines.extend(mandi_report)

            # Next step guidance
            reply_lines.append("")
            if intent.is_complete and intent.quantity_quintals:
                reply_lines.append(
                    "ℹ️ **Status:** Your harvest inquiry was recorded and live market rates were retrieved for this response. "
                    "Next, transit weather hazard scoring and buyer negotiations will evaluate your estimated net return."
                )
                try:
                    storage.save_session(
                        session_id=sender,
                        farmer_id=sender,
                        state="INTENT_CONFIRMED",
                    )
                except Exception:
                    pass
            else:
                if intent.follow_up_question:
                    reply_lines.append(f"👉 **To calculate your net return:** {intent.follow_up_question}")
                try:
                    storage.save_session(
                        session_id=sender,
                        farmer_id=sender,
                        state="AWAITING_DETAILS",
                    )
                except Exception:
                    pass

            reply_body = "\n".join(reply_lines)

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
