"""
Pydantic Domain Models for KrishiBazaar.
Defines structured schemas for farmer inquiries, mandi evidence, weather risk,
buyer offers, transport quotes, deterministic net-return breakdowns, and booking records.
"""

from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


class FarmerInquiry(BaseModel):
    """Farmer inquiry parsed from conversation."""

    farmer_id: str = Field(..., description="Unique identifier or handle of the farmer")
    crop: str | None = Field(default=None, description="Target agricultural crop name (e.g. Onion, Wheat, Tomato)")
    quantity_quintals: float | None = Field(default=None, gt=0, description="Crop quantity in quintals (1 quintal = 100 kg)")
    location_district: str | None = Field(default=None, description="Farmer's home district or village")
    state: str = Field(default="Maharashtra", description="Indian State")
    max_distance_km: float = Field(default=150.0, gt=0, description="Maximum transport radius in km")
    selling_deadline: str | None = Field(default=None, description="Preferred selling timeframe or deadline")
    minimum_net_return_inr: float | None = Field(default=None, description="Farmer target net payout in INR")
    storage_preference: str | None = Field(default=None, description="Optional storage preference")
    origin_location: str | None = Field(default=None, description="Original origin location query")
    created_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of the inquiry",
    )

    @field_validator("crop")
    @classmethod
    def normalize_crop_name(cls, v: str | None) -> str | None:
        return v.strip().title() if v else None

    @field_validator("location_district")
    @classmethod
    def normalize_district(cls, v: str | None) -> str | None:
        return v.strip().title() if v else None


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
    arrival_date: str = Field(..., description="Date of price reporting (YYYY-MM-DD or DD/MM/YYYY)")
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
    is_suspicious: bool = Field(
        default=False,
        description="True if price is anomalous, unit mismatch, or clerical entry error",
    )
    suspicion_reason: str | None = Field(
        default=None,
        description="Explanation if price record was flagged as suspicious",
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
    target_date_requested: str | None = Field(
        default=None, description="Requested selling or transit date"
    )
    is_target_date_matched: bool = Field(
        default=True, description="True if forecast_date matches requested target date"
    )


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
    is_apmc_exempt: bool = Field(
        default=True,
        description="Whether direct buyer trade is assumed exempt from APMC cess and mandi hamali",
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
    is_simulated: bool = Field(
        default=True,
        description="Strict compliance flag: Must be True for benchmark/sample quotes",
    )
    disclaimer: str = Field(
        default="Simulated freight quote for MVP estimation. Not a verified carrier booking.",
        description="Strict transparency disclaimer",
    )


class DataProvenance(BaseModel):
    """Metadata tracking data origin, observation date, freshness, and live vs simulated status."""

    source: str = Field(..., description="Data provider or dataset name")
    retrieval_timestamp_utc: str = Field(..., description="ISO 8601 UTC timestamp of retrieval")
    observation_date: str = Field(..., description="Date of observation or forecast (YYYY-MM-DD)")
    freshness_status: Literal["LIVE", "LIVE_NO_RECORDS", "RECENT", "STALE", "SIMULATED_SAMPLE"] = Field(
        ..., description="Data freshness state"
    )
    is_live: bool = Field(default=False, description="True only if retrieved from live official external API")
    disclaimer: str = Field(default="", description="Provenance or simulation disclaimer")


class MandiPriceResult(BaseModel):
    """Typed result wrapper for Mandi price retrieval."""

    success: bool = Field(..., description="True if query succeeded")
    commodity: str = Field(..., description="Commodity searched")
    records: list[MandiPriceRecord] = Field(default_factory=list, description="Mandi price records")
    error_message: str | None = Field(default=None, description="Error message if failed")
    provenance: DataProvenance = Field(..., description="Data provenance and freshness")
    request_id: str | None = Field(default=None, description="Correlation / request ID for end-to-end tracing")


class WeatherResult(BaseModel):
    """Typed result wrapper for Open-Meteo weather forecast retrieval."""

    success: bool = Field(..., description="True if weather query succeeded")
    location: str = Field(..., description="Location name or district")
    latitude: float | None = Field(default=None, description="Latitude used")
    longitude: float | None = Field(default=None, description="Longitude used")
    report: WeatherRiskReport | None = Field(default=None, description="Detailed weather and transit hazard report")
    error_message: str | None = Field(default=None, description="Error details if query failed")
    provenance: DataProvenance = Field(..., description="Data provenance and freshness")
    request_id: str | None = Field(default=None, description="Correlation / request ID for end-to-end tracing")


class BuyerOffersResult(BaseModel):
    """Typed result wrapper for Buyer Offer retrieval."""

    success: bool = Field(..., description="True if query succeeded")
    crop: str = Field(..., description="Target crop")
    quantity_quintals: float = Field(default=0.0, description="Quantity in quintals")
    offers: list[BuyerOffer] = Field(default_factory=list, description="List of buyer offers")
    error_message: str | None = Field(default=None, description="Error details if query failed")
    provenance: DataProvenance = Field(..., description="Data provenance and simulation status")
    request_id: str | None = Field(default=None, description="Correlation / request ID for end-to-end tracing")


class TransportQuotesResult(BaseModel):
    """Typed result wrapper for Transport Quotes retrieval."""

    success: bool = Field(..., description="True if query succeeded")
    origin: str = Field(..., description="Farmer origin location")
    quantity_quintals: float = Field(default=0.0, description="Quantity in quintals")
    quotes: dict[str, TransportQuote] = Field(
        default_factory=dict, description="Destination to TransportQuote map"
    )
    error_message: str | None = Field(default=None, description="Error details if query failed")
    provenance: DataProvenance = Field(..., description="Data provenance and simulation status")
    request_id: str | None = Field(default=None, description="Correlation / request ID for end-to-end tracing")


class MarketQuery(BaseModel):
    """Query message for Market specialist agent."""

    commodity: str = Field(..., description="Target agricultural crop name")
    district: str | None = Field(default=None, description="Target district")
    state: str | None = Field(default="Maharashtra", description="Target state")
    request_id: str | None = Field(default=None, description="Correlation / request ID for end-to-end tracing")


class WeatherQuery(BaseModel):
    """Query message for Weather specialist agent."""

    location: str = Field(..., description="Farmer or mandi location")
    target_date: str | None = Field(
        default=None, description="Requested transit or selling deadline date (YYYY-MM-DD)"
    )
    latitude: float | None = Field(default=None, description="Latitude")
    longitude: float | None = Field(default=None, description="Longitude")
    request_id: str | None = Field(default=None, description="Correlation / request ID for end-to-end tracing")


class BuyerQuery(BaseModel):
    """Query message for Buyer specialist agent."""

    crop: str = Field(..., description="Target crop")
    quantity_quintals: float = Field(..., gt=0, description="Quantity in quintals")
    origin_location: str = Field(..., description="Farmer origin location")
    price_hint: float | None = Field(default=None, description="Optional baseline price hint in INR")
    request_id: str | None = Field(default=None, description="Correlation / request ID for end-to-end tracing")


class TransportQuery(BaseModel):
    """Query message for Transport specialist agent."""

    origin: str = Field(..., description="Farmer origin location")
    destinations: list[str] = Field(..., description="List of candidate destinations")
    quantity_quintals: float = Field(..., gt=0, description="Quantity in quintals")
    weather_hazard_level: str = Field(default="LOW", description="Weather hazard level (LOW/MEDIUM/HIGH)")
    request_id: str | None = Field(default=None, description="Correlation / request ID for end-to-end tracing")


class NetReturnBreakdown(BaseModel):
    """
    Deterministic net financial return calculation for a specific sale channel.
    Formulas:
        gross_value = quantity_quintals * price_per_quintal
        expected_net_return = gross_value - transport_cost - applicable_market_charges - storage_cost - estimated_loss_cost
    """

    scenario_id: str = Field(..., description="Scenario identifier")
    channel_type: Literal["MANDI", "DIRECT_BUYER"] = Field(..., description="Channel type")
    destination_name: str = Field(..., description="Name of the mandi or buyer hub")
    distance_km: float = Field(default=0.0, ge=0, description="Transit distance from farm in km")
    quantity_quintals: float = Field(default=1.0, gt=0, description="Crop quantity in quintals")
    offered_price_per_quintal: float = Field(..., ge=0, description="Price per quintal in INR")

    # Formula components
    gross_value: float = Field(default=0.0, ge=0, description="Gross value = quantity * price (INR)")
    gross_revenue: float = Field(default=0.0, ge=0, description="Alias for gross_value for backward compatibility")
    transport_cost: float = Field(default=0.0, ge=0, description="Transport deduction in INR")
    applicable_market_charges: float = Field(
        default=0.0, ge=0, description="APMC cess + mandi hamali/labor in INR"
    )
    mandi_cess_apmc: float = Field(default=0.0, ge=0, description="APMC fee component in INR")
    loading_unloading_cost: float = Field(default=0.0, ge=0, description="Labor/hamali component in INR")
    storage_cost: float = Field(default=0.0, ge=0, description="Storage fee in INR (separately identifiable)")
    estimated_loss_cost: float = Field(
        default=0.0, ge=0, description="Weather-adjusted spoilage/shrinkage loss in INR"
    )
    spoilage_risk_penalty: float = Field(
        default=0.0, ge=0, description="Alias for estimated_loss_cost"
    )
    total_deductions: float = Field(default=0.0, ge=0, description="Sum of all cost deductions in INR")
    expected_net_return: float = Field(default=0.0, description="Net in-pocket return in INR")
    net_return: float = Field(default=0.0, description="Alias for expected_net_return")
    net_return_per_quintal: float = Field(
        default=0.0, description="Net return divided by quantity (INR/quintal)"
    )

    # Provenance and assumption labels
    price_provenance: str = Field(default="", description="Source provenance of price input")
    is_price_simulated: bool = Field(default=False, description="True if price is a simulated benchmark")
    transport_provenance: str = Field(default="Simulated freight tariff", description="Provenance of transport input")
    charges_provenance: str = Field(default="Assumed standard statutory rates", description="Provenance of market charges")
    is_apmc_exempt: bool = Field(
        default=False,
        description="Whether APMC cess and mandi labor exemption assumption was applied",
    )
    weather_loss_note: str = Field(
        default="",
        description="Detailed explanation of transit weather spoilage calculation and mitigation",
    )

    # Ranking & Evaluation
    meets_minimum_condition: bool = Field(
        default=True, description="True if expected_net_return >= farmer's minimum target"
    )
    is_feasible: bool = Field(default=True, description="True if viable and meets threshold")
    rank: int = Field(default=1, ge=1, description="Rank ordered by net return (1 = best)")
    recommendation_note: str = Field(default="", description="Key rationale explaining the ranking")

    def model_post_init(self, __context: Any) -> None:
        """Sync aliases and calculate values if not provided."""
        if self.gross_value == 0.0 and self.gross_revenue > 0.0:
            object.__setattr__(self, "gross_value", self.gross_revenue)
        elif self.gross_revenue == 0.0 and self.gross_value > 0.0:
            object.__setattr__(self, "gross_revenue", self.gross_value)

        if self.expected_net_return == 0.0 and self.net_return != 0.0:
            object.__setattr__(self, "expected_net_return", self.net_return)
        elif self.net_return == 0.0 and self.expected_net_return != 0.0:
            object.__setattr__(self, "net_return", self.expected_net_return)

        if self.applicable_market_charges == 0.0 and (self.mandi_cess_apmc > 0 or self.loading_unloading_cost > 0):
            object.__setattr__(self, "applicable_market_charges", round(self.mandi_cess_apmc + self.loading_unloading_cost, 2))

        if self.estimated_loss_cost == 0.0 and self.spoilage_risk_penalty > 0:
            object.__setattr__(self, "estimated_loss_cost", self.spoilage_risk_penalty)
        elif self.spoilage_risk_penalty == 0.0 and self.estimated_loss_cost > 0:
            object.__setattr__(self, "spoilage_risk_penalty", self.estimated_loss_cost)


class OptimizationResult(BaseModel):
    """Ranked comparison of all feasible and non-feasible selling channels."""

    crop: str = Field(..., description="Crop name")
    quantity_quintals: float = Field(..., gt=0, description="Crop quantity")
    minimum_net_return_inr: float | None = Field(default=None, description="Farmer's target net return")
    all_options: list[NetReturnBreakdown] = Field(default_factory=list, description="All evaluated channels")
    feasible_options: list[NetReturnBreakdown] = Field(default_factory=list, description="Options meeting threshold")
    recommended_option: NetReturnBreakdown | None = Field(default=None, description="Highest ranking option")
    summary_explanation: str = Field(default="", description="Detailed narrative explaining ranking")



class BookingProposal(BaseModel):
    """Pending booking proposal awaiting explicit farmer confirmation."""

    proposal_id: str = Field(..., description="Unique proposal identifier (e.g. PROP-20261018-ABCD)")
    session_id: str = Field(..., description="Farmer session ID this proposal belongs to")
    farmer_id: str = Field(..., description="Farmer identifier or handle")
    selected_option: NetReturnBreakdown = Field(..., description="Full net-return breakdown of the proposed channel")
    crop: str = Field(..., description="Target agricultural crop name")
    quantity_quintals: float = Field(..., gt=0, description="Produce quantity in quintals")
    origin_location: str = Field(..., description="Farmer origin / pickup location")
    destination_name: str = Field(..., description="Mandi or buyer delivery destination")
    channel_type: Literal["MANDI", "DIRECT_BUYER"] = Field(..., description="Sales channel type")
    transit_date: str = Field(..., description="Scheduled pickup or transit date (YYYY-MM-DD)")
    estimated_gross_value: float = Field(..., ge=0, description="Gross value in INR")
    itemized_deductions: float = Field(..., ge=0, description="Total deductions in INR")
    estimated_net_payout: float = Field(..., description="Estimated net return in INR")
    created_at_utc: str = Field(..., description="ISO 8601 UTC timestamp of creation")
    expires_at_utc: str = Field(..., description="ISO 8601 UTC timestamp of expiration")
    is_consumed: bool = Field(default=False, description="True once confirmed, cancelled, or superseded")


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


# =====================================================================
# Fetch.ai Agent Payment Protocol Models & KrishiBazaar Payment Subsystem
# =====================================================================

from uagents_core.contrib.protocols.payment import (
    Funds,
    RequestPayment,
    CommitPayment,
    RejectPayment,
    CancelPayment,
    CompletePayment,
    payment_protocol_spec,
)


class PaymentStatus(str, Enum):
    """Lifecycle statuses for the Fetch.ai Agent Payment Protocol subsystem."""

    REQUESTED = "REQUESTED"
    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
    SUBMITTING = "SUBMITTING"
    REJECTED = "REJECTED"
    COMMITTED = "COMMITTED"
    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"
    AWAITING_RECONCILIATION = "AWAITING_RECONCILIATION"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class PaymentPurpose(str, Enum):
    """Credible demonstration service monetization purposes."""

    ADVISORY_PASS = "KRISHIBAZAAR_PREMIUM_ADVISORY_PASS"
    MARKET_INTELLIGENCE = "KRISHIBAZAAR_MARKET_INTELLIGENCE"
    ROUTE_OPTIMIZATION = "KRISHIBAZAAR_ROUTE_OPTIMIZATION"


class ExplicitPaymentApproval(BaseModel):
    """
    Validated explicit human payment authorization.
    Strictly ties human approval to reference, amount, currency, recipient, and optional payer.
    """

    reference: str = Field(..., description="Unique payment request reference ID")
    amount_base_units: int = Field(..., gt=0, description="Exact amount in integer base units")
    currency: str = Field(..., description="Token denomination / currency string (e.g. atestfet, FET)")
    recipient: str = Field(..., description="Designated recipient wallet address")
    payer: str | None = Field(default=None, description="Optional payer identifier")


class PaymentReviewDetails(BaseModel):
    """Human-readable presentation review card for approval before transaction submission."""

    reference: str = Field(..., description="Payment reference")
    purpose: str = Field(..., description="Clearly designated service purpose")
    amount_base_units: int = Field(..., gt=0, description="Amount in integer base units")
    amount_display: str = Field(..., description="User-friendly display amount (e.g. '0.000001 FET (1000 atestfet)')")
    currency: str = Field(..., description="Denomination")
    recipient_address: str = Field(..., description="Designated payee wallet address (fetch1...)")
    network: str = Field(..., description="Target network (e.g. dorado-1 / testnet)")
    estimated_fee: str = Field(default="Standard network fee (~0.001 FET)", description="Estimated gas fee")
    deadline_seconds: int = Field(..., description="Validity window in seconds")
    approval_command_template: str = Field(
        ...,
        description="Exact command the user must submit to authorize payment",
    )
    payer_balance_status: str = Field(
        default="UNCHECKED (Preflight check pending)",
        description="Live balance verification status of designated payer wallet on Dorado testnet",
    )
    testnet_notice: str = Field(
        default="⚠️ TESTNET DEMO MICRO-FEE ONLY. No real fiat or bank funds debited.",
        description="Prominent testnet safety notice",
    )


class PaymentRecord(BaseModel):
    """
    Independent persistent payment record stored in SQLite payment ledger.
    Completely isolated from booking records.
    """

    payment_reference: str = Field(..., description="Unique payment tracking reference ID")
    service_inquiry_id: str | None = Field(default=None, description="Linked inquiry ID if applicable")
    purpose: str = Field(..., description="Clearly labelled demonstration service purpose")
    payer_agent_address: str = Field(..., description="Payer uAgents address")
    payer_wallet_address: str = Field(..., description="Payer ledger wallet address (fetch1...)")
    payee_agent_address: str = Field(..., description="Payee uAgents address")
    payee_wallet_address: str = Field(..., description="Designated payee ledger wallet address (fetch1...)")
    amount_base_units: int = Field(..., gt=0, description="Amount in integer base units (never float)")
    amount_display: str = Field(..., description="Formatted display amount")
    currency: str = Field(..., description="Token currency denomination (e.g. atestfet)")
    network: str = Field(..., description="Network identifier (e.g. dorado-1)")
    status: PaymentStatus = Field(
        default=PaymentStatus.REQUESTED,
        description="Current authoritative payment lifecycle state",
    )
    tx_hash: str | None = Field(default=None, description="Authoritative on-chain transaction hash")
    tx_height: int | None = Field(default=None, description="Authoritative block height")
    actual_fee_base_units: int | None = Field(default=None, description="Actual on-chain transaction fee in base units")
    actual_fee_display: str | None = Field(default=None, description="Formatted display of actual gas fee paid")
    deadline_seconds: int = Field(default=300, ge=10, description="Expiration deadline in seconds")
    created_at_utc: str = Field(..., description="ISO 8601 UTC creation timestamp")
    updated_at_utc: str = Field(..., description="ISO 8601 UTC last update timestamp")
    verified_at_utc: str | None = Field(default=None, description="Authoritative settlement verification timestamp")
    rejection_reason: str | None = Field(default=None, description="Reason if payment was rejected")
    cancellation_reason: str | None = Field(default=None, description="Reason if payment was cancelled")
    metadata: dict[str, str] = Field(default_factory=dict, description="Custom metadata dictionary")
