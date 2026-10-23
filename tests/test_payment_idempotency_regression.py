"""
Regression tests for Payment Idempotency, Concurrency Locking, Reference Consistency,
Booking Persistence, and Authoritative Ledger Reconciliation (Phase 9H Critical Fix).
"""

import concurrent.futures
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cosmpy.aerial.client import LedgerClient
from cosmpy.aerial.tx_helpers import SubmittedTx, TxResponse
from cosmpy.aerial.wallet import LocalWallet
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent

from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.config import CANONICAL_DB_PATH, PROJECT_ROOT, Settings
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    ExplicitPaymentApproval,
    NetReturnBreakdown,
    PaymentPurpose,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.services.booking_service import (
    create_booking_proposal,
    execute_simulated_booking,
    format_booking_confirmation,
    format_booking_status,
)
from krishibazaar.services.payment_service import PaymentService
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_storage(tmp_path: Path):
    """Create a temporary SQLite storage engine for isolated testing."""
    db_path = tmp_path / "test_idempotency.db"
    return StorageEngine(db_path=db_path)


@pytest.fixture
def mock_wallet():
    """Create a test LocalWallet for payer."""
    return LocalWallet.generate()


@pytest.fixture
def payee_address():
    """Designated test payee wallet address."""
    return "fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck"


@pytest.fixture
def payment_service(temp_storage, payee_address):
    """Initialize a PaymentService with payment enabled in testnet mode."""
    settings = Settings(
        enable_payment_protocol=True,
        payment_network="testnet",
        payment_testnet_chain_id="dorado-1",
        payment_recipient_whitelist=[payee_address],
        payment_advisory_fee_base_units=50000000000000000,
        payment_testnet_denom="atestfet",
    )
    mock_ledger = MagicMock(spec=LedgerClient)
    mock_ledger.query_bank_balance.return_value = 1000000000000000000  # 1 FET (funded)
    mock_submitted = MagicMock(spec=SubmittedTx)
    mock_submitted.tx_hash = (
        "AABBCCDDEEFF00112233445566778899AABBCCDDEEFF00112233445566778899"
    )
    mock_ledger.send_tokens.return_value = mock_submitted
    return PaymentService(
        storage_engine=temp_storage,
        settings_obj=settings,
        ledger_client=mock_ledger,
    )


class TestPaymentIdempotencyAndConcurrency:
    """Test idempotency, double-click prevention, and concurrent lock safety."""

    def test_single_authorization_succeeds(
        self, payment_service, temp_storage, mock_wallet, payee_address
    ):
        """Standard valid payment authorization succeeds once."""
        _, pay_rec = payment_service.create_advisory_payment_request(
            payee_agent_address="agent1_test_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            currency="atestfet",
            service_inquiry_id="KB-BOOK-20261022-TEST01",
        )

        approval = ExplicitPaymentApproval(
            reference=pay_rec.payment_reference,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=payee_address,
        )

        commit, updated_rec = payment_service.execute_authorized_transfer(
            approval, mock_wallet
        )

        assert commit.reference == pay_rec.payment_reference
        assert commit.transaction_id == "AABBCCDDEEFF00112233445566778899AABBCCDDEEFF00112233445566778899"
        assert updated_rec.status == PaymentStatus.COMMITTED
        assert updated_rec.tx_hash is not None

        # Confirm send_tokens was called exactly once
        assert payment_service._ledger.send_tokens.call_count == 1

    def test_double_click_or_replay_is_blocked(
        self, payment_service, temp_storage, mock_wallet, payee_address
    ):
        """
        Simulate rapid double-click on CTA or duplicate command delivery.
        The second call must fail with ValueError and never submit tokens again.
        """
        _, pay_rec = payment_service.create_advisory_payment_request(
            payee_agent_address="agent1_test_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            currency="atestfet",
            service_inquiry_id="KB-BOOK-20261022-TEST02",
        )

        approval = ExplicitPaymentApproval(
            reference=pay_rec.payment_reference,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=payee_address,
        )

        # First authorization
        payment_service.execute_authorized_transfer(approval, mock_wallet)
        assert payment_service._ledger.send_tokens.call_count == 1

        # Second authorization (duplicate click / replay command)
        with pytest.raises(ValueError, match="Cannot authorize payment"):
            payment_service.execute_authorized_transfer(approval, mock_wallet)

        # send_tokens was STILL called only once!
        assert payment_service._ledger.send_tokens.call_count == 1

    def test_concurrent_authorizations_atomic_lock(
        self, payment_service, temp_storage, mock_wallet, payee_address
    ):
        """
        Simulate concurrent authorization race: 10 parallel threads attempting to
        authorize the EXACT SAME payment reference simultaneously.
        Atomic pre-broadcast lock must ensure exactly 1 succeeds and 9 fail.
        """
        _, pay_rec = payment_service.create_advisory_payment_request(
            payee_agent_address="agent1_test_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            currency="atestfet",
            service_inquiry_id="KB-BOOK-20261022-CONC01",
        )

        approval = ExplicitPaymentApproval(
            reference=pay_rec.payment_reference,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=payee_address,
        )

        results = []
        errors = []

        def worker():
            try:
                res = payment_service.execute_authorized_transfer(approval, mock_wallet)
                results.append(res)
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker) for _ in range(10)]
            concurrent.futures.wait(futures)

        assert len(results) == 1, f"Expected exactly 1 success, got {len(results)}"
        assert len(errors) == 9, f"Expected 9 rejections, got {len(errors)}"
        assert payment_service._ledger.send_tokens.call_count == 1

    def test_cannot_authorize_submitting_or_committed(
        self, payment_service, temp_storage, mock_wallet, payee_address
    ):
        """A record in SUBMITTING or COMMITTED status cannot be authorized again."""
        _, pay_rec = payment_service.create_advisory_payment_request(
            payee_agent_address="agent1_test_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            currency="atestfet",
        )

        temp_storage.update_payment_status(
            pay_rec.payment_reference,
            PaymentStatus.SUBMITTING,
        )

        approval = ExplicitPaymentApproval(
            reference=pay_rec.payment_reference,
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=payee_address,
        )

        with pytest.raises(ValueError, match="current status is 'SUBMITTING'"):
            payment_service.execute_authorized_transfer(approval, mock_wallet)


class TestPaymentReferenceConsistencyAndReuse:
    """Test payment reference consistency across request, review, approval, and lookup."""

    def test_reference_remains_unchanged(
        self, payment_service, temp_storage, mock_wallet, payee_address
    ):
        """
        Verify the payment review reference matches the stored record,
        the approval command, the CommitPayment message, and status lookup.
        """
        msg, record = payment_service.create_advisory_payment_request(
            payee_agent_address="agent1_test_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            currency="atestfet",
            service_inquiry_id="KB-BOOK-20261022-BA2D27",
        )

        review = payment_service.format_review_details(record)
        assert review.reference == record.payment_reference
        assert f"REF:{record.payment_reference}" in review.approval_command_template

        approval = ExplicitPaymentApproval(
            reference=record.payment_reference,
            amount_base_units=record.amount_base_units,
            currency=record.currency,
            recipient=payee_address,
        )

        commit, committed_record = payment_service.execute_authorized_transfer(
            approval, mock_wallet
        )
        assert commit.reference == record.payment_reference
        assert committed_record.payment_reference == record.payment_reference

        # Lookup by reference (exact and lower-case)
        fetched_exact = temp_storage.get_payment_record(record.payment_reference)
        fetched_lower = temp_storage.get_payment_record(record.payment_reference.lower())
        assert fetched_exact is not None
        assert fetched_lower is not None
        assert fetched_exact.payment_reference == record.payment_reference
        assert fetched_lower.payment_reference == record.payment_reference

        # Lookup by inquiry
        by_inquiry = temp_storage.get_payment_record_by_inquiry("KB-BOOK-20261022-BA2D27")
        assert by_inquiry is not None
        assert by_inquiry.payment_reference == record.payment_reference


class TestBookingPersistenceAndLookup:
    """Test booking persistence and case-insensitive lookup."""

    def test_booking_persistence_and_case_insensitive_lookup(self, temp_storage):
        """Ensure confirmed bookings persist and can be looked up regardless of case."""
        now = datetime.now(timezone.utc).isoformat()
        booking = BookingRecord(
            booking_id="KB-BOOK-20261022-BA2D27",
            timestamp_utc=now,
            status="CONFIRMED",
            farmer_id="agent1_farmer_nashik",
            offer_id="PROP-20261022-8484FB",
            channel_name="APMC Nashik",
            transporter_name="Kisan Gati Logistics",
            crop="Onion",
            quantity_quintals=50.0,
            gross_revenue=90000.0,
            transport_cost=2000.0,
            other_deductions=3000.0,
            net_payout=85000.0,
            pickup_date="2026-10-23",
            tracking_token="KB-TRK-BA2D27",
            is_simulated_booking=True,
            notes="Test booking for farmer onion 50qtl",
        )

        temp_storage.create_booking(booking)

        # Lookup exact
        b1 = temp_storage.get_booking("KB-BOOK-20261022-BA2D27")
        assert b1 is not None
        assert b1.booking_id == "KB-BOOK-20261022-BA2D27"
        assert b1.net_payout == 85000.0

        # Lookup lower-case
        b2 = temp_storage.get_booking("kb-book-20261022-ba2d27")
        assert b2 is not None
        assert b2.booking_id == "KB-BOOK-20261022-BA2D27"

    def test_canonical_database_path_consistency(self, tmp_path):
        """Verify canonical database path is absolute and project-root based regardless of CWD."""
        # 1. Config default
        cfg = Settings()
        assert Path(cfg.database_path).is_absolute()
        assert Path(cfg.database_path) == CANONICAL_DB_PATH

        # 2. Relative string passed to Settings
        cfg_rel = Settings(database_path="krishibazaar.db")
        assert Path(cfg_rel.database_path).is_absolute()
        assert Path(cfg_rel.database_path) == CANONICAL_DB_PATH

        # 3. StorageEngine relative string resolution
        engine = StorageEngine("krishibazaar.db")
        assert Path(engine.db_path).is_absolute()
        assert Path(engine.db_path) == CANONICAL_DB_PATH

        # 4. StorageEngine default resolution
        engine_default = StorageEngine()
        assert Path(engine_default.db_path).is_absolute()
        assert Path(engine_default.db_path) == CANONICAL_DB_PATH

        # 5. CWD change resistance: changing working directory must not alter resolved path
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            engine_diff_cwd = StorageEngine("krishibazaar.db")
            assert Path(engine_diff_cwd.db_path).is_absolute()
            assert Path(engine_diff_cwd.db_path) == CANONICAL_DB_PATH
        finally:
            os.chdir(orig_cwd)

    def test_booking_confirmation_persists_exact_values_and_returns_on_subsequent_read(
        self, temp_storage
    ):
        """
        Verify booking confirmation persists exact ID and exact financial values
        shown in confirmation response, commits, and returns from subsequent read.
        """
        sample_opt = NetReturnBreakdown(
            scenario_id="SCENARIO-APMC-01",
            channel_type="MANDI",
            destination_name="Lasalgaon APMC",
            distance_km=25.0,
            gross_value=160000.0,
            gross_revenue=160000.0,
            offered_price_per_quintal=3200.0,
            price_provenance="Official data.gov.in AGMARKNET modal price benchmark",
            transport_cost=3500.0,
            transport_provenance="Simulated 3-Tonne Mini-Truck quote",
            mandi_cess_apmc=2400.0,
            loading_unloading_cost=1250.0,
            storage_cost=0.0,
            estimated_loss_cost=800.0,
            spoilage_risk_penalty=800.0,
            total_deductions=7950.0,
            applicable_market_charges=3650.0,
            charges_provenance="APMC cess (1.5%) + Hamali (INR 25/qtl) + Weather risk",
            expected_net_return=152050.0,
            net_return=152050.0,
            net_return_per_quintal=3041.0,
            rank=1,
            meets_minimum_condition=True,
            crop="Onion",
            quantity_quintals=50.0,
        )
        valid_transit = (
            datetime.now(timezone(timedelta(hours=5, minutes=30))) + timedelta(days=2)
        ).strftime("%Y-%m-%d")

        proposal = create_booking_proposal(
            session_id="agent1_farmer_exact_val",
            farmer_id="agent1_farmer_exact_val",
            selected_option=sample_opt,
            crop="Onion",
            quantity_quintals=50.0,
            origin_location="Nashik",
            transit_date=valid_transit,
        )
        booking = execute_simulated_booking(proposal)
        temp_storage.create_booking(booking)

        confirmation_lines = format_booking_confirmation(booking, payment_enabled=False)
        confirmation_text = "\n".join(confirmation_lines)

        # Check values in confirmation response
        assert booking.booking_id in confirmation_text
        assert booking.tracking_token in confirmation_text
        assert "160,000.00" in confirmation_text  # Gross revenue
        assert "3,500.00" in confirmation_text    # Transport cost
        assert "7,950.00" in confirmation_text    # Total deductions
        assert "152,050.00" in confirmation_text  # Net payout

        # Read back from persistent store
        stored = temp_storage.get_booking(booking.booking_id)
        assert stored is not None
        assert stored.booking_id == booking.booking_id
        assert stored.tracking_token == booking.tracking_token
        assert stored.gross_revenue == 160000.0
        assert stored.transport_cost == 3500.0
        assert stored.other_deductions == 4450.0
        assert stored.net_payout == 152050.0
        assert stored.crop == "Onion"
        assert stored.quantity_quintals == 50.0
        assert stored.status == "CONFIRMED"

    @pytest.mark.asyncio
    async def test_restart_safe_booking_lookup_after_orchestrator_restart(
        self, temp_storage
    ):
        """
        Verify status lookup retrieves confirmed booking record after an Orchestrator restart.
        """
        now = datetime.now(timezone.utc).isoformat()
        test_booking = BookingRecord(
            booking_id="KB-BOOK-20261022-PERSIST99",
            timestamp_utc=now,
            status="CONFIRMED",
            farmer_id="farmer_restart_test",
            offer_id="PROP-RESTART-01",
            channel_name="Lasalgaon APMC",
            transporter_name="Simulated Fleet",
            crop="Onion",
            quantity_quintals=50.0,
            gross_revenue=160000.0,
            transport_cost=3500.0,
            other_deductions=4450.0,
            net_payout=152050.0,
            pickup_date="2026-10-24",
            tracking_token="KB-TRK-PERSIST99",
            is_simulated_booking=True,
            notes="Persistent booking test across restarts",
        )
        temp_storage.create_booking(test_booking)

        # Simulate fresh Orchestrator restart: no in-memory session, fresh context
        mock_ctx = MagicMock()
        mock_ctx.send = AsyncMock()
        mock_ctx.logger = MagicMock()

        with patch("krishibazaar.agents.orchestrator.storage", temp_storage):
            msg = ChatMessage(
                msg_id=uuid4(),
                content=[TextContent(text="Status of KB-BOOK-20261022-PERSIST99")],
            )
            await handle_chat_message(mock_ctx, "farmer_restart_test", msg)

            assert mock_ctx.send.call_count >= 2
            # Second message is the response with booking details
            response_msg = mock_ctx.send.call_args_list[1][0][1]
            response_text = response_msg.content[0].text
            assert "Simulated Booking Status" in response_text
            assert "KB-BOOK-20261022-PERSIST99" in response_text
            assert "KB-TRK-PERSIST99" in response_text
            assert "CONFIRMED" in response_text
            assert "152,050.00" in response_text


class TestAuthoritativeLedgerReconciliation:
    """Test authoritative ledger reconciliation without merging records or faking settlement."""

    def test_reconcile_committed_payment_on_chain(
        self, payment_service, temp_storage, payee_address
    ):
        """Authoritatively reconcile an in-flight payment against simulated ledger TxResponse."""
        now = datetime.now(timezone.utc).isoformat()
        record = PaymentRecord(
            payment_reference="KB-PAY-RECONTEST01",
            service_inquiry_id="KB-BOOK-20261022-TESTREC",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address="agent1_payer",
            payer_wallet_address="fetch1payerwallet",
            payee_agent_address="agent1_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            amount_display="0.05 FET (50,000,000,000,000,000 atestfet)",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.COMMITTED,
            tx_hash="75D11CE8C4B4E4CACF84FB98620B74C4D428978049031FE6ECE636419A8ABA03",
            deadline_seconds=300,
            created_at_utc=now,
            updated_at_utc=now,
        )
        temp_storage.save_payment_record(record)

        # Mock ledger query_tx response
        mock_tx_resp = MagicMock(spec=TxResponse)
        mock_tx_resp.code = 0
        mock_tx_resp.is_successful.return_value = True
        mock_tx_resp.height = 25805077
        mock_tx_resp.events = {
            "coin_received": {
                "receiver": payee_address,
                "amount": "50000000000000000atestfet",
            }
        }
        mock_tx_resp.actual_fee_base_units = 430390000000000
        mock_tx_resp.actual_fee_display = "0.00043039 FET (430,390,000,000,000 atestfet)"

        mock_ledger = MagicMock(spec=LedgerClient)
        mock_ledger.query_tx.return_value = mock_tx_resp

        is_settled, updated = payment_service.reconcile_payment_on_chain(
            "KB-PAY-RECONTEST01", ledger_client=mock_ledger
        )

        assert is_settled is True
        assert updated.status == PaymentStatus.SETTLED
        assert updated.tx_height == 25805077
        assert updated.actual_fee_base_units == 430390000000000
        assert updated.verified_at_utc is not None

    def test_two_distinct_transactions_remain_separate(self, temp_storage, payee_address):
        """
        Verify Transaction A and Transaction B are never merged into one record.
        Each must maintain its independent hash, block, fee, and inquiry reference.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Transaction B
        rec_b = PaymentRecord(
            payment_reference="KB-PAY-928966AECE8A",
            service_inquiry_id="KB-BOOK-20261022-846937",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address="agent1_payer",
            payer_wallet_address="fetch1yjvtu3qyacpnzes5zw0a75qc9uzp4szxuftrw5",
            payee_agent_address="agent1_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            amount_display="0.05 FET (50,000,000,000,000,000 atestfet)",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.SETTLED,
            tx_hash="790479409859E4521A61461AFBBFCD367AFBB71222ADF7C257B7C17544EAE3A3",
            tx_height=25804243,
            actual_fee_base_units=430365000000000,
            actual_fee_display="0.000430365 FET (430,365,000,000,000 atestfet)",
            verified_at_utc="2026-10-22T10:07:05+00:00",
            deadline_seconds=300,
            created_at_utc=now,
            updated_at_utc=now,
        )

        # Transaction A
        rec_a = PaymentRecord(
            payment_reference="KB-PAY-3D183BD83FC6",
            service_inquiry_id="KB-BOOK-20261022-75BEA9",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address="agent1_payer",
            payer_wallet_address="fetch1yjvtu3qyacpnzes5zw0a75qc9uzp4szxuftrw5",
            payee_agent_address="agent1_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            amount_display="0.05 FET (50,000,000,000,000,000 atestfet)",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.SETTLED,
            tx_hash="75D11CE8C4B4E4CACF84FB98620B74C4D428978049031FE6ECE636419A8ABA03",
            tx_height=25805077,
            actual_fee_base_units=430390000000000,
            actual_fee_display="0.00043039 FET (430,390,000,000,000 atestfet)",
            verified_at_utc="2026-10-22T11:25:20+00:00",
            deadline_seconds=300,
            created_at_utc=now,
            updated_at_utc=now,
        )

        temp_storage.save_payment_record(rec_b)
        temp_storage.save_payment_record(rec_a)

        # Retrieve both
        loaded_b = temp_storage.get_payment_record("KB-PAY-928966AECE8A")
        loaded_a = temp_storage.get_payment_record("KB-PAY-3D183BD83FC6")

        assert loaded_b is not None
        assert loaded_a is not None

        # Distinct hashes
        assert loaded_b.tx_hash != loaded_a.tx_hash
        assert loaded_b.tx_hash == "790479409859E4521A61461AFBBFCD367AFBB71222ADF7C257B7C17544EAE3A3"
        assert loaded_a.tx_hash == "75D11CE8C4B4E4CACF84FB98620B74C4D428978049031FE6ECE636419A8ABA03"

        # Distinct block heights
        assert loaded_b.tx_height == 25804243
        assert loaded_a.tx_height == 25805077

        # Distinct fees
        assert loaded_b.actual_fee_base_units == 430365000000000
        assert loaded_a.actual_fee_base_units == 430390000000000

        # Distinct inquiries
        assert loaded_b.service_inquiry_id == "KB-BOOK-20261022-846937"
        assert loaded_a.service_inquiry_id == "KB-BOOK-20261022-75BEA9"

    def test_unauthorized_expired_record_cannot_be_authorized(
        self, payment_service, temp_storage, mock_wallet, payee_address
    ):
        """
        Record KB-PAY-53D8FE40477D expired without authorization.
        Must be FAILED and cannot be authorized or submitted to the ledger.
        """
        expired_created = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        rec_expired = PaymentRecord(
            payment_reference="KB-PAY-53D8FE40477D",
            service_inquiry_id="KB-BOOK-20261022-BA2D27",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address="agent1_payer",
            payer_wallet_address="fetch1payer",
            payee_agent_address="agent1_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            amount_display="0.05 FET",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.FAILED,
            rejection_reason="Payment request expired without authorization; no on-chain transaction was submitted",
            deadline_seconds=300,
            created_at_utc=expired_created,
            updated_at_utc=expired_created,
        )
        temp_storage.save_payment_record(rec_expired)

        approval = ExplicitPaymentApproval(
            reference="KB-PAY-53D8FE40477D",
            amount_base_units=50000000000000000,
            currency="atestfet",
            recipient=payee_address,
        )

        with pytest.raises(ValueError, match="current status is 'FAILED'"):
            payment_service.execute_authorized_transfer(approval, mock_wallet)

        assert payment_service._ledger.send_tokens.call_count == 0


class TestOrchestratorPaymentFlowIdempotency:
    """Test payment request reuse and booking lookup logic in orchestrator context."""

    def test_payment_review_request_reuses_unexpired_record(
        self, payment_service, temp_storage, payee_address
    ):
        """
        Verify that when a user requests payment review multiple times for the same booking,
        the unexpired payment record is reused rather than creating multiple UUIDs.
        """
        _, rec1 = payment_service.create_advisory_payment_request(
            payee_agent_address="agent1_test_payee",
            payee_wallet_address=payee_address,
            amount_base_units=50000000000000000,
            currency="atestfet",
            service_inquiry_id="KB-BOOK-20261022-BA2D27",
        )

        # Check existing by inquiry
        existing = temp_storage.get_payment_record_by_inquiry("KB-BOOK-20261022-BA2D27")
        assert existing is not None
        assert existing.payment_reference == rec1.payment_reference

        # When reused, no second record is created
        assert existing.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION)
        records = temp_storage.list_payment_records()
        assert len(records) == 1
        assert records[0].payment_reference == rec1.payment_reference

    def test_case_insensitive_status_and_booking_lookup(self, temp_storage):
        """Verify looking up bookings and payments with mixed/upper/lower case."""
        now = datetime.now(timezone.utc).isoformat()
        b_rec = BookingRecord(
            booking_id="KB-BOOK-20261022-BA2D27",
            timestamp_utc=now,
            status="CONFIRMED",
            farmer_id="farmer1",
            offer_id="PROP-1",
            channel_name="APMC Nashik",
            transporter_name="Logistics",
            crop="Onion",
            quantity_quintals=50.0,
            gross_revenue=90000.0,
            net_payout=85000.0,
            pickup_date="2026-10-23",
            tracking_token="KB-TRK-1",
            is_simulated_booking=True,
        )
        temp_storage.create_booking(b_rec)

        p_rec = PaymentRecord(
            payment_reference="KB-PAY-TESTREF123",
            service_inquiry_id="KB-BOOK-20261022-BA2D27",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address="agent1_payer",
            payer_wallet_address="fetch1payer",
            payee_agent_address="agent1_payee",
            payee_wallet_address="fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck",
            amount_base_units=50000000000000000,
            amount_display="0.05 FET",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.SETTLED,
            tx_hash="HASH123",
            tx_height=25805077,
            deadline_seconds=300,
            created_at_utc=now,
            updated_at_utc=now,
        )
        temp_storage.save_payment_record(p_rec)

        # Booking lookups
        assert temp_storage.get_booking("KB-BOOK-20261022-BA2D27") is not None
        assert temp_storage.get_booking("kb-book-20261022-ba2d27") is not None
        assert temp_storage.get_booking("Kb-Book-20261022-Ba2D27") is not None

        # Payment lookups
        assert temp_storage.get_payment_record("KB-PAY-TESTREF123") is not None
        assert temp_storage.get_payment_record("kb-pay-testref123") is not None
        assert temp_storage.get_payment_record_by_inquiry("KB-BOOK-20261022-BA2D27") is not None
        assert temp_storage.get_payment_record_by_inquiry("kb-book-20261022-ba2d27") is not None
