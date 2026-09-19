"""Google Analytics 4: absent with no ID, silent off the production host and under GPC, DNT or
the footer opt-out, and configured exactly as ADR 0006 says everywhere else.

Two kinds of test. The build tests read the site the documented offline command writes, with
and without a measurement ID, and check what the pages and the data files carry. The behavior
tests run the loader itself in Node against a stubbed ``window``, ``navigator``, ``document``
and ``localStorage``, because a string search over a script cannot show what the script does.
Locally they skip when Node is missing; in CI (``CI`` set) a missing Node is a failure, so the
gate cannot pass by never running them.

Every negative control asserts its sabotage landed - exactly one occurrence of the guard
replaced, and the script text changed - before it asserts the harness caught it. A sabotage
that silently matched nothing would otherwise read as a pass.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from fhir_scorecard import analytics
from fhir_scorecard.accessibility import audit_accessibility
from fhir_scorecard.audit import audit_site
from fhir_scorecard.cli import main
from fhir_scorecard.site import DEFAULT_ORIGIN
from fhir_scorecard.weight import audit_weight

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MID = analytics.GA4_MEASUREMENT_ID
GA_MARKERS = ("googletagmanager", "google-analytics", "gtag", "dataLayer", "data-analytics-choice")


def _build(out: Path) -> Path:
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(FIXTURES),
                "--registry",
                str(FIXTURES / "registry.json"),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    return out


@pytest.fixture
def site(tmp_path: Path) -> Path:
    return _build(tmp_path / "site")


@pytest.fixture
def site_without_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(analytics, "GA4_MEASUREMENT_ID", "")
    return _build(tmp_path / "site")


def _pages(root: Path) -> list[Path]:
    pages = sorted(root.rglob("*.html"))
    assert len(pages) > 20, "the fixture build wrote fewer pages than expected"
    return pages


def _head(text: str) -> str:
    return text.split("</head>", 1)[0]


# --- configuration ---


def test_the_committed_id_is_the_fhir_scorecard_web_stream() -> None:
    assert analytics.measurement_id(MID) == "G-5XE50LHZDJ"
    assert analytics.GA4_DATA_RETENTION == "14 months"


def test_the_production_host_is_the_host_of_the_default_origin() -> None:
    assert urlsplit(DEFAULT_ORIGIN).hostname == analytics.PRODUCTION_HOST


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_unset_id_means_no_ga(value: str | None) -> None:
    assert analytics.measurement_id(value) is None
    assert analytics.head_snippet(value or "") == ""


@pytest.mark.parametrize(
    "value", ["UA-12345-1", "G-", "g-5xe50lhzdj", 'G-ABC"};alert(1);//', "G-ABCD EFGH"]
)
def test_a_malformed_id_fails_the_build_instead_of_shipping(value: str) -> None:
    with pytest.raises(ValueError, match="not a GA4 measurement ID"):
        analytics.head_snippet(value)


def test_the_denied_regions_are_the_eea_the_uk_and_switzerland() -> None:
    regions = analytics.ANALYTICS_DENIED_REGIONS
    assert len(regions) == len(set(regions)) == 32
    assert {"DE", "FR", "IE", "IS", "LI", "NO", "GB", "CH"} <= set(regions)
    assert "US" not in regions


# --- the build ---


def test_a_build_with_no_id_carries_no_ga_on_any_page(site_without_id: Path) -> None:
    for page in _pages(site_without_id):
        text = page.read_text(encoding="utf-8")
        for marker in GA_MARKERS:
            assert marker not in text, f"{page.relative_to(site_without_id)} carries {marker!r}"
    privacy = (site_without_id / "privacy" / "index.html").read_text(encoding="utf-8")
    assert "This site runs no analytics" in privacy
    assert "Google Analytics" not in privacy
    home = (site_without_id / "index.html").read_text(encoding="utf-8")
    assert "This site runs no analytics and sets no cookies." in home


def test_every_page_carries_one_guarded_loader_in_head_and_one_footer_control(
    site: Path,
) -> None:
    snippet = analytics.head_snippet()
    for page in _pages(site):
        text = page.read_text(encoding="utf-8")
        where = page.relative_to(site)
        assert text.count(snippet) == 1, where
        assert snippet in _head(text), where
        assert text.count("googletagmanager.com/gtag/js") == 1, where
        assert text.count(analytics.FOOTER_CONTROL) == 1, where
        assert text.count(">Opt out of analytics</button>") == 1, where
        assert text.index(analytics.FOOTER_CONTROL) > text.index("<footer"), where
        assert 'href="/privacy/"' in text, where
        # Nothing names Google as a static subresource: gtag.js is only ever appended by the
        # guarded loader, never by a <script src> a browser would fetch unconditionally.
        assert not re.search(r"<script[^>]+src=\"https?://[^\"]*google", text), where


def test_the_guards_come_before_anything_is_created_or_requested() -> None:
    snippet = analytics.head_snippet()
    first_effect = min(snippet.index("w.dataLayer"), snippet.index("createElement"))
    for guard in (
        f"if (w.location.hostname !== {json.dumps(analytics.PRODUCTION_HOST)}) return;",
        "if (n.globalPrivacyControl === true) return;",
        'if (dnt === "1" || dnt === "yes") return;',
        "if (optedOut()) return;",
    ):
        assert snippet.count(guard) == 1, guard
        assert snippet.index(guard) < first_effect, guard


def test_the_stylesheet_lets_hidden_win_over_the_uswds_button_display() -> None:
    """USWDS gives every ``.usa-button`` ``display: inline-flex``, which beats the browser's own
    ``[hidden]`` rule. Measured in Chrome 153 on the built site: a hidden unstyled USWDS button
    computes to ``inline-flex`` without this rule and ``none`` with it. Without it, the button
    the loader hides under GPC or DNT would stay on screen."""
    css = (Path(analytics.__file__).parent / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".analytics-toggle[hidden] { display: none; }" in css
    assert 'class="usa-button usa-button--unstyled analytics-toggle"' in analytics.FOOTER_CONTROL


def test_the_data_files_feeds_and_badges_never_carry_ga(site: Path) -> None:
    others = [
        p
        for p in site.rglob("*")
        if p.is_file() and p.suffix in {".csv", ".json", ".xml", ".svg", ".txt"}
    ]
    assert any(p.suffix == ".csv" for p in others) and any(p.suffix == ".xml" for p in others)
    for path in others:
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in ("googletagmanager", "google-analytics", "gtag(", MID):
            assert marker not in text, f"{path.relative_to(site)} carries {marker!r}"


def test_the_privacy_page_is_built_listed_and_linked_and_describes_ga(site: Path) -> None:
    privacy = (site / "privacy" / "index.html").read_text(encoding="utf-8")
    for fact in (
        "Google Analytics 4",
        "Global Privacy Control",
        "Do Not Track",
        "Opt out of analytics",
        analytics.OPT_OUT_STORAGE_KEY,
        "_ga",
        "two years",
        "cookieless",
        "Switzerland",
        f"keeps it for {analytics.GA4_DATA_RETENTION}",
        "Google signals and ad personalization are off",
    ):
        assert fact in privacy, fact
    assert f"{DEFAULT_ORIGIN}/privacy/" in (site / "sitemap.xml").read_text(encoding="utf-8")


def test_both_builds_pass_the_site_contract_accessibility_and_weight(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert audit_weight(site) == []
    monkeypatch.setattr(analytics, "GA4_MEASUREMENT_ID", "")
    bare = _build(tmp_path / "bare")
    assert audit_site(bare, DEFAULT_ORIGIN) == []
    assert audit_accessibility(bare) == []


# --- the loader, run in Node ---

HARNESS = r"""
const fs = require("fs");
const sc = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const code = fs.readFileSync(process.argv[3], "utf8").replace(/^<script>/, "").replace(/<\/script>\s*$/, "");
const appended = [];
const listeners = {};
const data = Object.assign({}, sc.storage || {});
const blocked = () => { throw new Error("storage blocked"); };
const storage = sc.storageThrows
  ? { getItem: blocked, setItem: blocked, removeItem: blocked }
  : {
      getItem: (k) => (Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null),
      setItem: (k, v) => { data[k] = String(v); },
      removeItem: (k) => { delete data[k]; },
    };
const button = { hidden: true, textContent: "Opt out of analytics", onclick: null,
  addEventListener(t, f) { if (t === "click") this.onclick = f; } };
const status = { textContent: "" };
const box = { hidden: true, querySelector: (s) => (s === "button" ? button : status) };
const document = {
  head: { appendChild: (el) => appended.push(el) },
  createElement: (tag) => ({ tagName: tag, async: false, src: "" }),
  addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
  querySelector: (s) => (s === "[data-analytics-choice]" ? box : null),
  referrer: sc.referrer || "",
};
const navigator = Object.assign({}, sc.navigator || {});
const window = { location: Object.assign(
  { hostname: sc.hostname, origin: "https://" + sc.hostname, pathname: "/", search: "", hash: "" },
  sc.location || {}) };
const session = Object.assign({}, sc.session || {});
window.sessionStorage = {
  getItem: (k) => (Object.prototype.hasOwnProperty.call(session, k) ? session[k] : null),
  setItem: (k, v) => { session[k] = String(v); },
  removeItem: (k) => { delete session[k]; },
};
if (sc.windowDoNotTrack !== undefined) window.doNotTrack = sc.windowDoNotTrack;
Object.defineProperty(window, "localStorage", {
  get() { if (sc.storageGetterThrows) throw new Error("denied"); return storage; },
});
new Function("window", "navigator", "document", code)(window, navigator, document);
const snap = () => ({ hidden: button.hidden, text: button.textContent, status: status.textContent,
  boxHidden: box.hidden, stored: Object.assign({}, data), disabled: window[sc.gaDisable] === true });
const states = [];
if (sc.domReady) {
  (listeners.DOMContentLoaded || []).forEach((f) => f());
  states.push(snap());
  for (let i = 0; i < (sc.clicks || 0); i++) { button.onclick(); states.push(snap()); }
}
const mark = window.dataLayer ? window.dataLayer.length : 0;
(sc.commerce || []).forEach((detail) => {
  if (detail === "OPT_OUT") { data[sc.optOutKey] = "1"; return; }
  (listeners[sc.commerceEvent] || []).forEach((f) => f({ detail }));
});
const plain = (x) => (x instanceof Date ? "<date>" : x);
process.stdout.write(JSON.stringify({
  dataLayer: window.dataLayer ? window.dataLayer.map((a) => Array.from(a).map(plain)) : null,
  appended: appended.map((e) => ({ tag: e.tagName, async: e.async, src: e.src })),
  listeners: Object.keys(listeners),
  states,
  sent: window.dataLayer ? window.dataLayer.slice(mark).map((a) => Array.from(a).map(plain)) : [],
  session,
}));
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node is required in CI to run the GA4 loader's behavior tests")
        pytest.skip("Node is not installed; the loader's behavior tests need it")
    return node


@pytest.fixture
def run(tmp_path: Path) -> Iterator[Any]:
    node = _node()
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    counter = iter(range(1000))

    def _run(snippet: str | None = None, **scenario: Any) -> dict[str, Any]:
        n = next(counter)
        script = tmp_path / f"snippet-{n}.js"
        script.write_text(analytics.head_snippet() if snippet is None else snippet, "utf-8")
        scenario.setdefault("hostname", analytics.PRODUCTION_HOST)
        scenario.setdefault("gaDisable", f"ga-disable-{MID}")
        scenario.setdefault("commerceEvent", analytics.COMMERCE_EVENT)
        scenario.setdefault("optOutKey", analytics.OPT_OUT_STORAGE_KEY)
        spec = tmp_path / f"scenario-{n}.json"
        spec.write_text(json.dumps(scenario), encoding="utf-8")
        done = subprocess.run(  # noqa: S603 - a fixed local node binary and files this test wrote
            [node, str(harness), str(spec), str(script)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        result: dict[str, Any] = json.loads(done.stdout)
        return result

    yield _run


def _loaded(result: dict[str, Any]) -> bool:
    return result["dataLayer"] is not None or bool(result["appended"])


KEY = analytics.OPT_OUT_STORAGE_KEY
NOTHING_LOADS = {
    "another host": {"hostname": "chelseakr.github.io"},
    "localhost": {"hostname": "localhost"},
    "127.0.0.1": {"hostname": "127.0.0.1"},
    "GPC": {"navigator": {"globalPrivacyControl": True}},
    "navigator.doNotTrack": {"navigator": {"doNotTrack": "1"}},
    "window.doNotTrack": {"windowDoNotTrack": "1"},
    "navigator.msDoNotTrack": {"navigator": {"msDoNotTrack": "1"}},
    'doNotTrack "yes"': {"navigator": {"doNotTrack": "yes"}},
    "opted out": {"storage": {KEY: "1"}},
}


@pytest.mark.parametrize("case", sorted(NOTHING_LOADS))
def test_nothing_loads_off_host_under_gpc_or_dnt_or_opted_out(run: Any, case: str) -> None:
    result = run(**NOTHING_LOADS[case])
    assert result["dataLayer"] is None
    assert result["appended"] == []
    # The footer control is wired everywhere; nothing else is registered, so a purchase step
    # announced on this page has nothing to reach.
    assert result["listeners"] == ["DOMContentLoaded"]


def test_on_the_production_host_ga_loads_with_the_decided_configuration(run: Any) -> None:
    result = run()
    assert result["appended"] == [
        {"tag": "script", "async": True, "src": f"{analytics.GTAG_JS_URL}?id={MID}"}
    ]
    denied_ads = {"ad_storage": "denied", "ad_user_data": "denied", "ad_personalization": "denied"}
    assert result["dataLayer"] == [
        [
            "consent",
            "default",
            {
                **denied_ads,
                "analytics_storage": "denied",
                "region": list(analytics.ANALYTICS_DENIED_REGIONS),
            },
        ],
        ["consent", "default", {**denied_ads, "analytics_storage": "granted"}],
        ["js", "<date>"],
        [
            "config",
            MID,
            {
                "allow_google_signals": False,
                "allow_ad_personalization_signals": False,
                "page_location": f"https://{analytics.PRODUCTION_HOST}/",
                "page_referrer": "",
            },
        ],
    ]


@pytest.mark.parametrize(
    "scenario",
    [
        {"storage": {KEY: "0"}},
        {"storage": {"some-other-site:analytics-opt-out": "1"}},
        {"storageThrows": True},
        {"storageGetterThrows": True},
        {"navigator": {"doNotTrack": "0", "globalPrivacyControl": False}},
    ],
)
def test_anything_short_of_a_real_signal_or_opt_out_still_loads(
    run: Any, scenario: dict[str, Any]
) -> None:
    assert _loaded(run(**scenario))


def test_the_footer_control_opts_out_and_back_in_and_is_remembered(run: Any) -> None:
    result = run(domReady=True, clicks=2)
    first, out, back = result["states"]
    assert first == {
        "hidden": False,
        "text": "Opt out of analytics",
        "status": "",
        "boxHidden": False,
        "stored": {},
        "disabled": False,
    }
    assert out["text"] == "Opt back in"
    assert out["stored"] == {KEY: "1"}
    assert out["disabled"] is True
    assert out["status"] == analytics.OPT_OUT_MESSAGES["__MSG_OPTED_OUT__"]
    assert back["text"] == "Opt out of analytics"
    assert back["stored"] == {}
    assert back["status"] == analytics.OPT_OUT_MESSAGES["__MSG_BACK_IN__"]
    # The next page load reads the remembered choice before loading anything.
    later = run(domReady=True, storage={KEY: "1"})
    assert not _loaded(later)
    assert later["states"][0]["text"] == "Opt back in"
    assert later["states"][0]["status"] == analytics.OPT_OUT_MESSAGES["__MSG_IS_OUT__"]


def test_the_footer_control_is_wired_off_the_production_host_too(run: Any) -> None:
    result = run(hostname="127.0.0.1", domReady=True, clicks=1)
    assert not _loaded(result)
    assert result["states"][1]["stored"] == {KEY: "1"}


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ({"navigator": {"globalPrivacyControl": True}}, "__MSG_SIGNAL__"),
        ({"windowDoNotTrack": "1"}, "__MSG_SIGNAL__"),
        ({"storageThrows": True}, "__MSG_NO_STORAGE__"),
    ],
)
def test_under_a_signal_or_blocked_storage_the_button_is_hidden_and_says_why(
    run: Any, scenario: dict[str, Any], message: str
) -> None:
    state = run(domReady=True, **scenario)["states"][0]
    assert state["hidden"] is True
    assert state["boxHidden"] is False
    assert state["status"] == analytics.OPT_OUT_MESSAGES[message]


# --- negative controls: the harness must see each guard go missing ---

SABOTAGE = {
    "hostname": (
        f"  if (w.location.hostname !== {json.dumps(analytics.PRODUCTION_HOST)}) return;\n",
        {"hostname": "127.0.0.1"},
    ),
    "GPC": (
        "  if (n.globalPrivacyControl === true) return;\n",
        {"navigator": {"globalPrivacyControl": True}},
    ),
    "DNT": ('  if (dnt === "1" || dnt === "yes") return;\n', {"navigator": {"doNotTrack": "1"}}),
    "opt-out": ("  if (optedOut()) return;\n", {"storage": {KEY: "1"}}),
}


@pytest.mark.parametrize("guard", sorted(SABOTAGE))
def test_negative_control_removing_a_guard_is_caught(run: Any, guard: str) -> None:
    line, scenario = SABOTAGE[guard]
    snippet = analytics.head_snippet()
    assert snippet.count(line) == 1, f"the {guard} guard is not in the loader to remove"
    broken = snippet.replace(line, "", 1)
    assert broken != snippet and line not in broken  # the sabotage landed
    assert not _loaded(run(**scenario)), "the intact loader should load nothing here"
    assert _loaded(run(broken, **scenario)), f"removing the {guard} guard went unnoticed"


def test_negative_control_turning_google_signals_on_is_caught(run: Any) -> None:
    snippet = analytics.head_snippet()
    flag = "allow_google_signals: false"
    assert snippet.count(flag) == 1
    broken = snippet.replace(flag, "allow_google_signals: true", 1)
    assert broken != snippet
    config = run(broken)["dataLayer"][-1]
    assert config[0] == "config" and config[2]["allow_google_signals"] is True


# --- the bundle's purchase steps (ADR 0007): what reaches Google, and what never does ---

SESSION = "cs_live_a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuVwXyZ"
EMAIL = "buyer@example-compliance.test"
ORG = "Example Compliance Partners"
ORDER = "0123456789abcdef0123456789abcdef"
SETUP_PAGE = {
    "origin": f"https://{analytics.PRODUCTION_HOST}",
    "pathname": "/bundle/setup/",
    "search": f"?session_id={SESSION}",
    "hash": "#top",
}
PLAN_15 = {"item_id": "bundle_15", "price": 249}
PLAN_70 = {"item_id": "bundle_70", "price": 499}
#: Everything a page script could conceivably put in an event by mistake. None of it may reach
#: the dataLayer, whichever event carries it.
PII = (SESSION, EMAIL, ORG, "deliver_to", "program_name", "session_id", "cms-blue-button-2")


def _leaks(result: dict[str, Any]) -> list[str]:
    sent = json.dumps(result["dataLayer"])
    return [marker for marker in PII if marker in sent]


def _events(result: dict[str, Any]) -> list[list[Any]]:
    return [entry for entry in result["sent"] if entry[0] == "event"]


def test_the_page_address_is_sent_without_its_query_or_fragment(run: Any) -> None:
    config = run(location=SETUP_PAGE)["dataLayer"][-1]
    assert config[0] == "config"
    assert config[2]["page_location"] == f"https://{analytics.PRODUCTION_HOST}/bundle/setup/"
    assert SESSION not in json.dumps(config)


@pytest.mark.parametrize(
    ("referrer", "sent"),
    [
        (
            f"https://checkout.stripe.com/c/pay/{SESSION}#fidkdWxOYHwnPyd1",
            "https://checkout.stripe.com",
        ),
        (
            f"https://{analytics.PRODUCTION_HOST}/bundle/setup/?session_id={SESSION}",
            f"https://{analytics.PRODUCTION_HOST}/bundle/setup/",
        ),
        ("https://www.google.com/search?q=fhir+payer+compliance", "https://www.google.com"),
        ("", ""),
        ("not a url", ""),
    ],
)
def test_the_referrer_is_an_origin_or_a_path_on_this_site_and_never_a_query(
    run: Any, referrer: str, sent: str
) -> None:
    config = run(referrer=referrer)["dataLayer"][-1]
    assert config[2]["page_referrer"] == sent


def test_view_item_and_begin_checkout_carry_the_plan_and_price_and_nothing_else(run: Any) -> None:
    dirty = {
        "event": "view_item",
        "currency": "USD",
        "value": 249,
        "items": [
            {**PLAN_15, "email": EMAIL, "item_name": ORG},
            {**PLAN_70, "session_id": SESSION},
        ],
        "deliver_to": EMAIL,
        "program_name": ORG,
    }
    result = run(commerce=[dirty, {**dirty, "event": "begin_checkout", "items": [PLAN_70]}])
    assert _events(result) == [
        [
            "event",
            "view_item",
            {
                "currency": "USD",
                "value": 249,
                "items": [
                    {"item_id": "bundle_15", "price": 249, "quantity": 1},
                    {"item_id": "bundle_70", "price": 499, "quantity": 1},
                ],
                "page_location": f"https://{analytics.PRODUCTION_HOST}/",
                "page_referrer": "",
            },
        ],
        [
            "event",
            "begin_checkout",
            {
                "currency": "USD",
                "value": 249,
                "items": [{"item_id": "bundle_70", "price": 499, "quantity": 1}],
                "page_location": f"https://{analytics.PRODUCTION_HOST}/",
                "page_referrer": "",
            },
        ],
    ]
    for _, _, params in _events(result):
        assert set(params) <= set(analytics.COMMERCE_FIELDS)
    assert _leaks(result) == []
    assert json.loads(result["session"][analytics.CHECKOUT_STORAGE_KEY]) == {
        "item": {"item_id": "bundle_70", "price": 499, "quantity": 1},
        "currency": "USD",
    }


def test_a_purchase_carries_the_hashed_order_and_the_plan_chosen_and_counts_once(run: Any) -> None:
    result = run(
        location=SETUP_PAGE,
        session={analytics.CHECKOUT_STORAGE_KEY: json.dumps({"item": PLAN_15, "currency": "USD"})},
        commerce=[
            {"event": "purchase", "transaction_id": ORDER, "email": EMAIL},
            {"event": "purchase", "transaction_id": ORDER},  # a reload of the same page
        ],
    )
    assert _events(result) == [
        [
            "event",
            "purchase",
            {
                "transaction_id": ORDER,
                "currency": "USD",
                "value": 249,
                "items": [{"item_id": "bundle_15", "price": 249, "quantity": 1}],
                "page_location": f"https://{analytics.PRODUCTION_HOST}/bundle/setup/",
                "page_referrer": "",
            },
        ]
    ]
    assert _leaks(result) == []
    assert json.loads(result["session"][analytics.CHECKOUT_STORAGE_KEY]) == {"reported": ORDER}


def test_a_purchase_from_a_tab_with_no_checkout_is_counted_without_a_value(run: Any) -> None:
    result = run(location=SETUP_PAGE, commerce=[{"event": "purchase", "transaction_id": ORDER}])
    assert _events(result) == [
        [
            "event",
            "purchase",
            {
                "transaction_id": ORDER,
                "page_location": f"https://{analytics.PRODUCTION_HOST}/bundle/setup/",
                "page_referrer": "",
            },
        ]
    ]


@pytest.mark.parametrize(
    "detail",
    [
        {"event": "purchase", "transaction_id": SESSION},
        {"event": "purchase", "transaction_id": EMAIL},
        {"event": "purchase", "transaction_id": ORDER.upper()},
        {"event": "purchase"},
        {"event": "add_payment_info", "currency": "USD", "value": 1, "items": [PLAN_15]},
        {"event": "view_item", "currency": "usd", "value": 249, "items": [PLAN_15]},
        {"event": "view_item", "currency": "USD", "value": "249", "items": [PLAN_15]},
        {"event": "view_item", "currency": "USD", "value": 249, "items": []},
        {"event": "view_item", "currency": "USD", "value": 249, "items": [{"item_id": EMAIL}]},
        {
            "event": "view_item",
            "currency": "USD",
            "value": 249,
            "items": [{"item_id": ORG, "price": 1}],
        },
        "purchase",
        None,
    ],
)
def test_an_event_the_loader_cannot_check_is_dropped_whole(run: Any, detail: Any) -> None:
    result = run(commerce=[detail])
    assert _events(result) == []
    assert _leaks(result) == []


def test_nothing_is_forwarded_after_an_opt_out_on_the_same_page(run: Any) -> None:
    step = {"event": "view_item", "currency": "USD", "value": 249, "items": [PLAN_15]}
    assert len(_events(run(commerce=[step]))) == 1
    assert _events(run(commerce=["OPT_OUT", step])) == []


def test_the_opt_out_removes_the_kept_checkout(run: Any) -> None:
    kept = {analytics.CHECKOUT_STORAGE_KEY: json.dumps({"item": PLAN_15, "currency": "USD"})}
    result = run(domReady=True, clicks=1, session=kept)
    assert result["states"][1]["stored"] == {KEY: "1"}
    assert analytics.CHECKOUT_STORAGE_KEY not in result["session"]


def test_negative_control_a_loader_that_sends_the_raw_address_is_caught(run: Any) -> None:
    snippet = analytics.head_snippet()
    stripped = "var loc = w.location, PAGE = loc.origin + loc.pathname;"
    assert snippet.count(stripped) == 1
    broken = snippet.replace(
        stripped, "var loc = w.location, PAGE = loc.origin + loc.pathname + loc.search;", 1
    )
    assert broken != snippet
    assert _leaks(run(location=SETUP_PAGE)) == []
    assert SESSION in _leaks(run(broken, location=SETUP_PAGE))


def test_negative_control_a_loader_that_trusts_the_transaction_id_is_caught(run: Any) -> None:
    snippet = analytics.head_snippet()
    check = "!/^[0-9a-f]{32}$/.test(t)"
    assert snippet.count(check) == 1
    broken = snippet.replace(check, "false", 1)
    assert broken != snippet
    step = {"event": "purchase", "transaction_id": SESSION}
    assert _leaks(run(commerce=[step])) == []
    assert SESSION in _leaks(run(broken, commerce=[step]))


def test_negative_control_a_loader_that_forwards_items_whole_is_caught(run: Any) -> None:
    snippet = analytics.head_snippet()
    rebuild = "var it = item(raw[i]); if (it) list.push(it);"
    assert snippet.count(rebuild) == 1
    broken = snippet.replace(rebuild, "if (item(raw[i])) list.push(raw[i]);", 1)
    assert broken != snippet
    step = {
        "event": "view_item",
        "currency": "USD",
        "value": 249,
        "items": [{**PLAN_15, "email": EMAIL}],
    }
    assert _leaks(run(commerce=[step])) == []
    assert EMAIL in _leaks(run(broken, commerce=[step]))


def test_the_privacy_page_names_the_purchase_steps_and_the_checkout_key(site: Path) -> None:
    privacy = (site / "privacy" / "index.html").read_text(encoding="utf-8")
    for fact in (
        "three steps toward a",
        "hashing Stripe's order reference",
        "Nothing typed into the setup form is ever recorded",
        analytics.CHECKOUT_STORAGE_KEY,
        "without its query string",
    ):
        assert fact in privacy, fact
