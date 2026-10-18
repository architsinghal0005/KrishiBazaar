"""
Mandi Service module proxy for root import convenience.
Re-exports the implementation from krishibazaar.services.mandi_service.
"""

from krishibazaar.services.mandi_service import (
    DATAGOV_MANDI_RESOURCE_URL,
    SAMPLE_BENCHMARK_MANDI_DATA,
    get_mandi_prices,
    get_mandi_prices_async,
)

__all__ = [
    "DATAGOV_MANDI_RESOURCE_URL",
    "SAMPLE_BENCHMARK_MANDI_DATA",
    "get_mandi_prices",
    "get_mandi_prices_async",
]
