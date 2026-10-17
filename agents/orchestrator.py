"""
KrishiBazaar Orchestrator Agent Entry Point.
Exports the orchestrator agent and allows running directly via `python agents/orchestrator.py`.
"""

from krishibazaar.agents.orchestrator import (
    orchestrator_agent,
    create_orchestrator_agent,
    chat_proto,
    build_greeting_response,
)
from krishibazaar.config import settings

if __name__ == "__main__":
    print(f"[*] Starting KrishiBazaar Orchestrator Agent on port {settings.orchestrator_port}...")
    print(f"[*] Agent Address: {orchestrator_agent.address}")
    orchestrator_agent.run()
