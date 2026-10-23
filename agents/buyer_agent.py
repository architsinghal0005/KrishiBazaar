"""
Root entry point for KrishiBazaar Buyer Agent.
"""

from krishibazaar.agents.buyer_agent import (
    buyer_agent,
    buyer_proto,
    create_buyer_agent,
    handle_buyer_query,
)
from krishibazaar.config import settings

if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    print_startup_banner(
        agent_name="KrishiBazaar Buyer A Specialist",
        agent=buyer_agent,
        port=settings.buyer_agent_port,
        role="FPO & Direct Retail Procurement Bids (Simulated Benchmark)",
        extra_details={
            "Buyer Profile": "buyer_a (Farmer Producer Organizations & Retail Aggregators)",
            "Bid Model": "Simulated benchmarks with farmgate pickup / delivery options",
        },
    )
    buyer_agent.run()
