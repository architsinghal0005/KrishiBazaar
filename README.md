# KrishiBazaar (कृषिबाज़ार)

> Autonomous Farm-to-Market Negotiation Agent Network for Indian Farmers.  
> Built natively for **Fetch.ai ASI:One** conversations using **uAgents** and the **Agent Chat Protocol (ACP)**.

---

## 1. Overview

**KrishiBazaar** connects Indian farmers with optimal markets directly inside the **ASI:One** conversational interface. Rather than relying on static price boards or opaque middlemen, KrishiBazaar deploys an autonomous multi-agent network that aggregates official mandi benchmark prices, evaluates transit weather risks via Open-Meteo, queries institutional buyer bids, quotes capacity-matched freight transport, and deterministically calculates the true **net financial return** for the farmer.

---

## 2. Agent Network Architecture

1. **KrishiBazaar Orchestrator**: The primary ASI:One-facing agent. Implements `AgentChatProtocol` (`chat_protocol_spec`), manages farmer dialog, dispatches sub-agent tasks, presents net-return comparisons, and enforces explicit user confirmation before booking.
2. **Market Agent**: Queries official mandi price data (data.gov.in Agmarknet API) and provides verified benchmark records with transparent source provenance.
3. **Weather Agent**: Fetches Open-Meteo forecasts for origin and destination routes, evaluating transit precipitation and spoilage hazard scores.
4. **Buyer Agent(s)**: Generates structured institutional buyer bids (e.g. FPOs, agro-processors, retail fresh chains) with transparent simulated benchmark labeling.
5. **Transport Agent**: Quotes vehicle-matched freight rates and executes test bookings upon farmer approval.
6. **Net-Return Optimizer**: Pure deterministic Python business logic that computes:
   $$\text{Net Return} = (\text{Quantity} \times \text{Price}) - (\text{Transport} + \text{APMC Cess} + \text{Labor} + \text{Weather Risk Buffer})$$

---

## 3. Repository Structure

```text
KrishiBazaar/
├── .env.example              # Safe environment template (seeds, ports, API keys)
├── .gitignore                # Protects secrets, databases, and environments
├── PROJECT_PLAN.md           # Architectural roadmap & milestone breakdown
├── pyproject.toml            # Python packaging and pytest configuration
├── README.md                 # Project documentation & execution guide
├── requirements.txt          # Pinned dependency requirements
├── src/
│   └── krishibazaar/
│       ├── __init__.py       # Package marker
│       ├── config.py         # Validated Pydantic configuration loader
│       ├── models.py         # Shared Pydantic domain schemas
│       ├── storage.py        # SQLite persistent session & booking ledger
│       ├── agents/           # uAgents package (Orchestrator, Market, Weather, Buyer, Transport)
│       │   └── __init__.py
│       └── services/         # API clients, optimizer, and booking workflow engines
│           └── __init__.py
└── tests/
    ├── __init__.py
    ├── test_models.py        # Tests for domain models & validation
    └── test_storage.py       # Tests for SQLite persistence & session management
```

---

## 4. Quick Start (Windows PowerShell)

### Prerequisites
- Python 3.10+ (compatible with Python 3.14 on Windows)
- Git

### 1. Clone & Set Up Virtual Environment
```powershell
# Navigate to workspace
cd "c:\Users\ARCHIT SINGHAL\KrishiBazaar"

# Create virtual environment (if not already created)
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

### 3. Configure Environment Variables
```powershell
# Copy template to .env
Copy-Item .env.example .env
```
*Note: Never commit `.env` containing private seeds or API keys.*

### 4. Run Automated Tests
```powershell
pytest -v
```

---

## 5. Development Principles & Compliance

- **No LLM Math**: All financial and net-return calculations are pure, deterministic Python functions.
- **Transparency**: All benchmark buyer offers and simulated bookings are explicitly labeled to prevent deception.
- **Approval Gate**: No logistics booking or payment action can be executed without explicit farmer approval.
- **Privacy & Security**: Private agent seeds and credentials are never placed in source code or public Git commits.
