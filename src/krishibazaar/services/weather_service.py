"""
Weather Service for KrishiBazaar.
Integrates with Open-Meteo's documented forecast API (https://api.open-meteo.com/v1/forecast).
Validates coordinates, request parameters, HTTP status, timeouts, and missing fields.
Calculates agricultural transit hazard ratings and data provenance.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
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



def _get_weather_fallback(location: str, lat: float, lon: float) -> WeatherResult:
    """Return offline sample weather report clearly labeled as simulated benchmark."""
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")

    report = WeatherRiskReport(
        location=location.title(),
        forecast_date=today_str,
        temperature_max_c=31.0,
        temperature_min_c=19.5,
        precipitation_sum_mm=0.0,
        precipitation_probability_pct=15.0,
        wind_speed_max_kmh=12.0,
        spoilage_hazard_level="LOW",
        transit_advice="Offline sample weather. Standard dry transit conditions assumed.",
        data_source="Open-Meteo Offline Sample Cache",
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
            observation_date=today_str,
            freshness_status="SIMULATED_SAMPLE",
            is_live=False,
            disclaimer="Sample weather report for simulation. Not live meteorological forecast.",
        ),
    )


async def get_transit_weather_async(
    location: str,
    latitude: float | None = None,
    longitude: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> WeatherResult:
    """
    Fetch live weather forecast from Open-Meteo REST API.
    Validates coordinates, HTTP status, timeouts, and missing fields.
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
        "forecast_days": 3,
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
                return _get_weather_fallback(location, lat, lon)

            forecast_date = dates[0]
            temp_max = float(temp_maxes[0]) if temp_maxes[0] is not None else 30.0
            temp_min = float(temp_mins[0]) if temp_mins[0] is not None else 20.0
            precip_sum = float(precip_sums[0]) if (precip_sums and precip_sums[0] is not None) else 0.0
            precip_prob = float(precip_probs[0]) if (precip_probs and precip_probs[0] is not None) else 0.0
            wind_speed = float(winds[0]) if (winds and winds[0] is not None) else 10.0

            hazard_level, advice = _compute_transit_hazard(precip_sum, precip_prob, temp_max)

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
            return _get_weather_fallback(location, lat, lon)

    except httpx.TimeoutException:
        logger.warning(f"Open-Meteo API timed out for '{location}'. Using fallback.")
        return _get_weather_fallback(location, lat, lon)
    except Exception as ex:
        logger.error(f"Open-Meteo exception: {type(ex).__name__} - {str(ex)}")
        return _get_weather_fallback(location, lat, lon)


def get_transit_weather(
    location: str,
    latitude: float | None = None,
    longitude: float | None = None,
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
        "forecast_days": 3,
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
                return _get_weather_fallback(location, lat, lon)

            forecast_date = dates[0]
            temp_max = float(temp_maxes[0]) if temp_maxes[0] is not None else 30.0
            temp_min = float(temp_mins[0]) if temp_mins[0] is not None else 20.0
            precip_sum = float(precip_sums[0]) if (precip_sums and precip_sums[0] is not None) else 0.0
            precip_prob = float(precip_probs[0]) if (precip_probs and precip_probs[0] is not None) else 0.0
            wind_speed = float(winds[0]) if (winds and winds[0] is not None) else 10.0

            hazard_level, advice = _compute_transit_hazard(precip_sum, precip_prob, temp_max)

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
            return _get_weather_fallback(location, lat, lon)
    except Exception:
        return _get_weather_fallback(location, lat, lon)
