"""
Unit tests for KrishiBazaar Buyer Specialist Service (tests/test_buyer_service.py).
Verifies:
- All buyer offers are strictly flagged with is_simulated=True
- Disclaimers explicitly declare offers as simulated benchmarks
- Farmgate collection offers have farmgate_pickup=True
- Validation on zero/negative quantities returns typed failure
- Synchronous and asynchronous functions execute correctly
"""

import pytest
from krishibazaar.services.buyer_service import (
    generate_simulated_buyer_offers,
    get_buyer_offers,
    get_buyer_offers_async,
)


def test_generate_simulated_buyer_offers_success():
    """Verify buyer offers contain required simulated flags, disclaimers, and diversity."""
    result = generate_simulated_buyer_offers(
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        price_hint=3400.0,
    )

    assert result.success is True
    assert result.crop == "Onion"
    assert result.quantity_quintals == 50.0
    assert len(result.offers) == 3

    # Strict compliance: All offers MUST be labeled as simulated
    for offer in result.offers:
        assert offer.is_simulated is True
        assert "simulated" in offer.disclaimer.lower()
        assert offer.offered_price_per_quintal > 0
        assert offer.quantity_quintals == 50.0

    # Strict provenance compliance
    assert result.provenance.is_live is False
    assert result.provenance.freshness_status == "SIMULATED_SAMPLE"
    assert "simulated" in result.provenance.disclaimer.lower()

    # Verify at least one FPO farmgate collection offer exists
    fpo_offers = [o for o in result.offers if o.farmgate_pickup is True]
    assert len(fpo_offers) >= 1
    assert "fpo" in fpo_offers[0].buyer_type.lower()


def test_buyer_service_invalid_quantity():
    """Verify quantity <= 0 returns typed failure result."""
    result = generate_simulated_buyer_offers(
        crop="Tomato",
        quantity_quintals=0.0,
        origin_location="Pune",
    )
    assert result.success is False
    assert "greater than zero" in result.error_message


@pytest.mark.asyncio
async def test_async_and_sync_buyer_service_wrappers():
    """Verify async and sync wrappers return consistent results."""
    sync_res = get_buyer_offers(
        crop="Wheat",
        quantity_quintals=20.0,
        origin_location="Khanna, Punjab",
    )
    async_res = await get_buyer_offers_async(
        crop="Wheat",
        quantity_quintals=20.0,
        origin_location="Khanna, Punjab",
    )

    assert sync_res.success is True
    assert async_res.success is True
    assert len(sync_res.offers) == len(async_res.offers)
    assert sync_res.offers[0].crop == "Wheat"


def test_generate_buyer_b_distinct_simulated_offers():
    """Verify Buyer B generates distinct institutional and export simulated offers with explicit disclaimers."""
    result = generate_simulated_buyer_offers(
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        price_hint=3400.0,
        profile="buyer_b",
    )

    assert result.success is True
    assert result.crop == "Onion"
    assert result.quantity_quintals == 50.0
    assert len(result.offers) == 3

    # All offers MUST be labeled as simulated benchmarks
    for offer in result.offers:
        assert offer.is_simulated is True
        assert "simulated" in offer.disclaimer.lower()
        assert "buyer b" in offer.disclaimer.lower()
        assert offer.offered_price_per_quintal > 0
        assert offer.quantity_quintals == 50.0

    # Provenance must reflect Buyer B network
    assert result.provenance.is_live is False
    assert result.provenance.freshness_status == "SIMULATED_SAMPLE"
    assert "Buyer B" in result.provenance.source
    assert "simulated" in result.provenance.disclaimer.lower()

    # Buyer B must have institutional / export procurement buyers
    buyer_names = [o.buyer_name for o in result.offers]
    assert "Kalyani Agro-Exports Pvt Ltd" in buyer_names
    assert "ITC Choupal Saagar Rural Procurement" in buyer_names
    assert "Godavari Organic Agro-Processing Unit" in buyer_names

    # Check farmgate collection offer exists in Buyer B profile (ITC Choupal)
    itc_offers = [o for o in result.offers if "itc" in o.buyer_name.lower()]
    assert len(itc_offers) == 1
    assert itc_offers[0].farmgate_pickup is True


def test_buyer_a_and_buyer_b_offers_are_independent_and_distinct():
    """Verify Buyer A and Buyer B return distinct bids, distinct buyer IDs, and distinct profiles."""
    offers_a = generate_simulated_buyer_offers(
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        price_hint=3000.0,
        profile="buyer_a",
    )
    offers_b = generate_simulated_buyer_offers(
        crop="Onion",
        quantity_quintals=50.0,
        origin_location="Nashik",
        price_hint=3000.0,
        profile="buyer_b",
    )

    assert offers_a.success is True
    assert offers_b.success is True

    names_a = {o.buyer_name for o in offers_a.offers}
    names_b = {o.buyer_name for o in offers_b.offers}
    # No overlap in buyer names
    assert names_a.isdisjoint(names_b)

    ids_a = {o.buyer_id for o in offers_a.offers}
    ids_b = {o.buyer_id for o in offers_b.offers}
    # No overlap in buyer IDs
    assert ids_a.isdisjoint(ids_b)

    # Prices are independent
    prices_a = [o.offered_price_per_quintal for o in offers_a.offers]
    prices_b = [o.offered_price_per_quintal for o in offers_b.offers]
    assert prices_a != prices_b
