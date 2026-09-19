# 0007. Compliance bundle conversion events in GA4

## Status

Accepted - 2026-09-18

## Context

ADR 0006 loaded Google Analytics 4 as a page-view counter. The compliance report bundle
(`docs/compliance-bundle-plan.md`) sells through the site and search alone, with no outreach, so
the only way to tell whether `/bundle/` works is to count what readers do there. A checkout
happens on Stripe, and without explicit events GA4 would see it only as a click on a link to
another site, and would not see a completed purchase at all.

gtfs-scorecard added the same three GA4 recommended ecommerce events to its own bundle pages
(its ADR 0057). This mirrors those events on the fhir.chelseakr.com property so the two
products' funnels read the same way.

Two facts made the page address itself a problem. Stripe returns a buyer to
`/bundle/setup/?session_id=cs_...`, and the Checkout page a buyer arrives from carries the same
reference in its path. That reference is what the setup form uses to claim the order. ADR 0006
sent the page address as it was, because until the bundle no URL on this site carried a token.

## Decision

**Page address and referrer.** The loader's `config` now sets `page_location` to the page's
origin and path, with no query string or fragment, on every page. `page_referrer` is the
referrer's origin alone, or its origin and path when it is a page of this site. Neither the
order reference nor anything else in a query string reaches Google.

**Three events, one sender.** The bundle pages announce three steps as a `fhir-scorecard:commerce`
event on the document. The loader in `src/fhir_scorecard/analytics.py` is the only code that
calls `gtag`. It registers its listener only after every guard in ADR 0006 has passed, so
nothing is forwarded off the production host, under Global Privacy Control or Do Not Track, or
after the footer opt-out, including an opt-out made on the same page.

| Step | Where | GA4 event | Parameters |
| --- | --- | --- | --- |
| The plans on sale were shown | `/bundle/`, `assets/bundle.js`, only while a plan is on sale | `view_item` | `currency`, `value` (the first plan's price), `items` (every plan on sale) |
| A checkout link was followed | `/bundle/`, each plan's Buy link | `begin_checkout` | `currency`, `value`, `items` (that plan) |
| Stripe sent a buyer back after paying | `/bundle/setup/`, `assets/bundle-setup.js`, on load with a well-formed `session_id` | `purchase` | `transaction_id`, and `currency`, `value`, `items` when the plan is known |

Every event also carries the stripped `page_location` and `page_referrer`. Every item is
`{item_id, price, quantity: 1}`, where `item_id` is the plan key from `data/bundle/plan.json`.

**Each parameter is rebuilt, not passed through.** The loader checks the event name is one of the
three, a plan id matches `^[a-z0-9_]{1,40}$`, a price or value is a finite number from 0 to
100,000, the currency is three capital letters, and a transaction id is 32 lowercase hex
characters. It builds the GA4 parameters from those checked fields alone, so anything else in an
announcement (an email address or a raw order reference a future edit put there by mistake) is
dropped. An announcement that fails a check is dropped whole.

**The order reference never leaves the page.** `bundle-setup.js` hashes Stripe's session id with
SHA-256 and announces the first 32 hex characters as the transaction id. GA4 counts purchases
with one transaction id once, and the owner can match a GA4 purchase to a Stripe session by
hashing the session id the same way.

**What was bought.** The address Stripe returns to names no plan. On a checkout click the loader
keeps `{item, currency}` in the tab's `sessionStorage` under `fhir-scorecard:checkout`. On the
purchase it reads that, sends the plan and price, and replaces it with `{reported: <transaction
id>}`, so a reload of the setup page is not a second sale. A buyer who returns in a different
tab is still counted, without a value. The footer opt-out removes the key.

**No form field is ever measured.** The setup form sets `action="/bundle/setup/"` and
`method="post"`. Its script always sends it, but without them a browser with scripting off would
submit by GET and put the organization, email address and endpoint list in the page address.

## Consequences

- `/privacy/` says what the three steps carry, that the order reference is hashed, that nothing
  typed into the form is recorded, and names the session-storage key. `/bundle/trust/` says the
  same in one paragraph, and only while the build carries GA4.
- The loader grows from 3,018 to about 5,300 bytes on each page's own HTML. The weight budgets
  still pass; `tests/test_analytics.py` and `tests/test_accessibility_and_weight.py` hold that.
- `tests/test_analytics.py` runs the loader in Node with a stubbed page and dispatches
  announcements loaded with an email address, an organization name and a raw order reference,
  and asserts none reaches `dataLayer`. Three negative controls sabotage the loader (send the
  full address, trust the transaction id, forward items whole) and assert each leak is caught.
  `tests/test_bundle_scripts.py` holds the two page scripts to announcing only plan ids, prices,
  and a hashed order number.
- Owner follow-ups in the GA4 property, listed in `docs/compliance-bundle-owner-steps.md`: mark
  `begin_checkout` and `view_item` as key events (`purchase` is one by default), and turn off
  enhanced measurement's form interactions. With the form's `action` set, a `form_submit` would
  carry only `/bundle/setup/`, but it counts nothing the purchase event does not.
