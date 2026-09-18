# Compliance report bundle: what it is, what it costs, how it turns on

Built 2026-09-14 following the playbook gtfs-scorecard's program report bundle launched with on
2026-09-12 (`gtfs-scorecard/docs/program-plan.md`), and made launch-ready on 2026-09-18 after the
owner decided to "set up and launch" it (PR #151). This page is the plan: the
pieces, the prices, what is deliberately not built, and the day-90 gate. The owner's step-by-step
launch checklist is its own page, [`compliance-bundle-owner-steps.md`](compliance-bundle-owner-steps.md).

**Nothing in this tier can charge anyone today.** No Stripe account or object exists for this
product, no AWS resource in `infra/compliance-bundle/` has been applied, and
`data/bundle/plan.json` ships with `paymentsAvailable: false`, no `setup_api_base`, and no
checkout links. Each of those on its own keeps `/bundle/` from showing a price or a Buy link.

The tier sells passively, through the site and search. There is no outreach, no email campaign,
and no consulting attached to it.

## Step 1, before anything else: does this belong in the market at all

Before any of this was built, two questions were checked against public information, because a
paid product built into a market a free institutional competitor already owns is a bad idea
regardless of how well it is engineered.

**Lantern (ONC/MITRE).** Lantern is the government's own FHIR endpoint monitor -- free, run by
ONC (with Mettle Solutions), open source. Its endpoint lists are sourced primarily from the
Certified Health IT Product List, i.e. **ONC-certified EHR vendor / provider-side** endpoints,
though its dashboard does carry a "Payer" source tag. It is a monitoring and analytics dashboard:
availability, adoption statistics, and CapabilityStatement validation checkmarks. **It does not
publish letter grades, prioritized findings, or spec-cited compliance reports.** This registry's
subject is the other side of the market entirely: payer Patient Access and Provider Directory
APIs under the CMS Interoperability and Patient Access Rule (`data/cohorts/*-marketplace.json`,
the `payer` and `payer_provider_directory` kinds -- 70 of the registry's 81 endpoints when this was
checked, 77 of 88 on 2026-09-18). README's
own "How this relates to Inferno and Lantern" section already states this: "Lantern (ONC)
monitors FHIR endpoints of certified EHRs on the provider side. This project's target registry is
the payer side, which has no equivalent public monitor." Verified independently for this task via
Lantern's own documentation and GitHub README (`onc-healthit/lantern-back-end`) before building
anything: the finding holds. **Genuinely differentiated, not redundant with a free government
tool.**

**CMS itself has no equivalent live tool.** CMS is only *proposing* (CMS-0062-P) to require
payers to report their API endpoints for CMS to centralize; no public payer endpoint directory or
compliance dashboard exists from CMS today.

**The buyer.** Real compliance dates exist and are binding: the Patient Access API has been
required since CMS-9115-F (2021); CMS-0057-F adds Provider Directory, Payer-to-Payer, and Prior
Authorization API obligations with provisions phasing in through January 1, 2026 and 2027, plus a
new requirement that impacted payers (MA organizations, state Medicaid/CHIP agencies, Medicaid
managed care plans, CHIP managed care entities, and QHP issuers on the FFEs) report Patient Access
API usage metrics to CMS annually. Enforcement runs through each program's existing authority
(civil monetary penalties are possible for QHP issuers and MA organizations). The realistic buyer
is not gtfs-scorecard's buyer: not a public transit agency, but a compliance consultancy tracking
several payer clients, a health IT vendor (Edifecs-, Smile CDR-, 1upHealth-shaped) benchmarking
its own client base, or a state Medicaid/CHIP managed-care oversight office auditing its
contracted MCOs -- private-sector or quasi-private budget holders with real regulatory exposure,
distinct from gtfs-scorecard's public-sector transit-agency-program buyer. This is a plausibility
check, not proof of demand: gtfs-scorecard's own bundle had zero purchases in its first week, and
this one should be read with the same expectation.

## What it is

A compliance consultancy tracking several payer clients, a health IT vendor benchmarking a client
base, or a state Medicaid or CHIP office overseeing several managed-care organizations buys one
archive: every named endpoint's evidence report (the same content `/endpoint/<id>/report/`
already publishes for free, entity_report.py), with the buyer's own name, logo, and accent on
each cover, plus a manifest that names every id that was asked for and what happened to it.

Each file is the same free, self-contained report the site already ships for one endpoint. The
bundle computes no new finding and no new grade. It is packaging, branding, and delivery.

| Piece | Where | Status |
| --- | --- | --- |
| Core: validate a request, classify ids against the registry and the published dataset, render each included one through `entity_report.report_page`, zip with a manifest | `src/fhir_scorecard/bundle.py`, `bundle_report.py`; `fhir-scorecard bundle` | Built, tested |
| Fulfillment: collect the stored order, render, upload behind a capability key, email the link | `.github/workflows/compliance-bundle.yml`, `src/fhir_scorecard/bundle_order.py` | Built, tested. Takes one input, an opaque order reference, and masks every buyer value before any step can print it |
| Purchase plumbing: post-checkout form, download route, Stripe webhook, private bucket, the workflow's OIDC role | `infra/compliance-bundle/` | Written and **planned** (28 resources to add, 0 to change); **never applied** |
| Secrets: Stripe restricted key, webhook signing secret, GitHub dispatch token | SSM Parameter Store, `/fhir-scorecard/compliance-bundle/*` | Read at run time by the Lambdas; never a Terraform variable, an environment variable, or state. **Do not exist yet** |
| Pages: `/bundle/`, `/bundle/setup/`, `/bundle/trust/` | `src/fhir_scorecard/site.py` | Built. Closed: "not open yet", no price, no link, no Offer, until `site.bundle_offers` finds everything a purchase needs |
| Measurement: `view_item`, `begin_checkout`, `purchase` in GA4, with no personal data | `analytics.py`, `assets/bundle.js`, `assets/bundle-setup.js` | Built, tested ([ADR 0007](adr/0007-bundle-conversion-events.md)) |
| Stripe objects: an account, one product, two prices, two Payment Links | `scripts/stripe-setup.sh` | **Do not exist.** The owner's checklist creates them |
| Quarterly refresh, and the daily reconciler for paid-but-undelivered orders | gtfs-scorecard has both | **Not built, and not sold.** See below |

## Fail closed, at both ends

- **The page.** `site.bundle_offers` returns a plan only if `paymentsAvailable` is exactly
  `true`, `setup_api_base` is an https URL, the currency is three capital letters, and that plan
  has a positive price and a `https://buy.stripe.com/` Payment Link. Anything missing, and the
  page shows no price, no link, no Offer structured data, and no conversion script. The setup
  form carries the API base whenever one is configured, so the owner's test-mode purchase works
  while the public page is still closed.
- **The Lambda.** `common.payments_enabled` is true only if Terraform's `payments_enabled` is
  `"1"`, the bucket is configured, and both the Stripe restricted key (an `rk_<mode>_` key for
  the declared mode, never an `sk_` key) and the GitHub token can be read from SSM. Otherwise the
  setup route answers 503 before it reads Stripe or claims the checkout, and the webhook refuses
  every signature while its secret is absent.
- **Terraform.** With `payments_enabled = "1"` the plan fails until both price ids are set and
  all three SSM parameters exist. `stripe_price_ids` accepts only the two one-time plans.
- **CI.** An open tier that still links to a test-mode Payment Link, or leaves a plan unsold,
  fails `tests/test_compliance_bundle_contract.py` on the owner's launch PR.

## Why the workflow takes only an order reference

The first draft of `compliance-bundle.yml` took the buyer's email address, organization name,
endpoint list, and bundle id (the download capability) as `workflow_dispatch` inputs, and passed
them to steps through `env:`. GitHub prints each step's environment in the run log, and this
repository's run logs are public, so every order would have published who bought, what they
track, and a working download link. The workflow now takes only a random order reference, the
setup Lambda stores the order in the private bucket, and `bundle_order mask` registers every
buyer value with `::add-mask::` before anything can print it.

## Prices: a starting guess, not research

| Plan | Price | What it covers |
| --- | --- | --- |
| `bundle_15` | **$249** once | One archive, up to 15 endpoints |
| `bundle_70` | **$499** once | One archive, up to 70 endpoints (77 payer-side endpoints are tracked today) |

These are proposals for the owner to confirm or change in `data/bundle/plan.json` before
`scripts/stripe-setup.sh` creates the Stripe prices from that file. The evidence behind them:

- **The sibling's prices.** gtfs-scorecard sells $149 for up to 25 agencies and $349 for up to
  100, to transit programs. These are about 1.7x and 1.4x those amounts. The buyer here holds a
  compliance or health-IT budget with real penalty exposure, not a small agency's IT line, but
  the content is equally free on the site, which caps what packaging is worth.
- **The scope of the report.** Every report is the same one a visitor can open free at
  `/endpoint/<id>/report/`. What a buyer pays for is the hand assembly they skip (opening,
  saving, and branding 15 or 70 reports, with a manifest of what was missing) and a dated,
  white-labeled archive to hand a client or a board. At an assumed $150 to $250 an hour for a
  compliance analyst, 15 reports is an hour or two of that work and 70 is most of a day, which
  is where $249 and $499 sit.
- **The buyer and the channel.** With no outreach, the purchase has to be one a single person
  can make on a company card from the page. Keeping the widest bundle under $500 is meant to
  stay under the line where a purchase order is required. That line varies by organization and
  was not checked; it is an assumption.
- **The registry's size.** 88 endpoints on 2026-09-18, 77 of them payer-side. The top tier was
  sized as "the whole payer-side registry" when that was 70; the registry has since grown past
  it. Raising the cap (to 100, say) is an owner decision with a price attached, and it touches
  the plan key everywhere it is written (`common.PLAN_ENDPOINT_CAPS`, `site.BUNDLE_PLANS`,
  `bundle.MAX_ENDPOINTS`, `main.tf`, `plan.json`, `stripe-setup.sh`), which
  `tests/test_compliance_bundle_contract.py` holds equal. It must happen before the Stripe
  prices are created, because a price's plan cannot be changed afterward.

gtfs-scorecard had a verified market anchor for its prices (a single agency's commercial
on-time-performance module at about $3,000 a year). **No comparable anchor was found for this
product.** Revisit at the day-90 gate like gtfs-scorecard does.

`data/bundle/plan.json` is the single source of these numbers. The page renders every price and
every Offer from it at build time, and `scripts/stripe-setup.sh` creates the Stripe prices from
it, so the charged amount and the shown amount cannot drift apart.

## What is deliberately not built, and why launching without it is honest

1. **The quarterly refresh.** The subscription plans (`refresh_qtr`, `refresh_yr`) exist in the
   Lambda code, with gtfs-scorecard's anti-arbitrage rule (a refresh inherits the cap of the
   bundle it renews), but nothing re-dispatches a subscription's second quarter. Selling one
   would charge for deliveries nothing sends, so they are not sold: Terraform accepts no price
   id for them, the page renders no card for them whatever plan.json says, and the Lambda
   recognizes no price it was not configured with. Resuming means porting gtfs-scorecard's
   `refresh_handler.py` and its schedule, then adding the two plans to `BUNDLE_PLANS`,
   `stripe_price_ids`, and plan.json together (the contract test holds them equal). A price for
   later: about $79 a quarter or $249 a year, also a guess.
2. **The daily reconciler** (gtfs-scorecard's `reconcile_handler.py`). Nothing audits "paid but
   never sent the setup form" automatically. At the volume a passive tier sees, Stripe's
   per-payment email to the owner and GitHub's failed-run email are the monitor; the owner
   checklist turns both on.
3. **Refund tooling.** Refunds are by hand in the Stripe Dashboard. The deployed key can only
   read Checkout Sessions, and a credential that can move money does not belong in a Lambda.

## Owner steps

See [`compliance-bundle-owner-steps.md`](compliance-bundle-owner-steps.md): the two decisions
(prices; a new Stripe account under the same login, recommended over sharing gtfs-scorecard's),
then Stripe settings, test-mode objects, the GitHub token, the SSM secrets, the apply, the
webhook, the repository variables, a test-mode purchase end to end, live mode, publishing the
prices, and one live purchase refunded.

## The day-90 gate

Read this ninety days after the page opens, following gtfs-scorecard's own stop rule: a tier
that sells nothing is revisited rather than left up as if it were working, and zero purchases in
the first days of a freshly launched paid tier is the expected starting state, not a failure
signal on its own. GA4's `view_item`, `begin_checkout`, and `purchase` counts say where a
visitor stopped.

## What stays free, unconditionally

Every endpoint's evidence page (`/endpoint/<id>/`, `/endpoint/<id>/report/`), the dataset, the
API, the CI action, and instant scoring via `fhir-scorecard check`. Nothing is subtracted from
the free tier to create this bundle. Grades, methodology, weights, and which endpoints are listed
are never for sale -- see `/bundle/trust/`.
