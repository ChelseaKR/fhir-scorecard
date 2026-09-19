"""Tests for the /bundle/, /bundle/setup/, and /bundle/trust/ page generators in site.py, and
the noindex mechanism they introduced (Page.noindex, sitemap() and _shell()'s use of it).

The central property is that the purchase page fails closed: a price, a Buy link, an Offer, and
the conversion script appear only when *every* piece a purchase needs is configured, and each
missing piece on its own is enough to take them all away. Each of those negative controls starts
from the one plan that does sell (``_LIVE_PLAN``, asserted to sell first) and removes exactly one
thing, so a control that silently changed nothing would fail its own precondition.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from fhir_scorecard import analytics, site

ORIGIN = "https://fhir.chelseakr.com"
API = "https://abc123.execute-api.us-west-2.amazonaws.com"
REPO = Path(__file__).resolve().parents[1]

_LIVE_PLAN: dict[str, Any] = {
    "paymentsAvailable": True,
    "setup_api_base": API,
    "currency": "USD",
    "max_endpoints": 70,
    "products": {
        "bundle_15": {
            "label": "One archive, up to 15 endpoints",
            "price": 249,
            "interval": None,
            "checkout_url": "https://buy.stripe.com/test_bundle15",
        },
        "bundle_70": {
            "label": "One archive, up to 70 endpoints",
            "price": 499,
            "interval": None,
            "checkout_url": "https://buy.stripe.com/test_bundle70",
        },
    },
}


def _live() -> dict[str, Any]:
    return copy.deepcopy(_LIVE_PLAN)


def _sells(plan: dict[str, Any]) -> bool:
    """Every visible sign of a sale on the rendered page, in one place."""
    body = site.bundle_page(ORIGIN, plan).body
    return any(
        marker in body
        for marker in ("buy.stripe.com", "AggregateOffer", "/assets/bundle.js", "$249", "$499")
    )


def test_the_live_plan_sells_both_one_time_plans() -> None:
    """The positive control every negative control below starts from."""
    page = site.bundle_page(ORIGIN, _live())
    assert "Not yet available" not in page.body
    assert "This tier is not open yet" not in page.body
    # Once on each card, and once again per Offer in the JSON-LD block.
    assert page.body.count("buy.stripe.com") == 4
    assert "$249, paid once" in page.body and "$499, paid once" in page.body
    assert '"lowPrice":"249"' in page.body.replace(" ", "")
    assert '"highPrice":"499"' in page.body.replace(" ", "")
    assert '<script src="/assets/bundle.js" defer></script>' in page.body
    assert 'data-bundle-plan="bundle_15" data-bundle-price="249"' in page.body


# --- fails closed: remove one thing, and nothing is on sale ---


def _without(path: str) -> dict[str, Any]:
    plan = _live()
    *parents, leaf = path.split(".")
    node = plan
    for part in parents:
        node = node[part]
    assert leaf in node, f"{path} is not in the live plan to remove"
    del node[leaf]
    return plan


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda p: p.update(paymentsAvailable=False), id="payments-off"),
        pytest.param(lambda p: p.update(paymentsAvailable="true"), id="payments-a-string"),
        pytest.param(lambda p: p.update(setup_api_base=None), id="no-setup-api"),
        pytest.param(
            lambda p: p.update(setup_api_base="http://abc.example"), id="setup-api-not-https"
        ),
        pytest.param(
            lambda p: p.update(setup_api_base='https://x.example/"><script>'),
            id="setup-api-injection",
        ),
        pytest.param(lambda p: p.update(currency="usd"), id="bad-currency"),
        pytest.param(lambda p: p.update(products=[]), id="products-not-a-mapping"),
    ],
)
def test_a_missing_or_malformed_switch_closes_every_plan(mutate: Any) -> None:
    assert _sells(_live())
    plan = _live()
    mutate(plan)
    assert plan != _LIVE_PLAN  # the sabotage landed
    assert site.bundle_offers(plan) == ()
    assert not _sells(plan)
    body = site.bundle_page(ORIGIN, plan).body
    assert "This tier is not open yet" in body
    cards = 2 if isinstance(plan.get("products"), dict) else 0
    assert body.count("Not yet available") == cards


@pytest.mark.parametrize("path", ["paymentsAvailable", "setup_api_base"])
def test_an_absent_switch_closes_every_plan(path: str) -> None:
    assert _sells(_live())
    assert not _sells(_without(path))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("checkout_url", None),
        ("checkout_url", "https://example.com/pay"),
        ("checkout_url", "http://buy.stripe.com/test_bundle15"),
        ("checkout_url", 'https://buy.stripe.com/x"onclick="alert(1)'),
        ("price", None),
        ("price", 0),
        ("price", -5),
        ("price", True),
        ("price", "249"),
    ],
)
def test_a_plan_without_a_payment_link_or_a_price_is_not_sold(field: str, value: object) -> None:
    plan = _live()
    plan["products"]["bundle_15"][field] = value
    offers = site.bundle_offers(plan)
    assert [o.key for o in offers] == ["bundle_70"]
    body = site.bundle_page(ORIGIN, plan).body
    assert "$249" not in body
    assert "test_bundle15" not in body
    assert body.count("Not yet available") == 1


def test_a_refresh_plan_in_plan_json_is_never_sold() -> None:
    """The quarterly refresh is not built, so a plan.json entry for one is ignored: no card, no
    price, no link, no Offer, whatever it says."""
    plan = _live()
    plan["products"]["refresh_qtr"] = {
        "label": "Quarterly refresh",
        "price": 99,
        "interval": "quarter",
        "checkout_url": "https://buy.stripe.com/test_refreshqtr",
    }
    assert [o.key for o in site.bundle_offers(plan)] == ["bundle_15", "bundle_70"]
    body = site.bundle_page(ORIGIN, plan).body
    assert "refresh" not in body.lower()
    assert "$99" not in body


def test_a_closed_tier_publishes_none_of_the_proposed_prices() -> None:
    """Prices are proposed in plan.json before the owner opens the tier, and the page says none
    of them until she does. Checked against the committed file's prices with the tier closed,
    whatever state the file is in."""
    plan = json.loads((REPO / "data" / "bundle" / "plan.json").read_text(encoding="utf-8"))
    plan["paymentsAvailable"] = False
    assert site.bundle_offers(plan) == ()
    body = site.bundle_page(ORIGIN, plan).body
    for product in plan["products"].values():
        assert f"${product['price']}" not in body
    assert "AggregateOffer" not in body
    assert "/assets/bundle.js" not in body


# --- the rest of the page ---


def test_bundle_page_is_indexable() -> None:
    page = site.bundle_page(ORIGIN, _live())
    assert page.noindex is False
    assert page.path == "bundle"


def test_bundle_page_prices_come_from_the_plan_not_hardcoded() -> None:
    """Negative control: change a price in the plan dict and the page must say the new number,
    not a value baked into site.py."""
    changed = _live()
    changed["products"]["bundle_15"]["price"] = 12345
    page = site.bundle_page(ORIGIN, changed)
    assert "$12,345" in page.body
    assert "$249" not in page.body


def test_a_fractional_price_is_shown_to_the_cent_and_a_whole_one_never_as_dot_zero() -> None:
    plan = _live()
    plan["products"]["bundle_15"]["price"] = 249.5
    body = site.bundle_page(ORIGIN, plan).body
    assert "$249.50" in body
    assert "$499.0" not in body and "$499, paid once" in body


def test_bundle_setup_page_is_noindex() -> None:
    page = site.bundle_setup_page(ORIGIN)
    assert page.noindex is True
    assert page.path == "bundle/setup"


def test_bundle_setup_page_has_the_form_fields_the_client_script_reads() -> None:
    page = site.bundle_setup_page(ORIGIN, _live())
    for field in ("program_name", "accent", "logo", "endpoint_ids", "deliver_to"):
        assert f'name="{field}"' in page.body
    assert "bundle-setup.js" in page.body
    assert 'pattern="#[0-9A-Fa-f]{6}"' in page.body


def test_the_setup_form_carries_the_api_base_only_when_one_is_configured() -> None:
    assert f'data-api="{API}"' in site.bundle_setup_page(ORIGIN, _live()).body
    # Still carried while the tier is closed to the public: the owner's test purchase goes
    # straight to a test Payment Link and has to be able to send this form.
    closed = _live()
    closed["paymentsAvailable"] = False
    assert f'data-api="{API}"' in site.bundle_setup_page(ORIGIN, closed).body
    for plan in (None, {}, _without("setup_api_base"), {"setup_api_base": "http://x.example"}):
        assert "data-api" not in site.bundle_setup_page(ORIGIN, plan).body


def test_the_setup_form_never_submits_its_fields_into_the_address() -> None:
    """Without scripting a form with no method submits by GET, which would put the buyer's
    organization, email, and endpoint list in the URL."""
    form = re.search(r"<form[^>]*>", site.bundle_setup_page(ORIGIN, _live()).body)
    assert form is not None
    assert 'method="post"' in form.group(0)
    assert 'action="/bundle/setup/"' in form.group(0)


def test_bundle_trust_page_states_the_independence_and_refund_terms() -> None:
    page = site.bundle_trust_page(ORIGIN)
    assert "never for sale" in page.body
    assert "refunded in full" in page.body
    assert "patient data is ever requested" in page.body.lower()
    assert "Reply to the receipt Stripe emailed you" in page.body
    assert "subscription" not in page.body.lower()


def test_the_trust_page_describes_measurement_only_when_the_build_measures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "What the pages measure" in site.bundle_trust_page(ORIGIN).body
    monkeypatch.setattr(analytics, "GA4_MEASUREMENT_ID", "")
    assert "What the pages measure" not in site.bundle_trust_page(ORIGIN).body


def test_no_bundle_page_sends_a_buyer_to_the_public_issue_tracker() -> None:
    """A paying buyer's question goes to the Stripe receipt, which reaches the owner privately,
    not to a public issue where it would name the buyer."""
    bodies = [
        site.bundle_page(ORIGIN, _live()).body,
        site.bundle_setup_page(ORIGIN, _live()).body,
        site.bundle_trust_page(ORIGIN).body,
        (REPO / "src" / "fhir_scorecard" / "assets" / "bundle-setup.js").read_text("utf-8"),
    ]
    for body in bodies:
        assert "fhir-scorecard/issues" not in body


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
