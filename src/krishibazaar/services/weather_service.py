"""
Weather Service for KrishiBazaar.
Integrates with Open-Meteo's documented forecast API (https://api.open-meteo.com/v1/forecast).
Validates coordinates, request parameters, HTTP status, timeouts, and missing fields.
Calculates agricultural transit hazard ratings and data provenance.
"""

from __future__ import annotations
import logging
import re
from datetime import date, datetime, timezone, timedelta
from typing import Any
import httpx

from krishibazaar.config import settings
from krishibazaar.models import DataProvenance, WeatherResult, WeatherRiskReport

logger = logging.getLogger("krishibazaar.weather_service")

OPEN_METEO_BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Curated coordinates for prominent Indian agricultural districts and mandi hubs
LOCATION_COORDINATES: dict[str, tuple[float, float]] = {
    "nashik": (19.9975, 73.7898),
    "lasalgaon": (20.1472, 74.2267),
    "pimpalgaon": (20.1706, 73.9856),
    "yeola": (20.0422, 74.4891),
    "pune": (18.5204, 73.8567),
    "narayangaon": (19.1176, 73.9788),
    "solapur": (17.6599, 75.9064),
    "latur": (18.4088, 76.5604),
    "indore": (22.7196, 75.8577),
    "sehore": (23.2033, 77.0844),
    "khanna": (30.7071, 76.2167),
    "karnal": (29.6857, 76.9905),
    "bharatpur": (27.2173, 77.4895),
    "alwar": (27.5530, 76.6346),
    "rajkot": (22.3039, 70.8022),
    "guntur": (16.3067, 80.4365),
    "kolar": (13.1378, 78.1291),
    "agra": (27.1767, 78.0081),
    "delhi": (28.6139, 77.2090),
}


def resolve_coordinates(
    location: str, latitude: float | None = None, longitude: float | None = None
) -> tuple[float, float] | None:
    """Resolve latitude and longitude from parameters or district registry."""
    if latitude is not None and longitude is not None:
        if -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0:
            return (latitude, longitude)
        return None

    cleaned = location.strip().lower()
    for key, coords in LOCATION_COORDINATES.items():
        if key in cleaned:
            return coords

    # Default fallback coordinate (Nashik center of agro-market)
    return (19.9975, 73.7898)


def resolve_target_date(
    deadline_str: str | None,
    base_date: datetime | None = None,
) -> str | None:
    """
    Parse a selling deadline (e.g. 'tomorrow', '2026-10-19', 'in 2 days', 'today') into ISO 'YYYY-MM-DD'.
    Uses Asia/Kolkata (UTC+5:30) as standard reference for Indian agricultural markets.
    """
    if not deadline_str or not isinstance(deadline_str, str):
        return None

    cleaned = deadline_str.strip().lower()
    if cleaned in ("none", "null", ""):
        return None

    ist = timezone(timedelta(hours=5, minutes=30))
    if base_date:
        if isinstance(base_date, datetime):
            now_ist = base_date.astimezone(ist) if base_date.tzinfo else base_date.replace(tzinfo=ist)
        elif isinstance(base_date, date):
            now_ist = datetime(base_date.year, base_date.month, base_date.day, tzinfo=ist)
        else:
            now_ist = datetime.now(ist)
    else:
        now_ist = datetime.now(ist)

    # Relative terms
    if cleaned in ("today", "same day", "immediate", "now"):
        return now_ist.strftime("%Y-%m-%d")

    if cleaned in ("tomorrow", "next day", "1 day", "1 days", "in 1 day", "by tomorrow"):
        return (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")

    if cleaned in ("day after tomorrow", "2 days", "in 2 days", "within 2 days"):
        return (now_ist + timedelta(days=2)).strftime("%Y-%m-%d")

    if cleaned in ("3 days", "in 3 days", "within 3 days"):
        return (now_ist + timedelta(days=3)).strftime("%Y-%m-%d")

    # ISO YYYY-MM-DD
    iso_match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", cleaned)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Indian format DD/MM/YYYY or DD-MM-YYYY
    dmy_match = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", cleaned)
    if dmy_match:
        d, m, y = int(dmy_match.group(1)), int(dmy_match.group(2)), int(dmy_match.group(3))
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def resolve_planned_transit_date(
    deadline_str: str | None,
    base_date: datetime | None = None,
) -> tuple[str, bool, str | None]:
    """
    Resolve a planned pickup/transit date ensuring it is strictly a valid future date (>= tomorrow in IST).
    Indian agricultural markets operate on Asia/Kolkata (UTC+5:30) time.

    Returns:
        tuple of (planned_transit_date_iso, was_adjusted_from_past, original_parsed_date)
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    if base_date:
        now_ist = base_date.astimezone(ist) if base_date.tzinfo else base_date.replace(tzinfo=ist)
    else:
        now_ist = datetime.now(ist)

    today_str = now_ist.strftime("%Y-%m-%d")
    tomorrow_str = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")

    parsed_date = resolve_target_date(deadline_str, base_date=base_date)

    if not parsed_date:
        # Default to tomorrow (valid future pickup date)
        return tomorrow_str, False, None

    if parsed_date < today_str:
        # Requested date is in the past! Clamp to tomorrow and flag adjustment
        logger.warning(
            f"Requested selling/pickup date '{parsed_date}' is in the past (< {today_str}). "
            f"Automatically adjusting planned pickup date to next valid transit date: {tomorrow_str}."
        )
        return tomorrow_str, True, parsed_date
    elif parsed_date == today_str:
        # Same-day pickup requested: advance to tomorrow for valid scheduled dispatch
        return tomorrow_str, True, parsed_date
    else:
        # Valid future date
        return parsed_date, False, None


def _compute_transit_hazard(
    precip_mm: float, precip_prob_pct: float, temp_max_c: float
) -> tuple[str, str]:
    """Calculate agricultural transit hazard level and actionable farmer advice."""
    if precip_mm >= 10.0 or precip_prob_pct >= 70.0:
        return (
            "HIGH",
            "Heavy rain hazard expected. Waterproof tarpaulin cover is MANDATORY. High moisture risks rapid rotting of perishables in transit.",
        )
    elif precip_mm >= 2.0 or precip_prob_pct >= 40.0:
        return (
            "MEDIUM",
            "Moderate rain showers possible. Tarpaulin cover strongly advised to prevent transit moisture damage.",
        )
    elif temp_max_c >= 40.0:
        return (
            "MEDIUM",
            "High heat hazard. Ventilate vehicle to prevent heat-stress and weight shrinkage during midday transit.",
        )
    else:
        return (
            "LOW",
            "Clear/dry weather conditions. Safe for standard road freight transit without weather delays.",
        )


evaluate_transit_hazard = _compute_transit_hazard


def _get_weather_fallback(
    location: str,
    lat: float,
    lon: float,
    target_date: str | None = None,
) -> WeatherResult:
    """Return offline sample weather report clearly labeled as simulated benchmark."""
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    forecast_date = target_date or today_str

    report = WeatherRiskReport(
        location=location.title(),
        forecast_date=forecast_date,
        temperature_max_c=31.0,
        temperature_min_c=19.5,
        precipitation_sum_mm=0.0,
        precipitation_probability_pct=15.0,
        wind_speed_max_kmh=12.0,
        spoilage_hazard_level="LOW",
        transit_advice="Offline sample weather. Standard dry transit conditions assumed.",
        data_source="Open-Meteo Offline Sample Cache",
        target_date_requested=target_date,
        is_target_date_matched=True,
    )

    return WeatherResult(
        success=True,
        location=location,
        latitude=lat,
        longitude=lon,
        report=report,
        provenance=DataProvenance(
            source="Open-Meteo Offline Sample Cache",
            retrieval_timestamp_utc=now_utc.isoformat(),
            observation_date=forecast_date,
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
            disclaimer="Sample weather report for simulation. Not live meteorological forecast.",
        ),
    )


async def get_transit_weather_async(
    location: str,
    latitude: float | None = None,
    longitude: float | None = None,
    target_date: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> WeatherResult:
    """
    Fetch live weather forecast from Open-Meteo REST API.
    Validates coordinates, HTTP status, timeouts, and missing fields.
    Matches the requested transit or selling deadline date if available in forecast window.
    """
    coords = resolve_coordinates(location, latitude, longitude)
    now_utc = datetime.now(timezone.utc).isoformat()

    if not coords:
        return WeatherResult(
            success=False,
            location=location,
            error_message=f"Invalid coordinates ({latitude}, {longitude}) for location '{location}'.",
            provenance=DataProvenance(
                source="Open-Meteo API",
                retrieval_timestamp_utc=now_utc,
                observation_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                freshness_status="STALE",
                is_live=False,
                disclaimer="Failed due to coordinate validation error.",
            ),
        )

    lat, lon = coords
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "weathercode",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "precipitation_probability_max",
            "windspeed_10m_max",
        ],
        "timezone": "Asia/Kolkata",
        "forecast_days": 7,
    }

    try:
        if client:
            resp = await client.get(OPEN_METEO_BASE_URL, params=params, timeout=10.0)
        else:
            async with httpx.AsyncClient() as session:
                resp = await session.get(OPEN_METEO_BASE_URL, params=params, timeout=10.0)

        if resp.status_code == 200:
            data = resp.json()
            daily = data.get("daily", {})

            # Validate presence of required weather fields
            dates = daily.get("time", [])
            temp_maxes = daily.get("temperature_2m_max", [])
            temp_mins = daily.get("temperature_2m_min", [])
            precip_sums = daily.get("precipitation_sum", [])
            precip_probs = daily.get("precipitation_probability_max", [])
            winds = daily.get("windspeed_10m_max", [])

            if not (dates and temp_maxes and temp_mins):
                logger.warning("Open-Meteo response missing critical daily fields. Using fallback.")
                return _get_weather_fallback(location, lat, lon, target_date=target_date)

            matched_idx = 0
            is_date_matched = True
            if target_date:
                if target_date in dates:
                    matched_idx = dates.index(target_date)
                else:
                    is_date_matched = False
                    logger.info(
                        f"Requested transit date '{target_date}' not found in forecast window "
                        f"({dates[0]} to {dates[-1]}). Using first available date {dates[0]}."
                    )

            forecast_date = dates[matched_idx]
            temp_max = float(temp_maxes[matched_idx]) if temp_maxes[matched_idx] is not None else 30.0
            temp_min = float(temp_mins[matched_idx]) if temp_mins[matched_idx] is not None else 20.0
            precip_sum = float(precip_sums[matched_idx]) if (precip_sums and precip_sums[matched_idx] is not None) else 0.0
            precip_prob = float(precip_probs[matched_idx]) if (precip_probs and precip_probs[matched_idx] is not None) else 0.0
            wind_speed = float(winds[matched_idx]) if (winds and winds[matched_idx] is not None) else 10.0

            hazard_level, advice = _compute_transit_hazard(precip_sum, precip_prob, temp_max)
            if not is_date_matched and target_date:
                advice = (
                    f"Notice: Requested transit date '{target_date}' was outside forecast window. "
                    f"Showing forecast for {forecast_date}. {advice}"
                )

            report = WeatherRiskReport(
                location=location.title(),
                forecast_date=forecast_date,
                temperature_max_c=temp_max,
                temperature_min_c=temp_min,
                precipitation_sum_mm=precip_sum,
                precipitation_probability_pct=precip_prob,
                wind_speed_max_kmh=wind_speed,
                spoilage_hazard_level=hazard_level,  # type: ignore[arg-type]
                transit_advice=advice,
                data_source="Open-Meteo API",
                target_date_requested=target_date,
                is_target_date_matched=is_date_matched,
            )

            return WeatherResult(
                success=True,
                location=location,
                latitude=lat,
                longitude=lon,
                report=report,
                provenance=DataProvenance(
                    source="Open-Meteo API",
                    retrieval_timestamp_utc=now_utc,
                    observation_date=forecast_date,
                    freshness_status="LIVE",
                    is_live=True,
                    disclaimer="Live meteorological forecast retrieved from Open-Meteo API.",
                ),
            )
        else:
            logger.error(f"Open-Meteo HTTP {resp.status_code}: {resp.text[:120]}")
            return _get_weather_fallback(location, lat, lon, target_date=target_date)

    except httpx.TimeoutException:
        logger.warning(f"Open-Meteo API timed out for '{location}'. Using fallback.")
        return _get_weather_fallback(location, lat, lon, target_date=target_date)
    except Exception as ex:
        logger.error(f"Open-Meteo exception: {type(ex).__name__} - {str(ex)}")
        return _get_weather_fallback(location, lat, lon, target_date=target_date)


def get_transit_weather(
    location: str,
    latitude: float | None = None,
    longitude: float | None = None,
    target_date: str | None = None,
) -> WeatherResult:
    """Synchronous wrapper for Open-Meteo weather retrieval."""
    coords = resolve_coordinates(location, latitude, longitude)
    now_utc = datetime.now(timezone.utc).isoformat()

    if not coords:
        return WeatherResult(
            success=False,
            location=location,
            error_message=f"Invalid coordinates ({latitude}, {longitude}) for location '{location}'.",
            provenance=DataProvenance(
                source="Open-Meteo API",
                retrieval_timestamp_utc=now_utc,
                observation_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                freshness_status="STALE",
                is_live=False,
                disclaimer="Failed due to coordinate validation error.",
            ),
        )

    lat, lon = coords
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "weathercode",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "precipitation_probability_max",
            "windspeed_10m_max",
        ],
        "timezone": "Asia/Kolkata",
        "forecast_days": 7,
    }

    try:
        with httpx.Client() as client:
            resp = client.get(OPEN_METEO_BASE_URL, params=params, timeout=10.0)

        if resp.status_code == 200:
            data = resp.json()
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            temp_maxes = daily.get("temperature_2m_max", [])
            temp_mins = daily.get("temperature_2m_min", [])
            precip_sums = daily.get("precipitation_sum", [])
            precip_probs = daily.get("precipitation_probability_max", [])
            winds = daily.get("windspeed_10m_max", [])

            if not (dates and temp_maxes and temp_mins):
                return _get_weather_fallback(location, lat, lon, target_date=target_date)

            matched_idx = 0
            is_date_matched = True
            if target_date:
                if target_date in dates:
                    matched_idx = dates.index(target_date)
                else:
                    is_date_matched = False

            forecast_date = dates[matched_idx]
            temp_max = float(temp_maxes[matched_idx]) if temp_maxes[matched_idx] is not None else 30.0
            temp_min = float(temp_mins[matched_idx]) if temp_mins[matched_idx] is not None else 20.0
            precip_sum = float(precip_sums[matched_idx]) if (precip_sums and precip_sums[matched_idx] is not None) else 0.0
            precip_prob = float(precip_probs[matched_idx]) if (precip_probs and precip_probs[matched_idx] is not None) else 0.0
            wind_speed = float(winds[matched_idx]) if (winds and winds[matched_idx] is not None) else 10.0

            hazard_level, advice = _compute_transit_hazard(precip_sum, precip_prob, temp_max)
            if not is_date_matched and target_date:
                advice = (
                    f"Notice: Requested transit date '{target_date}' was outside forecast window. "
                    f"Showing forecast for {forecast_date}. {advice}"
                )

            report = WeatherRiskReport(
                location=location.title(),
                forecast_date=forecast_date,
                temperature_max_c=temp_max,
                temperature_min_c=temp_min,
                precipitation_sum_mm=precip_sum,
                precipitation_probability_pct=precip_prob,
                wind_speed_max_kmh=wind_speed,
                spoilage_hazard_level=hazard_level,  # type: ignore[arg-type]
                transit_advice=advice,
                data_source="Open-Meteo API",
                target_date_requested=target_date,
                is_target_date_matched=is_date_matched,
            )

            return WeatherResult(
                success=True,
                location=location,
                latitude=lat,
                longitude=lon,
                report=report,
                provenance=DataProvenance(
                    source="Open-Meteo API",
                    retrieval_timestamp_utc=now_utc,
                    observation_date=forecast_date,
                    freshness_status="LIVE",
                    is_live=True,
                    disclaimer="Live meteorological forecast retrieved from Open-Meteo API.",
                ),
            )
        else:
            return _get_weather_fallback(location, lat, lon, target_date=target_date)
    except Exception:
        return _get_weather_fallback(location, lat, lon, target_date=target_date)
