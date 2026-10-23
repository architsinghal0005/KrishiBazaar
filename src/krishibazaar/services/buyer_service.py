"""
Buyer Service for KrishiBazaar.
Generates structured, simulated benchmark buyer procurement offers for the MVP.

Strict Compliance Requirements:
- Every offer is explicitly flagged with `is_simulated=True`.
- Every offer contains a clear simulation disclaimer stating it is a benchmark estimate.
- Never implies a real commercial buyer made an offer.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from uuid import uuid4

from krishibazaar.models import BuyerOffer, BuyerOffersResult, DataProvenance

logger = logging.getLogger("krishibazaar.buyer_service")

# Approximate crop benchmark price baseline (INR per quintal)
CROP_BASE_PRICES: dict[str, float] = {
    "onion": 3200.0,
    "tomato": 2200.0,
    "potato": 1600.0,
    "wheat": 2400.0,
    "soybean": 4600.0,
    "mustard": 5300.0,
    "cotton": 7200.0,
}


def _get_base_price(crop: str, price_hint: float | None = None) -> float:
    """Determine baseline price for crop, using price hint if available."""
    if price_hint and price_hint > 0:
        return price_hint

    cleaned = crop.strip().lower()
    for key, price in CROP_BASE_PRICES.items():
        if key in cleaned:
            return price
    return 2500.0


def generate_simulated_buyer_offers(
    crop: str,
    quantity_quintals: float,
    origin_location: str,
    price_hint: float | None = None,
    profile: str = "buyer_a",
) -> BuyerOffersResult:
    """
    Generate realistic, structured benchmark buyer offers for negotiation comparison.
    Supports distinct profiles for Buyer A (regional aggregators & retail hubs)
    and Buyer B (institutional & export procurement).
    Every offer is strictly labeled as simulated.
    """
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    is_buyer_b = profile.lower() == "buyer_b"

    source_label = (
        "KrishiBazaar Simulated Buyer B Network (Institutional & Export Procurement)"
        if is_buyer_b
        else "KrishiBazaar Simulated Buyer Network"
    )
    disclaimer_label = (
        "Simulated benchmark buyer offers from Buyer B network for MVP comparison. No real buyer committed."
        if is_buyer_b
        else "Simulated benchmark buyer offers for MVP negotiation modeling. No real buyer committed."
    )

    if quantity_quintals <= 0:
        return BuyerOffersResult(
            success=False,
            crop=crop,
            quantity_quintals=quantity_quintals,
            offers=[],
            error_message=f"Quantity must be greater than zero, got {quantity_quintals}",
            provenance=DataProvenance(
                source=source_label,
                retrieval_timestamp_utc=now_utc.isoformat(),
                observation_date=today_str,
                freshness_status="SIMULATED_SAMPLE",
                is_live=False,
                disclaimer=disclaimer_label,
            ),
        )

    base_price = _get_base_price(crop, price_hint)
    crop_title = crop.strip().title()

    if is_buyer_b:
        # Distinct Buyer B Profile: Institutional, Corporate Agribusiness & Export Procurement
        # Offer 1: Export Commodity House (High Quality Premium)
        exp_price = round(base_price * 1.07, 2)
        offer_exp = BuyerOffer(
            offer_id=f"BO-SIM-{uuid4().hex[:6].upper()}-EXP",
            buyer_id="BUYER-B-EXP-01",
            buyer_name="Kalyani Agro-Exports Pvt Ltd",
            buyer_type="Export Aggregator",
            crop=crop_title,
            quantity_quintals=quantity_quintals,
            offered_price_per_quintal=exp_price,
            farmgate_pickup=False,  # Deliver to export terminal / container freight station
            quality_grade="Grade A Export Sorted (GlobalGAP certified)",
            payment_terms="T+1 Verified Bank Settlement",
            validity_hours=24,
            is_simulated=True,
            disclaimer="Simulated benchmark buyer offer from Buyer B (Export Procurement). Not a real commercial bid.",
        )

        # Offer 2: Corporate Farmgate Procurement (Instant e-Choupal Settlement)
        itc_price = round(base_price * 1.03, 2)
        offer_itc = BuyerOffer(
            offer_id=f"BO-SIM-{uuid4().hex[:6].upper()}-ITC",
            buyer_id="BUYER-B-INST-02",
            buyer_name="ITC Choupal Saagar Rural Procurement",
            buyer_type="Corporate Agribusiness",
            crop=crop_title,
            quantity_quintals=quantity_quintals,
            offered_price_per_quintal=itc_price,
            farmgate_pickup=True,  # Farmgate collection included
            quality_grade="FAQ (Fair Average Quality)",
            payment_terms="T+0 Instant e-Choupal Account Transfer",
            validity_hours=48,
            is_simulated=True,
            disclaimer="Simulated benchmark buyer offer from Buyer B (Corporate Farmgate). Not a real commercial bid.",
        )

        # Offer 3: Agro-Processing Unit (High Solids Dehydration Grade)
        proc_price = round(base_price * 1.05, 2)
        offer_proc = BuyerOffer(
            offer_id=f"BO-SIM-{uuid4().hex[:6].upper()}-GOD",
            buyer_id="BUYER-B-PROC-03",
            buyer_name="Godavari Organic Agro-Processing Unit",
            buyer_type="Agro-Processing Unit",
            crop=crop_title,
            quantity_quintals=quantity_quintals,
            offered_price_per_quintal=proc_price,
            farmgate_pickup=False,
            quality_grade="Processing & Dehydration Standard",
            payment_terms="T+2 Secure Trade ESCROW",
            validity_hours=36,
            is_simulated=True,
            disclaimer="Simulated benchmark buyer offer from Buyer B (Agro-Processing). Not a real commercial bid.",
        )

        offers = [offer_exp, offer_itc, offer_proc]

    else:
        # Standard Buyer A Profile: Regional FPO, Organized Retail Hub & Processing Mill
        # Offer 1: Local Farmer Producer Organisation (FPO) with Farmgate Collection
        fpo_price = round(base_price * 0.98, 2)
        offer_fpo = BuyerOffer(
            offer_id=f"BO-SIM-{uuid4().hex[:6].upper()}-FPO",
            buyer_id="BUYER-FPO-MAHA-01",
            buyer_name="Sahyadri Agro Kisan Producer Co. (FPO)",
            buyer_type="FPO Aggregator",
            crop=crop_title,
            quantity_quintals=quantity_quintals,
            offered_price_per_quintal=fpo_price,
            farmgate_pickup=True,  # Zero transport cost for farmer!
            quality_grade="FAQ (Fair Average Quality)",
            payment_terms="T+0 Instant Direct Bank Transfer",
            validity_hours=24,
            is_simulated=True,
            disclaimer="Simulated benchmark buyer offer for MVP negotiation modeling. Not a real commercial bid.",
        )

        # Offer 2: Organized Retail Chain / Agri-Commerce Platform (Premium Graded Quality)
        retail_price = round(base_price * 1.06, 2)
        offer_retail = BuyerOffer(
            offer_id=f"BO-SIM-{uuid4().hex[:6].upper()}-RTL",
            buyer_id="BUYER-RETAIL-HUB-02",
            buyer_name="Reliance Fresh Agro Aggregation Hub",
            buyer_type="Organized Retail Hub",
            crop=crop_title,
            quantity_quintals=quantity_quintals,
            offered_price_per_quintal=retail_price,
            farmgate_pickup=False,  # Farmer or transporter delivers to regional hub (~25 km)
            quality_grade="Grade A / Export Sorted",
            payment_terms="T+1 Verified Bank Settlement",
            validity_hours=36,
            is_simulated=True,
            disclaimer="Simulated benchmark buyer offer for MVP negotiation modeling. Not a real commercial bid.",
        )

        # Offer 3: Agro-Processing Industry / Mill (Bulk Processing Volume)
        mill_price = round(base_price * 1.02, 2)
        offer_mill = BuyerOffer(
            offer_id=f"BO-SIM-{uuid4().hex[:6].upper()}-MIL",
            buyer_id="BUYER-MILL-PROC-03",
            buyer_name="Maha Food Processing & Dehydration Mills",
            buyer_type="Food Processing Mill",
            crop=crop_title,
            quantity_quintals=quantity_quintals,
            offered_price_per_quintal=mill_price,
            farmgate_pickup=False,
            quality_grade="Industrial Processing Grade",
            payment_terms="T+2 Secure Trade ESCROW",
            validity_hours=48,
            is_simulated=True,
            disclaimer="Simulated benchmark buyer offer for MVP negotiation modeling. Not a real commercial bid.",
        )

        offers = [offer_fpo, offer_retail, offer_mill]

    return BuyerOffersResult(
        success=True,
        crop=crop_title,
        quantity_quintals=quantity_quintals,
        offers=offers,
        provenance=DataProvenance(
            source=source_label,
            retrieval_timestamp_utc=now_utc.isoformat(),
            observation_date=today_str,
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
            disclaimer=disclaimer_label,
        ),
    )


async def get_buyer_offers_async(
    crop: str,
    quantity_quintals: float,
    origin_location: str,
    price_hint: float | None = None,
    profile: str = "buyer_a",
) -> BuyerOffersResult:
    """Async wrapper for generating simulated buyer offers."""
    return generate_simulated_buyer_offers(
        crop=crop,
        quantity_quintals=quantity_quintals,
        origin_location=origin_location,
        price_hint=price_hint,
        profile=profile,
    )


def get_buyer_offers(
    crop: str,
    quantity_quintals: float,
    origin_location: str,
    price_hint: float | None = None,
    profile: str = "buyer_a",
) -> BuyerOffersResult:
    """Synchronous wrapper for generating simulated buyer offers."""
    return generate_simulated_buyer_offers(
        crop=crop,
        quantity_quintals=quantity_quintals,
        origin_location=origin_location,
        price_hint=price_hint,
        profile=profile,
    )
