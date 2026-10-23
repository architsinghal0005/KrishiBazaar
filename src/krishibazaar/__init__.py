"""
KrishiBazaar: Autonomous Farm-to-Market Negotiation Agent Network for Indian Farmers.
Built for native ASI:One conversational interaction via Fetch.ai uAgents & Agent Chat Protocol.
"""

from __future__ import annotations
import sys

# Configure UTF-8 encoding for Windows standard streams to prevent mojibake / charmap encoding errors
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__version__ = "0.1.0"

