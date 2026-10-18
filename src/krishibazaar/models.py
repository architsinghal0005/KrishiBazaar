"""
Pydantic Domain Models for KrishiBazaar.
Defines structured schemas for farmer inquiries, mandi evidence, weather risk,
buyer offers, transport quotes, deterministic net-return breakdowns, and booking records.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class FarmerInquiry(BaseModel):
    """Farmer inquiry parsed from conversation."""

    farmer_id: str = Field(..., description="Unique identifier or handle of the farmer")
    crop: str = Field(..., description="Target agricultural crop name (e.g. Onion, Wheat, Tomato)")
    quantity_quintals: float = Field(..., gt=0, description="Crop quantity in quintals (1 quintal = 100 kg)")
    location_district: str = Field(..., description="Farmer's home district or village")
    state: str = Field(default="Maharashtra", description="Indian State")
    max_distance_km: float = Field(default=150.0, gt=0, description="Maximum transport radius in km")
    created_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of the inquiry",
    )

    @field_validator("crop")
    @classmethod
    def normalize_crop_name(cls, v: str) -> str:
        return v.strip().title()

    @field_validator("location_district")
    @classmethod
    def normalize_district(cls, v: str) -> str:
        return v.strip().title()


class FarmerIntent(BaseModel):
    """
    Validated structured intent extracted from farmer's natural language.
    Includes completeness check and targeted follow-up questions for missing required fields.
    """

    crop: str | None = Field(default=None, description="Crop name (e.g. onion, wheat, tomato)")
    quantity_quintals: float | None = Field(
        default=None, gt=0, description="Harvest quantity in quintals"
    )
    origin_location: str | None = Field(
        default=None, description="Farmer village, mandi area, or district"
    )
    selling_deadline: str | None = Field(
        default=None, description="Preferred selling timeframe (e.g. tomorrow, 3 days)"
    )
    minimum_net_return_inr: float | None = Field(
        default=None, ge=0, description="Minimum acceptable net payout in INR"
    )
    storage_preference: str | None = Field(
        default=None, description="Optional storage preference (e.g. cold storage, dry shed)"
    )
    is_complete: bool = Field(
        default=False, description="True if all mandatory fields (crop, quantity, origin) are present"
    )
    missing_fields: list[str] = Field(
        default_factory=list, description="List of missing required fields"
    )
    follow_up_question: str | None = Field(
        default=None, description="Clarifying question for the farmer if info is missing"
    )
    extraction_source: str = Field(
        default="asi1", description="Extraction mechanism (asi1, rule_based_fallback)"
    )

    @field_validator("crop")
    @classmethod
    def clean_crop(cls, v: str | None) -> str | None:
        if v and isinstance(v, str):
            cleaned = v.strip().lower()
            return cleaned if cleaned not in ("null", "none", "") else None
        return None

    @field_validator("origin_location")
    @classmethod
    def clean_origin(cls, v: str | None) -> str | None:
        if v and isinstance(v, str):
            cleaned = v.strip().title()
            return cleaned if cleaned.lower() not in ("null", "none", "") else None
        return None

    def evaluate_completeness(self) -> FarmerIntent:
        """Evaluate missing fields and generate a farmer-friendly follow-up question."""
        missing = []
        if not self.crop:
            missing.append("crop")
        if self.quantity_quintals is None or self.quantity_quintals <= 0:
            missing.append("quantity_quintals")
        if not self.origin_location:
            missing.append("origin_location")

        self.missing_fields = missing
        self.is_complete = len(missing) == 0

        if not self.is_complete:
            if "crop" in missing and "quantity_quintals" in missing and "origin_location" in missing:
                self.follow_up_question = (
                    "Namaste! Could you please tell me which crop you want to sell, "
                    "the approximate quantity in quintals, and your village or district?"
                )
            elif "crop" in missing:
                self.follow_up_question = (
                    "Which agricultural crop or produce are you selling (e.g. Onion, Wheat, Tomato, Soybean)?"
                )
            elif "quantity_quintals" in missing and "origin_location" in missing:
                self.follow_up_question = (
                    f"Got it for {self.crop.title()}! How many quintals do you have to sell, "
                    "and what is your farm location (district or village)?"
                )
            elif "quantity_quintals" in missing:
                self.follow_up_question = (
                    f"How many quintals of {self.crop.title()} are you looking to sell? "
                    "(1 quintal = 100 kg)"
                )
            elif "origin_location" in missing:
                self.follow_up_question = (
                    f"Where is your farm located (district or village) to check nearby mandis and freight options?"
                )
            else:
                missing_str = ", ".join(missing).replace("_", " ")
                self.follow_up_question = (
                    f"Could you please clarify the following detail(s): {missing_str}?"
                )
        else:
            self.follow_up_question = None

        return self


class MandiPriceRecord(BaseModel):
    """Mandi price evidence record with provenance metadata."""

    mandi_name: str = Field(..., description="Name of the APMC mandi")
    district: str = Field(..., description="Mandi district")
    state: str = Field(..., description="Mandi state")
    commodity: str = Field(..., description="Commodity name")
    variety: str = Field(default="General", description="Commodity variety/grade")
    arrival_date: str = Field(..., description="Date of price reporting (YYYY-MM-DD)")
    min_price: float = Field(..., ge=0, description="Minimum price in INR per quintal")
    max_price: float = Field(..., ge=0, description="Maximum price in INR per quintal")
    modal_price: float = Field(..., ge=0, description="Modal (most frequent) price in INR per quintal")
    data_source: Literal["data.gov.in_live", "official_benchmark_cache"] = Field(
        ..., description="Provenance source of the mandi record"
    )
    source_disclaimer: str = Field(
        default="Official mandi pricing report.",
        description="Transparency disclaimer",
    )


class WeatherRiskReport(BaseModel):
    """Open-Meteo weather forecast and agricultural transit hazard evaluation."""

    location: str = Field(..., description="Target location name or district")
    forecast_date: str = Field(..., description="Forecast date (YYYY-MM-DD)")
    temperature_max_c: float = Field(..., description="Maximum temperature in Celsius")
    temperature_min_c: float = Field(..., description="Minimum temperature in Celsius")
    precipitation_sum_mm: float = Field(..., ge=0, description="Total expected precipitation in mm")
    precipitation_probability_pct: float = Field(
        ..., ge=0, le=100, description="Probability of precipitation (0-100%)"
    )
    wind_speed_max_kmh: float = Field(..., ge=0, description="Maximum wind gust speed in km/h")
    spoilage_hazard_level: Literal["LOW", "MEDIUM", "HIGH"] = Field(
        ..., description="Weather hazard assessment for crop transit"
    )
    transit_advice: str = Field(..., description="Actionable advice for farmer/transporter")
    data_source: str = Field(default="Open-Meteo API", description="Data provider")


class BuyerOffer(BaseModel):
    """Institutional or private buyer procurement offer."""

    offer_id: str = Field(..., description="Unique offer identifier")
    buyer_id: str = Field(..., description="Unique buyer identifier")
    buyer_name: str = Field(..., description="Company or aggregator name")
    buyer_type: str = Field(..., description="Type of buyer (e.g., FPO, Agro-Processor, Retail Chain)")
    crop: str = Field(..., description="Target crop")
    quantity_quintals: float = Field(..., gt=0, description="Quantity buyer is willing to buy")
    offered_price_per_quintal: float = Field(..., gt=0, description="Nominal offer price per quintal in INR")
    farmgate_pickup: bool = Field(
        default=False,
        description="True if buyer collects at farmer's gate (zero farmer transport)",
    )
    quality_grade: str = Field(default="FAQ (Fair Average Quality)", description="Required quality grade")
    payment_terms: str = Field(default="T+0 Instant Bank Transfer", description="Payment settlement timeline")
    validity_hours: int = Field(default=24, gt=0, description="Offer validity window in hours")
    is_simulated: bool = Field(
        default=True,
        description="Strict compliance flag: Must be True for benchmark/sample offers",
    )
    disclaimer: str = Field(
        default="Simulated benchmark buyer offer for negotiation comparison.",
        description="Strict transparency disclaimer",
    )


class TransportQuote(BaseModel):
    """Freight quote for transporting agricultural produce."""

    quote_id: str = Field(..., description="Unique quote identifier")
    transporter_id: str = Field(..., description="Unique transporter identifier")
    transporter_name: str = Field(..., description="Transporter or fleet name")
    vehicle_type: str = Field(
        ..., description="Vehicle type (e.g., 1-Tonne Pickup, 3-Tonne Mini-Truck)"
    )
    capacity_quintals: float = Field(..., gt=0, description="Payload capacity in quintals")
    route_origin: str = Field(..., description="Farmer pickup location")
    route_destination: str = Field(..., description="Delivery destination")
    distance_km: float = Field(..., ge=0, description="Travel distance in km")
    base_fare: float = Field(..., ge=0, description="Fixed base fare in INR")
    rate_per_km: float = Field(..., ge=0, description="Variable rate per km in INR")
    weather_surcharge: float = Field(default=0.0, ge=0, description="Hazard/rain protection surcharge in INR")
    tarp_cover_included: bool = Field(default=True, description="Waterproof tarpaulin cover included")
    total_transport_cost: float = Field(..., ge=0, description="Total transport cost in INR")
    estimated_transit_hours: float = Field(..., gt=0, description="Estimated transit time in hours")


class NetReturnBreakdown(BaseModel):
    """
    Deterministic net financial return calculation for a specific sale channel.
    Calculated via pure Python business logic in optimizer.py.
    """

    scenario_id: str = Field(..., description="Scenario identifier")
    channel_type: Literal["MANDI", "DIRECT_BUYER"] = Field(..., description="Channel type")
    destination_name: str = Field(..., description="Name of the mandi or buyer hub")
    distance_km: float = Field(default=0.0, ge=0, description="Transit distance from farm in km")
    gross_revenue: float = Field(..., ge=0, description="Quantity * Offered Price (INR)")
    offered_price_per_quintal: float = Field(..., ge=0, description="Nominal price per quintal in INR")
    transport_cost: float = Field(default=0.0, ge=0, description="Transport deduction in INR")
    mandi_cess_apmc: float = Field(default=0.0, ge=0, description="APMC / Market cess fee in INR")
    loading_unloading_cost: float = Field(default=0.0, ge=0, description="Hamali/loading labor cost in INR")
    spoilage_risk_penalty: float = Field(
        default=0.0, ge=0, description="Weather-adjusted transit risk penalty in INR"
    )
    total_deductions: float = Field(..., ge=0, description="Sum of all cost deductions in INR")
    net_return: float = Field(..., description="Gross Revenue minus Total Deductions in INR")
    net_return_per_quintal: float = Field(
        ..., description="Net return divided by crop quantity (INR/quintal)"
    )
    rank: int = Field(default=1, ge=1, description="Rank ordered by net return (1 = best)")
    recommendation_note: str = Field(default="", description="Key rationale explaining the ranking")


class BookingRequest(BaseModel):
    """Booking execution request requiring explicit farmer approval."""

    farmer_id: str = Field(..., description="Farmer identifier")
    offer_id: str = Field(..., description="Selected offer or mandi scenario ID")
    quote_id: str | None = Field(default=None, description="Selected transport quote ID if applicable")
    crop: str = Field(..., description="Crop name")
    quantity_quintals: float = Field(..., gt=0, description="Booking quantity in quintals")
    pickup_address: str = Field(..., description="Farmer's farm address for pickup")
    delivery_destination: str = Field(..., description="Delivery destination")
    expected_net_payout: float = Field(..., description="Expected net payout in INR")
    agreed_by_farmer: bool = Field(
        ...,
        description="Explicit approval flag. Must be True to execute booking.",
    )


class BookingRecord(BaseModel):
    """Immutable persistent booking record stored in SQLite ledger."""

    booking_id: str = Field(..., description="Unique booking tracking ID (e.g. KB-BOOK-...)")
    timestamp_utc: str = Field(..., description="ISO 8601 UTC timestamp of creation")
    status: Literal["PENDING_CONFIRMATION", "CONFIRMED", "DISPATCHED", "COMPLETED", "CANCELLED"] = Field(
        default="CONFIRMED", description="Current status of the booking"
    )
    farmer_id: str = Field(..., description="Farmer identifier")
    offer_id: str = Field(..., description="Selected offer identifier")
    quote_id: str | None = Field(default=None, description="Selected transport quote ID")
    channel_name: str = Field(..., description="Buyer or Mandi name")
    transporter_name: str = Field(..., description="Assigned transporter name")
    crop: str = Field(..., description="Crop name")
    quantity_quintals: float = Field(..., gt=0, description="Quantity booked")
    gross_revenue: float = Field(..., ge=0, description="Gross value in INR")
    transport_cost: float = Field(default=0.0, ge=0, description="Transport deduction in INR")
    other_deductions: float = Field(default=0.0, ge=0, description="APMC cess and labor deductions in INR")
    net_payout: float = Field(..., description="Net payout to farmer in INR")
    pickup_date: str = Field(..., description="Scheduled pickup date (YYYY-MM-DD)")
    tracking_token: str = Field(..., description="Cryptographic verification token")
    is_simulated_booking: bool = Field(
        default=True,
        description="Strict compliance flag: Must be True for test/simulated bookings",
    )
    notes: str = Field(default="", description="Audit notes and instructions")
