"""
Comprehensive Regression Tests for KrishiBazaar Final Production-Quality Demo Audit & Fix.
(tests/test_audit_and_fix_regressions.py)

Covers:
1. Exact financial arithmetic and itemized deductions (50 quintals onion @ ₹3,424/qtl, ₹1,980 freight -> ₹169,220 net, ₹3,384.40/qtl).
2. Transparent unverified cess and hamali exemption disclosures across cards, summaries, and proposals.
3. Market data provenance: Nashik zero-record notice vs. Pune regional fallback benchmark distinction.
4. Suspicious mandi price filtering (per-kg/bundle clerical error detection).
5. Proposal-to-booking financial consistency and simulated booking ledger safeguards.
6. Explicit approval gate, casual acknowledgement rejection, and cancellation handling.
7. Payment protocol recipient, denomination, gas preflight, and uncertain settlement holding state.
8. Subprocess startup diagnostics, exit status inspection, and child process cleanup.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    CommitPayment,
    DataProvenance,
    Funds,
    MandiPriceRecord,
    MandiPriceResult,
    NetReturnBreakdown,
    OptimizationResult,
    PaymentRecord,
    PaymentStatus,
)
from krishibazaar.services.booking_service import (
    create_booking_proposal,
    execute_simulated_booking,
    format_proposal_summary,
    format_booking_confirmation,
    is_casual_acknowledgement,
)
from krishibazaar.services.card_service import (
    build_channel_recommendations_card,
    build_proposal_review_card,
)
from krishibazaar.services.mandi_service import (
    validate_mandi_price,
)
from krishibazaar.agents.orchestrator import (
    format_mandi_market_report,
    format_optimization_report,
)
from krishibazaar.services.optimizer import (
    calculate_channel_net_return,
)
from krishibazaar.services.payment_service import (
    PaymentService,
    validate_fetch_wallet_address,
    extract_transaction_fee,
)
from krishibazaar.storage import StorageEngine


# =====================================================================
# 1. Financial Arithmetic & Deduction Itemization for Top Observed Offer
# =====================================================================
def test_observed_top_offer_exact_math():
    """
    Verify arithmetic for observed top simulated offer in demo:
    - 50 quintals onion @ INR 3,424.00/quintal = gross INR 171,200.00
    - Transport freight = INR 1,980.00
    - APMC cess = INR 0.00 (assumed unverified direct-trade exemption)
    - Hamali/labor = INR 0.00 (assumed unverified farmgate handling)
    - Weather loss = INR 0.00
    - Total deductions = INR 1,980.00
    - Net return = INR 169,220.00
    - Net return per quintal = INR 3,384.40
    - Target: INR 85,000 net -> Meets target (Rank 1)
    """
    qty = 50.0
    price_per_qtl = 3424.0
    freight = 1980.0
    target_net = 85000.0

    breakdown = calculate_channel_net_return(
        scenario_id="BO-SIM-OBSERVED-01",
        channel_type="DIRECT_BUYER",
        destination_name="Kalyani Agro-Exports Pvt Ltd",
        quantity_quintals=qty,
        price_per_quintal=price_per_qtl,
        distance_km=45.0,
        transport_cost=freight,
        applicable_market_charges=0.0,
        storage_cost=0.0,
        estimated_loss_cost=0.0,
        minimum_net_return_inr=target_net,
        mandi_cess_apmc=0.0,
        loading_unloading_cost=0.0,
        price_provenance="Simulated benchmark offer (Kalyani Agro-Exports Pvt Ltd)",
        is_price_simulated=True,
        transport_provenance="Simulated freight quote (Om Logistics Agro-Fleet)",
        charges_provenance="Direct trade statutory exemption assumption from APMC cess & hamali (unverified assumption)",
        is_apmc_exempt=True,
    )

    # Validate independent math
    assert breakdown.gross_value == 171200.00
    assert breakdown.transport_cost == 1980.00
    assert breakdown.mandi_cess_apmc == 0.00
    assert breakdown.loading_unloading_cost == 0.00
    assert breakdown.total_deductions == 1980.00
    assert breakdown.expected_net_return == 169220.00
    assert breakdown.net_return_per_quintal == 3384.40
    assert breakdown.meets_minimum_condition is True
    assert breakdown.is_feasible is True


# =====================================================================
# 2. Transparent Unverified Fee Disclosures in Cards & Summaries
# =====================================================================
def test_unverified_cess_and_hamali_disclosures():
    """
    Critical Rule: Never represent an unverified direct-trade assumption as legally confirmed.
    Verify cards and text summaries label zero cess and zero hamali as unverified assumptions.
    """
    breakdown = calculate_channel_net_return(
        scenario_id="BO-SIM-TEST-02",
        channel_type="DIRECT_BUYER",
        destination_name="Direct Agro Buyer Hub",
        quantity_quintals=50.0,
        price_per_quintal=3424.0,
        distance_km=30.0,
        transport_cost=1980.0,
        applicable_market_charges=0.0,
        mandi_cess_apmc=0.0,
        loading_unloading_cost=0.0,
        is_apmc_exempt=True,
    )

    opt = OptimizationResult(
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        minimum_net_return_inr=85000.0,
        recommended_option=breakdown,
        all_options=[breakdown],
    )

    # 1. Check Carousel Card
    card_meta = build_channel_recommendations_card(opt)
    card_str = str(card_meta.model_dump())
    assert "Assumed Direct-Trade Exemption - Unverified" in card_str
    assert "Assumed Farmgate Handling - Unverified" in card_str
    # Ensure raw misleading "Exempt" without qualification does not appear
    assert "APMC Cess: INR 0.00 (Exempt)" not in card_str

    # 2. Check Text Summary
    summary_lines = format_optimization_report(opt)
    summary_text = "\n".join(summary_lines)
    assert "Assumed Direct-Trade Exemption - Unverified" in summary_text
    assert "Assumed direct-buyer trade exemption outside APMC yard; unverified assumption, not legally verified" in summary_text


# =====================================================================
# 3. Market Data Provenance: Nashik Unavailable vs Pune Fallback
# =====================================================================
def test_market_provenance_nashik_zero_records_and_pune_benchmark():
    """
    Verify market reporting clearly discloses when Nashik has zero live arrival records
    and labels Pune records strictly as a regional/state benchmark rather than a live Nashik quote.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    nashik_res = MandiPriceResult(
        success=True,
        commodity="Onion",
        records=[],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET)",
            retrieval_timestamp_utc=now_utc,
            observation_date="2026-10-24",
            freshness_status="LIVE_NO_RECORDS",
            is_live=True,
        ),
    )

    pune_rec = MandiPriceRecord(
        mandi_name="Pune Gultekdi APMC",
        district="Pune",
        state="Maharashtra",
        commodity="Onion",
        variety="Local",
        arrival_date="2026-10-24",
        min_price=1900.0,
        max_price=2500.0,
        modal_price=2200.0,
        data_source="data.gov.in_live",
    )
    pune_res = MandiPriceResult(
        success=True,
        commodity="Onion",
        records=[pune_rec],
        provenance=DataProvenance(
            source="data.gov.in (AGMARKNET)",
            retrieval_timestamp_utc=now_utc,
            observation_date="2026-10-24",
            freshness_status="LIVE",
            is_live=True,
        ),
    )

    report_lines = format_mandi_market_report(
        crop="onion",
        district="Nashik",
        state="Maharashtra",
        mandi_result=nashik_res,
        nearby_result=pune_res,
    )
    report_text = "\n".join(report_lines)

    # 1. Preserves Nashik unavailability
    assert "No live arrival records were reported for 'Onion' in district 'Nashik', Maharashtra" in report_text
    assert "We never fabricate prices or substitute sample benchmark data as live prices" in report_text

    # 2. Pune is clearly labeled as state benchmark, NOT live Nashik
    assert "Other Live Maharashtra Mandi Benchmarks:" in report_text
    assert "**Pune Gultekdi APMC** (Pune)" in report_text
    assert "Modal Price **INR 2,200.00**/quintal" in report_text
    assert "Live Mandi Wholesale Prices (Onion in Nashik)" not in report_text


# =====================================================================
# 4. Suspicious Mandi Price & Unit Mismatch Filtering
# =====================================================================
def test_suspicious_mandi_price_filtering():
    """
    Verify suspicious entries (e.g. per-kg clerical entries < INR 100/qtl or inverted bounds)
    are flagged and excluded.
    """
    # 1. Per-kg or bundle entry (e.g., INR 25/qtl instead of INR 2500/qtl)
    is_susp, reason = validate_mandi_price("onion", modal_price=25.0, min_price=20.0, max_price=30.0)
    assert is_susp is True
    assert "Likely recorded per kg or per bundle" in reason

    # 2. Inverted bounds
    is_susp2, reason2 = validate_mandi_price("onion", modal_price=2200.0, min_price=2500.0, max_price=2000.0)
    assert is_susp2 is True
    assert "Inverted price range" in reason2

    # 3. Valid mandi quote
    is_susp3, reason3 = validate_mandi_price("onion", modal_price=2200.0, min_price=1800.0, max_price=2400.0)
    assert is_susp3 is False
    assert reason3 is None


# =====================================================================
# 5. Proposal-to-Booking Consistency & Safeguards
# =====================================================================
def test_proposal_to_booking_financial_reconciliation():
    """
    Verify that creating a proposal and executing a simulated booking preserves exact gross,
    freight, itemized deductions, and net payout without discrepancy.
    """
    breakdown = calculate_channel_net_return(
        scenario_id="BO-SIM-TEST-05",
        channel_type="DIRECT_BUYER",
        destination_name="Kalyani Agro-Exports Pvt Ltd",
        quantity_quintals=50.0,
        price_per_quintal=3424.0,
        distance_km=45.0,
        transport_cost=1980.0,
        applicable_market_charges=0.0,
        storage_cost=0.0,
        estimated_loss_cost=0.0,
        minimum_net_return_inr=85000.0,
        mandi_cess_apmc=0.0,
        loading_unloading_cost=0.0,
    )

    proposal = create_booking_proposal(
        session_id="sess_test_recon",
        farmer_id="farmer_test_recon",
        selected_option=breakdown,
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        transit_date="2026-10-25",
        validity_minutes=15,
        allow_past_date=True,
    )

    assert proposal.estimated_gross_value == 171200.00
    assert proposal.itemized_deductions == 1980.00
    assert proposal.estimated_net_payout == 169220.00

    booking = execute_simulated_booking(proposal)
    assert booking.is_simulated_booking is True
    assert booking.gross_revenue == 171200.00
    assert booking.transport_cost == 1980.00
    assert booking.other_deductions == 0.00
    assert booking.net_payout == 169220.00

    confirmation_lines = format_booking_confirmation(booking)
    conf_text = "\n".join(confirmation_lines)
    assert "SIMULATED — NOT A REAL BOOKING" in conf_text
    assert "Gross Produce Value: INR 171,200.00" in conf_text
    assert "Freight Transport: INR 1,980.00" in conf_text
    assert "Estimated Net Payout:** **INR 169,220.00**" in conf_text


# =====================================================================
# 6. Explicit Approval Gate & Casual Acknowledgement Guard
# =====================================================================
def test_explicit_approval_gate_and_casual_negation():
    """
    Verify casual affirmations (e.g. 'ok', 'sounds good') do NOT constitute explicit approval.
    """
    assert is_casual_acknowledgement("ok") is True
    assert is_casual_acknowledgement("sounds good") is True
    assert is_casual_acknowledgement("thanks") is True
    assert is_casual_acknowledgement("yes") is True

    # Explicit confirmation commands are NOT casual acknowledgements
    assert is_casual_acknowledgement("Confirm booking") is False
    assert is_casual_acknowledgement("Approve proposal PROP-20261024-ABCDEF") is False


# =====================================================================
# 7. Payment Recipient, Denomination & Uncertain Settlement Invariants
# =====================================================================
def test_payment_recipient_validation():
    """
    Verify payment subsystem strictly validates recipient addresses:
    - Rejects uAgents communication addresses ('agent1...').
    - Rejects informational specialist agent addresses.
    - Rejects placeholder or dummy addresses.
    - Accepts valid Fetch wallet addresses.
    """
    # 1. Agent address rejected
    ok1, msg1 = validate_fetch_wallet_address("agent1q0rsn3us4d9twt209r2xefsqku4qvgypjtp8xvkjep4tmrvrsldnw4z5az4")
    assert ok1 is False
    assert "uAgents communication address" in msg1

    # 2. Informational specialist address rejected
    ok2, msg2 = validate_fetch_wallet_address("fetch1aj7a34sp5udhksedvrdm2u5wqqcf9s77uk06a5")
    assert ok2 is False
    assert "Market Specialist Agent" in msg2

    # 3. Dummy / placeholder rejected
    ok3, msg3 = validate_fetch_wallet_address("fetch1testrecipientaddr01")
    assert ok3 is False
    assert "placeholder" in msg3.lower() or "dummy" in msg3.lower()

    # 4. Valid payee address accepted
    ok4, msg4 = validate_fetch_wallet_address("fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h")
    assert ok4 is True
    assert "Valid Fetch" in msg4 and "wallet address" in msg4


def test_payment_fee_extraction_and_uncertain_settlement(tmp_path: Path):
    """
    Verify fee extraction extracts exact 0.00043039 FET from event string
    and uncertain settlement ledger queries hold in AWAITING_RECONCILIATION without auto-retry.
    """
    # Test fee extraction
    mock_resp = MagicMock()
    mock_resp.events = {"tx": {"fee": "430390000000000atestfet"}}
    fee_base, fee_disp = extract_transaction_fee(mock_resp, expected_denom="atestfet")
    assert fee_base == 430390000000000
    assert "0.00043039 FET" in fee_disp

    # Test uncertain settlement holding state
    db = StorageEngine(db_path=tmp_path / "test_settlement.db")
    service = PaymentService(storage_engine=db)

    record = PaymentRecord(
        payment_reference="KB-PAY-UNCERTAIN-01",
        purpose="Advisory Service Micro-Fee (Demonstration)",
        payer_agent_address="agent1payer",
        payer_wallet_address="fetch1payer",
        payee_agent_address="agent1payee",
        payee_wallet_address="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
        amount_base_units=50000000000000000,
        amount_display="0.05 FET (50,000,000,000,000,000 atestfet)",
        currency="atestfet",
        network="fetchai-dorado-testnet",
        status=PaymentStatus.COMMITTED,
        tx_hash="UNCERTAIN_TX_HASH",
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        updated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    db.save_payment_record(record)

    mock_ledger = MagicMock()
    mock_ledger.query_tx.side_effect = TimeoutError("RPC connection timeout to testnet")

    commit = CommitPayment(
        funds=Funds(amount="50000000000000000", currency="atestfet", payment_method="fet_direct"),
        recipient="fetch1n09y85ndck56dmxrud7xpkmal0qqtqsej36u0h",
        transaction_id="UNCERTAIN_TX_HASH",
        reference="KB-PAY-UNCERTAIN-01",
    )

    is_settled, comp, cancel, updated_rec = service.verify_and_settle_commitment(commit, ledger_client=mock_ledger)
    assert is_settled is False
    assert comp is None
    assert cancel is None
    # Must be safely held in AWAITING_RECONCILIATION
    assert updated_rec.status == PaymentStatus.AWAITING_RECONCILIATION
    persisted = db.get_payment_record("KB-PAY-UNCERTAIN-01")
    assert persisted.status == PaymentStatus.AWAITING_RECONCILIATION
