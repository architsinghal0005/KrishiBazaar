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

### Phase 5: Multi-Agent Sub-Agents (Market, Buyer, Transport) & ASI:One Integration

- [ ] Multi-agent coordination and financial ranking synthesis.
- [ ] ASI:One Interactive Card payloads & structured markdown presentation.
- [ ] Strict farmer approval gate: requires explicit confirmation before executing booking.

### Phase 6: Booking Execution, Persistence & Audit Trail
- [ ] Handle booking confirmation, issue verifiable tracking tokens, and persist to SQLite ledger.
- [ ] Booking history retrieval for farmers.

### Phase 7: Multi-Agent Local Simulation & Agentverse Deployment
- [ ] Multi-agent runner script for local development on Windows (`run_network.py`).
- [ ] Agentverse registration, Almanac manifest publishing, and Mailbox guide.
- [ ] End-to-end simulation test suite.

---

## 4. Architectural Rules & Compliance
- **No LLM Money Guesswork:** Financial math is strictly deterministic in `optimizer.py`.
- **Transparency:** All benchmark buyer offers and simulated bookings are explicitly declared as simulated benchmarks.
- **Farmer Approval:** Logistics bookings cannot execute without explicit farmer confirmation.
- **Security:** Private seeds, tokens, and keys are strictly maintained in `.env`.
