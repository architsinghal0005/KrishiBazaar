"""
KrishiBazaar Payment Specialist Agent Forwarding Launcher.
Canonical specialist implementation and execution logic resides in:
`src/krishibazaar/agents/payment_agent.py` (Port 8006).
This entry point executes the single canonical Payment Specialist instance.
"""

from krishibazaar.agents.payment_agent import (
    payment_agent,
    create_payment_agent,
    payment_proto,
)
from krishibazaar.config import settings


if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    print_startup_banner(
        agent_name="KrishiBazaar Payment & Settlement Specialist",
        agent=payment_agent,
        port=settings.payment_agent_port,
        role="Fetch.ai Payment Protocol Payee / Settlement Provider",
        extra_details={
            "Environment": settings.payment_environment,
            "Network": settings.payment_testnet_chain_id,
            "Denomination": settings.payment_testnet_denom,
            "Advisory Fee": f"{settings.payment_advisory_fee_base_units} {settings.payment_testnet_denom}",
            "Protocol Status": "ENABLED" if settings.enable_payment_protocol else "DISABLED (Safety Default)",
        },
    )
    payment_agent.run()
