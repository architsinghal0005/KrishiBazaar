"""
Deterministic Net-Return Optimizer for KrishiBazaar.
Performs exact mathematical financial modeling without LLM guesswork.

Calculations for each sales channel:
    gross_value = quantity_quintals * price_per_quintal
    total_deductions = transport_cost + applicable_market_charges + storage_cost + estimated_loss_cost
    expected_net_return = gross_value - total_deductions
    net_return_per_quintal = expected_net_return / quantity_quintals

Enforces farmer's minimum net-return condition and provides comprehensive rankings.
"""

from __future__ import annotations
import logging
from typing import Literal

from krishibazaar.models import (
    BuyerOffer,
    MandiPriceRecord,
    NetReturnBreakdown,
    OptimizationResult,
    TransportQuote,
    WeatherRiskReport,
)

logger = logging.getLogger("krishibazaar.optimizer")

# Standard APMC market statutory rates (in accordance with Indian state Agricultural Marketing Regulations)
STANDARD_APMC_CESS_RATE = 0.015  # 1.5% statutory APMC market cess
STANDARD_HAMALI_PER_QUINTAL = 25.0  # INR 25 per quintal loading/unloading labor

# Standard storage tariffs if explicitly requested by farmer
STORAGE_DAILY_RATES_PER_QUINTAL = {
    "cold storage": 50.0,  # INR 50/qtl for perishable cold preservation
    "warehouse": 20.0,     # INR 20/qtl for dry warehouse storage
    "dry shed": 10.0,      # INR 10/qtl for shaded aggregate holding
}


def calculate_channel_net_return(
    scenario_id: str,
    channel_type: Literal["MANDI", "DIRECT_BUYER"],
    destination_name: str,
    quantity_quintals: float,
    price_per_quintal: float,
    distance_km: float = 0.0,
    transport_cost: float = 0.0,
    applicable_market_charges: float = 0.0,
    storage_cost: float = 0.0,
    estimated_loss_cost: float = 0.0,
    minimum_net_return_inr: float | None = None,
    mandi_cess_apmc: float = 0.0,
    loading_unloading_cost: float = 0.0,
) -> NetReturnBreakdown:
    """
    Calculate deterministic net financial return for a single sales channel.
    Raises ValueError for invalid inputs (negative or zero quantities/prices).
    """
    if quantity_quintals <= 0:
        raise ValueError(f"Quantity must be strictly positive (>0), got {quantity_quintals}")
    if price_per_quintal <= 0:
        raise ValueError(f"Price per quintal must be strictly positive (>0), got {price_per_quintal}")
    if transport_cost < 0 or applicable_market_charges < 0 or storage_cost < 0 or estimated_loss_cost < 0:
        raise ValueError("Cost deductions cannot be negative values.")

    # 1. Gross Revenue
    gross_value = round(quantity_quintals * price_per_quintal, 2)

    # 2. Total Deductions
    total_deductions = round(
        transport_cost + applicable_market_charges + storage_cost + estimated_loss_cost,
        2,
    )

    # 3. Expected Net Return
    expected_net_return = round(gross_value - total_deductions, 2)

    # 4. Net Return Per Quintal
    net_return_per_quintal = round(expected_net_return / quantity_quintals, 2)

    # 5. Enforce farmer's minimum threshold
    meets_minimum = (
        minimum_net_return_inr is None or expected_net_return >= minimum_net_return_inr
    )
    is_feasible = meets_minimum and (expected_net_return > 0)

    return NetReturnBreakdown(
        scenario_id=scenario_id,
        channel_type=channel_type,
        destination_name=destination_name,
        distance_km=round(distance_km, 1),
        quantity_quintals=quantity_quintals,
        offered_price_per_quintal=price_per_quintal,
        gross_value=gross_value,
        gross_revenue=gross_value,
        transport_cost=round(transport_cost, 2),
        applicable_market_charges=round(applicable_market_charges, 2),
        mandi_cess_apmc=round(mandi_cess_apmc, 2),
        loading_unloading_cost=round(loading_unloading_cost, 2),
        storage_cost=round(storage_cost, 2),
        estimated_loss_cost=round(estimated_loss_cost, 2),
        spoilage_risk_penalty=round(estimated_loss_cost, 2),
        total_deductions=total_deductions,
        expected_net_return=expected_net_return,
        net_return=expected_net_return,
        net_return_per_quintal=net_return_per_quintal,
        meets_minimum_condition=meets_minimum,
        is_feasible=is_feasible,
    )


def compute_weather_loss_risk(
    gross_value: float,
    weather_report: WeatherRiskReport | None,
    tarp_cover_included: bool = True,
) -> float:
    """
    Calculate estimated transit loss cost based on weather hazard severity.
    If waterproof tarpaulin cover is included, weather transit spoilage loss is 0.0.
    """
    if tarp_cover_included or not weather_report:
        return 0.0

    if weather_report.spoilage_hazard_level == "HIGH":
        # 3.5% spoilage risk penalty for uncovered transit during heavy rain
        return round(gross_value * 0.035, 2)
    elif weather_report.spoilage_hazard_level == "MEDIUM":
        # 1.5% spoilage risk penalty for uncovered transit during showers or extreme heat
        return round(gross_value * 0.015, 2)
    return 0.0


def compute_storage_cost(
    quantity_quintals: float,
    storage_preference: str | None = None,
) -> float:
    """Calculate optional storage cost if requested by farmer."""
    if not storage_preference:
        return 0.0

    pref_clean = storage_preference.strip().lower()
    for key, rate in STORAGE_DAILY_RATES_PER_QUINTAL.items():
        if key in pref_clean:
            return round(quantity_quintals * rate, 2)
    return 0.0


def optimize_sales_channels(
    crop: str,
    quantity_quintals: float,
    mandi_records: list[MandiPriceRecord],
    buyer_offers: list[BuyerOffer] | None = None,
    transport_quotes: dict[str, TransportQuote] | None = None,
    weather_report: WeatherRiskReport | None = None,
    minimum_net_return_inr: float | None = None,
    storage_preference: str | None = None,
) -> OptimizationResult:
    """
    Evaluate, calculate, and rank all available Mandi and Direct Buyer options.
    Enforces farmer minimum net return and outputs detailed comparative explanations.
    """
    if quantity_quintals <= 0:
        raise ValueError(f"Quantity must be strictly positive (>0), got {quantity_quintals}")

    quotes_map = transport_quotes or {}
    offers = buyer_offers or []
    all_breakdowns: list[NetReturnBreakdown] = []

    storage_deduction = compute_storage_cost(quantity_quintals, storage_preference)

    # 1. Evaluate Mandi Options
    for idx, mandi in enumerate(mandi_records, start=1):
        if getattr(mandi, "is_suspicious", False):
            logger.warning(
                f"Skipping suspicious mandi price record for '{mandi.mandi_name}' "
                f"(modal price: INR {mandi.modal_price}): {mandi.suspicion_reason}"
            )

            continue

        scenario_id = f"MANDI-{idx:02d}"
        dest_name = mandi.mandi_name
        price = mandi.modal_price

        # Retrieve or compute transport quote
        quote = quotes_map.get(dest_name) or quotes_map.get(mandi.district)
        if quote:
            trans_cost = quote.total_transport_cost
            dist_km = quote.distance_km
            tarp_included = quote.tarp_cover_included
        else:
            # Standard conservative distance estimate: 35 km @ INR 22/km + INR 500 base
            dist_km = 35.0
            trans_cost = round(500.0 + (dist_km * 22.0), 2)
            tarp_included = True

        gross = round(quantity_quintals * price, 2)
        cess = round(gross * STANDARD_APMC_CESS_RATE, 2)
        hamali = round(quantity_quintals * STANDARD_HAMALI_PER_QUINTAL, 2)
        market_charges = round(cess + hamali, 2)

        loss_cost = compute_weather_loss_risk(gross, weather_report, tarp_cover_included=tarp_included)

        breakdown = calculate_channel_net_return(
            scenario_id=scenario_id,
            channel_type="MANDI",
            destination_name=dest_name,
            quantity_quintals=quantity_quintals,
            price_per_quintal=price,
            distance_km=dist_km,
            transport_cost=trans_cost,
            applicable_market_charges=market_charges,
            storage_cost=storage_deduction,
            estimated_loss_cost=loss_cost,
            minimum_net_return_inr=minimum_net_return_inr,
            mandi_cess_apmc=cess,
            loading_unloading_cost=hamali,
        )
        all_breakdowns.append(breakdown)

    # 2. Evaluate Direct Buyer Offers
    for idx, buyer in enumerate(offers, start=1):
        scenario_id = f"BUYER-{idx:02d}"
        dest_name = f"{buyer.buyer_name} ({buyer.buyer_type})"
        price = buyer.offered_price_per_quintal

        if buyer.farmgate_pickup:
            trans_cost = 0.0
            dist_km = 0.0
            tarp_included = True
        else:
            quote = quotes_map.get(buyer.buyer_name)
            if quote:
                trans_cost = quote.total_transport_cost
                dist_km = quote.distance_km
                tarp_included = quote.tarp_cover_included
            else:
                dist_km = 20.0
                trans_cost = round(400.0 + (dist_km * 20.0), 2)
                tarp_included = True

        # Direct buyer sales exempt farmer from APMC market cess and loading charges
        market_charges = 0.0
        gross = round(quantity_quintals * price, 2)
        loss_cost = compute_weather_loss_risk(gross, weather_report, tarp_cover_included=tarp_included)

        breakdown = calculate_channel_net_return(
            scenario_id=scenario_id,
            channel_type="DIRECT_BUYER",
            destination_name=dest_name,
            quantity_quintals=quantity_quintals,
            price_per_quintal=price,
            distance_km=dist_km,
            transport_cost=trans_cost,
            applicable_market_charges=market_charges,
            storage_cost=storage_deduction,
            estimated_loss_cost=loss_cost,
            minimum_net_return_inr=minimum_net_return_inr,
            mandi_cess_apmc=0.0,
            loading_unloading_cost=0.0,
        )
        all_breakdowns.append(breakdown)

    # 3. Sort & Rank: Feasible options first (descending net return), followed by unfeasible
    sorted_options = sorted(
        all_breakdowns,
        key=lambda x: (x.is_feasible, x.expected_net_return),
        reverse=True,
    )

    # Assign ranks and notes
    for rank_idx, opt in enumerate(sorted_options, start=1):
        opt.rank = rank_idx
        if not opt.meets_minimum_condition and minimum_net_return_inr is not None:
            shortfall = round(minimum_net_return_inr - opt.expected_net_return, 2)
            opt.recommendation_note = (
                f"Below minimum target: Expected net return of INR {opt.expected_net_return:,.2f} "
                f"falls short of minimum threshold (INR {minimum_net_return_inr:,.2f}) by INR {shortfall:,.2f}."
            )
        elif rank_idx == 1:
            opt.recommendation_note = (
                f"Top Recommended Option: Highest net return of INR {opt.expected_net_return:,.2f} "
                f"(INR {opt.net_return_per_quintal:,.2f}/quintal) after all transport and statutory deductions."
            )
        else:
            diff = round(sorted_options[0].expected_net_return - opt.expected_net_return, 2)
            opt.recommendation_note = (
                f"Rank {rank_idx}: Delivers INR {opt.expected_net_return:,.2f} net (INR {diff:,.2f} less than top option)."
            )

    feasible_options = [opt for opt in sorted_options if opt.is_feasible]
    recommended = sorted_options[0] if sorted_options else None

    # 4. Generate Narrative Ranking Explanation
    if not sorted_options:
        explanation = "No sales channels or buyer offers were available for optimization."
    elif feasible_options:
        top = feasible_options[0]
        explanation = (
            f"**Recommended Option (Rank 1): {top.destination_name}**\n\n"
            f"• **Expected Net In-Pocket Return:** INR {top.expected_net_return:,.2f} "
            f"(INR {top.net_return_per_quintal:,.2f} per quintal)\n"
            f"• **Gross Value:** INR {top.gross_value:,.2f} ({top.quantity_quintals:g} qtl @ INR {top.offered_price_per_quintal:,.2f}/qtl)\n"
            f"• **Itemized Deductions:** Transport: INR {top.transport_cost:,.2f} | "
            f"APMC & Mandi Charges: INR {top.applicable_market_charges:,.2f} | "
            f"Storage: INR {top.storage_cost:,.2f} | Transit Risk Loss: INR {top.estimated_loss_cost:,.2f}\n\n"
            f"**Rationale:** {top.destination_name} ranks #1 because "
        )
        if top.channel_type == "DIRECT_BUYER":
            explanation += (
                "direct farmgate procurement eliminates APMC market cess and loading labor charges, "
                "yielding superior net profit for your harvest."
            )
        else:
            explanation += (
                "its higher modal price per quintal generates sufficient revenue margin to outweigh "
                "freight transport deductions and APMC market fees."
            )
    else:
        top = sorted_options[0]
        shortfall = round(minimum_net_return_inr - top.expected_net_return, 2) if minimum_net_return_inr else 0.0
        explanation = (
            f"⚠️ **Attention: None of the {len(sorted_options)} available options met your minimum net threshold** "
            f"of INR {minimum_net_return_inr:,.2f}.\n\n"
            f"The best available option is **{top.destination_name}**, yielding an expected net return of "
            f"INR {top.expected_net_return:,.2f} (a shortfall of INR {shortfall:,.2f})."
        )

    return OptimizationResult(
        crop=crop,
        quantity_quintals=quantity_quintals,
        minimum_net_return_inr=minimum_net_return_inr,
        all_options=sorted_options,
        feasible_options=feasible_options,
        recommended_option=recommended,
        summary_explanation=explanation,
    )
