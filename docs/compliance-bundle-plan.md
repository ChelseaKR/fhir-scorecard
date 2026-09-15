# Compliance report bundle: what it is, what it costs, how it would turn on

Built 2026-09-14, following the same playbook gtfs-scorecard's program report bundle shipped and
launched on 2026-09-12 (`gtfs-scorecard/docs/program-plan.md`). This page is the runbook: the
pieces, the prices, the honest state of the build, and the exact sequence that would open the
tier. **Nothing in this tier can charge anyone today.** No Stripe account exists for this
product, no AWS resources have been created, and `data/bundle/plan.json` ships with
`paymentsAvailable: false`.

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
the `payer` and `payer_provider_directory` kinds -- 70 of the registry's 81 endpoints). README's
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
check, not proof of demand: gtfs-scorecard's own bundle has zero purchases as of its own launch
week, and this one should be read with the same expectation.

## What it is

A compliance consultancy tracking several payer clients, a health IT vendor benchmarking a client
base, or a state Medicaid or CHIP office overseeing several managed-care organizations buys one
archive: every named endpoint's evidence report (the same content `/endpoint/<id>/report/`
already publishes for free, entity_report.py), with the buyer's own name, logo, and accent on
each cover, plus a manifest that names every id that was asked for and what happened to it.
One-time, or refreshed quarterly.

Each file is the same free, self-contained report the site already ships for one endpoint. The
bundle computes no new finding and no new grade. It is packaging, branding, and delivery.

| Piece | Where | Status |
| --- | --- | --- |
| Core: validate a request, classify ids against the registry and the published dataset, render each included one through `entity_report.report_page`, zip with a manifest | `src/fhir_scorecard/bundle.py`, `bundle_report.py`; `fhir-scorecard bundle` | Built, tested |
| Fulfilment: on-demand render, upload behind a capability key, email the link | `.github/workflows/compliance-bundle.yml` | Built; delivery steps gated on Actions variables that do not exist yet |
| Purchase plumbing: post-checkout form, download route, Stripe webhook | `infra/compliance-bundle/` | Written, **never applied**. No AWS resource described here exists |
| Quarterly re-dispatch for active subscriptions | gtfs-scorecard has `refresh_handler.py`; this repo does not yet | **Not built.** See "What is deliberately incomplete" below |
| Daily reconciler for paid-but-undelivered orders | gtfs-scorecard has `reconcile_handler.py`; this repo does not yet | **Not built.** |
| Storage: `compliance-bundles/<id>/bundle.zip` expires after 30 days | an S3 lifecycle rule | **Not written.** No artifacts bucket exists for this repo |
| Pages: `/bundle/`, `/bundle/setup/`, `/bundle/trust/` | `src/fhir_scorecard/site.py`, wired into every `fhir-scorecard grade` run | Built; live once deployed, `data/bundle/plan.json` says `paymentsAvailable: false` so every card reads "Not yet available" and no Offer structured data is emitted |
| Stripe objects: an account, two products, four prices, four Payment Links | nothing yet | **Does not exist.** See "Owner steps" below |

## Prices: a hypothesis, not research

Unlike gtfs-scorecard's program bundle, which has a verified market anchor (a single agency's
commercial on-time-performance module quoted at about $3,000/year), **no comparable anchor was
found for this product** during this build. The numbers below are a reasoned guess, not a
researched one, and should be read that way:

| Knob | Price | What it covers |
| --- | --- | --- |
| `bundle_15` | $299 once | One archive, up to 15 endpoints |
| `bundle_70` | $699 once | One archive, up to 70 endpoints (the entire payer-side registry) |
| `refresh_qtr` | $99 a quarter | A fresh archive every quarter, renews a bundle already bought, cancel any time |
| `refresh_yr` | $349 a year | The same, billed yearly |

Reasoning: the buyer is an institutional compliance or health-IT budget with real CMP exposure,
not a small transit agency's IT budget, so pricing is set roughly 2x gtfs-scorecard's per-tier
amounts -- but this is a guess about willingness to pay, made without a quoted comparable, and
should be revisited the same way gtfs-scorecard revisits its own prices at its day-90 gate (see
below). The registry itself is much smaller than GTFS Scorecard's (81 endpoints total, 70
payer-side, vs. 2,600+ feeds), so the tiers are sized to that reality: `bundle_70` is not "up to
some large round number that sounds impressive," it is "the entire tracked payer-side registry."

`data/bundle/plan.json` is the single source of these numbers. `bundle_page()` in `site.py`
renders every card and every `Offer` in JSON-LD directly from that file at build time -- there is
no second, hand-typed copy of a price anywhere in the HTML for a build step to drift from, and no
client-side rewriting is needed the way gtfs-scorecard's static `web/bundle/index.html` needs
`make sync-bundle-offers`, because this site is regenerated fresh by Python on every
`fhir-scorecard grade` run.

## What is deliberately incomplete, and why that is an acceptable stopping point

This is a substantial build finished to the point of "the core purchase-to-delivery path works
end-to-end, with real tests and negative controls," per the brief that authorized it, not to
gtfs-scorecard's full feature parity. What was cut, and what resuming it would take:

1. **Quarterly re-dispatch (`refresh_handler.py` in the reference implementation).** A
   subscription's *first* archive is dispatched by the setup route itself
   (`setup_handler._record_subscription`), so a `refresh_qtr` or `refresh_yr` purchase is not
   dead on arrival. What is missing is the recurring dispatch: nothing currently re-sends a
   second archive every quarter. Porting gtfs-scorecard's `refresh_handler.py` (241 lines) and
   its weekly `aws_cloudwatch_event_rule` almost mechanically would close this; the entitlement
   and idempotency logic it depends on (`common.py`, `PLAN_ENDPOINT_CAPS`) is already in place
   and already tested here.
2. **Daily reconciler (`reconcile_handler.py` in the reference implementation, 486 lines).**
   Nothing currently audits "paid but never returned to the setup form" or "claimed but never
   dispatched" orders. gtfs-scorecard's own reconciler is deployed but its CloudWatch schedule is
   still `DISABLED` pending a reporting channel, so porting this is not on this build's critical
   path either, but it is a real gap: an order that goes quiet between payment and delivery has
   no automated detection here at all yet.
3. **`scripts/build-lambda-package.sh` and `scripts/stripe-setup.sh`.** Neither exists in this
   repository. `infra/compliance-bundle/main.tf` names the first as a precondition of a
   successful `apply` (the Lambda deployment package does not build itself) and documents why a
   plain `pip install . -t build` on a Mac is not a substitute (it vendors macOS binaries that
   fail to import in the Lambda runtime, the same failure mode gtfs-scorecard's own comment
   documents). Porting both scripts from gtfs-scorecard's `scripts/` and repointing them at this
   package is mechanical but untested here.
4. **Refund tooling (`fhir-scorecard program-refunds` in the reference implementation).** Not
   built. Refunds would be manual via the Stripe dashboard until it is.
5. **A `data/bundle/plan.json` -> `terraform.tfvars` price-id sync test**, the analogue of
   gtfs-scorecard's CI check that a plan file and its Terraform variables cannot silently
   disagree. Deferred because there are no real price ids yet to disagree about.

None of this blocks the core path this task asked to prove: `fhir-scorecard bundle` genuinely
renders branded, self-contained, spec-cited compliance evidence reports from the published
dataset, a Stripe checkout's entitlement is correctly enforced and cannot be gamed by the cheapest
subscription (tested), the webhook's signature check refuses a forged or replayed event (tested),
and an unknown or disabled endpoint id is refused rather than silently dropped (tested).

## Owner steps this agent could not and did not take

Per this task's hard constraint, no Stripe account or Stripe object was created, and no AWS
resource was created. What exists is code written correctly against test-mode assumptions. To
open this tier for real, in order:

1. **Create a Stripe account for this product**, or decide it should share gtfs-scorecard's
   existing account (`acct_1UEJ7fAJdYOJsO05`) under a second set of products -- an operator
   decision this agent is not positioned to make, since it changes which legal entity's tax and
   compliance posture the revenue lands under.
2. **In test mode**, create two products and four prices (mirroring
   `scripts/stripe-setup.sh` in gtfs-scorecard, ported and re-pointed at this repo's four plan
   keys) with Payment Link success URLs at
   `https://fhir.chelseakr.com/bundle/setup/?session_id={CHECKOUT_SESSION_ID}`.
3. **Create a restricted key** scoped to Checkout Sessions: Read only, for the Lambda. Never the
   full secret key -- `infra/compliance-bundle/main.tf`'s `stripe_secret_key` variable refuses
   anything not prefixed `rk_`.
4. **Write `scripts/build-lambda-package.sh`** (port from gtfs-scorecard) and run it, then
   `terraform init && terraform apply` with `payments_enabled = "0"` for the first apply, exactly
   as gtfs-scorecard's own runbook did.
5. **Register a webhook endpoint** in Stripe (test mode) at the `webhook_url` output, events
   `checkout.session.completed`, `customer.subscription.created`, `.updated`, `.deleted`; apply
   again with the signing secret set.
6. **Set the Actions variables and secrets** the workflow reads: `ARTIFACTS_BUCKET`,
   `BUNDLE_API_BASE`, `SES_FROM`, `AWS_ROLE_ARN` -- none of which exist for this repository yet,
   and none of which this agent created.
7. **Walk the test-mode loop by hand**: buy a `bundle_15` with a Stripe test card, confirm the
   setup form dispatches, confirm the archive and manifest are correct, confirm a `bundle_15`
   buyer who lists 16 ids is refused with the reason rather than silently trimmed.
8. **Only after that**, in live mode: real Stripe products and prices, `stripe_price_ids_are_live
   = true`, and `data/bundle/plan.json` edited to `paymentsAvailable: true` with the real
   `checkout_url`s, in the same change, before merge -- `paymentsAvailable: true` with a null
   `checkout_url` publishes a page that announces checkout is open and then prices every plan
   "Not yet available."
9. **Write down, before turning it on**, what gtfs-scorecard's own runbook insists on recording
   at this step: tax treatment of the revenue, a written refund policy, and the two-business-day
   delivery commitment reviewed against what this pipeline actually guarantees. gtfs-scorecard's
   own review of its third item found the reconciler that would catch a breach was deployed
   disabled; this repository does not have a reconciler at all yet, which is a stronger reason to
   hold this step, not a weaker one.

## The day-90 gate

Read this ninety days after the page is linked and indexed, following gtfs-scorecard's own stop
rule: a tier that sells nothing is revisited rather than left up as if it were working, and zero
purchases in the first days of a freshly launched paid tier (as gtfs-scorecard's own is
experiencing) is the expected starting state, not a failure signal on its own.

## What stays free, unconditionally

Every endpoint's evidence page (`/endpoint/<id>/`, `/endpoint/<id>/report/`), the dataset, the
API, the CI action, and instant scoring via `fhir-scorecard check`. Nothing is subtracted from
the free tier to create this bundle. Grades, methodology, weights, and which endpoints are listed
are never for sale -- see `/bundle/trust/`.
