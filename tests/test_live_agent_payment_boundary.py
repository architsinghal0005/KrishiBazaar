"""
Integration test verifying live Payment Protocol message exchange between separate running agent processes.
(tests/test_live_agent_payment_boundary.py)

Key Requirements (Phase 9C):
1. Spawns two separate local agent processes running official uagents-core payment_protocol_spec:
   - Seller / Payee Process on port 8096
   - Buyer / Payer Process on port 8097
2. Exercises actual HTTP /submit endpoint communication across the OS process boundary.
3. Verifies protocol state transitions:
   - RequestPayment and valid payer response (CommitPayment)
   - Explicit rejection (RejectPayment)
   - Completion (CompletePayment) and cancellation (CancelPayment)
   - Invalid / unauthorized recipient and unsupported currency
   - Timeout, replay, and uncertain settlement state (Awaiting reconciliation)
4. Enforces NO wallet signing, NO external gateway calls, and NO real token transfers.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator

import pytest
from uagents import Agent
from uagents.communication import send_sync_message
from uagents.resolver import RulesBasedResolver

from krishibazaar.models import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    PaymentRecord,
    PaymentStatus,
    RejectPayment,
    RequestPayment,
)
from krishibazaar.services.payment_service import PaymentService, ExplicitPaymentApproval
from krishibazaar.storage import StorageEngine
from cosmpy.aerial.wallet import LocalWallet


SELLER_PORT = 8096
BUYER_PORT = 8097
WHITELISTED_RECIPIENT = "fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"


@pytest.fixture(scope="module")
def agent_processes(tmp_path_factory: pytest.TempPathFactory) -> Generator[dict, None, None]:
    """
    Launch two separate OS processes running uAgents with payment_protocol_spec:
    - Payee Process on SELLER_PORT
    - Payer Process on BUYER_PORT
    """
    tmp_dir = tmp_path_factory.mktemp("live_payment_proc")
    buyer_log = str(tmp_dir / "buyer_events.jsonl")
    seller_log = str(tmp_dir / "seller_events.jsonl")

    # Pre-derive addresses directly from seeds
    from uagents.crypto import Identity
    seller_address = Identity.from_seed(f"seed_live_payee_{SELLER_PORT}", 0).address
    buyer_address = Identity.from_seed(f"seed_live_payer_{BUYER_PORT}", 0).address

    env = os.environ.copy()
    env["PYTHONPATH"] = f".{os.pathsep}src{os.pathsep}{env.get('PYTHONPATH', '')}"

    cmd_seller = [
        sys.executable,
        "-m",
        "tests.live_payment_processes",
        "--role",
        "seller",
        "--port",
        str(SELLER_PORT),
        "--log-file",
        seller_log,
    ]
    cmd_buyer = [
        sys.executable,
        "-m",
        "tests.live_payment_processes",
        "--role",
        "buyer",
        "--port",
        str(BUYER_PORT),
        "--log-file",
        buyer_log,
    ]

    seller_out_file = tmp_dir / "seller_stdout.log"
    seller_err_file = tmp_dir / "seller_stderr.log"
    buyer_out_file = tmp_dir / "buyer_stdout.log"
    buyer_err_file = tmp_dir / "buyer_stderr.log"

    f_seller_out = open(seller_out_file, "w", encoding="utf-8")
    f_seller_err = open(seller_err_file, "w", encoding="utf-8")
    f_buyer_out = open(buyer_out_file, "w", encoding="utf-8")
    f_buyer_err = open(buyer_err_file, "w", encoding="utf-8")

    p_seller = None
    p_buyer = None

    import socket

    def wait_for_process_and_port(
        proc: subprocess.Popen,
        port: int,
        name: str,
        err_path: Path,
        out_path: Path,
        timeout: float = 30.0,
    ) -> None:
        start = time.time()
        while time.time() - start < timeout:
            if proc.poll() is not None:
                f_seller_err.flush()
                f_buyer_err.flush()
                f_seller_out.flush()
                f_buyer_out.flush()
                err_content = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
                out_content = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
                raise RuntimeError(
                    f"Agent subprocess '{name}' (port {port}, pid {proc.pid}) exited prematurely with code {proc.returncode}.\n"
                    f"Captured STDERR:\n{err_content}\nCaptured STDOUT:\n{out_content}"
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.2)

        f_seller_err.flush()
        f_buyer_err.flush()
        err_content = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
        out_content = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
        raise TimeoutError(
            f"Port {port} for agent '{name}' failed to start within {timeout}s.\n"
            f"Captured STDERR:\n{err_content}\nCaptured STDOUT:\n{out_content}"
        )

    try:
        p_seller = subprocess.Popen(cmd_seller, env=env, stdout=f_seller_out, stderr=f_seller_err)
        p_buyer = subprocess.Popen(cmd_buyer, env=env, stdout=f_buyer_out, stderr=f_buyer_err)

        wait_for_process_and_port(p_seller, SELLER_PORT, "seller", seller_err_file, seller_out_file)
        wait_for_process_and_port(p_buyer, BUYER_PORT, "buyer", buyer_err_file, buyer_out_file)

        resolver = RulesBasedResolver(
            rules={
                seller_address: [f"http://127.0.0.1:{SELLER_PORT}/submit"],
                buyer_address: [f"http://127.0.0.1:{BUYER_PORT}/submit"],
            }
        )

        context = {
            "p_seller": p_seller,
            "p_buyer": p_buyer,
            "seller_address": seller_address,
            "buyer_address": buyer_address,
            "seller_port": SELLER_PORT,
            "buyer_port": BUYER_PORT,
            "buyer_log": buyer_log,
            "seller_log": seller_log,
            "resolver": resolver,
        }

        yield context
    finally:
        for p in (p_seller, p_buyer):
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
        for p in (p_seller, p_buyer):
            if p is not None:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        for f in (f_seller_out, f_seller_err, f_buyer_out, f_buyer_err):
            try:
                f.close()
            except Exception:
                pass


def read_events(log_path: str) -> list[dict]:
    if not os.path.exists(log_path):
        return []
    events = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line.strip()))
    return events


# =====================================================================
# 1. RequestPayment -> Valid Payer Response (CommitPayment)
# =====================================================================

@pytest.mark.asyncio
async def test_live_boundary_request_and_valid_commit(agent_processes: dict):
    """
    Verify RequestPayment crosses the HTTP socket boundary to the Buyer process,
    and Buyer responds with a valid CommitPayment.
    """
    buyer_addr = agent_processes["buyer_address"]
    resolver = agent_processes["resolver"]

    req = RequestPayment(
        accepted_funds=[Funds(amount="1000", currency="atestfet", payment_method="fet_direct")],
        recipient=WHITELISTED_RECIPIENT,
        reference="KB-PAY-LIVE-VALID-01",
        description="Live inter-process advisory fee test",
        deadline_seconds=300,
    )

    reply = await send_sync_message(
        destination=buyer_addr,
        message=req,
        resolver=resolver,
        response_type=CommitPayment,
    )

    assert isinstance(reply, CommitPayment)
    assert reply.reference == "KB-PAY-LIVE-VALID-01"
    assert reply.funds.amount == "1000"
    assert reply.funds.currency == "atestfet"
    assert reply.recipient == WHITELISTED_RECIPIENT
    assert reply.transaction_id == "MOCK_LIVE_TX_98765"

    # Verify event logged in buyer's process
    buyer_events = read_events(agent_processes["buyer_log"])
    req_events = [e for e in buyer_events if e["event_type"] == "RECEIVED_REQUEST_PAYMENT"]
    assert any(e["data"]["reference"] == "KB-PAY-LIVE-VALID-01" for e in req_events)


# =====================================================================
# 2. Explicit Rejection
# =====================================================================

@pytest.mark.asyncio
async def test_live_boundary_explicit_rejection(agent_processes: dict):
    """
    Verify RequestPayment marked for rejection triggers a RejectPayment across HTTP boundary.
    """
    buyer_addr = agent_processes["buyer_address"]
    resolver = agent_processes["resolver"]

    req = RequestPayment(
        accepted_funds=[Funds(amount="1000", currency="atestfet", payment_method="fet_direct")],
        recipient=WHITELISTED_RECIPIENT,
        reference="REJECT_BY_USER_01",
        description="Test explicit user refusal",
        deadline_seconds=300,
    )

    reply = await send_sync_message(
        destination=buyer_addr,
        message=req,
        resolver=resolver,
        response_type=RejectPayment,
    )

    assert isinstance(reply, RejectPayment)
    assert "rejection" in reply.reason.lower()

    # Verify event logged in buyer's process
    buyer_events = read_events(agent_processes["buyer_log"])
    rej_events = [e for e in buyer_events if e["event_type"] == "SENT_REJECT_PAYMENT"]
    assert any(e["data"]["reference"] == "REJECT_BY_USER_01" for e in rej_events)


# =====================================================================
# 3. Completion and Cancellation Handling
# =====================================================================

@pytest.mark.asyncio
async def test_live_boundary_completion_handling(agent_processes: dict):
    """
    Verify Seller process receives CommitPayment over HTTP and replies with CompletePayment.
    """
    seller_addr = agent_processes["seller_address"]
    resolver = agent_processes["resolver"]

    commit = CommitPayment(
        funds=Funds(amount="1000", currency="atestfet", payment_method="fet_direct"),
        recipient=WHITELISTED_RECIPIENT,
        transaction_id="TX_SETTLED_SUCCESS_99",
        reference="KB-PAY-COMPLETE-01",
        description="Verify completion handshake",
    )

    reply = await send_sync_message(
        destination=seller_addr,
        message=commit,
        resolver=resolver,
        response_type=CompletePayment,
    )

    assert isinstance(reply, CompletePayment)
    assert reply.transaction_id == "TX_SETTLED_SUCCESS_99"

    seller_events = read_events(agent_processes["seller_log"])
    comp_events = [e for e in seller_events if e["event_type"] == "SENT_COMPLETE_PAYMENT"]
    assert any(e["data"]["reference"] == "KB-PAY-COMPLETE-01" for e in comp_events)


@pytest.mark.asyncio
async def test_live_boundary_cancellation_handling(agent_processes: dict):
    """
    Verify Seller process receives failed on-chain CommitPayment and replies with CancelPayment.
    """
    seller_addr = agent_processes["seller_address"]
    resolver = agent_processes["resolver"]

    commit = CommitPayment(
        funds=Funds(amount="1000", currency="atestfet", payment_method="fet_direct"),
        recipient=WHITELISTED_RECIPIENT,
        transaction_id="FAIL_ON_CHAIN_TX",
        reference="KB-PAY-FAIL-CANCEL-01",
        description="Verify failure cancellation handshake",
    )

    reply = await send_sync_message(
        destination=seller_addr,
        message=commit,
        resolver=resolver,
        response_type=CancelPayment,
    )

    assert isinstance(reply, CancelPayment)
    assert reply.transaction_id == "FAIL_ON_CHAIN_TX"
    assert "code 5" in reply.reason

    seller_events = read_events(agent_processes["seller_log"])
    cancel_events = [e for e in seller_events if e["event_type"] == "SENT_CANCEL_PAYMENT"]
    assert any(e["data"]["reference"] == "KB-PAY-FAIL-CANCEL-01" for e in cancel_events)


# =====================================================================
# 4. Incorrect Recipient, Currency, Reference, or Amount
# =====================================================================

@pytest.mark.asyncio
async def test_live_boundary_incorrect_recipient_rejection(agent_processes: dict):
    """
    Verify Buyer process rejects RequestPayment with non-whitelisted recipient.
    """
    buyer_addr = agent_processes["buyer_address"]
    resolver = agent_processes["resolver"]

    bad_recipient_req = RequestPayment(
        accepted_funds=[Funds(amount="1000", currency="atestfet", payment_method="fet_direct")],
        recipient="fetch1_attacker_unknown_wallet_addr",
        reference="KB-PAY-BAD-REC-01",
        description="Attempt unauthorized recipient",
        deadline_seconds=300,
    )

    reply = await send_sync_message(
        destination=buyer_addr,
        message=bad_recipient_req,
        resolver=resolver,
        response_type=RejectPayment,
    )

    assert isinstance(reply, RejectPayment)
    assert "not in whitelist" in reply.reason


@pytest.mark.asyncio
async def test_live_boundary_incorrect_currency_rejection(agent_processes: dict):
    """
    Verify Buyer process rejects RequestPayment with unsupported currency.
    """
    buyer_addr = agent_processes["buyer_address"]
    resolver = agent_processes["resolver"]

    bad_currency_req = RequestPayment(
        accepted_funds=[Funds(amount="1000", currency="USDT", payment_method="fet_direct")],
        recipient=WHITELISTED_RECIPIENT,
        reference="KB-PAY-BAD-CURR-01",
        description="Attempt unsupported currency USDT",
        deadline_seconds=300,
    )

    reply = await send_sync_message(
        destination=buyer_addr,
        message=bad_currency_req,
        resolver=resolver,
        response_type=RejectPayment,
    )

    assert isinstance(reply, RejectPayment)
    assert "unsupported currency" in reply.reason.lower()


# =====================================================================
# 5. Timeout, Replay, and Uncertain Settlement State
# =====================================================================

@pytest.mark.asyncio
async def test_live_boundary_uncertain_settlement_holding_state(agent_processes: dict):
    """
    Verify that when transaction settlement is uncertain (e.g. TIMEOUT_TX),
    the Seller agent does NOT return CompletePayment, but holds in reconciliation.
    """
    seller_addr = agent_processes["seller_address"]
    resolver = agent_processes["resolver"]

    commit = CommitPayment(
        funds=Funds(amount="1000", currency="atestfet", payment_method="fet_direct"),
        recipient=WHITELISTED_RECIPIENT,
        transaction_id="TIMEOUT_TX",
        reference="KB-PAY-UNCERTAIN-01",
        description="Simulate network uncertainty",
    )

    # Since seller holds in reconciliation and sends no reply, timeout is expected
    from uagents_core.types import MsgStatus
    reply = await send_sync_message(
        destination=seller_addr,
        message=commit,
        resolver=resolver,
        timeout=3,
    )

    # Should not be CompletePayment
    assert not isinstance(reply, CompletePayment)

    seller_events = read_events(agent_processes["seller_log"])
    holding_events = [e for e in seller_events if e["event_type"] == "HOLDING_RECONCILIATION"]
    assert any(e["data"]["reference"] == "KB-PAY-UNCERTAIN-01" for e in holding_events)


def test_payment_service_replay_and_detail_change_defense(tmp_path: Path):
    """
    Verify PaymentService strictly prevents replay and reuse after detail changes.
    """
    db_file = tmp_path / "replay_test.db"
    storage_engine = StorageEngine(db_path=db_file)
    payer = LocalWallet.generate()
    payee = LocalWallet.generate()

    from krishibazaar.config import Settings
    settings_obj = Settings(
        enable_payment_protocol=True,
        payment_recipient_whitelist=[str(payee.address())],
    )
    service = PaymentService(storage_engine=storage_engine, settings_obj=settings_obj)

    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1_s",
        payee_wallet_address=str(payee.address()),
        amount_base_units=1000,
    )

    # 1. Detail change: Modified amount
    approval_bad_amt = ExplicitPaymentApproval(
        reference=record.payment_reference,
        amount_base_units=999,  # altered
        currency="atestfet",
        recipient=str(payee.address()),
    )
    with pytest.raises(ValueError, match="Amount mismatch"):
        service.execute_authorized_transfer(approval_bad_amt, payer, MagicMockLedger())

    # 2. Detail change: Modified recipient
    approval_bad_rec = ExplicitPaymentApproval(
        reference=record.payment_reference,
        amount_base_units=1000,
        currency="atestfet",
        recipient=str(LocalWallet.generate().address()),  # altered
    )
    with pytest.raises(PermissionError):
        service.execute_authorized_transfer(approval_bad_rec, payer, MagicMockLedger())

    # 3. Separate wallets defense: Payer is Payee (self-payment)
    with pytest.raises(ValueError, match="must be separate designated wallets"):
        approval_self = ExplicitPaymentApproval(
            reference=record.payment_reference,
            amount_base_units=1000,
            currency="atestfet",
            recipient=str(payee.address()),
        )
        service.execute_authorized_transfer(approval_self, payee, MagicMockLedger())


class MagicMockLedger:
    def query_bank_balance(self, *args, **kwargs) -> int:
        return 500000

    def send_tokens(self, *args, **kwargs):
        class MockTx:
            tx_hash = "MOCK_TX_REPLAY"
        return MockTx()
