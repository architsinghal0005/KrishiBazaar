"""
KrishiBazaar Buyer B Specialist Agent.
Institutional & Export Procurement Specialist Agent.
Generates structured, simulated benchmark buyer procurement bids (export aggregators, corporate agribusiness, processing units).
Strict Compliance: All offers are labeled as simulated benchmarks; never implies a real buyer transaction.
"""

from __future__ import annotations
import logging
from uagents import Agent

from krishibazaar.agents.buyer_agent import create_buyer_agent, create_buyer_protocol
from krishibazaar.config import settings

logger = logging.getLogger("krishibazaar.buyer_b_agent")

buyer_b_proto = create_buyer_protocol(profile="buyer_b")


def create_buyer_b_agent(
    name: str = "krishibazaar_buyer_b",
    seed: str | None = None,
    port: int | None = None,
    mailbox: bool | None = None,
    publish_agent_details: bool = True,
    report_events: bool | None = None,
) -> Agent:
    """
    Factory creating the Buyer B Specialist Agent (Institutional & Export Procurement).
    Configures seed from settings.buyer_b_agent_seed, port from settings.buyer_b_agent_port,
    Agentverse mailbox, and protocol manifest publication.
    """
    agent_seed = seed or settings.buyer_b_agent_seed
    agent_port = port or settings.buyer_b_agent_port

    return create_buyer_agent(
        name=name,
        seed=agent_seed,
        port=agent_port,
        mailbox=mailbox,
        publish_agent_details=publish_agent_details,
        profile="buyer_b",
        protocol=buyer_b_proto,
        report_events=report_events,
    )


buyer_b_agent = create_buyer_b_agent()

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
