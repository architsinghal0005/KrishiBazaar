# KrishiBazaar - Project Plan & Multi-Agent Roadmap

Autonomous Farm-to-Market Negotiation Agent Network for Indian Farmers.
Engineered for native Fetch.ai ASI:One conversational interaction via the Agent Chat Protocol (ACP).

---

## 1. Project Mission

KrishiBazaar addresses the market asymmetry faced by Indian smallholder farmers. When a farmer harvests perishable crops (such as Onions, Tomatoes, or Potatoes), they often face distress selling at local APMC mandis due to lack of visibility into distant mandi prices, unpredictable weather conditions during transit, high logistics expenses, and opaque middlemen margins.

KrishiBazaar provides an autonomous negotiation network directly inside **ASI:One conversations** that:
1. Gathers and authenticates official Indian mandi price benchmarks (Agmarknet / data.gov.in).
2. Retrieves Open-Meteo forecasts to assess transit rain hazards and spoilage risks.
3. Obtains structured, competitive bids from institutional buyers (FPOs, retail chains, processing mills).
4. Solicits vehicle-capacity matched freight quotes from local transport providers.
5. Runs **deterministic financial optimization** to calculate net in-pocket returns for each route.
6. Presents an interactive comparison card and executes an approved test booking upon explicit farmer consent.

---

## 2. Multi-Agent System Architecture

```
+-------------------------------------------------------------------------+
|                              ASI:One Chat                               |
|                  Farmer interacts using natural language                |
+------------------------------------+------------------------------------+
                                     |
                                     | Agent Chat Protocol (ACP)
                                     v
                  +--------------------------------------+
                  |      KrishiBazaar Orchestrator       |
                  |     - Primary ASI:One endpoint       |
                  |     - Session & dialog management    |
                  |     - Coordination of sub-agents     |
                  |     - Approval verification gate     |
                  +---+--------------+--------------+----+
                      |              |              |
         +------------+              |              +------------+
         |                           |                           |
         v                           v                           v
+------------------+       +------------------+       +------------------+
|   Market Agent   |       |  Weather Agent   |       |   Buyer Agent    |
| - Mandi prices   |       | - Open-Meteo     |       | - Structured bids|
| - Agmarknet OGD  |       | - Transit hazard |       | - Farmgate pickup|
| - Benchmark cache|       | - Spoilage score |       | - Quality grades |
+------------------+       +------------------+       +------------------+
         |                           |                           |
         +------------+              |              +------------+
                      |              |              |
                      v              v              v
                  +--------------------------------------+
                  |       Transport & Logistics Agent    |
                  |     - Route distance calculation     |
                  |     - Freight quotes & vehicle match |
                  |     - Approved booking dispatch      |
                  +------------------+-------------------+
                                     |
                                     v
                  +--------------------------------------+
                  |     Net-Return Optimizer Engine      |
                  |   (Deterministic Python Module)      |
                  | - Gross = Quantity * Unit Price      |
                  | - Deduct: Transport, Cess, Labor     |
                  | - Net Return Ranking & Advice        |
                  +------------------+-------------------+
                                     |
                                     v
                  +--------------------------------------+
                  |         SQLite Audit Ledger          |
                  |   - Immutable booking record         |
                  |   - Cryptographic tracking token     |
                  +--------------------------------------+
```

---

## 3. Phased Implementation Milestones

### Phase 1: Project Foundation & Architecture (Completed)
- [x] Standard `src/krishibazaar` package layout with `agents/` and `services/`.
- [x] Strict configuration management with Pydantic validation (`config.py`).
- [x] Comprehensive domain models (`models.py`) with audit flags and disclaimers.
- [x] SQLite persistence layer for conversations and booking ledger (`storage.py`).
- [x] Safe `.gitignore` and `.env.example` preventing secret leaks.
- [x] Standard `pyproject.toml` and test suite.

### Phase 2: Minimal KrishiBazaar Orchestrator (Completed)
- [x] Core Orchestrator agent (`src/krishibazaar/agents/orchestrator.py` & `agents/orchestrator.py`).
- [x] Official Fetch.ai `AgentChatProtocol` v0.3.0 implementation (`chat_protocol_spec`).
- [x] Two-way messaging: `ChatMessage`, `ChatAcknowledgement`, and `EndSessionContent`.
- [x] Safe content extraction and secret-safe logging.
- [x] Agentverse mailbox connection and Almanac registration.

### Phase 3: Structured Intent Extraction using ASI:One (Completed)
- [x] ASI:One Developer API client (`https://api.asi1.ai/v1/chat/completions`) using `asi1-mini`.
- [x] Strict validation with `FarmerIntent` domain model (crop, quantity, origin, deadline, minimum net return, storage).
- [x] Targeted follow-up question generator for missing required fields (no guessing/inventing).
- [x] Resilient deterministic rule-based fallback when API credentials absent or on network errors.
- [x] Mocked pytest test suite (`tests/test_extraction.py`) and manual verification script (`smoke_test_asi1.py`).
- [x] Orchestrator integration: parses farmer queries and clarifies missing parameters.

### Phase 4: Market, Weather and Deterministic Profit Optimizer (Completed)
- [x] Official data.gov.in AGMARKNET daily mandi price service (`services/mandi_service.py` & `src/krishibazaar/services/mandi_service.py`).
- [x] Authentic benchmark fallback dataset across key crops (Onion, Tomato, Potato, Wheat, Soybean, Mustard, Cotton).
- [x] Open-Meteo forecast service (`services/weather_service.py` & `src/krishibazaar/services/weather_service.py`) with transit hazard evaluation.
- [x] Deterministic profit optimizer (`services/optimizer.py` & `src/krishibazaar/services/optimizer.py`) with exact formula:
      `expected_net_return = gross_value - transport_cost - applicable_market_charges - storage_cost - estimated_loss_cost`.
- [x] Farmer minimum net-return condition enforcement, multi-channel ranking, and transparent explanation engine.
- [x] Comprehensive data provenance tracking (`source`, `observation_date`, `retrieval_timestamp_utc`, `freshness_status`, `is_live`).
- [x] 50 unit tests across calculations, invalid inputs, missing fields, API errors, timeouts, and stale/sample records.

### Phase 5: Multi-Agent Sub-Agents (Market, Buyer, Transport) & Specialist Integration (Completed)
- [x] Market specialist (`MarketAgent` + `mandi_service.py`): Live/sample provenance, arrival dates, zero-record handling, suspicious-price flags.
- [x] Weather specialist (`WeatherAgent` + `weather_service.py`): Forecasts vs observed conditions, transit hazard scoring, timeout fallback.
- [x] Buyer specialist (`BuyerAgent` + `buyer_service.py`): Explicitly simulated benchmark buyer offers (FPO farmgate, retail, processing mills), mandatory simulation flags and disclaimers.
- [x] Transport specialist (`TransportAgent` + `transport_service.py`): Payload vehicle matching (Tata Ace, Bolero, Eicher, Multi-Axle), weather protection surcharges, simulated quotes.
- [x] Orchestration (`orchestrator.py`): Multi-agent concurrent dispatch with timeouts, partial-failure resilience, transparent status reporting.
- [x] Net-return optimizer integration: Reuses deterministic engine (`optimizer.py`), itemized deductions, minimum net-return condition enforcement, and "estimated net return" terminology.
- [x] Weather forecast transit-date alignment (`weather_service.py`): Parses selling deadline (e.g. "tomorrow", ISO dates), queries 7-day Open-Meteo forecast, aligns exact transit date, and displays transparent disclaimer if date is outside window.
- [x] APMC Mandi benchmark vs Buyer Offer distinction & Provenance labels: Clearly distinguishes Mandi price benchmarks from direct buyer offers, itemizes cost deduction assumptions (APMC cess, hamali labor, simulated freight), and strictly labels output as estimated net return (never guaranteed payout).
- [x] Strict farmer approval safety gate: No real booking, payment, or carrier dispatch without explicit farmer consent.
- [x] 80 unit tests covering specialist success, timeouts, unavailable data, simulated labels, date alignment, provenance labels, and multi-agent coordination.

### Phase 6: Booking Execution, Persistence & Audit Trail (Completed)
- [x] Dedicated booking service (`services/booking_service.py` & `src/krishibazaar/services/booking_service.py`) for proposal creation, confirmation, status lookup, and natural-language action parsing (`parse_booking_action`).
- [x] Time-bounded pending proposal lifecycle (`BookingProposal` model with 15-minute expiry `PROP-YYYYMMDD-XXXXXX`), stored in SQLite `pending_proposals` table.
- [x] Option selection workflow: Farmer selects ranked channel (e.g. *"Select option 1"*), generating an itemized proposal summary with gross value, transport, APMC cess, hamali labor, storage, and weather hazard deductions.
- [x] Strict explicit approval gate: Unambiguous affirmative confirmation required (e.g. *"Confirm booking"*, *"Approve"*). Casual greetings ("hi", "hello", "ok") are rejected with a reminder of the pending proposal.
- [x] Idempotency & race protection: Atomic SQLite proposal consumption (`consume_proposal`) prevents duplicate bookings on repeated approvals.
- [x] Verifiable simulated booking creation: Generates persistent `BookingRecord` in SQLite ledger with tracking ID (`KB-BOOK-YYYYMMDD-XXXXXX`), cryptographic token (`KB-TRK-XXXX`), and `is_simulated_booking = True`.
- [x] Status verification: Farmer can query simulated booking reference (e.g. *"Status of KB-BOOK-..."*) and receive audit status directly from SQLite ledger.
- [x] Proposal cancellation: Farmer can cancel pending proposals anytime (*"Cancel"*).
- [x] Transparency & safety: Mandatory simulation notices and advisory estimate warnings (never guaranteed returns; no real money, carrier, or buyer dispatched).
- [x] Automated test suite: 101 unit tests passed across models, storage, booking workflow, explicit approval, idempotency, expiry, status lookup, and specialist agent configurations.

### Phase 7: ASI:One Interactive Cards & Multi-Agent Interaction (Completed)
- [x] Agentverse mailbox and manifest publishing configuration for all specialist agents (`market_agent.py`, `weather_agent.py`, `buyer_agent.py`, `buyer_b_agent.py`, `transport_agent.py`).
- [x] ASI:One interactive cards: CarouselCard recommendations, ReviewCard proposal inspection, DetailCard receipts, and FormCard follow-ups with Markdown fallbacks.
- [x] Multi-turn session state management: Pending proposal retention across casual/ambiguous replies (`ok`, `yes`, `thanks`) without redisplaying produce inquiry prompts.
- [x] Mention-prefix sanitization and clean Agentverse payload routing.

### Phase 8: Production Readiness, Startup Diagnostics & Demo Hardening (Completed)
- [x] Diagnostic startup banners for all 6 agents auditing port, address, role, endpoints, and verifying zero private key/seed leakage.
- [x] Configuration validation in `diagnostics.py` validating port ranges, collision avoidance, and uAgents address formats.
- [x] Specialist communication failure isolation reporting in Orchestrator notices.
- [x] Multi-agent smoke test utility (`smoke_test.py`) with live pipeline mode and offline benchmark mode.
- [x] Provenance consistency review verifying live AGMARKNET modal prices (Dindori Vani @ â‚¹4,400) vs offline illustrative fixtures (Reliance Fresh @ â‚¹3,400).
- [x] 172 automated unit and regression tests passing with 100% compliance.

### Phase 9: Fetch.ai Agent Payment Protocol Evaluation & Deferral (Completed)
- [x] Non-payment booking workflow audit (35/35 tests passing across all 8 verification stages).
- [x] Official Fetch.ai documentation inspection (`uagents_core.contrib.protocols.payment`, `AgentPaymentProtocol` v0.1.0).
- [x] Architectural separation established: agent protocol envelope exchange vs external settlement rail verification (`cosmpy.aerial.client.LedgerClient.query_tx`).
- [x] Commercial payment-party mapping corrected (Buyer = Payer; Farmer = Recipient; Specialist agents = informational bots, never payees).
- [x] Currency reality check: Confirmed absence of native Indian Rupee (INR) settlement rails in Fetch.ai / CosmPy ecosystem.
- [x] Formal deferral of live/sandbox transfers due to absence of funded Dorado testnet wallet or Skyfire credentials.
- [x] Complete evaluation, safety criteria, and future extension prerequisites documented in `phase9_payment_protocol_evaluation_and_plan.md`.

### Phase 10: Multi-Agent Deployment & Final Delivery (Completed)
- [x] Agentverse publishing and Almanac registration guide for all specialist agents and orchestrator gateway (`docs/agentverse_almanac_registration_guide.md`). *Note: Completing this comprehensive guide provides the verified architectural blueprints, configuration matrices, and checklists; actual live Agentverse dashboard publishing and Almanac on-chain registration remain manual operator actions.*
- [x] Final multi-agent live demo script and operational runbook (`docs/phase10_multi_agent_demo_runbook.md`) covering startup commands, preflight checks, 6-step end-to-end demo flow, clean shutdown procedures, failure recovery, hackathon presentation narration, and final acceptance checklist.

---

## 4. Architectural Rules & Compliance
- **No LLM Money Guesswork:** Financial math is strictly deterministic in `optimizer.py`.
- **Transparency:** All benchmark buyer offers and simulated bookings are explicitly declared as simulated benchmarks.
- **Farmer Approval:** Logistics bookings cannot execute without explicit farmer confirmation.
- **Security:** Private seeds, tokens, and keys are strictly maintained in `.env`.
