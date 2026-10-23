"""
Weather Service module proxy for root import convenience.
Re-exports the implementation from krishibazaar.services.weather_service.
"""

from krishibazaar.services.weather_service import (
    OPEN_METEO_BASE_URL,
    LOCATION_COORDINATES,
    resolve_coordinates,
    resolve_target_date,
    evaluate_transit_hazard,
    get_transit_weather,
    get_transit_weather_async,
)

__all__ = [
    "OPEN_METEO_BASE_URL",
    "LOCATION_COORDINATES",
    "resolve_coordinates",
    "resolve_target_date",
    "evaluate_transit_hazard",
    "get_transit_weather",
    "get_transit_weather_async",
]
