"""
KrishiBazaar Agent Network package.
Houses autonomous uAgents: Orchestrator, Market, Weather, Buyer, and Transport agents.
Uses lazy loading for module-level agent instances to prevent process-wide dispatcher
collision when specialist agents run as separate operating system processes.
"""

__all__ = [
    "create_orchestrator_agent",
    "orchestrator_agent",
    "create_market_agent",
    "market_agent",
    "create_weather_agent",
    "weather_agent",
    "create_buyer_agent",
    "buyer_agent",
    "create_buyer_b_agent",
    "buyer_b_agent",
    "create_transport_agent",
    "transport_agent",
    "print_startup_banner",
    "validate_configuration",
    "get_agent_diagnostic_info",
]


def __getattr__(name: str):
    if name in ("create_orchestrator_agent", "orchestrator_agent"):
        import krishibazaar.agents.orchestrator as mod

        return getattr(mod, name)
    elif name in ("create_market_agent", "market_agent"):
        import krishibazaar.agents.market_agent as mod

        return getattr(mod, name)
    elif name in ("create_weather_agent", "weather_agent"):
        import krishibazaar.agents.weather_agent as mod

        return getattr(mod, name)
    elif name in ("create_buyer_agent", "buyer_agent"):
        import krishibazaar.agents.buyer_agent as mod

        return getattr(mod, name)
    elif name in ("create_buyer_b_agent", "buyer_b_agent"):
        import krishibazaar.agents.buyer_b_agent as mod

        return getattr(mod, name)
    elif name in ("create_transport_agent", "transport_agent"):
        import krishibazaar.agents.transport_agent as mod

        return getattr(mod, name)
    elif name in ("print_startup_banner", "validate_configuration", "get_agent_diagnostic_info"):
        import krishibazaar.agents.diagnostics as mod

        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
