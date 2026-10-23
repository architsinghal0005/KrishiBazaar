"""
Root entry point for KrishiBazaar Transport Agent.
"""

from krishibazaar.agents.transport_agent import (
    create_transport_agent,
    handle_transport_query,
    transport_agent,
    transport_proto,
)
from krishibazaar.config import settings

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
