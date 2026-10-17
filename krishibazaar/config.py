"""
Configuration module for KrishiBazaar.
Loads configuration from environment variables or .env file with safe defaults.
"""

from __future__ import annotations
import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Ensure local .env is loaded if present
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("ENVIRONMENT", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Agent Seeds (Used for deterministic addresses in development)
    orchestrator_seed: str = os.getenv(
        "ORCHESTRATOR_AGENT_SEED", "krishibazaar_orchestrator_local_seed"
    )
    market_agent_seed: str = os.getenv(
        "MARKET_AGENT_SEED", "krishibazaar_market_local_seed"
    )
    weather_agent_seed: str = os.getenv(
        "WEATHER_AGENT_SEED", "krishibazaar_weather_local_seed"
    )
    buyer_agent_seed: str = os.getenv(
        "BUYER_AGENT_SEED", "krishibazaar_buyer_local_seed"
    )
    transport_agent_seed: str = os.getenv(
        "TRANSPORT_AGENT_SEED", "krishibazaar_transport_local_seed"
    )

    # Local Multi-Agent Ports
    orchestrator_port: int = int(os.getenv("ORCHESTRATOR_PORT", "8000"))
    market_agent_port: int = int(os.getenv("MARKET_AGENT_PORT", "8001"))
    weather_agent_port: int = int(os.getenv("WEATHER_AGENT_PORT", "8002"))
    buyer_agent_port: int = int(os.getenv("BUYER_AGENT_PORT", "8003"))
    transport_agent_port: int = int(os.getenv("TRANSPORT_AGENT_PORT", "8004"))

    # Agentverse credentials (optional for local multi-agent network)
    agentverse_api_key: str | None = os.getenv("AGENTVERSE_API_KEY") or None
    agentverse_url: str = os.getenv("AGENTVERSE_URL", "https://agentverse.ai")

    # External APIs
    datagov_api_key: str | None = os.getenv("DATAGOV_API_KEY") or None
    open_meteo_base_url: str = os.getenv(
        "OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast"
    )

    # SQLite database file path
    database_path: str = os.getenv("DATABASE_PATH", "krishibazaar.db")


# Global singleton instance
settings = Settings()
