"""
KrishiBazaar Market Specialist Agent.
Retrieves and validates live official Indian APMC Mandi wholesale prices (data.gov.in / AGMARKNET).
Preserves live/sample provenance, arrival dates, zero-record handling, and suspicious-price flags.
"""

from __future__ import annotations
import asyncio
import logging
from uagents import Agent, Context, Protocol

from krishibazaar.config import settings
from krishibazaar.models import MandiPriceResult, MarketQuery
from krishibazaar.services.mandi_service import get_mandi_prices_async

from uuid import uuid4

logger = logging.getLogger("krishibazaar.market_agent")

market_proto = Protocol(name="KrishiBazaarMarketProtocol", version="0.1.0")


@market_proto.on_message(MarketQuery, replies=MandiPriceResult)
async def handle_market_query(ctx: Context, sender: str, msg: MarketQuery) -> None:
    """Handle incoming market inquiry from Orchestrator or other agents."""
    req_id = getattr(msg, "request_id", None) or f"REQ-{uuid4().hex[:8].upper()}"
    ctx.logger.info(
        f"[{req_id}] Request received from {sender}: commodity='{msg.commodity}', district='{msg.district}', state='{msg.state}'"
    )
    ctx.logger.info(f"[{req_id}] Handler started: retrieving APMC mandi prices")
    result = await get_mandi_prices_async(
        commodity=msg.commodity,
        district=msg.district,
        state=msg.state,
        allow_sample_fallback_on_error=True,
    )
    result.request_id = req_id
    try:
        await ctx.send(sender, result)
    except Exception as ex:
        ctx.logger.warning(
            f"[{req_id}] Delivery of response to {sender} failed: {type(ex).__name__} ({ex})"
        )


def create_market_agent(
    name: str = "krishibazaar_market",
    seed: str | None = None,
    port: int | None = None,
    mailbox: bool | None = None,
    publish_agent_details: bool = True,
    resolve: Any = None,
    report_events: bool | None = None,
) -> Agent:
    """
    Factory creating the Market Specialist Agent.
    Configures seed from environment, Agentverse mailbox, and protocol manifest publication.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    agent_seed = seed or settings.market_agent_seed
    agent_port = port or settings.market_agent_port
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
        description="KrishiBazaar Market Specialist: Live Mandi Wholesale Price Intelligence",
    )
    agent.include(market_proto, publish_manifest=publish_agent_details)
    return agent


market_agent = create_market_agent()

if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    print_startup_banner(
        agent_name="KrishiBazaar Market Specialist",
        agent=market_agent,
        port=settings.market_agent_port,
        role="Live APMC Mandi Wholesale Price Intelligence",
        extra_details={
            "Data Source": "data.gov.in (AGMARKNET)",
            "API Key Status": (
                "Configured (Present)"
                if settings.datagov_api_key
                else "Not set (Using official portal / curl fallback)"
            ),
        },
    )
    market_agent.run()
