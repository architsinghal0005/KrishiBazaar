"""
Interactive Card Service for KrishiBazaar (Phase 7).
Provides structured, interactive presentation of farmer requests, ranked selling channels,
itemized booking proposals, and confirmation audit records using the official ASI:One / Agentverse
card protocol (uagents_core.contrib.protocols.chat.cards).

Key Features:
1. Strict Card Protocol V1 adherence using supported payload types (Carousel, Detail, Review, Form).
2. Explicit provenance and distinction between live mandi benchmarks vs simulated buyer offers.
3. Explicit-approval safety gating (ReviewCardPayload with approve_cta and reject_cta).
4. Graceful handling of invalid selections, expired proposals, duplicate approvals, and corrupt metadata.
5. Rich Markdown TextContent display fallback for clients that cannot render interactive cards.
"""

from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any
from uuid import uuid4

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    EndSessionContent,
    MetadataContent,
    TextContent,
)
from uagents_core.contrib.protocols.chat.cards import (
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    CtaAction,
    DetailCardPayload,
    DetailSummaryRow,
    FormCardPayload,
    FormField,
    ReviewCardPayload,
    ReviewSummaryRow,
    create_card_content,
    extract_card_response,
)

from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    FarmerIntent,
    NetReturnBreakdown,
    OptimizationResult,
    PaymentRecord,
    PaymentReviewDetails,
    PaymentStatus,
)
from krishibazaar.services.booking_service import (
    BookingActionType,
    parse_booking_action,
    strip_agent_mention,
)

logger = logging.getLogger("krishibazaar.card_service")


def build_farmer_inquiry_card(intent: FarmerIntent) -> MetadataContent:
    """
    Construct a structured DetailCard summarizing the farmer's parsed produce request.
    Presents crop, quantity, origin location, selling deadline, and target net return.
    """
    crop_str = intent.crop.title() if intent.crop else "Pending"
    qty_str = (
        f"{intent.quantity_quintals:g} Quintals ({intent.quantity_quintals * 100:g} kg)"
        if intent.quantity_quintals
        else "Pending specification"
    )
    loc_str = intent.origin_location or "Pending specification"
    deadline_str = intent.selling_deadline or "Today (Immediate transit)"
    target_str = (
        f"INR {intent.minimum_net_return_inr:,.2f} / qtl"
        if intent.minimum_net_return_inr is not None
        else "None (Maximize net return)"
    )
    engine_str = (
        "ASI:One Developer API (Validated)"
        if intent.extraction_source == "asi1"
        else f"Rule-based Parser ({intent.extraction_source})"
    )

    rows = [
        DetailSummaryRow(label="Crop", value=crop_str),
        DetailSummaryRow(label="Produce Quantity", value=qty_str),
        DetailSummaryRow(label="Origin Location", value=loc_str),
        DetailSummaryRow(label="Selling Deadline", value=deadline_str),
        DetailSummaryRow(label="Target Net Return", value=target_str),
        DetailSummaryRow(label="Parser Engine", value=engine_str),
    ]

    ctas = [
        CtaAction(
            label="View Ranked Channels",
            selection={"action": "view_channels"},
            primary=True,
        )
    ]

    payload = DetailCardPayload(
        title=f"Produce Request: {crop_str}",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4())


def build_channel_recommendations_card(opt: OptimizationResult) -> MetadataContent:
    """
    Construct an interactive CarouselCard presenting the ranked sales channels.
    Includes rank, channel type, estimated net return, key deductions, and data provenance.
    Strictly distinguishes live Mandi prices from simulated buyer offers.
    """
    items: list[CarouselItem] = []

    for option in opt.all_options:
        # Determine badges
        badges: list[CarouselBadge] = []
        if option.rank == 1:
            badges.append(CarouselBadge(label="Top Recommendation", variant="success"))

        if option.meets_minimum_condition:
            badges.append(CarouselBadge(label="Target Met", variant="info"))
        elif getattr(opt, "minimum_net_return_inr", None) is not None:
            badges.append(CarouselBadge(label="Target Shortfall", variant="warning"))

        # Channel type badge
        channel_str = str(option.channel_type).upper()
        is_mandi = "MANDI" in channel_str
        if is_mandi:
            badges.append(CarouselBadge(label="Mandi Auction Benchmark", variant="info"))
        else:
            badges.append(CarouselBadge(label="Simulated Buyer Offer", variant="info"))

        # Itemize key deductions & provenance text
        transport_cost = getattr(option, "transport_cost", 0.0)
        statutory_fees = getattr(option, "applicable_market_charges", 0.0)
        weather_loss = getattr(option, "estimated_loss_cost", 0.0)
        cess = getattr(option, "mandi_cess_apmc", 0.0)
        hamali = getattr(option, "loading_unloading_cost", 0.0)
        gross_val = getattr(option, "gross_value", option.offered_price_per_quintal * getattr(opt, "quantity_quintals", 1.0))
        total_ded = getattr(option, "total_deductions", round(transport_cost + statutory_fees + weather_loss, 2))

        freight_desc = f"Freight: INR {transport_cost:,.2f}" if transport_cost > 0 else "Freight: INR 0.00 (Farmgate Pickup)"
        cess_desc = f"APMC Cess: INR {cess:,.2f}" if cess > 0 else "APMC Cess: INR 0.00 (Assumed Direct-Trade Exemption - Unverified)"
        hamali_desc = f"Hamali/Labor: INR {hamali:,.2f}" if hamali > 0 else "Hamali/Labor: INR 0.00 (Assumed Farmgate Handling - Unverified)"

        source_name = getattr(option, "price_provenance", "") or (
            "AGMARKNET Live" if is_mandi else "Simulated Buyer Benchmark"
        )
        is_simulated = getattr(option, "is_price_simulated", not is_mandi)
        freshness = "Simulated" if is_simulated else "Live Official Data"

        mandi_clarification = (
            "Observed auction modal benchmark (not a buyer bid)"
            if is_mandi
            else "Simulated institutional benchmark offer"
        )

        secondary_text = (
            f"Gross: INR {option.offered_price_per_quintal:,.2f}/qtl (Gross: INR {gross_val:,.2f}) | "
            f"{freight_desc} | "
            f"Market charges (cess/hamali): INR {statutory_fees:,.2f} [{cess_desc}, {hamali_desc}] | "
            f"Estimated weather loss: INR {weather_loss:,.2f} | "
            f"Total Deductions: INR {total_ded:,.2f} | "
            f"Source: {source_name} ({freshness}) | {mandi_clarification}"
        )

        option_id = getattr(option, "scenario_id", None) or f"opt_{option.rank}"

        item = CarouselItem(
            id=option_id,
            title=f"Rank #{option.rank}: {option.destination_name} ({channel_str})",
            subtitle=f"Est. Net: INR {option.net_return_per_quintal:,.2f}/qtl | Total: INR {option.expected_net_return:,.2f}",
            badges=badges,
            secondary_text=secondary_text,
            primary_cta=CtaAction(
                label=f"Select Rank #{option.rank}",
                selection={
                    "action": "select_channel",
                    "rank": option.rank,
                    "option_id": option_id,
                },
                primary=(option.rank == 1),
            ),
        )
        items.append(item)

    if not items:
        # Fallback empty carousel item if no feasible options
        items.append(
            CarouselItem(
                id="none",
                title="No Channels Feasible",
                subtitle="All channels failed minimum net-return target or distance constraints.",
                primary_cta=CtaAction(
                    label="Revise Produce Details",
                    selection={"action": "revise_details"},
                    primary=True,
                ),
            )
        )

    qty = getattr(opt, "quantity_quintals", 0.0)
    crop_name = getattr(opt, "crop", "Produce")
    crop = crop_name.title() if isinstance(crop_name, str) else "Produce"

    payload = CarouselCardPayload(
        title="Ranked Selling Channels (Net Return)",
        subtitle=f"Evaluated {len(opt.all_options)} sales channels for {qty:g} qtl {crop}",
        items=items,
    )
    return create_card_content(payload, card_id=uuid4())


def build_proposal_review_card(proposal: BookingProposal) -> MetadataContent:
    """
    Construct an explicit-approval ReviewCard for a pending booking proposal.
    Provides complete itemized financial deductions, transit date, and 15-minute expiry.
    Retains explicit-approval safety gate (requires farmer to click Approve or send affirmative confirmation).
    """
    channel_str = str(proposal.channel_type).upper()
    is_mandi_chan = "MANDI" in channel_str
    opt = proposal.selected_option
    trans_val = getattr(opt, "transport_cost", 0.0)
    cess_val = getattr(opt, "mandi_cess_apmc", 0.0)
    hamali_val = getattr(opt, "loading_unloading_cost", 0.0)
    weather_loss_val = getattr(opt, "estimated_loss_cost", 0.0)

    trans_str = f"INR {trans_val:,.2f} (Farmgate Pickup)" if trans_val == 0 and not is_mandi_chan else f"INR {trans_val:,.2f}"
    cess_str = f"INR {cess_val:,.2f} (Assumed Direct-Trade Exemption - Unverified)" if cess_val == 0 and not is_mandi_chan else f"INR {cess_val:,.2f}"
    hamali_str = f"INR {hamali_val:,.2f} (Assumed Farmgate Handling - Unverified)" if hamali_val == 0 and not is_mandi_chan else f"INR {hamali_val:,.2f}"
    weather_note = f" (Est. transit risk deduction: INR {weather_loss_val:,.2f})" if weather_loss_val > 0 else ""

    rows = [
        ReviewSummaryRow(label="Proposal Reference", value=proposal.proposal_id),
        ReviewSummaryRow(
            label="Produce & Quantity",
            value=f"{proposal.quantity_quintals:g} Quintals {proposal.crop}",
        ),
        ReviewSummaryRow(
            label="Destination Channel",
            value=f"{proposal.destination_name} ({channel_str})",
        ),
        ReviewSummaryRow(
            label="Gross Produce Value",
            value=f"INR {proposal.estimated_gross_value:,.2f}",
        ),
        ReviewSummaryRow(
            label="Transport Freight",
            value=trans_str,
        ),
        ReviewSummaryRow(
            label="APMC Market Cess",
            value=cess_str,
        ),
        ReviewSummaryRow(
            label="Hamali / Labour Charges",
            value=hamali_str,
        ),
        ReviewSummaryRow(
            label="Estimated Weather Spoilage Loss",
            value=f"INR {weather_loss_val:,.2f}",
        ),
        ReviewSummaryRow(
            label="Itemized Deductions",
            value=f"INR {proposal.itemized_deductions:,.2f}",
        ),
        ReviewSummaryRow(
            label="Estimated Net Payout",
            value=f"INR {proposal.estimated_net_payout:,.2f}",
        ),
        ReviewSummaryRow(
            label="Scheduled Transit",
            value=f"{proposal.transit_date}{weather_note}",
        ),
        ReviewSummaryRow(
            label="Safety Gate Status",
            value="AWAITING EXPLICIT FARMER APPROVAL",
        ),
        ReviewSummaryRow(
            label="Validity Window",
            value="15 Minutes (Market Rate Volatility Protection)",
        ),
    ]

    payload = ReviewCardPayload(
        title=f"Review Booking Proposal: {proposal.crop} to {proposal.destination_name}",
        summary_rows=rows,
        approve_cta=CtaAction(
            label="Approve & Generate Booking",
            selection={
                "action": "approve_booking",
                "proposal_id": proposal.proposal_id,
            },
            primary=True,
        ),
        reject_cta=CtaAction(
            label="Decline / Choose Another",
            selection={
                "action": "reject_booking",
                "proposal_id": proposal.proposal_id,
            },
        ),
    )
    return create_card_content(payload, card_id=uuid4())


def build_booking_confirmed_card(
    booking: BookingRecord,
    payment_enabled: bool | None = None,
    payment_record: PaymentRecord | None = None,
) -> MetadataContent:
    """
    Construct a DetailCard confirming a successfully recorded simulated booking record.
    Includes tracking reference, destination, scheduled pickup, and post-booking payment step.
    Distinguishes booking confirmation from payment settlement and disables payment review
    if a payment is already in progress or settled.
    """
    from krishibazaar.config import settings
    from krishibazaar.services.payment_service import (
        format_token_amount_display,
        mask_address,
    )

    is_pay_enabled = settings.enable_payment_protocol if payment_enabled is None else payment_enabled

    if payment_record is None:
        try:
            from krishibazaar.storage import storage
            payment_record = storage.get_payment_record_by_inquiry(booking.booking_id)
        except Exception:
            payment_record = None

    tracking = (
        getattr(booking, "tracking_token", None)
        or getattr(booking, "tracking_number", None)
        or "KB-TRK-PENDING"
    )
    destination = (
        getattr(booking, "channel_name", None)
        or getattr(booking, "destination_name", None)
        or "Destination"
    )
    payout = (
        getattr(booking, "net_payout", None)
        if getattr(booking, "net_payout", None) is not None
        else getattr(booking, "estimated_net_payout", 0.0)
    )
    pickup = (
        getattr(booking, "pickup_date", None)
        or getattr(booking, "transit_date", None)
        or "Pending"
    )

    gross = getattr(booking, "gross_revenue", 0.0)
    trans = getattr(booking, "transport_cost", 0.0)
    other = getattr(booking, "other_deductions", 0.0)
    total_ded = round(trans + other, 2)

    rows = [
        DetailSummaryRow(label="Audit Status", value="CONFIRMED (SIMULATED — NOT A REAL BOOKING)"),
        DetailSummaryRow(label="Booking Reference", value=booking.booking_id),
        DetailSummaryRow(label="Tracking Number", value=tracking),
        DetailSummaryRow(
            label="Produce & Quantity",
            value=f"{booking.quantity_quintals:g} Quintals {booking.crop}",
        ),
        DetailSummaryRow(
            label="Destination Channel",
            value=f"{destination}",
        ),
        DetailSummaryRow(
            label="Gross Produce Value",
            value=f"INR {gross:,.2f}",
        ),
        DetailSummaryRow(
            label="Total Deductions",
            value=f"INR {total_ded:,.2f} (Freight: INR {trans:,.2f} + Other: INR {other:,.2f})",
        ),
        DetailSummaryRow(
            label="Est. Net Farmer Payout",
            value=f"INR {payout:,.2f} (Reconciled: Gross - Total Deductions)",
        ),
        DetailSummaryRow(label="Scheduled Pickup Date", value=pickup),
        DetailSummaryRow(
            label="Simulation Notice",
            value="SIMULATED — NOT A REAL BOOKING: No real funds debited, no physical transport dispatched.",
        ),
    ]

    is_in_progress = (
        payment_record is not None
        and payment_record.status in (
            PaymentStatus.SUBMITTING,
            PaymentStatus.COMMITTED,
            PaymentStatus.PENDING_SETTLEMENT,
            PaymentStatus.AWAITING_RECONCILIATION,
        )
    )
    is_settled = (
        payment_record is not None
        and payment_record.status == PaymentStatus.SETTLED
    )

    if is_settled:
        rows.append(DetailSummaryRow(label="Payment Status", value="SETTLED (Verified on Dorado Testnet)"))
        rows.append(DetailSummaryRow(label="Payment Reference", value=payment_record.payment_reference))
        if payment_record.tx_hash:
            rows.append(DetailSummaryRow(label="Transaction Hash", value=payment_record.tx_hash))
        ctas = [
            CtaAction(
                label="Check Booking Status",
                selection={"action": "status_lookup", "booking_id": booking.booking_id},
                primary=True,
            ),
            CtaAction(
                label="View Payment Receipt",
                selection={"action": "payment_status_lookup", "reference": payment_record.payment_reference},
            ),
        ]
    elif is_in_progress:
        rows.append(DetailSummaryRow(label="Payment Status", value=f"{payment_record.status.value} (Payment In Progress)"))
        rows.append(DetailSummaryRow(label="Payment Reference", value=payment_record.payment_reference))
        if payment_record.tx_hash:
            rows.append(DetailSummaryRow(label="Transaction Hash", value=payment_record.tx_hash))
        rows.append(
            DetailSummaryRow(
                label="Payment Notice",
                value="Transaction is in progress / awaiting on-chain reconciliation. Payment review is disabled.",
            )
        )
        ctas = [
            CtaAction(
                label="Check Booking Status",
                selection={"action": "status_lookup", "booking_id": booking.booking_id},
                primary=True,
            ),
            CtaAction(
                label="Payment In Progress (Review Disabled)",
                selection={"action": "payment_status_lookup", "reference": payment_record.payment_reference},
            ),
        ]
    elif not is_pay_enabled:
        rows.append(DetailSummaryRow(label="Payment Status", value="DISABLED"))
        rows.append(
            DetailSummaryRow(
                label="Payment Notice",
                value="Payment is currently disabled. No transfer has been initiated.",
            )
        )
        ctas = [
            CtaAction(
                label="Check Booking Status",
                selection={"action": "status_lookup", "booking_id": booking.booking_id},
                primary=True,
            ),
            CtaAction(
                label="Pay Advisory Fee (Disabled)",
                selection={"action": "request_payment_review", "booking_id": booking.booking_id},
            ),
        ]
    else:
        fee_base = settings.payment_advisory_fee_base_units
        denom = settings.payment_testnet_denom
        amt_display = format_token_amount_display(fee_base, denom)
        payee_wallet = (
            settings.payment_payee_wallet_address
            or "fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck"
        )
        masked_payee = mask_address(payee_wallet)
        net_display = f"Fetch.ai {settings.payment_testnet_chain_id.title()} Testnet ({settings.payment_testnet_chain_id})"
        est_fee = "~0.0004 atestfet (standard testnet gas fee)"

        if payment_record and payment_record.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION):
            rows.append(DetailSummaryRow(label="Payment Status", value="AWAITING AUTHORIZATION (Review Pending)"))
            rows.append(DetailSummaryRow(label="Payment Reference", value=payment_record.payment_reference))
            pay_ref_param = payment_record.payment_reference
        else:
            rows.append(DetailSummaryRow(label="Payment Status", value="READY FOR OPTIONAL TEST PAYMENT"))
            pay_ref_param = None

        rows.append(DetailSummaryRow(label="Payment Purpose", value="Platform Advisory Fee (Optional Testnet Verification)"))
        rows.append(DetailSummaryRow(label="Payment Amount", value=amt_display))
        rows.append(DetailSummaryRow(label="Recipient (Masked)", value=masked_payee))
        rows.append(DetailSummaryRow(label="Network / Environment", value=net_display))
        rows.append(DetailSummaryRow(label="Estimated Network Fee", value=est_fee))

        review_sel = {"action": "request_payment_review", "booking_id": booking.booking_id}
        if pay_ref_param:
            review_sel["reference"] = pay_ref_param

        ctas = [
            CtaAction(
                label="Check Booking Status",
                selection={"action": "status_lookup", "booking_id": booking.booking_id},
            ),
            CtaAction(
                label="Proceed to Payment Review",
                selection=review_sel,
                primary=True,
            ),
        ]

    payload = DetailCardPayload(
        title=f"Booking Confirmed: {booking.booking_id} (SIMULATED — NOT A REAL BOOKING)",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4(), is_terminal=True)


def build_payment_option_card(
    booking: BookingRecord,
    payment_enabled: bool | None = None,
    payment_record: PaymentRecord | None = None,
) -> MetadataContent:
    """
    Construct a dedicated DetailCard for the post-booking Payment Option step.
    Displays booking reference, payment status, amount/currency, masked recipient,
    network, estimated network fee, and Pay/Test Payment action.
    Disables the review button if a payment request is already in progress.
    """
    from krishibazaar.config import settings
    from krishibazaar.services.payment_service import (
        format_token_amount_display,
        mask_address,
    )

    is_pay_enabled = settings.enable_payment_protocol if payment_enabled is None else payment_enabled

    if payment_record is None:
        try:
            from krishibazaar.storage import storage
            payment_record = storage.get_payment_record_by_inquiry(booking.booking_id)
        except Exception:
            payment_record = None

    fee_base = settings.payment_advisory_fee_base_units
    denom = settings.payment_testnet_denom
    amt_display = format_token_amount_display(fee_base, denom)
    payee_wallet = settings.payment_payee_wallet_address or "fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck"
    masked_payee = mask_address(payee_wallet)
    net_display = f"Fetch.ai {settings.payment_testnet_chain_id.title()} Testnet ({settings.payment_testnet_chain_id})"
    est_fee = "~0.0004 atestfet"

    is_in_progress = (
        payment_record is not None
        and payment_record.status in (
            PaymentStatus.SUBMITTING,
            PaymentStatus.COMMITTED,
            PaymentStatus.PENDING_SETTLEMENT,
            PaymentStatus.AWAITING_RECONCILIATION,
        )
    )
    is_settled = (
        payment_record is not None
        and payment_record.status == PaymentStatus.SETTLED
    )

    if is_settled:
        rows = [
            DetailSummaryRow(label="Booking Reference", value=booking.booking_id),
            DetailSummaryRow(label="Payment Status", value="SETTLED (Verified on Dorado Testnet)"),
            DetailSummaryRow(label="Payment Reference", value=payment_record.payment_reference),
            DetailSummaryRow(label="Transaction Hash", value=payment_record.tx_hash or "Confirmed"),
            DetailSummaryRow(
                label="Produce Proceeds Notice",
                value=f"Crop proceeds of INR {booking.net_payout:,.2f} are simulated mandi sales, not funds received.",
            ),
        ]
        ctas = [
            CtaAction(
                label="Check Booking Status",
                selection={"action": "status_lookup", "booking_id": booking.booking_id},
                primary=True,
            ),
            CtaAction(
                label="View Payment Receipt",
                selection={"action": "payment_status_lookup", "reference": payment_record.payment_reference},
            ),
        ]
    elif is_in_progress:
        rows = [
            DetailSummaryRow(label="Booking Reference", value=booking.booking_id),
            DetailSummaryRow(label="Payment Status", value=f"{payment_record.status.value} (Payment In Progress)"),
            DetailSummaryRow(label="Payment Reference", value=payment_record.payment_reference),
            DetailSummaryRow(
                label="Payment Notice",
                value="Transaction is in progress / awaiting on-chain reconciliation. Payment review is disabled.",
            ),
            DetailSummaryRow(
                label="Produce Proceeds Notice",
                value=f"Crop proceeds of INR {booking.net_payout:,.2f} are simulated mandi sales, not funds received.",
            ),
        ]
        ctas = [
            CtaAction(
                label="Check Booking Status",
                selection={"action": "status_lookup", "booking_id": booking.booking_id},
                primary=True,
            ),
            CtaAction(
                label="Payment In Progress (Review Disabled)",
                selection={"action": "payment_status_lookup", "reference": payment_record.payment_reference},
            ),
        ]
    elif not is_pay_enabled:
        rows = [
            DetailSummaryRow(label="Booking Reference", value=booking.booking_id),
            DetailSummaryRow(label="Payment Status", value="DISABLED"),
            DetailSummaryRow(
                label="Payment Notice",
                value="Payment is currently disabled. No transfer has been initiated.",
            ),
            DetailSummaryRow(
                label="Safety Gate Notice",
                value="ENABLE_PAYMENT_PROTOCOL is false. No token transfer will occur.",
            ),
        ]
        ctas = [
            CtaAction(
                label="Check Booking Status",
                selection={"action": "status_lookup", "booking_id": booking.booking_id},
                primary=True,
            ),
        ]
    else:
        rows = [
            DetailSummaryRow(label="Booking Reference", value=booking.booking_id),
            DetailSummaryRow(label="Payment Status", value="READY FOR OPTIONAL TEST PAYMENT"),
            DetailSummaryRow(label="Service Purpose", value="KrishiBazaar Platform Advisory Fee (Optional Testnet Verification)"),
            DetailSummaryRow(label="Amount & Currency", value=amt_display),
            DetailSummaryRow(label="Recipient (Masked)", value=masked_payee),
            DetailSummaryRow(label="Network / Environment", value=net_display),
            DetailSummaryRow(label="Estimated Network Fee", value=est_fee),
            DetailSummaryRow(
                label="Produce Proceeds Notice",
                value=f"Crop proceeds of INR {booking.net_payout:,.2f} are simulated mandi sales, not funds received.",
            ),
        ]
        review_sel = {"action": "request_payment_review", "booking_id": booking.booking_id}
        if payment_record and payment_record.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION):
            review_sel["reference"] = payment_record.payment_reference

        ctas = [
            CtaAction(
                label="Proceed to Payment Review",
                selection=review_sel,
                primary=True,
            ),
            CtaAction(
                label="Check Booking Status",
                selection={"action": "status_lookup", "booking_id": booking.booking_id},
            ),
        ]

    payload = DetailCardPayload(
        title=f"Payment Option: {booking.booking_id}",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4(), is_terminal=False)


def build_payment_review_card(details: PaymentReviewDetails) -> MetadataContent:
    """
    Construct an explicit-approval ReviewCard for a pending Fetch.ai Agent Payment Protocol request.
    Presents:
    - Exact amount and denomination (e.g. 1000 atestfet)
    - Verified recipient address (fetch1...)
    - Network and purpose (dorado-1 testnet)
    - Network fees or statement of estimated gas
    - Unique payment reference ID
    - Clear testnet-only disclosure
    - Approval CTA with exact reference and amount parameters
    """
    rows = [
        ReviewSummaryRow(label="Payment Reference", value=details.reference),
        ReviewSummaryRow(label="Service Purpose", value=details.purpose),
        ReviewSummaryRow(
            label="Exact Amount & Denom",
            value=f"{details.amount_base_units} {details.currency} ({details.amount_display})",
        ),
        ReviewSummaryRow(
            label="Verified Recipient",
            value=details.recipient_address,
        ),
        ReviewSummaryRow(
            label="Target Network",
            value=f"{details.network} (Fetch.ai Testnet)",
        ),
        ReviewSummaryRow(
            label="Estimated Network Fee",
            value=details.estimated_fee,
        ),
        ReviewSummaryRow(
            label="Payer Balance Status",
            value=details.payer_balance_status,
        ),
        ReviewSummaryRow(
            label="Disclosure",
            value=details.testnet_notice,
        ),
        ReviewSummaryRow(
            label="Safety Gate Status",
            value="AWAITING EXPLICIT HUMAN AUTHORIZATION",
        ),
        ReviewSummaryRow(
            label="Validity Window",
            value=f"{details.deadline_seconds}s Expiry (Rate Volatility Protection)",
        ),
    ]

    payload = ReviewCardPayload(
        title=f"Review Payment Request: {details.amount_display}",
        summary_rows=rows,
        approve_cta=CtaAction(
            label="Approve Testnet Payment",
            selection={
                "action": "authorize_payment",
                "reference": details.reference,
                "amount": details.amount_base_units,
                "currency": details.currency,
                "recipient": details.recipient_address,
            },
            primary=True,
        ),
        reject_cta=CtaAction(
            label="Decline Payment",
            selection={
                "action": "reject_payment",
                "reference": details.reference,
            },
        ),
    )
    return create_card_content(payload, card_id=uuid4())


def build_payment_confirmed_card(record: PaymentRecord) -> MetadataContent:
    """
    Construct a DetailCard confirming authoritative settlement of an on-chain testnet payment.
    Includes transaction hash, block height, settled amount, and testnet audit notice.
    """
    status_label = (
        "SETTLED (ON-CHAIN TESTNET VERIFIED)"
        if record.status == PaymentStatus.SETTLED
        else f"{record.status.value} (PENDING RECONCILIATION)"
    )

    rows = [
        DetailSummaryRow(label="Payment Status", value=status_label),
        DetailSummaryRow(label="Payment Reference", value=record.payment_reference),
        DetailSummaryRow(label="Transaction Hash", value=record.tx_hash or "Pending broadcast"),
        DetailSummaryRow(
            label="Block Height",
            value=str(record.tx_height) if record.tx_height else "Awaiting finality",
        ),
        DetailSummaryRow(label="Transferred Amount", value=record.amount_display),
        DetailSummaryRow(label="Payee Address", value=record.payee_wallet_address),
        DetailSummaryRow(label="Network", value=f"{record.network} (Testnet)"),
    ]

    if record.actual_fee_display:
        rows.append(DetailSummaryRow(label="Actual Network Fee", value=record.actual_fee_display))
    elif record.actual_fee_base_units:
        rows.append(DetailSummaryRow(label="Actual Network Fee", value=f"{record.actual_fee_base_units:,} {record.currency}"))

    rows.extend([
        DetailSummaryRow(
            label="Verification Timestamp",
            value=record.verified_at_utc or "Pending authoritative confirmation",
        ),
        DetailSummaryRow(
            label="Audit Notice",
            value="Fetch.ai Dorado-1 Testnet token transaction. Cryptographically verified against ledger state.",
        ),
    ])

    ctas = [
        CtaAction(
            label="Check Payment Record",
            selection={"action": "status_lookup", "reference": record.payment_reference},
            primary=True,
        )
    ]

    payload = DetailCardPayload(
        title=f"Payment Settled: {record.payment_reference}",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4(), is_terminal=True)


def build_payment_status_card(
    record: PaymentRecord, payment_enabled: bool = False
) -> MetadataContent:
    """
    Construct a DetailCard displaying authoritative lifecycle status for a PaymentRecord.
    Works for any state: REQUESTED, AWAITING_AUTHORIZATION, SETTLED, CANCELLED, FAILED, REJECTED.
    """
    from krishibazaar.services.payment_service import mask_address

    masked_recipient = mask_address(record.payee_wallet_address)
    masked_payer = (
        mask_address(record.payer_wallet_address)
        if record.payer_wallet_address and record.payer_wallet_address != "unknown_payer_wallet"
        else "Demo Testnet Payer"
    )

    status_str = record.status.value if hasattr(record.status, "value") else str(record.status)
    if not payment_enabled and status_str != "SETTLED":
        status_display = "DISABLED (Protocol deactivated for safety)"
    elif status_str == "SETTLED":
        status_display = "SETTLED (Verified on Dorado Testnet)"
    elif status_str in ("REQUESTED", "AWAITING_AUTHORIZATION"):
        status_display = f"{status_str} (Awaiting authorization; no tokens debited)"
    else:
        status_display = status_str

    rows = [
        DetailSummaryRow(label="Payment Reference", value=record.payment_reference),
        DetailSummaryRow(label="Payment Status", value=status_display),
        DetailSummaryRow(
            label="Amount & Denomination",
            value=f"{record.amount_display} ({record.amount_base_units:,} {record.currency})",
        ),
        DetailSummaryRow(label="Recipient (Masked)", value=masked_recipient),
        DetailSummaryRow(label="Payer (Masked)", value=masked_payer),
        DetailSummaryRow(label="Network / Environment", value=f"{record.network} (Fetch.ai Dorado Testnet)"),
    ]

    if record.tx_hash:
        rows.append(DetailSummaryRow(label="Transaction Hash", value=record.tx_hash))
    if record.tx_height:
        rows.append(DetailSummaryRow(label="Block Height", value=str(record.tx_height)))
    if record.actual_fee_display:
        rows.append(DetailSummaryRow(label="Actual Network Fee", value=record.actual_fee_display))
    elif record.actual_fee_base_units:
        rows.append(DetailSummaryRow(label="Actual Network Fee", value=f"{record.actual_fee_base_units:,} {record.currency}"))
    if record.service_inquiry_id:
        rows.append(DetailSummaryRow(label="Linked Booking / Service", value=record.service_inquiry_id))
    if record.rejection_reason:
        rows.append(DetailSummaryRow(label="Rejection Reason", value=record.rejection_reason))
    if record.cancellation_reason:
        rows.append(DetailSummaryRow(label="Cancellation Reason", value=record.cancellation_reason))

    rows.append(
        DetailSummaryRow(
            label="Safety Guarantee",
            value="No funds debited unless verified on the Dorado testnet ledger.",
        )
    )

    ctas = []
    if payment_enabled and status_str in ("REQUESTED", "AWAITING_AUTHORIZATION"):
        ctas.append(
            CtaAction(
                label="Proceed to Payment Review",
                selection={"action": "request_payment_review", "reference": record.payment_reference},
                primary=True,
            )
        )
    if record.service_inquiry_id and record.service_inquiry_id.startswith("KB-BOOK-"):
        ctas.append(
            CtaAction(
                label="View Booking Details",
                selection={"action": "status_lookup", "booking_id": record.service_inquiry_id},
                primary=(len(ctas) == 0),
            )
        )
    if not ctas:
        ctas.append(
            CtaAction(
                label="Check Status",
                selection={"action": "payment_status_lookup", "reference": record.payment_reference},
                primary=True,
            )
        )

    payload = DetailCardPayload(
        title=f"Payment Record: {record.payment_reference}",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4())


def build_incomplete_inquiry_form_card(intent: FarmerIntent) -> MetadataContent:
    """
    Construct a FormCard prompting the farmer for missing inquiry fields.
    """
    fields: list[FormField] = []

    if not intent.crop:
        fields.append(
            FormField(
                name="crop",
                kind="text",
                label="Crop Name",
                required=True,
                placeholder="e.g. Wheat, Onions, Soybeans, Tomatoes",
            )
        )

    if not intent.quantity_quintals:
        fields.append(
            FormField(
                name="quantity_quintals",
                kind="number",
                label="Quantity (in Quintals)",
                required=True,
                placeholder="e.g. 50 (1 quintal = 100 kg)",
            )
        )

    if not intent.origin_location:
        fields.append(
            FormField(
                name="origin_location",
                kind="text",
                label="Origin Location / District",
                required=True,
                placeholder="e.g. Nashik, Indore, Sehore",
            )
        )

    if not intent.selling_deadline:
        fields.append(
            FormField(
                name="selling_deadline",
                kind="text",
                label="Selling Deadline / Preferred Transit Date",
                required=False,
                placeholder="e.g. Today, Tomorrow, 2026-10-22",
            )
        )

    if not fields:
        fields.append(
            FormField(
                name="notes",
                kind="text",
                label="Additional Requirements",
                required=False,
                placeholder="e.g. cold storage preference",
            )
        )

    payload = FormCardPayload(
        title="Produce Details Required",
        fields=fields,
        submit_cta=CtaAction(
            label="Submit Produce Details",
            selection={"action": "submit_details"},
            primary=True,
        ),
    )
    return create_card_content(payload, card_id=uuid4())


def build_invalid_selection_card(
    error_msg: str, available_ranks: list[int]
) -> MetadataContent:
    """
    Construct an error DetailCard for invalid option selections.
    """
    ranks_desc = (
        f"Available options: Ranks {', '.join(str(r) for r in available_ranks)}"
        if available_ranks
        else "No active options available. Please submit a produce inquiry."
    )
    rows = [
        DetailSummaryRow(label="Error Notice", value=error_msg),
        DetailSummaryRow(label="Valid Selection Range", value=ranks_desc),
    ]

    ctas: list[CtaAction] = []
    for r in available_ranks[:3]:
        ctas.append(
            CtaAction(
                label=f"Select Option #{r}",
                selection={"action": "select_channel", "rank": r},
                primary=(r == 1),
            )
        )

    if not ctas:
        ctas.append(
            CtaAction(
                label="Start Fresh Produce Inquiry",
                selection={"action": "new_inquiry"},
                primary=True,
            )
        )

    payload = DetailCardPayload(
        title="Invalid Channel Selection",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4())


def build_expired_proposal_card(proposal_id: str, expired_at: str) -> MetadataContent:
    """
    Construct a DetailCard explaining proposal expiration.
    """
    rows = [
        DetailSummaryRow(label="Proposal Reference", value=proposal_id),
        DetailSummaryRow(label="Expired At (UTC)", value=expired_at),
        DetailSummaryRow(
            label="Protection Reason",
            value="15-minute validity window elapsed to protect against market rate volatility.",
        ),
    ]

    ctas = [
        CtaAction(
            label="Select Fresh Channel",
            selection={"action": "refresh_quote"},
            primary=True,
        )
    ]

    payload = DetailCardPayload(
        title="Proposal Expired",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4())


def build_duplicate_approval_card(proposal_id: str) -> MetadataContent:
    """
    Construct a DetailCard warning about duplicate approval attempt.
    """
    rows = [
        DetailSummaryRow(label="Proposal Reference", value=proposal_id),
        DetailSummaryRow(
            label="Notice",
            value="This proposal was already approved or cancelled. No duplicate booking created.",
        ),
    ]

    ctas = [
        CtaAction(
            label="Check Existing Booking",
            selection={"action": "status_lookup"},
            primary=True,
        )
    ]

    payload = DetailCardPayload(
        title="Proposal Already Processed",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4())


def build_payment_readiness_card(
    proposal_id: str | None,
    proposal: BookingProposal | None,
    booking: BookingRecord | None,
    payment_record: PaymentRecord | None,
    payment_enabled: bool = False,
) -> MetadataContent:
    """
    Construct a DetailCard summarizing proposal payment readiness,
    validity window, ledger isolation, and next safe step.
    """
    now_utc = datetime.now(timezone.utc)
    rows: list[DetailSummaryRow] = []

    if proposal:
        rows.append(DetailSummaryRow(label="Proposal Reference", value=proposal.proposal_id))
        rows.append(DetailSummaryRow(label="Produce", value=f"{proposal.quantity_quintals:g} qtl {proposal.crop}"))
        rows.append(DetailSummaryRow(label="Destination", value=f"{proposal.destination_name} ({proposal.channel_type})"))
        rows.append(DetailSummaryRow(label="Est. Net Payout", value=f"INR {proposal.estimated_net_payout:,.2f}"))

        try:
            exp = datetime.fromisoformat(proposal.expires_at_utc)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except Exception:
            exp = now_utc

        if now_utc > exp:
            status_val = "⚠️ EXPIRED (15-min validity window elapsed)"
        elif proposal.is_consumed:
            status_val = "ℹ️ CONSUMED / SUPERSEDED"
        else:
            status_val = "🟢 ACTIVE & VALID (Within 15-min window)"
        rows.append(DetailSummaryRow(label="Proposal Status", value=status_val))
    elif proposal_id:
        rows.append(DetailSummaryRow(label="Proposal Reference", value=proposal_id))
        rows.append(DetailSummaryRow(label="Proposal Status", value="⚠️ NOT FOUND in storage"))
    else:
        rows.append(DetailSummaryRow(label="Proposal Reference", value="None specified"))

    # Booking & Payment isolation
    rows.append(
        DetailSummaryRow(
            label="Booking Record",
            value=booking.booking_id if booking else "None (Unconfirmed)",
        )
    )
    rows.append(
        DetailSummaryRow(
            label="Payment Record",
            value=payment_record.payment_reference if payment_record else "None (No transfer initiated)",
        )
    )
    rows.append(
        DetailSummaryRow(
            label="Payment Protocol",
            value="🟢 Enabled (Testnet)" if payment_enabled else "🔴 Disabled (ENABLE_PAYMENT_PROTOCOL=False)",
        )
    )
    rows.append(
        DetailSummaryRow(
            label="Dry-Run Status",
            value="✅ Supported (Non-executing audit)",
        )
    )

    ctas: list[CtaAction] = []
    if proposal:
        try:
            exp_check = datetime.fromisoformat(proposal.expires_at_utc)
            if exp_check.tzinfo is None:
                exp_check = exp_check.replace(tzinfo=timezone.utc)
            is_exp = now_utc > exp_check
        except Exception:
            is_exp = False

        if not is_exp and not proposal.is_consumed:
            ctas.append(
                CtaAction(
                    label="Confirm Booking",
                    selection={"action": "approve_booking", "proposal_id": proposal.proposal_id},
                    primary=True,
                )
            )
            ctas.append(
                CtaAction(
                    label="Cancel Proposal",
                    selection={"action": "reject_booking", "proposal_id": proposal.proposal_id},
                    primary=False,
                )
            )
        elif is_exp:
            ctas.append(
                CtaAction(
                    label="Refresh Recommendations",
                    selection={"action": "refresh_quote"},
                    primary=True,
                )
            )

    if not ctas:
        if booking:
            ctas.append(
                CtaAction(
                    label="Check Booking Status",
                    selection={"action": "status_lookup", "booking_id": booking.booking_id},
                    primary=True,
                )
            )
        else:
            ctas.append(
                CtaAction(
                    label="Start Crop Inquiry",
                    selection={"action": "new_inquiry"},
                    primary=True,
                )
            )

    card_title = f"Booking Payment Audit: {booking.booking_id}" if (booking and not proposal) else "Proposal Payment Readiness"
    payload = DetailCardPayload(
        title=card_title,
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4())


def build_booking_payment_audit_card(
    booking_id: str,
    booking: BookingRecord | None,
    payment_record: PaymentRecord | None,
    payment_enabled: bool = False,
) -> MetadataContent:
    """
    Construct a DetailCard for a booking-specific payment audit.
    Reports booking reference, status, produce details, linked payment record,
    payment protocol enablement, dry-run status, and safe action CTA.
    """
    rows: list[DetailSummaryRow] = [
        DetailSummaryRow(label="Booking Reference", value=booking_id),
    ]

    if booking:
        rows.append(DetailSummaryRow(label="Booking Status", value=f"✅ {booking.status}"))
        rows.append(DetailSummaryRow(label="Produce", value=f"{booking.quantity_quintals:g} qtl {booking.crop} to {booking.channel_name}"))
        rows.append(DetailSummaryRow(label="Agreed Net Payout", value=f"INR {booking.net_payout:,.2f}"))
        rows.append(DetailSummaryRow(label="Transit Date", value=str(booking.pickup_date)))
    else:
        rows.append(DetailSummaryRow(label="Booking Status", value="⚠️ NOT FOUND in storage"))

    if payment_record:
        from krishibazaar.services.payment_service import mask_address
        masked_rec = mask_address(payment_record.payee_wallet_address)
        rows.append(DetailSummaryRow(label="Payment Record", value=f"{payment_record.payment_reference} ({payment_record.status.value})"))
        rows.append(DetailSummaryRow(label="Payee (Masked)", value=masked_rec))
    else:
        rows.append(DetailSummaryRow(label="Payment Record", value="None (No payment recorded)"))

    rows.append(
        DetailSummaryRow(
            label="Payment Protocol",
            value="🟢 Enabled (Testnet)" if payment_enabled else "🔴 Disabled (ENABLE_PAYMENT_PROTOCOL=False)",
        )
    )
    rows.append(DetailSummaryRow(label="Dry-Run Status", value="✅ Supported (Non-executing audit)"))
    if not payment_record or payment_record.status != PaymentStatus.SETTLED:
        rows.append(DetailSummaryRow(label="Transaction Status", value="🔒 No transaction initiated"))
    else:
        rows.append(DetailSummaryRow(label="Transaction Status", value="✅ Settled on testnet"))

    ctas = [
        CtaAction(
            label="Check Booking Status",
            selection={"action": "status_lookup", "booking_id": booking_id},
            primary=True,
        )
    ]

    payload = DetailCardPayload(
        title=f"Booking Payment Audit: {booking_id}",
        summary_rows=rows,
        ctas=ctas,
    )
    return create_card_content(payload, card_id=uuid4())


def extract_interaction_action(
    msg: ChatMessage, user_text: str
) -> tuple[BookingActionType, str | None, dict[str, Any] | None]:
    """
    Extract user action from incoming ChatMessage across supported interaction paths:
    1. Card interaction (MetadataContent block with CardResponse selection).
    2. JSON string in TextContent (for web chat clients passing selection payload as text).
    3. Natural language booking action parser (fallback commands, e.g. 'select option 1', 'approve', 'cancel').

    Returns (BookingActionType, action_param, raw_metadata_dict).
    """
    # 1. Check MetadataContent blocks for CardResponse
    for item in msg.content:
        if isinstance(item, MetadataContent) or getattr(item, "type", None) == "metadata":
            try:
                card_resp = extract_card_response(item)
                if card_resp is not None:
                    if card_resp.cancelled:
                        return BookingActionType.CANCEL, None, card_resp.selection

                    if card_resp.selection:
                        sel = card_resp.selection
                        act = sel.get("action")
                        if act in ("select_channel", "select_option"):
                            param = sel.get("rank") or sel.get("option_id")
                            return (
                                BookingActionType.SELECT_OPTION,
                                str(param) if param is not None else "1",
                                sel,
                            )
                        elif act in ("approve_booking", "approve"):
                            prop_id = sel.get("proposal_id")
                            return (
                                BookingActionType.APPROVE,
                                str(prop_id) if prop_id else None,
                                sel,
                            )
                        elif act in ("reject_booking", "cancel", "decline"):
                            prop_id = sel.get("proposal_id")
                            return (
                                BookingActionType.CANCEL,
                                str(prop_id) if prop_id else None,
                                sel,
                            )
                        elif act in ("payment_readiness", "check_payment_readiness", "payment_readiness_audit"):
                            prop_id = sel.get("proposal_id") or sel.get("reference")
                            return (
                                BookingActionType.PAYMENT_READINESS_AUDIT,
                                str(prop_id).upper() if prop_id else None,
                                sel,
                            )
                        elif act in ("payment_status_lookup", "check_payment", "payment_status"):
                            p_ref = sel.get("reference") or sel.get("payment_reference") or sel.get("booking_id")
                            return (
                                BookingActionType.PAYMENT_STATUS_LOOKUP,
                                str(p_ref) if p_ref else None,
                                sel,
                            )
                        elif act in ("status_lookup", "check_status"):
                            b_id = sel.get("booking_id") or sel.get("reference")
                            if b_id and str(b_id).upper().startswith("KB-PAY-"):
                                return (
                                    BookingActionType.PAYMENT_STATUS_LOOKUP,
                                    str(b_id).upper(),
                                    sel,
                                )
                            return (
                                BookingActionType.STATUS_LOOKUP,
                                str(b_id) if b_id else None,
                                sel,
                            )
                        elif act in ("authorize_payment", "approve_payment"):
                            ref = sel.get("reference")
                            return (
                                BookingActionType.AUTHORIZE_PAYMENT,
                                str(ref) if ref else None,
                                sel,
                            )
                        elif act in ("reject_payment", "decline_payment"):
                            ref = sel.get("reference")
                            return (
                                BookingActionType.REJECT_PAYMENT,
                                str(ref) if ref else None,
                                sel,
                            )
                        elif act in (
                            "request_payment_review",
                            "pay_advisory_fee",
                            "request_payment",
                            "review_payment",
                        ):
                            return (
                                BookingActionType.REQUEST_PAYMENT_REVIEW,
                                sel.get("reference") or sel.get("booking_id"),
                                sel,
                            )
            except Exception as ex:
                logger.debug(f"Failed to extract card response: {ex}")

    # 2. Check if user_text is a JSON string
    cleaned = strip_agent_mention(user_text).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            data = json.loads(cleaned)
            act = data.get("action")
            if act in ("select_channel", "select_option"):
                param = data.get("rank") or data.get("option_id")
                return (
                    BookingActionType.SELECT_OPTION,
                    str(param) if param is not None else "1",
                    data,
                )
            elif act in ("approve_booking", "approve"):
                prop_id = data.get("proposal_id")
                return (
                    BookingActionType.APPROVE,
                    str(prop_id) if prop_id else None,
                    data,
                )
            elif act in ("reject_booking", "cancel", "decline"):
                prop_id = data.get("proposal_id")
                return (
                    BookingActionType.CANCEL,
                    str(prop_id) if prop_id else None,
                    data,
                )
            elif act in ("payment_readiness", "check_payment_readiness", "payment_readiness_audit"):
                prop_id = data.get("proposal_id") or data.get("reference")
                return (
                    BookingActionType.PAYMENT_READINESS_AUDIT,
                    str(prop_id).upper() if prop_id else None,
                    data,
                )
            elif act in ("payment_status_lookup", "check_payment", "payment_status"):
                p_ref = data.get("reference") or data.get("payment_reference") or data.get("booking_id")
                return (
                    BookingActionType.PAYMENT_STATUS_LOOKUP,
                    str(p_ref) if p_ref else None,
                    data,
                )
            elif act in ("status_lookup", "check_status"):
                b_id = data.get("booking_id") or data.get("reference")
                if b_id and str(b_id).upper().startswith("KB-PAY-"):
                    return (
                        BookingActionType.PAYMENT_STATUS_LOOKUP,
                        str(b_id).upper(),
                        data,
                    )
                return (
                    BookingActionType.STATUS_LOOKUP,
                    str(b_id) if b_id else None,
                    data,
                )
            elif act in ("authorize_payment", "approve_payment"):
                ref = data.get("reference")
                return (
                    BookingActionType.AUTHORIZE_PAYMENT,
                    str(ref) if ref else None,
                    data,
                )
            elif act in ("reject_payment", "decline_payment"):
                ref = data.get("reference")
                return (
                    BookingActionType.REJECT_PAYMENT,
                    str(ref) if ref else None,
                    data,
                )
            elif act in (
                "request_payment_review",
                "pay_advisory_fee",
                "request_payment",
                "review_payment",
            ):
                return (
                    BookingActionType.REQUEST_PAYMENT_REVIEW,
                    data.get("reference") or data.get("booking_id"),
                    data,
                )
        except Exception:
            pass

    # 3. Check for explicit payment authorization command
    from krishibazaar.services.payment_service import parse_explicit_payment_approval
    payment_approval = parse_explicit_payment_approval(cleaned)
    if payment_approval is not None:
        return (
            BookingActionType.AUTHORIZE_PAYMENT,
            payment_approval.reference,
            payment_approval.model_dump(),
        )

    # 4. Check for payment review request commands
    clean_lower = cleaned.lower()
    if clean_lower in (
        "proceed to payment review",
        "proceed to payment",
        "pay advisory fee",
        "request payment",
        "payment review",
        "testnet payment",
        "pay advisory",
        "review payment",
        "advisory fee",
        "pay fee",
    ) or clean_lower.startswith("proceed to payment") or clean_lower.startswith("pay advisory") or clean_lower.startswith("request payment"):
        book_m = re.search(r"\b(kb-book-[a-z0-9\-]+)\b", cleaned, re.IGNORECASE)
        target_param = book_m.group(1).upper() if book_m else None
        return (
            BookingActionType.REQUEST_PAYMENT_REVIEW,
            target_param,
            None,
        )

    # 5. Fall back to natural language text booking action parser
    action, param = parse_booking_action(cleaned)
    return action, param, None


def build_chat_response(
    text: str, card_content: MetadataContent | None = None
) -> ChatMessage:
    """
    Construct a complete ChatMessage containing readable TextContent fallback,
    optional interactive card MetadataContent, and EndSessionContent.
    """
    content = [TextContent(text=text)]
    if card_content is not None:
        content.append(card_content)
    content.append(EndSessionContent())
    return ChatMessage(content=content)
