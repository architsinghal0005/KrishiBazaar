# KrishiBazaar (कृषिबाज़ार)

> **Autonomous Farm-to-Market Negotiation Agent Network for Indian Farmers**  
> Built natively for **Fetch.ai ASI:One** conversations using **uAgents**, the **Agent Chat Protocol (ACP)**, and the official **ASI:One Interactive Card Protocol**.

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

---

## 1. Overview & Vision

Indian farmers frequently lose 15% to 35% of their produce value due to information asymmetry, opaque physical mandi commission rates, weather-induced transit spoilage, and unexpected logistics deductions.

**KrishiBazaar** solves this by deploying a collaborative, autonomous network of specialized uAgents that negotiate and compute the farmer's true in-pocket **net financial return**:

$$\text{Net Return} = (\text{Produce Quantity} \times \text{Offered Price}) - (\text{Freight} + \text{APMC Statutory Cess} + \text{Hamali/Labor} + \text{Storage} + \text{Weather Risk Buffer})$$

All calculations are strictly deterministic Python arithmetic—zero LLM financial hallucinations or guesswork.

---

## 2. Agentverse Agents and Addresses

The KrishiBazaar multi-agent network is registered and discoverable on Agentverse. Indian farmers converse with the main entry point via ASI:One or Agentverse Chat, while the orchestrator coordinates autonomously with domain-specialist and settlement agents:

| Agent Name | Agentverse Address / Handle |
| :--- | :--- |
| KrishiBazaar Main | `@krishibazaar-india` |
| krishibazaar_market | `agent1q0rsn3us4d9twt209r2xefsqku4qvgypjtp8xvkjep4tmrvrsldnw4z5az4` |
| krishibazaar_weather | `agent1qwzwhwgk3hft3z9qnaf2y7mdlwll2r23383fn6x0fhmj33lyuwuhqhq49rv` |
| krishibazaar_buyer | `agent1qtgmthsczmt5uxchs9pz0033vrfm4mwhwl5lhqewz80qfggkjay659sn3fu` |
| krishibazaar_transport | `agent1q0he7rn2yyh7lkswt37hdddfaf7vjcu4vtvyjd2npte07vw6dnygk7tjl8j` |
| krishibazaar_buyer_b | `agent1qgw98k45pq6xp6vtj6sy4mrdmhjn206zaqs5f7ff6ghsj5rafs39sesvpzh` |
| krishibazaar_payment_agent | `agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf` |

### Agent Roles & Responsibilities

- **KrishiBazaar Main (`@krishibazaar-india`):** The primary project entry point and farmer-facing conversational orchestrator. Handles natural-language farmer dialogue via Agent Chat Protocol (ACP V1) / ASI:One chat, extracts structured produce inquiry parameters (crop, quantity in quintals, origin location, deadline, minimum net return target), coordinates specialist agent queries, executes deterministic multi-channel net-return optimization, renders interactive UI cards (Carousel, Review, Confirmation, Payment Review), and enforces the mandatory explicit farmer approval safety gate.
- **krishibazaar_market (`agent1q0rsn3us4d9twt209r2xefsqku4qvgypjtp8xvkjep4tmrvrsldnw4z5az4`):** Market Specialist Agent that queries the official Indian Open Government Data ([data.gov.in](https://data.gov.in) / AGMARKNET) daily mandi price dataset for target commodities and districts. Validates reported prices against clerical errors and unit mismatches (e.g. per-kg or per-bundle anomalies), reports transparent data provenance, and provides regional/state benchmarks when local district arrival records are unavailable.
- **krishibazaar_weather (`agent1qwzwhwgk3hft3z9qnaf2y7mdlwll2r23383fn6x0fhmj33lyuwuhqhq49rv`):** Weather Specialist Agent that evaluates live atmospheric forecasts (precipitation probability, rain volume, and temperature extremes) along transit routes via the Open-Meteo REST API, calculating objective crop spoilage hazard ratings (`LOW`, `MEDIUM`, `HIGH`) and transit risk deductions.
- **krishibazaar_buyer (`agent1qtgmthsczmt5uxchs9pz0033vrfm4mwhwl5lhqewz80qfggkjay659sn3fu`):** Buyer Specialist Agent A that generates structured, simulated benchmark procurement bids from regional aggregators, Farmer Producer Organizations (FPOs), and organized retail hubs for negotiation modeling.
- **krishibazaar_transport (`agent1q0he7rn2yyh7lkswt37hdddfaf7vjcu4vtvyjd2npte07vw6dnygk7tjl8j`):** Transport Specialist Agent that estimates freight quotes, calculates transit distance between farmgate and delivery destinations, matches vehicle capacity (Tata Ace, 407, 17ft, 22ft container), and incorporates weather protection surcharges (e.g. tarpaulin coverage).
- **krishibazaar_buyer_b (`agent1qgw98k45pq6xp6vtj6sy4mrdmhjn206zaqs5f7ff6ghsj5rafs39sesvpzh`):** Buyer Specialist Agent B that generates alternative simulated benchmark procurement offers from institutional buyers, corporate agribusinesses, and export commodity aggregators.
- **krishibazaar_payment_agent (`agent1qfw20k2rw8p68e04mzzul2sedcjgcfcryz29hh55xf3d9vz7f59tstejszf`):** Payment Specialist Agent (role: seller/payee) implementing the official Fetch.ai Agent Payment Protocol (`payment_protocol_spec`). Handles advisory-fee payment reviews, enforces an explicit human authorization gate (`AUTHORIZE PAYMENT REF:...`), validates recipient/currency/amounts, and performs authoritative on-chain settlement verification on the Fetch.ai Dorado Testnet.

---

## 3. Local Development Port Mapping

For local development, unit testing, and multi-process debugging, agents bind to the following local loopback ports on `127.0.0.1`:

| Local Agent Process | Local Port | Protocol / Spec | Internal Data Source |
| :--- | :---: | :--- | :--- |
| **Orchestrator** | `8000` | `AgentChatProtocol` (ACP V1) | ASI:One API (`asi1-mini`) + SQLite |
| **Market Agent** | `8001` | `KrishiBazaarMarketProtocol` | [data.gov.in](https://data.gov.in) (AGMARKNET API) |
| **Weather Agent** | `8002` | `KrishiBazaarWeatherProtocol` | [Open-Meteo REST API](https://open-meteo.com) |
| **Buyer Agent A** | `8003` | `KrishiBazaarBuyerProtocol` | Simulated Institutional Benchmarks |
| **Transport Agent** | `8004` | `KrishiBazaarTransportProtocol` | Simulated Fleet Logistics Models |
| **Buyer Agent B** | `8005` | `KrishiBazaarBuyerBProtocol` | Simulated Corporate Benchmarks |
| **Payment Agent** | `8006` | `payment_protocol_spec` (seller/payee) | Fetch.ai Dorado Testnet (`dorado-1`) |

> **Important Distinction:** Local development ports (`8000`–`8006`) are strictly for local loopback testing on `127.0.0.1`. They are not public endpoints. In deployed or hosted Agentverse mode, agents communicate across the network via Agentverse mailboxes and decentralized uAgents protocols.

---

## 4. Data Provenance, Demo Disclaimers & Safety Model

KrishiBazaar strictly separates verified real-world data from benchmark simulations, regulatory assumptions, and testnet payment operations:

* **Official Live Mandi Data (`data.gov.in`)**: Official AGMARKNET daily wholesale modal prices. Never fabricated. When zero arrival records exist for a district, regional state-level benchmarks are presented with an explicit provenance notice and are never misrepresented as local quotes.
* **Live Transit Weather (`Open-Meteo`)**: Live atmospheric forecasts mapped to the requested transit date. Includes spoilage hazard ratings (`LOW`, `MEDIUM`, `HIGH`).
* **Simulated Buyer Bids**: All commercial buyer bids are simulated benchmark models for negotiation comparison (`is_simulated=True`). No real commercial buyer bid or trade contract exists without verified offline execution.
* **Simulated Freight Logistics**: Quotes vehicle sizes matching quantity (e.g. 50 quintals &rarr; Tata 407) with dynamic distance routing and rain-protection tarpaulin coverage.
* **Assumed Statutory Deductions (Unverified Assumptions)**: Standard APMC market cess (1.5%) and labor/hamali (INR 25/qtl). Direct buyer trade options assume an APMC statutory exemption outside market yards; this is an explicitly disclosed, unverified operational assumption and is not represented as legally confirmed advice.
* **Simulated Produce Bookings**: All booking records generated upon farmer approval are strictly marked `is_simulated_booking=True`. No physical crop sale, commercial contract, or carrier dispatch occurs.
* **Advisory-Fee Payment Demo vs. Real Crop Proceeds**: The optional 0.05 FET advisory-fee payment flow operates on the Fetch.ai Dorado testnet (`dorado-1`) using test tokens (`atestfet`). It is strictly an advisory service fee demonstration and is completely separate from any fiat crop sale proceeds.
* **Strict Human Approval Gates**:
  1. *Booking Gate*: Casual affirmations (`"ok"`, `"sounds good"`) are blocked from confirming proposals. Explicit confirmation (`"Confirm booking"`, `"Approve proposal PROP-..."`) is mandatory.
  2. *Payment Gate*: Exact structured authorization (`AUTHORIZE PAYMENT REF:... AMOUNT:... CURRENCY:... RECIPIENT:...`) is mandatory before any transaction is broadcast.
* **Credential & Environment Security**: Users must configure required environment variables locally via `.env` (using `.env.example` as a template). Never commit private keys, wallet seed phrases, API keys, or secret credentials to version control.

---

## 5. Setup & Installation (Windows PowerShell)

### Prerequisites
- **Python 3.13** or **Python 3.14** (virtual environment `.venv313` or `.venv`)
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

## 6. Agent Startup Order (Windows PowerShell)

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

# Tab 4: Transport Specialist (Port 8004)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\transport_agent.py

# Tab 5: Buyer B Specialist (Port 8005)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\buyer_b_agent.py

# Tab 6: Payment & Settlement Specialist (Port 8006)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\payment_agent.py

# Tab 7: Orchestrator Agent (Port 8000)
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\orchestrator.py
```

Each agent displays a startup banner with its local endpoint, address, and protocol bindings.

---

## 7. Demo & Smoke Testing

### Automated Demo Smoke Test
To verify configured ports, check all specialist HTTP connections, and test the negotiation pipeline without creating bookings:

```powershell
# Full live smoke test (checks ports + live specialist queries + pipeline math)
.\.venv313\Scripts\python.exe smoke_test.py

# Quick socket verification only (ports 8000-8006)
.\.venv313\Scripts\python.exe smoke_test.py --ports-only

# In-memory pipeline verification (runs without active background agents)
.\.venv313\Scripts\python.exe smoke_test.py --offline
```

### Interactive Dialogue Walkthrough

1. **Farmer Submits Produce Inquiry**:
   > *"I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."*
   * Orchestrator queries Market, Weather, Buyer A, Buyer B, and Transport over HTTP.
   * Renders interactive **Ranked Selling Channels CarouselCard** with transparent provenance and itemized deductions.

2. **Farmer Selects an Option**:
   > *"Select option 1"* (or clicks **Select Rank #1** CTA)
   * Orchestrator generates a **ReviewCard** with Proposal Reference (e.g. `PROP-20261021-XXXXXX`).
   * Displays itemized Freight, APMC Cess, Hamali/Labor, Weather Risk Loss, and Net Return.
   * Sets safety status: `AWAITING EXPLICIT FARMER APPROVAL` (valid for 15 minutes).

3. **Casual Acknowledgment Does NOT Confirm Booking**:
   > *"ok"* or *"sounds good"*
   * Safety gate activates: Orchestrator explains explicit approval is required and redisplays the existing proposal ReviewCard without creating a booking or re-asking for crop details.

4. **Explicit Approval Confirms Simulated Booking**:
   > *"Approve proposal PROP-20261021-XXXXXX"* (or clicks **Approve & Generate Booking** CTA)
   * Validates proposal ID and expiration.
   * Atomically records confirmation audit record in SQLite (`krishibazaar.db`).
   * Displays **Booking Confirmation Card** (`is_simulated_booking=True`). Duplicate approvals are strictly blocked.

5. **Optional Advisory-Fee Payment Review**:
   > *"Pay advisory fee"* (or clicks **Proceed to Payment Review** CTA)
   * Generates a **Payment Review DetailCard** for 0.05 FET on Dorado testnet with exact command template:
     `AUTHORIZE PAYMENT REF:KB-PAY-... AMOUNT:50000000000000000 CURRENCY:atestfet RECIPIENT:fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h`
   * Human gate executes transaction broadcast, validates Dorado ledger block height, verifies transferred funds, and records settlement with actual gas fee.

---

## 8. Troubleshooting Guide (Windows PowerShell)

| Observed Output | Cause | Recommended Action |
| :--- | :--- | :--- |
| `ERROR: Failed to retrieve messages: 401` | Background Agentverse mailbox token requires refresh. | **No action needed for local demos.** Direct inter-agent HTTP messaging runs independently on local ports 8000–8006. |
| `WARNING: I do not have enough funds to register on Almanac contract` | Agent has no mainnet FET tokens for smart contract registration. | **No action needed.** Almanac REST API registration succeeds automatically. Contract writes are optional. |
| `UnicodeEncodeError: 'charmap' codec can't encode characters` | Windows PowerShell default console encoding is CP1252. | Run `chcp 65001` or set `$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding` before running scripts. |
| `Port already in use` (8000–8006) | Another agent process is already bound to the port. | Run `Get-NetTCPConnection -State Listen \| Where-Object { $_.LocalPort -ge 8000 -and $_.LocalPort -le 8006 }` and terminate the orphaned process with `Stop-Process -Id <PID>`. |
| `specialist query timed out` | Upstream network latency on data.gov.in or slow response. | The Orchestrator automatically falls back to verified regional benchmark data and displays transparent provenance disclaimers. |

---

## 9. Automated Test Suite

Run the complete test suite:

```powershell
# Run all 403 regression and integration tests
.\.venv313\Scripts\python.exe -m pytest -v
```

The test suite covers:
- **Financial & Deduction Arithmetic**: Deterministic gross value, itemized deductions, net return, per-quintal rates, and target threshold consistency.
- **Provenance & Unverified Fee Disclosures**: APMC cess and hamali unverified assumption disclosures, zero-record notices, and regional benchmark distinction.
- **Card Protocol Tests**: Carousel, Detail, Review, and Form card generation with CTA payload validation.
- **Approval Safety Gate Tests**: Casual acknowledgement blocking, ID mismatch rejection, expiry enforcement, cancellation, and duplicate approval prevention.
- **Multi-Process HTTP Integration Tests**: Live HTTP request-response validation across specialist agents on ports 8001–8006.
- **Payment Protocol Invariants**: Preflight address validation, recipient whitelisting, exact base units, gas preflight, and uncertain settlement holding state.

---

## 10. Hackathon Demo Compliance & Safety

* **No LLM Math**: All net returns and deductions are calculated using pure deterministic Python business logic.
* **Full Provenance Transparency**: Benchmark buyer offers, simulated freight quotes, and statutory cess assumptions are clearly labeled with unverified status where applicable.
* **Strict Safety Gate**: Zero real commercial crop sales, buyer contracts, or carrier dispatches are initiated. All bookings are explicitly simulated.
* **Credential Protection**: Zero private seeds, API tokens, or secrets in source code, logs, or documentation.
* **Testnet Micro-Fee Isolation**: Advisory-fee payment operates strictly on Fetch.ai Dorado testnet with explicit authorization and is completely isolated from crop proceeds.
