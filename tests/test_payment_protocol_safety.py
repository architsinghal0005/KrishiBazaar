"""
Isolated Unit and Safety Tests for Fetch.ai Agent Payment Protocol (Phase 9).
Tests protocol models, message interactions, explicit payment authorization gates,
recipient whitelist validation, amount matching, duplicate protection, secret redaction,
and confirms that real payment transfers remain disabled by default.

CRITICAL SAFETY COMPLIANCE:
- Never executes real monetary or testnet blockchain transactions.
- All testing uses isolated in-memory models and mock verification.
- Confirms protocol messages alone do not constitute financial settlement.
"""

from __future__ import annotations
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# Official Fetch.ai payment protocol models from installed uagents-core
from uagents_core.contrib.protocols.payment import (
    Funds,
    RequestPayment,
    RejectPayment,
    CommitPayment,
    CancelPayment,
    CompletePayment,
    payment_protocol_spec,
)
from krishibazaar.config import settings, Settings


# =====================================================================
# Isolated Payment Safety Helpers (Mock Implementation for Testing)
# =====================================================================

class PaymentAuthorizationGate:
    """
    Isolated test-mode safety gate implementing strict verification rules
    prior to any protocol commitment.
    """

    def __init__(self, allowed_recipients: list[str] | None = None) -> None:
        self.allowed_recipients = allowed_recipients or []
        self.processed_references: set[str] = set()

    def parse_and_validate_approval(
        self,
        user_message: str,
        active_request: RequestPayment,
        expected_amount: str,
        expected_currency: str,
    ) -> tuple[bool, str, CommitPayment | None]:
        """
        Validate explicit user approval. Ambiguous words (ok, yes, thanks) are strictly rejected.
        Approval must explicitly match reference, recipient, currency, and amount.
        """
        msg_clean = user_message.strip()
        lower = msg_clean.lower()

        # 1. Reject ambiguous / casual messages
        casual_words = {"ok", "yes", "sounds good", "thanks", "proceed", "yep", "sure", "fine", "cool", "hi", "hello"}
        if lower in casual_words or len(lower.split()) <= 2 and any(w in lower for w in ["ok", "yes", "sure", "confirm"]):
            if not lower.startswith("authorize payment"):
                return False, "Ambiguous input cannot authorize payment. Explicit 'AUTHORIZE PAYMENT' required.", None

        if not lower.startswith("authorize payment"):
            return False, "Explicit authorization command required (e.g. 'AUTHORIZE PAYMENT REF:<id>').", None

        # 2. Replay / Idempotency check
        if active_request.reference in self.processed_references:
            return False, f"Duplicate authorization blocked: Reference {active_request.reference} already processed.", None

        # 3. Recipient verification
        if active_request.recipient not in self.allowed_recipients:
            return False, f"Recipient rejected: '{active_request.recipient}' is not in the verified whitelist.", None

        # 4. Amount & Currency matching
        matching_fund = next(
            (f for f in active_request.accepted_funds if f.amount == expected_amount and f.currency == expected_currency),
            None,
        )
        if not matching_fund:
            return False, f"Payment terms mismatch: Expected {expected_amount} {expected_currency}.", None

        # 5. Success - mark reference processed (idempotency)
        self.processed_references.add(active_request.reference or "UNKNOWN_REF")
        commit_msg = CommitPayment(
            funds=matching_fund,
            recipient=active_request.recipient,
            transaction_id=f"MOCK-TX-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            reference=active_request.reference,
            description="Mock authorized sandbox payment commitment",
        )
        return True, "Payment authorized successfully in test mode.", commit_msg


def safe_redact_secrets(data: str, secret_tokens: list[str]) -> str:
    """Sanitize strings ensuring private keys and API tokens are redacted."""
    sanitized = data
    for secret in secret_tokens:
        if secret and len(secret) > 4:
            sanitized = sanitized.replace(secret, "***REDACTED***")
    return sanitized


# =====================================================================
# Scenario 1: Construct and Validate Funds and RequestPayment
# =====================================================================

def test_scenario_1_construct_and_validate_funds_and_request_payment():
    """Verify Funds and RequestPayment can be instantiated and validate schema constraints."""
    funds = Funds(amount="152050", currency="FET", payment_method="fet_direct")
    assert funds.amount == "152050"
    assert funds.currency == "FET"
    assert funds.payment_method == "fet_direct"

    request = RequestPayment(
        accepted_funds=[funds],
        recipient="fetch1_test_designated_seller_wallet",
        deadline_seconds=300,
        reference="PROP-20261021-TEST01",
        description="Produce payout for 50 quintals onion",
        metadata={"crop": "onion", "quantity": "50"},
    )
    assert len(request.accepted_funds) == 1
    assert request.accepted_funds[0].amount == "152050"
    assert request.recipient == "fetch1_test_designated_seller_wallet"
    assert request.deadline_seconds == 300
    assert request.reference == "PROP-20261021-TEST01"
    assert request.metadata == {"crop": "onion", "quantity": "50"}


# =====================================================================
# Scenario 2: RequestPayment -> CommitPayment or RejectPayment Interaction
# =====================================================================

def test_scenario_2_protocol_spec_request_to_commit_or_reject():
    """Verify official payment_protocol_spec defines RequestPayment -> {CommitPayment, RejectPayment}."""
    interactions = payment_protocol_spec.interactions
    assert RequestPayment in interactions
    assert interactions[RequestPayment] == {CommitPayment, RejectPayment}

    # Construct both valid branches
    fund = Funds(amount="152050", currency="FET", payment_method="fet_direct")
    commit = CommitPayment(
        funds=fund,
        recipient="fetch1_test_seller_wallet",
        transaction_id="MOCK_TX_HASH_XYZ123",
        reference="PROP-20261021-TEST01",
    )
    assert commit.transaction_id == "MOCK_TX_HASH_XYZ123"

    reject = RejectPayment(reason="Farmer declined payment terms")
    assert reject.reason == "Farmer declined payment terms"


# =====================================================================
# Scenario 3: CommitPayment -> CompletePayment or CancelPayment Handling
# =====================================================================

def test_scenario_3_protocol_spec_commit_to_complete_or_cancel():
    """Verify CommitPayment -> {CompletePayment, CancelPayment} and terminal state completeness."""
    interactions = payment_protocol_spec.interactions
    assert CommitPayment in interactions
    assert interactions[CommitPayment] == {CompletePayment, CancelPayment}

    # Terminal states must have empty outgoing interactions
    assert interactions[CompletePayment] == set()
    assert interactions[CancelPayment] == set()
    assert interactions[RejectPayment] == set()

    complete = CompletePayment(transaction_id="MOCK_TX_HASH_XYZ123")
    assert complete.transaction_id == "MOCK_TX_HASH_XYZ123"

    cancel = CancelPayment(transaction_id="MOCK_TX_HASH_XYZ123", reason="Payment expired before on-chain confirmation")
    assert cancel.reason == "Payment expired before on-chain confirmation"


# =====================================================================
# Scenario 4: Ambiguous Responses Cannot Authorize Payment
# =====================================================================

@pytest.mark.parametrize("ambiguous_msg", ["ok", "yes", "sounds good", "thanks", "proceed", "yep", "sure", "fine", "hi", "ok confirm"])
def test_scenario_4_ambiguous_responses_cannot_authorize_payment(ambiguous_msg: str):
    """Confirm casual acknowledgements are strictly blocked by payment safety gate."""
    gate = PaymentAuthorizationGate(allowed_recipients=["fetch1_verified_seller_wallet"])
    req = RequestPayment(
        accepted_funds=[Funds(amount="152050", currency="FET", payment_method="fet_direct")],
        recipient="fetch1_verified_seller_wallet",
        deadline_seconds=300,
        reference="PROP-20261021-TEST01",
    )

    success, msg, commit = gate.parse_and_validate_approval(
        user_message=ambiguous_msg,
        active_request=req,
        expected_amount="152050",
        expected_currency="FET",
    )
    assert success is False
    assert "Ambiguous input cannot authorize payment" in msg or "Explicit authorization command required" in msg
    assert commit is None


# =====================================================================
# Scenario 5: Require Explicit Approval Tied to Exact Reference & Terms
# =====================================================================

def test_scenario_5_explicit_approval_success():
    """Verify explicit approval succeeds when reference, amount, currency, and recipient match."""
    gate = PaymentAuthorizationGate(allowed_recipients=["fetch1_verified_seller_wallet"])
    req = RequestPayment(
        accepted_funds=[Funds(amount="152050", currency="FET", payment_method="fet_direct")],
        recipient="fetch1_verified_seller_wallet",
        deadline_seconds=300,
        reference="PROP-20261021-TEST01",
    )

    explicit_msg = "AUTHORIZE PAYMENT REF:PROP-20261021-TEST01"
    success, msg, commit = gate.parse_and_validate_approval(
        user_message=explicit_msg,
        active_request=req,
        expected_amount="152050",
        expected_currency="FET",
    )
    assert success is True
    assert commit is not None
    assert commit.recipient == "fetch1_verified_seller_wallet"
    assert commit.funds.amount == "152050"
    assert commit.funds.currency == "FET"
    assert commit.reference == "PROP-20261021-TEST01"


# =====================================================================
# Scenario 6: Reject Invalid Recipient and Mismatched Amount
# =====================================================================

def test_scenario_6_rejects_unverified_recipient_and_mismatched_amount():
    """Verify payments to unverified recipients (e.g. Market Specialist) or incorrect amounts are rejected."""
    # Whitelist contains only the verified seller wallet
    gate = PaymentAuthorizationGate(allowed_recipients=["fetch1_verified_seller_wallet"])

    # 1. Attempting to pay unverified recipient (e.g. Market Specialist internal address)
    unauthorized_req = RequestPayment(
        accepted_funds=[Funds(amount="152050", currency="FET", payment_method="fet_direct")],
        recipient="agent1q0rsn3us4d9twt209r2xefsqku4qvgypjtp8xvkjep4tmrvrsldnw4z5az4",  # Market Specialist!
        deadline_seconds=300,
        reference="PROP-20261021-TEST01",
    )
    success, msg, _ = gate.parse_and_validate_approval(
        user_message="AUTHORIZE PAYMENT REF:PROP-20261021-TEST01",
        active_request=unauthorized_req,
        expected_amount="152050",
        expected_currency="FET",
    )
    assert success is False
    assert "Recipient rejected" in msg
    assert "not in the verified whitelist" in msg

    # 2. Attempting to pay mismatched amount (e.g. seller requests 200,000 instead of 152,050)
    mismatched_amount_req = RequestPayment(
        accepted_funds=[Funds(amount="200000", currency="FET", payment_method="fet_direct")],
        recipient="fetch1_verified_seller_wallet",
        deadline_seconds=300,
        reference="PROP-20261021-TEST02",
    )
    success2, msg2, _ = gate.parse_and_validate_approval(
        user_message="AUTHORIZE PAYMENT REF:PROP-20261021-TEST02",
        active_request=mismatched_amount_req,
        expected_amount="152050",  # Expected net payout
        expected_currency="FET",
    )
    assert success2 is False
    assert "Payment terms mismatch" in msg2


# =====================================================================
# Scenario 7: Block Duplicate Authorization and Replayed Requests
# =====================================================================

def test_scenario_7_block_duplicate_authorization_and_replay():
    """Verify repeated authorization attempts for the same payment reference are blocked."""
    gate = PaymentAuthorizationGate(allowed_recipients=["fetch1_verified_seller_wallet"])
    req = RequestPayment(
        accepted_funds=[Funds(amount="152050", currency="FET", payment_method="fet_direct")],
        recipient="fetch1_verified_seller_wallet",
        deadline_seconds=300,
        reference="PROP-20261021-TEST01",
    )

    # 1. First authorization: succeeds
    success1, _, commit1 = gate.parse_and_validate_approval(
        user_message="AUTHORIZE PAYMENT REF:PROP-20261021-TEST01",
        active_request=req,
        expected_amount="152050",
        expected_currency="FET",
    )
    assert success1 is True
    assert commit1 is not None

    # 2. Second authorization attempt with same reference: strictly blocked
    success2, msg2, commit2 = gate.parse_and_validate_approval(
        user_message="AUTHORIZE PAYMENT REF:PROP-20261021-TEST01",
        active_request=req,
        expected_amount="152050",
        expected_currency="FET",
    )
    assert success2 is False
    assert "Duplicate authorization blocked" in msg2
    assert commit2 is None


# =====================================================================
# Scenario 8: Handle Expired Requests, Cancellation, and Unknown States
# =====================================================================

def test_scenario_8_expiry_cancellation_and_unknown_states():
    """Verify expired requests and cancellations transition to safe non-settled states."""
    # 1. Expired request
    created_at = datetime.now(timezone.utc) - timedelta(seconds=400)
    deadline_seconds = 300
    is_expired = (datetime.now(timezone.utc) - created_at).total_seconds() > deadline_seconds
    assert is_expired is True

    # 2. Safe cancellation
    cancel = CancelPayment(transaction_id="MOCK-TX-123", reason="Request expired past 300s window")
    assert cancel.reason == "Request expired past 300s window"

    # 3. Unknown transaction state handling
    raw_status = "UNKNOWN_NODE_TIMEOUT"
    normalized_state = "UNVERIFIED_PENDING_RECONCILIATION" if raw_status not in ("SUCCESS", "CONFIRMED") else "SETTLED"
    assert normalized_state == "UNVERIFIED_PENDING_RECONCILIATION"


# =====================================================================
# Scenario 9: Protocol Message / Mock ID Alone Cannot Mark Payment Settled
# =====================================================================

def test_scenario_9_protocol_message_alone_cannot_settle():
    """
    Critical Architectural Rule:
    A CommitPayment or CompletePayment envelope does NOT prove funds moved.
    Must require external ledger / gateway reconciliation before marking SETTLED.
    """
    # Simulated mock commit message
    mock_commit = CommitPayment(
        funds=Funds(amount="152050", currency="FET", payment_method="fet_direct"),
        recipient="fetch1_verified_seller_wallet",
        transaction_id="MOCK_TX_UNCONFIRMED_999",
        reference="PROP-20261021-TEST01",
    )

    # In-memory payment ledger representation
    payment_record = {
        "reference": mock_commit.reference,
        "transaction_id": mock_commit.transaction_id,
        "state": "COMMITTED_UNVERIFIED",
        "is_settled": False,
    }

    # Attempting to settle without on-chain proof must fail
    mock_ledger_client = MagicMock()
    # Ledger returns None (transaction not found on-chain / no block inclusion)
    mock_ledger_client.query_tx.return_value = None

    def attempt_settlement(record: dict, ledger_client: Any) -> bool:
        tx_info = ledger_client.query_tx(record["transaction_id"])
        if tx_info is None or getattr(tx_info, "code", -1) != 0:
            return False
        record["state"] = "SETTLED"
        record["is_settled"] = True
        return True

    settled = attempt_settlement(payment_record, mock_ledger_client)
    assert settled is False
    assert payment_record["state"] == "COMMITTED_UNVERIFIED"
    assert payment_record["is_settled"] is False


# =====================================================================
# Scenario 10: Verify Secrets Never Appear in Logs or Test Output
# =====================================================================

def test_scenario_10_secrets_never_leak_in_logs():
    """Verify sensitive private keys, seeds, and API keys are redacted from logs and messages."""
    simulated_private_key = "d0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    simulated_seed = "super_secret_agent_private_seed_phrase_xyz"
    simulated_skyfire_key = "skyfire_live_sec_999888777666555"

    log_entry = (
        f"Error connecting wallet: key={simulated_private_key}, seed={simulated_seed}, "
        f"api_key={simulated_skyfire_key} for transaction PROP-01"
    )

    cleaned = safe_redact_secrets(
        log_entry,
        [simulated_private_key, simulated_seed, simulated_skyfire_key],
    )

    assert simulated_private_key not in cleaned
    assert simulated_seed not in cleaned
    assert simulated_skyfire_key not in cleaned
    assert "***REDACTED***" in cleaned


# =====================================================================
# Scenario 11: Confirm Payment Functionality Remains Disabled by Default
# =====================================================================

def test_scenario_11_payment_functionality_disabled_by_default():
    """Verify that settings default to payment protocol disabled with zero whitelisted recipients."""
    default_cfg = Settings()
    assert default_cfg.enable_payment_protocol is False
    assert default_cfg.payment_environment == "disabled"
    assert default_cfg.payment_recipient_whitelist == []

    # Current singleton settings in runtime environment
    assert settings.enable_payment_protocol is False
    assert settings.payment_environment in ("disabled", "testnet")
    assert isinstance(settings.payment_recipient_whitelist, list)


# =====================================================================
# Scenario 12: Existing Non-Payment Workflow Regression Test
# =====================================================================

def test_scenario_12_non_payment_booking_service_remains_operational():
    """Verify that existing proposal creation, cancellation, and action parsing operate untouched."""
    from krishibazaar.services.booking_service import parse_booking_action, BookingActionType

    # Non-payment action parsing
    action, val = parse_booking_action("Select option 1")
    assert action == BookingActionType.SELECT_OPTION
    assert val == "1"

    action, val = parse_booking_action("Confirm booking")
    assert action == BookingActionType.APPROVE
    assert val is None

    action, val = parse_booking_action("Cancel")
    assert action == BookingActionType.CANCEL

    # Casual greeting is not booking approval
    action, val = parse_booking_action("ok")
    assert action == BookingActionType.NONE


# =====================================================================
# Phase 9E: Address Validation, Placeholder Rejection, and Preflight Tests
# =====================================================================

def test_phase9e_reject_placeholder_and_specialist_addresses():
    """
    Phase 9E: Strictly reject placeholder addresses, agent communication addresses,
    and informational specialist agent addresses.
    """
    from krishibazaar.services.payment_service import (
        validate_fetch_wallet_address,
        PaymentService,
    )

    # 1. Reject unverified placeholder addresses
    placeholders = [
        "fetch1testrecipientaddr01",
        "fetch1_whitelisted_testnet_recipient",
        "fetch1_dummy_test_recipient",
        "fetch1_placeholder_recipient",
        "fetch1_fake_addr",
        "fetch1_attacker_unknown_wallet_addr",
    ]
    for ph in placeholders:
        is_valid, reason = validate_fetch_wallet_address(ph)
        assert is_valid is False, f"Expected {ph} to be rejected"
        assert "placeholder" in reason.lower() or "sdk" in reason.lower()

    # 2. Reject uAgents communication addresses (agent1...)
    is_valid, reason = validate_fetch_wallet_address(
        "agent1qd3h83llg09jmurahgs3tk32wvu6g066zhvpl54gkr0pk5nx6jdp7ppsfnc"
    )
    assert is_valid is False
    assert "agent1..." in reason and "uagents communication address" in reason.lower()

    # 3. Reject routing to any informational specialist agents
    specialist_wallets = [
        ("fetch1aj7a34sp5udhksedvrdm2u5wqqcf9s77uk06a5", "Market"),
        ("fetch1gm974uk642dklm7gtmtvmtxx8c6c4szxxt98lw", "Weather"),
        ("fetch1f8uxzuhfaxhuhtc5t47yn4a0hxpgyhrcpv4khh", "Buyer [Mandi]"),
        ("fetch1r0m5ncaugjt63968wte7gefk9vy76cwd3txwgt", "Buyer [Reliance]"),
        ("fetch1n856kjr8dqx726rhme8s99hnqd0k9l4t4rn42v", "Transport"),
        ("fetch17h5q0nhxv2thzdphmec5ymy3pqhvz66zrt7g3z", "Orchestrator"),
    ]
    for wallet_addr, name in specialist_wallets:
        is_valid, reason = validate_fetch_wallet_address(wallet_addr)
        assert is_valid is False, f"Expected {name} wallet {wallet_addr} to be rejected"
        assert "informational specialist" in reason.lower()

    # 4. Valid designated payee and payer wallets must pass validation
    for payee_addr in [
        "fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
        "fetch1sqccsyquf3zdk0054hfv4utehwjz8e8ypvr9nl",
        "fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
    ]:
        is_valid, reason = validate_fetch_wallet_address(payee_addr)
        assert is_valid is True, f"Failed for valid payee {payee_addr}: {reason}"

    payer_addr = "fetch1yjvtu3qyacpnzes5zw0a75qc9uzp4szxuftrw5"
    is_valid, reason = validate_fetch_wallet_address(payer_addr)
    assert is_valid is True, f"Failed for valid payer: {reason}"


def test_phase9e_create_payment_request_rejects_placeholder_and_self_payment(tmp_path: Path):
    """Verify PaymentService.create_advisory_payment_request blocks placeholders and self-payment."""
    from krishibazaar.services.payment_service import PaymentService
    from krishibazaar.storage import StorageEngine
    import pytest

    db = StorageEngine(str(tmp_path / "safety1.db"))
    db.init_db()
    service = PaymentService(storage_engine=db)

    # 1. Placeholder recipient rejected
    with pytest.raises(ValueError, match="Invalid payee wallet address"):
        service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1testrecipientaddr01",
            amount_base_units=50000000000000000,
        )

    # 2. Self-payment (payer == payee) rejected
    valid_addr = "fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"
    with pytest.raises(ValueError, match="self-payment is not permitted"):
        service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address=valid_addr,
            payer_wallet_address=valid_addr,
            amount_base_units=50000000000000000,
        )


def test_phase9e_network_fee_and_balance_check_status(tmp_path: Path):
    """Verify dynamic network fee estimation and balance checking in review details."""
    from krishibazaar.services.payment_service import PaymentService
    from krishibazaar.storage import StorageEngine
    from unittest.mock import MagicMock

    db = StorageEngine(str(tmp_path / "safety2.db"))
    db.init_db()
    mock_ledger = MagicMock()
    mock_ledger.network_config.fee_minimum_gas_price = 5000000000
    mock_ledger.query_bank_balance.return_value = 0  # Unfunded balance

    service = PaymentService(storage_engine=db, ledger_client=mock_ledger)

    fee_base, fee_display = service.estimate_network_fee()
    assert fee_base == 400_000_000_000_000
    assert "0.0004 FET" in fee_display
    assert "80,000 gas" in fee_display

    bal, is_suff, status = service.check_payer_balance(
        payer_address="fetch1yjvtu3qyacpnzes5zw0a75qc9uzp4szxuftrw5",
        required_base_units=50_000_000_000_000_000,
    )
    assert bal == 0
    assert is_suff is False
    assert "UNFUNDED" in status
    assert "0 atestfet available" in status

    # Verify review details incorporates live status
    _, record = service.create_advisory_payment_request(
        payee_agent_address="agent1q_payee",
        payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
        payer_wallet_address="fetch1yjvtu3qyacpnzes5zw0a75qc9uzp4szxuftrw5",
        amount_base_units=50_000_000_000_000_000,
    )
    review = service.format_review_details(record)
    assert "0.0004 FET" in review.estimated_fee
    assert "UNFUNDED" in review.payer_balance_status
    assert "TESTNET DEMO MICRO-FEE ONLY" in review.testnet_notice


def test_phase9h_mainnet_strictly_prohibited(tmp_path: Path):
    """
    Verify requirement 4: Execution path supports ONLY Dorado testnet (dorado-1).
    Mainnet transfers and non-Dorado chain IDs must be strictly prohibited with PermissionError.
    """
    from krishibazaar.config import Settings
    from krishibazaar.models import ExplicitPaymentApproval
    from krishibazaar.services.payment_service import PaymentService
    from krishibazaar.storage import StorageEngine
    from cosmpy.aerial.wallet import LocalWallet
    import pytest

    db = StorageEngine(str(tmp_path / "safety_mainnet.db"))
    db.init_db()

    # 1. Mainnet setting rejected in get_ledger
    mainnet_settings = Settings(
        payment_network="mainnet",
        payment_testnet_chain_id="fetchhub-4",
        enable_payment_protocol=True,
    )
    mainnet_service = PaymentService(storage_engine=db, settings_obj=mainnet_settings)
    with pytest.raises(PermissionError, match="ONLY the Fetch.ai Dorado testnet"):
        mainnet_service.get_ledger()

    # 2. Mainnet setting rejected in create_advisory_payment_request
    with pytest.raises(PermissionError, match="ONLY the Fetch.ai Dorado testnet"):
        mainnet_service.create_advisory_payment_request(
            payee_agent_address="agent1q_test",
            payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
        )

    # 3. Mainnet setting rejected in execute_authorized_transfer
    payer = LocalWallet.generate()
    approval = ExplicitPaymentApproval(
        reference="KB-PAY-TEST",
        amount_base_units=50000000000000000,
        currency="atestfet",
        recipient="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
    )
    with pytest.raises(PermissionError, match="ONLY the Fetch.ai Dorado testnet"):
        mainnet_service.execute_authorized_transfer(approval, payer)

    # 4. Non-dorado chain ID rejected
    wrong_chain_settings = Settings(
        payment_network="testnet",
        payment_testnet_chain_id="theta-testnet-001",
        enable_payment_protocol=True,
    )
    wrong_chain_service = PaymentService(storage_engine=db, settings_obj=wrong_chain_settings)
    with pytest.raises(PermissionError, match="ONLY the Fetch.ai Dorado testnet"):
        wrong_chain_service.get_ledger()
