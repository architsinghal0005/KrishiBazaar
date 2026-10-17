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

### Phase 1: Project Foundation & Architecture (Current)
- [x] Standard `src/krishibazaar` package layout with `agents/` and `services/`.
- [x] Strict configuration management with Pydantic validation (`config.py`).
- [x] Comprehensive domain models (`models.py`) with audit flags and disclaimers.
- [x] SQLite persistence layer for conversations and booking ledger (`storage.py`).
- [x] Safe `.gitignore` and `.env.example` preventing secret leaks.
- [x] Standard `pyproject.toml` and test suite.

### Phase 2: Deterministic Net-Return Optimizer
- [ ] Implement `services/optimizer.py` with pure, deterministic financial logic.
- [ ] Account for APMC cess (typically 1-2%), loading/unloading (hamali), vehicle freight, and weather spoilage risk buffers.
- [ ] Implement transparent ranking logic (Mandi vs Direct Buyers).
- [ ] Comprehensive unit tests with zero-tolerance for floating-point inaccuracies.

### Phase 3: External API Services (Market & Weather)
- [ ] `services/market.py`: Official Agmarknet / data.gov.in client with verified benchmark fallback cache.
- [ ] Explicit provenance metadata tagging (`data.gov.in_live` vs `official_benchmark_cache`).
- [ ] `services/weather.py`: Open-Meteo client assessing precipitation probability and transit rain hazard.
- [ ] Network failure handling, timeouts, and fallback resilience.

### Phase 4: Specialist Sub-Agents (Market, Weather, Buyer, Transport)
- [ ] `agents/market_agent.py`: uAgent serving mandi price inquiries.
- [ ] `agents/weather_agent.py`: uAgent serving transit hazard evaluations.
- [ ] `agents/buyer_agent.py`: uAgent returning structured institutional buyer bids with explicit benchmark disclaimers.
- [ ] `agents/transport_agent.py`: uAgent returning freight quotes and managing booking creation.
- [ ] Typed inter-agent communication protocols.

### Phase 5: KrishiBazaar Orchestrator & ASI:One Chat Protocol (ACP)
- [ ] `agents/orchestrator.py`: Native ASI:One agent implementing `AgentChatProtocol` (`chat_protocol_spec`).
- [ ] Natural language parsing of crop, quantity, and location.
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
