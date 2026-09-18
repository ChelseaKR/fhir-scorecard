"""The bundle pages' own scripts, run in Node: what they announce, and what they never put in an
announcement (ADR 0007).

``assets/bundle.js`` announces the plans on sale and a followed checkout link;
``assets/bundle-setup.js`` announces the purchase Stripe returned a buyer from. Neither calls
Google: the analytics loader forwards what they announce (``tests/test_analytics.py`` holds the
loader to its checks). These tests hold the scripts to theirs: an announcement names a plan id,
a price, a currency, or a hashed order number, and never Stripe's order reference or anything a
buyer typed.

Locally they skip when Node is missing; in CI (``CI`` set) a missing Node is a failure, so the
gate cannot pass by never running them.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ASSETS = Path(__file__).resolve().parents[1] / "src" / "fhir_scorecard" / "assets"
SESSION = "cs_test_a1B2c3D4e5F6g7H8i9J0"
API = "https://abc123.execute-api.us-west-2.amazonaws.com"

HARNESS = r"""
const fs = require("fs");
const sc = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const code = fs.readFileSync(process.argv[3], "utf8");
const announced = [];
const listeners = {};
const status = { textContent: "", className: "" };
const elements = [{ disabled: false }, { disabled: false }];
const form = sc.form ? {
  elements,
  getAttribute: (k) => (k === "data-api" ? sc.form.api || null : null),
  addEventListener: () => {},
} : null;
const links = (sc.links || []).map((l) => ({
  onclick: null,
  getAttribute: (k) => (k === "data-bundle-plan" ? l.plan : k === "data-bundle-price" ? l.price : null),
  addEventListener(t, f) { if (t === "click") this.onclick = f; },
}));
const box = sc.links ? {
  getAttribute: (k) => (k === "data-currency" ? sc.currency || null : null),
  querySelectorAll: () => links,
} : null;
class Evt { constructor(type, init) { this.type = type; this.detail = init && init.detail; } }
const document = {
  getElementById: (id) => (id === "bundle-setup-form" ? form : id === "bundle-setup-status" ? status
    : id === "bundle-offers" ? box : null),
  dispatchEvent: (e) => { announced.push({ type: e.type, detail: e.detail }); return true; },
  addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
};
const window = { crypto: sc.noCrypto ? undefined : globalThis.crypto, TextEncoder };
const location = { search: sc.search || "" };
new Function("window", "document", "location", "CustomEvent", "URLSearchParams", "TextEncoder", code)(
  window, document, location, Evt, URLSearchParams, TextEncoder);
(sc.clicks || []).forEach((i) => links[i].onclick());
setTimeout(() => {
  process.stdout.write(JSON.stringify({ announced, status, disabled: elements.map((e) => e.disabled) }));
}, 50);
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node is required in CI to run the bundle scripts' behavior tests")
        pytest.skip("Node is not installed; the bundle scripts' behavior tests need it")
    return node


@pytest.fixture
def run(tmp_path: Path) -> Any:
    node = _node()
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    counter = iter(range(1000))

    def _run(script: str, **scenario: Any) -> dict[str, Any]:
        n = next(counter)
        spec = tmp_path / f"scenario-{n}.json"
        spec.write_text(json.dumps(scenario), encoding="utf-8")
        done = subprocess.run(  # noqa: S603 - a fixed local node binary and files this test wrote
            [node, str(harness), str(spec), str(ASSETS / script)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        result: dict[str, Any] = json.loads(done.stdout)
        return result

    return _run


def _order(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:32]


# --- bundle-setup.js ---


def test_the_setup_page_announces_the_purchase_by_its_hashed_order_only(run: Any) -> None:
    result = run("bundle-setup.js", form={"api": API}, search=f"?session_id={SESSION}")
    assert result["announced"] == [
        {
            "type": "fhir-scorecard:commerce",
            "detail": {"event": "purchase", "transaction_id": _order(SESSION)},
        }
    ]
    assert SESSION not in json.dumps(result["announced"])
    assert result["disabled"] == [False, False]


def test_negative_control_the_hash_really_depends_on_the_reference(run: Any) -> None:
    other = "cs_test_Z9y8X7w6V5u4T3s2R1q0"
    first = run("bundle-setup.js", form={"api": API}, search=f"?session_id={SESSION}")
    second = run("bundle-setup.js", form={"api": API}, search=f"?session_id={other}")
    assert first["announced"][0]["detail"]["transaction_id"] == _order(SESSION)
    assert second["announced"][0]["detail"]["transaction_id"] == _order(other)
    assert _order(SESSION) != _order(other)


@pytest.mark.parametrize(
    "scenario",
    [
        {"search": ""},
        {"search": "?session_id=not-a-stripe-reference"},
        {"search": f"?session_id={SESSION}", "noCrypto": True},
    ],
)
def test_no_reference_or_no_hashing_means_no_purchase_is_announced(
    run: Any, scenario: dict[str, Any]
) -> None:
    assert run("bundle-setup.js", form={"api": API}, **scenario)["announced"] == []


def test_without_an_api_base_the_form_is_disabled_and_says_so(run: Any) -> None:
    result = run("bundle-setup.js", form={}, search=f"?session_id={SESSION}")
    assert result["disabled"] == [True, True]
    assert "setup service is not deployed yet" in result["status"]["textContent"]
    assert "reply to the receipt Stripe emailed you" in result["status"]["textContent"]
    # The payment happened whatever state the setup service is in, so it is still counted.
    assert [a["detail"]["event"] for a in result["announced"]] == ["purchase"]


# --- bundle.js ---

LINKS = [{"plan": "bundle_15", "price": "249"}, {"plan": "bundle_70", "price": "499"}]


def test_the_purchase_page_announces_the_plans_shown_valued_at_the_first(run: Any) -> None:
    result = run("bundle.js", links=LINKS, currency="USD")
    assert result["announced"] == [
        {
            "type": "fhir-scorecard:commerce",
            "detail": {
                "event": "view_item",
                "currency": "USD",
                "value": 249,
                "items": [
                    {"item_id": "bundle_15", "price": 249},
                    {"item_id": "bundle_70", "price": 499},
                ],
            },
        }
    ]


def test_a_checkout_click_announces_that_plan_alone(run: Any) -> None:
    result = run("bundle.js", links=LINKS, currency="USD", clicks=[1])
    assert result["announced"][1] == {
        "type": "fhir-scorecard:commerce",
        "detail": {
            "event": "begin_checkout",
            "currency": "USD",
            "value": 499,
            "items": [{"item_id": "bundle_70", "price": 499}],
        },
    }


def test_a_page_with_nothing_on_sale_announces_nothing(run: Any) -> None:
    assert run("bundle.js", links=[], currency="USD")["announced"] == []
    assert run("bundle.js")["announced"] == []
