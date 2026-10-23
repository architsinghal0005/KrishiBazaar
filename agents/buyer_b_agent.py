"""
Root entry point for KrishiBazaar Buyer B Agent (Institutional & Export Procurement).
"""

from krishibazaar.agents.buyer_b_agent import (
    buyer_b_agent,
    buyer_b_proto,
    create_buyer_b_agent,
)
from krishibazaar.config import settings

if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    print_startup_banner(
        agent_name="KrishiBazaar Buyer B Specialist",
        agent=buyer_b_agent,
        port=settings.buyer_b_agent_port,
        role="Corporate Agribusiness & Export Procurement (Simulated Benchmark)",
        extra_details={
            "Buyer Profile": "buyer_b (Export Aggregators & Agro-Processing Units)",
            "Bid Model": "Simulated bulk volume procurement offers with quality premiums",
        },
    )
    buyer_b_agent.run()
