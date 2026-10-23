"""
Integration tests for Fetch.ai Agent Payment Protocol subsystem (tests/test_payment_protocol_integration.py).

Tests:
1. Seller RequestPayment generation and storage.
2. Buyer RequestPayment receipt, validation, and presentation review details.
3. Human approval gate: explicit authorization vs casual text rejection.
4. Safe rejection path generating RejectPayment and updating record to REJECTED.
5. Authorized execution: whitelist enforcement, amount matching, replay defense, balance verification.
6. Authoritative on-chain settlement: verification via ledger response, event checking, SETTLED transition.
7. Settlement defense: failed or uncertain transactions never marked SETTLED.
8. Local Agent protocol message handling using official payment_protocol_spec.
9. SQLite persistence and complete isolation from booking records.
10. Secret redaction verification.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.tx_helpers import SubmittedTx, TxResponse
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.address import Address

from krishibazaar.config import Settings
from krishibazaar.models import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    ExplicitPaymentApproval,
    Funds,
    PaymentPurpose,
    PaymentRecord,
    PaymentStatus,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)
from krishibazaar.services.payment_service import (
    PaymentService,
    create_buyer_payment_protocol,
    create_seller_payment_protocol,
    parse_explicit_payment_approval,
    redact_secrets,
)
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path: Path) -> StorageEngine:
    db_file = tmp_path / "test_payment_integration.db"
    return StorageEngine(db_path=db_file)


@pytest.fixture
def test_wallets() -> tuple[LocalWallet, LocalWallet]:
    payer = LocalWallet.generate()
    payee = LocalWallet.generate()
    return payer, payee


@pytest.fixture
def enabled_settings(test_wallets: tuple[LocalWallet, LocalWallet]) -> Settings:
    payer, payee = test_wallets
    return Settings(
        enable_payment_protocol=True,
        payment_environment="sandbox",
        payment_network="testnet",
        payment_testnet_chain_id="dorado-1",
        payment_testnet_denom="atestfet",
        payment_advisory_fee_base_units=1000,
        payment_recipient_whitelist=[str(payee.address())],
    )


class MockLedger:
    """Mock testnet ledger client for isolated deterministic protocol testing."""

    def __init__(self, initial_balance: int = 1000000) -> None:
        self.balance = initial_balance
        self.sent_transactions: list[dict] = []
        self.tx_responses: dict[str, TxResponse] = {}

    def query_bank_balance(self, address: Address, denom: str | None = None) -> int:
        return self.balance

    def send_tokens(
        self,
        destination: Address,
        amount: int,
        denom: str,
        sender: LocalWallet,
        memo: str | None = None,
        fee: Any = None,
        timeout_height: Any = None,
    ) -> SubmittedTx:
        if self.balance < amount:
            raise ValueError("Insufficient balance")
        self.balance -= amount
        tx_hash = f"TX_HASH_{len(self.sent_transactions) + 1:04d}_DORADO"
        self.sent_transactions.append({
            "destination": str(destination),
            "amount": amount,
            "denom": denom,
            "sender": str(sender.address()),
            "tx_hash": tx_hash,
            "memo": memo,
        })
        # Default mock successful response
        resp = TxResponse(
            hash=tx_hash,
            height=123456,
            code=0,
            gas_wanted=100000,
            gas_used=50000,
            raw_log="",
            logs=[],
            events={
                "coin_received": {
                    "receiver": str(destination),
                    "amount": f"{amount}{denom}",
                }
            },
            timestamp=datetime.now(timezone.utc),
        )
        self.tx_responses[tx_hash] = resp
        mock_sub = MagicMock(spec=SubmittedTx)
        mock_sub.tx_hash = tx_hash
        mock_sub.response = resp
        return mock_sub

    def query_tx(self, tx_hash: str) -> TxResponse:
        if tx_hash in self.tx_responses:
            return self.tx_responses[tx_hash]
        raise ValueError(f"Tx {tx_hash} not found")


# =====================================================================
# Tests: Request Generation and Parsing
# =====================================================================

def test_create_advisory_payment_request(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings)

    msg, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
        amount_base_units=1000,
        currency="atestfet",
        purpose=PaymentPurpose.ADVISORY_PASS.value,
        payer_agent_address="agent1q_buyer",
        payer_wallet_address=str(payer.address()),
    )

    assert isinstance(msg, RequestPayment)
    assert msg.reference == record.payment_reference
    assert len(msg.accepted_funds) == 1
    assert msg.accepted_funds[0].amount == "1000"
    assert msg.accepted_funds[0].currency == "atestfet"
    assert msg.recipient == str(payee.address())
    assert record.status == PaymentStatus.REQUESTED

    # Verify persisted in DB
    retrieved = temp_db.get_payment_record(record.payment_reference)
    assert retrieved is not None
    assert retrieved.amount_base_units == 1000
    assert retrieved.status == PaymentStatus.REQUESTED


def test_receive_payment_request_and_review_details(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings)

    msg, _ = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
    )

    ok, reason, record = service.receive_payment_request(
        msg,
        payer_agent_address="agent1q_buyer",
        payer_wallet_address=str(payer.address()),
    )

    assert ok is True
    assert record is not None
    assert record.status == PaymentStatus.AWAITING_AUTHORIZATION

    review = service.format_review_details(record)
    assert review.reference == record.payment_reference
    assert review.amount_base_units == 1000
    assert "AUTHORIZE PAYMENT REF:" in review.approval_command_template
    assert str(payee.address()) in review.approval_command_template


# =====================================================================
# Tests: Explicit Approval vs Casual Affirmations
# =====================================================================

@pytest.mark.parametrize(
    "casual",
    ["ok", "yes", "sounds good", "thanks", "proceed", "yep", "sure", "fine", "hi", "ok confirm", "confirm"],
)
def test_casual_affirmations_cannot_authorize_payment(casual: str):
    res = parse_explicit_payment_approval(casual)
    assert res is None


def test_explicit_payment_approval_success():
    cmd = "AUTHORIZE PAYMENT REF:KB-PAY-ABC123XYZ AMOUNT:1000 CURRENCY:atestfet RECIPIENT:fetch1te2q3hxldxkm0ecefw3kttq0qhekxq7x7dshhm"
    appr = parse_explicit_payment_approval(cmd)
    assert appr is not None
    assert appr.reference == "KB-PAY-ABC123XYZ"
    assert appr.amount_base_units == 1000
    assert appr.currency == "atestfet"
    assert appr.recipient == "fetch1te2q3hxldxkm0ecefw3kttq0qhekxq7x7dshhm"


def test_reject_payment_request(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings)

    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
    )

    reject_msg = service.reject_payment_request(record.payment_reference, "Farmer decided not to purchase advisory pass")
    assert isinstance(reject_msg, RejectPayment)
    assert reject_msg.reason == "Farmer decided not to purchase advisory pass"

    updated = temp_db.get_payment_record(record.payment_reference)
    assert updated.status == PaymentStatus.REJECTED
    assert updated.rejection_reason == "Farmer decided not to purchase advisory pass"


# =====================================================================
# Tests: Transfer Execution Safety Gates
# =====================================================================

def test_execute_transfer_success(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    mock_ledger = MockLedger(initial_balance=5000)
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings, ledger_client=mock_ledger)  # type: ignore

    msg, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
        amount_base_units=1000,
        currency="atestfet",
    )

    approval = ExplicitPaymentApproval(
        reference=record.payment_reference,
        amount_base_units=1000,
        currency="atestfet",
        recipient=str(payee.address()),
    )

    commit_msg, updated_record = service.execute_authorized_transfer(
        approval=approval,
        payer_wallet=payer,
        ledger_client=mock_ledger,  # type: ignore
    )

    assert isinstance(commit_msg, CommitPayment)
    assert commit_msg.transaction_id.startswith("TX_HASH_")
    assert commit_msg.reference == record.payment_reference
    assert updated_record.status == PaymentStatus.COMMITTED
    assert updated_record.tx_hash == commit_msg.transaction_id

    # Verify ledger state
    assert len(mock_ledger.sent_transactions) == 1
    assert mock_ledger.balance == 4000


def test_execute_transfer_blocked_when_disabled(temp_db: StorageEngine, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    disabled_settings = Settings(enable_payment_protocol=False)
    service = PaymentService(storage_engine=temp_db, settings_obj=disabled_settings)

    approval = ExplicitPaymentApproval(
        reference="KB-PAY-REF1",
        amount_base_units=1000,
        currency="atestfet",
        recipient=str(payee.address()),
    )

    with pytest.raises(RuntimeError, match="Payment transfers are disabled by default"):
        service.execute_authorized_transfer(approval, payer)


def test_execute_transfer_blocked_when_not_whitelisted(temp_db: StorageEngine, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    unwhitelisted_settings = Settings(
        enable_payment_protocol=True,
        payment_recipient_whitelist=["fetch1_other_merchant_only"],
    )
    service = PaymentService(storage_engine=temp_db, settings_obj=unwhitelisted_settings)

    approval = ExplicitPaymentApproval(
        reference="KB-PAY-REF1",
        amount_base_units=1000,
        currency="atestfet",
        recipient=str(payee.address()),
    )

    with pytest.raises(PermissionError, match="PAYMENT_RECIPIENT_WHITELIST"):
        service.execute_authorized_transfer(approval, payer)


def test_execute_transfer_duplicate_authorization_blocked(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    mock_ledger = MockLedger(initial_balance=5000)
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings, ledger_client=mock_ledger)  # type: ignore

    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
        amount_base_units=1000,
    )

    approval = ExplicitPaymentApproval(
        reference=record.payment_reference,
        amount_base_units=1000,
        currency="atestfet",
        recipient=str(payee.address()),
    )

    # First authorization succeeds
    service.execute_authorized_transfer(approval, payer, mock_ledger)  # type: ignore

    # Duplicate authorization attempt MUST be blocked
    with pytest.raises(ValueError, match="replay or duplicate authorization blocked"):
        service.execute_authorized_transfer(approval, payer, mock_ledger)  # type: ignore


def test_execute_transfer_mismatched_amount_rejected(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    mock_ledger = MockLedger()
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings, ledger_client=mock_ledger)  # type: ignore

    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
        amount_base_units=1000,
    )

    approval = ExplicitPaymentApproval(
        reference=record.payment_reference,
        amount_base_units=500,  # Mismatched amount
        currency="atestfet",
        recipient=str(payee.address()),
    )

    with pytest.raises(ValueError, match="Amount mismatch"):
        service.execute_authorized_transfer(approval, payer, mock_ledger)  # type: ignore


# =====================================================================
# Tests: Authoritative On-Chain Settlement Verification
# =====================================================================

def test_verify_and_settle_success(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    mock_ledger = MockLedger(initial_balance=5000)
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings, ledger_client=mock_ledger)  # type: ignore

    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
        amount_base_units=1000,
    )

    approval = ExplicitPaymentApproval(
        reference=record.payment_reference,
        amount_base_units=1000,
        currency="atestfet",
        recipient=str(payee.address()),
    )

    commit_msg, _ = service.execute_authorized_transfer(approval, payer, mock_ledger)  # type: ignore

    # Synchronous verification using ledger.query_tx
    is_settled, complete, cancel, updated = service.verify_and_settle_commitment(commit_msg, mock_ledger)  # type: ignore

    assert is_settled is True
    assert isinstance(complete, CompletePayment)
    assert cancel is None
    assert updated.status == PaymentStatus.SETTLED
    assert updated.verified_at_utc is not None


@pytest.mark.asyncio
async def test_verify_and_settle_async_success(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    mock_ledger = MockLedger(initial_balance=5000)
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings, ledger_client=mock_ledger)  # type: ignore

    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
        amount_base_units=1000,
    )

    approval = ExplicitPaymentApproval(
        reference=record.payment_reference,
        amount_base_units=1000,
        currency="atestfet",
        recipient=str(payee.address()),
    )

    commit_msg, _ = service.execute_authorized_transfer(approval, payer, mock_ledger)  # type: ignore
    tx_resp = mock_ledger.tx_responses[commit_msg.transaction_id]

    from unittest.mock import patch, AsyncMock
    with patch("krishibazaar.services.payment_service.wait_for_tx_to_complete", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = tx_resp
        is_settled, complete, cancel, updated = await service.verify_and_settle_commitment_async(commit_msg, mock_ledger)  # type: ignore

    assert is_settled is True
    assert isinstance(complete, CompletePayment)
    assert cancel is None
    assert updated.status == PaymentStatus.SETTLED


def test_verify_and_settle_failed_on_chain_leads_to_cancellation(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    mock_ledger = MockLedger()
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings, ledger_client=mock_ledger)  # type: ignore

    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
    )

    commit = CommitPayment(
        funds=Funds(amount="1000", currency="atestfet"),
        recipient=str(payee.address()),
        transaction_id="FAILED_TX_999",
        reference=record.payment_reference,
    )

    failed_resp = TxResponse(
        hash="FAILED_TX_999",
        height=123,
        code=5,  # Cosmos error code
        gas_wanted=100,
        gas_used=100,
        raw_log="out of gas error",
        logs=[],
        events={},
        timestamp=datetime.now(timezone.utc),
    )
    mock_ledger.tx_responses["FAILED_TX_999"] = failed_resp

    is_settled, complete, cancel, updated = service.verify_and_settle_commitment(commit, mock_ledger)  # type: ignore

    assert is_settled is False
    assert complete is None
    assert isinstance(cancel, CancelPayment)
    assert "code 5" in cancel.reason
    assert updated.status == PaymentStatus.FAILED


def test_verify_and_settle_timeout_reconciliation_holding_state(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    mock_ledger = MockLedger()
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings, ledger_client=mock_ledger)  # type: ignore

    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_seller",
        payee_wallet_address=str(payee.address()),
    )

    commit = CommitPayment(
        funds=Funds(amount="1000", currency="atestfet"),
        recipient=str(payee.address()),
        transaction_id="TIMEOUT_TX_000",
        reference=record.payment_reference,
    )

    mock_ledger.query_tx = MagicMock(side_effect=TimeoutError("RPC timeout"))

    is_settled, complete, cancel, updated = service.verify_and_settle_commitment(commit, mock_ledger)  # type: ignore

    assert is_settled is False
    assert complete is None
    assert cancel is None
    assert updated.status == PaymentStatus.AWAITING_RECONCILIATION


# =====================================================================
# Tests: Protocol Spec & Handlers Attachment
# =====================================================================

def test_protocol_factory_and_roles(temp_db: StorageEngine, enabled_settings: Settings, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    service = PaymentService(storage_engine=temp_db, settings_obj=enabled_settings)

    buyer_proto = create_buyer_payment_protocol(service, payer)
    seller_proto = create_seller_payment_protocol(service)

    assert buyer_proto.name == "AgentPaymentProtocol"
    assert buyer_proto.version == "0.1.0"
    assert getattr(buyer_proto, "_role", None) == "buyer"

    assert seller_proto.name == "AgentPaymentProtocol"
    assert seller_proto.version == "0.1.0"
    assert getattr(seller_proto, "_role", None) == "seller"


# =====================================================================
# Tests: Storage & Booking Isolation
# =====================================================================

def test_storage_isolation_between_payments_and_bookings(temp_db: StorageEngine, test_wallets: tuple[LocalWallet, LocalWallet]):
    payer, payee = test_wallets
    rec = PaymentRecord(
        payment_reference="KB-PAY-ISO-1",
        purpose="Test Isolation",
        payer_agent_address="agent1_p",
        payer_wallet_address=str(payer.address()),
        payee_agent_address="agent1_s",
        payee_wallet_address=str(payee.address()),
        amount_base_units=1000,
        amount_display="1000 atestfet",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.REQUESTED,
        deadline_seconds=300,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        updated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    temp_db.save_payment_record(rec)

    # Bookings list must remain empty
    bookings = temp_db.list_bookings()
    assert len(bookings) == 0

    # Payments list must have 1 record
    payments = temp_db.list_payment_records()
    assert len(payments) == 1
    assert payments[0].payment_reference == "KB-PAY-ISO-1"


def test_secret_redaction_utility():
    secret_key = "0xabcdef1234567890abcdef1234567890"
    redacted = redact_secrets(secret_key)
    assert secret_key not in redacted
    assert "[REDACTED]" in redacted
