"""
Optimizer module proxy for root import convenience.
"""

from krishibazaar.services.optimizer import *  # noqa: F403
from krishibazaar.services.optimizer import (
    calculate_channel_net_return,
    optimize_sales_channels,
)

__all__ = [
    "calculate_channel_net_return",
    "optimize_sales_channels",
]
