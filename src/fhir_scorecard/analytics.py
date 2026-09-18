"""Google Analytics 4 on the published HTML pages, and nowhere else ([ADR 0006](../../docs/adr/0006-google-analytics-4.md)).

The owner decided on 2026-09-17 to run GA4 on every public site in the portfolio, with the
privacy copy changed to match. This module is the one place that decision is implemented, and
:data:`GA4_MEASUREMENT_ID` is the one place the property's measurement ID lives. The ID is
public - every page that loads GA hands it to the browser - so it is committed as site
configuration rather than kept as a secret. Set it to ``""`` and the build emits no GA at all:
no ``<script>`` naming Google, no footer control, and ``/privacy/`` says the site runs no
analytics.

What :func:`head_snippet` emits when an ID is set, once per HTML page, in ``<head>``:

* One inline ``<script>``. It loads nothing unless the page is being served from
  :data:`PRODUCTION_HOST`, so a local preview, a test build, the CI audit, or a copy served
  from anywhere else never requests gtag.js or sends a hit to the real property.
* It returns before loading anything when the browser sends Global Privacy Control
  (``navigator.globalPrivacyControl === true``) or Do Not Track (``navigator.doNotTrack``,
  ``window.doNotTrack`` or ``navigator.msDoNotTrack`` is ``"1"`` or ``"yes"``), or when the
  visitor used the footer's "Opt out of analytics" (``localStorage`` :data:`OPT_OUT_STORAGE_KEY`
  is ``"1"``). No Google script, no request to Google, no cookie.
* Before those checks it wires the footer control (:data:`FOOTER_CONTROL`) on
  ``DOMContentLoaded``, on every host, so the control can be exercised anywhere. "Opt out of
  analytics" sets the flag and Google's own ``window["ga-disable-<ID>"]`` property; "Opt back
  in" removes the flag. Under GPC or DNT, or with storage blocked, the button stays hidden and
  the status line says why.
* Consent Mode v2 defaults: ``ad_storage``, ``ad_user_data`` and ``ad_personalization`` are
  denied everywhere; ``analytics_storage`` is denied through ``region`` for the EEA, the UK and
  Switzerland and granted elsewhere. There is no consent banner, so nothing ever updates those
  defaults, and visitors in those regions get no GA cookie while gtag still sends Google
  cookieless pings. ``/privacy/`` says so.
* ``gtag("config", ...)`` with ``allow_google_signals`` and ``allow_ad_personalization_signals``
  both false.

This is a static multi-page site, not a single-page app: every navigation is a full page load,
so gtag's own ``page_view`` on ``config`` is the page view and nothing has to be sent on a route
change. The page address is sent as origin and path only, without its query string or fragment,
and the referrer as its origin alone, or its origin and path when it is a page of this site
([ADR 0007](../../docs/adr/0007-bundle-conversion-events.md)). ``/bundle/setup/`` is reached with
Stripe's order reference in its query string, and the page Stripe sends a buyer from carries one
in its path; neither may reach Google.

On the bundle pages the loader also forwards three purchase steps -- ``view_item``,
``begin_checkout`` and ``purchase`` -- that ``assets/bundle.js`` and ``assets/bundle-setup.js``
announce as a :data:`COMMERCE_EVENT` event on the document. The loader is the only code that
calls ``gtag``, it registers the listener only after every guard above has passed, and it
rebuilds each event from fields it checks one at a time (:data:`COMMERCE_FIELDS`), so nothing
else a page script put in an event can reach Google. The plan a buyer chose is kept in the tab's
``sessionStorage`` under :data:`CHECKOUT_STORAGE_KEY` from the checkout click to the purchase,
because the address Stripe returns to does not say what was bought.

The machine-readable outputs - ``dataset.csv``, ``scorecards.json``, the ``api/`` tree, the
Atom feeds and the badge SVGs - never pass through this module. Only ``site._shell`` calls it,
and only HTML pages go through ``_shell``.
"""

from __future__ import annotations

import html
import json
import re

#: The one place the measurement ID goes. ``""`` means no GA anywhere. ``G-5XE50LHZDJ`` is the
#: fhir.chelseakr.com web stream of GA4 property 554880958 (provisioned 2026-09-17 with 14-month
#: event retention and Google signals disabled on the property).
GA4_MEASUREMENT_ID = "G-5XE50LHZDJ"

#: What ``/privacy/`` tells a visitor about retention. It must match the property's Admin > Data
#: collection and modification > Data retention setting.
GA4_DATA_RETENTION = "14 months"

#: The only hostname the loader runs on. ``tests/test_analytics.py`` holds it equal to the host
#: of ``site.DEFAULT_ORIGIN``, so the two cannot drift apart.
PRODUCTION_HOST = "fhir.chelseakr.com"

#: GA4 web-stream measurement IDs are ``G-`` then uppercase letters and digits. Checked strictly
#: because the value is interpolated into an inline script.
MEASUREMENT_ID_RE = re.compile(r"G-[A-Z0-9]{4,20}")

#: ``analytics_storage`` defaults to denied for visitors in these regions (ISO 3166-1 alpha-2):
#: the 27 EU member states, the three other EEA states (Iceland, Liechtenstein, Norway), the
#: United Kingdom, and Switzerland.
EU_MEMBER_STATES = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
)  # fmt: skip
ANALYTICS_DENIED_REGIONS = (*EU_MEMBER_STATES, "IS", "LI", "NO", "GB", "CH")

GTAG_JS_URL = "https://www.googletagmanager.com/gtag/js"

#: The document event the bundle pages announce a purchase step with. Only the loader listens.
COMMERCE_EVENT = "fhir-scorecard:commerce"

#: Where the loader keeps ``{item, currency}`` from a checkout click until the purchase, and then
#: ``{reported: <transaction id>}`` so a reload is not a second sale. Tab-scoped session storage,
#: written only while GA4 is on, and removed by the footer opt-out.
CHECKOUT_STORAGE_KEY = "fhir-scorecard:checkout"

#: Every parameter a forwarded purchase step can carry. ``page_location`` and ``page_referrer``
#: are the stripped values the page view carries; ``items`` holds only ``item_id`` (a plan id
#: from plan.json), ``price`` and ``quantity``.
COMMERCE_FIELDS = (
    "currency",
    "value",
    "items",
    "transaction_id",
    "page_location",
    "page_referrer",
)

#: The footer's "Opt out of analytics" choice, remembered per browser in ``localStorage`` under
#: this key and read before gtag.js is ever requested. Renaming it would silently opt every
#: opted-out visitor back in, so it is never renamed.
OPT_OUT_STORAGE_KEY = "fhir-scorecard:analytics-opt-out"

#: The footer control, on every page when an ID is set. ``hidden`` until the loader wires it on
#: ``DOMContentLoaded``, so a browser without JavaScript - which never runs GA either - is never
#: shown a button that does nothing. A ``<button>``, not a link, because it changes a setting
#: rather than going anywhere (WCAG 2.2 SC 4.1.2); USWDS's unstyled button makes it read like
#: the links around it.
FOOTER_CONTROL = (
    '<span class="analytics-choice" data-analytics-choice hidden>'
    '<button type="button" class="usa-button usa-button--unstyled analytics-toggle">'
    "Opt out of analytics</button> "
    '<span class="analytics-status" role="status"></span></span>'
)

#: The status line after each state change, announced by its ``role="status"`` live region.
#: ``/privacy/`` describes the same behavior.
OPT_OUT_MESSAGES = {
    "__MSG_OPTED_OUT__": (
        "Opted out. From the next page you open, this site will not load Google Analytics "
        "in this browser."
    ),
    "__MSG_IS_OUT__": "You have opted out: this site does not load Google Analytics in this browser.",
    "__MSG_BACK_IN__": "Opted back in. Analytics resumes from the next page you open.",
    "__MSG_SIGNAL__": (
        "Analytics is off: your browser sends Global Privacy Control or Do Not Track."
    ),
    "__MSG_NO_STORAGE__": (
        "This browser is blocking site storage, so an opt-out cannot be remembered here. "
        "Global Privacy Control or Do Not Track keeps analytics off."
    ),
}

_SNIPPET_TEMPLATE = r"""<script>
(function () {
  var w = window, n = navigator, d = document, KEY = __OPT_OUT_KEY__, OFF = __GA_DISABLE__;
  var CK = __CHECKOUT_KEY__;
  var store = null;
  try { store = w.localStorage; store.getItem(KEY); } catch (e) { store = null; }
  function optedOut() { try { return !!store && store.getItem(KEY) === "1"; } catch (e) { return false; } }
  var dnt = n.doNotTrack || w.doNotTrack || n.msDoNotTrack;
  var signal = n.globalPrivacyControl === true || dnt === "1" || dnt === "yes";
  d.addEventListener("DOMContentLoaded", function () {
    var box = d.querySelector("[data-analytics-choice]");
    if (!box) return;
    var button = box.querySelector("button"), status = box.querySelector("[role=status]");
    function render(message) {
      button.textContent = optedOut() ? "Opt back in" : "Opt out of analytics";
      button.hidden = signal || !store;
      status.textContent = message;
      box.hidden = false;
    }
    button.addEventListener("click", function () {
      try {
        if (optedOut()) {
          store.removeItem(KEY);
          w[OFF] = false;
          render(__MSG_BACK_IN__);
        } else {
          store.setItem(KEY, "1");
          w[OFF] = true;
          try { w.sessionStorage.removeItem(CK); } catch (e) { /* nothing was kept */ }
          render(__MSG_OPTED_OUT__);
        }
      } catch (e) {
        store = null;
        render(__MSG_NO_STORAGE__);
      }
    });
    render(signal ? __MSG_SIGNAL__ : !store ? __MSG_NO_STORAGE__ : optedOut() ? __MSG_IS_OUT__ : "");
  });
  if (w.location.hostname !== __HOST__) return;
  if (n.globalPrivacyControl === true) return;
  if (dnt === "1" || dnt === "yes") return;
  if (optedOut()) return;
  w.dataLayer = w.dataLayer || [];
  function gtag() { w.dataLayer.push(arguments); }
  gtag("consent", "default", {
    ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    analytics_storage: "denied", region: __DENIED_REGIONS__
  });
  gtag("consent", "default", {
    ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    analytics_storage: "granted"
  });
  var loc = w.location, PAGE = loc.origin + loc.pathname;
  var ref = /^(https?:\/\/[^\/?#]+)([^?#]*)/.exec(d.referrer || "");
  var REF = !ref ? "" : ref[1] === loc.origin ? ref[1] + ref[2] : ref[1];
  gtag("js", new Date());
  gtag("config", __ID__, {
    allow_google_signals: false, allow_ad_personalization_signals: false,
    page_location: PAGE, page_referrer: REF
  });
  function money(x) { return typeof x === "number" && isFinite(x) && x >= 0 && x <= 100000 ? x : null; }
  function item(x) {
    if (!x || typeof x !== "object") return null;
    var id = x.item_id, p = money(x.price);
    return typeof id === "string" && /^[a-z0-9_]{1,40}$/.test(id) && p !== null ? { item_id: id, price: p, quantity: 1 } : null;
  }
  function cur(x) { return typeof x === "string" && /^[A-Z]{3}$/.test(x) ? x : ""; }
  function keep(v) {
    try { if (v === null) w.sessionStorage.removeItem(CK); else w.sessionStorage.setItem(CK, JSON.stringify(v)); } catch (e) { /* storage refused */ }
  }
  function kept() { try { return JSON.parse(w.sessionStorage.getItem(CK) || "null"); } catch (e) { return null; } }
  d.addEventListener(__COMMERCE_EVENT__, function (ev) {
    var x = ev && ev.detail;
    if (optedOut() || !x || typeof x !== "object") return;
    if (x.event === "purchase") {
      var t = x.transaction_id, c = kept();
      if (typeof t !== "string" || !/^[0-9a-f]{32}$/.test(t) || (c && c.reported === t)) return;
      var b = c && item(c.item), m = c && cur(c.currency);
      keep({ reported: t });
      gtag("event", "purchase", b && m
        ? { transaction_id: t, currency: m, value: b.price, items: [b], page_location: PAGE, page_referrer: REF }
        : { transaction_id: t, page_location: PAGE, page_referrer: REF });
      return;
    }
    if (x.event !== "view_item" && x.event !== "begin_checkout") return;
    var list = [], raw = Array.isArray(x.items) ? x.items.slice(0, 8) : [];
    for (var i = 0; i < raw.length; i++) { var it = item(raw[i]); if (it) list.push(it); }
    var m2 = cur(x.currency), v = money(x.value);
    if (!list.length || !m2 || v === null) return;
    if (x.event === "begin_checkout") keep({ item: list[0], currency: m2 });
    gtag("event", x.event, { currency: m2, value: v, items: list, page_location: PAGE, page_referrer: REF });
  });
  var s = d.createElement("script");
  s.async = true;
  s.src = __GTAG_SRC__;
  d.head.appendChild(s);
})();
</script>"""


def measurement_id(value: str | None) -> str | None:
    """``None`` for an unset or blank ID, the ID itself when it is well formed.

    Anything else raises rather than being written into a script: a typo should fail the build,
    not ship a broken tag.
    """
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not MEASUREMENT_ID_RE.fullmatch(value):
        raise ValueError(f"not a GA4 measurement ID (expected G-XXXXXXXXXX): {value!r}")
    return value


def enabled() -> bool:
    """Whether this build carries GA4 at all, read at call time so a test can switch it off."""
    return measurement_id(GA4_MEASUREMENT_ID) is not None


def head_snippet(ga4_id: str | None = None) -> str:
    """The ``<head>`` markup for one page: ``""`` with no ID, otherwise the guarded loader.

    ``ga4_id`` defaults to :data:`GA4_MEASUREMENT_ID`, read at call time.
    """
    mid = measurement_id(GA4_MEASUREMENT_ID if ga4_id is None else ga4_id)
    if mid is None:
        return ""
    replacements = {
        "__HOST__": json.dumps(PRODUCTION_HOST),
        "__DENIED_REGIONS__": json.dumps(list(ANALYTICS_DENIED_REGIONS)),
        "__ID__": json.dumps(mid),
        "__GTAG_SRC__": json.dumps(f"{GTAG_JS_URL}?id={mid}"),
        "__OPT_OUT_KEY__": json.dumps(OPT_OUT_STORAGE_KEY),
        "__CHECKOUT_KEY__": json.dumps(CHECKOUT_STORAGE_KEY),
        "__COMMERCE_EVENT__": json.dumps(COMMERCE_EVENT),
        "__GA_DISABLE__": json.dumps(f"ga-disable-{mid}"),
        **{token: json.dumps(message) for token, message in OPT_OUT_MESSAGES.items()},
    }
    snippet = _SNIPPET_TEMPLATE
    for token, value in replacements.items():
        snippet = snippet.replace(token, value)
    return snippet


def footer_note() -> str:
    """The footer's privacy sentence, the link to ``/privacy/``, and the control when GA is on."""
    link = '<a href="/privacy/">Privacy</a>'
    if not enabled():
        return (
            f"<p>This site runs no analytics and sets no cookies. {link}: what this site "
            "measures and what it never does.</p>"
        )
    return (
        "<p>Pages on this site use Google Analytics 4 to count visits, with its advertising "
        "features off. It does not load in a browser that sends Global Privacy Control or Do "
        f"Not Track. {link}: what it records and how to turn it off. {FOOTER_CONTROL}</p>"
    )


def privacy_body() -> str:
    """The body of ``/privacy/``, true for whichever state :data:`GA4_MEASUREMENT_ID` is in."""
    crumbs = (
        '<nav class="usa-breadcrumb" aria-label="Breadcrumbs"><ol class="usa-breadcrumb__list">'
        '<li class="usa-breadcrumb__list-item"><a href="/" class="usa-breadcrumb__link">'
        "<span>Home</span></a></li></ol></nav>"
    )
    hosting = """<h2>Hosting</h2>
<p>The site is static files served by GitHub Pages. GitHub receives each request for a page, as
any web host does, and its own handling of that is covered by the
<a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub
General Privacy Statement</a>. This project sees no server log.</p>
<h2>The probes are a different thing</h2>
<p>This page is about people reading the site. What the project does to the FHIR servers it
grades is stated separately, in <a href="/claim/">our probe contract</a>: two public discovery
documents per endpoint, never authenticated, and never any patient data.</p>"""
    if not enabled():
        return f"""
{crumbs}
<p class="eyebrow">Privacy</p>
<h1>What this site measures</h1>
<p class="lede">Nothing. This site runs no analytics, loads no script from anyone else, and sets
no cookies.</p>
{hosting}
"""
    retention = html.escape(GA4_DATA_RETENTION)
    key = html.escape(OPT_OUT_STORAGE_KEY)
    checkout = html.escape(CHECKOUT_STORAGE_KEY)
    return f"""
{crumbs}
<p class="eyebrow">Privacy</p>
<h1>What this site measures</h1>
<p class="lede">The pages use Google Analytics 4 to count visits. Its advertising features are
off, and it does not load at all if your browser asks not to be tracked.</p>
<h2>What Google Analytics records</h2>
<p>When a page loads, Google Analytics records a page view: the page's address, the page you came
from, your browser, device type and screen size, and an approximate location. Google derives that
location from your IP address and says it does not store the address itself. It also records
some interactions Google turns on by default, such as scrolling to the bottom of a page, clicking
a link that leaves this site, and downloading a file such as <code>dataset.csv</code>.</p>
<p>The page address is sent without its query string, and the page you came from as its site
alone, or as its address without a query string when it is a page of this site.</p>
<h2 id="purchase-steps">On the compliance bundle pages</h2>
<p>The <a href="/bundle/">compliance report bundle</a> pages also record three steps toward a
purchase: that the plans on sale were shown, that a checkout link was followed, and that Stripe
sent a buyer back after paying. Each carries the plan and its price in dollars. The purchase
carries an order number made by hashing Stripe's order reference, so the reference itself is
never sent. Nothing typed into the setup form is ever recorded. To connect the purchase to the
plan chosen, the plan is kept in this tab's session storage under <code>{checkout}</code> from
the checkout click until Stripe sends you back.</p>
<p>It sets two first-party cookies, <code>_ga</code> and <code>_ga_&hellip;</code>, which let it
tell a returning browser from a new one. They last up to two years. Google LLC receives and
stores the data, and this project keeps it for {retention}.</p>
<h2>What is switched off</h2>
<p>Google signals and ad personalization are off, and the advertising consent settings are denied
for every visitor. Nothing collected here is used to show you ads, joined to a Google account, or
sold. This project has no ad account and no other analytics tool.</p>
<h2>Visitors in Europe, the UK and Switzerland</h2>
<p>If you are in the European Economic Area, the United Kingdom or Switzerland, analytics storage
defaults to denied. Google Analytics sets no cookie for you, but it still sends Google a cookieless
ping for each page view, without an identifier that links one visit to the next.</p>
<h2>How to turn it off</h2>
<p>Google Analytics does not load at all if your browser sends Global Privacy Control or Do Not
Track. You can also use the <strong>Opt out of analytics</strong> button at the foot of every
page. It stores <code>{key}</code> in this browser's local storage, not in a cookie, and every
page checks it before loading anything from Google. Opting out also removes
<code>{checkout}</code>. It covers this browser on this device only, clearing the site's data
clears it, and it does not delete Google cookies already set. The same
button reads <strong>Opt back in</strong> once you have opted out.</p>
<h2>What is never tracked</h2>
<p>The data files, <code>dataset.csv</code>, <code>scorecards.json</code> and the
<code>api/</code> tree, the Atom feeds and the badge images carry no script. A program or feed
reader that fetches them is not counted. A click on a download link from one of these pages may be
counted as a download.</p>
{hosting}
"""
