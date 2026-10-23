"""
Deterministic Net-Return Optimizer for KrishiBazaar.
Performs exact deterministic mathematical financial modeling using stated assumptions, modal benchmarks, and simulated quotes.

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
from krishibazaar.services.transport_service import estimate_route_distance_km

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
    price_provenance: str = "",
    is_price_simulated: bool = False,
    transport_provenance: str = "Simulated freight tariff",
    charges_provenance: str = "Assumed standard statutory rates",
    is_apmc_exempt: bool = False,
    weather_loss_note: str = "",
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

    # Ensure consistency between itemized market charges and applicable_market_charges
    if applicable_market_charges == 0.0 and (mandi_cess_apmc > 0.0 or loading_unloading_cost > 0.0):
        applicable_market_charges = round(mandi_cess_apmc + loading_unloading_cost, 2)

    # Transparent provenance for zero freight
    if transport_cost == 0.0:
        if channel_type == "DIRECT_BUYER" and (not transport_provenance or transport_provenance == "Simulated freight tariff"):
            transport_provenance = "Buyer farmgate collection assumption (no freight cost incurred by farmer; INR 0.00)"
        elif channel_type == "MANDI" and (not transport_provenance or transport_provenance == "Simulated freight tariff"):
            transport_provenance = "Warning: Zero freight recorded for mandi haulage (unverified assumption; INR 0.00)"

    # Transparent provenance for market charges
    if is_apmc_exempt and (not charges_provenance or charges_provenance == "Assumed standard statutory rates"):
        charges_provenance = (
            "Direct trade statutory exemption assumption from APMC cess & hamali "
            "(assumed direct-buyer trade outside market yard; unverified assumption, not legally verified)"
        )
    elif not is_apmc_exempt and (not charges_provenance or charges_provenance == "Assumed standard statutory rates"):
        charges_provenance = (
            f"Statutory APMC market cess ({STANDARD_APMC_CESS_RATE*100:g}%) + "
            f"hamali labor (INR {STANDARD_HAMALI_PER_QUINTAL:g}/qtl) assumption applied"
        )

    # 2. Total Deductions (Deterministic sum)
    total_deductions = round(
        transport_cost + applicable_market_charges + storage_cost + estimated_loss_cost,
        2,
    )

    # 3. Expected Net Return (Strictly: gross_value - total_deductions)
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
        price_provenance=price_provenance,
        is_price_simulated=is_price_simulated,
        transport_provenance=transport_provenance,
        charges_provenance=charges_provenance,
        is_apmc_exempt=is_apmc_exempt,
        weather_loss_note=weather_loss_note,
        meets_minimum_condition=meets_minimum,
        is_feasible=is_feasible,
    )


def compute_weather_risk_details(
    gross_value: float,
    weather_report: WeatherRiskReport | None,
    tarp_cover_included: bool = True,
) -> tuple[float, str]:
    """
    Calculate estimated transit loss cost based on weather hazard severity,
    and return an explicit explanation connecting the weather forecast with the deduction.
    """
    if not weather_report:
        return 0.0, (
            "Weather forecast unavailable; spoilage risk could not be assessed. "
            "INR 0.00 is not a verified zero-loss estimate."
        )
    hazard = (weather_report.spoilage_hazard_level or "LOW").upper()
    if hazard == "HIGH":
        potential = round(gross_value * 0.035, 2)
        if tarp_cover_included:
            return (
                0.0,
                f"Mitigated: Waterproof tarpaulin cover included on vehicle prevents rain spoilage "
                f"(uncovered transit would risk ~3.5% / INR {potential:,.2f} loss)",
            )
        return (
            potential,
            f"High precipitation hazard: 3.5% transit rain spoilage deduction for uncovered transport (INR {potential:,.2f})",
        )
    elif hazard == "MEDIUM":
        potential = round(gross_value * 0.015, 2)
        if tarp_cover_included:
            return (
                0.0,
                f"Mitigated: Waterproof tarpaulin cover included on vehicle prevents precipitation/heat spoilage "
                f"(uncovered transit would risk ~1.5% / INR {potential:,.2f} loss)",
            )
        return (
            potential,
            f"Medium weather hazard: 1.5% transit spoilage deduction for uncovered transport (INR {potential:,.2f})",
        )
    else:
        return 0.0, "Low weather hazard; minimal transit spoilage risk (INR 0.00)"


def compute_weather_loss_risk(
    gross_value: float,
    weather_report: WeatherRiskReport | None,
    tarp_cover_included: bool = True,
) -> float:
    """
    Calculate estimated transit loss cost based on weather hazard severity.
    If waterproof tarpaulin cover is included, weather transit spoilage loss is 0.0.
    """
    loss, _ = compute_weather_risk_details(gross_value, weather_report, tarp_cover_included)
    return loss


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
    origin_location: str | None = None,
    target_district: str | None = None,
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
            if quote.weather_surcharge > 0:
                transport_provenance = (
                    f"Simulated freight quote ({quote.transporter_name}; includes INR {quote.weather_surcharge:,.2f} weather protection surcharge)"
                )
            else:
                transport_provenance = f"Simulated freight quote ({quote.transporter_name})"
        else:
            dist_km = estimate_route_distance_km(origin_location or "Nashik", mandi.mandi_name or mandi.district)
            trans_cost = round(500.0 + (dist_km * 22.0), 2)
            tarp_included = True
            transport_provenance = f"Standard route distance & freight tariff assumption ({dist_km:g} km @ INR 22/km + INR 500 base)"

        gross = round(quantity_quintals * price, 2)
        cess = round(gross * STANDARD_APMC_CESS_RATE, 2)
        hamali = round(quantity_quintals * STANDARD_HAMALI_PER_QUINTAL, 2)
        market_charges = round(cess + hamali, 2)

        loss_cost, weather_note = compute_weather_risk_details(
            gross, weather_report, tarp_cover_included=tarp_included
        )

        is_live = getattr(mandi, "data_source", None) == "data.gov.in_live"
        is_fallback_market = bool(
            target_district
            and mandi.district
            and mandi.district.strip().lower() != target_district.strip().lower()
        )
        if is_fallback_market:
            price_provenance = (
                f"Nearby/State APMC Benchmark ({mandi.mandi_name}, {mandi.district} - Arrival: {mandi.arrival_date}; "
                f"regional fallback because no live records were reported in {target_district})"
            )
        elif is_live:
            price_provenance = (
                f"Official APMC Mandi Modal Price ({mandi.mandi_name}, {mandi.district} - Arrival: {mandi.arrival_date}; live data.gov.in)"
            )
        else:
            price_provenance = (
                f"Official Benchmark Cache ({mandi.mandi_name}, {mandi.district} - Arrival: {mandi.arrival_date}; sample data)"
            )
        charges_provenance = (
            f"Statutory APMC market cess ({STANDARD_APMC_CESS_RATE*100:g}%) + hamali labor (INR {STANDARD_HAMALI_PER_QUINTAL:g}/qtl) assumption"
        )

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
            price_provenance=price_provenance,
            is_price_simulated=False,
            transport_provenance=transport_provenance,
            charges_provenance=charges_provenance,
            is_apmc_exempt=False,
            weather_loss_note=weather_note,
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
            transport_provenance = "Buyer farmgate collection assumption (no freight cost incurred by farmer; INR 0.00)"
        else:
            quote = quotes_map.get(buyer.buyer_name)
            if quote:
                trans_cost = quote.total_transport_cost
                dist_km = quote.distance_km
                tarp_included = quote.tarp_cover_included
                if quote.weather_surcharge > 0:
                    transport_provenance = f"Simulated freight quote ({quote.transporter_name}; includes INR {quote.weather_surcharge:,.2f} weather protection surcharge)"
                else:
                    transport_provenance = f"Simulated freight quote ({quote.transporter_name})"
            else:
                dist_km = estimate_route_distance_km(origin_location or "Nashik", buyer.buyer_name)
                trans_cost = round(400.0 + (dist_km * 20.0), 2)
                tarp_included = True
                transport_provenance = f"Standard regional hub transit tariff assumption ({dist_km:g} km @ INR 20/km + INR 400 base)"

        gross = round(quantity_quintals * price, 2)
        is_exempt = getattr(buyer, "is_apmc_exempt", True)
        if is_exempt:
            cess = 0.0
            hamali = 0.0
            market_charges = 0.0
            charges_provenance = (
                "Direct trade statutory exemption assumption from APMC cess & hamali "
                "(assumed direct-buyer trade outside market yard; unverified assumption, not legally verified)"
            )
        else:
            cess = round(gross * STANDARD_APMC_CESS_RATE, 2)
            hamali = round(quantity_quintals * STANDARD_HAMALI_PER_QUINTAL, 2)
            market_charges = round(cess + hamali, 2)
            charges_provenance = (
                f"Statutory APMC market cess ({STANDARD_APMC_CESS_RATE*100:g}%) + "
                f"hamali labor (INR {STANDARD_HAMALI_PER_QUINTAL:g}/qtl) assumption applied"
            )

        loss_cost, weather_note = compute_weather_risk_details(
            gross, weather_report, tarp_cover_included=tarp_included
        )

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
            mandi_cess_apmc=cess,
            loading_unloading_cost=hamali,
            price_provenance=f"Simulated benchmark offer ({buyer.buyer_name})",
            is_price_simulated=True,
            transport_provenance=transport_provenance,
            charges_provenance=charges_provenance,
            is_apmc_exempt=is_exempt,
            weather_loss_note=weather_note,
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
                f"Below minimum target: Estimated net return of INR {opt.expected_net_return:,.2f} "
                f"falls short of minimum threshold (INR {minimum_net_return_inr:,.2f}) by INR {shortfall:,.2f}."
            )
        elif rank_idx == 1:
            if opt.channel_type == "MANDI":
                opt.recommendation_note = (
                    f"Top Recommended Option: Highest estimated net return of INR {opt.expected_net_return:,.2f} "
                    f"(INR {opt.net_return_per_quintal:,.2f}/quintal) based on official APMC mandi benchmark "
                    f"after assumed transport and statutory deductions. Note: Advisory estimate only, not a guaranteed payout or buyer purchase offer."
                )
            else:
                ex_note = " with assumed direct-trade cess/labor exemption (unverified assumption)" if opt.is_apmc_exempt else ""
                opt.recommendation_note = (
                    f"Top Recommended Option: Highest estimated net return of INR {opt.expected_net_return:,.2f} "
                    f"(INR {opt.net_return_per_quintal:,.2f}/quintal) based on simulated buyer benchmark{ex_note} "
                    f"after assumed deductions. Note: Advisory estimate only, not a guaranteed payout or verified contract."
                )
        else:
            diff = round(sorted_options[0].expected_net_return - opt.expected_net_return, 2)
            opt.recommendation_note = (
                f"Rank {rank_idx}: Delivers estimated INR {opt.expected_net_return:,.2f} net "
                f"(INR {diff:,.2f} less than top option)."
            )

    feasible_options = [opt for opt in sorted_options if opt.is_feasible]
    recommended = sorted_options[0] if sorted_options else None

    # 4. Generate Narrative Ranking Explanation
    if not sorted_options:
        explanation = "No sales channels or buyer options were available for optimization."
    elif feasible_options:
        top = feasible_options[0]
        channel_desc = (
            f"APMC Wholesale Mandi ({top.destination_name}) — official benchmark price, NOT an actual buyer purchase offer"
            if top.channel_type == "MANDI"
            else f"Direct Buyer ({top.destination_name}) — simulated benchmark bid, NOT an actual commercial contract"
        )
        if top.is_apmc_exempt or (top.channel_type == "DIRECT_BUYER" and top.mandi_cess_apmc == 0.0):
            cess_line = (
                f"  - Statutory APMC Market Cess: INR 0.00 *(Assumed direct-buyer trade exemption outside APMC yard; unverified assumption, not legally verified)*\n"
            )
            hamali_line = (
                f"  - Mandi Hamali/Labor: INR 0.00 *(Assumed direct farmgate/buyer handling without APMC market labor charge; unverified assumption)*\n"
            )
        else:
            cess_line = (
                f"  - Statutory APMC Market Cess: INR {top.mandi_cess_apmc:,.2f} *(Assumed @ {STANDARD_APMC_CESS_RATE*100:g}% statutory tariff)*\n"
            )
            hamali_line = (
                f"  - Mandi Hamali/Labor: INR {top.loading_unloading_cost:,.2f} *(Assumed @ INR {STANDARD_HAMALI_PER_QUINTAL:g}/qtl standard labor charge)*\n"
            )

        explanation = (
            f"**Recommended Option (Rank 1): {top.destination_name}**\n\n"
            f"• **Channel Type:** {channel_desc}\n"
            f"• **Estimated Net In-Pocket Return:** INR {top.expected_net_return:,.2f} "
            f"(INR {top.net_return_per_quintal:,.2f} per quintal) — *Advisory estimate only, not a guaranteed payout*\n"
            f"• **Gross Value:** INR {top.gross_value:,.2f} ({top.quantity_quintals:g} qtl @ INR {top.offered_price_per_quintal:,.2f}/qtl)\n"
            f"• **Itemized Deductions (Assumed & Estimated):**\n"
            f"  - Transport Freight: INR {top.transport_cost:,.2f} *({top.transport_provenance})*\n"
            f"{cess_line}"
            f"{hamali_line}"
            f"  - Storage Fee: INR {top.storage_cost:,.2f} *(Identified storage assumption)*\n"
            f"  - Transit Weather Spoilage: INR {top.estimated_loss_cost:,.2f} *({top.weather_loss_note or 'Weather risk loss estimate'})*\n\n"
            f"**Rationale:** {top.destination_name} ranks #1 because "
        )
        if top.channel_type == "DIRECT_BUYER":
            if top.is_apmc_exempt:
                explanation += (
                    "direct farmgate procurement assumes exemption from APMC market cess and loading labor charges "
                    "(unverified assumption), yielding superior estimated net return for your harvest."
                )
            else:
                explanation += (
                    "its offered procurement price yields higher estimated net return despite applicable market and freight deductions."
                )
        else:
            explanation += (
                "its higher modal price per quintal generates sufficient revenue margin to outweigh "
                "assumed freight transport deductions and statutory APMC market fees."
            )
    else:
        top = sorted_options[0]
        shortfall = round(minimum_net_return_inr - top.expected_net_return, 2) if minimum_net_return_inr else 0.0
        explanation = (
            f"⚠️ **Attention: None of the {len(sorted_options)} available options met your minimum net threshold** "
            f"of INR {minimum_net_return_inr:,.2f}.\n\n"
            f"The best available option is **{top.destination_name}**, yielding an estimated net return of "
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
