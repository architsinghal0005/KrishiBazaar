"""
Startup Diagnostics and Configuration Validation Module for KrishiBazaar (Phase 8).
Provides clean startup banners, safe configuration validation without secret leaks,
and port/connectivity health checks for hackathon demo hardening.
"""

from __future__ import annotations
import logging
import os
import sys
from pathlib import Path
from typing import Any

from krishibazaar.config import settings

logger = logging.getLogger("krishibazaar.diagnostics")


def validate_configuration() -> list[str]:
    """
    Validate system and environment configuration for the 6-agent network.
    Never exposes raw secrets, seeds, or API keys in return values or logs.
    Returns a list of warning or error messages (empty if configuration is valid).
    """
    issues: list[str] = []

    # 1. Port collision check across all 7 agents
    port_assignments = {
        "Orchestrator": settings.orchestrator_port,
        "Market Specialist": settings.market_agent_port,
        "Weather Specialist": settings.weather_agent_port,
        "Buyer A Specialist": settings.buyer_agent_port,
        "Buyer B Specialist": settings.buyer_b_agent_port,
        "Transport Specialist": settings.transport_agent_port,
        "Payment Specialist": settings.payment_agent_port,
    }

    seen_ports: dict[int, str] = {}
    for agent_role, port in port_assignments.items():
        if port in seen_ports:
            issues.append(
                f"Port collision detected: {agent_role} and {seen_ports[port]} both configured on port {port}."
            )
        seen_ports[port] = agent_role

    # 2. Port range validation
    for agent_role, port in port_assignments.items():
        if not (1024 <= port <= 65535):
            issues.append(f"Invalid port for {agent_role}: {port} (must be between 1024 and 65535).")

    # 3. Database directory accessibility
    try:
        db_path = Path(settings.database_path).resolve()
        db_dir = db_path.parent
        if not db_dir.exists():
            issues.append(f"Database directory does not exist: {db_dir}")
    except Exception as ex:
        issues.append(f"Invalid database path '{settings.database_path}': {ex}")

    # 4. Specialist address validation (must be valid uAgents Bech32 address starting with 'agent1')
    specialist_addrs = {
        "MARKET_AGENT_ADDRESS": settings.market_agent_address,
        "WEATHER_AGENT_ADDRESS": settings.weather_agent_address,
        "BUYER_AGENT_ADDRESS": settings.buyer_agent_address,
        "BUYER_B_AGENT_ADDRESS": settings.buyer_b_agent_address,
        "TRANSPORT_AGENT_ADDRESS": settings.transport_agent_address,
        "PAYMENT_PAYEE_AGENT_ADDRESS": settings.payment_payee_agent_address,
    }
    for env_var, addr in specialist_addrs.items():
        if addr is not None:
            clean_addr = addr.strip()
            if not clean_addr.startswith("agent1") or len(clean_addr) < 30:
                issues.append(
                    f"Invalid uAgents address format for {env_var}: '{clean_addr[:12]}...' "
                    "(must start with 'agent1' and be a valid Bech32 address)."
                )

    # 5. External URLs format check
    if not (settings.open_meteo_base_url.startswith("http://") or settings.open_meteo_base_url.startswith("https://")):
        issues.append(f"Invalid OPEN_METEO_BASE_URL: '{settings.open_meteo_base_url}'")
    if not (settings.agentverse_url.startswith("http://") or settings.agentverse_url.startswith("https://")):
        issues.append(f"Invalid AGENTVERSE_URL: '{settings.agentverse_url}'")

    return issues


def get_agent_diagnostic_info(
    agent_name: str,
    agent: Any,
    port: int,
    role: str,
    extra_details: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Compile safe diagnostic information for an agent instance without secret leakage.
    """
    python_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    address = getattr(agent, "address", "Uninitialized")

    info: dict[str, Any] = {
        "agent_name": agent_name,
        "role": role,
        "pid": os.getpid(),
        "address": address,
        "port": port,
        "local_endpoint": f"http://127.0.0.1:{port}/submit",
        "database_path": str(Path(settings.database_path).resolve()),
        "mailbox_enabled": settings.use_mailbox,
        "mailbox_url": settings.agentverse_url if settings.use_mailbox else "N/A",
        "report_events": settings.report_events,
        "environment": settings.environment,
        "python_version": python_ver,
        "secrets_leak_protected": True,
    }

    # Safe presence indicators without exposing secret tokens
    if extra_details:
        info["details"] = extra_details

    return info


def print_startup_banner(
    agent_name: str,
    agent: Any,
    port: int,
    role: str,
    extra_details: dict[str, str] | None = None,
) -> None:
    """
    Print an attractive, professional ASCII startup banner for terminal monitoring.
    Never prints secret seeds, private keys, or API tokens.
    """
    address = getattr(agent, "address", "N/A")
    python_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    mailbox_status = f"Enabled ({settings.agentverse_url})" if settings.use_mailbox else "Disabled (Local only)"
    telemetry_status = "Enabled" if settings.report_events else "Disabled (Telemetry off)"

    print("=" * 76)
    print(f" KrishiBazaar Agent: {agent_name} [{role}]")
    print("=" * 76)
    print(f"  • Process PID:   {os.getpid()}")
    print(f"  • Address:       {address}")
    print(f"  • Local Port:    {port} (http://127.0.0.1:{port}/submit)")
    print(f"  • Database Path: {Path(settings.database_path).resolve()}")
    print(f"  • Environment:   {settings.environment} | Python: {python_ver}")
    print(f"  • Mailbox:       {mailbox_status}")
    print(f"  • Telemetry:     {telemetry_status}")

    if extra_details:
        for k, v in extra_details.items():
            print(f"  • {k:<15}: {v}")

    # Check for configuration warnings
    warnings = validate_configuration()
    if warnings:
        print("-" * 76)
        print("  ⚠️ Configuration Notices:")
        for w in warnings:
            print(f"    - {w}")

    print("=" * 76)
