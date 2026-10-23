"""
KrishiBazaar Transport Specialist Agent.
Generates payload-matched, weather-aware simulated freight quotes.
Strict Compliance: All quotes are clearly marked as simulated benchmarks, not verified bookings.
"""

from __future__ import annotations
import asyncio
import logging
from uagents import Agent, Context, Protocol

from krishibazaar.config import settings
from krishibazaar.models import TransportQuotesResult, TransportQuery, WeatherRiskReport
from krishibazaar.services.transport_service import get_transport_quotes_async

logger = logging.getLogger("krishibazaar.transport_agent")

from uuid import uuid4

transport_proto = Protocol(name="KrishiBazaarTransportProtocol", version="0.1.0")


@transport_proto.on_message(TransportQuery, replies=TransportQuotesResult)
async def handle_transport_query(ctx: Context, sender: str, msg: TransportQuery) -> None:
    """Handle incoming request for freight quotes."""
    req_id = getattr(msg, "request_id", None) or f"REQ-{uuid4().hex[:8].upper()}"
    ctx.logger.info(
        f"[{req_id}] Request received from {sender}: origin='{msg.origin}', dests={len(msg.destinations)}, qty={msg.quantity_quintals}qtl"
    )
    ctx.logger.info(f"[{req_id}] Handler started: calculating payload-matched freight quotes")
    # Reconstruct a lightweight WeatherRiskReport for hazard level if specified
    weather_report: WeatherRiskReport | None = None
    if msg.weather_hazard_level in ("MEDIUM", "HIGH"):
        weather_report = WeatherRiskReport(
            location=msg.origin,
            forecast_date="today",
            temperature_max_c=32.0,
            temperature_min_c=20.0,
            precipitation_sum_mm=10.0 if msg.weather_hazard_level == "HIGH" else 3.0,
            precipitation_probability_pct=75.0 if msg.weather_hazard_level == "HIGH" else 45.0,
            wind_speed_max_kmh=15.0,
            spoilage_hazard_level=msg.weather_hazard_level,
            transit_advice="Weather hazard mitigation active.",
        )

    result = await get_transport_quotes_async(
        origin=msg.origin,
        destinations=msg.destinations,
        quantity_quintals=msg.quantity_quintals,
        weather_report=weather_report,
    )
    result.request_id = req_id
    try:
        await ctx.send(sender, result)
    except Exception as ex:
        ctx.logger.warning(
            f"[{req_id}] Delivery of response to {sender} failed: {type(ex).__name__} ({ex})"
        )


def create_transport_agent(
    name: str = "krishibazaar_transport",
    seed: str | None = None,
    port: int | None = None,
    mailbox: bool | None = None,
    publish_agent_details: bool = True,
    resolve: Any = None,
    report_events: bool | None = None,
) -> Agent:
    """
    Factory creating the Transport Specialist Agent.
    Configures seed from environment, Agentverse mailbox, and protocol manifest publication.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    agent_seed = seed or settings.transport_agent_seed
    agent_port = port or settings.transport_agent_port
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
        description="KrishiBazaar Transport Specialist: Freight Logistics & Fleet Matching",
    )
    agent.include(transport_proto, publish_manifest=publish_agent_details)
    return agent


transport_agent = create_transport_agent()

if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    print_startup_banner(
        agent_name="KrishiBazaar Transport Specialist",
        agent=transport_agent,
        port=settings.transport_agent_port,
        role="Freight Logistics & Fleet Capacity Matching (Simulated Benchmark)",
        extra_details={
            "Fleet Engine": "Dynamic capacity matching (Tata Ace, 407, 17ft, 22ft)",
            "Weather Surcharge": "Integrated Open-Meteo hazard risk buffer (0-20%)",
        },
    )
    transport_agent.run()
