# KrishiBazaar: Agentverse v2 Publishing & Almanac Registration Guide

**Document Version:** 1.0.0 (Phase 10 Delivery)
**Evaluation Date:** October 2026
**System Architecture:** 1 Conversational Orchestrator Gateway + 6 Specialist Microservice Agents (7 Total Services)
**Target Environment:** Fetch.ai Agentverse v2 (`https://agentverse.ai`), Fetch.ai Almanac Smart Contract / REST API, and ASI:One Interactive Protocol

---

## 1. Executive Summary & Network Distinction

KrishiBazaar is architected as an autonomous multi-agent negotiation network designed to empower Indian farmers. It bridges information asymmetry through real-time APMC mandi intelligence, Open-Meteo transit hazard evaluation, multi-buyer benchmark procurement bids, capacity-matched freight quoting, and deterministic net-return optimization.

### Orchestrator vs. Specialist Agent Hierarchy

A critical architectural distinction exists between the **Conversational Orchestrator Gateway** and the **Six Specialist Agents**:

```
                              +---------------------------------------+
                              |      Farmer / User via ASI:One        |
                              +---------------------------------------+
                                                  â”‚
                                                  â”‚ (AgentChatProtocol - ACP V1)
                                                  â–¼
                              +---------------------------------------+
                              |         KRISHIBAZAAR ORCHESTRATOR     |
                              |               (Port 8000)             |
                              |  - Session & Context State Manager    |
                              |  - ASI:One Intent Parser (asi1-mini)  |
                              |  - Deterministic Profit Optimizer     |
                              |  - Human Approval Safety Gate         |
                              +---------------------------------------+
                                                  â”‚
           â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
           â”‚                 â”‚                    â”‚                   â”‚                 â”‚
           â–¼                 â–¼                    â–¼                   â–¼                 â–¼
   +---------------+ +---------------+   +------------------+ +---------------+ +---------------+
   | Market Agent  | | Weather Agent |   | Buyer Agents     | | Transport Agt | | Payment Agt   |
   | (Port 8001)   | | (Port 8002)   |   | A (8003) B (8005)| | (Port 8004)   | | (Port 8006)   |
   | Agmarknet     | | Open-Meteo    |   | FPO / Export     | | Fleet Matched | | On-Chain      |
   | Live Mandis   | | Route Spoilage|   | Simulated Bids   | | Logistics     | | Micro-Fee     |
   +---------------+ +---------------+   +------------------+ +---------------+ +---------------+
```

1. **The Orchestrator Agent (Gateway)**:
   - Does **not** provide domain-specific market or logistics calculations itself.
   - Acts as the farmer-facing chat interface implementing the official Fetch.ai **Agent Chat Protocol (ACP V1)** and ASI:One interactive card generation (Carousel, Review, Detail, Form cards).
   - Coordinates the six specialists, runs deterministic net-payout arithmetic, enforces the human confirmation safety gate, and maintains session state in SQLite.
2. **The Six Specialist Agents (Domain Microservices)**:
   - Run as headless domain services listening on dedicated local HTTP ports (`8001`â€“`8006`).
   - Communicate synchronously using Pydantic message models over standard uAgents protocols.
   - Do not directly interface with the farmer chat; they respond to structured queries from the Orchestrator (or other authorized network agents).

> [!IMPORTANT]
> Writing this documentation does **not** automatically publish or register any agent on Agentverse or the Fetch.ai Almanac. Publication and registration require manual developer configuration in the Agentverse web dashboard and active network credentials, as detailed below.

---

## 2. Verified Agent Inventory & Configuration Matrix

This inventory is verified against the codebase ([`config.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/config.py), [`resolver.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/agents/resolver.py), and the respective entry-point files in `src/krishibazaar/agents/`).

| Agent Name | Role / Function | Entry Point File | Port | Protocol Spec & Version | Local HTTP Submit Endpoint | Required Configuration Variables |
| :--- | :--- | :--- | :---: | :--- | :--- | :--- |
| **KrishiBazaar Orchestrator** | Conversational Gateway & Coordinator | `src/krishibazaar/agents/orchestrator.py` | `8000` | `AgentChatProtocol`<br>`(ACP V1)` | `http://127.0.0.1:8000/submit` | `ORCHESTRATOR_AGENT_SEED`<br>`ORCHESTRATOR_PORT`<br>`ASI_ONE_API_KEY`<br>`DATABASE_PATH` |
| **Market Specialist** | Live APMC Mandi wholesale price intelligence | `src/krishibazaar/agents/market_agent.py` | `8001` | `KrishiBazaarMarketProtocol`<br>`(v0.1.0)` | `http://127.0.0.1:8001/submit` | `MARKET_AGENT_SEED`<br>`MARKET_AGENT_PORT`<br>`DATAGOV_API_KEY` |
| **Weather Specialist** | Transit weather forecasting & spoilage hazard scoring | `src/krishibazaar/agents/weather_agent.py` | `8002` | `KrishiBazaarWeatherProtocol`<br>`(v0.1.0)` | `http://127.0.0.1:8002/submit` | `WEATHER_AGENT_SEED`<br>`WEATHER_AGENT_PORT`<br>`OPEN_METEO_BASE_URL` |
| **Buyer A Specialist** | FPO and regional retail benchmark procurement bids | `src/krishibazaar/agents/buyer_agent.py` | `8003` | `KrishiBazaarBuyerProtocol`<br>`(v0.1.0, profile="buyer_a")` | `http://127.0.0.1:8003/submit` | `BUYER_AGENT_SEED`<br>`BUYER_AGENT_PORT` |
| **Transport Specialist** | Fleet payload matching & weather-aware freight quotes | `src/krishibazaar/agents/transport_agent.py` | `8004` | `KrishiBazaarTransportProtocol`<br>`(v0.1.0)` | `http://127.0.0.1:8004/submit` | `TRANSPORT_AGENT_SEED`<br>`TRANSPORT_AGENT_PORT` |
| **Buyer B Specialist** | Corporate agribusiness & export volume procurement bids | `src/krishibazaar/agents/buyer_b_agent.py` | `8005` | `KrishiBazaarBuyerProtocol`<br>`(v0.1.0, profile="buyer_b")` | `http://127.0.0.1:8005/submit` | `BUYER_B_AGENT_SEED`<br>`BUYER_B_AGENT_PORT` |
| **Payment Specialist** | Fetch.ai testnet micro-fee payee & settlement engine | `src/krishibazaar/agents/payment_agent.py` | `8006` | `AgentPaymentProtocol`<br>`(v0.1.0, role='seller')` | `http://127.0.0.1:8006/submit` | `PAYMENT_PAYEE_SEED`<br>`PAYMENT_AGENT_PORT`<br>`PAYMENT_PAYEE_WALLET_ADDRESS` |

### Verified Status & Unknowns:
- **Verified Locally**: All 7 agent processes bind to non-conflicting TCP ports (`8000`â€“`8006`), resolve deterministically via `KrishiBazaarResolver`, and pass the full test suite (`298 passed`).
- **Unknown / Requires Live Agentverse Configuration**: Specific user Agentverse Mailbox pairing tokens, public cloud deployment URLs, and live Almanac smart contract registration states.

---

## 3. Agentverse Deployment Paradigms: Local, Mailbox, Hosted, and Almanac

Understanding the distinction between deployment models is critical for reliable agent operations:

```
+---------------------------------------------------------------------------------------------------------+
|                                  FETCH.AI AGENT DEPLOYMENT SPECTRUM                                     |
+---------------------------------------------------------------------------------------------------------+
|                                                                                                         |
| 1. LOCAL AGENT              2. EXTERNALLY INTEGRATED         3. HOSTED AGENT        4. ALMANAC          |
|    (Development)               (Agentverse Mailbox)             (Agentverse Cloud)     REGISTRATION     |
|                                                                                                         |
|  [Host Machine]              [Host Machine]                   [Agentverse Cloud]    [Fetch.ai Ledger]   |
|   +---------------+           +---------------+                +---------------+    +-----------------+ |
|   | Agent Process |           | Agent Process |                | MicroVM / Pod |    | Smart Contract  | |
|   | Port: 800X    |           | (Outbound WSS)|                | Runs Agent.py |    | agent1... ->    | |
|   +---------------+           +-------â”¬-------+                +---------------+    | public endpoint | |
|           â”‚                           â”‚                                â”‚            +-----------------+ |
|           â–¼                           â–¼                                â–¼                     â–²          |
|   Loopback (127.0.0.1)      Agentverse Mailbox              Public HTTP Endpoint             â”‚          |
|   Reachable ONLY on host.   Reverse-proxies public          Managed 24/7 by Fetch.ai.        â”‚          |
|   Zero external ingress.    inbound messages to agent.      No local server needed.          â”‚          |
|                                       â”‚                                â”‚                     â”‚          |
|                                       â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜          |
|                                                     Announced & Resolvable Globally                     |
+---------------------------------------------------------------------------------------------------------+
```

### Detailed Breakdown

1. **Local Agent (Local Dev Mode)**:
   - Runs exclusively on `127.0.0.1` listening on local ports (`8000`â€“`8006`).
   - Suitable for offline unit testing, automated pytest runs, and multi-process PowerShell demonstrations using `KrishiBazaarResolver`.
   - **Limitation**: Not discoverable or contactable by remote agents outside the local host.

2. **Externally Integrated Agent (Agentverse Mailbox)**:
   - Code executes on developer hardware (Windows, Linux, or on-prem server).
   - Establishes a persistent outbound secure WebSocket (`wss://`) or gRPC connection to the Agentverse Mailbox server (`https://agentverse.ai/v2/agents/mailbox/submit`).
   - When external agents dispatch messages to the agent's `agent1...` address, Agentverse buffers the message and delivers it down the active mailbox connection.
   - **Key Advantage**: Zero firewall reconfiguration, port-forwarding, or public IP needed. Ideal for KrishiBazaar hackathon execution.

3. **Hosted Agent (Agentverse Cloud)**:
   - The Python code itself is uploaded directly to the Agentverse online IDE and runs inside a containerized sandbox managed by Fetch.ai.
   - Uptime, scaling, and public endpoints are completely managed by Agentverse.
   - **Trade-off**: Requires restructuring custom repository modules (`services/`, `models.py`, `storage.py`) into self-contained single-file or package bundles supported by the hosted environment.

4. **Almanac Contract Registration**:
   - The Fetch.ai Almanac is a decentralized smart contract on the Fetch.ai blockchain (`fetchhub-4` mainnet or `dorado-1` testnet).
   - Acts as an on-chain DNS directory mapping an agent's Bech32 address (`agent1...`) to its public endpoint and protocol manifest digests.
   - Registration requires:
     - Cryptographic signature using the agent's private key.
     - FET tokens to pay on-chain registration transaction gas fees (unless synchronized automatically via Agentverse API).
   - **Crucial Rule**: Writing documentation or starting an agent with `mailbox=True` does **not** complete Almanac registration. Almanac registration is finalized only when an on-chain registration transaction succeeds or the Agentverse Almanac service caches and broadcasts the record.

---

## 4. Prerequisites for Agentverse Publishing & Public Reachability

Before registering agents in Agentverse, ensure the following prerequisites are met:

### A. Agentverse Account & API Key
1. Register an account at [Agentverse.ai](https://agentverse.ai).
2. Navigate to **Profile Settings &rarr; API Keys**.
3. Generate a new API Key with agent management permissions.
4. Record the key safely. Keep it strictly inside `.env` (`AGENTVERSE_API_KEY`); **never** commit it to public source control.

### B. Agent Identity & Deterministic Seeds
Every uAgent derives its public Bech32 address (`agent1...`) and signing keys deterministically from a private seed phrase:
- For consistent agent identity across server restarts, unique seeds must be provided in `.env`:
  - `ORCHESTRATOR_AGENT_SEED`
  - `MARKET_AGENT_SEED`
  - `WEATHER_AGENT_SEED`
  - `BUYER_AGENT_SEED`
  - `BUYER_B_AGENT_SEED`
  - `TRANSPORT_AGENT_SEED`
  - `PAYMENT_PAYEE_SEED`
- *Security Rule*: Never reuse seeds across environments. Never use default development seeds in production.

### C. Protocol Compatibility & Manifests
Agents announce their supported protocols to Agentverse via **Protocol Manifests**:
- uAgents automatically computes cryptographic schema digests for all message models (`on_message` and `replies`).
- In the factory constructors, `publish_agent_details=True` and `publish_manifest=True` ensure that upon connecting to Agentverse, the manifest is submitted so other agents know what payloads this agent accepts.

### D. Public Reachability (Mailbox vs. Webhook)
- **Recommended Method**: Use **Agentverse Mailbox** (`USE_MAILBOX=true` in `.env`). This requires zero network ingress configuration.
- **Alternative Method (Direct Public Webhook)**: If using public HTTP endpoints instead of Mailbox, you must provide a publicly accessible HTTPS URL (e.g., via AWS ALB, Nginx, or Cloudflare Tunnel) configured in `Agent(endpoint=["https://agent.example.com/submit"])`.

---

## 5. Step-by-Step Agentverse v2 Publishing Instructions

Follow this workflow to establish Agentverse Mailbox integration for KrishiBazaar agents:

### Step 1: Obtain Mailbox Connection for an Agent
1. Log in to [Agentverse.ai](https://agentverse.ai).
2. Go to **My Agents** and click **Local Agents** (or **External Agents**).
3. Click **+ New External Agent** or **Add Mailbox**.
4. Name the agent according to its role (e.g., `KrishiBazaar Orchestrator`).
5. Agentverse will prompt for the Agent's Bech32 address (`agent1...`) or generate a pairing connection string.
6. Copy the assigned **Mailbox Key / Connection String**.

### Step 2: Configure Local Environment (`.env`)
Update `.env` with the Agentverse credentials:

```bash
# Agentverse platform credentials
AGENTVERSE_API_KEY="your_agentverse_api_key_here"
AGENTVERSE_URL="https://agentverse.ai"
USE_MAILBOX=true

# Set deterministic agent seeds
ORCHESTRATOR_AGENT_SEED="your_secret_orchestrator_seed_here"
MARKET_AGENT_SEED="your_secret_market_seed_here"
WEATHER_AGENT_SEED="your_secret_weather_seed_here"
BUYER_AGENT_SEED="your_secret_buyer_seed_here"
BUYER_B_AGENT_SEED="your_secret_buyer_b_seed_here"
TRANSPORT_AGENT_SEED="your_secret_transport_seed_here"
PAYMENT_PAYEE_SEED="your_secret_payee_seed_here"
```

### Step 3: Publish Protocol Manifests
When initializing agents, verify that manifest publishing is enabled:
```python
# In agent factory (e.g., create_market_agent):
agent = Agent(
    name=name,
    seed=agent_seed,
    port=agent_port,
    mailbox=use_mailbox,
    agentverse=agentverse_config,
    publish_agent_details=True,
    description="KrishiBazaar Market Specialist: Live Mandi Wholesale Price Intelligence",
)
agent.include(market_proto, publish_manifest=True)
```

### Step 4: Register in Almanac Directory
1. In the Agentverse dashboard under **Local Agents**, locate the connected agent.
2. If the agent status shows **Connected (Mailbox Active)**, click **Register in Almanac**.
3. Select the appropriate network (Mainnet or Dorado Testnet).
4. Review the announced protocols and endpoints. Confirm the registration.
5. Once registered, the agent will receive a green **Almanac Registered** status badge.

---

## 6. Agent-by-Agent Publication & Verification Checklist

Use this checklist during manual Agentverse registration:

### Agent 1: KrishiBazaar Orchestrator (Port 8000)
- [ ] **Address Verified**: Run `python src/krishibazaar/agents/orchestrator.py` and capture its `agent1...` address from the startup banner.
- [ ] **Mailbox Created**: Register the address in Agentverse as `KrishiBazaar Orchestrator`.
- [ ] **Protocols Published**: Verify `AgentChatProtocol` (ACP V1) manifest appears in Agentverse.
- [ ] **ASI:One Card Support**: Ensure `AgentChatProtocol` specifies card compatibility in its capabilities.
- [ ] **End-to-End Test**: Send a test chat message from the ASI:One interface or Agentverse chat playground.

### Agent 2: Market Specialist (Port 8001)
- [ ] **Address Verified**: Run `python src/krishibazaar/agents/market_agent.py` and capture the `agent1...` address.
- [ ] **Mailbox Created**: Register in Agentverse as `KrishiBazaar Market Specialist`.
- [ ] **Protocols Published**: Verify `KrishiBazaarMarketProtocol` (`MarketQuery` &rarr; `MandiPriceResult`) manifest is visible.
- [ ] **Mandi Feed Checked**: Confirm data.gov.in API key is active or benchmark fallback is operational.
- [ ] **Communication Test**: Dispatch a test `MarketQuery(commodity='onion', district='Nashik', state='Maharashtra')` and verify `MandiPriceResult` reply.

### Agent 3: Weather Specialist (Port 8002)
- [ ] **Address Verified**: Run `python src/krishibazaar/agents/weather_agent.py` and capture the `agent1...` address.
- [ ] **Mailbox Created**: Register in Agentverse as `KrishiBazaar Weather Specialist`.
- [ ] **Protocols Published**: Verify `KrishiBazaarWeatherProtocol` (`WeatherQuery` &rarr; `WeatherResult`) manifest is visible.
- [ ] **Open-Meteo Checked**: Confirm REST connection to Open-Meteo functions without errors.
- [ ] **Communication Test**: Dispatch a test `WeatherQuery(location='Nashik, Maharashtra', target_date='tomorrow')` and verify `WeatherResult` reply.

### Agent 4: Buyer A Specialist (Port 8003)
- [ ] **Address Verified**: Run `python src/krishibazaar/agents/buyer_agent.py` and capture the `agent1...` address.
- [ ] **Mailbox Created**: Register in Agentverse as `KrishiBazaar Buyer Specialist (FPO/Retail)`.
- [ ] **Protocols Published**: Verify `KrishiBazaarBuyerProtocol` (`BuyerQuery` &rarr; `BuyerOffersResult`) manifest is visible.
- [ ] **Bid Models Checked**: Confirm benchmark wholesale bids generate with explicit simulation tags.
- [ ] **Communication Test**: Dispatch a test `BuyerQuery(crop='onion', quantity_quintals=50.0, origin_location='Nashik')` and verify `BuyerOffersResult` reply.

### Agent 5: Transport Specialist (Port 8004)
- [ ] **Address Verified**: Run `python src/krishibazaar/agents/transport_agent.py` and capture the `agent1...` address.
- [ ] **Mailbox Created**: Register in Agentverse as `KrishiBazaar Transport Specialist`.
- [ ] **Protocols Published**: Verify `KrishiBazaarTransportProtocol` (`TransportQuery` &rarr; `TransportQuotesResult`) manifest is visible.
- [ ] **Fleet Sizing Checked**: Confirm vehicle capacity models (Tata Ace/407/17ft) execute cleanly.
- [ ] **Communication Test**: Dispatch a test `TransportQuery(origin='Nashik', destinations=['Nashik Mandi'], quantity_quintals=50.0)` and verify quotes reply.

### Agent 6: Buyer B Specialist (Port 8005)
- [ ] **Address Verified**: Run `python src/krishibazaar/agents/buyer_b_agent.py` and capture the `agent1...` address.
- [ ] **Mailbox Created**: Register in Agentverse as `KrishiBazaar Buyer B Specialist (Corporate/Export)`.
- [ ] **Protocols Published**: Verify `KrishiBazaarBuyerProtocol` (`profile="buyer_b"`) manifest is visible.
- [ ] **Corporate Bids Checked**: Confirm bulk export aggregator bids generate with explicit simulation tags.
- [ ] **Communication Test**: Dispatch a test `BuyerQuery(crop='onion', quantity_quintals=50.0, origin_location='Nashik')` with `profile="buyer_b"` and verify reply.

### Agent 7: Payment & Settlement Specialist (Port 8006)
- [ ] **Address Verified**: Run `python src/krishibazaar/agents/payment_agent.py` and capture the payee `agent1...` address.
- [ ] **Wallet Whitelist Checked**: Confirm `PAYMENT_PAYEE_WALLET_ADDRESS` (`fetch1...`) is designated in `.env`.
- [ ] **Protocols Published**: Verify official `AgentPaymentProtocol` (`role='seller'`) manifest is visible.
- [ ] **Safety Gating Maintained**: Verify `ENABLE_PAYMENT_PROTOCOL=false` remains intact until authorized.
- [ ] **Reconciliation Check**: Confirm CosmPy `LedgerClient` connects to Dorado testnet RPC (`grpc+https://grpc-dorado.fetch.ai`).

---

## 7. Safe Troubleshooting Guide

| Issue / Observed Output | Root Cause | Safe Remediation Procedure |
| :--- | :--- | :--- |
| **`ERROR: Failed to retrieve messages: 401 Unauthorized`** | Expired, invalid, or missing Agentverse mailbox token/API key in `.env`. | 1. Log in to [Agentverse.ai](https://agentverse.ai).<br>2. Re-generate the Mailbox API token.<br>3. Update `AGENTVERSE_API_KEY` in `.env`.<br>4. *Note for local demos*: Local HTTP inter-agent messaging operates completely independent of mailbox authentication. |
| **`WARNING: I do not have enough funds to register on Almanac contract`** | Agent wallet has zero FET tokens required to sign on-chain contract transactions. | 1. If running on Testnet, obtain test tokens from the [Fetch.ai Dorado Faucet](https://faucet-dorado.fetch.ai).<br>2. Alternatively, rely on Agentverse REST API registration which does not require direct on-chain gas expenditure.<br>3. In local multi-process execution, `KrishiBazaarResolver` handles direct routing without Almanac contract queries. |
| **`502 Bad Gateway` / `504 Gateway Timeout` on Agentverse** | Cloudflare or Agentverse backend API transient network degradation. | 1. KrishiBazaar's built-in `KrishiBazaarResolver` automatically bypasses remote Almanac lookups for known agents.<br>2. Restart the local agent; it will fall back to local direct endpoints (`http://127.0.0.1:800X/submit`). |
| **`Address resolution timed out after 3.0s`** | Upstream Almanac query hung due to network latency. | 1. Ensure `ALLOW_DIRECT_SERVICE_FALLBACK=true` in `.env`.<br>2. Orchestrator will automatically proceed using direct local services, ensuring uninterrupted farmer demonstrations. |
| **`Port already in use: [WinError 10048]`** | An orphaned background agent process is still bound to the port. | 1. Open PowerShell and run:<br>`Get-NetTCPConnection -State Listen \| Where-Object { $_.LocalPort -ge 8000 -and $_.LocalPort -le 8006 }`<br>2. Terminate the process using: `Stop-Process -Id <PID> -Force`. |
| **`UnicodeEncodeError: 'charmap' codec can't encode characters`** | Windows console default encoding (CP1252) cannot render Rupee symbol (â‚¹) or Hindi text. | Run `$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8` and `chcp 65001` before launching agent processes. |

---

## 8. Status Summary: Verified Local vs. Pending Manual Dashboard Action

| Component / Layer | Verified Locally in Codebase | Requires Manual Action in Agentverse |
| :--- | :---: | :---: |
| **Agent Port Allocations (8000â€“8006)** | âœ… Verified (Active & Listening) | N/A (Local Ingress) |
| **Deterministic Seed Identity Derivation** | âœ… Verified (`Identity.from_seed`) | Set production seeds in `.env` |
| **Pydantic Message Schemas & Serialization** | âœ… Verified (100% compliant) | Auto-submitted on connection |
| **Inter-Agent Direct HTTP Routing (`KrishiBazaarResolver`)** | âœ… Verified (All 6 Specialists) | N/A (Bypasses Almanac locally) |
| **Payment Safety Boundary (`ENABLE_PAYMENT_PROTOCOL=false`)** | âœ… Verified (Enforced in tests) | Do NOT enable without funded wallet |
| **Agentverse Mailbox Pairing Tokens** | âš ï¸ Placeholder in `.env.example` | â³ Must generate in Agentverse Dashboard |
| **Almanac Contract Directory Registration** | âš ï¸ Bypassed locally via Resolver | â³ Must click "Register in Almanac" in UI |
| **ASI:One Interactive Discovery Listing** | âš ï¸ Schema definitions verified | â³ Requires Agentverse team catalog review |

---

## 9. Official Documentation & Reference Links

1. **Fetch.ai Core Documentation Platform**:
   [https://fetch.ai/docs](https://fetch.ai/docs)
2. **uAgents Framework Reference**:
   [https://fetch.ai/docs/concepts/agents/agents-intro](https://fetch.ai/docs/concepts/agents/agents-intro)
3. **Agentverse Portal & Agent Management**:
   [https://agentverse.ai](https://agentverse.ai)
4. **Agentverse Mailbox Integration Guide**:
   [https://fetch.ai/docs/guides/agents/intermediate/mailbox](https://fetch.ai/docs/guides/agents/intermediate/mailbox)
5. **Almanac Registration & Agent Discovery**:
   [https://fetch.ai/docs/guides/agents/register-in-almanac](https://fetch.ai/docs/guides/agents/register-in-almanac)
6. **Agent Chat Protocol (ACP) & ASI:One Integration**:
   [https://fetch.ai/docs/guides/agentverse/agent-chat-protocol](https://fetch.ai/docs/guides/agentverse/agent-chat-protocol)
7. **Fetch.ai Dorado Testnet Faucet & Explorer**:
   [https://faucet-dorado.fetch.ai](https://faucet-dorado.fetch.ai) | [https://explore-dorado.fetch.ai](https://explore-dorado.fetch.ai)
