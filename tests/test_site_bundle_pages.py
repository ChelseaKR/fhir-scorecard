"""Tests for the /bundle/, /bundle/setup/, and /bundle/trust/ page generators in site.py, and
the noindex mechanism they introduced (Page.noindex, sitemap() and _shell()'s use of it)."""

from __future__ import annotations

from fhir_scorecard import site

ORIGIN = "https://fhir.chelseakr.com"

_LIVE_PLAN = {
    "paymentsAvailable": True,
    "currency": "USD",
    "max_endpoints": 70,
    "products": {
        "bundle_15": {
            "label": "One archive, up to 15 endpoints",
            "price": 299,
            "interval": None,
            "checkout_url": "https://buy.stripe.com/test_bundle15",
        },
        "bundle_70": {
            "label": "One archive, up to 70 endpoints",
            "price": 699,
            "interval": None,
            "checkout_url": "https://buy.stripe.com/test_bundle70",
        },
        "refresh_qtr": {
            "label": "Quarterly refresh",
            "price": 99,
            "interval": "quarter",
            "checkout_url": "https://buy.stripe.com/test_refreshqtr",
        },
        "refresh_yr": {
            "label": "Quarterly refresh, billed yearly",
            "price": 349,
            "interval": "year",
            "checkout_url": "https://buy.stripe.com/test_refreshyr",
        },
    },
}

_OFF_PLAN = {
    "paymentsAvailable": False,
    "currency": "USD",
    "max_endpoints": 70,
    "products": {
        "bundle_15": {
            "label": "One archive, up to 15 endpoints",
            "price": 299,
            "interval": None,
            "checkout_url": None,
        },
    },
}


def test_bundle_page_renders_not_yet_available_while_payments_are_off() -> None:
    page = site.bundle_page(ORIGIN, _OFF_PLAN)
    assert "Not yet available" in page.body
    assert "This tier is not open yet" in page.body
    assert "buy.stripe.com" not in page.body


def test_bundle_page_emits_no_offer_jsonld_while_payments_are_off() -> None:
    page = site.bundle_page(ORIGIN, _OFF_PLAN)
    assert "AggregateOffer" not in page.body


def test_bundle_page_renders_buy_buttons_and_offers_when_live() -> None:
    page = site.bundle_page(ORIGIN, _LIVE_PLAN)
    assert "Not yet available" not in page.body
    # Once in each of the four pricing cards, and once again per Offer in the JSON-LD block.
    assert page.body.count("buy.stripe.com") == 8
    assert "AggregateOffer" in page.body
    assert '"lowPrice":"99"' in page.body.replace(" ", "")
    assert '"highPrice":"699"' in page.body.replace(" ", "")


def test_bundle_page_is_indexable() -> None:
    page = site.bundle_page(ORIGIN, _OFF_PLAN)
    assert page.noindex is False
    assert page.path == "bundle"


def test_bundle_page_prices_come_from_the_plan_not_hardcoded() -> None:
    """Negative control: change a price in the plan dict and the page must say the new number,
    not a value baked into site.py."""
    changed = {**_LIVE_PLAN, "products": {**_LIVE_PLAN["products"]}}
    changed["products"]["bundle_15"] = {**changed["products"]["bundle_15"], "price": 12345}
    page = site.bundle_page(ORIGIN, changed)
    assert "$12,345" in page.body
    assert "$299" not in page.body


def test_bundle_setup_page_is_noindex() -> None:
    page = site.bundle_setup_page(ORIGIN)
    assert page.noindex is True
    assert page.path == "bundle/setup"


def test_bundle_setup_page_has_the_form_fields_the_client_script_reads() -> None:
    page = site.bundle_setup_page(ORIGIN)
    for field in ("program_name", "accent", "logo", "endpoint_ids", "deliver_to"):
        assert f'name="{field}"' in page.body
    assert "bundle-setup.js" in page.body


def test_bundle_trust_page_states_the_independence_and_refund_terms() -> None:
    page = site.bundle_trust_page(ORIGIN)
    assert "never for sale" in page.body
    assert "refunded" in page.body
    assert "patient data is ever requested" in page.body.lower()


# ---------------------------------------------------------------------------
# noindex propagation: sitemap() and _shell()
# ---------------------------------------------------------------------------


def test_sitemap_excludes_a_noindex_page() -> None:
    pages = [
        site.Page(path="a", title="A", description="", body=""),
        site.Page(path="b", title="B", description="", body="", noindex=True),
    ]
    xml = site.sitemap(pages, ORIGIN)
    assert f"{ORIGIN}/a/" in xml
    assert f"{ORIGIN}/b/" not in xml


def test_shell_emits_robots_noindex_meta_only_for_noindex_pages() -> None:
    indexable = site.Page(path="a", title="A", description="d", body="body")
    hidden = site.Page(path="b", title="B", description="d", body="body", noindex=True)
    rendered_indexable = site._shell(
        indexable, canonical=f"{ORIGIN}/a/", origin=ORIGIN, generated_at="now"
    )
    rendered_hidden = site._shell(
        hidden, canonical=f"{ORIGIN}/b/", origin=ORIGIN, generated_at="now"
    )
    assert 'name="robots"' not in rendered_indexable
    assert 'name="robots" content="noindex,follow"' in rendered_hidden
