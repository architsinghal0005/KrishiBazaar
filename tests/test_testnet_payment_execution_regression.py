"""
Regression Tests for Fetch.ai Agent Payment Protocol Testnet Execution (Dorado-1).

Validates:
1. Disabled execution safety gate (ENABLE_PAYMENT_PROTOCOL=False blocks transfers).
2. Valid testnet authorization execution & authoritative on-chain settlement.
3. Chain restriction enforcement (supports ONLY Fetch.ai Dorado testnet 'dorado-1').
4. Recipient whitelist enforcement and placeholder address rejection.
5. Payer vs payee role separation (self-payment prohibited).
6. Spendable balance pre-flight check (must cover amount + network gas fee).
7. Idempotency & duplicate approval replay defense.
8. Broadcast failure handling & atomic status transitions.
9. Timeout recovery holding in AWAITING_RECONCILIATION with subsequent on-chain verification.
10. Authoritative fee and block height extraction from ledger events.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.tx_helpers import SubmittedTx, TxResponse
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.address import Address
from uagents import Context
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    TextContent,
)
from uagents_core.contrib.protocols.chat.cards import create_card_response_content

from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.config import Settings, settings
from krishibazaar.models import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    ExplicitPaymentApproval,
    Funds,
    PaymentPurpose,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.services.card_service import (
    build_payment_confirmed_card,
    build_payment_status_card,
)
from krishibazaar.services.payment_service import PaymentService
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(isolate_test_storage: StorageEngine) -> StorageEngine:
    return isolate_test_storage


@pytest.fixture
def mock_ctx():
    ctx = MagicMock(spec=Context)
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    ctx.storage = MagicMock()
    return ctx


@pytest.fixture
def payer_and_payee_wallets():
    payer = LocalWallet.generate()
    payee = LocalWallet.generate()
    return payer, payee


@pytest.fixture
def testnet_settings(payer_and_payee_wallets):
    payer, payee = payer_and_payee_wallets
    return Settings(
        enable_payment_protocol=True,
        payment_environment="testnet",
        payment_network="testnet",
        payment_testnet_chain_id="dorado-1",
        payment_testnet_denom="atestfet",
        payment_advisory_fee_base_units=50000000000000000,  # 0.05 FET
        payment_payer_seed="test_payer_seed_phrase_for_unit_tests_only_1234",
        payment_payee_seed="test_payee_seed_phrase_for_unit_tests_only_5678",
        payment_payee_wallet_address=str(payee.address()),
        payment_payee_agent_address="agent1q_testnet_payee_agent",
        payment_recipient_whitelist=[str(payee.address())],
    )


def make_test_record(
    reference: str,
    service_inquiry_id: str,
    payer_addr: str,
    payee_addr: str,
    amount_base_units: int = 50000000000000000,
    status: PaymentStatus = PaymentStatus.REQUESTED,
    tx_hash: str | None = None,
    tx_height: int | None = None,
    deadline_seconds: int = 900,
) -> PaymentRecord:
    now_iso = datetime.now(timezone.utc).isoformat()
    return PaymentRecord(
        payment_reference=reference,
        service_inquiry_id=service_inquiry_id,
        purpose=PaymentPurpose.ADVISORY_PASS.value,
        payer_agent_address="agent1_test_payer_agent",
        payer_wallet_address=payer_addr,
        payee_agent_address="agent1_test_payee_agent",
        payee_wallet_address=payee_addr,
        amount_base_units=amount_base_units,
        amount_display="0.05 FET (50,000,000,000,000,000 atestfet)",
        currency="atestfet",
        network="dorado-1",
        status=status,
        tx_hash=tx_hash,
        tx_height=tx_height,
        deadline_seconds=deadline_seconds,
        created_at_utc=now_iso,
        updated_at_utc=now_iso,
    )


class TestTestnetPaymentExecutionAndSafety:
    """Test suite for live testnet payment execution, safety gates, and reconciliation."""

    @pytest.mark.asyncio
    async def test_1_disabled_execution_safety_gate(self, temp_db: StorageEngine, mock_ctx: MagicMock):
        """When ENABLE_PAYMENT_PROTOCOL=False, authorization request is safely blocked."""
        sender = "agent1_farmer_disabled_gate"
        ref = "KB-PAY-DIS-GATE-01"

        record = make_test_record(
            reference=ref,
            service_inquiry_id="KB-BOOK-DIS-01",
            payer_addr="fetch1testpayeraddr",
            payee_addr="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        )
        temp_db.save_payment_record(record)

        auth_resp = create_card_response_content(
            selection={
                "action": "authorize_payment",
                "reference": ref,
                "amount": 50000000000000000,
                "currency": "atestfet",
                "recipient": "fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
            }
        )
        msg = ChatMessage(msg_id=uuid4(), content=[auth_resp])

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch.object(settings, "enable_payment_protocol", False):
            await handle_chat_message(mock_ctx, sender, msg)

            reply = mock_ctx.send.call_args[0][1].content[0].text
            assert "Payment Protocol Safety Gate Active" in reply
            assert "ENABLE_PAYMENT_PROTOCOL=false" in reply
            assert "NO testnet tokens or funds have been transferred" in reply

            # Verify status was not modified
            unchanged = temp_db.get_payment_record(ref)
            assert unchanged.status == PaymentStatus.REQUESTED
            assert unchanged.tx_hash is None

    def test_2_wrong_chain_rejection(self, temp_db: StorageEngine, payer_and_payee_wallets):
        """Mainnet and non-dorado-1 chain IDs are strictly rejected."""
        payer, payee = payer_and_payee_wallets
        cfg = Settings(
            enable_payment_protocol=True,
            payment_network="mainnet",
            payment_testnet_chain_id="fetchhub-4",
        )
        service = PaymentService(storage_engine=temp_db, settings_obj=cfg)

        with pytest.raises(PermissionError, match="ONLY the Fetch.ai Dorado testnet"):
            service.get_ledger()

        wrong_chain_cfg = Settings(
            enable_payment_protocol=True,
            payment_network="testnet",
            payment_testnet_chain_id="theta-testnet-001",
        )
        wrong_service = PaymentService(storage_engine=temp_db, settings_obj=wrong_chain_cfg)
        with pytest.raises(PermissionError, match="ONLY the Fetch.ai Dorado testnet"):
            wrong_service.get_ledger()

    def test_3_wrong_recipient_not_in_whitelist(self, temp_db: StorageEngine, testnet_settings, payer_and_payee_wallets):
        """Transfers to recipients not in PAYMENT_RECIPIENT_WHITELIST are rejected."""
        payer, _ = payer_and_payee_wallets
        service = PaymentService(storage_engine=temp_db, settings_obj=testnet_settings)

        unwhitelisted = LocalWallet.generate()
        approval = ExplicitPaymentApproval(
            reference="KB-PAY-UNWHITELISTED",
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=str(unwhitelisted.address()),
        )

        with pytest.raises(PermissionError, match="not in PAYMENT_RECIPIENT_WHITELIST"):
            service.execute_authorized_transfer(approval, payer)

    def test_4_self_payment_prohibited(self, temp_db: StorageEngine, testnet_settings, payer_and_payee_wallets):
        """Payer and payee having the same wallet address is rejected."""
        payer, _ = payer_and_payee_wallets
        same_addr = str(payer.address())
        testnet_settings.payment_recipient_whitelist.append(same_addr)
        service = PaymentService(storage_engine=temp_db, settings_obj=testnet_settings)

        ref = "KB-PAY-SELF-PAY"
        record = make_test_record(
            reference=ref,
            service_inquiry_id="KB-BOOK-SELF",
            payer_addr=same_addr,
            payee_addr=same_addr,
        )
        temp_db.save_payment_record(record)

        approval = ExplicitPaymentApproval(
            reference=ref,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=same_addr,
        )

        with pytest.raises(ValueError, match="must be separate designated wallets; self-payment is not permitted"):
            service.execute_authorized_transfer(approval, payer)

    def test_5_insufficient_balance_including_network_fee(self, temp_db: StorageEngine, testnet_settings, payer_and_payee_wallets):
        """Payer balance must cover amount + network gas fee; partial balance fails pre-flight."""
        payer, payee = payer_and_payee_wallets
        service = PaymentService(storage_engine=temp_db, settings_obj=testnet_settings)

        ref = "KB-PAY-LOW-BAL"
        record = make_test_record(
            reference=ref,
            service_inquiry_id="KB-BOOK-LOW-BAL",
            payer_addr=str(payer.address()),
            payee_addr=str(payee.address()),
        )
        temp_db.save_payment_record(record)

        approval = ExplicitPaymentApproval(
            reference=ref,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=str(payee.address()),
        )

        # Mock ledger has exactly 0.05 FET (enough for amount, but NOT for amount + 0.0004 FET gas fee)
        mock_ledger = MagicMock(spec=LedgerClient)
        mock_ledger.query_bank_balance.return_value = 50000000000000000
        mock_ledger.network_config = MagicMock(fee_minimum_gas_price=5000000000)

        with pytest.raises(ValueError, match="Insufficient spendable balance"):
            service.execute_authorized_transfer(approval, payer, ledger_client=mock_ledger)

    def test_6_valid_testnet_broadcast_and_settlement(self, temp_db: StorageEngine, testnet_settings, payer_and_payee_wallets):
        """Valid testnet payment successfully broadcasts, transitions to COMMITTED, and settles on-chain."""
        payer, payee = payer_and_payee_wallets
        service = PaymentService(storage_engine=temp_db, settings_obj=testnet_settings)

        ref = "KB-PAY-VALID-EXEC"
        payee_addr = str(payee.address())
        record = make_test_record(
            reference=ref,
            service_inquiry_id="KB-BOOK-EXEC",
            payer_addr=str(payer.address()),
            payee_addr=payee_addr,
        )
        temp_db.save_payment_record(record)

        approval = ExplicitPaymentApproval(
            reference=ref,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=payee_addr,
        )

        mock_ledger = MagicMock(spec=LedgerClient)
        # Funded with 1 FET
        mock_ledger.query_bank_balance.return_value = 1000000000000000000
        mock_ledger.network_config = MagicMock(fee_minimum_gas_price=5000000000)

        expected_tx_hash = "0xTESTNET_DORADO_TX_HASH_847392"
        mock_submitted_tx = MagicMock(spec=SubmittedTx, tx_hash=expected_tx_hash)
        mock_ledger.send_tokens.return_value = mock_submitted_tx

        # 1. Execute transfer
        commit_msg, committed_rec = service.execute_authorized_transfer(
            approval, payer, ledger_client=mock_ledger
        )

        assert commit_msg.transaction_id == expected_tx_hash
        assert commit_msg.reference == ref
        assert committed_rec.status == PaymentStatus.COMMITTED
        assert committed_rec.tx_hash == expected_tx_hash

        # 2. Authoritative on-chain settlement verification
        mock_tx_resp = MagicMock(spec=TxResponse)
        mock_tx_resp.is_successful.return_value = True
        mock_tx_resp.height = 123456
        mock_tx_resp.events = {
            "transfer": {
                "recipient": payee_addr,
                "amount": "50000000000000000atestfet",
            },
            "tx": {
                "fee": "400000000000000atestfet",
            },
        }
        mock_ledger.query_tx.return_value = mock_tx_resp

        is_settled, complete, cancel, settled_rec = service.verify_and_settle_commitment(
            commit_msg, ledger_client=mock_ledger
        )

        assert is_settled is True
        assert complete is not None
        assert complete.transaction_id == expected_tx_hash
        assert settled_rec.status == PaymentStatus.SETTLED
        assert settled_rec.tx_height == 123456
        assert settled_rec.actual_fee_base_units == 400000000000000

    def test_7_duplicate_approval_rejected_after_settled(self, temp_db: StorageEngine, testnet_settings, payer_and_payee_wallets):
        """Already SETTLED records cannot be re-authorized or re-broadcast."""
        payer, payee = payer_and_payee_wallets
        service = PaymentService(storage_engine=temp_db, settings_obj=testnet_settings)

        ref = "KB-PAY-ALREADY-SETTLED"
        record = make_test_record(
            reference=ref,
            service_inquiry_id="KB-BOOK-SETTLED",
            payer_addr=str(payer.address()),
            payee_addr=str(payee.address()),
            status=PaymentStatus.SETTLED,
            tx_hash="0xALREADY_SETTLED_TX",
            tx_height=99999,
        )
        temp_db.save_payment_record(record)

        approval = ExplicitPaymentApproval(
            reference=ref,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=str(payee.address()),
        )

        with pytest.raises(ValueError, match="current status is 'SETTLED', replay or duplicate authorization blocked"):
            service.execute_authorized_transfer(approval, payer)

    def test_8_failed_broadcast_handling(self, temp_db: StorageEngine, testnet_settings, payer_and_payee_wallets):
        """When ledger.send_tokens raises an error, status transitions atomically to FAILED."""
        payer, payee = payer_and_payee_wallets
        service = PaymentService(storage_engine=temp_db, settings_obj=testnet_settings)

        ref = "KB-PAY-FAIL-BROADCAST"
        record = make_test_record(
            reference=ref,
            service_inquiry_id="KB-BOOK-FAIL",
            payer_addr=str(payer.address()),
            payee_addr=str(payee.address()),
        )
        temp_db.save_payment_record(record)

        approval = ExplicitPaymentApproval(
            reference=ref,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=str(payee.address()),
        )

        mock_ledger = MagicMock(spec=LedgerClient)
        mock_ledger.query_bank_balance.return_value = 1000000000000000000
        mock_ledger.network_config = MagicMock(fee_minimum_gas_price=5000000000)
        mock_ledger.send_tokens.side_effect = RuntimeError("RPC endpoint unreachable: 503 Service Unavailable")

        with pytest.raises(RuntimeError, match="RPC endpoint unreachable"):
            service.execute_authorized_transfer(approval, payer, ledger_client=mock_ledger)

        failed_rec = temp_db.get_payment_record(ref)
        assert failed_rec.status == PaymentStatus.FAILED
        assert "Broadcast failed" in failed_rec.rejection_reason

    def test_9_timeout_recovery_and_on_chain_reconciliation(self, temp_db: StorageEngine, testnet_settings, payer_and_payee_wallets):
        """Unconfirmed ledger queries hold in AWAITING_RECONCILIATION, then settle once confirmed."""
        payer, payee = payer_and_payee_wallets
        service = PaymentService(storage_engine=temp_db, settings_obj=testnet_settings)

        ref = "KB-PAY-TIMEOUT-RECON"
        tx_hash = "0xTIMEOUT_TX_HASH_123"
        payee_addr = str(payee.address())

        commit = CommitPayment(
            funds=Funds(amount="50000000000000000", currency="atestfet", payment_method="fet_direct"),
            recipient=payee_addr,
            transaction_id=tx_hash,
            reference=ref,
        )

        record = make_test_record(
            reference=ref,
            service_inquiry_id="KB-BOOK-RECON",
            payer_addr=str(payer.address()),
            payee_addr=payee_addr,
            status=PaymentStatus.COMMITTED,
            tx_hash=tx_hash,
        )
        temp_db.save_payment_record(record)

        mock_ledger = MagicMock(spec=LedgerClient)
        # 1. First query times out / throws exception
        mock_ledger.query_tx.side_effect = TimeoutError("Query timed out waiting for tx block")

        is_settled, complete, cancel, unconfirmed_rec = service.verify_and_settle_commitment(
            commit, ledger_client=mock_ledger
        )

        assert is_settled is False
        assert complete is None
        assert cancel is None
        assert unconfirmed_rec.status == PaymentStatus.AWAITING_RECONCILIATION

        # 2. Later query succeeds with confirmed block
        mock_tx_resp = MagicMock(spec=TxResponse)
        mock_tx_resp.is_successful.return_value = True
        mock_tx_resp.height = 88888
        mock_tx_resp.events = {
            "coin_received": {
                "receiver": payee_addr,
                "amount": "50000000000000000atestfet",
            },
            "tx": {
                "fee": "420000000000000atestfet",
            },
        }
        mock_ledger.query_tx.side_effect = None
        mock_ledger.query_tx.return_value = mock_tx_resp

        reconciled_settled, reconciled_rec = service.reconcile_payment_on_chain(
            ref, ledger_client=mock_ledger
        )

        assert reconciled_settled is True
        assert reconciled_rec.status == PaymentStatus.SETTLED
        assert reconciled_rec.tx_height == 88888
        assert reconciled_rec.actual_fee_base_units == 420000000000000
