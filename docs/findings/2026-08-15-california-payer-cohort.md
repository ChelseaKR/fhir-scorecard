# What 27 California health plans publish about their FHIR endpoints

Curation review of 2026-08-07, written up 2026-08-15. Evidence:
[`data/cohorts/california.json`](../../data/cohorts/california.json) for the roster, the
reviews and the sources; [`data/registry.json`](../../data/registry.json) for the endpoints
that were verified; and
[`2026-08-15-california-payer-cohort.json`](2026-08-15-california-payer-cohort.json) beside
this file for the outcome classification the tables below are counted from. Every number here
is recomputed from those three files by
[`tests/test_findings_evidence.py`](../../tests/test_findings_evidence.py), which fails the
build if a figure in this document stops matching its data.

The cohort was assembled the way a cohort has to be if a hit rate is going to mean anything:
the membership list was fixed from a public roster before any endpoint was looked for, so the
plans that publish nothing are part of the result rather than an absence in it.

**What this is not.** Nothing below is a compliance determination. Every organization here is
obliged by the federal CMS Interoperability and Patient Access rule (CMS-9115-F) to expose a
Patient Access API and a Provider Directory API over FHIR R4. That rule does not require a
plan to print its base URL where an unregistered visitor can read it, and a plan that does not
is not violating anything this project reads. It is only not independently checkable from
outside, which is a narrower claim and the only one made here. No plan is ranked, no plan is
described as failing an obligation, and nothing here describes care quality.

**Nor is it a statement about California's Data Exchange Framework.** The DxF runs through the
Data Sharing Agreement and qualified health information organizations and requires none of the
surfaces reviewed here.

## Headline

Twenty-seven organizations: every Medi-Cal managed care plan in the DHCS directory plus every
Covered California qualified health plan issuer for 2026, deduplicated. Twenty-three are
Medi-Cal plans, eleven are Covered California issuers, and seven are both.

| Outcome of the review | plans | of 27 |
|---|---:|---:|
| Listed, with a base URL this project verified | 8 | 30% |
| Not listed | 19 | 70% |
| Publishes no base URL readable without an account | 12 | 44% |
| Publishes a base URL that did not resolve or did not answer | 4 | 15% |
| Publishes a sandbox base URL only, production unfilled | 2 | 7% |
| Publishes a base URL that answers but cannot be attributed to the plan | 1 | 4% |

The four rows under "not listed" partition the 19. Seven of those 19 plans did publish a base
URL in some form; the other twelve published none that a visitor without an account could read.

Every one of the nineteen reviews was completed on 2026-08-07 and carries a reason, a review
method, a date and a source URL. Eighteen distinct sources cover them: two plans share one
document, because one plan's developer page is the documentation for both.

| Depth of the review behind an exclusion | plans |
|---|---:|
| The plan's own documentation was retrieved (`portal_reviewed`) | 17 |
| No interoperability documentation could be located at all (`not_located`) | 2 |

The eight listed plans account for eleven verified endpoints, seven Medi-Cal plans and four
Covered California issuers, three of which are both.

## Method, and what it can and cannot support

**The denominator came first.** Membership is the DHCS Medi-Cal Managed Care Health Plan
Directory plus the DHCS Medi-Medi plan-by-county table for 2026 plus the Covered California
2026 issuer announcement, all three retrieved 2026-08-07 and recorded in the cohort file with
their URLs. Seven organizations run in both programs and appear once, tagged with both. SCAN
Connections appears in the DHCS table and is scoped out, because DHCS's own footnote describes
it as a Medicare Advantage Special Needs Plan rather than a managed care plan contract.

**This is a review of public documentation, not a probe sweep.** For each organization the
plan's own developer or interoperability documentation was searched for and, where found,
retrieved. Where it named a base URL, that URL was requested. Nothing was registered for,
nothing was authenticated to, and no non-public surface was touched. Registration for API
access would produce better data and destroy the premise, which is that a public surface is
observable by the public.

**`basis` is how far the review went, not what it found.** The cohort file keeps those two
apart on purpose. `portal_reviewed` means the plan's own documentation was retrieved on the
stated date; `not_located` means none could be retrieved at all. The outcome lives in the
`reason` field, because "publishes a base URL that returns 404" and "publishes nothing" are
different findings that one field would flatten. The outcome column in the headline table is a
classification of those committed reasons, assigned on 2026-08-15 with no new retrieval; each
one carries the verbatim clause of the reason that decided it, in the JSON beside this file.

**The reachability observations are one network's, on one day.** The requests behind
"returns 404", "returns 401" and "does not resolve in public DNS" were made once, on
2026-08-07, from one network. This project's own rule is that reaching an endpoint from
somewhere settles that it is up while failing from one place settles nothing, and that rule
applies to this review too: a plan in the "did not resolve or did not answer" row may be
answering someone else. What that row supports is narrower and still worth having, which is
that a documented URL did not answer a good-faith request from outside.

**One state, one moment.** Twenty-seven organizations in California in August 2026. No
percentage here estimates a national population, and payer interoperability documentation
changes without notice.

## Finding 1: seven in ten plans on a public roster have no FHIR endpoint an outsider can check

Nineteen of 27 organizations could not be listed. That is the number the cohort exists to
produce, and it is only meaningful because the denominator was fixed from a public roster
before the search began. A registry built the usual way, by adding whatever turns up, cannot
produce it at all: the plans that publish nothing never enter the file, so the miss rate is
structurally invisible.

The result cuts almost identically across the two programs. Seven of 23 Medi-Cal managed care
plans are listed, and four of 11 Covered California issuers, three of them the same
organizations. Neither program's plans are noticeably more discoverable than the other's.

What the gap costs is specific. For nineteen of these organizations, no outside party can
answer "does this plan's Patient Access API actually work" without first entering a business
relationship with the plan. Not a regulator reading a public dashboard, not a researcher, not
a developer deciding whether to build against it, and not a member. Conformance for these
endpoints is checkable only by people the plan has already approved.

## Finding 2: "publishes nothing" and "publishes a URL that does not answer" are different populations, and the difference is seven plans

Collapsing the nineteen into a single "no public endpoint" number would merge two situations
that call for opposite responses.

Twelve plans published no base URL a visitor without an account could read. Five of them route
developers to a developer portal on a shared vendor platform that renders no base URL to an
unregistered visitor. Two more route to the same partner portal of a single national parent
organization, with the same result. One publishes an interoperability page carrying a heading
for the endpoint list where the list itself is missing from the page. One documents 23 APIs at
FHIR 4.0.1 as relative paths with no root URL anywhere reachable without an account. One
publishes a portal where the Provider Directory can be tried against mock data but no
production base URL is printed. For the last two, no interoperability documentation could be
located at all.

Seven plans did publish a base URL, and it is not usable as published:

- Four published production base URLs on their own pages that did not answer. One plan's host
  does not resolve in public DNS. One returns 404 on the Provider Directory and 401 on Patient
  Access. One returns 404 on both. For one, the live developer page could not be retrieved and
  an archived copy documents a Patient Access host that does not resolve.
- Two published a sandbox base URL that returns 401, with the production entry in the plan's
  own documentation left as an unfilled placeholder. Both are covered by one plan's
  documentation, which is why eighteen sources cover nineteen reviews.
- One published a base URL that answers, and the document it returns cannot be attributed to
  the plan. That case has its own write-up:
  [one URL, three brands](2026-08-15-anthem-multi-tenant-attribution.md).

The distinction matters because the second group is a fixable defect with a named owner and
the first is a publication decision. A plan whose documented endpoint returns 404 has done the
work of publishing and has a broken deployment. A plan whose portal requires an account has a
working deployment and a policy.

## Finding 3: the CapabilityStatement names the plan for five of the eleven endpoints that answered

Attribution is the hard part of a payer registry and it does not get easier once an endpoint
answers. Of the eleven endpoints the eight listed plans account for:

| What the conformance document says about who runs it | endpoints |
|---|---:|
| The `publisher` field names the plan | 5 |
| `publisher` is empty and only `implementation.description` names the plan | 2 |
| `publisher` names the platform vendor, not the plan | 1 |
| The document names no deployment at all | 3 |

For six of eleven, the CapabilityStatement's `publisher` field does not name the plan the
endpoint belongs to. Three name no deployment whatsoever: two are generic HAPI servers on a
vendor-controlled host with `publisher` reading "Not provided", and one is the Da Vinci
PDex Plan-Net implementation guide's own CapabilityStatement served verbatim, publisher
"HL7 Financial Management Working Group", canonical URL pointing at hl7.org.

In every one of those six cases the entry stands on the plan having printed that exact base
URL on its own site, which the registry's verification record says outright rather than
implying. Never on a URL path segment, and never on the vendor's word. That rule is why one
plan in the roster is excluded rather than listed, and why one endpoint on a shared platform
was attributed while another on the same kind of platform was not.

This is the observability limit worth generalizing past California: on a multi-tenant payer
platform, the conformance document is frequently a property of the platform rather than of the
tenant, and an outside observer who trusts it is trusting the wrong document.

## Finding 4: the coverage tracker this project planned needs three populations, not two

[`ROADMAP.md`](../../ROADMAP.md) phase 5 describes a coverage tracker as "which CMS-regulated
payers have a *publicly checkable* endpoint at all, with the 'documented but unreachable' and
'no public URL found' populations counted separately and never merged". That is the right
instinct and this cohort is the first data behind it, but 27 organizations were enough to
produce a population the two-way split has no room for: an endpoint that is documented,
reachable, and answering, whose answer cannot be attributed to the plan.

It belongs with neither of the other two. It is not "no public URL found", because the URL is
published and it works. It is not "documented but unreachable", because it is reached. It is a
third state, and on a platform where one operator runs thirteen brands it is likely to be the
common one rather than the exception.

The cohort file cannot count these populations today. `basis` records review depth, so
splitting the nineteen by `basis` puts a plan publishing a 404 and a plan publishing nothing in
the same bucket of 17, which is the exact merge the roadmap forbids. That is why the
classification for this write-up lives in a JSON file beside it rather than being read out of
the cohort. Promoting it into the cohort schema, as a required field with a closed vocabulary
the loader enforces, is what the coverage tracker will need before it can be built for a
second state.

## Every organization on the roster, as reviewed on 2026-08-07

Listed, with verified endpoints:

| Plan | Programs | Endpoints |
|---|---|---:|
| Inland Empire Health Plan | Medi-Cal, Covered California | 2 |
| Kaiser Permanente | Medi-Cal, Covered California | 1 |
| L.A. Care Health Plan | Medi-Cal, Covered California | 1 |
| Sharp Health Plan | Covered California | 2 |
| Santa Clara Family Health Plan | Medi-Cal | 2 |
| Health Plan of San Mateo | Medi-Cal | 1 |
| Central California Alliance for Health | Medi-Cal | 1 |
| Community Health Group Partnership Plan | Medi-Cal | 1 |

Reviewed and not listed:

| Plan | Programs | Outcome |
|---|---|---|
| Alameda Alliance for Health | Medi-Cal | no public base URL |
| Anthem Blue Cross (Elevance Health) | Medi-Cal, Covered California | answers, unattributable |
| Blue Shield of California (including Promise Health Plan) | Medi-Cal, Covered California | no public base URL |
| CalOptima Health | Medi-Cal | no public base URL |
| CalViva Health | Medi-Cal | no public base URL |
| CenCal Health | Medi-Cal | no public base URL |
| Chinese Community Health Plan (Balance by CCHP) | Covered California | no public base URL |
| Community Health Plan of Imperial Valley | Medi-Cal | documented, unreachable |
| Contra Costa Health Plan | Medi-Cal | documented, unreachable |
| Gold Coast Health Plan | Medi-Cal | no public base URL |
| Health Net Community Solutions | Medi-Cal, Covered California | no public base URL |
| Health Plan of San Joaquin | Medi-Cal | sandbox only |
| Kern Family Health Care | Medi-Cal | documented, unreachable |
| Molina Healthcare of California | Medi-Cal, Covered California | no public base URL |
| Mountain Valley Health Plan | Medi-Cal | sandbox only |
| Partnership HealthPlan of California | Medi-Cal | documented, unreachable |
| San Francisco Health Plan | Medi-Cal | no public base URL |
| Valley Health Plan | Covered California | no public base URL |
| Western Health Advantage | Covered California | no public base URL |

The reason, review method, date and source for each of these nineteen is in
[`data/cohorts/california.json`](../../data/cohorts/california.json) and is rendered on the
[California cohort page](https://chelseakr.github.io/fhir-scorecard/california/).

## Reproducing this

The cohort loads and validates as part of the normal build; a member that carries neither
endpoints nor an exclusion, or both, is rejected rather than published.

Install the toolchain as the README's quick start describes, then:

```sh
.venv/bin/fhir-scorecard grade --registry data/registry.json --out site
```

The tables above are checked against the data on every run of the test suite:

```sh
.venv/bin/python -m pytest tests/test_findings_evidence.py
```

Re-running the underlying review means retrieving nineteen plans' documentation again by hand.
The numbers will move as plans publish, and that is the point of dating them.

## A correction we would welcome

A review that found nothing is not proof that nothing exists. If one of these nineteen plans
publishes a base URL this review missed, the
[claim flow](https://chelseakr.github.io/fhir-scorecard/claim/) exists for exactly that, and
the entry will be corrected with the evidence recorded the same way every other entry is.

## Limits of this review, stated plainly

- Twenty-seven organizations in one state at one moment. No national population estimate
  follows, and no trend does either: this is the first observation, not a series.
- The reachability observations behind four of the exclusions were made once, from one
  network, on 2026-08-07. A failure from one network settles nothing about an endpoint, only
  about that attempt.
- The outcome classification in the headline table was assigned on 2026-08-15 by reading
  reasons written on 2026-08-07. It adds no observation; it makes existing observations
  countable. Each assignment carries the clause it rests on so it can be argued with.
- The review looked at each plan's own developer or interoperability documentation. A plan may
  publish a base URL somewhere this review did not reach, including in a document available
  to its contracted providers.
- "Verified" for the eight listed plans means the base URL was published by the plan and the
  endpoint answered on 2026-08-07. It is not a conformance result. Whether those eleven
  endpoints answer today, and what they score, is on the live cohort page and moves.
- Nothing here evaluates the APIs these plans provide to members and contracted developers
  through their portals. Those may be excellent. They are not observable from outside, which
  is the whole subject.
