"""
Live agent subprocess runner for Phase 9C Payment Protocol integration testing.
Runs a standalone uAgents agent process with official payment_protocol_spec attached
in either 'buyer' (payer) or 'seller' (payee) role.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import uagents.network
uagents.network.AlmanacContract.check_version = lambda self: False

from uagents import Agent, Context, Protocol
from krishibazaar.models import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    PaymentStatus,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("live_payment_process")


def append_event(log_file: str | None, event_type: str, data: dict) -> None:
    if not log_file:
        return
    entry = {
        "timestamp": time.time(),
        "event_type": event_type,
        "pid": os.getpid(),
        "data": data,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def run_buyer_agent(port: int, log_file: str | None) -> None:
    agent = Agent(
        name=f"live_payer_{port}",
        seed=f"seed_live_payer_{port}",
        port=port,
        mailbox=False,
        publish_agent_details=False,
        report_events=False,
        enable_agent_inspector=False,
    )

    proto = Protocol(spec=payment_protocol_spec, role="buyer")

    @proto.on_message(RequestPayment)
    async def handle_request_payment(ctx: Context, sender: str, msg: RequestPayment) -> None:
        logger.info(f"[BUYER] Received RequestPayment for {msg.reference} from {sender}")
        funds = msg.accepted_funds[0] if msg.accepted_funds else None

        append_event(
            log_file,
            "RECEIVED_REQUEST_PAYMENT",
            {
                "sender": sender,
                "reference": msg.reference,
                "amount": funds.amount if funds else None,
                "currency": funds.currency if funds else None,
                "recipient": msg.recipient,
            },
        )

        # 1. Currency validation
        if not funds or funds.currency != "atestfet":
            reason = f"Unsupported currency: {funds.currency if funds else 'none'}"
            logger.warning(f"[BUYER] Rejecting: {reason}")
            await ctx.send(sender, RejectPayment(reason=reason))
            append_event(log_file, "SENT_REJECT_PAYMENT", {"reason": reason, "reference": msg.reference})
            return

        # 2. Recipient whitelist validation
        whitelisted_recipients = {"fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"}
        if msg.recipient not in whitelisted_recipients:
            reason = f"Recipient {msg.recipient} not in whitelist"
            logger.warning(f"[BUYER] Rejecting: {reason}")
            await ctx.send(sender, RejectPayment(reason=reason))
            append_event(log_file, "SENT_REJECT_PAYMENT", {"reason": reason, "reference": msg.reference})
            return

        # 3. Explicit rejection test reference
        if msg.reference and msg.reference.startswith("REJECT_"):
            reason = "Explicit user rejection via safety gate"
            logger.info(f"[BUYER] Explicitly rejecting: {reason}")
            await ctx.send(sender, RejectPayment(reason=reason))
            append_event(log_file, "SENT_REJECT_PAYMENT", {"reason": reason, "reference": msg.reference})
            return

        # 4. Valid response: mock commit (no wallet signing, no real tx)
        tx_id = "FAIL_ON_CHAIN_TX" if msg.reference == "TEST_FAIL_SETTLE" else (
            "TIMEOUT_TX" if msg.reference == "TEST_TIMEOUT_SETTLE" else "MOCK_LIVE_TX_98765"
        )
        commit = CommitPayment(
            funds=funds,
            recipient=msg.recipient,
            transaction_id=tx_id,
            reference=msg.reference,
            description="Live integration test commitment",
        )
        logger.info(f"[BUYER] Sending CommitPayment for {msg.reference} with tx={tx_id}")
        await ctx.send(sender, commit)
        append_event(
            log_file,
            "SENT_COMMIT_PAYMENT",
            {
                "reference": msg.reference,
                "transaction_id": tx_id,
                "recipient": msg.recipient,
            },
        )

    @proto.on_message(CompletePayment)
    async def handle_complete_payment(ctx: Context, sender: str, msg: CompletePayment) -> None:
        logger.info(f"[BUYER] Received CompletePayment for tx={msg.transaction_id}")
        append_event(
            log_file,
            "RECEIVED_COMPLETE_PAYMENT",
            {
                "sender": sender,
                "transaction_id": msg.transaction_id,
            },
        )

    @proto.on_message(CancelPayment)
    async def handle_cancel_payment(ctx: Context, sender: str, msg: CancelPayment) -> None:
        logger.warning(f"[BUYER] Received CancelPayment: {msg.reason}")
        append_event(
            log_file,
            "RECEIVED_CANCEL_PAYMENT",
            {
                "sender": sender,
                "reason": msg.reason,
                "transaction_id": msg.transaction_id,
            },
        )

    agent.include(proto)
    logger.info(f"[BUYER] Agent starting on port {port}, address: {agent.address}")
    agent.run()


def run_seller_agent(port: int, log_file: str | None) -> None:
    agent = Agent(
        name=f"live_payee_{port}",
        seed=f"seed_live_payee_{port}",
        port=port,
        mailbox=False,
        publish_agent_details=False,
        report_events=False,
        enable_agent_inspector=False,
    )

    proto = Protocol(spec=payment_protocol_spec, role="seller")

    @proto.on_message(CommitPayment)
    async def handle_commit_payment(ctx: Context, sender: str, msg: CommitPayment) -> None:
        logger.info(f"[SELLER] Received CommitPayment for {msg.reference}, tx={msg.transaction_id}")
        append_event(
            log_file,
            "RECEIVED_COMMIT_PAYMENT",
            {
                "sender": sender,
                "reference": msg.reference,
                "transaction_id": msg.transaction_id,
                "amount": msg.funds.amount,
                "currency": msg.funds.currency,
                "recipient": msg.recipient,
            },
        )

        # Failure case: on-chain execution failure
        if msg.transaction_id == "FAIL_ON_CHAIN_TX":
            logger.warning(f"[SELLER] Simulating on-chain failure code 5 for {msg.reference}")
            cancel = CancelPayment(
                transaction_id=msg.transaction_id,
                reason="On-chain ledger code 5: out of gas / execution failed",
            )
            await ctx.send(sender, cancel)
            append_event(
                log_file,
                "SENT_CANCEL_PAYMENT",
                {"reference": msg.reference, "reason": cancel.reason},
            )
            return

        # Timeout / Uncertain case: do not respond with Complete; hold in reconciliation
        if msg.transaction_id == "TIMEOUT_TX":
            logger.info(f"[SELLER] Simulating timeout for {msg.reference}; holding in reconciliation")
            append_event(
                log_file,
                "HOLDING_RECONCILIATION",
                {"reference": msg.reference, "reason": "RPC timeout / ledger query timeout"},
            )
            return

        # Success case: verified on-chain -> send CompletePayment
        logger.info(f"[SELLER] Verified on-chain for {msg.reference}. Sending CompletePayment.")
        complete = CompletePayment(transaction_id=msg.transaction_id)
        await ctx.send(sender, complete)
        append_event(
            log_file,
            "SENT_COMPLETE_PAYMENT",
            {"reference": msg.reference, "transaction_id": msg.transaction_id},
        )

    @proto.on_message(RejectPayment)
    async def handle_reject_payment(ctx: Context, sender: str, msg: RejectPayment) -> None:
        logger.info(f"[SELLER] Received RejectPayment for {msg.reference}: {msg.reason}")
        append_event(
            log_file,
            "RECEIVED_REJECT_PAYMENT",
            {"sender": sender, "reference": msg.reference, "reason": msg.reason},
        )

    agent.include(proto)
    logger.info(f"[SELLER] Agent starting on port {port}, address: {agent.address}")
    agent.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live Payment Agent Process")
    parser.add_argument("--role", required=True, choices=["buyer", "seller"])
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--log-file", default=None)
    args = parser.parse_args()

    if args.role == "buyer":
        run_buyer_agent(args.port, args.log_file)
    else:
        run_seller_agent(args.port, args.log_file)
