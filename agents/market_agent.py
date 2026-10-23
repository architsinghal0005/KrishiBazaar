"""
Root entry point for KrishiBazaar Market Agent.
"""

from krishibazaar.agents.market_agent import (
    create_market_agent,
    handle_market_query,
    market_agent,
    market_proto,
)
from krishibazaar.config import settings

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
