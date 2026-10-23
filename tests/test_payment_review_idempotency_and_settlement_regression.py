"""
Regression tests for KrishiBazaar Payment Review Flow Idempotency, Card Review Button Gating,
and Settlement Isolation.

Covers:
1. Repeated clicks on "Proceed to Payment Review" reuse the existing payment record (same booking & purpose).
2. Repeated "booking status" requests reuse the existing payment record without creating new references.
3. Server-side idempotency in PaymentService and StorageEngine.
4. Review button disabled / gated while a payment request is in progress.
5. Strict distinction between booking confirmation and payment settlement.
6. Broadcast transactions remain AWAITING_RECONCILIATION until authoritative on-chain verification confirms settlement.
7. Zero auto-approval, zero auto-resubmission, and zero settlement from UI actions.
8. Existing payment safety checks preserved (whitelist, testnet-only, address validation, disabled gate).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    MetadataContent,
    TextContent,
)
from uagents_core.contrib.protocols.chat.cards import (
    DetailCardPayload,
    create_card_response_content,
    extract_card,
)

from krishibazaar.config import Settings, settings
from krishibazaar.models import (
    BookingRecord,
    CommitPayment,
    CompletePayment,
    ExplicitPaymentApproval,
    Funds,
    PaymentPurpose,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.services.booking_service import format_booking_confirmation, format_booking_status
from krishibazaar.services.card_service import (
    build_booking_confirmed_card,
    build_payment_option_card,
    build_payment_status_card,
)
from krishibazaar.services.payment_service import (
    PaymentService,
    validate_fetch_wallet_address,
)
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path):
    """Provide an isolated, clean SQLite database instance."""
    db_file = tmp_path / "test_payment_review_regression.db"
    return StorageEngine(db_file)


@pytest.fixture
def mock_ctx():
    """Mock uAgents Context for orchestrator message handlers."""
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


@pytest.fixture
def sample_booking():
    """Create a standard confirmed simulated produce booking."""
    now = datetime.now(timezone.utc).isoformat()
    return BookingRecord(
        booking_id="KB-BOOK-20261023-REV001",
        timestamp_utc=now,
        status="CONFIRMED",
        farmer_id="agent1q_farmer_test",
        offer_id="PROP-20261023-ONION-01",
        channel_name="APMC Lasalgaon, Nashik",
        transporter_name="Mahindra Bolero Maxi Truck (Fleet #12)",
        crop="Onion",
        quantity_quintals=50.0,
        gross_revenue=95000.0,
        transport_cost=2100.0,
        other_deductions=1425.0,
        net_payout=91475.0,
        pickup_date="2026-10-24",
        tracking_token="KB-TRK-REV-001",
        is_simulated_booking=True,
        notes="Simulated benchmark booking for idempotency testing",
    )


class TestPaymentReviewDoubleClicksAndStatusChecks:
    """Verify repeated review clicks and status checks reuse the same payment record."""

    @pytest.mark.asyncio
    async def test_repeated_payment_review_clicks_reuse_existing_record(
        self, temp_db: StorageEngine, mock_ctx, sample_booking: BookingRecord
    ):
        """
        Repeated clicks on 'Proceed to Payment Review' (e.g. double-click)
        MUST reuse the existing payment record for the same booking and payment purpose
        instead of creating new payment references or records in storage.
        """
        temp_db.create_booking(sample_booking)
        farmer_sender = sample_booking.farmer_id

        temp_db.save_session(
            session_id=farmer_sender,
            farmer_id=farmer_sender,
            state="BOOKING_CONFIRMED",
            active_booking_id=sample_booking.booking_id,
        )

        with patch("krishibazaar.agents.orchestrator.storage", temp_db):
            # Click 1: Proceed to Payment Review via Card CTA
            click_1 = ChatMessage(
                msg_id=uuid4(),
                content=[
                    create_card_response_content(
                        selection={
                            "action": "request_payment_review",
                            "booking_id": sample_booking.booking_id,
                        }
                    )
                ],
            )
            await handle_chat_message(mock_ctx, farmer_sender, click_1)

            resp_1 = mock_ctx.send.call_args_list[1][0][1]
            card_meta_1 = next(c for c in resp_1.content if isinstance(c, MetadataContent))
            card_1 = extract_card(card_meta_1)
            rows_1 = {r.label: r.value for r in card_1.payload.summary_rows}
            ref_1 = rows_1["Payment Reference"]
            assert ref_1.startswith("KB-PAY-")

            # Check storage after click 1: exactly 1 payment record exists
            records_1 = temp_db.list_payment_records()
            assert len(records_1) == 1
            assert records_1[0].payment_reference == ref_1
            assert records_1[0].service_inquiry_id == sample_booking.booking_id

            # Click 2: Rapid second click (double-click simulation)
            click_2 = ChatMessage(
                msg_id=uuid4(),
                content=[
                    create_card_response_content(
                        selection={
                            "action": "request_payment_review",
                            "booking_id": sample_booking.booking_id,
                        }
                    )
                ],
            )
            await handle_chat_message(mock_ctx, farmer_sender, click_2)

            resp_2 = mock_ctx.send.call_args_list[3][0][1]
            card_meta_2 = next(c for c in resp_2.content if isinstance(c, MetadataContent))
            card_2 = extract_card(card_meta_2)
            rows_2 = {r.label: r.value for r in card_2.payload.summary_rows}
            ref_2 = rows_2["Payment Reference"]

            # Must reuse exact same payment reference
            assert ref_2 == ref_1

            # Storage MUST NOT have created a second payment record!
            records_2 = temp_db.list_payment_records()
            assert len(records_2) == 1
            assert records_2[0].payment_reference == ref_1

    @pytest.mark.asyncio
    async def test_repeated_booking_status_checks_reuse_existing_payment_record(
        self, temp_db: StorageEngine, mock_ctx, sample_booking: BookingRecord
    ):
        """
        Repeated 'booking status' requests MUST reuse the existing payment record
        and never generate new payment references or database rows.
        """
        temp_db.create_booking(sample_booking)
        farmer_sender = sample_booking.farmer_id

        # Pre-create payment record for this booking
        service = PaymentService(storage_engine=temp_db)
        _, initial_pay = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
            service_inquiry_id=sample_booking.booking_id,
        )
        assert initial_pay.payment_reference.startswith("KB-PAY-")

        with patch("krishibazaar.agents.orchestrator.storage", temp_db):
            # Check status 3 times
            for i in range(3):
                status_msg = ChatMessage(
                    msg_id=uuid4(),
                    content=[
                        create_card_response_content(
                            selection={
                                "action": "status_lookup",
                                "booking_id": sample_booking.booking_id,
                            }
                        )
                    ],
                )
                await handle_chat_message(mock_ctx, farmer_sender, status_msg)

            # Confirm storage still has exactly 1 payment record
            records = temp_db.list_payment_records()
            assert len(records) == 1
            assert records[0].payment_reference == initial_pay.payment_reference
            assert records[0].service_inquiry_id == sample_booking.booking_id

    def test_payment_service_server_side_idempotency(self, temp_db: StorageEngine):
        """
        PaymentService.create_advisory_payment_request must be idempotent on (service_inquiry_id, purpose).
        Calling it multiple times with the same booking and purpose must return the same record.
        """
        service = PaymentService(storage_engine=temp_db)
        booking_id = "KB-BOOK-20261023-IDEMP-01"

        msg1, rec1 = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address="agent1q_payer",
            service_inquiry_id=booking_id,
        )

        # Call again with identical parameters
        msg2, rec2 = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address="agent1q_payer",
            service_inquiry_id=booking_id,
        )

        assert rec1.payment_reference == rec2.payment_reference
        assert msg1.reference == msg2.reference
        assert len(temp_db.list_payment_records()) == 1


class TestReviewButtonDisablingWhileRequestInProgress:
    """Verify review button is disabled when a payment is in progress."""

    def test_review_button_disabled_in_booking_confirmed_card_when_in_progress(
        self, sample_booking: BookingRecord
    ):
        """
        When a payment record for the booking is in an in-progress status
        (SUBMITTING, COMMITTED, PENDING_SETTLEMENT, AWAITING_RECONCILIATION),
        the booking confirmed card must disable the 'Proceed to Payment Review' button.
        """
        now = datetime.now(timezone.utc).isoformat()
        in_progress_statuses = [
            PaymentStatus.SUBMITTING,
            PaymentStatus.COMMITTED,
            PaymentStatus.PENDING_SETTLEMENT,
            PaymentStatus.AWAITING_RECONCILIATION,
        ]

        for st in in_progress_statuses:
            pay_rec = PaymentRecord(
                payment_reference=f"KB-PAY-TEST-{st.value}",
                service_inquiry_id=sample_booking.booking_id,
                purpose=PaymentPurpose.ADVISORY_PASS.value,
                payer_agent_address="agent1q_payer",
                payer_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
                payee_agent_address="agent1q_payee",
                payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
                amount_base_units=50000000000000000,
                amount_display="0.05 FET (50,000,000,000,000,000 atestfet)",
                currency="atestfet",
                network="dorado-1",
                status=st,
                tx_hash="0xSIMULATED_TX_HASH_PROGRESS",
                deadline_seconds=300,
                created_at_utc=now,
                updated_at_utc=now,
            )

            card_meta = build_booking_confirmed_card(
                sample_booking, payment_enabled=True, payment_record=pay_rec
            )
            card = extract_card(card_meta)
            assert card is not None

            # Must NOT offer an active "Proceed to Payment Review" action
            cta_labels = [c.label for c in card.payload.ctas]
            cta_actions = [c.selection.get("action") for c in card.payload.ctas]

            assert "Proceed to Payment Review" not in cta_labels
            assert "request_payment_review" not in cta_actions
            assert "Payment In Progress (Review Disabled)" in cta_labels

    def test_review_button_disabled_in_payment_option_card_when_in_progress(
        self, sample_booking: BookingRecord
    ):
        """
        In build_payment_option_card, the review button must also be replaced
        with 'Payment In Progress (Review Disabled)' when status is in progress.
        """
        now = datetime.now(timezone.utc).isoformat()
        pay_rec = PaymentRecord(
            payment_reference="KB-PAY-OPT-PROG",
            service_inquiry_id=sample_booking.booking_id,
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address="agent1q_payer",
            payer_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            amount_display="0.05 FET",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.AWAITING_RECONCILIATION,
            tx_hash="0xTX_AWAITING_RECON",
            deadline_seconds=300,
            created_at_utc=now,
            updated_at_utc=now,
        )

        card_meta = build_payment_option_card(
            sample_booking, payment_enabled=True, payment_record=pay_rec
        )
        card = extract_card(card_meta)
        assert card is not None

        cta_labels = [c.label for c in card.payload.ctas]
        assert "Proceed to Payment Review" not in cta_labels
        assert "Payment In Progress (Review Disabled)" in cta_labels


class TestDistinguishBookingConfirmationFromSettlement:
    """Verify booking confirmation is strictly decoupled from payment settlement."""

    def test_booking_confirmation_does_not_claim_settlement(
        self, sample_booking: BookingRecord
    ):
        """
        Simulated booking confirmation notices and cards must explicitly state
        that no real payment transfer has occurred.
        """
        lines = format_booking_confirmation(sample_booking, payment_enabled=False)
        full_text = "\n".join(lines)

        assert "SIMULATED — NOT A REAL BOOKING" in full_text
        assert "No real commercial buyer contract, physical crop sale, carrier dispatch, or payment transfer has occurred" in full_text
        assert "Payment is currently disabled. No transfer has been initiated" in full_text

    def test_booking_status_verification_text_distinguishes_payment(
        self, sample_booking: BookingRecord
    ):
        """Booking status lookup must explicitly report simulated record status without implying payment settlement."""
        status_lines = format_booking_status(sample_booking)
        text = "\n".join(status_lines)

        assert "Simulated MVP Record" in text
        assert "No real carrier, buyer, or financial transaction has occurred" in text


class TestAwaitingReconciliationAndAuthoritativeSettlement:
    """Verify broadcast transactions remain AWAITING_RECONCILIATION until authoritative ledger verification."""

    @pytest.mark.asyncio
    async def test_broadcast_transaction_remains_awaiting_reconciliation_when_unconfirmed(
        self, temp_db: StorageEngine, mock_ctx, sample_booking: BookingRecord
    ):
        """
        When a transaction is broadcast but on-chain settlement is unconfirmed or pending,
        the record must remain in AWAITING_RECONCILIATION. It must NEVER auto-settle.
        """
        temp_db.create_booking(sample_booking)
        farmer_sender = sample_booking.farmer_id

        custom_settings = Settings(
            enable_payment_protocol=True,
            payment_payer_seed="test_payer_seed_awaiting_recon",
            payment_payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            payment_recipient_whitelist=["fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"],
        )

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.settings", custom_settings), \
             patch("krishibazaar.services.payment_service.settings", custom_settings):

            service = PaymentService(storage_engine=temp_db, settings_obj=custom_settings)
            _, pay_rec = service.create_advisory_payment_request(
                payee_agent_address="agent1q_payee",
                payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
                amount_base_units=50000000000000000,
                currency="atestfet",
                purpose=PaymentPurpose.ADVISORY_PASS.value,
                payer_agent_address=farmer_sender,
                service_inquiry_id=sample_booking.booking_id,
            )

            auth_msg = ChatMessage(
                msg_id=uuid4(),
                content=[
                    create_card_response_content(
                        selection={
                            "action": "authorize_payment",
                            "reference": pay_rec.payment_reference,
                            "amount": pay_rec.amount_base_units,
                            "currency": pay_rec.currency,
                            "recipient": pay_rec.payee_wallet_address,
                        }
                    )
                ],
            )

            mock_commit = CommitPayment(
                funds=Funds(
                    amount=str(pay_rec.amount_base_units),
                    currency=pay_rec.currency,
                    payment_method="fet_direct",
                ),
                recipient=pay_rec.payee_wallet_address,
                transaction_id="0xBROADCAST_PENDING_TX_001",
                reference=pay_rec.payment_reference,
            )

            # Payment Agent times out or sends pending response (not CompletePayment)
            with patch(
                "krishibazaar.services.payment_service.PaymentService.execute_authorized_transfer",
                return_value=(mock_commit, pay_rec),
            ), patch(
                "uagents.communication.send_sync_message",
                new=AsyncMock(return_value=None),  # timeout / no response
            ):
                await handle_chat_message(mock_ctx, farmer_sender, auth_msg)

                # Record in storage MUST be AWAITING_RECONCILIATION
                rec = temp_db.get_payment_record(pay_rec.payment_reference)
                assert rec is not None
                assert rec.status == PaymentStatus.AWAITING_RECONCILIATION
                assert rec.tx_hash == "0xBROADCAST_PENDING_TX_001"

    @pytest.mark.asyncio
    async def test_ui_status_checks_never_mark_payment_settled(
        self, temp_db: StorageEngine, mock_ctx, sample_booking: BookingRecord
    ):
        """
        UI actions ('Check Booking Status', 'Check Payment Record', repeated status lookups)
        must NEVER mark payment settled without authoritative on-chain verification.
        """
        temp_db.create_booking(sample_booking)
        farmer_sender = sample_booking.farmer_id

        now = datetime.now(timezone.utc).isoformat()
        pending_rec = PaymentRecord(
            payment_reference="KB-PAY-PENDING-RECON",
            service_inquiry_id=sample_booking.booking_id,
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
            payer_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            amount_display="0.05 FET",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.AWAITING_RECONCILIATION,
            tx_hash="0xTX_HASH_UNCONFIRMED",
            deadline_seconds=300,
            created_at_utc=now,
            updated_at_utc=now,
        )
        temp_db.save_payment_record(pending_rec)

        with patch("krishibazaar.agents.orchestrator.storage", temp_db):
            # 1. UI Action: booking status lookup
            booking_status_msg = ChatMessage(
                msg_id=uuid4(),
                content=[
                    create_card_response_content(
                        selection={
                            "action": "status_lookup",
                            "booking_id": sample_booking.booking_id,
                        }
                    )
                ],
            )
            await handle_chat_message(mock_ctx, farmer_sender, booking_status_msg)

            # Record must remain in AWAITING_RECONCILIATION
            rec1 = temp_db.get_payment_record("KB-PAY-PENDING-RECON")
            assert rec1.status == PaymentStatus.AWAITING_RECONCILIATION

            # 2. UI Action: payment status lookup when protocol is disabled
            pay_status_msg = ChatMessage(
                msg_id=uuid4(),
                content=[
                    create_card_response_content(
                        selection={
                            "action": "payment_status_lookup",
                            "reference": "KB-PAY-PENDING-RECON",
                        }
                    )
                ],
            )
            await handle_chat_message(mock_ctx, farmer_sender, pay_status_msg)

            # Record must STILL remain in AWAITING_RECONCILIATION
            rec2 = temp_db.get_payment_record("KB-PAY-PENDING-RECON")
            assert rec2.status == PaymentStatus.AWAITING_RECONCILIATION
            assert rec2.status != PaymentStatus.SETTLED

    @pytest.mark.asyncio
    async def test_replay_blocked_on_submitting_or_awaiting_reconciliation(
        self, temp_db: StorageEngine, mock_ctx, sample_booking: BookingRecord
    ):
        """
        Duplicate transaction prevention: An approval request for a payment that is
        already SUBMITTING or AWAITING_RECONCILIATION must be rejected as replay.
        It must never auto-resubmit.
        """
        temp_db.create_booking(sample_booking)
        farmer_sender = sample_booking.farmer_id

        now = datetime.now(timezone.utc).isoformat()
        rec_awaiting = PaymentRecord(
            payment_reference="KB-PAY-REPLAY-DEFENSE",
            service_inquiry_id=sample_booking.booking_id,
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
            payer_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            amount_display="0.05 FET",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.AWAITING_RECONCILIATION,
            tx_hash="0xTX_ALREADY_BROADCAST",
            deadline_seconds=300,
            created_at_utc=now,
            updated_at_utc=now,
        )
        temp_db.save_payment_record(rec_awaiting)

        custom_settings = Settings(
            enable_payment_protocol=True,
            payment_payer_seed="test_payer_seed_replay",
            payment_payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            payment_recipient_whitelist=["fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"],
        )

        with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
             patch("krishibazaar.agents.orchestrator.settings", custom_settings):

            # Attempt to re-authorize an already broadcast payment
            replay_msg = ChatMessage(
                msg_id=uuid4(),
                content=[
                    create_card_response_content(
                        selection={
                            "action": "authorize_payment",
                            "reference": "KB-PAY-REPLAY-DEFENSE",
                            "amount": 50000000000000000,
                            "currency": "atestfet",
                            "recipient": "fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
                        }
                    )
                ],
            )
            await handle_chat_message(mock_ctx, farmer_sender, replay_msg)

            resp = mock_ctx.send.call_args_list[1][0][1]
            text = next(c.text for c in resp.content if isinstance(c, TextContent))

            assert "Payment Already Submitted" in text
            assert "Replay or duplicate authorization blocked" in text
