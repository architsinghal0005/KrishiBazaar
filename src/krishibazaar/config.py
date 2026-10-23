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
    load_dotenv(dotenv_path=_env_path, override=True)

# Project root directory containing repository root (.env or pyproject.toml)
PROJECT_ROOT: Path = _env_path.parent if _env_path else Path(__file__).resolve().parents[2]
CANONICAL_DB_PATH: Path = (PROJECT_ROOT / "krishibazaar.db").resolve()


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
    buyer_b_agent_seed: str = Field(
        default="krishibazaar_buyer_b_local_seed",
        min_length=8,
        description="Private seed for Buyer B agent",
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
    buyer_b_agent_port: int = Field(default=8005, ge=1024, le=65535)
    transport_agent_port: int = Field(default=8004, ge=1024, le=65535)

    # Agentverse credentials & mailbox
    agentverse_api_key: str | None = Field(
        default=None, description="Agentverse API key"
    )
    agentverse_url: str = Field(
        default="https://agentverse.ai", description="Agentverse platform base URL"
    )
    use_mailbox: bool = Field(
        default=True, description="Enable Agentverse mailbox connection"
    )
    report_events: bool = Field(
        default=False,
        description="Enable Agentverse telemetry event reporting (default False for MVP/local execution)",
    )

    # Specialist Agent Addresses (Inter-Agent Communication)
    market_agent_address: str | None = Field(
        default=None, description="Registered uAgents address for Market agent"
    )
    weather_agent_address: str | None = Field(
        default=None, description="Registered uAgents address for Weather agent"
    )
    buyer_agent_address: str | None = Field(
        default=None, description="Registered uAgents address for Buyer A agent"
    )
    buyer_b_agent_address: str | None = Field(
        default=None, description="Registered uAgents address for Buyer B agent"
    )
    transport_agent_address: str | None = Field(
        default=None, description="Registered uAgents address for Transport agent"
    )
    allow_direct_service_fallback: bool = Field(
        default=True,
        description="Allow falling back to direct service calls when agent addresses are unconfigured (Local Dev)",
    )
    specialist_timeout_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Request timeout for specialist agent queries in seconds",
    )

    # Fetch.ai Agent Payment Protocol (Phase 9 - Safe Defaults: Disabled)
    enable_payment_protocol: bool = Field(
        default=False,
        description="Enable Fetch.ai Agent Payment Protocol (default False; real transfers deferred)",
    )
    payment_environment: Literal["disabled", "sandbox", "testnet"] = Field(
        default="disabled",
        description="Payment runtime environment (disabled, sandbox, testnet)",
    )
    payment_network: Literal["testnet", "mainnet"] = Field(
        default="testnet",
        description="CosmPy/Fetch network: testnet (dorado-1) or mainnet (fetchhub-4)",
    )
    payment_testnet_chain_id: str = Field(
        default="dorado-1",
        description="Chain ID for testnet",
    )
    payment_testnet_rpc_url: str = Field(
        default="grpc+https://grpc-dorado.fetch.ai",
        description="gRPC endpoint for testnet ledger",
    )
    payment_testnet_denom: str = Field(
        default="atestfet",
        description="Token denomination for testnet (1 FET = 10^18 atestfet)",
    )
    payment_payer_seed: str | None = Field(
        default=None,
        description="Private seed phrase for dedicated testnet payer wallet (kept local, never committed)",
    )
    payment_payee_seed: str | None = Field(
        default=None,
        description="Private seed phrase for dedicated testnet payee agent (kept local, never committed)",
    )
    payment_payee_wallet_address: str | None = Field(
        default=None,
        description="Designated testnet payee fetch1... wallet address",
    )
    payment_payee_agent_address: str | None = Field(
        default=None,
        description="Designated payee agent1... address",
    )
    payment_agent_port: int = Field(
        default=8006,
        ge=1024,
        le=65535,
        description="Port for dedicated payment specialist agent",
    )
    payment_advisory_fee_base_units: int = Field(
        default=50_000_000_000_000_000,
        ge=1,
        description="Demonstration advisory fee in token base units (default: 0.05 FET = 50,000,000,000,000,000 atestfet)",
    )
    payment_recipient_whitelist: list[str] = Field(
        default_factory=list,
        description="Explicit whitelist of verified payee addresses for payments",
    )

    # External APIs
    datagov_api_key: str | None = Field(
        default=None, description="Data.gov.in Agmarknet API key"
    )
    open_meteo_base_url: str = Field(
        default="https://api.open-meteo.com/v1/forecast",
        description="Open-Meteo REST endpoint",
    )

    # ASI:One Developer API
    asi_one_api_key: str | None = Field(
        default=None, description="ASI:One Developer API key"
    )
    asi_one_base_url: str = Field(
        default="https://api.asi1.ai/v1",
        description="ASI:One OpenAI-compatible base URL",
    )
    asi_one_model: str = Field(
        default="asi1-mini",
        description="ASI:One model name for intent extraction",
    )
    asi_one_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=120.0,
        description="HTTP request timeout for ASI:One API in seconds",
    )

    # SQLite database file path (strictly canonical and project-root absolute)
    database_path: str = Field(
        default_factory=lambda: str(CANONICAL_DB_PATH),
        description="Local SQLite database file path (canonical absolute path)",
    )

    @field_validator("database_path", mode="before")
    @classmethod
    def resolve_canonical_database_path(cls, path: Any) -> str:
        if not path:
            return str(CANONICAL_DB_PATH)
        p = Path(str(path))
        if not p.is_absolute():
            p = (PROJECT_ROOT / p).resolve()
        return str(p)

    @field_validator(
        "orchestrator_port",
        "market_agent_port",
        "weather_agent_port",
        "buyer_agent_port",
        "buyer_b_agent_port",
        "transport_agent_port",
    )
    @classmethod
    def validate_port_range(cls, port: int) -> int:
        if not (1024 <= port <= 65535):
            raise ValueError(f"Port {port} must be within non-privileged range 1024-65535")
        return port

    @classmethod
    def load_from_env(cls) -> Settings:
        """Construct Settings instance directly from environment variables."""
        env_file = find_env_file()
        if env_file:
            load_dotenv(dotenv_path=env_file, override=True)
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
            buyer_b_agent_seed=os.getenv(
                "BUYER_B_AGENT_SEED", "krishibazaar_buyer_b_local_seed"
            ),
            transport_agent_seed=os.getenv(
                "TRANSPORT_AGENT_SEED", "krishibazaar_transport_local_seed"
            ),
            orchestrator_port=int(os.getenv("ORCHESTRATOR_PORT", "8000")),
            market_agent_port=int(os.getenv("MARKET_AGENT_PORT", "8001")),
            weather_agent_port=int(os.getenv("WEATHER_AGENT_PORT", "8002")),
            buyer_agent_port=int(os.getenv("BUYER_AGENT_PORT", "8003")),
            buyer_b_agent_port=int(os.getenv("BUYER_B_AGENT_PORT", "8005")),
            transport_agent_port=int(os.getenv("TRANSPORT_AGENT_PORT", "8004")),
            agentverse_api_key=os.getenv("AGENTVERSE_API_KEY") or None,
            agentverse_url=os.getenv("AGENTVERSE_URL", "https://agentverse.ai"),
            use_mailbox=os.getenv("USE_MAILBOX", "true").lower() in ("true", "1", "yes"),
            report_events=os.getenv("REPORT_EVENTS", "false").lower() in ("true", "1", "yes"),
            datagov_api_key=os.getenv("DATAGOV_API_KEY") or None,
            open_meteo_base_url=os.getenv(
                "OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast"
            ),
            asi_one_api_key=os.getenv("ASI_ONE_API_KEY") or None,
            asi_one_base_url=os.getenv("ASI_ONE_BASE_URL", "https://api.asi1.ai/v1"),
            asi_one_model=os.getenv("ASI_ONE_MODEL", "asi1-mini"),
            asi_one_timeout_seconds=float(os.getenv("ASI_ONE_TIMEOUT_SECONDS", "15.0")),
            database_path=os.getenv("DATABASE_PATH", str(CANONICAL_DB_PATH)),
            market_agent_address=os.getenv("MARKET_AGENT_ADDRESS") or None,
            weather_agent_address=os.getenv("WEATHER_AGENT_ADDRESS") or None,
            buyer_agent_address=os.getenv("BUYER_AGENT_ADDRESS") or None,
            buyer_b_agent_address=os.getenv("BUYER_B_AGENT_ADDRESS") or None,
            transport_agent_address=os.getenv("TRANSPORT_AGENT_ADDRESS") or None,
            allow_direct_service_fallback=os.getenv(
                "ALLOW_DIRECT_SERVICE_FALLBACK", "true"
            ).lower() in ("true", "1", "yes"),
            specialist_timeout_seconds=float(
                os.getenv("SPECIALIST_TIMEOUT_SECONDS", "10.0")
            ),
            enable_payment_protocol=os.getenv(
                "ENABLE_PAYMENT_PROTOCOL", "false"
            ).lower() in ("true", "1", "yes"),
            payment_environment=os.getenv("PAYMENT_ENVIRONMENT", "disabled"),  # type: ignore[arg-type]
            payment_network=os.getenv("PAYMENT_NETWORK", "testnet"),  # type: ignore[arg-type]
            payment_testnet_chain_id=os.getenv("PAYMENT_TESTNET_CHAIN_ID", "dorado-1"),
            payment_testnet_rpc_url=os.getenv(
                "PAYMENT_TESTNET_RPC_URL", "grpc+https://grpc-dorado.fetch.ai"
            ),
            payment_testnet_denom=os.getenv("PAYMENT_TESTNET_DENOM", "atestfet"),
            payment_payer_seed=os.getenv("PAYMENT_PAYER_SEED") or None,
            payment_payee_seed=os.getenv("PAYMENT_PAYEE_SEED") or None,
            payment_payee_wallet_address=os.getenv("PAYMENT_PAYEE_WALLET_ADDRESS") or None,
            payment_payee_agent_address=os.getenv("PAYMENT_PAYEE_AGENT_ADDRESS") or None,
            payment_agent_port=int(os.getenv("PAYMENT_AGENT_PORT", "8006")),
            payment_advisory_fee_base_units=int(
                os.getenv("PAYMENT_ADVISORY_FEE_BASE_UNITS", "50000000000000000")
            ),
            payment_recipient_whitelist=[
                addr.strip()
                for addr in os.getenv("PAYMENT_RECIPIENT_WHITELIST", "").split(",")
                if addr.strip()
            ],
        )


# Global settings singleton
settings = Settings.load_from_env()


def reload_settings() -> Settings:
    """Reload global settings singleton from environment / .env file."""
    global settings
    settings = Settings.load_from_env()
    return settings

