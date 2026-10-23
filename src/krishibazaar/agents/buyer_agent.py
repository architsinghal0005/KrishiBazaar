"""
KrishiBazaar Buyer Specialist Agent.
Generates structured, simulated benchmark buyer procurement bids for negotiation comparison.
Strict Compliance: All offers are labeled as simulated benchmarks; never implies a real buyer transaction.
"""

from __future__ import annotations
import asyncio
import logging
from uagents import Agent, Context, Protocol

from krishibazaar.config import settings
from krishibazaar.models import BuyerOffersResult, BuyerQuery
from krishibazaar.services.buyer_service import get_buyer_offers_async

from uuid import uuid4

logger = logging.getLogger("krishibazaar.buyer_agent")


async def handle_buyer_query(
    ctx: Context,
    sender: str,
    msg: BuyerQuery,
    profile: str = "buyer_a",
) -> None:
    """Handle incoming request for buyer procurement offers."""
    req_id = getattr(msg, "request_id", None) or f"REQ-{uuid4().hex[:8].upper()}"
    ctx.logger.info(
        f"[{req_id}] Request received [{profile}] from {sender}: crop='{msg.crop}', qty={msg.quantity_quintals}qtl, origin='{msg.origin_location}'"
    )
    ctx.logger.info(f"[{req_id}] Handler started [{profile}]: calculating benchmark procurement bids")
    result = await get_buyer_offers_async(
        crop=msg.crop,
        quantity_quintals=msg.quantity_quintals,
        origin_location=msg.origin_location,
        price_hint=msg.price_hint,
        profile=profile,
    )
    result.request_id = req_id
    try:
        await ctx.send(sender, result)
    except Exception as ex:
        ctx.logger.warning(
            f"[{req_id}] Delivery of response [{profile}] to {sender} failed: {type(ex).__name__} ({ex})"
        )


def create_buyer_protocol(profile: str = "buyer_a") -> Protocol:
    """
    Factory creating KrishiBazaarBuyerProtocol configured for a specific buyer profile.
    """
    proto = Protocol(name="KrishiBazaarBuyerProtocol", version="0.1.0")

    @proto.on_message(BuyerQuery, replies=BuyerOffersResult)
    async def _on_buyer_query(ctx: Context, sender: str, msg: BuyerQuery) -> None:
        await handle_buyer_query(ctx, sender, msg, profile=profile)

    return proto


buyer_proto = create_buyer_protocol(profile="buyer_a")


def create_buyer_agent(
    name: str = "krishibazaar_buyer",
    seed: str | None = None,
    port: int | None = None,
    mailbox: bool | None = None,
    publish_agent_details: bool = True,
    profile: str = "buyer_a",
    protocol: Protocol | None = None,
    resolve: Any = None,
    report_events: bool | None = None,
) -> Agent:
    """
    Factory creating the Buyer Specialist Agent.
    Configures seed from environment, Agentverse mailbox, and protocol manifest publication.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if profile == "buyer_b":
        agent_seed = seed or settings.buyer_b_agent_seed
        agent_port = port or settings.buyer_b_agent_port
        description = "KrishiBazaar Buyer B Specialist: Institutional & Export Procurement Bids"
    else:
        agent_seed = seed or settings.buyer_agent_seed
        agent_port = port or settings.buyer_agent_port
        description = "KrishiBazaar Buyer Specialist: Structured Benchmark Procurement Bids"

    use_mailbox = mailbox if mailbox is not None else settings.use_mailbox
    use_report_events = report_events if report_events is not None else settings.report_events

    agentverse_config = settings.agentverse_url
    if settings.agentverse_api_key:
        agentverse_config = f"{settings.agentverse_api_key}@{settings.agentverse_url}"

    from krishibazaar.agents.resolver import get_krishibazaar_resolver

    resolver = resolve or get_krishibazaar_resolver()
    agent = Agent(
        name=name,
        seed=agent_seed,
        port=agent_port,
        mailbox=use_mailbox,
        agentverse=agentverse_config,
        resolve=resolver,
        publish_agent_details=publish_agent_details,
        report_events=use_report_events,
        description=description,
    )
    proto_to_include = protocol or (buyer_proto if profile == "buyer_a" else create_buyer_protocol(profile))
    agent.include(proto_to_include, publish_manifest=publish_agent_details)
    return agent


buyer_agent = create_buyer_agent()

if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    print_startup_banner(
        agent_name="KrishiBazaar Buyer A Specialist",
        agent=buyer_agent,
        port=settings.buyer_agent_port,
        role="FPO & Direct Retail Procurement Bids (Simulated Benchmark)",
        extra_details={
            "Buyer Profile": "buyer_a (Farmer Producer Organizations & Retail Aggregators)",
            "Bid Model": "Simulated benchmarks with farmgate pickup / delivery options",
        },
    )
    buyer_agent.run()
