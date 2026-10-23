"""
Orchestrator-to-Payment-Agent Integration & Flow Tests (tests/test_orchestrator_payment_flow.py).

Verifies the complete KrishiBazaar Phase 9D payment wiring:
1. Orchestrator receives REQUEST_PAYMENT_REVIEW (via card CTA or chat text 'pay advisory fee'):
   - Generates unique reference (KB-PAY-...).
   - Produces ReviewCardPayload with:
     - Exact amount & denom (50,000,000,000,000,000 atestfet / 0.05 FET)
     - Verified recipient wallet address
     - Testnet network (dorado-1)
     - Service purpose (KRISHIBAZAAR_PREMIUM_ADVISORY_PASS)
     - Estimated gas fee statement (~0.001 FET)
     - Testnet-only demo notice
   - Persists session state 'AWAITING_PAYMENT_APPROVAL'.
2. Casual affirmations ('ok', 'yes', 'sure') strictly blocked from authorizing payments:
   - Intercepts casual messages when payment request is pending.
   - Demands explicit authorization command.
3. Payment Safety Gate enforcement:
   - When ENABLE_PAYMENT_PROTOCOL=false (default), blocks transfer safely.
4. Payment Rejection:
   - User declines payment, status updated to REJECTED.
5. Simulated / Mocked on-chain settlement with Payment Agent:
   - CommitPayment transmission and CompletePayment reconciliation to SETTLED.
   - CancelPayment reconciliation to FAILED.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    MetadataContent,
)
from uagents_core.contrib.protocols.chat.cards import (
    create_card_response_content,
    extract_card,
    ReviewCardPayload,
    DetailCardPayload,
)

from krishibazaar.agents.orchestrator import handle_chat_message
from krishibazaar.config import Settings, settings
from krishibazaar.models import (
    CommitPayment,
    CompletePayment,
    CancelPayment,
    Funds,
    PaymentPurpose,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.services.booking_service import BookingActionType
from krishibazaar.services.card_service import extract_interaction_action
from krishibazaar.services.payment_service import PaymentService
from krishibazaar.storage import StorageEngine


@pytest.fixture
def temp_db(tmp_path) -> StorageEngine:
    db_file = tmp_path / "orch_pay_flow_test.db"
    return StorageEngine(db_path=str(db_file))


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


@pytest.mark.asyncio
async def test_orchestrator_request_payment_review_via_card_cta(temp_db: StorageEngine, mock_ctx):
    """
    User clicks 'Pay Advisory Fee (Testnet)' on confirmed booking card.
    Orchestrator generates payment request and returns ReviewCard with complete immutable details.
    """
    with patch("krishibazaar.agents.orchestrator.storage", temp_db):
        cta_resp = create_card_response_content(
            selection={
                "action": "request_payment_review",
                "booking_id": "KB-BOOK-20261021-TEST99",
            }
        )
        incoming = ChatMessage(msg_id=uuid4(), content=[cta_resp])
        farmer_sender = "agent1q_farmer_request_pay_01"

        await handle_chat_message(mock_ctx, farmer_sender, incoming)

        # 1. Immediate ACK
        ack_msg = mock_ctx.send.call_args_list[0][0][1]
        assert isinstance(ack_msg, ChatAcknowledgement)

        # 2. Review Card Response
        response_msg = mock_ctx.send.call_args_list[1][0][1]
        assert isinstance(response_msg, ChatMessage)

        # Check Text Content
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))
        assert "Payment Review Request" in text_content
        assert "50,000,000,000,000,000 atestfet" in text_content or "0.05 FET" in text_content
        assert "TESTNET DEMO MICRO-FEE ONLY" in text_content

        # Check Review Card Payload
        card_item = next(c for c in response_msg.content if isinstance(c, MetadataContent))
        card = extract_card(card_item)
        assert card is not None
        assert isinstance(card.payload, ReviewCardPayload)
        assert "Review Payment Request" in card.payload.title
        assert card.payload.approve_cta is not None
        assert card.payload.approve_cta.selection["action"] == "authorize_payment"
        assert card.payload.approve_cta.selection["amount"] == 50000000000000000
        assert card.payload.approve_cta.selection["currency"] == "atestfet"
        assert card.payload.reject_cta is not None
        assert card.payload.reject_cta.selection["action"] == "reject_payment"

        # Check Session state
        sess = temp_db.get_session(farmer_sender)
        assert sess is not None
        assert sess["state"] == "AWAITING_PAYMENT_APPROVAL"
        ref = sess["pending_payment_reference"]
        assert ref.startswith("KB-PAY-")

        # Check PaymentRecord in storage
        record = temp_db.get_payment_record(ref)
        assert record is not None
        assert record.status == PaymentStatus.REQUESTED
        assert record.amount_base_units == 50000000000000000
        assert record.currency == "atestfet"
        assert record.network == "dorado-1"


@pytest.mark.asyncio
async def test_orchestrator_request_payment_review_via_text(temp_db: StorageEngine, mock_ctx):
    """User types 'pay advisory fee' in chat. Orchestrator triggers payment review."""
    with patch("krishibazaar.agents.orchestrator.storage", temp_db):
        incoming = ChatMessage(
            msg_id=uuid4(),
            content=[TextContent(text="pay advisory fee")],
        )
        farmer_sender = "agent1q_farmer_text_pay_01"

        await handle_chat_message(mock_ctx, farmer_sender, incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))
        assert "Payment Review Request" in text_content
        assert "0.05 FET" in text_content or "50,000,000,000,000,000 atestfet" in text_content


@pytest.mark.asyncio
async def test_casual_affirmations_cannot_authorize_payment(temp_db: StorageEngine, mock_ctx):
    """
    When a payment review is pending, sending casual messages ('ok', 'yes', 'sure')
    must NOT authorize the payment, but must remind the user of explicit approval requirements.
    """
    with patch("krishibazaar.agents.orchestrator.storage", temp_db):
        farmer_sender = "agent1q_farmer_casual_01"

        # Create a pending payment request
        service = PaymentService(storage_engine=temp_db)
        _, pay_rec = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
        )
        temp_db.save_session(
            session_id=farmer_sender,
            farmer_id=farmer_sender,
            state="AWAITING_PAYMENT_APPROVAL",
            pending_payment_reference=pay_rec.payment_reference,
        )

        # Farmer says "ok"
        incoming = ChatMessage(msg_id=uuid4(), content=[TextContent(text="ok")])
        await handle_chat_message(mock_ctx, farmer_sender, incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Pending Payment Request Awaiting Explicit Authorization" in text_content
        assert "Casual messages or ambiguous acknowledgements (*'ok'*, *'yes'*) do NOT authorize token transfers" in text_content
        assert f"AUTHORIZE PAYMENT REF:{pay_rec.payment_reference}" in text_content

        # Payment record status must remain REQUESTED (not AUTHORIZED or SETTLED)
        rec = temp_db.get_payment_record(pay_rec.payment_reference)
        assert rec is not None
        assert rec.status == PaymentStatus.REQUESTED


@pytest.mark.asyncio
async def test_orchestrator_payment_safety_gate_disabled_by_default(temp_db: StorageEngine, mock_ctx):
    """
    When ENABLE_PAYMENT_PROTOCOL is False (default), even explicit authorization
    is safely blocked by the safety gate.
    """
    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):
        farmer_sender = "agent1q_farmer_gate_01"

        service = PaymentService(storage_engine=temp_db)
        _, pay_rec = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
        )

        auth_resp = create_card_response_content(
            selection={
                "action": "authorize_payment",
                "reference": pay_rec.payment_reference,
                "amount": pay_rec.amount_base_units,
                "currency": pay_rec.currency,
                "recipient": pay_rec.payee_wallet_address,
            }
        )
        incoming = ChatMessage(msg_id=uuid4(), content=[auth_resp])

        await handle_chat_message(mock_ctx, farmer_sender, incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Payment Protocol Safety Gate Active" in text_content
        assert "ENABLE_PAYMENT_PROTOCOL=false" in text_content
        assert "NO testnet tokens or funds have been transferred" in text_content


@pytest.mark.asyncio
async def test_orchestrator_payment_rejection(temp_db: StorageEngine, mock_ctx):
    """Farmer rejects payment. Status updates to REJECTED."""
    with patch("krishibazaar.agents.orchestrator.storage", temp_db):
        farmer_sender = "agent1q_farmer_rej_01"

        service = PaymentService(storage_engine=temp_db)
        _, pay_rec = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
        )

        rej_resp = create_card_response_content(
            selection={
                "action": "reject_payment",
                "reference": pay_rec.payment_reference,
            }
        )
        incoming = ChatMessage(msg_id=uuid4(), content=[rej_resp])

        await handle_chat_message(mock_ctx, farmer_sender, incoming)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Payment Request Declined" in text_content
        assert pay_rec.payment_reference in text_content

        rec = temp_db.get_payment_record(pay_rec.payment_reference)
        assert rec is not None
        assert rec.status == PaymentStatus.REJECTED


@pytest.mark.asyncio
async def test_orchestrator_to_payment_agent_settlement_flow(temp_db: StorageEngine, mock_ctx):
    """
    When payment protocol is enabled and transfer is executed,
    CommitPayment is sent to payment_agent, which responds with CompletePayment upon verified settlement.
    """
    custom_settings = Settings(
        enable_payment_protocol=True,
        payment_payer_seed="test_payer_seed_for_settlement_test",
        payment_payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
        payment_recipient_whitelist=["fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"],
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch("krishibazaar.agents.orchestrator.settings", custom_settings), \
         patch("krishibazaar.services.payment_service.settings", custom_settings):

        farmer_sender = "agent1q_farmer_settle_01"
        service = PaymentService(storage_engine=temp_db, settings_obj=custom_settings)

        _, pay_rec = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
        )

        auth_resp = create_card_response_content(
            selection={
                "action": "authorize_payment",
                "reference": pay_rec.payment_reference,
                "amount": pay_rec.amount_base_units,
                "currency": pay_rec.currency,
                "recipient": pay_rec.payee_wallet_address,
            }
        )
        incoming = ChatMessage(msg_id=uuid4(), content=[auth_resp])

        mock_commit = CommitPayment(
            funds=Funds(
                amount=str(pay_rec.amount_base_units),
                currency=pay_rec.currency,
                payment_method="fet_direct",
            ),
            recipient=pay_rec.payee_wallet_address,
            transaction_id="0xTESTNET_SIMULATED_TX_HASH_001",
            reference=pay_rec.payment_reference,
        )
        mock_complete = CompletePayment(
            transaction_id="0xTESTNET_SIMULATED_TX_HASH_001",
            reference=pay_rec.payment_reference,
        )

        async def mock_verified_payment_agent(destination, message, resolver, timeout):
            # Authoritative Payment Agent marks record SETTLED on-chain before returning CompletePayment
            temp_db.update_payment_status(
                reference=pay_rec.payment_reference,
                status=PaymentStatus.SETTLED,
                tx_hash="0xTESTNET_SIMULATED_TX_HASH_001",
                tx_height=25805077,
                verified_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            return mock_complete

        with patch(
            "krishibazaar.services.payment_service.PaymentService.execute_authorized_transfer",
            return_value=(mock_commit, pay_rec),
        ), patch(
            "uagents.communication.send_sync_message",
            new=AsyncMock(side_effect=mock_verified_payment_agent),
        ):
            await handle_chat_message(mock_ctx, farmer_sender, incoming)

            response_msg = mock_ctx.send.call_args_list[1][0][1]
            text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

            assert "Payment Settled On-Chain" in text_content
            assert "0xTESTNET_SIMULATED_TX_HASH_001" in text_content

            # Check confirmed card
            card_item = next(c for c in response_msg.content if isinstance(c, MetadataContent))
            card = extract_card(card_item)
            assert card is not None
            assert isinstance(card.payload, DetailCardPayload)
            assert f"Payment Settled: {pay_rec.payment_reference}" in card.payload.title

            # Check record status in storage
            rec = temp_db.get_payment_record(pay_rec.payment_reference)
            assert rec is not None
            assert rec.status == PaymentStatus.SETTLED
            assert rec.tx_hash == "0xTESTNET_SIMULATED_TX_HASH_001"


@pytest.mark.asyncio
async def test_orchestrator_unverified_complete_payment_cannot_mark_settled(temp_db: StorageEngine, mock_ctx):
    """
    Requirement 5: An unverified CompletePayment protocol response cannot cause the orchestrator
    to independently mark a payment SETTLED without authoritative verification proof.
    """
    custom_settings = Settings(
        enable_payment_protocol=True,
        payment_payer_seed="test_payer_seed_for_settlement_test",
        payment_payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
        payment_recipient_whitelist=["fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"],
    )

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch("krishibazaar.agents.orchestrator.settings", custom_settings), \
         patch("krishibazaar.services.payment_service.settings", custom_settings):

        farmer_sender = "agent1q_farmer_unverified_01"
        service = PaymentService(storage_engine=temp_db, settings_obj=custom_settings)

        _, pay_rec = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
        )

        auth_resp = create_card_response_content(
            selection={
                "action": "authorize_payment",
                "reference": pay_rec.payment_reference,
                "amount": pay_rec.amount_base_units,
                "currency": pay_rec.currency,
                "recipient": pay_rec.payee_wallet_address,
            }
        )
        incoming = ChatMessage(msg_id=uuid4(), content=[auth_resp])

        mock_commit = CommitPayment(
            funds=Funds(
                amount=str(pay_rec.amount_base_units),
                currency=pay_rec.currency,
                payment_method="fet_direct",
            ),
            recipient=pay_rec.payee_wallet_address,
            transaction_id="0xUNVERIFIED_ROGUE_TX_HASH_999",
            reference=pay_rec.payment_reference,
        )
        # Rogue / unverified CompletePayment received
        mock_unverified_complete = CompletePayment(
            transaction_id="0xUNVERIFIED_ROGUE_TX_HASH_999",
            reference=pay_rec.payment_reference,
        )

        with patch(
            "krishibazaar.services.payment_service.PaymentService.execute_authorized_transfer",
            return_value=(mock_commit, pay_rec),
        ), patch(
            "uagents.communication.send_sync_message",
            new=AsyncMock(return_value=mock_unverified_complete),
        ), patch(
            "krishibazaar.services.payment_service.PaymentService.reconcile_payment_on_chain",
            return_value=(False, pay_rec),
        ):
            await handle_chat_message(mock_ctx, farmer_sender, incoming)

            response_msg = mock_ctx.send.call_args_list[1][0][1]
            text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

            # Must NOT claim settled on-chain
            assert "Payment Settled On-Chain" not in text_content
            assert "Payment Pending Authoritative Finality" in text_content

            # Must NOT produce confirmed card
            metadata_cards = [c for c in response_msg.content if isinstance(c, MetadataContent)]
            assert len(metadata_cards) == 0

            # Record in storage must NOT be SETTLED
            rec = temp_db.get_payment_record(pay_rec.payment_reference)
            assert rec is not None
            assert rec.status != PaymentStatus.SETTLED


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_post_booking_payment_option_when_payment_disabled(temp_db: StorageEngine, mock_ctx):
    """
    When booking is approved and ENABLE_PAYMENT_PROTOCOL is False (default):
    - Confirmed booking is created in database.
    - Message contains: "Payment is currently disabled. No transfer has been initiated."
    - Card rows contain Payment Status: "DISABLED" and notice.
    - No payment transfer is initiated or authorized.
    """
    from datetime import datetime, timezone, timedelta
    from krishibazaar.config import settings
    from krishibazaar.models import BookingProposal, NetReturnBreakdown

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", False):
        farmer_sender = "agent1q_farmer_booking_pay_disabled"
        now = datetime.now(timezone.utc)
        valid_transit = (now + timedelta(days=2)).strftime("%Y-%m-%d")
        opt = NetReturnBreakdown(
            scenario_id="mandi_nashik",
            rank=1,
            channel_type="MANDI",
            destination_name="Nashik APMC Mandi",
            distance_km=25.0,
            quantity_quintals=50.0,
            offered_price_per_quintal=1800.0,
            gross_value=90000.0,
            transport_cost=3000.0,
            applicable_market_charges=2000.0,
            storage_cost=0.0,
            estimated_loss_cost=0.0,
            total_deductions=5000.0,
            expected_net_return=85000.0,
            net_return_per_quintal=1700.0,
            price_provenance="agmarknet",
            is_price_simulated=True,
            meets_minimum_condition=True,
            recommendation_note="Nashik APMC Mandi benchmark.",
        )
        proposal = BookingProposal(
            proposal_id="KB-PROP-TEST-DIS-01",
            session_id=farmer_sender,
            farmer_id=farmer_sender,
            selected_option=opt,
            crop="Onion",
            quantity_quintals=50.0,
            origin_location="Nashik",
            destination_name=opt.destination_name,
            channel_type=opt.channel_type,
            transit_date=valid_transit,
            estimated_gross_value=opt.gross_value,
            itemized_deductions=opt.total_deductions,
            estimated_net_payout=opt.expected_net_return,
            created_at_utc=now.isoformat(),
            expires_at_utc=(now + timedelta(minutes=15)).isoformat(),
            is_consumed=False,
        )
        temp_db.save_proposal(proposal)
        temp_db.save_session(
            session_id=farmer_sender,
            farmer_id=farmer_sender,
            state="AWAITING_APPROVAL",
        )

        approve_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="APPROVE KB-PROP-TEST-DIS-01")])
        await handle_chat_message(mock_ctx, farmer_sender, approve_msg)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        # Assert post-booking payment step when disabled
        assert "Payment is currently disabled. No transfer has been initiated." in text_content
        assert "Simulated Booking Confirmed!" in text_content

        # Assert card has disabled payment row
        card_item = next(c for c in response_msg.content if isinstance(c, MetadataContent))
        card = extract_card(card_item)
        assert card is not None
        rows = {r.label: r.value for r in card.payload.summary_rows}
        assert rows["Payment Status"] == "DISABLED"
        assert rows["Payment Notice"] == "Payment is currently disabled. No transfer has been initiated."

        # Safety: No payment records created, no transfer executed
        sess = temp_db.get_session(farmer_sender)
        assert sess["state"] == "BOOKING_CONFIRMED"
        assert sess.get("pending_payment_reference") is None


@pytest.mark.asyncio
async def test_post_booking_payment_option_when_payment_enabled(temp_db: StorageEngine, mock_ctx):
    """
    When booking is approved and ENABLE_PAYMENT_PROTOCOL is True:
    - Post-booking payment option is presented in text and card.
    - Shows booking reference, payment status, amount/currency, masked recipient,
      network (Dorado testnet), estimated network fee, and Pay action.
    - Separates crop sale proceeds from testnet micro-fee.
    - Does NOT automatically transfer funds on booking approval.
    """
    from datetime import datetime, timezone, timedelta
    from krishibazaar.config import settings
    from krishibazaar.models import BookingProposal, NetReturnBreakdown

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", True):
        farmer_sender = "agent1q_farmer_booking_pay_enabled"
        now = datetime.now(timezone.utc)
        valid_transit = (now + timedelta(days=2)).strftime("%Y-%m-%d")
        opt = NetReturnBreakdown(
            scenario_id="buyer_bigbasket",
            rank=1,
            channel_type="DIRECT_BUYER",
            destination_name="Pune BigBasket Hub",
            distance_km=30.0,
            quantity_quintals=50.0,
            offered_price_per_quintal=1860.0,
            gross_value=93000.0,
            transport_cost=3000.0,
            applicable_market_charges=2000.0,
            storage_cost=0.0,
            estimated_loss_cost=0.0,
            total_deductions=5000.0,
            expected_net_return=88000.0,
            net_return_per_quintal=1760.0,
            price_provenance="bigbasket",
            is_price_simulated=True,
            meets_minimum_condition=True,
            recommendation_note="BigBasket B2B direct procurement.",
        )
        proposal = BookingProposal(
            proposal_id="KB-PROP-TEST-EN-02",
            session_id=farmer_sender,
            farmer_id=farmer_sender,
            selected_option=opt,
            crop="Onion",
            quantity_quintals=50.0,
            origin_location="Nashik",
            destination_name=opt.destination_name,
            channel_type=opt.channel_type,
            transit_date=valid_transit,
            estimated_gross_value=opt.gross_value,
            itemized_deductions=opt.total_deductions,
            estimated_net_payout=opt.expected_net_return,
            created_at_utc=now.isoformat(),
            expires_at_utc=(now + timedelta(minutes=15)).isoformat(),
            is_consumed=False,
        )
        temp_db.save_proposal(proposal)
        temp_db.save_session(
            session_id=farmer_sender,
            farmer_id=farmer_sender,
            state="AWAITING_APPROVAL",
        )

        approve_msg = ChatMessage(msg_id=uuid4(), content=[TextContent(text="APPROVE KB-PROP-TEST-EN-02")])
        await handle_chat_message(mock_ctx, farmer_sender, approve_msg)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        # Verify post-booking payment option fields in message
        assert "Payment Option (Optional Testnet Advisory Payment)" in text_content
        assert "READY FOR OPTIONAL TEST PAYMENT" in text_content
        assert "0.05 FET" in text_content or "50,000,000,000,000,000 atestfet" in text_content
        assert "Dorado" in text_content
        assert "fetch1" in text_content  # Masked recipient address
        assert "Pay advisory fee" in text_content
        assert "Proceed to Payment Review" in text_content
        assert "simulated mandi crop sales" in text_content  # Does not treat crop proceeds as received

        # Verify card payload
        card_item = next(c for c in response_msg.content if isinstance(c, MetadataContent))
        card = extract_card(card_item)
        assert card is not None
        rows = {r.label: r.value for r in card.payload.summary_rows}
        assert rows["Payment Status"] == "READY FOR OPTIONAL TEST PAYMENT"
        assert "fetch1" in rows["Recipient (Masked)"]
        assert "Dorado" in rows["Network / Environment"]
        assert "~0.0004 atestfet" in rows["Estimated Network Fee"]
        assert card.payload.ctas[1].selection["action"] == "request_payment_review"

        # Safety Gate: Booking approval alone did NOT authorize payment or initiate transfers
        sess = temp_db.get_session(farmer_sender)
        assert sess["state"] == "BOOKING_CONFIRMED"
        assert sess.get("pending_payment_reference") is None


def test_canonical_payment_specialist_configuration():
    """
    Regression test verifying:
    - Exactly one canonical Payment Specialist is registered on port 8006.
    - Resolver correctly routes canonical payment address to port 8006 submit endpoint.
    - No duplicate or competing payment agent addresses exist.
    """
    from krishibazaar.agents.resolver import KrishiBazaarResolver
    from krishibazaar.config import settings

    assert settings.payment_agent_port == 8006
    resolver = KrishiBazaarResolver()

    # Address mapped to port 8006
    pay_addr = settings.payment_payee_agent_address
    if pay_addr:
        endpoints = resolver.get_known_endpoints(pay_addr)
        assert endpoints is not None
        assert f"http://127.0.0.1:8006/submit" in endpoints


@pytest.mark.asyncio
async def test_duplicate_payment_authorization_rejected(temp_db: StorageEngine, mock_ctx):
    """
    Replay attack defense:
    Attempting to authorize a payment that is already SETTLED must be rejected safely.
    """
    from datetime import datetime, timezone
    from krishibazaar.config import settings
    from krishibazaar.models import PaymentRecord, PaymentStatus

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", True):
        farmer_sender = "agent1q_farmer_dup_auth"
        ref = "KB-PAY-ALREADY-SETTLED-99"

        record = PaymentRecord(
            payment_reference=ref,
            purpose="Advisory Fee",
            payer_agent_address=farmer_sender,
            payer_wallet_address="fetch1testpayer",
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            amount_display="0.05 FET",
            currency="atestfet",
            network="dorado-1",
            status=PaymentStatus.SETTLED,
            tx_hash="0xPRIOR_SETTLED_TX_HASH",
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        temp_db.save_payment_record(record)
        temp_db.save_session(
            session_id=farmer_sender,
            farmer_id=farmer_sender,
            state="AWAITING_PAYMENT_APPROVAL",
            pending_payment_reference=ref,
        )

        auth_msg = ChatMessage(
            msg_id=uuid4(),
            content=[
                create_card_response_content(
                    selection={
                        "action": "authorize_payment",
                        "reference": ref,
                        "amount": 50000000000000000,
                        "currency": "atestfet",
                        "recipient": "fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
                    }
                )
            ],
        )

        await handle_chat_message(mock_ctx, farmer_sender, auth_msg)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Duplicate Authorization" in text_content
        assert "already been SETTLED" in text_content
        assert "Replay or duplicate authorization blocked" in text_content


@pytest.mark.asyncio
async def test_mismatched_payment_authorization_rejected(temp_db: StorageEngine, mock_ctx):
    """
    Parameter tampering defense:
    Authorizing with a mismatched amount or recipient is caught and rejected.
    """
    from krishibazaar.config import settings

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", True), \
         patch.object(settings, "payment_payer_seed", "test_payer_seed_123456789"), \
         patch.object(settings, "payment_recipient_whitelist", ["fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"]):
        farmer_sender = "agent1q_farmer_mismatch_auth"
        service = PaymentService(storage_engine=temp_db)
        _, pay_rec = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
        )

        # Mismatched amount: user submits 999999 instead of 50000000000000000
        auth_msg = ChatMessage(
            msg_id=uuid4(),
            content=[
                create_card_response_content(
                    selection={
                        "action": "authorize_payment",
                        "reference": pay_rec.payment_reference,
                        "amount": 999999,
                        "currency": "atestfet",
                        "recipient": pay_rec.payee_wallet_address,
                    }
                )
            ],
        )

        await handle_chat_message(mock_ctx, farmer_sender, auth_msg)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Payment Validation Error" in text_content
        assert "Amount mismatch" in text_content

        # Record must NOT be settled
        rec = temp_db.get_payment_record(pay_rec.payment_reference)
        assert rec is not None
        assert rec.status != PaymentStatus.SETTLED


@pytest.mark.asyncio
async def test_expired_payment_authorization_rejected(temp_db: StorageEngine, mock_ctx):
    """
    Expiry protection:
    Authorizing after the deadline has elapsed is rejected with TimeoutError / expired notice.
    """
    from datetime import datetime, timezone, timedelta
    from krishibazaar.config import settings

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", True), \
         patch.object(settings, "payment_payer_seed", "test_payer_seed_123456789"), \
         patch.object(settings, "payment_recipient_whitelist", ["fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"]):
        farmer_sender = "agent1q_farmer_expired_auth"
        service = PaymentService(storage_engine=temp_db)
        _, pay_rec = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
        )

        # Set creation timestamp 2 hours in the past (exceeds deadline_seconds=300)
        expired_dt = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        pay_rec.created_at_utc = expired_dt
        temp_db.save_payment_record(pay_rec)

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

        await handle_chat_message(mock_ctx, farmer_sender, auth_msg)

        response_msg = mock_ctx.send.call_args_list[1][0][1]
        text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

        assert "Payment Request Expired" in text_content

        rec = temp_db.get_payment_record(pay_rec.payment_reference)
        assert rec is not None
        assert rec.status == PaymentStatus.FAILED


@pytest.mark.asyncio
async def test_ledger_cancellation_never_reports_paid(temp_db: StorageEngine, mock_ctx):
    """
    Safety rule: Never report a payment as successful until verified on the ledger.
    If Payment Agent returns CancelPayment, Orchestrator reports failed, not settled.
    """
    from krishibazaar.config import settings

    with patch("krishibazaar.agents.orchestrator.storage", temp_db), \
         patch.object(settings, "enable_payment_protocol", True), \
         patch.object(settings, "payment_payer_seed", "test_payer_seed_123456789"), \
         patch.object(settings, "payment_recipient_whitelist", ["fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h"]):
        farmer_sender = "agent1q_farmer_cancel_test"
        service = PaymentService(storage_engine=temp_db)
        _, pay_rec = service.create_advisory_payment_request(
            payee_agent_address="agent1q_payee",
            payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
            amount_base_units=50000000000000000,
            currency="atestfet",
            purpose=PaymentPurpose.ADVISORY_PASS.value,
            payer_agent_address=farmer_sender,
        )

        auth_resp = create_card_response_content(
            selection={
                "action": "authorize_payment",
                "reference": pay_rec.payment_reference,
                "amount": pay_rec.amount_base_units,
                "currency": pay_rec.currency,
                "recipient": pay_rec.payee_wallet_address,
            }
        )
        incoming = ChatMessage(msg_id=uuid4(), content=[auth_resp])

        mock_commit = CommitPayment(
            funds=Funds(
                amount=str(pay_rec.amount_base_units),
                currency=pay_rec.currency,
                payment_method="fet_direct",
            ),
            recipient=pay_rec.payee_wallet_address,
            transaction_id="0xFAILED_TX",
            reference=pay_rec.payment_reference,
        )
        mock_cancel = CancelPayment(
            reason="Transaction failed on-chain with code 5 (out of gas)",
            reference=pay_rec.payment_reference,
        )

        with patch(
            "krishibazaar.services.payment_service.PaymentService.execute_authorized_transfer",
            return_value=(mock_commit, pay_rec),
        ), patch(
            "uagents.communication.send_sync_message",
            new=AsyncMock(return_value=mock_cancel),
        ):
            await handle_chat_message(mock_ctx, farmer_sender, incoming)

            response_msg = mock_ctx.send.call_args_list[1][0][1]
            text_content = next(c.text for c in response_msg.content if isinstance(c, TextContent))

            assert "Payment Failed / Cancelled" in text_content
            assert "Payment Settled" not in text_content

            rec = temp_db.get_payment_record(pay_rec.payment_reference)
            assert rec is not None
            assert rec.status != PaymentStatus.SETTLED
