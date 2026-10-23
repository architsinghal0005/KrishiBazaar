"""
KrishiBazaar Weather Specialist Agent.
Retrieves Open-Meteo forecasts and evaluates transit weather hazards (rain/heat/spoilage).
Clearly reports unavailable or fallback data and distinguishes forecasts from observed conditions.
"""

from __future__ import annotations
import asyncio
import logging
from uagents import Agent, Context, Protocol

from krishibazaar.config import settings
from krishibazaar.models import WeatherQuery, WeatherResult
from krishibazaar.services.weather_service import get_transit_weather_async

from uuid import uuid4

logger = logging.getLogger("krishibazaar.weather_agent")

weather_proto = Protocol(name="KrishiBazaarWeatherProtocol", version="0.1.0")


@weather_proto.on_message(WeatherQuery, replies=WeatherResult)
async def handle_weather_query(ctx: Context, sender: str, msg: WeatherQuery) -> None:
    """Handle incoming weather forecast inquiry from Orchestrator or other agents."""
    req_id = getattr(msg, "request_id", None) or f"REQ-{uuid4().hex[:8].upper()}"
    ctx.logger.info(
        f"[{req_id}] Request received from {sender}: location='{msg.location}', target_date='{msg.target_date}', coords=({msg.latitude}, {msg.longitude})"
    )
    ctx.logger.info(f"[{req_id}] Handler started: evaluating transit weather hazards")
    result = await get_transit_weather_async(
        location=msg.location,
        latitude=msg.latitude,
        longitude=msg.longitude,
        target_date=msg.target_date,
    )
    result.request_id = req_id
    hazard = result.report.spoilage_hazard_level if result.report else "N/A"
    try:
        await ctx.send(sender, result)
    except Exception as ex:
        ctx.logger.warning(
            f"[{req_id}] Delivery of response to {sender} failed: {type(ex).__name__} ({ex})"
        )


def create_weather_agent(
    name: str = "krishibazaar_weather",
    seed: str | None = None,
    port: int | None = None,
    mailbox: bool | None = None,
    publish_agent_details: bool = True,
    resolve: Any = None,
    report_events: bool | None = None,
) -> Agent:
    """
    Factory creating the Weather Specialist Agent.
    Configures seed from environment, Agentverse mailbox, and protocol manifest publication.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    agent_seed = seed or settings.weather_agent_seed
    agent_port = port or settings.weather_agent_port
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
        description="KrishiBazaar Weather Specialist: Transit Weather Hazard Forecasting",
    )
    agent.include(weather_proto, publish_manifest=publish_agent_details)
    return agent


weather_agent = create_weather_agent()

if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    print_startup_banner(
        agent_name="KrishiBazaar Weather Specialist",
        agent=weather_agent,
        port=settings.weather_agent_port,
        role="Transit Weather Hazard Forecasting (Open-Meteo)",
        extra_details={
            "Forecast API": settings.open_meteo_base_url,
            "Spoilage Risk": "Deterministic threshold buffer (Precipitation & Temperature)",
        },
    )
    weather_agent.run()
