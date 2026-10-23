"""
KrishiBazaar Dedicated Payment & Settlement Specialist Agent.
Acts as the authoritative service provider / payee in the Fetch.ai Agent Payment Protocol.
Runs the official payment_protocol_spec with role='seller'.
Handles:
- Receipt of CommitPayment over HTTP /submit
- Authoritative on-chain ledger reconciliation via CosmPy LedgerClient
- Transition to SETTLED upon verified confirmation, issuing CompletePayment
- Transition to FAILED upon confirmed on-chain errors, issuing CancelPayment
- Safe holding in AWAITING_RECONCILIATION during RPC timeout or network uncertainty
- Processing of explicit RejectPayment notifications
"""

from __future__ import annotations

import logging
from uagents import Agent, Context, Protocol

from krishibazaar.config import settings
from krishibazaar.services.payment_service import (
    PaymentService,
    create_seller_payment_protocol,
)
from krishibazaar.storage import storage

logger = logging.getLogger("krishibazaar.payment_agent")


def create_payment_agent(
    name: str = "krishibazaar_payment_agent",
    port: int | None = None,
    seed: str | None = None,
    service: PaymentService | None = None,
) -> tuple[Agent, Protocol]:
    """
    Factory creating the dedicated Payment Specialist Agent and its attached seller payment protocol.
    """
    effective_port = port or settings.payment_agent_port
    effective_seed = seed or settings.payment_payee_seed
    if not effective_seed:
        if settings.payment_environment == "testnet":
            raise ValueError(
                "Insecure Payment Agent initialization: PAYMENT_PAYEE_SEED must be configured "
                "in local .env for testnet operation to ensure secure, non-predictable wallet identity."
            )
        effective_seed = "ephemeral_local_test_seed_payment_agent"

    agent = Agent(
        name=name,
        seed=effective_seed,
        port=effective_port,
        mailbox=False,
        publish_agent_details=False,
    )

    pay_service = service or PaymentService(storage_engine=storage, settings_obj=settings)
    proto = create_seller_payment_protocol(pay_service)
    agent.include(proto)

    return agent, proto


payment_agent, payment_proto = create_payment_agent()


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
