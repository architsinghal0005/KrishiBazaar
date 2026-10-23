"""
KrishiBazaar Orchestrator Agent Entry Point.
Exports the orchestrator agent and allows running directly via `python agents/orchestrator.py`.
"""

from krishibazaar.agents.orchestrator import (
    orchestrator_agent,
    create_orchestrator_agent,
    chat_proto,
    specialist_response_proto,
    build_greeting_response,
    format_mandi_market_report,
    format_weather_report,
    format_optimization_report,
    coordinate_specialists_async,
    query_specialist_agent_async,
)
from krishibazaar.config import settings


if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    specialist_summary = {
        "Chat Protocol": "AgentChatProtocol (ASI:One Native ACP V1)",
        "Intent Engine": (
            f"ASI:One ({settings.asi_one_model})"
            if settings.asi_one_api_key
            else "Rule-based fallback parser (ASI_ONE_API_KEY not configured)"
        ),
        "Market Agent": (
            f"Configured ({settings.market_agent_address[:16]}...) [Port {settings.market_agent_port}]"
            if settings.market_agent_address
            else "Unconfigured (Direct fallback enabled)"
        ),
        "Weather Agent": (
            f"Configured ({settings.weather_agent_address[:16]}...) [Port {settings.weather_agent_port}]"
            if settings.weather_agent_address
            else "Unconfigured (Direct fallback enabled)"
        ),
        "Buyer Agents": (
            f"Configured (A: Port {settings.buyer_agent_port}, B: Port {settings.buyer_b_agent_port})"
            if (settings.buyer_agent_address or settings.buyer_b_agent_address)
            else "Unconfigured (Direct fallback enabled)"
        ),
        "Transport Agent": (
            f"Configured ({settings.transport_agent_address[:16]}...) [Port {settings.transport_agent_port}]"
            if settings.transport_agent_address
            else "Unconfigured (Direct fallback enabled)"
        ),
        "Local Fallback": (
            "Enabled (graceful partial-failure resilience)"
            if settings.allow_direct_service_fallback
            else "Disabled (strict remote agent messaging required)"
        ),
    }

    print_startup_banner(
        agent_name="KrishiBazaar Orchestrator Agent",
        agent=orchestrator_agent,
        port=settings.orchestrator_port,
        role="ASI:One Conversational Multi-Agent Farm-to-Market Gateway",
        extra_details=specialist_summary,
    )
    orchestrator_agent.run()
