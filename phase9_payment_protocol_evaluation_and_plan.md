# Phase 9: Fetch.ai Agent Payment Protocol Evaluation & Deferral Report (Corrected)

**Evaluation Date:** October 21, 2026
**Project:** KrishiBazaar (Farm-to-Market Multi-Agent Network)
**Environment:** Python 3.13.15 (`.venv313`) | `uagents` 0.26.1 | `uagents-core` 0.2.14
**Integration Status:** **DEFERRED** (Prerequisites for live or sandbox settlement rails are currently unavailable; existing non-payment workflow is 100% verified and operational).

---

## 1. Non-Payment Booking Workflow Audit (Preserved & Passing)

The complete non-payment booking workflow was audited across the existing implementation ([`orchestrator.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/agents/orchestrator.py), [`booking_service.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/services/booking_service.py), and [`storage.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/storage.py)). All eight stages pass without regression:

| Stage | Verification Objective | Primary Test Cases | Result |
| :--- | :--- | :--- | :--- |
| **1. Selling Request & Ranking** | Produce inquiry parsing, multi-agent coordination, and deterministic net-return ranking | `test_phase7_interaction.py::test_full_inquiry_flow_generates_carousel_card` | **PASS** |
| **2. Option Selection & Proposal Generation** | Selecting a ranked channel generates a structured `BookingProposal` (15m expiry) | `test_booking.py::test_create_booking_proposal_and_expiry`, `test_phase7_interaction.py::test_option_selection_via_card_cta_generates_review_card` | **PASS** |
| **3. Pending Proposal Display** | Itemized gross value, transport, APMC cess, hamali, and net payout in `ReviewCard` | `test_booking.py::test_format_proposal_summary_itemization_and_provenance` | **PASS** |
| **4. Ambiguous Acknowledgement Safety Gate** | Casual messages (`ok`, `yes`, `thanks`, `hi`) in `AWAITING_APPROVAL` are rejected and do **not** confirm booking | `test_booking.py::test_orchestrator_ambiguous_or_casual_message_rejected_in_awaiting_approval`, `test_phase7_interaction.py::test_casual_message_blocked_by_approval_safety_gate` | **PASS** |
| **5. Explicit Approval Requirement** | Strict confirmation syntax required (`CONFIRM <PROP-ID>`, `approve booking`, or Card CTA) | `test_booking.py::test_orchestrator_explicit_approval_accepted_flow`, `test_phase7_interaction.py::test_explicit_approval_via_card_cta` | **PASS** |
| **6. Cancellation & Expiration** | Explicit cancellation marks proposal consumed; expired proposals (>15m) are strictly blocked | `test_booking.py::test_orchestrator_cancel_pending_proposal`, `test_booking.py::test_orchestrator_expired_proposal_rejected` | **PASS** |
| **7. Duplicate & Replay Protection** | Replayed approval messages on consumed proposals are rejected; idempotency guaranteed | `test_booking.py::test_orchestrator_duplicate_approval_rejected_idempotency`, `test_phase7_interaction.py::test_duplicate_approval_attempt_blocked` | **PASS** |
| **8. Status & Ledger Persistence** | Confirmed bookings create persistent `BookingRecord` in SQLite with unique `KB-BOOK-` ID and tracking token | `test_booking.py::test_orchestrator_status_lookup_flow`, `test_storage.py::test_booking_create_and_retrieve` | **PASS** |

### Verified Test Suite Execution
- **Booking & Interaction Suite:**
  ```powershell
  .venv313\Scripts\python.exe -m pytest tests/test_booking.py tests/test_phase7_interaction.py tests/test_storage.py -v
  ============================= 35 passed in 7.52s ==============================
  ```
- **Full Project Regression Suite:**
  ```powershell
  .venv313\Scripts\python.exe -m pytest -v
  ======================= 172 passed in 80.13s (0:01:20) ========================
  ```
Zero application code was altered. The non-payment workflow remains completely operational.

---

## 2. Official Fetch.ai Payment Documentation & Source Links

### Canonical Documentation Sources Inspected (October 21, 2026)
1. **Official uAgents Documentation Platform**:
   `https://uagents.fetch.ai/docs/`
2. **Fetch.ai Core Developer Documentation**:
   `https://fetch.ai/docs`
3. **uAgents Payment Protocol Specification**:
   `uagents_core.contrib.protocols.payment` (installed in `.venv313`, version `0.2.14`)
4. **CosmPy Aerial Ledger Client Reference**:
   `cosmpy.aerial.client.LedgerClient` (installed in `.venv313`)

### Protocol Specification in `uagents-core`
```python
# Protocol Name: "AgentPaymentProtocol", Version: "0.1.0"
payment_protocol_spec = ProtocolSpecification(
    name="AgentPaymentProtocol",
    version="0.1.0",
    interactions={
        RequestPayment: {CommitPayment, RejectPayment},
        CommitPayment: {CompletePayment, CancelPayment},
        CompletePayment: set(),
        CancelPayment: set(),
        RejectPayment: set(),
    },
    roles={
        "seller": {CommitPayment, RejectPayment},
        "buyer": {RequestPayment, CancelPayment, CompletePayment},
    },
)
```

---

## 3. Critical Architectural Separation: Protocol Messages vs. Payment Rails

A fundamental finding of this evaluation is the distinction between **protocol message exchange** and **value settlement**:

```
+--------------------------------------------------------------------------+
|                       AGENT PROTOCOL LAYER (uAgents)                     |
|                                                                          |
|   Seller Agent ---------------- RequestPayment ----------------> Buyer   |
|   Seller Agent <--------------- CommitPayment ------------------ Buyer   |
|   Seller Agent ---------------- CompletePayment ---------------> Buyer   |
+--------------------------------------------------------------------------+
                                    |
               CRITICAL VERIFICATION GAP (MUST NOT BE ASSUMED)
                                    |
                                    v
+--------------------------------------------------------------------------+
|                     ACTUAL SETTLEMENT RAIL (External)                    |
|                                                                          |
|   â€¢ On-Chain FET Transfer: cosmpy query_tx(tx_hash) on Fetch.ai Ledger   |
|   â€¢ Skyfire USDC: REST API call /api/v1/payments/verify with KYA token   |
|   â€¢ Stripe Checkout: stripe.checkout.Session.retrieve(cs_...) API call   |
+--------------------------------------------------------------------------+
```

### Why Protocol Messages Alone Are NOT Proof of Payment:
1. `CommitPayment`:
   - Contains `funds: Funds`, `recipient: str`, and `transaction_id: str`.
   - This message is **only an unverified claim** by the payer that a transaction was initiated.
   - Anyone can construct a `CommitPayment` message with a synthetic string (e.g. `transaction_id="fake_tx_123"`).
   - Receiving a `CommitPayment` does **not** move money and does **not** prove money was moved.
2. `CompletePayment`:
   - Emitted by the seller to acknowledge completion.
   - It is an application-level acknowledgement, **not** a bank receipt.
3. **How an Actual Transaction Must Be Verified Before Emitting `CompletePayment`**:
   - **On Fetch.ai Ledger (`fet_direct`)**: The seller must query the ledger using `LedgerClient.query_tx(tx_hash)` to verify:
     1. The transaction exists on-chain and status is successful (`code == 0`).
     2. The sender address matches the payer's verified address.
     3. The recipient address matches the seller's designated wallet.
     4. The transferred denom and amount (`atestfet` / `afet`) match the requested amount exactly.
     5. The transaction has reached block finality.
   - **On Skyfire (`skyfire`)**: The seller must call Skyfire's verification API (`POST /payments/verify`) using an authenticated API key to verify that the KYA token is valid and USDC funds were deposited into the seller's account.
   - **On Stripe (`stripe`)**: The seller must query `stripe.checkout.Session.retrieve(session_id)` and verify `payment_status == 'paid'`.

---

## 4. Payment Party Mapping & Role Analysis

In the KrishiBazaar agricultural network, payment flows must reflect real-world agricultural commerce, **not** agent communication routing:

```
+-----------------------------------------------------------------------------+
|                          COMMERCIAL PAYMENT MAPPING                         |
+-----------------------------------------------------------------------------+
|                                                                             |
|  1. Produce Purchase Transaction:                                           |
|     â€¢ Actual Payer:     Commercial Buyer (e.g., Reliance Fresh, ITC)       |
|     â€¢ Actual Recipient: Farmer (selling onions/wheat)                       |
|     â€¢ Currency Domain:  Indian Rupees (INR)                                 |
|                                                                             |
|  2. Freight Logistics Transaction:                                          |
|     â€¢ Actual Payer:     Farmer or Buyer (depending on delivery terms)       |
|     â€¢ Actual Recipient: Logistics Carrier (e.g., Nashik Kisan Logistics)    |
|     â€¢ Currency Domain:  Indian Rupees (INR)                                 |
|                                                                             |
+-----------------------------------------------------------------------------+
```

### Agent Identity Correction:
- **Market Specialist (`agent1q0rsn...`)**: An advisory API specialist querying AGMARKNET / data.gov.in price records. It is **NOT** a farmer, **NOT** a mandi trader, and **NOT** a bank account. **Routing payments to the Market Specialist address would misappropriate funds to an informational API bot.**
- **Weather Specialist (`agent1qw...`)**: An informational service querying Open-Meteo.
- **Transport Specialist (`agent1q2...`)**: An advisory fleet-matching agent.
- **Farmer**: The human user interacting via chat / ASI:One. The farmer does not possess a custodial crypto wallet in the current MVP.
- **Conclusion**: Recipient addresses must always be verified external destinations belonging to the actual seller/farmer or carrier, **never** KrishiBazaar internal specialist agents.

---

## 5. Currency & Rail Reality Check: Absence of INR Rails

1. **Protocol Models vs. Real Settlement**:
   `Funds.currency` is an arbitrary string field in `uagents-core` (e.g. `Funds(amount="152050", currency="INR")`). However:
   - **There is NO Indian Rupee (INR) settlement rail in Fetch.ai uAgents, Agentverse, or CosmPy.**
   - The Fetch.ai ledger operates on **FET** (or `atestfet` on testnet).
   - Skyfire operates on **USDC** (stablecoin).
   - Stripe operates on supported merchant currencies, but no Stripe merchant account, API key, or webhook listener is configured in KrishiBazaar.
2. **Current MVP Limitation**:
   KrishiBazaar models Indian wholesale produce where market prices, transport costs, and statutory mandi cess are calculated strictly in **INR**. Attempting to execute an "INR payment" via the Agent Payment Protocol without an authorized, credentialed payment gateway is impossible and must not be simulated as real.

---

## 6. Credentials & Environment Audit

| Credential / Resource | Status in `.env` / Environment | Impact |
| :--- | :--- | :--- |
| **Fetch.ai Funded Testnet Wallet** | **NOT AVAILABLE** (No funded Dorado testnet private key or seed) | Cannot submit on-chain test transactions |
| **Skyfire API Credentials** | **NOT AVAILABLE** (`SKYFIRE_API_KEY` absent) | Cannot verify USDC microtransactions |
| **Stripe API Credentials** | **NOT AVAILABLE** (`STRIPE_API_KEY` absent) | Cannot initiate or verify card checkout |
| **Dorado Testnet RPC Access** | Publicly accessible (`https://rpc-dorado.fetch.ai:443`) | Can query testnet, but cannot broadcast without a funded wallet |
| **Verified Test Recipient Address** | **NOT CONFIGURED** | No verified test recipient established |

In accordance with strict safety rules:
- **No private keys, seeds, or credentials were requested in chat.**
- **No real-money or uncredentialed transfers were attempted.**

---

## 7. Recommendation: Formal Deferral of Phase 9 Real-Transfer Integration

### Formal Decision: **DEFERRED**

Real-transfer integration is formally **DEFERRED** because:
1. No funded testnet wallet (`Dorado`) or test token faucet allocation is configured.
2. No sandbox credentials (Skyfire / Stripe) are provisioned.
3. No native INR payment gateway exists for the Fetch.ai Agent Payment Protocol.
4. KrishiBazaar's primary value is deterministic net-return advisory, transparent multi-agent discovery, and farmer decision support; executing unverified transactions would violate safety mandates.

### Configuration Safety:
- `ENABLE_PAYMENT_PROTOCOL=false` is enforced.
- No simulated transaction will ever be labeled or reported as a real financial settlement.
- The existing non-payment booking workflow, interactive cards, and explicit approval safety gates remain 100% active and verified.

---

## 8. Exact Prerequisites for Future Integration

When Fetch.ai payment integration is revisited in a dedicated future milestone, the following exact prerequisites must be satisfied before any code changes:

1. **Testnet Wallet Provisioning**:
   - Generate a dedicated testnet wallet identity specifically for the test payer:
     ```bash
     fetchd keys add test_payer_wallet --network dorado
     ```
   - Fund the wallet with test tokens via the official Fetch.ai Dorado Faucet (`https://faucet-dorado.fetch.ai`).
   - Store the key securely in `.env` as `FETCHAI_PAYER_PRIVATE_KEY` (never committed to git).
2. **Designated Test Recipient**:
   - Establish a verified test recipient wallet address (`fetch1...`) specifically designated as the test seller/farmer.
   - Add this address to an explicit whitelist in `config.py`. Under no circumstances should internal advisory agents (such as Market Specialist) be in this whitelist.
3. **Currency Conversion Layer**:
   - Implement an explicit conversion service (e.g. INR $\rightarrow$ FET or INR $\rightarrow$ USDC) with transparent exchange rate provenance, since agricultural bids are denominated in INR.
4. **On-Chain Settlement Verification Service**:
   - Implement a background verification worker using `cosmpy.aerial.client.LedgerClient` that queries `query_tx()` and verifies block inclusion, amounts, sender, and recipient before emitting `CompletePayment`.
5. **Dual Safety Gate**:
   - Require separate explicit approvals: Tier 1 for Booking Confirmation, Tier 2 for Payment Transfer Authorization.

---

## 9. Final Review Conclusion

- **Workflow Health:** Clean (35/35 booking/storage tests pass; 172/172 full suite tests pass).
- **Payment Evaluation:** Completed with accurate documentation, party mapping, and verification boundaries.
- **Safety Posture:** 100% secure. Zero financial transfers attempted, zero secrets exposed, zero application code broken.
- **Phase 10 Deployment:** Strictly held. No deployment actions will be taken in this phase.
