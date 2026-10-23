"""
Root entry point for KrishiBazaar Weather Agent.
"""

from krishibazaar.agents.weather_agent import (
    create_weather_agent,
    handle_weather_query,
    weather_agent,
    weather_proto,
)
from krishibazaar.config import settings

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
