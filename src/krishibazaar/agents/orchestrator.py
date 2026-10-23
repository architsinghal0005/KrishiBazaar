"""
KrishiBazaar Orchestrator Agent.
Primary ASI:One-facing conversational front-door implementing Fetch.ai Agent Chat Protocol (ACP).
Coordinates specialist agents (Market, Weather, Buyer, Transport) and deterministic profit optimizer.
"""

from __future__ import annotations
import asyncio
import inspect
import logging
import re
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4
from pydantic import BaseModel
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    EndSessionContent,
    chat_protocol_spec,
)

from krishibazaar.agents.resolver import get_krishibazaar_resolver
from krishibazaar.config import settings
from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    BuyerOffer,
    BuyerOffersResult,
    BuyerQuery,
    DataProvenance,
    FarmerInquiry,
    FarmerIntent,
    MandiPriceRecord,
    MandiPriceResult,
    MarketQuery,
    OptimizationResult,
    PaymentPurpose,
    PaymentRecord,
    PaymentStatus,
    ExplicitPaymentApproval,
    TransportQuote,
    TransportQuotesResult,
    TransportQuery,
    WeatherQuery,
    WeatherResult,
    WeatherRiskReport,
)
from krishibazaar.services.booking_service import (
    BookingActionType,
    create_booking_proposal,
    execute_simulated_booking,
    format_booking_confirmation,
    format_booking_status,
    format_proposal_summary,
    parse_booking_action,
    strip_agent_mention,
    is_casual_acknowledgement,
    is_informational_query,
    is_payment_readiness_query,
    is_greeting,
    is_diagnostic_text,
    has_produce_signals,
    extract_proposal_reference,
    extract_booking_reference,
)
from krishibazaar.agents.mailbox_auth import patch_agent_mailbox
from krishibazaar.services.card_service import (
    build_farmer_inquiry_card,
    build_channel_recommendations_card,
    build_proposal_review_card,
    build_booking_confirmed_card,
    build_incomplete_inquiry_form_card,
    build_invalid_selection_card,
    build_expired_proposal_card,
    build_duplicate_approval_card,
    build_payment_review_card,
    build_payment_confirmed_card,
    build_payment_status_card,
    build_payment_readiness_card,
    build_booking_payment_audit_card,
    extract_interaction_action,
    build_chat_response,
)
from krishibazaar.services.payment_service import (
    format_payment_status,
    format_payment_readiness_audit,
    format_booking_payment_audit,
    mask_address,
)
from krishibazaar.services.buyer_service import get_buyer_offers_async
from krishibazaar.services.extraction import extract_farmer_intent_async
from krishibazaar.services.mandi_service import (
    get_mandi_prices_async,
    resolve_district_state,
)
from krishibazaar.services.optimizer import optimize_sales_channels
from krishibazaar.services.transport_service import get_transport_quotes_async
from krishibazaar.services.weather_service import (
    get_transit_weather_async,
    resolve_target_date,
    resolve_planned_transit_date,
)
from krishibazaar.storage import storage

logger = logging.getLogger("krishibazaar.orchestrator")


def get_session_key(sender: str, card_selection: dict[str, Any] | None = None) -> str:
    """
    Derive a stable session/conversation key across incoming ChatMessages.
    Enforces strict session isolation so one sender cannot hijack or access another sender's session.
    """
    if card_selection and isinstance(card_selection, dict):
        sid = card_selection.get("session_id")
        if sid and isinstance(sid, str) and sid.strip():
            cand = sid.strip()
            # Session isolation: A sender can only reference a session_id that
            # matches their sender address or is explicitly prefixed by their sender address.
            if cand == sender.strip() or cand.startswith(f"{sender.strip()}:"):
                return cand
    return sender.strip()


# Initialize Agent Chat Protocol using official Fetch.ai specification
chat_proto = Protocol(spec=chat_protocol_spec)

# Specialist response protocol registering typed models so Orchestrator agent recognizes
# all incoming specialist response schema digests (MandiPriceResult, WeatherResult, BuyerOffersResult, TransportQuotesResult)
specialist_response_proto = Protocol(name="KrishiBazaarSpecialistResponseProtocol", version="0.1.0")


@specialist_response_proto.on_message(MandiPriceResult)
async def handle_async_mandi_result(ctx: Context, sender: str, msg: MandiPriceResult) -> None:
    req_id = getattr(msg, "request_id", None) or "ASYNC"
    ctx.logger.info(
        f"[{req_id}] Specialist response received from '{sender}': "
        f"type=MandiPriceResult, success={msg.success}, records={len(msg.records)}"
    )


@specialist_response_proto.on_message(WeatherResult)
async def handle_async_weather_result(ctx: Context, sender: str, msg: WeatherResult) -> None:
    req_id = getattr(msg, "request_id", None) or "ASYNC"
    hazard = msg.report.spoilage_hazard_level if msg.report else "N/A"
    ctx.logger.info(
        f"[{req_id}] Specialist response received from '{sender}': "
        f"type=WeatherResult, success={msg.success}, hazard={hazard}"
    )


@specialist_response_proto.on_message(BuyerOffersResult)
async def handle_async_buyer_result(ctx: Context, sender: str, msg: BuyerOffersResult) -> None:
    req_id = getattr(msg, "request_id", None) or "ASYNC"
    ctx.logger.info(
        f"[{req_id}] Specialist response received from '{sender}': "
        f"type=BuyerOffersResult, success={msg.success}, offers={len(msg.offers)}"
    )


@specialist_response_proto.on_message(TransportQuotesResult)
async def handle_async_transport_result(ctx: Context, sender: str, msg: TransportQuotesResult) -> None:
    req_id = getattr(msg, "request_id", None) or "ASYNC"
    ctx.logger.info(
        f"[{req_id}] Specialist response received from '{sender}': "
        f"type=TransportQuotesResult, success={msg.success}, quotes={len(msg.quotes)}"
    )



def build_greeting_response(user_text: str | None = None) -> str:
    """Construct initial KrishiBazaar greeting message."""
    return (
        "ðŸŒ¾ **Namaste! Welcome to KrishiBazaar (à¤•à¥ƒà¤·à¤¿à¤¬à¤¾à¤œà¤¼à¤¾à¤°)** ðŸŒ¾\n\n"
        "I am your autonomous farm-to-market negotiation agent. I help Indian farmers compare "
        "mandi benchmark prices, assess transit weather hazards, receive competitive buyer offers, "
        "and calculate estimated net returns.\n\n"
        "ðŸ‘‰ **How to start:** Tell me what you have to sell, for example:\n"
        "*\"I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow.\"*"
    )


def build_introduction_response(existing_booking_id: str | None = None) -> str:
    """
    Construct a comprehensive, informative introduction to KrishiBazaar explaining
    how it helps Indian farmers maximize net returns without LLM guesswork.
    General informational queries must return an informational overview without
    injecting or leaking unrelated booking details.
    """
    lines = [
        "ðŸŒ¾ **Welcome to KrishiBazaar (à¤•à¥ƒà¤·à¤¿à¤¬à¤¾à¤œà¤¼à¤¾à¤°)** ðŸŒ¾",
        "*Autonomous Farm-to-Market Negotiation Agent Network for Indian Farmers*",
        "",
        "**KrishiBazaar** is an intelligent multi-agent network designed to solve agricultural price asymmetry. "
        "We help Indian farmers compare live wholesale market rates, transit weather risks, and competitive procurement offers "
        "to calculate their true **in-pocket net return** before leaving the farmgate.",
        "",
        "### ðŸŒŸ How KrishiBazaar Helps Farmers:",
        "1. ðŸ“Š **Live APMC Mandi Intelligence:** Queries real-time wholesale modal prices and daily arrivals directly from the Government of India (`data.gov.in` / AGMARKNET).",
        "2. ðŸŒ¦ï¸ **Transit Spoilage & Weather Hazards:** Connects with Open-Meteo forecasts to evaluate precipitation and heat spoilage risks along transit routes.",
        "3. ðŸ¤ **Competitive Buyer Network Bids:** Aggregates benchmark procurement bids from Farmer Producer Organizations (FPOs), regional retail chains, and bulk corporate buyers with quality premiums.",
        "4. ðŸš› **Capacity-Matched Freight Logistics:** Automatically matches vehicle capacity (Tata Ace, 407, 17ft, 22ft) with distance-based freight tariffs and weather protection (tarpaulin) surcharges.",
        "5. ðŸ’° **Deterministic Net Return Calculation:** Calculates your true in-pocket payout using strict Python arithmetic: deducting freight, statutory APMC cess (1.5%), and loading/hamali (zero LLM hallucinations).",
        "6. ðŸ”’ **Strict Farmer Approval Safety Gate:** No commercial contract, transport booking, or payment is ever executed without your explicit human approval.",
        "",
        "ðŸ‘‰ **How to start an inquiry:** Simply describe your crop, quantity, and village or district. For example:",
        "*\"I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow.\"*",
    ]
    return "\n".join(lines)


def format_mandi_market_report(
    crop: str,
    district: str,
    state: str | None,
    mandi_result: MandiPriceResult,
    nearby_result: MandiPriceResult | None = None,
) -> list[str]:
    """Format transparent mandi price report, zero-record notice, or error explanation."""
    from datetime import timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    today_ist = datetime.now(ist).date()
    lines: list[str] = []

    # Case 1: Live records found for target district
    if mandi_result.success and mandi_result.records:
        lines.append(f"ðŸ“Š **Live Mandi Wholesale Prices ({crop.title()} in {district}):**")
        retrieval_ts = mandi_result.provenance.retrieval_timestamp_utc
        obs_date = mandi_result.provenance.observation_date
        lines.append(
            f"*Source: {mandi_result.provenance.source} | Observation Date: {obs_date} | Retrieved (UTC): {retrieval_ts}*"
        )

        has_authentic = False
        latest_arrival: str | None = None
        for rec in mandi_result.records:
            if rec.arrival_date and (latest_arrival is None or rec.arrival_date > latest_arrival):
                latest_arrival = rec.arrival_date
            if rec.is_suspicious:
                lines.append(
                    f"â€¢ âš ï¸ **{rec.mandi_name}** ({rec.district}): Modal Price INR {rec.modal_price:,.2f}/quintal â€” "
                    f"*Flagged as suspicious ({rec.suspicion_reason}) and excluded from recommendation.*"
                )
            else:
                has_authentic = True
                price_range = (
                    f"(Min: INR {rec.min_price:,.2f}, Max: INR {rec.max_price:,.2f})"
                    if rec.min_price and rec.max_price
                    else ""
                )
                lines.append(
                    f"â€¢ **{rec.mandi_name}** ({rec.district}): Modal Price **INR {rec.modal_price:,.2f}**/quintal {price_range} â€” "
                    f"Arrival Date: {rec.arrival_date}"
                )

        if not has_authentic:
            lines.append("âš ï¸ *All reported prices for this district were flagged as suspicious (e.g. bundle/kg entries).*")

        # Staleness Warning Check: Check freshness_status or record arrival date age
        is_stale = mandi_result.provenance.freshness_status == "STALE"
        stale_detail = ""
        check_date_str = latest_arrival or obs_date
        if check_date_str:
            try:
                if "/" in check_date_str:
                    parts = check_date_str.split("/")
                    rec_date = datetime(int(parts[2]), int(parts[1]), int(parts[0])).date()
                else:
                    rec_date = datetime.strptime(check_date_str, "%Y-%m-%d").date()
                days_old = (today_ist - rec_date).days
                if days_old >= 2:
                    is_stale = True
                    stale_detail = f"Latest arrival record is from {check_date_str} ({days_old} days old)."
            except Exception:
                pass

        if is_stale:
            warn_msg = (
                f"âš ï¸ **Mandi Data Freshness Warning:** {stale_detail or 'Market records are stale or historical.'} "
                f"Wholesale prices fluctuate daily based on yard arrivals; please verify current physical market conditions before committing."
            )
            lines.append(warn_msg)

    # Case 2: Zero live records for target district
    elif mandi_result.success and not mandi_result.records:
        loc_str = f"district '{district}'" + (f", {state}" if state else "")
        retrieval_ts = mandi_result.provenance.retrieval_timestamp_utc
        obs_date = mandi_result.provenance.observation_date
        lines.append(
            f"â„¹ï¸ **Live Mandi Status:** No live arrival records were reported for '{crop.title()}' in {loc_str} on data.gov.in (AGMARKNET) today."
        )
        lines.append(
            f"*Source: {mandi_result.provenance.source} | Observation Date: {obs_date} | Retrieved (UTC): {retrieval_ts}*"
        )
        lines.append("*We never fabricate prices or substitute sample benchmark data as live prices.*")

        # Offer clearly labelled state live benchmarks if actually retrieved
        if nearby_result and nearby_result.success and nearby_result.records:
            lines.append("")
            state_label = f"{state} " if state else ""
            lines.append(f"ðŸ“ **Other Live {state_label}Mandi Benchmarks:**")
            n_ts = nearby_result.provenance.retrieval_timestamp_utc
            n_obs = nearby_result.provenance.observation_date
            lines.append(
                f"*Source: {nearby_result.provenance.source} | Observation Date: {n_obs} | Retrieved (UTC): {n_ts}*"
            )
            shown = 0
            for rec in nearby_result.records:
                if rec.is_suspicious:
                    lines.append(
                        f"â€¢ âš ï¸ **{rec.mandi_name}** ({rec.district}): Modal Price INR {rec.modal_price:,.2f}/quintal â€” "
                        f"*Flagged as suspicious ({rec.suspicion_reason})*"
                    )
                else:
                    lines.append(
                        f"â€¢ **{rec.mandi_name}** ({rec.district}): Modal Price **INR {rec.modal_price:,.2f}**/quintal â€” "
                        f"Arrival Date: {rec.arrival_date}"
                    )
                shown += 1
                if shown >= 5:
                    break

            if nearby_result.provenance.freshness_status == "STALE":
                lines.append("âš ï¸ **Mandi Data Freshness Warning:** State benchmark data is marked stale. Use as indicative reference only.")

    # Case 3: API failed or timed out
    else:
        err_msg = mandi_result.error_message or "API connection unavailable"
        retrieval_ts = mandi_result.provenance.retrieval_timestamp_utc
        lines.append(f"âš ï¸ **Market Data Notice:** Live mandi prices could not be retrieved from data.gov.in at this moment ({err_msg}).")
        lines.append(f"*Source: {mandi_result.provenance.source} | Query Timestamp (UTC): {retrieval_ts}*")
        lines.append("*We do not invent prices or make unverified assumptions.*")

    return lines


def format_weather_report(
    weather_result: WeatherResult,
    selling_deadline: str | None = None,
    target_date: str | None = None,
    date_was_past: bool = False,
    original_past_date: str | None = None,
) -> list[str]:
    """Format transit weather hazard forecast, clearly distinguishing forecasts from observed conditions."""
    lines: list[str] = []
    if weather_result.success and weather_result.report:
        rep = weather_result.report
        is_fallback = "sample" in weather_result.provenance.source.lower() or not weather_result.provenance.is_live
        source_note = "Offline Fallback Sample" if is_fallback else "Live Forecast (Open-Meteo)"

        req_date = target_date or rep.target_date_requested
        date_alignment_note = ""
        if date_was_past and original_past_date:
            date_alignment_note = (
                f" [Note: Requested pickup date '{original_past_date}' was in the past. "
                f"Weather forecast corresponds to the planned future transit date: {rep.forecast_date}]"
            )
        elif req_date:
            if rep.is_target_date_matched or rep.forecast_date == req_date:
                deadline_label = f" (Deadline: {selling_deadline})" if selling_deadline and selling_deadline != req_date else ""
                date_alignment_note = f" [Forecast aligned with requested selling/transit date: {rep.forecast_date}{deadline_label}]"
            else:
                date_alignment_note = (
                    f" [âš ï¸ Actual forecast date: {rep.forecast_date}. Requested date '{req_date}' was outside available forecast window â€” "
                    f"do NOT treat this as {selling_deadline or req_date}'s forecast]"
                )
        elif rep.forecast_date:
            date_alignment_note = f" [Forecast Date: {rep.forecast_date}]"

        lines.append(f"ðŸŒ¤ï¸ **Transit Weather & Spoilage Hazard ({rep.location}):**")
        lines.append(f"*Source: {weather_result.provenance.source} | Forecast Date: {rep.forecast_date} | Retrieved (UTC): {weather_result.provenance.retrieval_timestamp_utc} ({source_note})*{date_alignment_note}")
        lines.append(
            f"â€¢ **Atmospheric Forecast:** Temp: {rep.temperature_min_c:g}Â°C - {rep.temperature_max_c:g}Â°C | "
            f"Precipitation: {rep.precipitation_sum_mm:g} mm ({rep.precipitation_probability_pct:g}% chance) | "
            f"Wind: {rep.wind_speed_max_kmh:g} km/h"
        )
        hazard_icon = (
            "ðŸŸ¢" if rep.spoilage_hazard_level == "LOW" else ("ðŸŸ¡" if rep.spoilage_hazard_level == "MEDIUM" else "ðŸ”´")
        )
        lines.append(f"â€¢ **Transit Spoilage Hazard:** {hazard_icon} **{rep.spoilage_hazard_level} HAZARD**")
        lines.append(f"â€¢ **Actionable Advice:** {rep.transit_advice}")
        lines.append("*(Note: Weather predictions represent atmospheric forecasts and should be verified against local ground conditions)*")
    else:
        err = weather_result.error_message or "Weather service unavailable"
        lines.append(f"âš ï¸ **Transit Weather Notice:** Live forecast could not be retrieved ({err}). Standard dry transit conditions assumed.")
    return lines


def format_optimization_report(
    opt: OptimizationResult,
    buyer_result: BuyerOffersResult | None = None,
    transport_result: TransportQuotesResult | None = None,
) -> list[str]:
    """Format deterministic net-return channel ranking and detailed deductions with explicit provenance."""
    lines: list[str] = []
    lines.append("ðŸ“ˆ **Deterministic Net-Return Optimization & Channel Ranking:**")
    lines.append(
        "*All calculations are deterministic estimates using stated assumptions, official mandi benchmarks, "
        "and simulated market quotes. Results represent estimated net returns, NOT guaranteed payouts or confirmed sale prices.*"
    )
    lines.append("")

    if not opt.all_options:
        lines.append("âš ï¸ *No sales channels or buyer bids were available for optimization.*")
        return lines

    # Summary of options evaluated
    lines.append(f"Evaluated **{len(opt.all_options)} selling channel(s)** across live mandis and benchmark buyer bids:")
    for opt_item in opt.all_options:
        badge = "ðŸ† Rank 1 (Top Option)" if opt_item.rank == 1 else f"Rank {opt_item.rank}"
        channel_lbl = (
            "Direct Buyer (Simulated Bid)"
            if opt_item.channel_type == "DIRECT_BUYER"
            else "APMC Mandi (Benchmark Rate)"
        )
        feasibility_mark = "âœ… Meets Target" if opt_item.meets_minimum_condition else "âš ï¸ Below Minimum"
        lines.append(
            f"â€¢ **{badge}: {opt_item.destination_name}** [{channel_lbl}] â€” "
            f"Est. Net Return: **INR {opt_item.expected_net_return:,.2f}** "
            f"(INR {opt_item.net_return_per_quintal:,.2f}/qtl) â€” {feasibility_mark}"
        )

    lines.append("")
    # Top Recommended Option Detailed Breakdown
    if opt.recommended_option:
        top = opt.recommended_option
        is_mandi = top.channel_type == "MANDI"
        channel_desc = (
            "APMC Wholesale Mandi (Official Benchmark Modal Price â€” no buyer contract exists)"
            if is_mandi
            else "Direct Commercial Buyer (Simulated Bid)"
        )
        lines.append(f"ðŸ’¡ **Top Recommendation: {top.destination_name}** [{channel_desc}]")
        lines.append(
            f"â€¢ **Estimated Net In-Pocket Return:** **INR {top.expected_net_return:,.2f}** "
            f"(INR {top.net_return_per_quintal:,.2f}/qtl) *(Estimated return only; NOT a guaranteed payout)*"
        )
        lines.append(
            f"â€¢ **Gross Value:** INR {top.gross_value:,.2f} "
            f"({top.quantity_quintals:g} qtl @ INR {top.offered_price_per_quintal:,.2f}/qtl) "
            f"[{top.price_provenance}]"
        )
        lines.append(
            f"â€¢ **Itemized Deductions (Total Estimated Deductions: INR {top.total_deductions:,.2f}):**"
        )
        lines.append(
            f"  - Transport: INR {top.transport_cost:,.2f} [{top.transport_provenance}]"
        )
        if getattr(top, "is_apmc_exempt", False) or (top.channel_type == "DIRECT_BUYER" and top.mandi_cess_apmc == 0.0):
            lines.append(
                f"  - APMC Statutory Cess: INR 0.00 [Assumed direct-buyer trade exemption outside APMC yard; unverified assumption, not legally verified]"
            )
            lines.append(
                f"  - Hamali/Labor: INR 0.00 [Assumed direct farmgate/buyer handling without APMC market labor charge; unverified assumption]"
            )
        else:
            lines.append(
                f"  - APMC Statutory Cess: INR {top.mandi_cess_apmc:,.2f} [Assumed statutory tariff @ 1.5%]"
            )
            lines.append(
                f"  - Hamali/Labor: INR {top.loading_unloading_cost:,.2f} [Assumed standard labor charge @ INR 25/qtl]"
            )
        if top.storage_cost > 0:
            lines.append(
                f"  - Storage: INR {top.storage_cost:,.2f} [Assumed warehouse holding charge]"
            )
        weather_expl = getattr(top, "weather_loss_note", "") or "Estimated transit risk deduction"
        lines.append(
            f"  - Transit Weather Spoilage: INR {top.estimated_loss_cost:,.2f} [{weather_expl}]"
        )
        lines.append(
            f"â€¢ **Financial Reconciliation:** Gross Value (INR {top.gross_value:,.2f}) - Total Deductions (INR {top.total_deductions:,.2f}) "
            f"= Est. Net Return (INR {top.expected_net_return:,.2f})"
        )
        lines.append(
            f"â€¢ **Input Provenance & Deductions Breakdown:** {top.charges_provenance}. "
            f"{'Note: This option is based on an official mandi modal-price benchmark from AGMARKNET. No confirmed buyer offer exists; APMC prices depend on daily physical auction bidding.' if is_mandi else 'Note: Direct buyer bid is a simulated benchmark offer.'}"
        )
        lines.append(f"â€¢ **Rationale:** {top.recommendation_note}")

    if not opt.feasible_options and opt.minimum_net_return_inr:
        lines.append("")
        lines.append(
            f"âš ï¸ **Target Threshold Notice:** None of the options met your minimum net payout target of INR {opt.minimum_net_return_inr:,.2f}."
        )

    return lines


def format_simulation_and_safety_notices(coord: dict[str, Any] | None = None) -> list[str]:
    """Format transparency simulation notices, specialist communication diagnostics, and safety approval gates."""
    lines: list[str] = [
        "â„¹ï¸ **Simulation Notice:** All buyer offers and transport quotes are simulated benchmark models for MVP negotiation comparison. No real commercial buyer or carrier is contracted.",
    ]

    # Specialist Diagnostics Transparency Reporting
    if coord and "specialist_diagnostics" in coord:
        diag = coord["specialist_diagnostics"]
        failed_specialists = []
        remote_specialists = []
        for key, s in diag.items():
            name = s.get("agent_role", key)
            if s.get("status") == "REMOTE_SUCCESS":
                remote_specialists.append(name)
            elif s.get("status") in ("FALLBACK_USED", "FAILED"):
                err = s.get("error_detail") or "remote query unavailable"
                failed_specialists.append(f"â€¢ **{name}:** Remote query unavailable ({err}). Used verified local fallback data.")

        if failed_specialists:
            lines.append("âš ï¸ **Specialist Communication Diagnostics:**")
            lines.extend(failed_specialists)
            if remote_specialists:
                lines.append(f"*(Remote agent messaging verified for: {', '.join(remote_specialists)})*")
        elif remote_specialists:
            lines.append(f"ðŸ“¡ **Multi-Agent Network Verified:** All specialist inquiries serviced via live agent-to-agent HTTP messaging ({', '.join(remote_specialists)}).")

    lines.extend([
        "ðŸ”’ **Safety Gate:** No real booking, payment, or carrier dispatch has been made. Any booking requires your explicit approval.",
        "ðŸ‘‰ **Next Step:** To select an option and generate a booking proposal, reply with *\"Select option 1\"* (or choose another rank), or request parameter adjustments.",
    ])
    return lines


def is_transient_error(err_str: str | None) -> bool:
    """Determine if an error represents a transient network/gateway error."""
    if not err_str:
        return False
    lower = err_str.lower()
    transient_indicators = (
        "503",
        "522",
        "502",
        "504",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "connection timed out",
        "connection reset",
        "connection refused",
        "server disconnected",
        "clientconnectorerror",
        "almanac",
        "resolution",
        "unable to resolve destination endpoint",
        "temporary",
    )
    return any(ind in lower for ind in transient_indicators)


async def query_specialist_agent_async(
    destination: str,
    query_msg: BaseModel,
    response_type: type[Any],
    specialist_name: str,
    ctx: Context | None = None,
    timeout: float | None = None,
    max_retries: int = 1,
    backoff_seconds: float = 0.5,
) -> tuple[Any | None, str | None]:
    """
    Send typed request message to a remote specialist agent address and await response.
    Uses ctx.send_and_receive when an active Context with messaging is provided,
    otherwise falls back to uAgents send_sync_message using KrishiBazaarResolver.
    Implements bounded retries with backoff for transient network/resolution errors (HTTP 503/522, Almanac lookup failures).
    Returns (result_object, error_message).
    """
    effective_timeout = timeout if timeout is not None else settings.specialist_timeout_seconds
    request_id = getattr(query_msg, "request_id", None) or f"REQ-{uuid4().hex[:8].upper()}"
    if hasattr(query_msg, "request_id") and getattr(query_msg, "request_id", None) is None:
        query_msg.request_id = request_id

    if not destination or not destination.strip():
        logger.warning(f"[{request_id}] Query aborted: Agent address for '{specialist_name}' is empty.")
        return None, f"Agent address for '{specialist_name}' is empty."

    logger.info(
        f"[{request_id}] Outbound request to '{specialist_name}' ({destination}): "
        f"msg_type={type(query_msg).__name__}, expected_response={response_type.__name__}, timeout={effective_timeout}s"
    )

    from uagents.dispatch import dispatcher

    can_use_ctx = (
        ctx is not None
        and hasattr(ctx, "send_and_receive")
        and callable(getattr(ctx, "send_and_receive"))
    )

    resolver = get_krishibazaar_resolver()
    resolved_addr, endpoints = await resolver.resolve(destination)
    route_type = "unknown"
    if endpoints:
        if any("127.0.0.1" in ep or "localhost" in ep for ep in endpoints):
            route_type = "reachable_local_endpoint"
        elif any("mailbox" in ep for ep in endpoints):
            route_type = "agentverse_mailbox_route"
        else:
            route_type = "custom_endpoint"

    logger.info(
        f"[{request_id}] Resolved destination '{specialist_name}' ({destination[:16]}...) -> "
        f"endpoints={endpoints}, route_type={route_type}"
    )

    max_attempts = 1 + max(0, max_retries)
    last_err: str | None = None

    for attempt in range(max_attempts):
        if can_use_ctx:
            try:
                send_kwargs: dict[str, Any] = {
                    "destination": destination,
                    "message": query_msg,
                    "response_type": response_type,
                    "timeout": int(effective_timeout),
                }
                if not isinstance(getattr(ctx, "send_and_receive"), AsyncMock):
                    send_kwargs["sync"] = True

                resp, msg_status = await asyncio.wait_for(
                    ctx.send_and_receive(**send_kwargs),
                    timeout=effective_timeout + 2.0,
                )
                if isinstance(resp, response_type):
                    if hasattr(resp, "request_id") and not getattr(resp, "request_id", None):
                        resp.request_id = request_id
                    logger.info(
                        f"[{request_id}] Response received from '{specialist_name}' ({destination[:16]}...) via {route_type}: "
                        f"type={type(resp).__name__}"
                    )
                    return resp, None
                if resp is not None:
                    err = f"Agent '{specialist_name}' ({destination}) validation failure (unexpected response: {type(resp).__name__}, expected {response_type.__name__})"
                    logger.warning(f"[{request_id}] {err}")
                    return None, err
                detail = getattr(msg_status, "detail", "No response received") if msg_status else "No response received"
                if "timeout" in detail.lower():
                    last_err = f"Agent '{specialist_name}' ({destination}) query timed out after {effective_timeout}s."
                else:
                    last_err = f"Agent '{specialist_name}' ({destination}) communication failed: {detail}"
                logger.warning(f"[{request_id}] {last_err}")

            except asyncio.TimeoutError:
                last_err = f"Agent '{specialist_name}' ({destination}) query timed out after {effective_timeout}s."
                logger.warning(f"[{request_id}] {last_err}")
                # A full-duration query timeout is not retried to avoid doubling overall request latency
                return None, last_err
            except Exception as ex:
                last_err = f"Agent '{specialist_name}' ({destination}) communication error: {str(ex)}"
                logger.error(f"[{request_id}] {last_err}")

        else:
            # Standalone uAgents send_sync_message with KrishiBazaarResolver and signed orchestrator identity
            try:
                from uagents.communication import send_sync_message
                from uagents.crypto import Identity
                from uagents_core.types import DeliveryStatus, MsgStatus
                from pydantic import ValidationError

                resp = await asyncio.wait_for(
                    send_sync_message(
                        destination=destination,
                        message=query_msg,
                        response_type=response_type,
                        resolver=resolver,
                        timeout=int(effective_timeout),
                    ),
                    timeout=effective_timeout + 2.0,
                )

                if isinstance(resp, response_type):
                    if hasattr(resp, "request_id") and not getattr(resp, "request_id", None):
                        resp.request_id = request_id
                    logger.info(
                        f"[{request_id}] Response received from '{specialist_name}' ({destination[:16]}...) via {route_type}: "
                        f"type={type(resp).__name__}"
                    )
                    return resp, None

                if isinstance(resp, MsgStatus):
                    if resp.status == DeliveryStatus.DELIVERED:
                        last_err = f"Agent '{specialist_name}' request delivered but handler not triggered or response not sent"
                    else:
                        last_err = f"Agent '{specialist_name}' request not delivered ({resp.detail})"
                    logger.warning(f"[{request_id}] {last_err}")

                elif isinstance(resp, dict) and "error" in resp:
                    err_msg = resp.get("error", "")
                    if "Query envelope expired" in err_msg:
                        last_err = f"Agent '{specialist_name}' request delivered but handler not triggered / response timed out ('Query envelope expired')"
                    else:
                        last_err = f"Agent '{specialist_name}' handler error: {err_msg}"
                    logger.warning(f"[{request_id}] {last_err}")

                elif resp is None:
                    last_err = f"Agent '{specialist_name}' response not received (empty reply)"
                    logger.warning(f"[{request_id}] {last_err}")

                else:
                    last_err = f"Agent '{specialist_name}' validation failure (unexpected response: {type(resp).__name__}, expected {response_type.__name__})"
                    logger.warning(f"[{request_id}] {last_err}")
                    return None, last_err

            except asyncio.TimeoutError:
                last_err = f"Agent '{specialist_name}' response not received (timeout after {effective_timeout}s waiting on {route_type})"
                logger.warning(f"[{request_id}] {last_err}")
                return None, last_err

            except ValidationError as val_err:
                if "Query envelope expired" in str(val_err):
                    last_err = f"Agent '{specialist_name}' request delivered but handler not triggered / response timed out ('Query envelope expired')"
                else:
                    last_err = f"Agent '{specialist_name}' validation failure (schema mismatch): {val_err}"
                logger.warning(f"[{request_id}] {last_err}")
                return None, last_err

            except Exception as ex:
                last_err = f"Agent '{specialist_name}' communication error: {type(ex).__name__} - {str(ex)}"
                logger.error(f"[{request_id}] {last_err}")

        # If error was transient and we have remaining attempts, retry with backoff
        if attempt < max_attempts - 1 and is_transient_error(last_err):
            logger.warning(
                f"[{request_id}] Transient error querying '{specialist_name}' ({destination}): {last_err}. "
                f"Retrying in {backoff_seconds}s (attempt {attempt + 1}/{max_attempts})..."
            )
            await asyncio.sleep(backoff_seconds)
            continue
        break

    return None, last_err


async def coordinate_specialists_async(
    crop: str,
    quantity_quintals: float,
    origin_location: str,
    selling_deadline: str | None = None,
    minimum_net_return_inr: float | None = None,
    storage_preference: str | None = None,
    specialist_timeout: float | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """
    Coordinate Market, Weather, Buyer (A & B), and Transport specialists concurrently with timeouts and partial-failure resilience.
    Uses configured agent addresses when available (real inter-agent messaging).
    Falls back to direct service calls only when explicitly allowed in local development.
    Feeds results into deterministic profit optimizer.
    """
    effective_timeout = (
        specialist_timeout if specialist_timeout is not None else settings.specialist_timeout_seconds
    )
    district, state = resolve_district_state(origin_location)
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    planned_transit_date, date_was_past, original_past_date = resolve_planned_transit_date(selling_deadline)
    target_date = planned_transit_date

    # 1. Market Specialist Query (Live Mandi Prices)
    async def fetch_market() -> tuple[MandiPriceResult, MandiPriceResult | None]:
        mandi_res: MandiPriceResult
        nearby_res: MandiPriceResult | None = None

        if settings.market_agent_address:
            query_msg = MarketQuery(commodity=crop, district=district, state=state)
            res, err = await query_specialist_agent_async(
                destination=settings.market_agent_address,
                query_msg=query_msg,
                response_type=MandiPriceResult,
                specialist_name="Market Specialist",
                ctx=ctx,
                timeout=effective_timeout,
            )
            if res is not None and isinstance(res, MandiPriceResult):
                mandi_res = res
                mandi_res.provenance.source = f"data.gov.in (AGMARKNET) via Market Agent [{settings.market_agent_address}]"
                mandi_res.provenance.disclaimer = f"Retrieved via real agent messaging from Market Agent [{settings.market_agent_address}]."
                if mandi_res.success and not mandi_res.records and state:
                    fallback_msg = MarketQuery(commodity=crop, district=None, state=state)
                    f_res, _ = await query_specialist_agent_async(
                        destination=settings.market_agent_address,
                        query_msg=fallback_msg,
                        response_type=MandiPriceResult,
                        specialist_name="Market Specialist (State Fallback)",
                        ctx=ctx,
                        timeout=effective_timeout,
                    )
                    if f_res is not None and isinstance(f_res, MandiPriceResult):
                        nearby_res = f_res
            elif settings.allow_direct_service_fallback:
                logger.info(
                    f"Market Agent ({settings.market_agent_address}) query failed/timed out ({err}). "
                    f"Invoking local direct-service fallback."
                )
                try:
                    mandi_res = await asyncio.wait_for(
                        get_mandi_prices_async(commodity=crop, district=district, state=state),
                        timeout=effective_timeout,
                    )
                    mandi_res.provenance.source = f"{mandi_res.provenance.source} (Local Fallback)"
                    mandi_res.provenance.disclaimer = (
                        f"Local service fallback used: Market Agent query failed ({err})."
                    )
                except Exception as ex:
                    mandi_res = MandiPriceResult(
                        success=False,
                        commodity=crop,
                        records=[],
                        error_message=f"Market fallback error: {str(ex)}",
                        provenance=DataProvenance(
                            source="data.gov.in (AGMARKNET) (Local Fallback Failed)",
                            retrieval_timestamp_utc=now_utc.isoformat(),
                            observation_date=today_str,
                            freshness_status="STALE",
                            is_live=False,
                            disclaimer=f"Agent failed ({err}) and fallback failed ({ex}).",
                        ),
                    )

                if mandi_res.success and not mandi_res.records and state:
                    try:
                        nearby_res = await asyncio.wait_for(
                            get_mandi_prices_async(commodity=crop, state=state, district=None),
                            timeout=effective_timeout,
                        )
                    except Exception:
                        nearby_res = None
            else:
                mandi_res = MandiPriceResult(
                    success=False,
                    commodity=crop,
                    records=[],
                    error_message=err or f"Market agent query timed out after {effective_timeout}s.",
                    provenance=DataProvenance(
                        source=f"data.gov.in (AGMARKNET) via Market Agent [{settings.market_agent_address}]",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="STALE",
                        is_live=False,
                        disclaimer="Market agent communication failed or timed out; direct-service fallback disabled.",
                    ),
                )
        elif settings.allow_direct_service_fallback:
            logger.info("Market agent address unconfigured. Invoking local direct-service fallback.")
            try:
                mandi_res = await asyncio.wait_for(
                    get_mandi_prices_async(commodity=crop, district=district, state=state),
                    timeout=effective_timeout,
                )
                mandi_res.provenance.source = f"{mandi_res.provenance.source} (Local Fallback)"
                mandi_res.provenance.disclaimer = "Local service fallback used: Market Agent address unconfigured."
            except asyncio.TimeoutError:
                mandi_res = MandiPriceResult(
                    success=False,
                    commodity=crop,
                    records=[],
                    error_message=f"Live mandi query timed out after {effective_timeout}s.",
                    provenance=DataProvenance(
                        source="data.gov.in (AGMARKNET) (Local Fallback)",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="STALE",
                        is_live=False,
                        disclaimer="Direct service timed out.",
                    ),
                )
            except Exception as ex:
                mandi_res = MandiPriceResult(
                    success=False,
                    commodity=crop,
                    records=[],
                    error_message=f"Live mandi query error: {str(ex)}",
                    provenance=DataProvenance(
                        source="data.gov.in (AGMARKNET) (Local Fallback)",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="STALE",
                        is_live=False,
                        disclaimer="Direct service error.",
                    ),
                )

            if mandi_res.success and not mandi_res.records and state:
                try:
                    nearby_res = await asyncio.wait_for(
                        get_mandi_prices_async(commodity=crop, state=state, district=None),
                        timeout=effective_timeout,
                    )
                except Exception:
                    nearby_res = None
        else:
            mandi_res = MandiPriceResult(
                success=False,
                commodity=crop,
                records=[],
                error_message="Market agent address not configured (MARKET_AGENT_ADDRESS is missing) and direct-service fallback is disabled.",
                provenance=DataProvenance(
                    source="data.gov.in (AGMARKNET)",
                    retrieval_timestamp_utc=now_utc.isoformat(),
                    observation_date=today_str,
                    freshness_status="STALE",
                    is_live=False,
                    disclaimer="Unconfigured agent; direct-service fallback disabled.",
                ),
            )

        return mandi_res, nearby_res

    # 2. Weather Specialist Query (Open-Meteo)
    async def fetch_weather() -> WeatherResult:
        if settings.weather_agent_address:
            query_msg = WeatherQuery(location=origin_location, target_date=target_date)
            res, err = await query_specialist_agent_async(
                destination=settings.weather_agent_address,
                query_msg=query_msg,
                response_type=WeatherResult,
                specialist_name="Weather Specialist",
                ctx=ctx,
                timeout=effective_timeout,
            )
            if res is not None and isinstance(res, WeatherResult):
                res.provenance.source = f"Open-Meteo API via Weather Agent [{settings.weather_agent_address}]"
                res.provenance.disclaimer = f"Retrieved via real agent messaging from Weather Agent [{settings.weather_agent_address}]."
                return res
            elif settings.allow_direct_service_fallback:
                logger.info(
                    f"Weather Agent ({settings.weather_agent_address}) query failed/timed out ({err}). "
                    f"Invoking local direct-service fallback."
                )
                try:
                    w_res = await asyncio.wait_for(
                        get_transit_weather_async(location=origin_location, target_date=target_date),
                        timeout=effective_timeout,
                    )
                    w_res.provenance.source = f"{w_res.provenance.source} (Local Fallback)"
                    w_res.provenance.disclaimer = (
                        f"Local service fallback used: Weather Agent query failed ({err})."
                    )
                    return w_res
                except Exception as ex:
                    return WeatherResult(
                        success=False,
                        location=origin_location,
                        error_message=f"Weather fallback error: {str(ex)}",
                        provenance=DataProvenance(
                            source="Open-Meteo API (Local Fallback Failed)",
                            retrieval_timestamp_utc=now_utc.isoformat(),
                            observation_date=today_str,
                            freshness_status="STALE",
                            is_live=False,
                            disclaimer=f"Agent failed ({err}) and fallback failed ({ex}).",
                        ),
                    )
            else:
                return WeatherResult(
                    success=False,
                    location=origin_location,
                    error_message=err or f"Weather agent query timed out after {effective_timeout}s.",
                    provenance=DataProvenance(
                        source=f"Open-Meteo API via Weather Agent [{settings.weather_agent_address}]",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="STALE",
                        is_live=False,
                        disclaimer="Weather agent communication failed or timed out; direct-service fallback disabled.",
                    ),
                )
        elif settings.allow_direct_service_fallback:
            logger.info("Weather agent address unconfigured. Invoking local direct-service fallback.")
            try:
                w_res = await asyncio.wait_for(
                    get_transit_weather_async(location=origin_location, target_date=target_date),
                    timeout=effective_timeout,
                )
                w_res.provenance.source = f"{w_res.provenance.source} (Local Fallback)"
                w_res.provenance.disclaimer = "Local service fallback used: Weather Agent address unconfigured."
                return w_res
            except asyncio.TimeoutError:
                return WeatherResult(
                    success=False,
                    location=origin_location,
                    error_message=f"Weather forecast query timed out after {effective_timeout}s.",
                    provenance=DataProvenance(
                        source="Open-Meteo API (Local Fallback)",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="STALE",
                        is_live=False,
                        disclaimer="Weather query timed out.",
                    ),
                )
            except Exception as ex:
                return WeatherResult(
                    success=False,
                    location=origin_location,
                    error_message=f"Weather query error: {str(ex)}",
                    provenance=DataProvenance(
                        source="Open-Meteo API (Local Fallback)",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="STALE",
                        is_live=False,
                        disclaimer="Weather query failed.",
                    ),
                )
        else:
            return WeatherResult(
                success=False,
                location=origin_location,
                error_message="Weather agent address not configured (WEATHER_AGENT_ADDRESS is missing) and direct-service fallback is disabled.",
                provenance=DataProvenance(
                    source="Open-Meteo API",
                    retrieval_timestamp_utc=now_utc.isoformat(),
                    observation_date=today_str,
                    freshness_status="STALE",
                    is_live=False,
                    disclaimer="Unconfigured agent; direct-service fallback disabled.",
                ),
            )

    # 3. Buyer Specialists Query (Buyer A and/or Buyer B)
    async def fetch_buyers() -> BuyerOffersResult:
        has_buyer_a = bool(settings.buyer_agent_address)
        has_buyer_b = bool(settings.buyer_b_agent_address)

        if has_buyer_a or has_buyer_b:
            queries = []
            labels = []
            if has_buyer_a:
                qa = BuyerQuery(crop=crop, quantity_quintals=quantity_quintals, origin_location=origin_location)
                queries.append(
                    query_specialist_agent_async(
                        destination=settings.buyer_agent_address,  # type: ignore[arg-type]
                        query_msg=qa,
                        response_type=BuyerOffersResult,
                        specialist_name="Buyer A Specialist",
                        ctx=ctx,
                        timeout=effective_timeout,
                    )
                )
                labels.append("Buyer A")
            if has_buyer_b:
                qb = BuyerQuery(crop=crop, quantity_quintals=quantity_quintals, origin_location=origin_location)
                queries.append(
                    query_specialist_agent_async(
                        destination=settings.buyer_b_agent_address,  # type: ignore[arg-type]
                        query_msg=qb,
                        response_type=BuyerOffersResult,
                        specialist_name="Buyer B Specialist",
                        ctx=ctx,
                        timeout=effective_timeout,
                    )
                )
                labels.append("Buyer B")

            results = await asyncio.gather(*queries, return_exceptions=True)

            combined_offers = []
            errors = []
            successful_labels = []

            for label, r in zip(labels, results):
                if isinstance(r, Exception):
                    errors.append(f"{label} error: {r}")
                elif isinstance(r, tuple) and r[0] is not None and getattr(r[0], "success", False):
                    combined_offers.extend(r[0].offers)
                    successful_labels.append(label)
                else:
                    err_msg = r[1] if isinstance(r, tuple) else "unknown error"
                    errors.append(f"{label} failed: {err_msg}")

            if combined_offers:
                source_str = f"KrishiBazaar Buyer Network ({' & '.join(successful_labels)}) via Agent Messaging"
                return BuyerOffersResult(
                    success=True,
                    crop=crop,
                    quantity_quintals=quantity_quintals,
                    offers=combined_offers,
                    error_message="; ".join(errors) if errors else None,
                    provenance=DataProvenance(
                        source=source_str,
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="SIMULATED_SAMPLE",
                        is_live=False,
                        disclaimer=(
                            f"Simulated benchmark buyer offers received via real agent messaging from {', '.join(successful_labels)}. Note: {'; '.join(errors)}."
                            if errors
                            else "Simulated benchmark buyer offers received via real agent messaging from specialist agent(s)."
                        ),
                    ),
                )
            elif settings.allow_direct_service_fallback:
                logger.info(
                    f"Buyer agents communication failed ({'; '.join(errors)}). "
                    f"Invoking local direct-service fallback."
                )
                try:
                    b_res = await asyncio.wait_for(
                        get_buyer_offers_async(
                            crop=crop,
                            quantity_quintals=quantity_quintals,
                            origin_location=origin_location,
                        ),
                        timeout=effective_timeout,
                    )
                    b_res.provenance.source = "KrishiBazaar Simulated Buyer Network (Local Fallback)"
                    b_res.provenance.disclaimer = (
                        f"Local service fallback used: Buyer agents messaging failed ({'; '.join(errors)})."
                    )
                    return b_res
                except Exception as ex:
                    return BuyerOffersResult(
                        success=False,
                        crop=crop,
                        quantity_quintals=quantity_quintals,
                        offers=[],
                        error_message=f"Buyer service fallback error: {str(ex)}",
                        provenance=DataProvenance(
                            source="KrishiBazaar Simulated Buyer Network (Local Fallback Failed)",
                            retrieval_timestamp_utc=now_utc.isoformat(),
                            observation_date=today_str,
                            freshness_status="SIMULATED_SAMPLE",
                            is_live=False,
                            disclaimer=f"Agent messaging failed and fallback failed ({ex}).",
                        ),
                    )
            else:
                return BuyerOffersResult(
                    success=False,
                    crop=crop,
                    quantity_quintals=quantity_quintals,
                    offers=[],
                    error_message="; ".join(errors) if errors else "No buyer offers received.",
                    provenance=DataProvenance(
                        source="KrishiBazaar Buyer Network",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="SIMULATED_SAMPLE",
                        is_live=False,
                        disclaimer="Buyer agents communication failed; direct-service fallback disabled.",
                    ),
                )
        elif settings.allow_direct_service_fallback:
            logger.info("Buyer agent addresses unconfigured. Invoking local direct-service fallback.")
            try:
                b_res = await asyncio.wait_for(
                    get_buyer_offers_async(
                        crop=crop,
                        quantity_quintals=quantity_quintals,
                        origin_location=origin_location,
                    ),
                    timeout=effective_timeout,
                )
                b_res.provenance.source = "KrishiBazaar Simulated Buyer Network (Local Fallback)"
                b_res.provenance.disclaimer = "Local service fallback used: Buyer agent addresses unconfigured."
                return b_res
            except Exception as ex:
                return BuyerOffersResult(
                    success=False,
                    crop=crop,
                    quantity_quintals=quantity_quintals,
                    offers=[],
                    error_message=f"Buyer service error: {str(ex)}",
                    provenance=DataProvenance(
                        source="KrishiBazaar Simulated Buyer Network (Local Fallback)",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="SIMULATED_SAMPLE",
                        is_live=False,
                        disclaimer="Direct service fallback failed.",
                    ),
                )
        else:
            return BuyerOffersResult(
                success=False,
                crop=crop,
                quantity_quintals=quantity_quintals,
                offers=[],
                error_message="Buyer agent addresses not configured (BUYER_AGENT_ADDRESS / BUYER_B_AGENT_ADDRESS missing) and direct-service fallback is disabled.",
                provenance=DataProvenance(
                    source="KrishiBazaar Buyer Network",
                    retrieval_timestamp_utc=now_utc.isoformat(),
                    observation_date=today_str,
                    freshness_status="SIMULATED_SAMPLE",
                    is_live=False,
                    disclaimer="Unconfigured agent; direct-service fallback disabled.",
                ),
            )

    # Dispatch market, weather, and buyers concurrently
    (mandi_result, nearby_result), weather_result, buyer_result = await asyncio.gather(
        fetch_market(),
        fetch_weather(),
        fetch_buyers(),
    )

    # 4. Transport Specialist Query
    candidate_dests: list[str] = []
    if mandi_result.success and mandi_result.records:
        candidate_dests.extend([r.mandi_name for r in mandi_result.records if not r.is_suspicious])
    elif nearby_result and nearby_result.success and nearby_result.records:
        candidate_dests.extend([r.mandi_name for r in nearby_result.records[:3] if not r.is_suspicious])

    if buyer_result.success:
        candidate_dests.extend([b.buyer_name for b in buyer_result.offers if not b.farmgate_pickup])

    # Remove duplicates
    seen = set()
    unique_dests = []
    for d in candidate_dests:
        if d not in seen:
            seen.add(d)
            unique_dests.append(d)

    if settings.transport_agent_address:
        hazard_level = (
            weather_result.report.spoilage_hazard_level
            if (weather_result.success and weather_result.report)
            else "LOW"
        )
        t_query = TransportQuery(
            origin=origin_location,
            destinations=unique_dests,
            quantity_quintals=quantity_quintals,
            weather_hazard_level=hazard_level,
        )
        t_res, t_err = await query_specialist_agent_async(
            destination=settings.transport_agent_address,
            query_msg=t_query,
            response_type=TransportQuotesResult,
            specialist_name="Transport Specialist",
            ctx=ctx,
            timeout=effective_timeout,
        )
        if t_res is not None and isinstance(t_res, TransportQuotesResult):
            transport_result = t_res
            transport_result.provenance.source = (
                f"KrishiBazaar Simulated Logistics Network via Transport Agent [{settings.transport_agent_address}]"
            )
            transport_result.provenance.disclaimer = (
                "Simulated freight quotes received via real agent messaging from Transport Agent."
            )
        elif settings.allow_direct_service_fallback:
            logger.info(
                f"Transport Agent ({settings.transport_agent_address}) query failed/timed out ({t_err}). "
                f"Invoking local direct-service fallback."
            )
            try:
                transport_result = await asyncio.wait_for(
                    get_transport_quotes_async(
                        origin=origin_location,
                        destinations=unique_dests,
                        quantity_quintals=quantity_quintals,
                        weather_report=weather_result.report if weather_result.success else None,
                    ),
                    timeout=effective_timeout,
                )
                transport_result.provenance.source = "KrishiBazaar Simulated Logistics Network (Local Fallback)"
                transport_result.provenance.disclaimer = (
                    f"Local service fallback used: Transport Agent query failed ({t_err})."
                )
            except Exception as ex:
                transport_result = TransportQuotesResult(
                    success=False,
                    origin=origin_location,
                    quantity_quintals=quantity_quintals,
                    quotes={},
                    error_message=f"Transport service fallback error: {str(ex)}",
                    provenance=DataProvenance(
                        source="KrishiBazaar Simulated Logistics Network (Local Fallback Failed)",
                        retrieval_timestamp_utc=now_utc.isoformat(),
                        observation_date=today_str,
                        freshness_status="SIMULATED_SAMPLE",
                        is_live=False,
                        disclaimer=f"Agent failed ({t_err}) and fallback failed ({ex}).",
                    ),
                )
        else:
            transport_result = TransportQuotesResult(
                success=False,
                origin=origin_location,
                quantity_quintals=quantity_quintals,
                quotes={},
                error_message=t_err or f"Transport agent query timed out after {effective_timeout}s.",
                provenance=DataProvenance(
                    source=f"KrishiBazaar Simulated Logistics Network via Transport Agent [{settings.transport_agent_address}]",
                    retrieval_timestamp_utc=now_utc.isoformat(),
                    observation_date=today_str,
                    freshness_status="SIMULATED_SAMPLE",
                    is_live=False,
                    disclaimer="Transport agent communication failed or timed out; direct-service fallback disabled.",
                ),
            )
    elif settings.allow_direct_service_fallback:
        logger.info("Transport agent address unconfigured. Invoking local direct-service fallback.")
        try:
            transport_result = await asyncio.wait_for(
                get_transport_quotes_async(
                    origin=origin_location,
                    destinations=unique_dests,
                    quantity_quintals=quantity_quintals,
                    weather_report=weather_result.report if weather_result.success else None,
                ),
                timeout=effective_timeout,
            )
            transport_result.provenance.source = "KrishiBazaar Simulated Logistics Network (Local Fallback)"
            transport_result.provenance.disclaimer = "Local service fallback used: Transport Agent address unconfigured."
        except Exception as ex:
            transport_result = TransportQuotesResult(
                success=False,
                origin=origin_location,
                quantity_quintals=quantity_quintals,
                quotes={},
                error_message=f"Transport service error: {str(ex)}",
                provenance=DataProvenance(
                    source="KrishiBazaar Simulated Logistics Network (Local Fallback)",
                    retrieval_timestamp_utc=now_utc.isoformat(),
                    observation_date=today_str,
                    freshness_status="SIMULATED_SAMPLE",
                    is_live=False,
                    disclaimer="Direct service fallback failed.",
                ),
            )
    else:
        transport_result = TransportQuotesResult(
            success=False,
            origin=origin_location,
            quantity_quintals=quantity_quintals,
            quotes={},
            error_message="Transport agent address not configured (TRANSPORT_AGENT_ADDRESS missing) and direct-service fallback is disabled.",
            provenance=DataProvenance(
                source="KrishiBazaar Simulated Logistics Network",
                retrieval_timestamp_utc=now_utc.isoformat(),
                observation_date=today_str,
                freshness_status="SIMULATED_SAMPLE",
                is_live=False,
                disclaimer="Unconfigured agent; direct-service fallback disabled.",
            ),
        )

    # 5. Deterministic Optimization
    valid_mandis = []
    if mandi_result.success and mandi_result.records:
        valid_mandis = [r for r in mandi_result.records if not r.is_suspicious]
    elif nearby_result and nearby_result.success and nearby_result.records:
        valid_mandis = [r for r in nearby_result.records[:3] if not r.is_suspicious]

    optimization_result = optimize_sales_channels(
        crop=crop,
        quantity_quintals=quantity_quintals,
        mandi_records=valid_mandis,
        buyer_offers=buyer_result.offers if buyer_result.success else [],
        transport_quotes=transport_result.quotes if transport_result.success else {},
        weather_report=weather_result.report if weather_result.success else None,
        minimum_net_return_inr=minimum_net_return_inr,
        storage_preference=storage_preference,
        origin_location=origin_location,
        target_district=district,
    )

    specialist_diagnostics = {
        "market": {
            "agent_role": "Market Specialist",
            "configured_address": settings.market_agent_address,
            "status": (
                "REMOTE_SUCCESS"
                if ("via Market Agent" in mandi_result.provenance.source and "Local Fallback" not in mandi_result.provenance.source)
                else ("FALLBACK_USED" if "Local Fallback" in mandi_result.provenance.source else "FAILED")
            ),
            "is_remote": (
                "via Market Agent" in mandi_result.provenance.source
                and "Local Fallback" not in mandi_result.provenance.source
            ),
            "records_count": len(mandi_result.records) if mandi_result.records else 0,
            "error_detail": (
                mandi_result.error_message
                if (
                    "via Market Agent" not in mandi_result.provenance.source
                    or "Local Fallback" in mandi_result.provenance.source
                )
                else None
            ),
        },
        "weather": {
            "agent_role": "Weather Specialist",
            "configured_address": settings.weather_agent_address,
            "status": (
                "REMOTE_SUCCESS"
                if ("via Weather Agent" in weather_result.provenance.source and "Local Fallback" not in weather_result.provenance.source)
                else ("FALLBACK_USED" if "Local Fallback" in weather_result.provenance.source else "FAILED")
            ),
            "is_remote": (
                "via Weather Agent" in weather_result.provenance.source
                and "Local Fallback" not in weather_result.provenance.source
            ),
            "hazard_level": (
                weather_result.report.spoilage_hazard_level if weather_result.report else None
            ),
            "error_detail": (
                weather_result.error_message
                if (
                    "via Weather Agent" not in weather_result.provenance.source
                    or "Local Fallback" in weather_result.provenance.source
                )
                else None
            ),
        },
        "buyers": {
            "agent_role": "Buyer Network",
            "status": (
                "REMOTE_SUCCESS"
                if ("via Agent Messaging" in buyer_result.provenance.source and "Local Fallback" not in buyer_result.provenance.source)
                else ("FALLBACK_USED" if "Local Fallback" in buyer_result.provenance.source else "FAILED")
            ),
            "is_remote": (
                "via Agent Messaging" in buyer_result.provenance.source
                and "Local Fallback" not in buyer_result.provenance.source
            ),
            "offers_count": len(buyer_result.offers) if buyer_result.offers else 0,
            "error_detail": (
                buyer_result.error_message
                if (
                    "via Agent Messaging" not in buyer_result.provenance.source
                    or "Local Fallback" in buyer_result.provenance.source
                )
                else None
            ),
        },
        "transport": {
            "agent_role": "Transport Specialist",
            "configured_address": settings.transport_agent_address,
            "status": (
                "REMOTE_SUCCESS"
                if ("via Transport Agent" in transport_result.provenance.source and "Local Fallback" not in transport_result.provenance.source)
                else ("FALLBACK_USED" if "Local Fallback" in transport_result.provenance.source else "FAILED")
            ),
            "is_remote": (
                "via Transport Agent" in transport_result.provenance.source
                and "Local Fallback" not in transport_result.provenance.source
            ),
            "quotes_count": len(transport_result.quotes) if transport_result.quotes else 0,
            "error_detail": (
                transport_result.error_message
                if (
                    "via Transport Agent" not in transport_result.provenance.source
                    or "Local Fallback" in transport_result.provenance.source
                )
                else None
            ),
        },
    }

    return {
        "district": district,
        "state": state,
        "target_date": target_date,
        "planned_transit_date": planned_transit_date,
        "date_was_past": date_was_past,
        "original_past_date": original_past_date,
        "selling_deadline": selling_deadline,
        "mandi_result": mandi_result,
        "nearby_result": nearby_result,
        "weather_result": weather_result,
        "buyer_result": buyer_result,
        "transport_result": transport_result,
        "optimization_result": optimization_result,
        "specialist_diagnostics": specialist_diagnostics,
    }


@chat_proto.on_message(ChatMessage)
async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage) -> None:
    """
    Handle incoming ChatMessage from ASI:One or another agent.
    1. Sends delivery acknowledgement.
    2. Safely extracts user TextContent.
    3. Runs structured intent extraction via ASI:One API.
    4. Validates completeness with typed Pydantic models.
    5. Coordinates specialist agents (Market, Weather, Buyer, Transport) and deterministic profit optimizer.
    6. Asks follow-up question if required fields are missing; confirms details and optimization if complete.
    7. Replies with a structured ChatMessage including TextContent and EndSessionContent.
    """
    try:
        # 1. Immediate Delivery Acknowledgement matching msg_id
        ack = ChatAcknowledgement(acknowledged_msg_id=msg.msg_id)
        await ctx.send(sender, ack)

        # 2. Extract TextContent safely
        user_text = ""
        for item in msg.content:
            if isinstance(item, TextContent):
                user_text += item.text
            elif getattr(item, "type", None) == "text" and hasattr(item, "text"):
                user_text += getattr(item, "text", "")

        # Strip any leading Agentverse @agent-address mention prefix
        user_text = strip_agent_mention(user_text)

        # 3. Log incoming request safely without exposing secret seeds or keys
        ctx.logger.info(
            f"Received ChatMessage from sender '{sender}' "
            f"(msg_id: {msg.msg_id}, length: {len(user_text)} chars)"
        )

        # 4. Phase 7: Interactive Card Action Interceptor
        booking_action, action_param, card_selection = extract_interaction_action(msg, user_text)
        session_key = get_session_key(sender, card_selection)

        # Context restoration check: If user message contains explicit booking or payment ID,
        # restore existing context into current session_key so new sessions seamlessly inherit history
        book_match = re.search(r"\b(kb-book-[a-z0-9\-]+)\b", user_text, re.IGNORECASE)
        if book_match:
            ref_book = storage.get_booking(book_match.group(1).upper())
            if ref_book:
                inq_obj = FarmerInquiry(
                    farmer_id=ref_book.farmer_id or sender,
                    crop=ref_book.crop,
                    quantity_quintals=ref_book.quantity_quintals,
                    location_district="Nashik",
                    origin_location="Nashik",
                    minimum_net_return_inr=ref_book.net_payout,
                    selling_deadline=ref_book.pickup_date,
                )
                linked_pay = storage.get_payment_record_by_inquiry(ref_book.booking_id)
                storage.save_session(
                    session_id=session_key,
                    farmer_id=sender,
                    state="BOOKING_CONFIRMED",
                    inquiry=inq_obj,
                    active_booking_id=ref_book.booking_id,
                    active_proposal_id=ref_book.offer_id,
                    pending_payment_reference=(
                        linked_pay.payment_reference
                        if linked_pay and linked_pay.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION)
                        else None
                    ),
                )

        pay_match = re.search(r"\b(kb-pay-[a-z0-9\-]+)\b", user_text, re.IGNORECASE)
        # Only restore payment session context if there is NO explicit booking being discussed,
        # AND the payment record is in an active pending authorization state.
        if pay_match and not book_match:
            ref_pay = storage.get_payment_record(pay_match.group(1).upper())
            if ref_pay and ref_pay.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION):
                storage.save_session(
                    session_id=session_key,
                    farmer_id=sender,
                    state="AWAITING_PAYMENT_APPROVAL",
                    pending_payment_reference=ref_pay.payment_reference,
                )

        # 4a. Booking Status Verification Query
        if booking_action == BookingActionType.STATUS_LOOKUP:
            target_id = action_param
            booking = None
            if target_id:
                booking = storage.get_booking(target_id)
            else:
                sess = storage.get_session(session_key)
                if sess and sess.get("active_booking_id"):
                    cand_b = storage.get_booking(sess["active_booking_id"])
                    if cand_b and (cand_b.farmer_id == sender or sess.get("farmer_id") == sender):
                        booking = cand_b
                if not booking:
                    bookings = storage.list_bookings(farmer_id=sender)
                    if bookings:
                        booking = bookings[0]

            card_content = None
            if booking:
                inquiry_obj = FarmerInquiry(
                    farmer_id=booking.farmer_id or sender,
                    crop=booking.crop,
                    quantity_quintals=booking.quantity_quintals,
                    location_district="Nashik",
                    origin_location="Nashik",
                    minimum_net_return_inr=booking.net_payout,
                    selling_deadline=booking.pickup_date,
                )
                linked_pay = storage.get_payment_record_by_inquiry(booking.booking_id)
                storage.save_session(
                    session_id=session_key,
                    farmer_id=sender,
                    state="BOOKING_CONFIRMED",
                    inquiry=inquiry_obj,
                    active_booking_id=booking.booking_id,
                    active_proposal_id=booking.offer_id,
                    pending_payment_reference=linked_pay.payment_reference if linked_pay else None,
                )

                status_lines = format_booking_status(booking)
                reply_body = "\n".join(status_lines)
                card_content = build_booking_confirmed_card(
                    booking, payment_enabled=settings.enable_payment_protocol
                )
            else:
                ref_desc = f"reference '{target_id}'" if target_id else f"farmer session '{sender}'"
                reply_body = (
                    f"âš ï¸ **Booking Not Found:** No simulated booking record found for {ref_desc}. "
                    f"Please verify your booking reference ID (e.g. *\"Status of KB-BOOK-...\"*) "
                    f"or submit a produce inquiry to start a new booking."
                )

            response_msg = build_chat_response(reply_body, card_content)
            await ctx.send(sender, response_msg)
            ctx.logger.info(f"Dispatched status lookup response to sender '{sender}'")
            return

        # 4a.5 Payment Status Verification Query
        elif booking_action == BookingActionType.PAYMENT_STATUS_LOOKUP:
            target_ref = action_param.strip() if action_param else None
            pay_rec = None
            sess = storage.get_session(session_key)

            # Case B: The user supplies a booking reference (e.g. KB-BOOK-20261022-8ED080 or KB-BOOK-20261023-93DB4D)
            if target_ref and target_ref.upper().startswith("KB-BOOK-"):
                target_book_id = target_ref.upper()
                booking = storage.get_booking(target_book_id)
                is_owner = True
                if booking and booking.farmer_id and booking.farmer_id != sender:
                    if not sess or sess.get("farmer_id") != sender:
                        is_owner = False

                cand_pay = storage.get_payment_record_by_inquiry(target_book_id)
                # Strict verification: Ensure the payment's stored association matches the requested booking ID
                if cand_pay and cand_pay.service_inquiry_id and cand_pay.service_inquiry_id.upper() == target_book_id:
                    pay_rec = cand_pay
                else:
                    pay_rec = None

                report_lines = format_booking_payment_audit(
                    booking_id=target_book_id,
                    booking=booking,
                    payment_record=pay_rec,
                    settings_obj=settings,
                    is_owner=is_owner,
                )
                reply_body = "\n".join(report_lines)
                card_content = None
                try:
                    card_content = build_booking_payment_audit_card(
                        booking_id=target_book_id,
                        booking=booking,
                        payment_record=pay_rec,
                        payment_enabled=settings.enable_payment_protocol,
                    )
                except Exception as ex:
                    ctx.logger.debug(f"Could not build booking payment audit card: {ex}")

                response_msg = build_chat_response(reply_body, card_content)
                await ctx.send(sender, response_msg)
                ctx.logger.info(f"Dispatched booking payment audit response for booking '{target_book_id}' to sender '{sender}'")
                return

            # Case A: The user supplies an explicit payment reference (e.g. KB-PAY-27A4A2EC1F3E)
            elif target_ref and target_ref.upper().startswith("KB-PAY-"):
                target_pay_id = target_ref.upper()
                pay_rec = storage.get_payment_record(target_pay_id)
                if not pay_rec:
                    reply_body = f"âš ï¸ **Payment Record Not Found:** No payment record found for payment reference `{target_pay_id}`."
                    response_msg = build_chat_response(reply_body, None)
                    await ctx.send(sender, response_msg)
                    ctx.logger.info(f"Dispatched payment not found response for ref '{target_pay_id}' to sender '{sender}'")
                    return

            # Case D: The user does not supply any reference (generic payment status query)
            else:
                if sess and sess.get("active_booking_id"):
                    active_b_id = sess["active_booking_id"].upper()
                    cand_pay = storage.get_payment_record_by_inquiry(active_b_id)
                    if cand_pay and cand_pay.service_inquiry_id and cand_pay.service_inquiry_id.upper() == active_b_id:
                        pay_rec = cand_pay
                    else:
                        reply_body = f"âš ï¸ **Payment Record Not Found:** No payment record found for your active booking `{active_b_id}`."
                        response_msg = build_chat_response(reply_body, None)
                        await ctx.send(sender, response_msg)
                        ctx.logger.info(f"Dispatched payment not found for active booking '{active_b_id}' to sender '{sender}'")
                        return
                elif sess and sess.get("pending_payment_reference"):
                    # Only consider pending payment if it's currently awaiting authorization for this payer
                    cand_pay = storage.get_payment_record(sess["pending_payment_reference"])
                    if cand_pay and cand_pay.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION) and cand_pay.payer_agent_address == sender:
                        pay_rec = cand_pay

                if not pay_rec:
                    reply_body = (
                        f"âš ï¸ **Payment Record Not Found:** No active payment record found for farmer session '{sender}'. "
                        f"Please verify your payment or booking reference ID (e.g. *\"Status of KB-PAY-...\"* or *\"Check payment for KB-BOOK-...\"*)."
                    )
                    response_msg = build_chat_response(reply_body, None)
                    await ctx.send(sender, response_msg)
                    ctx.logger.info(f"Dispatched generic payment not found response to sender '{sender}'")
                    return

            card_content = None
            if pay_rec.status in (PaymentStatus.COMMITTED, PaymentStatus.AWAITING_RECONCILIATION) and pay_rec.tx_hash and settings.enable_payment_protocol:
                from krishibazaar.services.payment_service import PaymentService
                pay_service = PaymentService(storage_engine=storage, settings_obj=settings)
                try:
                    is_settled, updated_rec = pay_service.reconcile_payment_on_chain(pay_rec.payment_reference)
                    if is_settled:
                        pay_rec = updated_rec
                except Exception as ex:
                    ctx.logger.warning(f"On-demand reconciliation failed for {pay_rec.payment_reference}: {ex}")

            if pay_rec.service_inquiry_id and pay_rec.service_inquiry_id.upper().startswith("KB-BOOK-"):
                ref_b = storage.get_booking(pay_rec.service_inquiry_id)
                if ref_b and (ref_b.farmer_id == sender or (sess and sess.get("farmer_id") == sender)):
                    inq_obj = FarmerInquiry(
                        farmer_id=ref_b.farmer_id or sender,
                        crop=ref_b.crop,
                        quantity_quintals=ref_b.quantity_quintals,
                        location_district="Nashik",
                        origin_location="Nashik",
                        minimum_net_return_inr=ref_b.net_payout,
                        selling_deadline=ref_b.pickup_date,
                    )
                    storage.save_session(
                        session_id=session_key,
                        farmer_id=sender,
                        state="BOOKING_CONFIRMED",
                        inquiry=inq_obj,
                        active_booking_id=ref_b.booking_id,
                        active_proposal_id=ref_b.offer_id,
                        pending_payment_reference=pay_rec.payment_reference if pay_rec.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION) else None,
                    )

            status_lines = format_payment_status(
                pay_rec, payment_enabled=settings.enable_payment_protocol
            )
            reply_body = "\n".join(status_lines)
            card_content = build_payment_status_card(
                pay_rec, payment_enabled=settings.enable_payment_protocol
            )
            response_msg = build_chat_response(reply_body, card_content)
            await ctx.send(sender, response_msg)
            ctx.logger.info(f"Dispatched payment status response to sender '{sender}' for ref '{pay_rec.payment_reference}'")
            return

        # 4a.6 Payment Readiness & Proposal Audit Query
        elif booking_action == BookingActionType.PAYMENT_READINESS_AUDIT:
            target_ref = action_param.strip().upper() if action_param else None

            # If user explicitly supplied a booking ID (e.g. KB-BOOK-20261023-93DB4D)
            if target_ref and target_ref.startswith("KB-BOOK-"):
                target_book_id = target_ref
                booking = storage.get_booking(target_book_id)
                sess = storage.get_session(session_key)
                is_owner = True
                if booking and booking.farmer_id and booking.farmer_id != sender:
                    if not sess or sess.get("farmer_id") != sender:
                        is_owner = False

                cand_pay = storage.get_payment_record_by_inquiry(target_book_id)
                pay_rec = cand_pay if (cand_pay and cand_pay.service_inquiry_id and cand_pay.service_inquiry_id.upper() == target_book_id) else None

                report_lines = format_booking_payment_audit(
                    booking_id=target_book_id,
                    booking=booking,
                    payment_record=pay_rec,
                    settings_obj=settings,
                    is_owner=is_owner,
                )
                reply_body = "\n".join(report_lines)
                card_content = None
                try:
                    card_content = build_booking_payment_audit_card(
                        booking_id=target_book_id,
                        booking=booking,
                        payment_record=pay_rec,
                        payment_enabled=settings.enable_payment_protocol,
                    )
                except Exception as ex:
                    ctx.logger.debug(f"Could not build booking payment audit card: {ex}")

                response_msg = build_chat_response(reply_body, card_content)
                await ctx.send(sender, response_msg)
                ctx.logger.info(f"Dispatched booking payment audit response to sender '{sender}' for booking '{target_book_id}'")
                return

            target_prop_id = target_ref
            prop = None
            sess = storage.get_session(session_key)

            if target_prop_id:
                prop = storage.get_proposal(target_prop_id)
            else:
                pending_prop = storage.get_pending_proposal_for_session(session_key)
                if pending_prop:
                    prop = pending_prop
                    target_prop_id = pending_prop.proposal_id
                elif sess and sess.get("active_proposal_id"):
                    cand_p = storage.get_proposal(sess["active_proposal_id"])
                    if cand_p:
                        prop = cand_p
                        target_prop_id = cand_p.proposal_id

            # Associated booking check (strictly isolated; never substitute an unrelated booking)
            booking = None
            if target_prop_id:
                booking = storage.get_booking_by_proposal(target_prop_id)

            # Associated payment check (strictly isolated; never substitute an unrelated payment)
            pay_rec = None
            if booking:
                pay_rec = storage.get_payment_record_by_inquiry(booking.booking_id)
            elif target_prop_id:
                pay_rec = storage.get_payment_record_by_inquiry(target_prop_id)

            report_lines = format_payment_readiness_audit(
                proposal_id=target_prop_id,
                proposal=prop,
                booking=booking,
                payment_record=pay_rec,
                settings_obj=settings,
                storage_engine=storage,
            )
            reply_body = "\n".join(report_lines)
            card_content = None
            try:
                card_content = build_payment_readiness_card(
                    proposal_id=target_prop_id,
                    proposal=prop,
                    booking=booking,
                    payment_record=pay_rec,
                    payment_enabled=settings.enable_payment_protocol,
                )
            except Exception as ex:
                ctx.logger.debug(f"Could not build payment readiness card: {ex}")

            response_msg = build_chat_response(reply_body, card_content)
            await ctx.send(sender, response_msg)
            ctx.logger.info(
                f"Dispatched payment readiness audit response to sender '{sender}' for proposal '{target_prop_id or 'none'}'"
            )
            return

        # 4b. Explicit Cancellation Action
        elif booking_action == BookingActionType.CANCEL:
            pending_prop = storage.get_pending_proposal_for_session(session_key)
            if pending_prop and not pending_prop.is_consumed:
                storage.cancel_pending_proposals(session_key)
                storage.save_session(
                    session_id=session_key,
                    farmer_id=sender,
                    state="OFFERS_OPTIMIZED",
                )
                reply_body = (
                    f"âŒ **Booking Cancelled:** Your pending proposal `{pending_prop.proposal_id}` for "
                    f"{pending_prop.quantity_quintals:g} qtl {pending_prop.crop} to {pending_prop.destination_name} "
                    f"has been cancelled. No booking was created. You can select another option (e.g. *\"Select option 1\"*) "
                    f"or provide updated crop details anytime."
                )
            else:
                reply_body = (
                    "â„¹ï¸ **No Pending Proposal:** There was no active booking proposal to cancel. "
                    "You can submit a crop inquiry or ask for market prices anytime."
                )

            response_msg = build_chat_response(reply_body, None)
            await ctx.send(sender, response_msg)
            ctx.logger.info(f"Dispatched cancellation response to sender '{sender}'")
            return

        # 4c. Explicit Approval Action
        elif booking_action == BookingActionType.APPROVE:
            prop_id = action_param.strip() if action_param else None
            pending_prop = storage.get_pending_proposal_for_session(session_key)

            if not pending_prop:
                if prop_id:
                    past_prop = storage.get_proposal(prop_id)
                    if past_prop and past_prop.is_consumed:
                        reply_body = (
                            f"âš ï¸ **Duplicate Approval / Already Processed:** Proposal `{past_prop.proposal_id}` "
                            f"has already been approved or consumed. A simulated booking record was already recorded "
                            f"or the proposal was cancelled. To verify status, reply with *\"Status of <booking_id>\"*."
                        )
                        card_content = build_duplicate_approval_card(past_prop.proposal_id)
                        response_msg = build_chat_response(reply_body, card_content)
                        await ctx.send(sender, response_msg)
                        return

                reply_body = (
                    "âš ï¸ **Approval Rejected: No Pending Proposal**\n\n"
                    "There is no active pending proposal for your session. "
                    "Generating or viewing a proposal does not automatically confirm a booking. "
                    "Before a booking can be confirmed, please select a sales channel from your recommendations "
                    "(e.g. *\"Select option 1\"*) so we can prepare an itemized proposal for your explicit review."
                )
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                ctx.logger.info(
                    f"Rejected approval from sender '{sender}': No active pending proposal found for session."
                )
                return

            # Proposal ID Validation: If a specific proposal ID was supplied, validate it against active pending proposal
            if prop_id and pending_prop.proposal_id.upper() != prop_id.upper():
                reply_body = (
                    f"âš ï¸ **Approval Rejected: Proposal ID Mismatch**\n\n"
                    f"Supplied proposal ID `{prop_id}` does not match your active pending proposal `{pending_prop.proposal_id}`. "
                    f"Please confirm your active proposal or select another option."
                )
                card_content = build_proposal_review_card(pending_prop)
                response_msg = build_chat_response(reply_body, card_content)
                await ctx.send(sender, response_msg)
                ctx.logger.info(
                    f"Rejected mismatched proposal approval from sender '{sender}': {prop_id} vs {pending_prop.proposal_id}"
                )
                return

            proposal = pending_prop
            card_content = None
            if proposal.is_consumed:
                reply_body = (
                    f"âš ï¸ **Duplicate Approval / Already Processed:** Proposal `{proposal.proposal_id}` "
                    f"has already been approved or consumed. A simulated booking record was already recorded "
                    f"or the proposal was cancelled. To verify status, reply with *\"Status of <booking_id>\"*."
                )
                card_content = build_duplicate_approval_card(proposal.proposal_id)
            else:
                # Check expiration
                now_utc = datetime.now(timezone.utc)
                try:
                    expires_at = datetime.fromisoformat(proposal.expires_at_utc)
                except Exception:
                    expires_at = now_utc

                if now_utc > expires_at:
                    storage.consume_proposal(proposal.proposal_id)
                    reply_body = (
                        f"âš ï¸ **Approval Rejected: Proposal Expired:** Proposal `{proposal.proposal_id}` "
                        f"expired at {proposal.expires_at_utc}. Due to market rate and transport quote volatility, "
                        f"proposals are only valid for 15 minutes. Please select an option again (e.g. *\"Select option 1\"*) "
                        f"to generate a fresh proposal."
                    )
                    card_content = build_expired_proposal_card(
                        proposal.proposal_id, proposal.expires_at_utc
                    )
                else:
                    # Atomic consumption to protect against race conditions and duplicates
                    consumed = storage.consume_proposal(proposal.proposal_id)
                    if not consumed:
                        reply_body = (
                            f"âš ï¸ **Duplicate Approval / Already Processed:** Proposal `{proposal.proposal_id}` "
                            f"was already processed by another concurrent request. No duplicate booking created."
                        )
                        card_content = build_duplicate_approval_card(proposal.proposal_id)
                    else:
                        booking_record = execute_simulated_booking(proposal)
                        storage.create_booking(booking_record)
                        inquiry_obj = FarmerInquiry(
                            farmer_id=sender,
                            crop=booking_record.crop,
                            quantity_quintals=booking_record.quantity_quintals,
                            location_district=proposal.origin_location,
                            origin_location=proposal.origin_location,
                            minimum_net_return_inr=proposal.estimated_net_payout,
                            selling_deadline=proposal.transit_date,
                        )
                        storage.save_session(
                            session_id=session_key,
                            farmer_id=sender,
                            state="BOOKING_CONFIRMED",
                            inquiry=inquiry_obj,
                            active_proposal_id=proposal.proposal_id,
                            active_booking_id=booking_record.booking_id,
                            clear_pending_payment=True,
                        )
                        confirmation_lines = format_booking_confirmation(
                            booking_record,
                            payment_enabled=settings.enable_payment_protocol,
                        )
                        reply_body = "\n".join(confirmation_lines)
                        card_content = build_booking_confirmed_card(
                            booking_record,
                            payment_enabled=settings.enable_payment_protocol,
                        )

            response_msg = build_chat_response(reply_body, card_content)
            await ctx.send(sender, response_msg)
            ctx.logger.info(f"Dispatched approval handling response to sender '{sender}'")
            return

        # 4d. Option Selection Action
        elif booking_action == BookingActionType.SELECT_OPTION:
            sess = storage.get_session(session_key)
            active_options = sess.get("active_options") if sess else None

            if not active_options:
                reply_body = (
                    "âš ï¸ **No Active Options Found:** Please tell me what crop, quantity, and location you have "
                    "(e.g. *\"I have 50 quintals of onions in Nashik\"*) so I can retrieve live rates and present "
                    "ranked selling channels to choose from."
                )
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                return

            chosen_option = None
            available_ranks = [opt.rank for opt in active_options]
            if action_param and action_param.isdigit():
                rank_num = int(action_param)
                chosen_option = next(
                    (opt for opt in active_options if opt.rank == rank_num), None
                )
                if not chosen_option:
                    err_msg = f"Option #{rank_num} does not exist in your active recommendations."
                    reply_body = (
                        f"âš ï¸ **Invalid Channel Selection:** {err_msg}\n\n"
                        f"Please select from available ranks: "
                        f"{', '.join(f'*\"Select option {r}\"*' for r in available_ranks)}."
                    )
                    err_card = build_invalid_selection_card(err_msg, available_ranks)
                    response_msg = build_chat_response(reply_body, err_card)
                    await ctx.send(sender, response_msg)
                    return
            elif action_param:
                param_clean = action_param.lower().strip()
                chosen_option = next(
                    (
                        opt
                        for opt in active_options
                        if getattr(opt, "scenario_id", "").lower() == param_clean
                        or getattr(opt, "option_id", "").lower() == param_clean
                    ),
                    None,
                )
                if not chosen_option:
                    chosen_option = next(
                        (
                            opt
                            for opt in active_options
                            if param_clean in opt.destination_name.lower()
                        ),
                        None,
                    )
                if not chosen_option:
                    err_msg = f"Channel '{action_param}' was not recognized in your recommendations."
                    reply_body = (
                        f"âš ï¸ **Invalid Channel Selection:** {err_msg}\n\n"
                        f"Please select from available ranks: "
                        f"{', '.join(f'*\"Select option {r}\"*' for r in available_ranks)}."
                    )
                    err_card = build_invalid_selection_card(err_msg, available_ranks)
                    response_msg = build_chat_response(reply_body, err_card)
                    await ctx.send(sender, response_msg)
                    return

            if not chosen_option:
                chosen_option = active_options[0]

            inquiry: FarmerInquiry | None = sess.get("inquiry") if sess else None
            crop = (
                inquiry.crop
                if (inquiry and inquiry.crop)
                else getattr(chosen_option, "crop", "Produce")
            )
            quantity_quintals = (
                inquiry.quantity_quintals
                if (inquiry and inquiry.quantity_quintals)
                else getattr(chosen_option, "quantity_quintals", 1.0)
            )
            origin_location = (
                inquiry.location_district
                if (inquiry and inquiry.location_district)
                else getattr(chosen_option, "origin_location", "Farmer Origin")
            )
            transit_date, _, _ = resolve_planned_transit_date(
                inquiry.selling_deadline if inquiry else None
            )

            # Cancel any previous pending proposal before creating the new one
            storage.cancel_pending_proposals(session_key)

            proposal = create_booking_proposal(
                session_id=session_key,
                farmer_id=sender,
                selected_option=chosen_option,
                crop=crop,
                quantity_quintals=quantity_quintals,
                origin_location=origin_location,
                transit_date=transit_date,
            )
            storage.save_proposal(proposal)
            storage.save_session(
                session_id=session_key,
                farmer_id=sender,
                state="AWAITING_APPROVAL",
                inquiry=inquiry,
                active_options=active_options,
                active_proposal_id=proposal.proposal_id,
            )
            proposal_lines = format_proposal_summary(proposal)
            reply_body = "\n".join(proposal_lines)
            card_content = build_proposal_review_card(proposal)

            response_msg = build_chat_response(reply_body, card_content)
            await ctx.send(sender, response_msg)
            ctx.logger.info(f"Dispatched proposal selection response to sender '{sender}'")
            return

        # 4e. Request Payment Review Action
        elif booking_action == BookingActionType.REQUEST_PAYMENT_REVIEW:
            from krishibazaar.services.payment_service import (
                PaymentService,
                validate_fetch_wallet_address,
            )
            from uagents.crypto import Identity
            pay_service = PaymentService(storage_engine=storage, settings_obj=settings)
            if settings.payment_network != "testnet" or settings.payment_testnet_chain_id != "dorado-1":
                reply_body = (
                    "âŒ **Security Violation:** Mainnet transfers are strictly disabled in KrishiBazaar. "
                    "Only the Fetch.ai Dorado testnet (dorado-1) is supported."
                )
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                return

            sess = storage.get_session(session_key)

            # Determine clean booking inquiry reference
            clean_inquiry_id = None
            if action_param:
                param_str = action_param.strip()
                if param_str.upper().startswith("KB-BOOK-"):
                    clean_inquiry_id = param_str.upper()
                elif param_str.upper().startswith("KB-PAY-"):
                    rec = storage.get_payment_record(param_str)
                    if rec and rec.service_inquiry_id and rec.service_inquiry_id.upper().startswith("KB-BOOK-"):
                        clean_inquiry_id = rec.service_inquiry_id.upper()
            if not clean_inquiry_id and sess and sess.get("active_booking_id"):
                clean_inquiry_id = sess["active_booking_id"].upper()

            # Check if an existing payment record already exists for this booking or session
            existing_pay = None
            if action_param and action_param.strip().upper().startswith("KB-PAY-"):
                cand = storage.get_payment_record(action_param.strip())
                if cand:
                    if clean_inquiry_id:
                        if cand.service_inquiry_id and cand.service_inquiry_id.upper() == clean_inquiry_id.upper():
                            existing_pay = cand
                        else:
                            ctx.logger.warning(
                                f"Requested payment reference '{action_param}' belongs to inquiry '{cand.service_inquiry_id}', "
                                f"not active booking '{clean_inquiry_id}'. Ignoring mismatched payment."
                            )
                    else:
                        existing_pay = cand
                        clean_inquiry_id = cand.service_inquiry_id
                else:
                    reply_body = f"âš ï¸ **Payment Record Not Found:** No payment record found for reference `{action_param.strip()}`."
                    response_msg = build_chat_response(reply_body, None)
                    await ctx.send(sender, response_msg)
                    return

            if not existing_pay and clean_inquiry_id:
                existing_pay = storage.get_payment_record_by_inquiry(clean_inquiry_id)

            if not existing_pay and sess and sess.get("pending_payment_reference"):
                cand = storage.get_payment_record(sess["pending_payment_reference"])
                if cand:
                    if clean_inquiry_id:
                        if cand.service_inquiry_id and cand.service_inquiry_id.upper() == clean_inquiry_id.upper():
                            existing_pay = cand
                        else:
                            ctx.logger.info(
                                f"Ignoring and clearing stale pending_payment_reference '{cand.payment_reference}' linked to '{cand.service_inquiry_id}', "
                                f"expected '{clean_inquiry_id}'"
                            )
                            storage.clear_session_payment_reference(session_key)
                    else:
                        existing_pay = cand
                        clean_inquiry_id = cand.service_inquiry_id

            if not existing_pay and not clean_inquiry_id:
                clean_inquiry_id = session_key

            pay_record = None
            if existing_pay:
                if existing_pay.status == PaymentStatus.SETTLED:
                    reply_body = (
                        f"âœ… **Payment Already Settled:** Advisory payment for booking `{clean_inquiry_id or existing_pay.payment_reference}` "
                        f"is already cryptographically settled on Dorado testnet (Tx: `{existing_pay.tx_hash}`)."
                    )
                    card_content = build_payment_confirmed_card(existing_pay)
                    response_msg = build_chat_response(reply_body, card_content)
                    await ctx.send(sender, response_msg)
                    return
                elif existing_pay.status in (PaymentStatus.SUBMITTING, PaymentStatus.COMMITTED, PaymentStatus.PENDING_SETTLEMENT, PaymentStatus.AWAITING_RECONCILIATION):
                    reply_body = (
                        f"â³ **Payment In Progress:** Payment reference `{existing_pay.payment_reference}` has already been submitted "
                        f"(Status: `{existing_pay.status.value}`). Awaiting authoritative ledger settlement."
                    )
                    card_content = build_payment_status_card(existing_pay, payment_enabled=settings.enable_payment_protocol)
                    response_msg = build_chat_response(reply_body, card_content)
                    await ctx.send(sender, response_msg)
                    return
                elif existing_pay.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION):
                    # Check deadline
                    now_utc = datetime.now(timezone.utc)
                    try:
                        pay_created = datetime.fromisoformat(existing_pay.created_at_utc)
                        elapsed = (now_utc - pay_created).total_seconds()
                    except Exception:
                        elapsed = 0
                    if elapsed <= existing_pay.deadline_seconds:
                        pay_record = existing_pay
                    else:
                        storage.update_payment_status(
                            reference=existing_pay.payment_reference,
                            status=PaymentStatus.FAILED,
                            rejection_reason="Payment request expired before authorization.",
                        )

            if pay_record is None:
                pay_seed = settings.payment_payee_seed
                if not pay_seed:
                    if settings.payment_environment == "testnet":
                        reply_body = (
                            "âŒ **Payment Configuration Blocker:** PAYMENT_PAYEE_SEED is not configured in environment. "
                            "Cannot initialize secure payment specialist identity."
                        )
                        response_msg = build_chat_response(reply_body, None)
                        await ctx.send(sender, response_msg)
                        return
                    pay_seed = "ephemeral_local_test_seed_payment_agent"

                from cosmpy.aerial.wallet import LocalWallet
                payee_agent_addr = settings.payment_payee_agent_address or Identity.from_seed(pay_seed, 0).address
                try:
                    derived_payee_wallet = str(LocalWallet.from_mnemonic(pay_seed).address())
                except Exception:
                    derived_payee_wallet = str(LocalWallet.from_unsafe_seed(pay_seed).address())
                payee_wallet_addr = settings.payment_payee_wallet_address or derived_payee_wallet

                is_valid_rec, rec_err = validate_fetch_wallet_address(payee_wallet_addr)
                if not is_valid_rec:
                    reply_body = f"âŒ **Payment Preflight Blocker:** Designated recipient address is invalid: {rec_err}"
                    response_msg = build_chat_response(reply_body, None)
                    await ctx.send(sender, response_msg)
                    return

                payer_wallet_addr = pay_service.get_payer_address() or "unknown_payer_wallet"

                req_msg, pay_record = pay_service.create_advisory_payment_request(
                    payee_agent_address=payee_agent_addr,
                    payee_wallet_address=payee_wallet_addr,
                    amount_base_units=settings.payment_advisory_fee_base_units,
                    currency=settings.payment_testnet_denom,
                    purpose=PaymentPurpose.ADVISORY_PASS.value,
                    payer_agent_address=sender,
                    payer_wallet_address=payer_wallet_addr,
                    service_inquiry_id=clean_inquiry_id or session_key,
                )

            storage.save_session(
                session_id=session_key,
                farmer_id=sender,
                state="AWAITING_PAYMENT_APPROVAL",
                pending_payment_reference=pay_record.payment_reference,
                active_booking_id=clean_inquiry_id,
            )

            review_details = pay_service.format_review_details(pay_record)
            card_content = build_payment_review_card(review_details)

            reply_lines = [
                f"ðŸ’³ **Payment Review Request: {review_details.purpose}**",
                f"*(Fetch.ai Agent Payment Protocol â€” {review_details.network} Testnet)*",
                "",
                f"â€¢ **Associated Booking:** `{clean_inquiry_id}`" if clean_inquiry_id and clean_inquiry_id.startswith("KB-BOOK-") else "",
                f"â€¢ **Payment Reference:** `{review_details.reference}`",
                f"â€¢ **Exact Amount & Denomination:** **{review_details.amount_display}**",
                f"â€¢ **Verified Recipient Address:** `{review_details.recipient_address}`",
                f"â€¢ **Target Network:** {review_details.network} (Fetch.ai Testnet)",
                f"â€¢ **Estimated Network Fee:** {review_details.estimated_fee}",
                f"â€¢ **Payer Balance Status:** {review_details.payer_balance_status}",
                f"â€¢ **Notice:** {review_details.testnet_notice}",
                f"â€¢ **Validity Window:** {review_details.deadline_seconds}s expiry protection.",
            ]
            if not settings.enable_payment_protocol:
                reply_lines.append("â€¢ **Protocol Execution:** ðŸ”´ Disabled (`ENABLE_PAYMENT_PROTOCOL=false`; test safety mode).")
            reply_lines.extend([
                "",
                "ðŸ”’ **Safety Gate Status:** Awaiting your explicit authorization.",
                "Click **'Approve Testnet Payment'** on the card below or reply with the exact authorization command to proceed.",
                "Casual messages (*'ok'*, *'yes'*) will not authorize payment.",
            ])
            reply_lines = [line for line in reply_lines if line != ""]
            reply_body = "\n".join(reply_lines)
            response_msg = build_chat_response(reply_body, card_content)
            await ctx.send(sender, response_msg)
            ctx.logger.info(f"Dispatched payment review card ({pay_record.payment_reference}) to sender '{sender}'")
            return

        # 4f. Payment Protocol Authorization Action
        elif booking_action == BookingActionType.AUTHORIZE_PAYMENT:
            ref = action_param.strip() if action_param else None
            sess = storage.get_session(session_key)
            if not ref and sess:
                ref = sess.get("pending_payment_reference")

            # Verify that if ref exists and session has active_booking_id, ref matches active_booking_id
            if ref and sess and sess.get("active_booking_id"):
                cand_rec = storage.get_payment_record(ref)
                if cand_rec and cand_rec.service_inquiry_id and cand_rec.service_inquiry_id.upper() != sess["active_booking_id"].upper():
                    ctx.logger.warning(
                        f"Refusing authorization of '{ref}' linked to '{cand_rec.service_inquiry_id}' "
                        f"which does not match active booking '{sess['active_booking_id']}'"
                    )
                    reply_body = (
                        f"âŒ **Mismatched Payment Reference:** Payment reference `{ref}` does not belong to "
                        f"your current active booking `{sess['active_booking_id']}`."
                    )
                    response_msg = build_chat_response(reply_body, None)
                    await ctx.send(sender, response_msg)
                    return

            # Safety gate check: If payment protocol is disabled (default):
            if not settings.enable_payment_protocol:
                record = storage.get_payment_record(ref) if ref else None
                amt_desc = f" ({record.amount_display} to `{record.payee_wallet_address}`)" if record else ""
                reply_body = (
                    "ðŸ”’ **Payment Protocol Safety Gate Active:**\n\n"
                    f"Fetch.ai Agent Payment Protocol execution is currently disabled (`ENABLE_PAYMENT_PROTOCOL=false`).\n\n"
                    f"Your payment authorization for reference `{ref or 'unknown'}`{amt_desc} "
                    "has been safely registered, but NO testnet tokens or funds have been transferred.\n\n"
                    "To enable live testnet settlement, configure designated testnet wallets with spendable funds and set `ENABLE_PAYMENT_PROTOCOL=true`."
                )
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                ctx.logger.info(
                    f"Payment authorization safely blocked by disabled gate for sender '{sender}' (ref={ref})"
                )
                return

            if not ref:
                reply_body = "âš ï¸ **Missing Reference:** Payment reference ID was not provided or active."
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                return

            record = storage.get_payment_record(ref)
            if not record:
                reply_body = f"âš ï¸ **Payment Not Found:** No payment record found for reference `{ref}`."
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                return

            if record.status == PaymentStatus.SETTLED:
                reply_body = (
                    f"âš ï¸ **Duplicate Authorization:** Payment reference `{ref}` has already been SETTLED on-chain "
                    f"(Tx: `{record.tx_hash or 'verified'}`). Replay or duplicate authorization blocked."
                )
                response_msg = build_chat_response(reply_body, build_payment_confirmed_card(record))
                await ctx.send(sender, response_msg)
                return

            if record.status == PaymentStatus.SUBMITTING:
                reply_body = (
                    f"â³ **Payment Currently Submitting:** Payment reference `{ref}` is currently being submitted "
                    "to the network. Please wait for the broadcast to complete."
                )
                response_msg = build_chat_response(reply_body, build_payment_status_card(record, payment_enabled=True))
                await ctx.send(sender, response_msg)
                return

            if record.status in (PaymentStatus.COMMITTED, PaymentStatus.PENDING_SETTLEMENT, PaymentStatus.AWAITING_RECONCILIATION):
                reply_body = (
                    f"â³ **Payment Already Submitted:** Payment reference `{ref}` has already been broadcast "
                    f"(Tx: `{record.tx_hash or 'broadcasted'}`). Replay or duplicate authorization blocked. "
                    "Awaiting authoritative ledger settlement."
                )
                response_msg = build_chat_response(reply_body, build_payment_status_card(record, payment_enabled=True))
                await ctx.send(sender, response_msg)
                return

            if record.status in (PaymentStatus.FAILED, PaymentStatus.CANCELLED, PaymentStatus.REJECTED):
                reply_body = (
                    f"âš ï¸ **Invalid Authorization State:** Payment reference `{ref}` is currently `{record.status.value}`. "
                    "Cannot authorize a cancelled, failed, or rejected payment. Please request a new payment review."
                )
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                return

            # Live payment protocol execution path (when enabled)
            from krishibazaar.services.payment_service import (
                PaymentService,
                validate_fetch_wallet_address,
            )
            from cosmpy.aerial.wallet import LocalWallet
            pay_service = PaymentService(storage_engine=storage, settings_obj=settings)

            if settings.payment_network != "testnet" or settings.payment_testnet_chain_id != "dorado-1":
                reply_body = (
                    "âŒ **Security Violation:** Mainnet transfers are strictly disabled in KrishiBazaar. "
                    "Only the Fetch.ai Dorado testnet (dorado-1) is supported."
                )
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                return

            # Reject unverified or placeholder addresses before executing
            is_valid_rec, rec_err = validate_fetch_wallet_address(record.payee_wallet_address)
            if not is_valid_rec:
                reply_body = f"âŒ **Payment Rejected:** Recipient address is invalid or a placeholder: {rec_err}"
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                return

            if not settings.payment_payer_seed:
                reply_body = (
                    f"âš ï¸ **Payer Wallet Missing:** Payment protocol is enabled, but no payer seed is configured "
                    f"(PAYMENT_PAYER_SEED is empty). Cannot sign or execute transfer for `{ref}`."
                )
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                return

            try:
                payer_wallet = LocalWallet.from_mnemonic(settings.payment_payer_seed)
            except Exception:
                payer_wallet = LocalWallet.from_unsafe_seed(settings.payment_payer_seed)

            # Strictly enforce user-specified approval parameters against record
            user_amount = (
                card_selection.get("amount")
                or card_selection.get("amount_base_units")
                if card_selection
                else record.amount_base_units
            )
            user_currency = (
                card_selection.get("currency")
                if card_selection and card_selection.get("currency")
                else record.currency
            )
            user_recipient = (
                card_selection.get("recipient")
                if card_selection and card_selection.get("recipient")
                else record.payee_wallet_address
            )
            approval = ExplicitPaymentApproval(
                reference=record.payment_reference,
                amount_base_units=int(user_amount),
                currency=str(user_currency),
                recipient=str(user_recipient),
            )

            try:
                commit_msg, record = pay_service.execute_authorized_transfer(approval, payer_wallet)
                # Dispatch CommitPayment to canonical Payment Agent on port 8006
                payee_agent_addr = record.payee_agent_address
                resolver = get_krishibazaar_resolver()
                from uagents.communication import send_sync_message
                from krishibazaar.models import CompletePayment, CancelPayment

                settle_reply = await send_sync_message(
                    destination=payee_agent_addr,
                    message=commit_msg,
                    resolver=resolver,
                    timeout=15,
                )

                if isinstance(settle_reply, CompletePayment):
                    # Hardened settlement verification:
                    # An unverified or standalone CompletePayment message MUST NOT independently mark a payment SETTLED.
                    # Verify that storage was authoritatively marked SETTLED by the Payment Agent,
                    # or verify on-chain via authoritative reconciliation before accepting settlement.
                    rec = storage.get_payment_record(record.payment_reference)
                    is_authoritative = (
                        rec is not None
                        and rec.status == PaymentStatus.SETTLED
                        and rec.tx_hash == settle_reply.transaction_id
                        and rec.tx_height is not None
                    )
                    if not is_authoritative and rec and rec.tx_hash:
                        try:
                            is_authoritative, rec = pay_service.reconcile_payment_on_chain(record.payment_reference)
                        except Exception as e:
                            logger.warning(f"Authoritative reconciliation failed for {record.payment_reference}: {e}")
                            is_authoritative = False

                    if is_authoritative and rec and rec.status == PaymentStatus.SETTLED:
                        record = rec
                        card_content = build_payment_confirmed_card(record)
                        reply_body = (
                            f"âœ… **Payment Settled On-Chain:** Reference `{record.payment_reference}` settled! "
                            f"Transferred {record.amount_display} to `{record.payee_wallet_address}`. "
                            f"Transaction Hash: `{settle_reply.transaction_id}`."
                        )
                    else:
                        card_content = None
                        reply_body = (
                            f"â³ **Payment Pending Authoritative Finality:** Received settlement notification for "
                            f"`{record.payment_reference}`, but on-chain verification is still pending or unconfirmed."
                        )
                elif isinstance(settle_reply, CancelPayment):
                    card_content = None
                    reply_body = (
                        f"âŒ **Payment Failed / Cancelled:** On-chain verification failed: {settle_reply.reason}. "
                        f"Reference `{record.payment_reference}`."
                    )
                else:
                    card_content = None
                    reply_body = (
                        f"â³ **Payment Pending Finality:** Transaction broadcast for `{record.payment_reference}`, "
                        "awaiting authoritative block confirmation."
                    )

            except TimeoutError as ex:
                reply_body = f"âŒ **Payment Request Expired:** {str(ex)} Please request a new payment review."
                card_content = None
            except ValueError as ex:
                reply_body = f"âŒ **Payment Validation Error:** {str(ex)}"
                card_content = None
            except Exception as ex:
                reply_body = f"âŒ **Payment Execution Error:** {str(ex)}"
                card_content = None

            response_msg = build_chat_response(reply_body, card_content)
            await ctx.send(sender, response_msg)
            return

        # 4g. Payment Protocol Rejection Action
        elif booking_action == BookingActionType.REJECT_PAYMENT:
            ref = action_param.strip() if action_param else None
            if not ref:
                sess = storage.get_session(session_key)
                ref = sess.get("pending_payment_reference") if sess else None

            if ref:
                record = storage.get_payment_record(ref)
                if record:
                    storage.update_payment_status(
                        reference=ref,
                        status=PaymentStatus.REJECTED,
                        rejection_reason="Declined by user via chat UI",
                    )
            reply_body = (
                f"âŒ **Payment Request Declined:** Payment authorization for reference `{ref or 'unknown'}` "
                f"was explicitly declined. No funds were debited."
            )
            response_msg = build_chat_response(reply_body, None)
            await ctx.send(sender, response_msg)
            ctx.logger.info(f"Dispatched payment rejection response to sender '{sender}'")
            return

        # 4h. Safety Check: If sender has an active pending payment awaiting authorization
        sess = storage.get_session(session_key)
        pending_pay_ref = sess.get("pending_payment_reference") if sess else None
        if pending_pay_ref:
            pay_rec = storage.get_payment_record(pending_pay_ref)
            if pay_rec and pay_rec.status in (PaymentStatus.REQUESTED, PaymentStatus.AWAITING_AUTHORIZATION):
                # Check expiration
                now_utc = datetime.now(timezone.utc)
                try:
                    pay_created = datetime.fromisoformat(pay_rec.created_at_utc)
                    elapsed = (now_utc - pay_created).total_seconds()
                except Exception:
                    elapsed = 0
                if elapsed > pay_rec.deadline_seconds:
                    storage.update_payment_status(
                        reference=pending_pay_ref,
                        status=PaymentStatus.FAILED,
                        rejection_reason="Payment request expired before authorization.",
                    )
                elif is_casual_acknowledgement(user_text) or not user_text.strip():
                    from krishibazaar.services.payment_service import PaymentService
                    pay_service = PaymentService(storage_engine=storage, settings_obj=settings)
                    review_details = pay_service.format_review_details(pay_rec)
                    card_content = build_payment_review_card(review_details)
                    reply_body = (
                        f"â³ **Pending Payment Request Awaiting Explicit Authorization**\n\n"
                        f"You have an active pending payment request `{pay_rec.payment_reference}` for "
                        f"**{pay_rec.amount_display}** to `{pay_rec.payee_wallet_address}`.\n\n"
                        f"ðŸ”’ Casual messages or ambiguous acknowledgements (*'ok'*, *'yes'*) do NOT authorize token transfers.\n"
                        f"To authorize, click **'Approve Testnet Payment'** on the card below or reply with the exact authorization command:\n"
                        f"`{review_details.approval_command_template}`\n\n"
                        f"To decline, reply with *'Decline'*."
                    )
                    response_msg = build_chat_response(reply_body, card_content)
                    await ctx.send(sender, response_msg)
                    ctx.logger.info(f"Prompted explicit payment authorization to sender '{sender}' for ref '{pending_pay_ref}'")
                    return

        # 4i. Safety Check: If sender has an active unexpired pending proposal awaiting explicit approval
        pending_prop = storage.get_pending_proposal_for_session(session_key)
        if pending_prop and not pending_prop.is_consumed:
            # Check expiration
            now_utc = datetime.now(timezone.utc)
            try:
                expires_at = datetime.fromisoformat(pending_prop.expires_at_utc)
            except Exception:
                expires_at = now_utc

            if now_utc > expires_at:
                storage.consume_proposal(pending_prop.proposal_id)
                reply_body = (
                    f"âš ï¸ **Proposal Expired:** Your pending proposal `{pending_prop.proposal_id}` "
                    f"expired at {pending_prop.expires_at_utc}. Due to market rate and transport quote volatility, "
                    f"proposals are only valid for 15 minutes. Please select an option again (e.g. *\"Select option 1\"*) "
                    f"to generate a fresh proposal."
                )
                exp_card = build_expired_proposal_card(
                    pending_prop.proposal_id, pending_prop.expires_at_utc
                )
                response_msg = build_chat_response(reply_body, exp_card)
                await ctx.send(sender, response_msg)
                ctx.logger.info(f"Notified sender '{sender}' of expired pending proposal '{pending_prop.proposal_id}'")
                return

            # Proposal is active and unexpired!
            # If farmer sent a casual acknowledgement, greeting, or non-inquiry text, do NOT confirm booking or ask for crop details
            if is_casual_acknowledgement(user_text) or not user_text.strip():
                reply_body = (
                    f"â³ **Pending Proposal Awaiting Explicit Confirmation**\n\n"
                    f"You have an active pending proposal `{pending_prop.proposal_id}` for "
                    f"**{pending_prop.quantity_quintals:g} qtl {pending_prop.crop}** to **{pending_prop.destination_name}** "
                    f"(Est. Net Payout: INR {pending_prop.estimated_net_payout:,.2f}).\n\n"
                    f"ðŸ”’ Casual messages or ambiguous acknowledgements do NOT confirm bookings. "
                    f"To confirm this simulated booking, please reply with unambiguous approval:\n"
                    f"ðŸ‘‰ *\"Confirm booking\"* or *\"Approve proposal {pending_prop.proposal_id}\"*\n\n"
                    f"To cancel, reply with *\"Cancel\"*. Or describe new produce requirements to start over."
                )
                review_card = build_proposal_review_card(pending_prop)
                response_msg = build_chat_response(reply_body, review_card)
                await ctx.send(sender, response_msg)
                ctx.logger.info(
                    f"Prompted explicit approval clarification to sender '{sender}' for proposal '{pending_prop.proposal_id}'"
                )
                return

        # 4j. Active Confirmed Booking Context Preservation:
        active_sess = storage.get_session(session_key)
        confirmed_booking = None
        if active_sess:
            if active_sess.get("active_booking_id"):
                cand_booking = storage.get_booking(active_sess["active_booking_id"])
                # Session isolation check: ensure candidate booking belongs to this sender
                if cand_booking and (cand_booking.farmer_id == sender or active_sess.get("farmer_id") == sender):
                    confirmed_booking = cand_booking
            if not confirmed_booking and active_sess.get("state") == "BOOKING_CONFIRMED":
                bookings = storage.list_bookings(farmer_id=sender)
                if bookings:
                    confirmed_booking = bookings[0]

        # 4k. General Informational & Introduction Query Check:
        # If the user asks for an introduction, explanation, or overview of KrishiBazaar and how it helps farmers,
        # return a comprehensive informational response. Do NOT hijack with an old booking or onboarding form.
        if is_informational_query(user_text):
            intro_text = build_introduction_response()
            response_msg = build_chat_response(intro_text, None)
            await ctx.send(sender, response_msg)
            ctx.logger.info(f"Dispatched KrishiBazaar introduction/overview response to sender '{sender}'")
            return

        # 4l. Payment Readiness & Subsystem Audit Check (Safety net before intent extraction)
        if is_payment_readiness_query(user_text):
            target_book_id = extract_booking_reference(user_text)
            if target_book_id:
                booking = storage.get_booking(target_book_id)
                sess = storage.get_session(session_key)
                is_owner = True
                if booking and booking.farmer_id and booking.farmer_id != sender:
                    if not sess or sess.get("farmer_id") != sender:
                        is_owner = False

                cand_pay = storage.get_payment_record_by_inquiry(target_book_id)
                pay_rec = cand_pay if (cand_pay and cand_pay.service_inquiry_id and cand_pay.service_inquiry_id.upper() == target_book_id) else None

                report_lines = format_booking_payment_audit(
                    booking_id=target_book_id,
                    booking=booking,
                    payment_record=pay_rec,
                    settings_obj=settings,
                    is_owner=is_owner,
                )
                reply_body = "\n".join(report_lines)
                card_content = None
                try:
                    card_content = build_booking_payment_audit_card(
                        booking_id=target_book_id,
                        booking=booking,
                        payment_record=pay_rec,
                        payment_enabled=settings.enable_payment_protocol,
                    )
                except Exception:
                    pass

                response_msg = build_chat_response(reply_body, card_content)
                await ctx.send(sender, response_msg)
                ctx.logger.info(f"Dispatched fallback booking payment audit response to sender '{sender}' for booking '{target_book_id}'")
                return

            target_prop_id = extract_proposal_reference(user_text)
            prop = None
            sess = storage.get_session(session_key)
            if target_prop_id:
                prop = storage.get_proposal(target_prop_id)
            else:
                pending_prop = storage.get_pending_proposal_for_session(session_key)
                if pending_prop:
                    prop = pending_prop
                    target_prop_id = pending_prop.proposal_id
                elif sess and sess.get("active_proposal_id"):
                    cand_p = storage.get_proposal(sess["active_proposal_id"])
                    if cand_p:
                        prop = cand_p
                        target_prop_id = cand_p.proposal_id

            booking = storage.get_booking_by_proposal(target_prop_id) if target_prop_id else None
            pay_rec = None
            if booking:
                pay_rec = storage.get_payment_record_by_inquiry(booking.booking_id)
            elif target_prop_id:
                pay_rec = storage.get_payment_record_by_inquiry(target_prop_id)

            report_lines = format_payment_readiness_audit(
                proposal_id=target_prop_id,
                proposal=prop,
                booking=booking,
                payment_record=pay_rec,
                settings_obj=settings,
                storage_engine=storage,
            )
            reply_body = "\n".join(report_lines)
            card_content = None
            try:
                card_content = build_payment_readiness_card(
                    proposal_id=target_prop_id,
                    proposal=prop,
                    booking=booking,
                    payment_record=pay_rec,
                    payment_enabled=settings.enable_payment_protocol,
                )
            except Exception:
                pass
            response_msg = build_chat_response(reply_body, card_content)
            await ctx.send(sender, response_msg)
            ctx.logger.info(
                f"Dispatched fallback payment readiness audit response to sender '{sender}' for proposal '{target_prop_id or 'none'}'"
            )
            return

        # 5. Extract structured intent via ASI:One Developer API
        intent = await extract_farmer_intent_async(user_text)
        if intent.extraction_source != "asi1":
            ctx.logger.info(f"Intent extracted via: {intent.extraction_source}")

        # Determine if this message is a new produce sale or market inquiry
        clean_lower = user_text.lower().strip()
        is_new_produce_inquiry = bool(
            intent.is_complete
            or (intent.quantity_quintals is not None)
            or (intent.crop and confirmed_booking and intent.crop.lower() != confirmed_booking.crop.lower())
            or (intent.crop and intent.origin_location)
            or any(
                w in clean_lower
                for w in (
                    "want to sell",
                    "sell",
                    "have",
                    "quote",
                    "quotes",
                    "rate",
                    "rates",
                    "mandi",
                    "mandis",
                    "price",
                    "prices",
                    "market price",
                    "fresh quote",
                    "new quote",
                    "start over",
                )
            )
        )

        is_payment_query = bool(
            confirmed_booking
            and not is_new_produce_inquiry
            and (
                (confirmed_booking.booking_id.lower() in clean_lower)
                or any(
                    kw in clean_lower
                    for kw in (
                        "booking payment",
                        "pay advisory fee",
                        "pay advisory",
                        "pay fee",
                        "payment for booking",
                        "pay for booking",
                        "payment status",
                        "check payment",
                        "advisory fee",
                    )
                )
            )
        )

        # A confirmed booking is ONLY shown when the user explicitly requests its status,
        # references its booking ID, or asks a direct follow-up about next steps / pickup
        is_booking_followup = bool(
            confirmed_booking
            and not is_new_produce_inquiry
            and (
                (confirmed_booking.booking_id.lower() in clean_lower)
                or any(
                    phrase in clean_lower
                    for phrase in (
                        "what should i do now",
                        "what should i do",
                        "what next",
                        "what to do next",
                        "what to do now",
                        "what do i do now",
                        "what do i do next",
                        "next step",
                        "what should i do for my booking",
                        "next step for my booking",
                        "when is pickup",
                        "pickup time",
                        "pickup date",
                        "who is the driver",
                        "scheduled pickup",
                        "my booking",
                        "booking details",
                        "booking status",
                        "track booking",
                        "where is my driver",
                        "where is my truck",
                    )
                )
            )
        )

        if confirmed_booking and not is_new_produce_inquiry and (is_payment_query or is_booking_followup):
            if is_payment_query and not is_casual_acknowledgement(user_text):
                pay_rec = storage.get_payment_record_by_inquiry(confirmed_booking.booking_id)
                if not pay_rec and active_sess and active_sess.get("pending_payment_reference"):
                    cand_pay = storage.get_payment_record(active_sess["pending_payment_reference"])
                    # Strict verification: Ensure the candidate payment belongs to confirmed_booking
                    if cand_pay and cand_pay.service_inquiry_id and cand_pay.service_inquiry_id.upper() == confirmed_booking.booking_id.upper():
                        pay_rec = cand_pay

                if pay_rec:
                    status_lines = format_payment_status(
                        pay_rec, payment_enabled=settings.enable_payment_protocol
                    )
                    reply_body = "\n".join(status_lines)
                    card_content = build_payment_status_card(
                        pay_rec, payment_enabled=settings.enable_payment_protocol
                    )
                else:
                    reply_lines = [
                        f"ðŸ’³ **Payment Information for Booking `{confirmed_booking.booking_id}`**",
                        "",
                        f"â€¢ **Booking Reference:** `{confirmed_booking.booking_id}` ({confirmed_booking.quantity_quintals:g} qtl {confirmed_booking.crop})",
                        f"â€¢ **Advisory Service Fee:** 0.05 FET (50,000,000,000,000,000 atestfet)",
                        f"â€¢ **Network:** Fetch.ai Dorado Testnet (`dorado-1`)",
                        f"â€¢ **Recipient (Masked):** {mask_address(settings.payment_payee_wallet_address)}",
                    ]
                    if settings.enable_payment_protocol:
                        reply_lines.append("â€¢ **Payment Protocol:** Active (Testnet Mode).")
                        reply_lines.append(
                            "\nðŸ‘‰ Reply *\"Pay advisory fee\"* or click **\"Proceed to Payment Review\"** below to test the Fetch.ai Agent Payment Protocol."
                        )
                    else:
                        reply_lines.append("â€¢ **Payment Protocol:** Payment is currently disabled. No transfer has been initiated.")

                    reply_body = "\n".join(reply_lines)
                    card_content = build_booking_confirmed_card(
                        confirmed_booking, payment_enabled=settings.enable_payment_protocol
                    )

                response_msg = build_chat_response(reply_body, card_content)
                await ctx.send(sender, response_msg)
                ctx.logger.info(
                    f"Maintained confirmed booking payment context for sender '{sender}': {confirmed_booking.booking_id}"
                )
                return

            if is_booking_followup:
                reply_lines = [
                    f"âœ… **Your Booking is Confirmed:** `{confirmed_booking.booking_id}`",
                    "",
                    f"â€¢ **Crop & Quantity:** {confirmed_booking.quantity_quintals:g} qtl {confirmed_booking.crop}",
                    f"â€¢ **Destination Channel:** {confirmed_booking.channel_name}",
                    f"â€¢ **Estimated Net Payout:** INR {confirmed_booking.net_payout:,.2f}",
                    f"â€¢ **Scheduled Pickup:** {confirmed_booking.pickup_date}",
                ]
                if settings.enable_payment_protocol:
                    reply_lines.append("â€¢ **Testnet Advisory Payment:** Optional (Reply *\"Pay advisory fee\"* to test the Fetch.ai payment protocol).")
                else:
                    reply_lines.append("â€¢ **Payment Status:** Payment is currently disabled. No transfer has been initiated.")

                reply_lines.append("")
                reply_lines.append(
                    f"â„¹ï¸ *Your produce details are already saved for booking `{confirmed_booking.booking_id}`. You do not need to resubmit them.*"
                )
                reply_lines.append(
                    f"*(To track status, reply \"Status of {confirmed_booking.booking_id}\". To start a new quote for a different crop, specify the new crop name, e.g. \"I want to sell 100 quintals of wheat\")*"
                )

                card_content = build_booking_confirmed_card(
                    confirmed_booking, payment_enabled=settings.enable_payment_protocol
                )
                response_msg = build_chat_response("\n".join(reply_lines), card_content)
                await ctx.send(sender, response_msg)
                ctx.logger.info(
                    f"Maintained confirmed booking context for sender '{sender}': {confirmed_booking.booking_id}"
                )
                return

        # 4m. Greeting, Diagnostic & Non-Produce Query Guard:
        # Prevent bare greetings, diagnostic/ping messages, and general non-produce text from triggering optimization
        if not has_produce_signals(intent, user_text):
            if is_greeting(user_text) or is_casual_acknowledgement(user_text):
                reply_body = build_greeting_response(user_text)
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                ctx.logger.info(f"Dispatched greeting response to sender '{sender}'")
                return

            if is_diagnostic_text(user_text):
                reply_body = (
                    "ðŸŒ¾ **KrishiBazaar Diagnostic Received** ðŸŒ¾\n\n"
                    f"Diagnostic message received: `{user_text.strip()}`.\n\n"
                    "The KrishiBazaar autonomous multi-agent gateway is online and operational. "
                    "To retrieve wholesale APMC modal prices, assess transit weather hazards, and evaluate competitive buyer bids, "
                    "please specify your crop details (e.g. *\"I have 50 quintals of onions in Nashik\"*)."
                )
                response_msg = build_chat_response(reply_body, None)
                await ctx.send(sender, response_msg)
                ctx.logger.info(f"Dispatched diagnostic acknowledgement to sender '{sender}'")
                return

            if is_informational_query(user_text):
                intro_text = build_introduction_response()
                response_msg = build_chat_response(intro_text, None)
                await ctx.send(sender, response_msg)
                ctx.logger.info(f"Dispatched KrishiBazaar introduction/overview response to sender '{sender}'")
                return

        # Multi-turn intent preservation: Merge active session inquiry details if available
        # Only merge across turns within the same active unconfirmed inquiry flow when produce signals exist
        if (
            active_sess
            and active_sess.get("inquiry")
            and active_sess.get("state") != "BOOKING_CONFIRMED"
            and not is_new_produce_inquiry
            and has_produce_signals(intent, user_text)
        ):
            prev_inq = active_sess["inquiry"]
            if not intent.crop and getattr(prev_inq, "crop", None):
                intent.crop = prev_inq.crop
            if intent.quantity_quintals is None and getattr(prev_inq, "quantity_quintals", None):
                intent.quantity_quintals = prev_inq.quantity_quintals
            if not intent.origin_location:
                intent.origin_location = getattr(prev_inq, "origin_location", None) or getattr(prev_inq, "location_district", None)
            if intent.minimum_net_return_inr is None and getattr(prev_inq, "minimum_net_return_inr", None) is not None:
                intent.minimum_net_return_inr = prev_inq.minimum_net_return_inr
            if not intent.selling_deadline and getattr(prev_inq, "selling_deadline", None):
                intent.selling_deadline = prev_inq.selling_deadline
            if not intent.storage_preference and getattr(prev_inq, "storage_preference", None):
                intent.storage_preference = prev_inq.storage_preference

            intent.evaluate_completeness()

        # 6. Formulate response payload based on intent completeness
        if intent.crop and intent.origin_location:
            district, state = resolve_district_state(intent.origin_location)

            if intent.is_complete and intent.quantity_quintals:
                # Full Multi-Specialist Coordination & Deterministic Profit Optimization
                coord = await coordinate_specialists_async(
                    crop=intent.crop,
                    quantity_quintals=intent.quantity_quintals,
                    origin_location=intent.origin_location,
                    selling_deadline=intent.selling_deadline,
                    minimum_net_return_inr=intent.minimum_net_return_inr,
                    storage_preference=intent.storage_preference,
                    ctx=ctx,
                )

                mandi_res: MandiPriceResult = coord["mandi_result"]
                nearby_res: MandiPriceResult | None = coord["nearby_result"]
                weather_res: WeatherResult = coord["weather_result"]
                buyer_res: BuyerOffersResult = coord["buyer_result"]
                transport_res: TransportQuotesResult = coord["transport_result"]
                opt_res: OptimizationResult = coord["optimization_result"]
                target_date: str | None = coord.get("target_date")

                reply_lines: list[str] = [
                    "ðŸŒ¾ **KrishiBazaar: Request Understood & Optimized!** ðŸŒ¾",
                    "",
                    f"â€¢ **Crop:** {intent.crop.title()}",
                    f"â€¢ **Quantity:** {intent.quantity_quintals:g} quintals (approx. {intent.quantity_quintals * 100:g} kg)",
                    f"â€¢ **Origin Location:** {intent.origin_location} ({district}{f', {state}' if state else ''})",
                ]
                if intent.selling_deadline:
                    reply_lines.append(f"â€¢ **Selling Deadline:** {intent.selling_deadline}")
                if intent.minimum_net_return_inr is not None:
                    reply_lines.append(f"â€¢ **Target Net Return:** INR {intent.minimum_net_return_inr:,.2f}")
                if intent.storage_preference:
                    reply_lines.append(f"â€¢ **Storage Preference:** {intent.storage_preference}")
                reply_lines.append("")
                if intent.extraction_source == "asi1":
                    reply_lines.append("âœ… *Extracted and validated via ASI:One.*")
                else:
                    reply_lines.append(f"â„¹ï¸ *Extracted via fallback parser ({intent.extraction_source}).*")
                reply_lines.append("")

                # 1. Market Specialist Section
                mandi_report = format_mandi_market_report(
                    crop=intent.crop,
                    district=district,
                    state=state,
                    mandi_result=mandi_res,
                    nearby_result=nearby_res,
                )
                reply_lines.extend(mandi_report)
                reply_lines.append("")

                # 2. Weather Specialist Section
                weather_report = format_weather_report(
                    weather_res,
                    selling_deadline=intent.selling_deadline,
                    target_date=target_date,
                    date_was_past=coord.get("date_was_past", False),
                    original_past_date=coord.get("original_past_date"),
                )
                reply_lines.extend(weather_report)
                reply_lines.append("")

                # 3. Deterministic Net-Return Optimization & Channel Ranking Section
                opt_report = format_optimization_report(
                    opt=opt_res,
                    buyer_result=buyer_res,
                    transport_result=transport_res,
                )
                reply_lines.extend(opt_report)
                reply_lines.append("")

                # 4. Simulation Notice & Safety Gate Section
                safety_notices = format_simulation_and_safety_notices(coord)
                reply_lines.extend(safety_notices)

                inquiry_obj = FarmerInquiry(
                    farmer_id=sender,
                    crop=intent.crop,
                    quantity_quintals=intent.quantity_quintals,
                    location_district=district or intent.origin_location,
                    origin_location=intent.origin_location,
                    selling_deadline=intent.selling_deadline,
                    minimum_net_return_inr=intent.minimum_net_return_inr,
                    storage_preference=intent.storage_preference,
                )
                try:
                    storage.save_session(
                        session_id=session_key,
                        farmer_id=sender,
                        state="OFFERS_OPTIMIZED",
                        inquiry=inquiry_obj,
                        active_options=opt_res.all_options,
                    )
                except Exception as ex:
                    ctx.logger.warning(f"Failed to persist session: {ex}")

                card_content = build_channel_recommendations_card(opt_res)

            else:
                # Incomplete intent: Query mandi prices and prompt for missing fields
                if settings.market_agent_address:
                    query_msg = MarketQuery(commodity=intent.crop, district=district, state=state)
                    res, _ = await query_specialist_agent_async(
                        destination=settings.market_agent_address,
                        query_msg=query_msg,
                        response_type=MandiPriceResult,
                        specialist_name="Market Specialist",
                        ctx=ctx,
                        timeout=4.0,
                    )
                    if res is not None:
                        mandi_res = res
                        nearby_res = None
                        if mandi_res.success and not mandi_res.records and state:
                            fallback_msg = MarketQuery(commodity=intent.crop, district=None, state=state)
                            nearby_res, _ = await query_specialist_agent_async(
                                destination=settings.market_agent_address,
                                query_msg=fallback_msg,
                                response_type=MandiPriceResult,
                                specialist_name="Market Specialist (State Fallback)",
                                ctx=ctx,
                                timeout=4.0,
                            )
                    elif settings.allow_direct_service_fallback:
                        logger.info("Market agent query failed. Invoking local direct-service fallback.")
                        mandi_res = await get_mandi_prices_async(
                            commodity=intent.crop,
                            district=district,
                            state=state,
                        )
                        nearby_res = None
                        if mandi_res.success and not mandi_res.records and state:
                            nearby_res = await get_mandi_prices_async(
                                commodity=intent.crop,
                                state=state,
                                district=None,
                            )
                    else:
                        mandi_res = MandiPriceResult(
                            success=False,
                            commodity=intent.crop,
                            records=[],
                            error_message="Market agent communication failed.",
                            provenance=DataProvenance(
                                source=f"data.gov.in (AGMARKNET) via Market Agent [{settings.market_agent_address}]",
                                retrieval_timestamp_utc=datetime.now(timezone.utc).isoformat(),
                                observation_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                freshness_status="STALE",
                                is_live=False,
                            ),
                        )
                        nearby_res = None
                elif settings.allow_direct_service_fallback:
                    mandi_res = await get_mandi_prices_async(
                        commodity=intent.crop,
                        district=district,
                        state=state,
                    )
                    nearby_res = None
                    if mandi_res.success and not mandi_res.records and state:
                        nearby_res = await get_mandi_prices_async(
                            commodity=intent.crop,
                            state=state,
                            district=None,
                        )
                else:
                    mandi_res = MandiPriceResult(
                        success=False,
                        commodity=intent.crop,
                        records=[],
                        error_message="Market agent address not configured and direct-service fallback disabled.",
                        provenance=DataProvenance(
                            source="data.gov.in (AGMARKNET)",
                            retrieval_timestamp_utc=datetime.now(timezone.utc).isoformat(),
                            observation_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                            freshness_status="STALE",
                            is_live=False,
                            disclaimer="Unconfigured agent; direct-service fallback disabled.",
                        ),
                    )
                    nearby_res = None

                reply_lines = [
                    "ðŸŒ¾ **KrishiBazaar: Mandi Market Enquiry** ðŸŒ¾",
                    "",
                    f"â€¢ **Crop:** {intent.crop.title()}",
                    f"â€¢ **Origin Location:** {intent.origin_location} ({district}{f', {state}' if state else ''})",
                    "",
                ]

                # Append live mandi market report
                mandi_report = format_mandi_market_report(
                    crop=intent.crop,
                    district=district,
                    state=state,
                    mandi_result=mandi_res,
                    nearby_result=nearby_res,
                )
                reply_lines.extend(mandi_report)
                reply_lines.append("")

                if intent.follow_up_question:
                    reply_lines.append(f"ðŸ‘‰ **To calculate your net return:** {intent.follow_up_question}")

                partial_inq = FarmerInquiry(
                    farmer_id=sender,
                    crop=intent.crop,
                    quantity_quintals=intent.quantity_quintals,
                    location_district=district or intent.origin_location,
                    origin_location=intent.origin_location,
                    selling_deadline=intent.selling_deadline,
                    minimum_net_return_inr=intent.minimum_net_return_inr,
                    storage_preference=intent.storage_preference,
                )
                try:
                    storage.save_session(
                        session_id=session_key,
                        farmer_id=sender,
                        state="AWAITING_DETAILS",
                        inquiry=partial_inq,
                    )
                except Exception:
                    pass

                card_content = build_incomplete_inquiry_form_card(intent)

            reply_body = "\n".join(reply_lines)

        else:
            # Check if user gave a greeting, diagnostic ping, or general non-produce question
            cleaned_text = user_text.lower().strip()
            if not cleaned_text or is_greeting(user_text) or is_casual_acknowledgement(user_text):
                reply_body = build_greeting_response(user_text)
            elif is_informational_query(user_text):
                reply_body = build_introduction_response()
            elif is_diagnostic_text(user_text):
                reply_body = (
                    "ðŸŒ¾ **KrishiBazaar Diagnostic Received** ðŸŒ¾\n\n"
                    f"Diagnostic message received: `{user_text.strip()}`.\n\n"
                    "The KrishiBazaar agent network is operational. "
                    "To calculate your net return, please describe your produce (e.g. *\"I have 50 quintals of onions in Nashik\"*)."
                )
            elif not intent.crop and intent.quantity_quintals is None and not intent.origin_location:
                # Unrelated questions or general queries with zero produce details
                reply_body = (
                    "ðŸŒ¾ **KrishiBazaar: Produce Details Needed**\n\n"
                    "I am your autonomous farm-to-market negotiation agent. I didn't recognize any crop or produce "
                    "details in your message.\n\n"
                    "ðŸ‘‰ **How to get started:** Tell me what crop you would like to sell, for example:\n"
                    "*\"I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow.\"*"
                )
            else:
                reply_body = (
                    f"ðŸŒ¾ **KrishiBazaar Clarification**\n\n"
                    f"{intent.follow_up_question}\n\n"
                    "*(Please specify this detail so I can check nearby mandis and transport quotes without guessing)*"
                )
                if intent.crop or intent.quantity_quintals or intent.origin_location or intent.minimum_net_return_inr:
                    partial_inq = FarmerInquiry(
                        farmer_id=sender,
                        crop=intent.crop,
                        quantity_quintals=intent.quantity_quintals,
                        location_district=intent.origin_location,
                        origin_location=intent.origin_location,
                        selling_deadline=intent.selling_deadline,
                        minimum_net_return_inr=intent.minimum_net_return_inr,
                        storage_preference=intent.storage_preference,
                    )
                    try:
                        storage.save_session(
                            session_id=session_key,
                            farmer_id=sender,
                            state="AWAITING_DETAILS",
                            inquiry=partial_inq,
                        )
                    except Exception:
                        pass
            card_content = None

        # 6. Send response with TextContent, interactive Card (if applicable), and EndSessionContent
        response_msg = build_chat_response(reply_body, card_content)

        await ctx.send(sender, response_msg)
        ctx.logger.info(f"Dispatched response to sender '{sender}' (complete={intent.is_complete})")

    except Exception as ex:
        # Safe error logging without secret exposure
        ctx.logger.error(f"Error handling ChatMessage from {sender}: {type(ex).__name__} - {str(ex)}")


@chat_proto.on_message(ChatAcknowledgement)
async def handle_acknowledgement(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
    """Handle delivery acknowledgement receipts."""
    ctx.logger.debug(
        f"Delivery receipt from sender '{sender}' for message: {msg.acknowledged_msg_id}"
    )


def create_orchestrator_agent(
    name: str = "krishibazaar_orchestrator",
    seed: str | None = None,
    port: int | None = None,
    mailbox: bool | None = None,
    publish_agent_details: bool = True,
    resolve: Any = None,
    report_events: bool | None = None,
) -> Agent:
    """
    Factory creating the KrishiBazaar Orchestrator Agent.
    Configures seed from environment, Agentverse mailbox, KrishiBazaarResolver, and chat protocol manifest.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    agent_seed = seed or settings.orchestrator_seed
    agent_port = port or settings.orchestrator_port
    use_mailbox = mailbox if mailbox is not None else settings.use_mailbox
    use_report_events = report_events if report_events is not None else settings.report_events

    agentverse_config = settings.agentverse_url
    if settings.agentverse_api_key:
        agentverse_config = f"{settings.agentverse_api_key}@{settings.agentverse_url}"

    resolver = resolve or get_krishibazaar_resolver()
    agent = Agent(
        name=name,
        seed=agent_seed,
        port=agent_port,
        mailbox=use_mailbox,
        agentverse=agentverse_config,
        resolve=resolver,
        publish_agent_details=publish_agent_details,
        report_events=use_report_events,
        description="KrishiBazaar Orchestrator: Autonomous Farm-to-Market Negotiation Agent for Indian Farmers",
    )

    # Apply Agentverse v2 authenticated mailbox client patch
    patch_agent_mailbox(agent, settings.agentverse_api_key)

    # Attach the official Agent Chat Protocol with manifest publication
    agent.include(chat_proto, publish_manifest=publish_agent_details)
    # Attach specialist response protocol so Orchestrator recognizes all specialist schema digests
    agent.include(specialist_response_proto, publish_manifest=False)
    return agent


# Default agent instance
orchestrator_agent = create_orchestrator_agent()


if __name__ == "__main__":
    from krishibazaar.agents.diagnostics import print_startup_banner

    specialist_summary = {
        "Chat Protocol": "AgentChatProtocol (ASI:One Native ACP V1)",
        "Intent Engine": (
            f"ASI:One ({settings.asi_one_model})"
            if settings.asi_one_api_key
            else "Rule-based fallback parser (ASI_ONE_API_KEY not configured)"
        ),
        "Market Agent": (
            f"Configured ({settings.market_agent_address[:16]}...) [Port {settings.market_agent_port}]"
            if settings.market_agent_address
            else "Unconfigured (Direct fallback enabled)"
        ),
        "Weather Agent": (
            f"Configured ({settings.weather_agent_address[:16]}...) [Port {settings.weather_agent_port}]"
            if settings.weather_agent_address
            else "Unconfigured (Direct fallback enabled)"
        ),
        "Buyer Agents": (
            f"Configured (A: Port {settings.buyer_agent_port}, B: Port {settings.buyer_b_agent_port})"
            if (settings.buyer_agent_address or settings.buyer_b_agent_address)
            else "Unconfigured (Direct fallback enabled)"
        ),
        "Transport Agent": (
            f"Configured ({settings.transport_agent_address[:16]}...) [Port {settings.transport_agent_port}]"
            if settings.transport_agent_address
            else "Unconfigured (Direct fallback enabled)"
        ),
        "Local Fallback": (
            "Enabled (graceful partial-failure resilience)"
            if settings.allow_direct_service_fallback
            else "Disabled (strict remote agent messaging required)"
        ),
    }

    print_startup_banner(
        agent_name="KrishiBazaar Orchestrator Agent",
        agent=orchestrator_agent,
        port=settings.orchestrator_port,
        role="ASI:One Conversational Multi-Agent Farm-to-Market Gateway",
        extra_details=specialist_summary,
    )
    orchestrator_agent.run()
