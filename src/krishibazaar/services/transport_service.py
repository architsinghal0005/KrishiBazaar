"""
Transport Service for KrishiBazaar.
Generates vehicle-matched, weather-aware simulated freight quotes for the MVP.

Strict Compliance Requirements:
- Every freight quote is explicitly flagged with `is_simulated=True`.
- Every quote contains a clear simulation disclaimer stating it is a benchmark estimate.
- Clearly marks quotes as simulated, not verified quotations or carrier bookings.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from uuid import uuid4

from krishibazaar.models import (
    DataProvenance,
    TransportQuote,
    TransportQuotesResult,
    WeatherRiskReport,
)

logger = logging.getLogger("krishibazaar.transport_service")

# Curated inter-district road distances in Maharashtra (km)
DISTANCE_REGISTRY: dict[tuple[str, str], float] = {
    ("nashik", "pimpalgaon"): 32.0,
    ("nashik", "lasalgaon"): 58.0,
    ("nashik", "yeola"): 82.0,
    ("nashik", "pune"): 210.0,
    ("nashik", "mumbai"): 165.0,
    ("nashik", "ahilyanagar"): 155.0,
    ("pune", "mumbai"): 148.0,
    ("pune", "ahilyanagar"): 122.0,
    ("pune", "solapur"): 250.0,
    ("ahilyanagar", "newasa"): 55.0,
    ("ahilyanagar", "shrirampur"): 75.0,
    ("ahilyanagar", "pune"): 122.0,
}


def estimate_route_distance_km(origin: str, destination: str) -> float:
    """Estimate road transit distance in kilometers between origin and destination."""
    orig_clean = origin.strip().lower()
    dest_clean = destination.strip().lower()

    # Same location / local APMC in same town
    if orig_clean in dest_clean or dest_clean in orig_clean:
        return 18.0

    # Check known pairwise distance registry
    for (loc1, loc2), dist in DISTANCE_REGISTRY.items():
        if (loc1 in orig_clean and loc2 in dest_clean) or (loc2 in orig_clean and loc1 in dest_clean):
            return dist

    # Conservative default for intra-district / adjacent mandi transit
    return 35.0


def select_vehicle_for_quantity(quantity_quintals: float) -> tuple[str, float, float, float]:
    """
    Select vehicle type and freight tariff based on harvest payload.
    Returns: (vehicle_type, capacity_quintals, base_fare, rate_per_km)
    """
    if quantity_quintals <= 10.0:
        return (
            "Tata Ace / 1-Tonne Mini Pickup",
            10.0,
            400.0,
            18.0,
        )
    elif quantity_quintals <= 35.0:
        return (
            "Mahindra Bolero Maxi Truck / 3-Tonne LCV",
            35.0,
            600.0,
            22.0,
        )
    elif quantity_quintals <= 75.0:
        return (
            "Eicher Pro 6-Tonne Commercial Truck",
            75.0,
            1000.0,
            28.0,
        )
    else:
        return (
            "10-Tonne Multi-Axle Freight Truck",
            120.0,
            1600.0,
            34.0,
        )


def generate_simulated_transport_quotes(
    origin: str,
    destinations: list[str],
    quantity_quintals: float,
    weather_report: WeatherRiskReport | None = None,
) -> TransportQuotesResult:
    """
    Generate vehicle-capacity matched, weather-aware simulated freight quotes.
    All quotes are strictly marked as simulated benchmarks.
    """
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")

    if quantity_quintals <= 0:
        return TransportQuotesResult(
            success=False,
            origin=origin,
            quantity_quintals=quantity_quintals,
            quotes={},
            error_message=f"Quantity must be greater than zero, got {quantity_quintals}",
            provenance=DataProvenance(
                source="KrishiBazaar Simulated Logistics Network",
                retrieval_timestamp_utc=now_utc.isoformat(),
                observation_date=today_str,
                freshness_status="SIMULATED_SAMPLE",
                is_live=False,
                disclaimer="Simulated freight quotes for MVP route comparison.",
            ),
        )

    vehicle_type, capacity, base_fare, rate_per_km = select_vehicle_for_quantity(quantity_quintals)

    # Weather surcharge calculation
    weather_surcharge = 0.0
    if weather_report:
        if weather_report.spoilage_hazard_level == "HIGH":
            weather_surcharge = 250.0  # Weatherproofing tarp tie-downs during heavy rain
        elif weather_report.spoilage_hazard_level == "MEDIUM":
            weather_surcharge = 100.0

    quotes: dict[str, TransportQuote] = {}

    for dest in destinations:
        dist_km = estimate_route_distance_km(origin, dest)
        total_cost = round(base_fare + (dist_km * rate_per_km) + weather_surcharge, 2)
        transit_hours = max(0.5, round(dist_km / 35.0, 1))

        quote = TransportQuote(
            quote_id=f"TQ-SIM-{uuid4().hex[:6].upper()}",
            transporter_id="TRANS-MAHA-KISAN-01",
            transporter_name="Kisan Gati Logistics (Simulated Fleet)",
            vehicle_type=vehicle_type,
            capacity_quintals=capacity,
            route_origin=origin.strip().title(),
            route_destination=dest.strip(),
            distance_km=dist_km,
            base_fare=base_fare,
            rate_per_km=rate_per_km,
            weather_surcharge=weather_surcharge,
            tarp_cover_included=True,
            total_transport_cost=total_cost,
            estimated_transit_hours=transit_hours,
            is_simulated=True,
            disclaimer="Simulated freight quote for MVP route estimation. Not a verified carrier booking.",
        )
        quotes[dest] = quote

    return TransportQuotesResult(
        success=True,
        origin=origin.strip().title(),
        quantity_quintals=quantity_quintals,
        quotes=quotes,
        provenance=DataProvenance(
            source="KrishiBazaar Simulated Logistics Network",
            retrieval_timestamp_utc=now_utc.isoformat(),
            observation_date=today_str,
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
            disclaimer="Simulated freight quotes for MVP route estimation. Not a verified carrier booking.",
        ),
    )


async def get_transport_quotes_async(
    origin: str,
    destinations: list[str],
    quantity_quintals: float,
    weather_report: WeatherRiskReport | None = None,
) -> TransportQuotesResult:
    """Async wrapper for generating simulated transport quotes."""
    return generate_simulated_transport_quotes(
        origin=origin,
        destinations=destinations,
        quantity_quintals=quantity_quintals,
        weather_report=weather_report,
    )


def get_transport_quotes(
    origin: str,
    destinations: list[str],
    quantity_quintals: float,
    weather_report: WeatherRiskReport | None = None,
) -> TransportQuotesResult:
    """Synchronous wrapper for generating simulated transport quotes."""
    return generate_simulated_transport_quotes(
        origin=origin,
        destinations=destinations,
        quantity_quintals=quantity_quintals,
        weather_report=weather_report,
    )
