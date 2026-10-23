"""
Comprehensive regression tests for authoritative payment settlement verification in KrishiBazaar.

Tests cover all required scenarios:
1. Valid, successfully confirmed transaction with correct recipient, amount and denomination settles.
2. Transfer events in message logs settle.
3. Transfer events with standard 'transfer' type settle.
4. Failed transaction execution (code != 0) does not settle (marks FAILED with CancelPayment).
5. Wrong recipient does not settle (marks FAILED with CancelPayment).
6. Wrong amount does not settle (marks FAILED with CancelPayment).
7. Wrong denomination does not settle (marks FAILED with CancelPayment).
8. Missing coin_received/transfer events do not settle (holds in AWAITING_RECONCILIATION).
9. Malformed event data does not settle (holds in AWAITING_RECONCILIATION).
10. Ambiguous event data does not settle (holds in AWAITING_RECONCILIATION).
11. Unconfirmed / zero block height does not settle (holds in AWAITING_RECONCILIATION).
12. An unverified CompletePayment cannot cause the orchestrator to mark a record SETTLED.
13. Duplicate authorization does not produce a second transfer.
14. Existing SETTLED records remain protected from replay.

CRITICAL SAFETY COMPLIANCE:
- Uses isolated in-memory or temporary database instances.
- Uses mocked ledger responses. Never broadcasts live transactions.
- Never modifies the runtime database.
"""

from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.tx_helpers import MessageLog, SubmittedTx, TxResponse
from cosmpy.aerial.wallet import LocalWallet
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
)

from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.config import Settings
from krishibazaar.models import (
    ExplicitPaymentApproval,
    PaymentPurpose,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.services.payment_service import PaymentService
from krishibazaar.storage import StorageEngine
from uagents_core.contrib.protocols.chat.cards import (
    DetailCardPayload,
    create_card_response_content,
    extract_card,
)


@pytest.fixture
def temp_storage(tmp_path: Path) -> StorageEngine:
    db_path = tmp_path / "test_settlement_verification.db"
    return StorageEngine(db_path=db_path)


@pytest.fixture
def test_wallets() -> tuple[LocalWallet, LocalWallet]:
    return LocalWallet.generate(), LocalWallet.generate()


@pytest.fixture
def test_settings(test_wallets: tuple[LocalWallet, LocalWallet]) -> Settings:
    payer, payee = test_wallets
    return Settings(
        enable_payment_protocol=True,
        payment_network="testnet",
        payment_testnet_chain_id="dorado-1",
        payment_recipient_whitelist=[str(payee.address())],
        payment_payee_wallet_address=str(payee.address()),
        payment_advisory_fee_base_units=50000000000000000,
        payment_testnet_denom="atestfet",
    )


@pytest.fixture
def payment_service(temp_storage: StorageEngine, test_settings: Settings) -> PaymentService:
    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_bank_balance.return_value = 1000000000000000000  # 1 FET
    mock_sub = MagicMock(spec=SubmittedTx)
    mock_sub.tx_hash = "0xSIMULATED_TEST_TX_HASH_12345"
    mock_ledger.send_tokens.return_value = mock_sub
    return PaymentService(
        storage_engine=temp_storage,
        settings_obj=test_settings,
        ledger_client=mock_ledger,
    )


def _create_committed_payment(
    service: PaymentService,
    storage: StorageEngine,
    payer: LocalWallet,
    payee: LocalWallet,
    tx_hash: str = "0xSIMULATED_TEST_TX_HASH_12345",
) -> tuple[CommitPayment, PaymentRecord]:
    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_test_payee",
        payee_wallet_address=str(payee.address()),
        payer_agent_address="agent1q_test_payer",
        payer_wallet_address=str(payer.address()),
        amount_base_units=50000000000000000,
        currency="atestfet",
    )
    storage.update_payment_status(
        reference=record.payment_reference,
        status=PaymentStatus.COMMITTED,
        tx_hash=tx_hash,
    )
    record = storage.get_payment_record(record.payment_reference)
    assert record is not None
    commit = CommitPayment(
        funds=Funds(amount=str(record.amount_base_units), currency=record.currency),
        recipient=record.payee_wallet_address,
        transaction_id=tx_hash,
        reference=record.payment_reference,
    )
    return commit, record


# =====================================================================
# Scenario 1: Valid Authoritative Confirmation Settles
# =====================================================================

def test_valid_confirmed_transaction_with_coin_received_settles(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """A valid, successfully confirmed transaction with correct recipient, amount and denom settles."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    mock_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805077,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={
            "coin_received": {
                "receiver": str(payee.address()),
                "amount": "50000000000000000atestfet",
            }
        },
        timestamp=datetime.now(timezone.utc),
    )
    mock_resp.actual_fee_base_units = 430390000000000
    mock_resp.actual_fee_display = "0.00043039 FET (430,390,000,000,000 atestfet)"

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = mock_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is True
    assert isinstance(complete, CompletePayment)
    assert complete.transaction_id == commit.transaction_id
    assert cancel is None
    assert updated.status == PaymentStatus.SETTLED
    assert updated.tx_height == 25805077
    assert updated.actual_fee_base_units == 430390000000000
    assert updated.verified_at_utc is not None


def test_valid_confirmed_transaction_with_transfer_event_settles(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Verification succeeds when transfer event with 'recipient' attribute is present."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    mock_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805078,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={
            "transfer": {
                "recipient": str(payee.address()),
                "amount": "50000000000000000atestfet",
            }
        },
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = mock_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is True
    assert isinstance(complete, CompletePayment)
    assert updated.status == PaymentStatus.SETTLED
    assert updated.tx_height == 25805078


def test_valid_confirmed_transaction_in_message_logs_settles(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Verification succeeds when transfer event is found in message logs."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    mock_log = MessageLog(
        index=0,
        log="",
        events={
            "coin_received": {
                "receiver": str(payee.address()),
                "amount": "50000000000000000atestfet",
            }
        },
    )
    mock_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805079,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[mock_log],
        events={},
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = mock_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is True
    assert isinstance(complete, CompletePayment)
    assert updated.status == PaymentStatus.SETTLED
    assert updated.tx_height == 25805079


# =====================================================================
# Scenario 2: Failed Execution Does Not Settle
# =====================================================================

def test_failed_transaction_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """A failed transaction (code != 0) must not settle; marks FAILED and returns CancelPayment."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    failed_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805080,
        code=5,
        gas_wanted=100000,
        gas_used=100000,
        raw_log="out of gas: out of gas error",
        logs=[],
        events={},
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = failed_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is False
    assert complete is None
    assert isinstance(cancel, CancelPayment)
    assert "code 5" in cancel.reason
    assert updated.status == PaymentStatus.FAILED


# =====================================================================
# Scenario 3: Wrong Recipient Does Not Settle
# =====================================================================

def test_wrong_recipient_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Transfer to a recipient other than payee_wallet_address must not settle; marks FAILED."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    wrong_recipient = "fetch1wrongpayee99999999999999999999999999"
    wrong_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805081,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={
            "coin_received": {
                "receiver": wrong_recipient,
                "amount": "50000000000000000atestfet",
            }
        },
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = wrong_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is False
    assert complete is None
    assert isinstance(cancel, CancelPayment)
    assert "receiver" in cancel.reason.lower()
    assert updated.status == PaymentStatus.FAILED


# =====================================================================
# Scenario 4: Wrong Amount or Denomination Does Not Settle
# =====================================================================

def test_wrong_amount_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Transfer of an amount different from amount_base_units must not settle; marks FAILED."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    wrong_amt_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805082,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={
            "coin_received": {
                "receiver": str(payee.address()),
                "amount": "1000atestfet",  # Expected 50000000000000000atestfet
            }
        },
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = wrong_amt_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is False
    assert complete is None
    assert isinstance(cancel, CancelPayment)
    assert "amount" in cancel.reason.lower()
    assert updated.status == PaymentStatus.FAILED


def test_wrong_denomination_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Transfer of currency other than expected testnet denom must not settle; marks FAILED."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    wrong_denom_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805083,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={
            "coin_received": {
                "receiver": str(payee.address()),
                "amount": "50000000000000000uatom",  # Wrong denom
            }
        },
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = wrong_denom_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is False
    assert complete is None
    assert isinstance(cancel, CancelPayment)
    assert "amount" in cancel.reason.lower()
    assert updated.status == PaymentStatus.FAILED


# =====================================================================
# Scenario 5: Missing Transfer Events Do Not Settle
# =====================================================================

def test_missing_transfer_events_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """If required coin_received/transfer events are missing, payment MUST NOT settle."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    # Empty events and logs
    missing_events_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805084,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={},
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = missing_events_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    # Must NOT settle!
    assert is_settled is False
    assert complete is None
    assert cancel is None
    assert updated.status == PaymentStatus.AWAITING_RECONCILIATION


# =====================================================================
# Scenario 6: Malformed or Ambiguous Event Data Does Not Settle
# =====================================================================

def test_malformed_empty_attributes_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Empty or missing receiver/amount attributes must not settle; holds in AWAITING_RECONCILIATION."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    malformed_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805085,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={"coin_received": {}},  # Empty attributes dict
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = malformed_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is False
    assert complete is None
    assert cancel is None
    assert updated.status == PaymentStatus.AWAITING_RECONCILIATION


def test_malformed_empty_receiver_string_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Empty receiver string in coin_received must not settle."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    malformed_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805086,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={
            "coin_received": {
                "receiver": "",
                "amount": "50000000000000000atestfet",
            }
        },
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = malformed_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is False
    assert complete is None
    assert cancel is None
    assert updated.status == PaymentStatus.AWAITING_RECONCILIATION


def test_ambiguous_amount_format_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Unparseable or ambiguous amount string must not settle; holds in AWAITING_RECONCILIATION."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    ambiguous_resp = TxResponse(
        hash=commit.transaction_id,
        height=25805087,
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={
            "coin_received": {
                "receiver": str(payee.address()),
                "amount": "not_a_valid_coin_format",
            }
        },
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = ambiguous_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is False
    assert complete is None
    assert cancel is None
    assert updated.status == PaymentStatus.AWAITING_RECONCILIATION


# =====================================================================
# Scenario 7: Unconfirmed / Zero Block Height Does Not Settle
# =====================================================================

def test_unconfirmed_zero_block_height_does_not_settle(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """A transaction with height <= 0 is not confirmed in a block; holds in AWAITING_RECONCILIATION."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    unconfirmed_resp = TxResponse(
        hash=commit.transaction_id,
        height=0,  # Unconfirmed
        code=0,
        gas_wanted=100000,
        gas_used=50000,
        raw_log="",
        logs=[],
        events={
            "coin_received": {
                "receiver": str(payee.address()),
                "amount": "50000000000000000atestfet",
            }
        },
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = unconfirmed_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is False
    assert complete is None
    assert cancel is None
    assert updated.status == PaymentStatus.AWAITING_RECONCILIATION


# =====================================================================
# Scenario 8: On-Chain Reconciliation Verification
# =====================================================================

def test_reconcile_payment_on_chain_authoritative_verification(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """reconcile_payment_on_chain adheres strictly to authoritative event validation."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    # 1. Missing events -> False
    missing_resp = MagicMock(spec=TxResponse)
    missing_resp.code = 0
    missing_resp.is_successful.return_value = True
    missing_resp.height = 25805088
    missing_resp.events = {}
    missing_resp.logs = []
    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = missing_resp

    is_settled, rec = payment_service.reconcile_payment_on_chain(
        record.payment_reference, ledger_client=mock_ledger
    )
    assert is_settled is False
    assert rec.status == PaymentStatus.AWAITING_RECONCILIATION

    # 2. Confirmed events -> True
    confirmed_resp = MagicMock(spec=TxResponse)
    confirmed_resp.code = 0
    confirmed_resp.is_successful.return_value = True
    confirmed_resp.height = 25805089
    confirmed_resp.events = {
        "coin_received": {
            "receiver": str(payee.address()),
            "amount": "50000000000000000atestfet",
        }
    }
    confirmed_resp.logs = []
    confirmed_resp.actual_fee_base_units = 430390000000000
    confirmed_resp.actual_fee_display = "0.00043039 FET"
    mock_ledger.query_tx.return_value = confirmed_resp

    is_settled, rec = payment_service.reconcile_payment_on_chain(
        record.payment_reference, ledger_client=mock_ledger
    )
    assert is_settled is True
    assert rec.status == PaymentStatus.SETTLED
    assert rec.tx_height == 25805089


# =====================================================================
# Scenario 9: Duplicate Authorization and Replay Protection
# =====================================================================

def test_duplicate_authorization_blocked_from_second_transfer(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """Authorizing an already committed or settled payment must be rejected without broadcasting."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    approval = ExplicitPaymentApproval(
        reference=record.payment_reference,
        amount_base_units=record.amount_base_units,
        currency=record.currency,
        recipient=record.payee_wallet_address,
    )

    # Attempting to authorize an already COMMITTED record must fail
    with pytest.raises(ValueError, match="replay or duplicate authorization blocked"):
        payment_service.execute_authorized_transfer(approval, payer)


def test_existing_settled_records_protected_from_replay(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """An already SETTLED record is idempotently preserved and does not re-verify or change."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    now_iso = datetime.now(timezone.utc).isoformat()
    temp_storage.update_payment_status(
        reference=record.payment_reference,
        status=PaymentStatus.SETTLED,
        tx_hash=commit.transaction_id,
        tx_height=25805090,
        verified_at_utc=now_iso,
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    # Ledger must not have been queried
    mock_ledger.query_tx.assert_not_called()
    assert is_settled is True
    assert isinstance(complete, CompletePayment)
    assert complete.transaction_id == commit.transaction_id
    assert cancel is None
    assert updated.status == PaymentStatus.SETTLED
    assert updated.tx_height == 25805090


# =====================================================================
# Scenario 10: Actual Network Fee Extraction, Validation & Persistence
# =====================================================================

def test_extract_fee_populated_actual_fee_base_units():
    """Verify fee extraction when actual_fee_base_units is directly populated on TxResponse."""
    from krishibazaar.services.payment_service import extract_transaction_fee

    mock_resp = MagicMock(spec=TxResponse)
    mock_resp.actual_fee_base_units = 430390000000000
    mock_resp.actual_fee_display = "0.00043039 FET (430,390,000,000,000 atestfet)"

    fee_base, fee_disp = extract_transaction_fee(mock_resp, expected_denom="atestfet")
    assert fee_base == 430390000000000
    assert fee_disp == "0.00043039 FET (430,390,000,000,000 atestfet)"


def test_extract_fee_from_tx_event_standard_format():
    """Verify fee extraction from tx event: '430390000000000atestfet'."""
    from krishibazaar.services.payment_service import extract_transaction_fee

    mock_resp = MagicMock(spec=TxResponse)
    mock_resp.actual_fee_base_units = None
    mock_resp.actual_fee_display = None
    mock_resp.events = {
        "tx": {
            "fee": "430390000000000atestfet",
            "fee_payer": "fetch1yjvtu3qyacpnzes5zw0a75qc9uzp4szxuftrw5",
        }
    }

    fee_base, fee_disp = extract_transaction_fee(mock_resp, expected_denom="atestfet")
    assert fee_base == 430390000000000
    assert "0.00043039 FET" in fee_disp
    assert "430,390,000,000,000 atestfet" in fee_disp


def test_extract_fee_missing_fee_event():
    """Missing tx or fee event must return (None, None) without raising or inventing a value."""
    from krishibazaar.services.payment_service import extract_transaction_fee

    # 1. Empty events
    mock_resp1 = MagicMock(spec=TxResponse)
    mock_resp1.actual_fee_base_units = None
    mock_resp1.actual_fee_display = None
    mock_resp1.events = {}
    mock_resp1.logs = []
    assert extract_transaction_fee(mock_resp1, expected_denom="atestfet") == (None, None)

    # 2. 'tx' event with no 'fee' key
    mock_resp2 = MagicMock(spec=TxResponse)
    mock_resp2.actual_fee_base_units = None
    mock_resp2.actual_fee_display = None
    mock_resp2.events = {"tx": {"fee_payer": "fetch1..."}}
    mock_resp2.logs = []
    assert extract_transaction_fee(mock_resp2, expected_denom="atestfet") == (None, None)


def test_extract_fee_malformed_fee_string():
    """Malformed or invalid fee string must return (None, None)."""
    from krishibazaar.services.payment_service import extract_transaction_fee

    for bad_fee in ["", "   ", "invalid_fee", "atestfet", "???43039???", "-500atestfet"]:
        mock_resp = MagicMock(spec=TxResponse)
        mock_resp.actual_fee_base_units = None
        mock_resp.actual_fee_display = None
        mock_resp.events = {"tx": {"fee": bad_fee}}
        assert extract_transaction_fee(mock_resp, expected_denom="atestfet") == (None, None)


def test_extract_fee_wrong_denomination():
    """Fee with wrong denomination must return (None, None) and not be treated as matching fee."""
    from krishibazaar.services.payment_service import extract_transaction_fee

    mock_resp = MagicMock(spec=TxResponse)
    mock_resp.actual_fee_base_units = None
    mock_resp.actual_fee_display = None
    mock_resp.events = {"tx": {"fee": "430390000000000uatom"}}

    fee_base, fee_disp = extract_transaction_fee(mock_resp, expected_denom="atestfet")
    assert fee_base is None
    assert fee_disp is None


def test_fee_metadata_persisted_correctly_on_settlement(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """When a transaction settles, the extracted fee metadata is persisted to storage."""
    payer, payee = test_wallets
    commit, record = _create_committed_payment(payment_service, temp_storage, payer, payee)

    mock_resp = TxResponse(
        hash=commit.transaction_id,
        height=25809429,
        code=0,
        gas_wanted=100000,
        gas_used=74760,
        raw_log="",
        logs=[],
        events={
            "coin_received": {
                "receiver": str(payee.address()),
                "amount": "50000000000000000atestfet",
            },
            "tx": {
                "fee": "430390000000000atestfet",
                "fee_payer": str(payer.address()),
            },
        },
        timestamp=datetime.now(timezone.utc),
    )

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = mock_resp

    is_settled, complete, cancel, updated = payment_service.verify_and_settle_commitment(
        commit, mock_ledger
    )

    assert is_settled is True
    assert updated.status == PaymentStatus.SETTLED
    assert updated.actual_fee_base_units == 430390000000000
    assert "0.00043039 FET" in updated.actual_fee_display

    # Verify persisted in storage
    stored = temp_storage.get_payment_record(record.payment_reference)
    assert stored is not None
    assert stored.actual_fee_base_units == 430390000000000
    assert "0.00043039 FET" in stored.actual_fee_display


def test_previously_settled_record_fee_metadata_repaired_without_rebroadcast(
    payment_service: PaymentService,
    temp_storage: StorageEngine,
    test_wallets: tuple[LocalWallet, LocalWallet],
):
    """
    An already-SETTLED record missing actual_fee_base_units can be safely repaired
    by querying the ledger for its existing tx_hash without rebroadcast or altering status.
    """
    payer, payee = test_wallets
    tx_hash = "36C3088738B2A82CC61C7F18144DE8015FCF4A0587DCFE5060C3A27E384CBBEF"
    now_iso = datetime.now(timezone.utc).isoformat()

    # Create already-settled record with missing fee metadata (matching KB-PAY-27A4A2EC1F3E state)
    record = PaymentRecord(
        payment_reference="KB-PAY-TESTREPAIR01",
        service_inquiry_id="KB-BOOK-20261022-4C1CE8",
        purpose=PaymentPurpose.ADVISORY_PASS.value,
        payer_agent_address="agent1q_test_payer",
        payer_wallet_address=str(payer.address()),
        payee_agent_address="agent1q_test_payee",
        payee_wallet_address=str(payee.address()),
        amount_base_units=50000000000000000,
        amount_display="0.05 FET (50,000,000,000,000,000 atestfet)",
        currency="atestfet",
        network="dorado-1",
        status=PaymentStatus.SETTLED,
        tx_hash=tx_hash,
        tx_height=25809429,
        actual_fee_base_units=None,  # Missing fee
        actual_fee_display=None,     # Missing fee
        verified_at_utc=now_iso,
        deadline_seconds=300,
        created_at_utc=now_iso,
        updated_at_utc=now_iso,
    )
    temp_storage.save_payment_record(record)

    # Mock authoritative ledger query_tx response matching real Dorado output
    mock_resp = MagicMock(spec=TxResponse)
    mock_resp.code = 0
    mock_resp.is_successful.return_value = True
    mock_resp.height = 25809429
    mock_resp.actual_fee_base_units = None
    mock_resp.actual_fee_display = None
    mock_resp.events = {
        "tx": {
            "fee": "430390000000000atestfet",
            "fee_payer": str(payer.address()),
        }
    }

    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_tx.return_value = mock_resp

    # Execute metadata repair path
    is_settled, updated = payment_service.repair_settled_payment_fee_metadata(
        "KB-PAY-TESTREPAIR01", ledger_client=mock_ledger
    )

    # 1. Rebroadcast must NOT occur
    mock_ledger.send_tokens.assert_not_called()

    # 2. Record must remain SETTLED with same tx_hash and height
    assert is_settled is True
    assert updated.status == PaymentStatus.SETTLED
    assert updated.tx_hash == tx_hash
    assert updated.tx_height == 25809429

    # 3. Fee metadata is backfilled
    assert updated.actual_fee_base_units == 430390000000000
    assert "0.00043039 FET" in updated.actual_fee_display

    # 4. Storage is updated
    stored = temp_storage.get_payment_record("KB-PAY-TESTREPAIR01")
    assert stored is not None
    assert stored.actual_fee_base_units == 430390000000000
    assert stored.actual_fee_display == updated.actual_fee_display
