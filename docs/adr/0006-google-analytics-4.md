# 0006. Google Analytics 4 on the HTML pages

## Status

Accepted - 2026-09-17

## Context

Until now the site ran no analytics. `docs/RESPONSIBLE-TECH-AUDITS.md` §C said the repository
added no analytics or tracking, the README's Performance row said no page loaded a third-party
subresource, and `entity_report.py` argued there was no privacy-respecting way to count views.

On 2026-09-17 the owner decided that every public site in the portfolio runs Google Analytics 4,
with privacy pages and claims changed so nothing published becomes false. The property was
provisioned the same day: GA4 property 554880958, web stream measurement ID `G-5XE50LHZDJ`,
event data retention 14 months, Google signals disabled on the property.

## Decision

Load GA4 on every HTML page, and nowhere else, through one guarded inline loader in
`src/fhir_scorecard/analytics.py`. `site._shell` is the only page template, so the loader reaches
every page type (home, category, organization, endpoint, report, capability, cohort, coverage,
history, availability, over-time, method, claim and privacy) without a per-page edit.

**Where the ID lives.** `analytics.GA4_MEASUREMENT_ID`. It is public, so it is committed as site
configuration. `""` turns GA off: no loader, no footer control, and `/privacy/` and the footer
say the site runs no analytics. A malformed value raises when the site is built.

**When nothing loads.** The loader returns before creating `dataLayer` or requesting gtag.js:

- off `fhir.chelseakr.com` (`analytics.PRODUCTION_HOST`, held equal to the host of
  `site.DEFAULT_ORIGIN` by a test), so local builds, the test suite, the publish workflow's
  audit and any other copy never contact Google;
- when `navigator.globalPrivacyControl === true`;
- when `navigator.doNotTrack`, `window.doNotTrack` or `navigator.msDoNotTrack` is `"1"` or
  `"yes"`;
- when the visitor has used the footer's "Opt out of analytics" control, which stores `"1"`
  under `localStorage["fhir-scorecard:analytics-opt-out"]` and sets Google's own
  `window["ga-disable-G-5XE50LHZDJ"]`. The control toggles to "Opt back in". It is a `<button>`
  because it changes a setting, it stays `hidden` without JavaScript, and under GPC, DNT or
  blocked storage it is hidden with a status line saying why.

**How it is configured.** Consent Mode v2 defaults deny `ad_storage`, `ad_user_data` and
`ad_personalization` everywhere, and deny `analytics_storage` through `region` for the 27 EU
states, Iceland, Liechtenstein, Norway, the UK and Switzerland, granting it elsewhere. There is
no consent banner, so nothing updates those defaults; visitors in those regions get no GA cookie
and gtag sends Google cookieless pings, which the owner accepted. The config sets
`allow_google_signals: false` and `allow_ad_personalization_signals: false`.

**Not a single-page app.** Every navigation is a full page load, so gtag's own page view on
`config` is the page view and no route-change handling is needed. No URL the site writes carries
a token or anything about a person, so `page_location` is left as the page address.

**No CSP change.** The site sets no Content-Security-Policy: GitHub Pages sends no custom
headers and no page carries a CSP meta tag. A future CSP would need `www.googletagmanager.com`
in `script-src` and `*.google-analytics.com` and `*.analytics.google.com` in `connect-src` and
`img-src`.

**What stays untracked.** `dataset.csv`, `scorecards.json`, the `api/` tree, the Atom feeds and
the badge SVGs never pass through the page template and carry no script. A click on a download
link from a page may be recorded by GA's enhanced measurement as a download.

## Consequences

- Reading the site now sends Google a page view with the page address, referrer, browser and
  device data and an approximate location, and outside the EEA, UK and Switzerland sets the
  `_ga` cookies for up to two years. `/privacy/`, linked from every footer and listed in the
  sitemap, says so, along with retention, the regional behavior, and how to turn it off.
- The README's "no third-party subresource" statement is now "one third-party subresource,
  gtag.js". The weight budgets are unchanged: they count bytes this build writes, and the loader
  adds about 3 KB (3,018 bytes) to each page's own HTML.
- `tests/test_analytics.py` runs the loader in Node against stubbed browser objects. It checks
  that nothing loads off-host, under GPC, under each DNT spelling or when opted out; that the
  consent defaults and config are pushed in order; that the footer control toggles and is
  remembered; and that removing any one guard is caught, with each sabotage asserted to have
  landed first.
- Owner follow-up in the GA4 web stream: enhanced measurement is on by default. The privacy page
  describes page views, scrolls, outbound clicks and file downloads. If site search or form
  interactions are wanted off, that is a setting in the property, not in this repository.
