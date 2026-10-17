"""
Configuration module for KrishiBazaar.
Loads configuration from environment variables or .env file with strict Pydantic validation.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


def find_env_file() -> Path | None:
    """Find .env file traversing up from this file's directory."""
    current = Path(__file__).resolve().parent
    for _ in range(4):
        candidate = current / ".env"
        if candidate.is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


# Load .env if discovered
_env_path = find_env_file()
if _env_path:
    load_dotenv(dotenv_path=_env_path)


class Settings(BaseModel):
    """Application settings with strict validation."""

    environment: Literal["development", "staging", "production"] = Field(
        default="development", description="Runtime environment"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Application log level"
    )

    # Agent Seeds (Used for deterministic addresses in local development)
    orchestrator_seed: str = Field(
        default="krishibazaar_orchestrator_local_seed",
        min_length=8,
        description="Private seed for Orchestrator agent",
    )
    market_agent_seed: str = Field(
        default="krishibazaar_market_local_seed",
        min_length=8,
        description="Private seed for Market agent",
    )
    weather_agent_seed: str = Field(
        default="krishibazaar_weather_local_seed",
        min_length=8,
        description="Private seed for Weather agent",
    )
    buyer_agent_seed: str = Field(
        default="krishibazaar_buyer_local_seed",
        min_length=8,
        description="Private seed for Buyer agent",
    )
    transport_agent_seed: str = Field(
        default="krishibazaar_transport_local_seed",
        min_length=8,
        description="Private seed for Transport agent",
    )

    # Local Multi-Agent Ports
    orchestrator_port: int = Field(default=8000, ge=1024, le=65535)
    market_agent_port: int = Field(default=8001, ge=1024, le=65535)
    weather_agent_port: int = Field(default=8002, ge=1024, le=65535)
    buyer_agent_port: int = Field(default=8003, ge=1024, le=65535)
    transport_agent_port: int = Field(default=8004, ge=1024, le=65535)

    # Agentverse credentials (optional for local multi-agent network)
    agentverse_api_key: str | None = Field(
        default=None, description="Agentverse API key"
    )
    agentverse_url: str = Field(
        default="https://agentverse.ai", description="Agentverse platform base URL"
    )

    # External APIs
    datagov_api_key: str | None = Field(
        default=None, description="Data.gov.in Agmarknet API key"
    )
    open_meteo_base_url: str = Field(
        default="https://api.open-meteo.com/v1/forecast",
        description="Open-Meteo REST endpoint",
    )

    # SQLite database file path
    database_path: str = Field(
        default="krishibazaar.db", description="Local SQLite database file"
    )

    @field_validator("orchestrator_port", "market_agent_port", "weather_agent_port", "buyer_agent_port", "transport_agent_port")
    @classmethod
    def validate_port_range(cls, port: int) -> int:
        if not (1024 <= port <= 65535):
            raise ValueError(f"Port {port} must be within non-privileged range 1024-65535")
        return port

    @classmethod
    def load_from_env(cls) -> Settings:
        """Construct Settings instance directly from environment variables."""
        return cls(
            environment=os.getenv("ENVIRONMENT", "development"),  # type: ignore[arg-type]
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),  # type: ignore[arg-type]
            orchestrator_seed=os.getenv(
                "ORCHESTRATOR_AGENT_SEED", "krishibazaar_orchestrator_local_seed"
            ),
            market_agent_seed=os.getenv(
                "MARKET_AGENT_SEED", "krishibazaar_market_local_seed"
            ),
            weather_agent_seed=os.getenv(
                "WEATHER_AGENT_SEED", "krishibazaar_weather_local_seed"
            ),
            buyer_agent_seed=os.getenv(
                "BUYER_AGENT_SEED", "krishibazaar_buyer_local_seed"
            ),
            transport_agent_seed=os.getenv(
                "TRANSPORT_AGENT_SEED", "krishibazaar_transport_local_seed"
            ),
            orchestrator_port=int(os.getenv("ORCHESTRATOR_PORT", "8000")),
            market_agent_port=int(os.getenv("MARKET_AGENT_PORT", "8001")),
            weather_agent_port=int(os.getenv("WEATHER_AGENT_PORT", "8002")),
            buyer_agent_port=int(os.getenv("BUYER_AGENT_PORT", "8003")),
            transport_agent_port=int(os.getenv("TRANSPORT_AGENT_PORT", "8004")),
            agentverse_api_key=os.getenv("AGENTVERSE_API_KEY") or None,
            agentverse_url=os.getenv("AGENTVERSE_URL", "https://agentverse.ai"),
            datagov_api_key=os.getenv("DATAGOV_API_KEY") or None,
            open_meteo_base_url=os.getenv(
                "OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast"
            ),
            database_path=os.getenv("DATABASE_PATH", "krishibazaar.db"),
        )


# Global settings singleton
settings = Settings.load_from_env()
