# KrishiBazaar (à¤•à¥ƒà¤·à¤¿à¤¬à¤¾à¤œà¤¼à¤¾à¤°)

> **Autonomous Farm-to-Market Negotiation Agent Network for Indian Farmers**
> Built natively for **Fetch.ai ASI:One** conversations using **uAgents**, the **Agent Chat Protocol (ACP)**, and the official **ASI:One Interactive Card Protocol**.

---

## 1. Overview & Vision

Indian farmers frequently lose 15% to 35% of their produce value due to information asymmetry, opaque physical mandi commission rates, weather-induced transit spoilage, and unexpected logistics deductions.

**KrishiBazaar** solves this by deploying a collaborative, autonomous network of six specialized agents that negotiate and compute the farmer's true in-pocket **net financial return**:

$$\text{Net Return} = (\text{Produce Quantity} \times \text{Offered Price}) - (\text{Freight} + \text{APMC Statutory Cess} + \text{Hamali/Labor} + \text{Storage} + \text{Weather Risk Buffer})$$

All calculations are strictly deterministic Python arithmeticâ€”no LLM hallucinations or financial guesswork.

---

## 2. Multi-Agent Network Architecture

| Agent Name | Port | Protocol | Primary Responsibility | Data Source |
| :--- | :---: | :--- | :--- | :--- |
| **Orchestrator** | `8000` | `AgentChatProtocol`<br>`(ACP V1)` | Farmer dialogue, intent extraction, specialist coordination, channel ranking, and approval safety gating. | ASI:One API (`asi1-mini`) + SQLite |
| **Market Specialist** | `8001` | `KrishiBazaarMarketProtocol` | Live wholesale benchmark prices from Indian APMC mandis. | [data.gov.in](https://data.gov.in) (AGMARKNET) |
| **Weather Specialist** | `8002` | `KrishiBazaarWeatherProtocol` | Route-specific precipitation, temperature, and transit spoilage hazard scoring. | [Open-Meteo REST API](https://open-meteo.com) |
| **Buyer A Specialist** | `8003` | `KrishiBazaarBuyerProtocol` | Structured bids from Farmer Producer Organizations (FPOs) and regional retail chains. | Simulated Institutional Benchmarks |
| **Buyer B Specialist** | `8005` | `KrishiBazaarBuyerBProtocol` | Bulk procurement offers from export aggregators and food processing corporations. | Simulated Corporate Benchmarks |
| **Transport Specialist** | `8004` | `KrishiBazaarTransportProtocol` | Freight vehicle capacity matching (Tata Ace, 407, 17ft, 22ft) with weather hazard surcharges. | Simulated Fleet Logistics Models |
| **Payment Specialist** | `8006` | `AgentPaymentProtocol`<br>`(Role: seller/payee)` | Authoritative Fetch.ai on-chain transaction reconciliation, CommitPayment validation, and CompletePayment settlement. | Fetch.ai Dorado Testnet (`dorado-1`) |

---

## 3. Data Provenance & Trust Model

KrishiBazaar strictly separates verified real-world data from benchmark simulations and regulatory assumptions:

* **Live APMC Mandi Data (`data.gov.in`)**: Official Agmarknet daily wholesale modal prices. Never fabricated. When zero arrival records exist for a district, state-level benchmarks are presented with an explicit provenance notice.
* **Live Transit Weather (`Open-Meteo`)**: Live atmospheric forecasts mapped to the requested transit date. Includes spoilage hazard ratings (`LOW`, `MEDIUM`, `HIGH`).
* **Simulated Buyer Bids**: Labeled as benchmark models for comparison. No commercial contract exists without farmer confirmation.
* **Simulated Freight Logistics**: Quotes vehicle sizes matching quantity (e.g. 50 quintals &rarr; Tata 407) with dynamic distance routing and rain-protection tarpaulin coverage.
* **Assumed Statutory Deductions**: Standard APMC market cess (1.5%) and labor/hamali (INR 25/qtl). Direct buyer options assume APMC exemption outside market yards.
* **Strict Safety Gate**: No financial transaction, carrier dispatch, or commercial booking is ever created without explicit farmer approval.
* **Payment Protocol Gate**: `ENABLE_PAYMENT_PROTOCOL=false` by default. Dedicated testnet payee (`fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h`) strictly isolated from informational specialist agents.

---

## 4. Setup & Installation (Windows PowerShell)

### Prerequisites
- **Python 3.13** (verified under `.venv313`)
- **Git**

### Step 1: Clone & Navigate
```powershell
git clone https://github.com/your-org/KrishiBazaar.git
cd KrishiBazaar
```

### Step 2: Create & Activate Virtual Environment (`.venv313`)
```powershell
# Create virtual environment with Python 3.13
py -3.13 -m venv .venv313

# Activate virtual environment in PowerShell
.\.venv313\Scripts\Activate.ps1
```

### Step 3: Install Dependencies
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

### Step 4: Configure Environment Variables
```powershell
# Copy safe configuration template
Copy-Item .env.example .env
```
Open `.env` and verify the settings. Safe local defaults and deterministic seeds are provided. Never commit private credentials or seeds to public version control.

---

## 5. Agent Startup Order (Windows PowerShell)

For live multi-process agent messaging, launch the specialist agents first, followed by the Payment Specialist and the Orchestrator. Open **seven separate PowerShell tabs**:

```powershell
# Tab 1: Market Specialist (Port 8001)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\market_agent.py

# Tab 2: Weather Specialist (Port 8002)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\weather_agent.py

# Tab 3: Buyer A Specialist (Port 8003)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\buyer_agent.py

# Tab 4: Buyer B Specialist (Port 8005)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\buyer_b_agent.py

# Tab 5: Transport Specialist (Port 8004)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\transport_agent.py

# Tab 6: Payment & Settlement Specialist (Port 8006)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\payment_agent.py

# Tab 7: Orchestrator Agent (Port 8000)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\orchestrator.py
```

Each agent will display a startup banner with its local endpoint, address, and protocol bindings.

---

## 6. Demo & Smoke Testing

### Automated Demo Smoke Test
To verify configured ports, check all specialist HTTP connections, and test the negotiation pipeline without creating bookings:

```powershell
# Full live smoke test (checks ports + live specialist queries + pipeline math)
.\.venv313\Scripts\python.exe smoke_test.py

# Quick socket verification only (ports 8000-8005)
.\.venv313\Scripts\python.exe smoke_test.py --ports-only

# In-memory pipeline verification (runs without active background agents)
.\.venv313\Scripts\python.exe smoke_test.py --offline
```

### Interactive Dialogue Walkthrough

1. **Farmer Submits Produce Inquiry**:
   > *"I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."*
   * Orchestrator queries Market, Weather, Buyer A, Buyer B, and Transport over HTTP.
   * Renders interactive **Ranked Selling Channels CarouselCard** with transparent provenance.

2. **Farmer Selects an Option**:
   > *"Select option 1"* (or clicks **Select Rank #1** CTA)
   * Orchestrator generates a **ReviewCard** with Proposal Reference (e.g. `PROP-20261021-XXXXXX`).
   * Sets safety status: `AWAITING EXPLICIT FARMER APPROVAL` (valid for 15 minutes).

3. **Casual Acknowledgment Does NOT Confirm Booking**:
   > *"ok"* or *"sounds good"*
   * Safety gate activates: Orchestrator explains explicit approval is required and redisplays the existing proposal ReviewCard without creating a booking or re-asking for crop details.

4. **Explicit Approval Confirms Booking**:
   > *"Approve proposal PROP-20261021-XXXXXX"* (or clicks **Approve & Generate Booking** CTA)
   * Validates proposal ID and expiration.
   * Atomically records confirmation audit record in SQLite (`krishibazaar.db`).
   * Displays **Booking Confirmation Card**. Duplicate approvals are strictly blocked.

---

## 7. Troubleshooting Guide (Windows PowerShell)

| Observed Output | Cause | Recommended Action |
| :--- | :--- | :--- |
| `ERROR: Failed to retrieve messages: 401` | Background Agentverse mailbox token requires refresh. | **No action needed for local demos.** Direct inter-agent HTTP messaging runs independently on local ports 8000â€“8005. |
| `WARNING: I do not have enough funds to register on Almanac contract` | Agent has no mainnet FET tokens for smart contract registration. | **No action needed.** Almanac REST API registration succeeds automatically. Contract writes are optional. |
| `UnicodeEncodeError: 'charmap' codec can't encode characters` | Windows PowerShell default console encoding is CP1252. | Run `chcp 65001` or set `$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding` before running scripts. |
| `Port already in use` (8000-8005) | Another agent process is already bound to the port. | Run `Get-NetTCPConnection -State Listen \| Where-Object { $_.LocalPort -ge 8000 -and $_.LocalPort -le 8005 }` and terminate the orphaned process with `Stop-Process -Id <PID>`. |
| `specialist query timed out` | Upstream network latency on data.gov.in or slow response. | The Orchestrator automatically falls back to verified local benchmark data and displays transparent provenance disclaimers. |

---

## 8. Automated Test Suite

Run the complete test suite using Python 3.13:

```powershell
.\.venv313\Scripts\python.exe -m pytest -v
```

The test suite covers:
- **Unit & Mocked Tests**: Pydantic schemas, data provenance, deterministic net-return math, and SQLite atomic transactions.
- **Card Protocol Tests**: Carousel, Detail, Review, and Form card generation with CTA payload validation.
- **Approval Safety Gate Tests**: Casual acknowledgement blocking, ID mismatch rejection, expiry enforcement, cancellation, and duplicate approval prevention.
- **Multi-Process HTTP Integration Tests**: Live HTTP request-response validation across specialist agents on ports 8001â€“8005.

---

## 9. Hackathon Demo Compliance

* **No LLM Math**: All net returns and deductions are calculated using pure deterministic Python business logic.
* **Full Provenance Transparency**: Benchmark buyer offers, simulated freight quotes, and statutory cess assumptions are clearly labeled.
* **Strict Safety Gate**: Zero real commercial bookings, payments, or carrier dispatches are initiated.
* **Credential Protection**: Zero private seeds, API tokens, or secrets in source code, logs, or documentation.
