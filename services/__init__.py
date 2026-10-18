"""
Root services package proxying to krishibazaar.services.
Enables imports such as `import services.mandi_service` or `from krishibazaar.services import ...`.
"""

from krishibazaar.services.extraction import (
    extract_farmer_intent,
    extract_farmer_intent_async,
)
from krishibazaar.services.mandi_service import (
    get_mandi_prices,
    get_mandi_prices_async,
)
from krishibazaar.services.weather_service import (
    get_transit_weather,
    get_transit_weather_async,
)
from krishibazaar.services.optimizer import (
    calculate_channel_net_return,
    optimize_sales_channels,
)

__all__ = [
    "extract_farmer_intent",
    "extract_farmer_intent_async",
    "get_mandi_prices",
    "get_mandi_prices_async",
    "get_transit_weather",
    "get_transit_weather_async",
    "calculate_channel_net_return",
    "optimize_sales_channels",
]
