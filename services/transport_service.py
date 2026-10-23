"""
Root proxy for KrishiBazaar Transport Service.
Re-exports functionality from src/krishibazaar/services/transport_service.py.
"""

from krishibazaar.services.transport_service import (
    DISTANCE_REGISTRY,
    estimate_route_distance_km,
    generate_simulated_transport_quotes,
    get_transport_quotes,
    get_transport_quotes_async,
    select_vehicle_for_quantity,
)

__all__ = [
    "DISTANCE_REGISTRY",
    "estimate_route_distance_km",
    "generate_simulated_transport_quotes",
    "get_transport_quotes",
    "get_transport_quotes_async",
    "select_vehicle_for_quantity",
]
