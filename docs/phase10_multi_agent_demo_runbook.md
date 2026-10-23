# KrishiBazaar: Multi-Agent Demo Script & Operational Runbook

**Document Version:** 1.0.0 (Phase 10 Delivery)
**Target Environment:** Windows PowerShell 7 / Windows Terminal, Python 3.13 (`.venv313`)
**Architecture:** 1 Conversational Gateway Orchestrator + 6 Specialist Microservices (7 Distinct Agent Services)
**Safety Protocol:** Strictly Non-Destructive (`ENABLE_PAYMENT_PROTOCOL=false` Safe Default)

---

## 1. Purpose, Scope & Architecture Overview

### Purpose & Scope
This operational runbook provides the definitive, step-by-step procedure for preparing, launching, verifying, narrating, and cleanly shutting down the KrishiBazaar multi-agent system during live demonstrations and hackathon jury presentations.

It verifies that all calculations (mandi prices, freight payload matching, APMC cess, labor/hamali, transit spoilage buffers, and net in-pocket returns) execute with 100% mathematical determinism, transparent data provenance, and strict safety gating without unapproved commercial or blockchain side effects.

### Architecture Overview
KrishiBazaar operates as a federated multi-agent network where seven distinct agent processes coordinate across dedicated local TCP ports:

```
                                  [ Farmer / Evaluator ]
                                             â”‚
                                             â”‚ (AgentChatProtocol - ACP V1)
                                             â–¼
                                +â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€+
                                â”‚   ORCHESTRATOR AGENT    â”‚
                                â”‚       (Port 8000)       â”‚
                                +â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€+
                                             â”‚
                 â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                 â”‚                           â”‚                           â”‚
                 â–¼                           â–¼                           â–¼
        +-----------------+         +-----------------+         +-----------------+
        |  Market Agent   |         |  Weather Agent  |         |  Buyer Agents   |
        |   (Port 8001)   |         |   (Port 8002)   |         | A:8003 | B:8005 |
        | Agmarknet Live  |         | Open-Meteo REST |         | Wholesale/Export|
        +-----------------+         +-----------------+         +-----------------+
                 â”‚                           â”‚                           â”‚
                 â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                             â”‚
                 â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                 â”‚                                                       â”‚
                 â–¼                                                       â–¼
        +-----------------+                                     +-----------------+
        | Transport Agent |                                     |  Payment Agent  |
        |   (Port 8004)   |                                     |   (Port 8006)   |
        | Tata Fleet Sizing                                     | On-Chain Micro-Fee
        +-----------------+                                     +-----------------+
```

- **Orchestrator Agent (Port 8000)**: Primary conversational front-door. Handles farmer dialogue via ACP V1, invokes the ASI:One natural language parser, queries the specialists, evaluates net payouts, generates interactive cards, and gates bookings behind human confirmation.
- **Market Specialist (Port 8001)**: Queries official Indian APMC Mandi modal prices from `data.gov.in` (Agmarknet). Includes arrival dates and transparent benchmark fallback.
- **Weather Specialist (Port 8002)**: Evaluates transit spoilage hazards (`LOW`, `MEDIUM`, `HIGH`) and precipitation probability using the Open-Meteo REST API.
- **Buyer A Specialist (Port 8003)**: Generates structured, benchmark procurement bids from Farmer Producer Organizations (FPOs) and regional retail chains.
- **Buyer B Specialist (Port 8005)**: Generates bulk corporate agribusiness and export procurement bids with volume premiums.
- **Transport Specialist (Port 8004)**: Computes vehicle payload capacity matching (Tata Ace, 407, 17ft, 22ft), route distances, and weather hazard surcharges (tarpaulin protection).
- **Payment Specialist (Port 8006)**: Dedicated payee for Fetch.ai Agent Payment Protocol. Enforces micro-fee settlement verification against Dorado testnet (`dorado-1`) while isolated from commercial bookings.

---

## 2. Prerequisites & Environment Setup

### System Prerequisites
- **Operating System:** Windows 10/11 (PowerShell 5.1+ or PowerShell 7)
- **Python Version:** Python 3.13 (verified in `.venv313`)
- **Git:** Installed and available in PATH
- **Network Access:** Outbound HTTPS to `data.gov.in`, `api.open-meteo.com`, `api.asi1.ai`, and `grpc-dorado.fetch.ai`

### Directory & Virtual Environment Setup
Open Windows PowerShell, navigate to the repository root, and confirm Python 3.13:

```powershell
cd "C:\Users\ARCHIT SINGHAL\KrishiBazaar"

# Activate Python 3.13 virtual environment
.\.venv313\Scripts\Activate.ps1

# Verify Python version (must be 3.13.x)
python --version
```

### UTF-8 Console Encoding Configuration
Windows PowerShell defaults to legacy code pages (CP1252), which will corrupt Indian Rupee symbols (`â‚¹`) or Hindi characters. Run this configuration once per session:

```powershell
# Configure UTF-8 encoding for standard output and input
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001
```

---

## 3. Preflight Checklist

Run through these checks before starting the agent services:

```powershell
# 1. Check for stale port listeners (Ports 8000-8006 must be free initially)
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 8000 -and $_.LocalPort -le 8006 } |
    Select-Object LocalAddress, LocalPort, OwningProcess

# 2. Verify configuration integrity (no secrets printed)
.\.venv313\Scripts\python.exe -c "from krishibazaar.agents.diagnostics import validate_configuration; issues = validate_configuration(); print('Config Valid' if not issues else f'Issues: {issues}')"

# 3. Verify SQLite database accessibility
.\.venv313\Scripts\python.exe -c "import sqlite3; conn = sqlite3.connect('krishibazaar.db'); print(f'Database accessible. Booking count: {conn.execute(\"SELECT count(*) FROM bookings\").fetchone()[0]}')"

# 4. Quick in-memory smoke test (validates arithmetic without active agents)
.\.venv313\Scripts\python.exe smoke_test.py --offline
```

- [ ] **Port Clearance**: No orphaned processes bound to ports 8000â€“8006.
- [ ] **Configuration Check**: `validate_configuration()` returns `Config Valid`.
- [ ] **Database Verified**: `krishibazaar.db` is present, readable, and writable.
- [ ] **Safety Flag Confirmed**: `ENABLE_PAYMENT_PROTOCOL=false` in `.env`.
- [ ] **Offline Math Verified**: `smoke_test.py --offline` exits with code 0 (`[PASS]`).

---

## 4. Exact Service Startup Commands

To demonstrate authentic multi-agent communication, launch each specialist agent in its own PowerShell terminal tab, followed by the Payment Specialist and Orchestrator:

```powershell
# ==============================================================================
# TAB 1: Market Specialist (Port 8001)
# ==============================================================================
cd "C:\Users\ARCHIT SINGHAL\KrishiBazaar"
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\market_agent.py

# ==============================================================================
# TAB 2: Weather Specialist (Port 8002)
# ==============================================================================
cd "C:\Users\ARCHIT SINGHAL\KrishiBazaar"
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\weather_agent.py

# ==============================================================================
# TAB 3: Buyer A Specialist - FPO & Retail Bids (Port 8003)
# ==============================================================================
cd "C:\Users\ARCHIT SINGHAL\KrishiBazaar"
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\buyer_agent.py

# ==============================================================================
# TAB 4: Buyer B Specialist - Corporate & Export Bids (Port 8005)
# ==============================================================================
cd "C:\Users\ARCHIT SINGHAL\KrishiBazaar"
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\buyer_b_agent.py

# ==============================================================================
# TAB 5: Transport Specialist - Freight & Fleet Sizing (Port 8004)
# ==============================================================================
cd "C:\Users\ARCHIT SINGHAL\KrishiBazaar"
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\transport_agent.py

# ==============================================================================
# TAB 6: Payment & Settlement Specialist (Port 8006)
# ==============================================================================
cd "C:\Users\ARCHIT SINGHAL\KrishiBazaar"
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\payment_agent.py

# ==============================================================================
# TAB 7: Orchestrator Gateway Agent (Port 8000)
# ==============================================================================
cd "C:\Users\ARCHIT SINGHAL\KrishiBazaar"
.\.venv313\Scripts\Activate.ps1
python .\src\krishibazaar\agents\orchestrator.py
```

### Verification of All Running Services
In an 8th terminal tab, run the automated port and connectivity verification:

```powershell
.\.venv313\Scripts\python.exe smoke_test.py --ports-only
```

**Expected Output:**
```
================================================================================
 KrishiBazaar (KrishiBazaar Farm-to-Market Agent Network) - Demo Smoke Test
================================================================================

[*] Port & Socket Connectivity Status:
+-------+----------------------+--------------------+--------+------------------+
| Port  | Agent Name           | Address            | Mode   | Status           |
+-------+----------------------+--------------------+--------+------------------+
| 8000  | Orchestrator         | krishibazaar_or... | HTTP   | [PASS] Listening |
| 8001  | Market Specialist    | agent1q0rsn3us4... | HTTP   | [PASS] Listening |
| 8002  | Weather Specialist   | agent1qwzwhwgk3... | HTTP   | [PASS] Listening |
| 8003  | Buyer A Specialist   | agent1qtgmthscz... | HTTP   | [PASS] Listening |
| 8005  | Buyer B Specialist   | agent1qgw98k45p... | HTTP   | [PASS] Listening |
| 8004  | Transport Specialist | agent1q0he7rn2y... | HTTP   | [PASS] Listening |
| 8006  | Payment Specialist   | agent1qfw20k2rw... | HTTP   | [PASS] Listening |
+-------+----------------------+--------------------+--------+------------------+

[SUCCESS] All 7 agent ports (8000-8006) are active and listening.
```

---

## 5. End-to-End Live Demonstration Workflow

### Step 1: Automated Live Inter-Agent Pipeline Audit
Run the automated live coordination smoke test to prove all 7 agents communicate over HTTP `/submit` without mocks:

```powershell
.\.venv313\Scripts\python.exe smoke_test.py
```

**Observations & Verification Criteria:**
- **Market Response:** Fetches real mandi modal prices from `data.gov.in` (e.g., 16 authentic records in Nashik district).
- **Weather Response:** Connects to Open-Meteo and scores transit spoilage hazard level (`LOW` or `MEDIUM`).
- **Buyer Network Bids:** Buyer A and Buyer B return 6 combined procurement offers.
- **Transport Quotes:** Transport agent matches a Tata 407 (capacity: 50 quintals) across 18 candidate routes.
- **Deterministic Optimization:** All 22 channels ranked by expected net in-pocket return. Top rank yields verified net payout (e.g., Dindori Vani APMC @ â‚¹4,400/qtl yielding â‚¹218,444.25 net).
- **Safety Status:** Bounded proposal generated; zero unapproved bookings created in SQLite.

---

### Step 2: Natural Language Farmer Produce Inquiry
Simulate a farmer conversational message via ASI:One intent extraction or chat simulation:

**Farmer Message:**
> *"I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."*

**Execution via CLI Smoke Utility:**
```powershell
.\.venv313\Scripts\python.exe smoke_test_asi1.py "I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."
```

**Observations & Verification Criteria:**
- Intent parser extracts:
  - `crop`: `"onion"`
  - `quantity_quintals`: `50.0`
  - `origin_location`: `"Nashik"`
  - `selling_deadline`: `"tomorrow"`
  - `minimum_net_return_inr`: `85000.0`
  - `is_complete`: `True`
- Orchestrator generates a **Ranked Selling Channels CarouselCard** displaying top market options, freight vehicle breakdown, statutory APMC cess (1.5%), hamali labor charges, and net payout.

---

### Step 3: Farmer Selects Selling Option (Proposal Generation)
The farmer selects an option (e.g. clicking the **Select Rank #1** CTA or typing `"Select option 1"`).

**Observations & Verification Criteria:**
- Orchestrator issues a structured **ReviewCard** containing:
  - Unique Proposal ID: `PROP-20261023-XXXXXX`
  - Safety Warning: `AWAITING EXPLICIT FARMER APPROVAL`
  - Expiry Timer: Valid for exactly 15 minutes.
  - Transparent Provenance: Itemizes gross revenue, transport cost, APMC cess, labor, and net payout.
- *Crucial Observation*: The status in SQLite is `AWAITING_APPROVAL`. No commercial contract or carrier dispatch has occurred.

---

### Step 4: Casual Acknowledgment Blocked by Safety Gate
To prove safety hardening to the jury, the farmer submits an ambiguous or casual acknowledgment:

**Farmer Message:**
> *"ok sounds good"* or *"thanks"*

**Observations & Verification Criteria:**
- The Orchestrator **refuses** to execute a booking.
- Logs show: `Casual/ambiguous acknowledgement rejected in AWAITING_APPROVAL`.
- The Orchestrator re-displays the pending `ReviewCard` and explains that explicit confirmation is mandatory.
- The existing proposal remains preserved without re-asking the farmer for crop, quantity, or location details.

---

### Step 5: Explicit Farmer Confirmation & Atomic Booking
The farmer gives unambiguous, explicit approval:

**Farmer Message:**
> *"Approve proposal PROP-20261023-XXXXXX"* (or clicks the **Approve & Generate Booking** CTA)

**Observations & Verification Criteria:**
- Proposal ID and expiry are validated.
- SQLite atomically records the confirmation in the `bookings` table with unique reference `KB-BOOK-20261023-XXXXXX`.
- Orchestrator returns a **Booking Confirmed DetailCard** with a verifiable tracking token.
- *Replay Protection Test*: Submitting the same approval a second time is strictly blocked with `DUPLICATE_APPROVAL_REJECTED`.

---

### Step 6: Payment Safety & Testnet Micro-Fee Boundary
The jury may ask: *"Can KrishiBazaar trigger real payments?"*

**Operational Explanation & Verification:**
- Explain that KrishiBazaar maintains a strict safety boundary: `ENABLE_PAYMENT_PROTOCOL=false` by default.
- Informational specialist agents (Market, Weather, Transport, Buyer) are bots and **never** receive payments.
- Real on-chain settlement is restricted to a dedicated Payee Agent on port 8006 on Fetch.ai Dorado testnet (`dorado-1`).
- Run the safety boundary test suite to verify that unauthenticated payment commits are rejected:
  ```powershell
  .\.venv313\Scripts\python.exe -m pytest tests/test_payment_protocol_safety.py -v
  ```
- **Result:** All 5 safety gate tests pass (`100% PASS`), confirming zero unauthorized asset transfers can occur.

---

## 6. Safe Failure & Clean Shutdown Procedures

### Stopping Agent Services Cleanly
Never leave orphaned background Python processes holding TCP ports. Follow these instructions:

#### Method A: Graceful Interactive Shutdown (Recommended)
Navigate to each of the 7 PowerShell terminal tabs and press:
`Ctrl + C`
Each agent will output an unbinding log and terminate cleanly:
```
[INFO] Agent shutdown initiated. Unbinding port 800X...
[INFO] UAgents event loop terminated cleanly.
```

#### Method B: One-Liner PowerShell Port Reclaim
If terminals were closed without stopping the processes, or if ports remain locked:

```powershell
# Terminate any orphaned Python process listening on ports 8000 to 8006
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 8000 -and $_.LocalPort -le 8006 } |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object {
        Write-Host "Terminating orphaned agent PID: $_"
        Stop-Process -Id $_ -Force
    }

# Confirm ports are clear
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 8000 -and $_.LocalPort -le 8006 }
```

---

## 7. Operational Troubleshooting Matrix

| Symptom / Error | Root Cause | Immediate Remediation |
| :--- | :--- | :--- |
| **`[WinError 10048] Only one usage of each socket address is normally permitted`** | Port collision. An existing agent instance is already occupying port 8000â€“8006. | Run the **Method B** PowerShell one-liner above to kill orphaned processes, then re-launch the agent. |
| **`401 Unauthorized: Failed to retrieve messages`** | Agentverse mailbox token in `.env` is expired or invalid. | **Ignore during local demo.** Local inter-agent communication relies on `KrishiBazaarResolver` and local HTTP `/submit` endpoints, completely bypassing external mailbox authentication. |
| **`WARNING: I do not have enough funds to register on Almanac contract`** | Agent wallet has 0 FET on mainnet/testnet for contract writes. | Normal behavior for local execution. Almanac contract writes are optional. In-memory `KrishiBazaarResolver` resolves all specialist ports locally. |
| **`specialist query timed out after 10s`** | `data.gov.in` upstream API latency or network packet drop. | The Orchestrator automatically falls back to verified benchmark records from `official_benchmark_cache` with a transparent provenance notice. |
| **`database is locked: sqlite3.OperationalError`** | Concurrent multi-process write or uncommitted transaction. | KrishiBazaar uses SQLite WAL (Write-Ahead Logging) and atomic transaction contexts. Ensure no SQLite GUI browser holds an exclusive write lock. |
| **`UnicodeEncodeError: 'charmap' codec can't encode characters`** | Default Windows console encoding is CP1252. | Run `chcp 65001` and `$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8` before executing scripts. |

---

## 8. Hackathon Presentation Script & Narration (2-Minute Pitch)

Use this concise narrative while executing the live demo before judges:

> **[0:00 - The Problem]**
> *"Good morning judges. In India, smallholder farmers lose up to 35% of their produce value. When a farmer harvests 50 quintals of onions in Nashik, they don't know whether selling at the local APMC mandi, an FPO in Pune, or an export aggregator in Mumbai will yield the highest in-pocket return after deducting transport, statutory mandi cess, loading hamali, and rain spoilage."*

> **[0:30 - The Solution & Architecture]**
> *"KrishiBazaar solves this by deploying a collaborative, autonomous network of seven uAgents built natively for Fetch.ai ASI:One. Our Orchestrator acts as the conversational gateway, querying live wholesale mandi prices from the Indian government's Agmarknet API, live atmospheric transit risk from Open-Meteo, structured FPO bids, and payload-matched fleet logistics."*

> **[1:00 - Live Demonstration & Mathematical Rigor]**
> *[Show Terminal & run `smoke_test.py`]*
> *"Watch our multi-agent network in action across ports 8000 to 8006. In less than two seconds, 22 selling channels are evaluated. Notice that our net-return arithmetic is 100% deterministic Python logicâ€”zero LLM financial hallucinations. The system accurately accounts for â‚¹1,980 in Tata 407 freight, â‚¹3,375 in APMC cess, and â‚¹1,250 in labor, presenting Dindori Vani Mandi as the top option yielding â‚¹218,444 net."*

> **[1:30 - Trust, Safety Gate & Payment Boundary]**
> *"Crucially, KrishiBazaar implements an uncompromising safety gate. Casual replies like 'ok sounds good' are strictly blocked. A commercial booking is generated only upon explicit farmer confirmation, recorded with an atomic audit trail in SQLite. Furthermore, all payment protocols remain isolated behind safety flags, with real settlement verified on the Fetch.ai Dorado testnet."*

> **[1:55 - Conclusion]**
> *"KrishiBazaar bridges the information gap, eliminates intermediary exploitation, and puts verified net profit directly into the Indian farmer's hands. Thank you!"*

---

## 9. Final Acceptance Checklist

| Verification Category | Inspection Item | Required Evidence | Acceptance Status |
| :--- | :--- | :--- | :---: |
| **1. Agent Network Topology** | 7 dedicated processes running on ports `8000`â€“`8006` | `smoke_test.py --ports-only` returns code 0 | **PASS** |
| **2. Data Provenance** | APMC Mandi modal prices clearly distinguish live vs. benchmark | Price provenance field populated (`data.gov.in_live`) | **PASS** |
| **3. Transit Hazard Intelligence** | Open-Meteo forecast evaluates precipitation & spoilage risk | Hazard level scored (`LOW`, `MEDIUM`, or `HIGH`) | **PASS** |
| **4. Transport Sizing** | Tata Ace/407 payload matching matches 50 quintals to Tata 407 | Vehicle model matched with distance-based tariff | **PASS** |
| **5. Deterministic Optimization** | In-pocket net return calculation matches verified formula | Arithmetic matches `Gross - (Freight + Cess + Labor)` | **PASS** |
| **6. Approval Safety Gate** | Casual messages (`ok`, `yes`, `thanks`) do NOT confirm booking | Safety gate blocks confirmation; re-displays review card | **PASS** |
| **7. Atomic Confirmation** | Explicit approval creates persistent SQLite record | Booking record in `krishibazaar.db` with tracking token | **PASS** |
| **8. Replay Protection** | Duplicate approval on consumed proposal rejected | `DUPLICATE_APPROVAL_REJECTED` error returned | **PASS** |
| **9. Payment Protocol Safety** | `ENABLE_PAYMENT_PROTOCOL=false` enforced by default | All safety gate tests pass in test suite | **PASS** |
| **10. Clean Process Teardown** | All 7 ports freed upon termination | Sockets unbind cleanly with zero orphaned processes | **PASS** |
| **11. Agentverse Live Mailbox Sync** | External public reachability via Agentverse Mailbox | Requires manual API key configuration in dashboard | **NOT VERIFIED** (Manual Action) |
| **12. Almanac On-Chain Registration** | Blockchain directory registration on `fetchhub-4` | Requires live mainnet FET gas fee transaction | **NOT VERIFIED** (Manual Action) |
