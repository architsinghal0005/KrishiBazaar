# Phase 9B: Fetch.ai Agent Payment Protocol Implementation Guide

**Project:** KrishiBazaar
**Protocol Version:** `AgentPaymentProtocol` v0.1.0 (`uagents-core` 0.4.12, `uagents` 0.26.1, `cosmpy` 0.12.2)
**Status:** Implemented & Verified in Sandbox/Mock Environments
**Default Configuration:** Disabled (`ENABLE_PAYMENT_PROTOCOL=false`)

---

## 1. Executive Summary

In Phase 9B, KrishiBazaar integrated the official **Fetch.ai Agent Payment Protocol** (`uagents_core.contrib.protocols.payment`) into its multi-agent architecture. Rather than leaving payment integration as documentation or simulated mocks, a production-grade, maintainable payment subsystem was implemented in:
- [`src/krishibazaar/services/payment_service.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/services/payment_service.py)
- [`src/krishibazaar/models.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/models.py)
- [`src/krishibazaar/storage.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/storage.py)
- [`src/krishibazaar/config.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/src/krishibazaar/config.py)
- [`tests/test_payment_protocol_integration.py`](file:///c:/Users/ARCHIT%20SINGHAL/KrishiBazaar/tests/test_payment_protocol_integration.py)

This implementation adheres to all Fetch.ai protocol rules, maintains strict safety boundaries, enforces integer base-unit token arithmetic, requires explicit human authorization, and strictly separates protocol message envelopes from authoritative on-chain settlement verification.

---

## 2. Compatibility Audit & Verified Schemas

### Package Versions in `.venv313`
- **`uagents`**: `0.26.1`
- **`uagents-core`**: `0.4.12`
- **`cosmpy`**: `0.12.2`
- **`pydantic`**: `2.14.1`

### Installed Protocol Models (`uagents_core.contrib.protocols.payment`)

```python
class Funds(Model):
    amount: str                # Token base units or decimal as string (e.g. "1000")
    currency: str              # Denomination (e.g. "atestfet", "FET", "USDC")
    payment_method: str = "fet_direct"  # "fet_direct" or "skyfire"

class RequestPayment(Model):
    accepted_funds: list[Funds]  # Acceptable currency/amount combinations
    recipient: str             # Destination wallet address (fetch1...)
    deadline_seconds: int      # Validity window in seconds
    reference: str | None      # Unique payment request tracking ID
    description: str | None    # Service purpose / description
    metadata: dict | None      # Context metadata

class CommitPayment(Model):
    funds: Funds               # Committed payment funds
    recipient: str             # Destination wallet address
    transaction_id: str        # Transaction hash from broadcast
    reference: str | None      # Matching payment reference
    description: str | None    # Service description
    metadata: dict | None      # Context metadata

class RejectPayment(Model):
    reason: str | None         # Rejection explanation

class CancelPayment(Model):
    transaction_id: str | None # Linked tx hash if any
    reason: str | None         # Cancellation explanation

class CompletePayment(Model):
    transaction_id: str | None # Confirmed settled tx hash
```

### Protocol Roles & Allowed Interaction Handlers
Under `payment_protocol_spec` (v0.1.0):
- **Role `"seller"` (Payee / Service Provider)**: Permitted to receive incoming `CommitPayment` and `RejectPayment`.
- **Role `"buyer"` (Payer / Client)**: Permitted to receive incoming `RequestPayment`, `CompletePayment`, and `CancelPayment`.

---

## 3. Architecture & Safety Principles

```
  [Payee / Advisory Service]                          [Payer / Farmer Agent]
              â”‚                                                 â”‚
              â”‚ 1. RequestPayment(accepted_funds, ref)          â”‚
              â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–ºâ”‚
              â”‚                                                 â”‚ â”€â”€â”€â”
              â”‚                                                 â”‚    â”‚ 2. Store in DB (AWAITING_AUTH)
              â”‚                                                 â”‚    â”‚    Present Review Details
              â”‚                                                 â”‚ â—„â”€â”€â”˜
              â”‚                                                 â”‚
              â”‚                                  [Human Farmer / User]
              â”‚                                                 â”‚
              â”‚                                                 â”‚ 3. Explicit Human Approval:
              â”‚                                                 â”‚    "AUTHORIZE PAYMENT REF:...
              â”‚                                                 â”‚     AMOUNT:... CURRENCY:...
              â”‚                                                 â”‚     RECIPIENT:..."
              â”‚                                                 â”‚ â”€â”€â”€â–º (Rejects "ok", "yes", etc.)
              â”‚                                                 â”‚
              â”‚                                                 â”‚ â”€â”€â”€â” 4. Check Whitelist & Balance
              â”‚                                                 â”‚    â”‚    cosmpy: ledger.send_tokens()
              â”‚                                                 â”‚ â—„â”€â”€â”˜    capture tx_hash
              â”‚ 5. CommitPayment(tx_hash, ref)                  â”‚
              â”‚â—„â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
              â”‚                                                 â”‚
        â”€â”€â”€â”  â”‚                                                 â”‚
Authori-   â”‚  â”‚                                                 â”‚
tative     â”‚  â”‚                                                 â”‚
Ledger     â”‚  â”‚                                                 â”‚
Check      â”‚  â”‚                                                 â”‚
code==0    â”‚  â”‚                                                 â”‚
        â”€â”€â”€â”˜  â”‚                                                 â”‚
              â”‚ 6. CompletePayment(tx_hash)                     â”‚
              â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–ºâ”‚
              â”‚                                                 â”‚ 7. Mark SETTLED in DB
              â–¼                                                 â–¼
```

### 10 Critical Safety Rules Enforced in KrishiBazaar:
1. **Disabled by Default:** `ENABLE_PAYMENT_PROTOCOL=false`. Transfers cannot execute unless explicitly enabled.
2. **Mainnet Disabled:** Only `dorado-1` testnet (`atestfet`) is supported in test configurations.
3. **Recipient Whitelist Enforcement:** Payees must match `PAYMENT_RECIPIENT_WHITELIST`. Specialist agents (`Market`, `Weather`, `Buyer A`, `Buyer B`, `Transport`) are informational bots and cannot be paid.
4. **Explicit Human Approval Gate:** Only strings matching `AUTHORIZE PAYMENT REF:<ref> AMOUNT:<amt> CURRENCY:<cur> RECIPIENT:<rec>` authorize transfers. Casual replies (`ok`, `yes`, `thanks`, `proceed`, etc.) are unconditionally rejected.
5. **Integer Base Units:** All token transfers specify amounts as exact integer base units (`1000 atestfet`), never floating-point numbers.
6. **Isolated Storage:** Payments are persisted in `payment_records` in SQLite, completely separate from `booking_ledger`. Booking approval does not authorize payment; payment does not authorize booking.
7. **Idempotency & Replay Defense:** Once a payment reference transitions to `COMMITTED` or `SETTLED`, subsequent authorization attempts are rejected.
8. **No Message-Only Settlement:** Receiving `CommitPayment` or `CompletePayment` does not settle a payment. Authoritative settlement requires on-chain transaction lookup (`code == 0` and event verification).
9. **Timeout Reconciliation:** Uncertain or timed-out RPC queries transition to `AWAITING_RECONCILIATION`. Automatic retries are forbidden to prevent double-spending.
10. **Zero Secret Leakage:** Seeds, mnemonics, and private keys are never stored in the database, logged to consoles, or printed in error traces.

---

## 4. Credible Demonstration Monetization Flow

KrishiBazaar deliberately does **not** route estimated crop sale proceeds (e.g. â‚¹208,629) through this protocol. Those amounts represent mandi benchmark sales estimates, not confirmed buyer obligations.

Instead, the protocol is demonstrated via a realistic, low-risk advisory micro-fee:
- **Service Name:** `KRISHIBAZAAR_PREMIUM_ADVISORY_PASS`
- **Purpose:** On-chain access pass for premium multi-mandi arbitrage & route intelligence.
- **Demonstration Fee:** `1,000 atestfet` (~0.000000000000001 FET)
- **Designated Network:** `dorado-1` (Fetch.ai testnet)

---

## 5. Environment Configuration

To configure payment testing safely, add the following to `.env`:

```bash
# -------------------------------------------------------------
# Fetch.ai Agent Payment Protocol (Phase 9B)
# -------------------------------------------------------------
ENABLE_PAYMENT_PROTOCOL=false
PAYMENT_ENVIRONMENT=disabled
PAYMENT_NETWORK=testnet
PAYMENT_TESTNET_CHAIN_ID=dorado-1
PAYMENT_TESTNET_RPC_URL=grpc+https://grpc-dorado.fetch.ai
PAYMENT_TESTNET_DENOM=atestfet
PAYMENT_ADVISORY_FEE_BASE_UNITS=1000

# Verified Recipient Whitelist (Comma-separated fetch1... addresses)
# Example: PAYMENT_RECIPIENT_WHITELIST=fetch1ej0edgh03qnxqnyr60877kzukzl0lehv5yjgz7
PAYMENT_RECIPIENT_WHITELIST=

# Testnet Payer Wallet Seed (Optional - kept local only, never committed!)
PAYMENT_PAYER_SEED=
```

---

## 6. How to Run Protocol Verification Tests

### Run the Isolated Safety Suite:
```bash
.venv313\Scripts\python.exe -m pytest tests/test_payment_protocol_safety.py -v
```
*(21 passed)*

### Run the Protocol Integration Suite:
```bash
.venv313\Scripts\python.exe -m pytest tests/test_payment_protocol_integration.py -v
```
*(27 passed)*

### Run the Complete KrishiBazaar Suite:
```bash
.venv313\Scripts\python.exe -m pytest -v
```
*(220 passed)*

---

## 7. Blockers & Prerequisites for a Genuine On-Chain Testnet Transfer

To transition from mock/sandbox execution to an actual on-chain testnet transfer on `dorado-1`:

1. **Funded Testnet Payer Wallet:** A dedicated testnet account with Dorado testnet FET from the official faucet (`https://faucet-dorado.fetch.ai/api/v3/claims`).
2. **Verified Designated Merchant Address:** A designated payee `fetch1...` address added to `PAYMENT_RECIPIENT_WHITELIST`.
3. **Explicit Human Confirmation:** Live user prompt displaying the transaction details before submitting the transaction to the ledger.
4. **Active Toggle:** `ENABLE_PAYMENT_PROTOCOL=true` and `PAYMENT_ENVIRONMENT=testnet`.
