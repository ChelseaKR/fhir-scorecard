# Candidate log

Every probed base URL, verified or not, so curation work is visible and nobody re-probes a
known-dead guess. One `/metadata` request per candidate per probe date.

## 2026-08-04

| Candidate | Base URL | Outcome |
|---|---|---|
| CMS Blue Button 2.0 | api.bluebutton.cms.gov/v2/fhir | **Verified** → registry |
| CMS Blue Button 2.0 sandbox | sandbox.bluebutton.cms.gov/v2/fhir | Verified but redundant with production (same software); not listed |
| Humana | fhir.humana.com/api | **Verified** → registry |
| Humana sandbox | fhir.humana.com/sandbox/api | Rejected: HTTP 403 |
| Aetna public demo | vteapif1.aetna.com/fhirdemo/v1/patientaccess | **Verified** → registry (demo instance, labeled as such) |
| Cigna Patient Access | fhir.cigna.com/PatientAccess/v1 | **Verified** → registry |
| Cigna alt path | fhir.cigna.com/r4 | Rejected: HTTP 403 |
| Optum/UHC public (guess) | public.fhir.flex.optum.com/R4 | Rejected: URLError (name did not resolve) |
| Molina (guess) | fhir.molinahealthcare.com/fhir/r4 | Rejected: URLError |
| Centene (guess) | api.centene.com/fhir/r4 | Rejected: HTTP 404 |
| ONC Inferno reference | inferno.healthit.gov/reference-server/r4 | **Verified** → registry (reference) |
| Firely public | server.fire.ly | **Verified** → registry (reference) |

## 2026-08-05

Second wave. Candidates sourced from public payer developer-portal documentation rather than
guessed from company names, which improved the hit rate but still failed most of the time. That
ratio is the point of keeping this log: published base URLs go stale, sit behind gateways that
refuse unauthenticated discovery, or were never public.

| Candidate | Base URL | Outcome |
|---|---|---|
| BCBS Minnesota | preview-api.bluecrossmn.com/fhir | **Verified** → registry (preview environment, labeled) |
| HealthPartners | api-developerportal.healthpartners.com/interop/external/fhir | **Verified** → registry |
| Capital Blue Cross | patientaccess-api.capbluecross.com/r4 | Rejected: DNS did not resolve |
| Capital Blue Cross demo | patientaccess-api-demo.capbluecross.com/r4 | Rejected: DNS did not resolve |
| MVP Health Care | patientaccess.mvphealthcare.com/fhir/r4 | Rejected: DNS did not resolve |
| Anthem/Elevance | fhir.anthem.com/api/v1 | Rejected: DNS did not resolve |
| Kaiser Permanente | healthy.kaiserpermanente.org/fhir/r4 | Rejected: HTTP 410 Gone |
| UnitedHealthcare | public-fhir.uhc.com/R4 | Rejected: DNS did not resolve |
| Centene | production.api.centene.com/fhir/patientaccess/r4 | Rejected: DNS did not resolve |
| Health Alliance Plan | api.hap.org/fhir/r4 | Rejected: HTTP 404 |

Standing conclusion after two waves: 6 of 22 probed candidates expose an unauthenticated
CapabilityStatement. Many plans gate `/metadata` behind registration, which is permitted but
makes their conformance publicly unverifiable. That is itself a finding worth stating plainly
rather than a gap in this dataset.

## 2026-08-05 (third wave)

Widened past payers to the surfaces that anchor comparison: EHR vendor sandboxes and a federal
provider API. These are recorded under distinct `kind` values because a payer Patient Access API
and an EHR sandbox answer to different implementation guides; the report groups by kind and never
ranks across them.

| Candidate | Base URL | Outcome |
|---|---|---|
| VA Lighthouse (production) | api.va.gov/services/fhir/v0/r4 | **Verified** → registry (`provider`) |
| VA Lighthouse (sandbox) | sandbox-api.va.gov/services/fhir/v0/r4 | Verified but redundant with production (same software); not listed |
| Epic on FHIR sandbox | fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4 | **Verified** → registry (`ehr`) |
| Oracle Health (Cerner) open | fhir-open.cerner.com/r4/ec2458f2-… | **Verified** → registry (`ehr`) |
| Medplum public API | api.medplum.com/fhir/R4 | **Verified** → registry (`ehr`) |
| Acentra Health sandbox | sandbox.mhbapp.com/fhir/r4 | Rejected: HTTP 403 |
| Acentra alt host | api.mhbapp.com/fhir/r4 | Rejected: DNS did not resolve |
| Elevance TotalView | totalview.healthos.elevancehealth.com/fhir | Rejected: answered HTML, not a CapabilityStatement |
| Elevance TotalView /r4 | totalview.healthos.elevancehealth.com/fhir/r4 | Rejected: answered HTML, not a CapabilityStatement |
| Logica Health sandbox | api.logicahealth.org/fhirserver/open | Rejected: DNS did not resolve |

## 2026-08-05 (fourth wave) and a correction to the headline statistic

Probed ten Medicaid managed-care and regional plans using the URL pattern many payers follow
(`patientaccess.<company>.com/fhir/r4`), plus re-probes of two earlier failures. **All twelve
failed**, ten of them because the hostname does not resolve.

| Candidate | Outcome |
|---|---|
| Healthfirst NY, Molina, EmblemHealth, HCSC/BCBSIL, Wellcare, CareFirst, Premera, BCBS MA, Highmark, Point32Health | Rejected: DNS did not resolve (pattern guesses) |
| Kaiser Permanente (re-probe) | Rejected: HTTP 410 Gone, unchanged since 2026-08-05 wave 2 |
| Centene (re-probe) | Rejected: DNS did not resolve, unchanged |

**Correction.** Earlier waves reported "6 of 22 payer candidates verified." That number
conflated two different populations: endpoints whose base URL was **documented** on a public
developer portal, and endpoints whose URL I **guessed** from a naming pattern. A guessed hostname
that does not resolve says nothing about the payer; it says the guess was wrong. Reporting them
together inflates a claim about industry practice using my own failed guesses as evidence.

Corrected counts, payers only:

| Population | Probed | Verified |
|---|---|---|
| Base URL documented on a public developer portal | 10 | **6** |
| Base URL guessed from a naming pattern | 18 | **0** |

The defensible claim is the first row, and even that is small-N. The second row is a fact about
guessing, not about payers. The zero is still informative in one narrow sense: payer FHIR base
URLs are not predictable from company names, so any registry of them has to be curated from
documentation rather than generated.

## 2026-08-05 (fifth wave)

Targeted Provider Directory APIs, which sit under the same CMS interoperability rules but are
required to be reachable **without** authentication, plus additional reference servers.

| Candidate | Outcome |
|---|---|
| Cigna Provider Directory | **Verified** → registry (new kind `payer_provider_directory`) |
| Humana Provider Directory | Rejected: HTTP 400 |
| CMS MA Provider Directory, UnitedHealthcare, Centene, BCBS FEP directories | Rejected: DNS did not resolve |
| HAPI R5, Firely R5, SMART Health IT STU3 | **Verified but deliberately not listed** (see below) |
| CMS Blue Button sandbox | Verified but redundant with production; not listed |

**Three verified servers were held back, then admitted.** HAPI R5 (5.0.0), Firely R5 (5.0.0),
and SMART Health IT STU3 (3.0.1) all answered correctly, but transparency awarded points for
declaring R4, so grading them would have measured the wrong thing. Rather than add them for the
count, grading was made version-aware first: each entry declares `expects` and is checked against
that release. All three are now in the registry, graded on their own terms.

**Calibration found by this wave.** Provider Directory APIs must be publicly reachable, so the
SMART-discovery and OAuth findings would have penalized Cigna's directory for correctly having
no authorization surface. Those two findings are now reported as *not applicable* for that kind
and carry no points in either direction. A regression test pins it.

Next: source base URLs from CMS's own payer API listings and individual plan developer portals
rather than patterns; re-probe rejected payers quarterly, since a 404 today may be live later;
make grading FHIR-version-aware so R5 and STU3 servers can be graded on their own terms.

## 2026-08-07 (eighth wave): the California cohort

A different sourcing strategy from every wave before it. Earlier waves picked payers by size or by
whoever turned up, which makes the resulting counts uninterpretable: a hit rate over a set nobody
defined is not a rate. This wave started from two **public rosters** instead, so the denominator
exists before any probing does. DHCS publishes the Medi-Cal managed care plans by county, and
Covered California publishes its qualified health plan issuers. Deduplicated, and with the one
Medicare Advantage D-SNP that DHCS lists alongside the MCPs scoped out, that is **27
organizations**, seven of which run in both programs.

These endpoints are required by the federal CMS Interoperability and Patient Access rule
(CMS-9115-F). That is the only obligation claimed anywhere in this work: California's Data
Exchange Framework runs through the DSA and QHIOs and requires none of these surfaces, and
CMS-0057-F's Provider Access, Payer-to-Payer, and Prior Authorization APIs are not required until
January 2027 and are not graded.

**Eight of the 27 publish a base URL that answers. Eleven endpoints entered the registry.**

| Candidate | Base URL | Outcome |
|---|---|---|
| Inland Empire Health Plan Patient Access | fhir.iehp.org/fhir-request | **Verified** → registry (publisher `IEHP`) |
| Inland Empire Health Plan Provider Directory | fhir.iehp.org/provider-directory | **Verified** → registry |
| Sharp Health Plan Patient Access | shp-apis.sharphealthplan.com/patientaccess | **Verified** → registry (publisher `Sharp Health Plan`) |
| Sharp Health Plan Provider Directory | shp-apis.sharphealthplan.com/shpconformance/api | **Verified** → registry |
| Kaiser Permanente health plan | kpx-service-bus.kp.org/…/healthplankpxv1rc/FHIR/api | **Verified** → registry (publisher `Kaiser Permanente`) |
| Santa Clara Family Health Plan Patient Access | fhir.scfhp.com/baseR4 | **Verified** → registry |
| Santa Clara Family Health Plan Provider Directory | fhir.scfhp.com/providerAPI | **Verified** → registry |
| Health Plan of San Mateo | api.hpsmfhir.com/r4 | **Verified** → registry (vendor-attributed, see below) |
| L.A. Care Provider Directory | us107.ir4.edifecscloud.com/lac/fhir/pd/R4 | **Verified** → registry (anonymous document, see below) |
| Central California Alliance Provider Directory | us120.fhir.edifecsfedcloud.com/ccah/fhir/pd/R4 | **Verified** → registry (anonymous document) |
| Community Health Group Provider Directory | api-chgsd-prd.safhir.io/v1/api/provider-directory | **Verified** → registry (anonymous document) |
| Community Health Plan of Imperial Valley | production.api.centene.com/fhir/{patientaccess,providerdirectory} | Rejected: DNS did not resolve |
| Partnership HealthPlan | us120.fhir.edifecsfedcloud.com/php_pdfhir, /php_fhir | Rejected: 404 and 401 |
| Kern Family Health Care | fastplusapi.khs-net.com:8080/{patient/paa,provider/pda}/r4 | Rejected: HTTP 404 |
| Health Plan of San Joaquin | us120.fhir.edifecsfedcloud.com/hpsj_fhir | Rejected: HTTP 401 (sandbox; production is a placeholder in the plan's own PDF) |
| Anthem Blue Cross (Elevance) | totalview.healthos.elevancehealth.com/…/AnthemBlueCross/… | Answered; **deliberately not listed** (see below) |
| Contra Costa Health Plan | icproxy.mycclink.org/…, plus an mPulse directory | Rejected: DNS did not resolve; directory unattributable |
| Blue Shield of CA, Health Net, CalViva, Molina, Alameda Alliance, CalOptima, SFHP, CenCal, Gold Coast, CCHP, Mountain Valley | none published | No base URL published to an unregistered visitor |
| Valley Health Plan, Western Health Advantage | none published | No developer documentation located at all |

### The attribution problem, in its sharpest form yet

The seventh wave found that a vendor-hosted multi-tenant directory describes the platform rather
than the tenant, and that `implementation.url` was the one field distinguishing two plans'
otherwise byte-identical documents. This wave found the next step down.

**Three of the eleven listed endpoints answer with a document that names nobody.** L.A. Care's and
the Alliance's directories both report `publisher: Not provided` and a generic `HAPI FHIR Server`
with an Edifecs build suffix, on an Edifecs-controlled host. Community Health Group's directory is
stranger still: it serves the Da Vinci PDex Plan-Net implementation guide's *own* CapabilityStatement
verbatim, `publisher: HL7 Financial Management Working Group`, canonical URL on hl7.org. Nothing in
any of the three is about the plan.

They are listed anyway, and the reason is a distinction the seventh wave did not have to draw.
Premera's directory was held out and then listed under Opala because only the **vendor's**
documentation connected the server to the plan. Here the **plan's own site** prints the base URL:
L.A. Care publishes it as its "PROD Base/Endpoint URL", the Alliance's own API documentation uses
resource URLs under it, Community Health Group publishes the capability-statement URL directly.
That is the plan putting its name behind an address, which is a claim by the organization the entry
names, and it is not the thing the rule forbids. The rule forbids attributing on a URL path
segment, and `lac`, `ccah` and `chgsd` are exactly the evidence not being used. Each entry's
verification record says outright that the conformance document does not name the plan.

### One endpoint answered and was held out, and the reason is new

Elevance publishes one production base URL per brand and gives "Anthem Blue Cross" its own row.
That URL answers with `publisher: Elevance Health, Inc`. It was fetched three times:

| Fetch | `implementation.url` brand |
|---|---|
| 1 | `AnthemBlueCrossBlueShield` |
| 2 | `AnthemBlueCross` |
| 3 | `Wellpoint` |

Same URL, three brands, documents otherwise identical apart from `id` and `date`. The seventh wave's
finding was that `implementation.url` is the only field that can distinguish tenants on a
multi-tenant platform. The finding here is worse: **on this platform that field is not stable across
requests to a fixed URL**, so it distinguishes nothing at all. A registry built by fetching
`/metadata` and reading the document would have attributed this endpoint to whichever brand the
server happened to name that second. It is recorded and not listed.

### What the roster-first method buys

The eight-of-27 figure means something the earlier hit rates could not, because the 27 was fixed by
DHCS and Covered California before any URL was probed. Two further things are worth stating plainly.

**The pattern is a vendor pattern, not a plan pattern.** Seven of the nineteen unlisted plans are on
one vendor's portal, which renders no endpoint content to an unregistered visitor. Three more route
to one corporate host that does not resolve. Whether a California resident can independently check
their plan's API turns mostly on which platform the plan bought, not on the plan's size or
diligence.

**Four plans publish an address that does not work**, which is a different and more specific finding
than publishing nothing: Imperial Valley, Partnership, Kern, and San Joaquin all print base URLs in
their own materials that return DNS failure, 404, or 401. Publishing no URL is permitted. Publishing
one that 404s is a defect in the public record, and it is only visible because the roster said to go
look.

One methodological note against ourselves. The first Kern probe failed at TLS, which reads exactly
like the Capital Blue Cross incident of 2026-08-05 and was nearly logged that way. It was not the
endpoint: this vantage's Python trust store lacks the Sectigo root Kern's server chains to. Re-probed
with a client that has it, the endpoint returns a plain 404. The lesson from that incident held up
the second time, in the opposite direction: a TLS error is a question, not an answer.

## 2026-08-19 (ninth wave): a re-verification pass, and a cohort drawn from a federal roster

Two different kinds of work on one date.

### Re-verification of the whole registry

The shipped verification dates ran from 2026-08-04 to 2026-08-07, so curation was two weeks old
while availability was being re-measured daily and published beside it. Every listed endpoint was
re-fetched once from the davis-ca residential vantage. **Twenty-nine of thirty answered** and now
carry a dated `verification.reverified` record naming what the document said today.

**One did not, and carries no re-verification record at all rather than a borrowed date.**
`capital-bluecross` failed TLS certificate verification from this vantage ("self-signed certificate
in certificate chain"), which is the same vantage-local interception that made this endpoint look
dead on 2026-08-05 and is not evidence about the endpoint. Its entry still says 2026-08-05, and its
page now says in words that it has not been re-checked since.

Two declarations had moved and are recorded rather than smoothed: Epic's sandbox from `Epic May
2026` to `Epic August 2026` (59 to 60 resource types), and HealthPartners' Azure backend from
`4.0.817` to `5.0.23`.

The quarterly re-probe of `data/rejected.json` ran the same day: **15 candidates, 0 now answer**,
every outcome unchanged in character since it was first recorded.

### The Texas marketplace cohort

The rule this wave followed is now written down in [`docs/SAMPLING-FRAME.md`](../docs/SAMPLING-FRAME.md).
The California cohort's roster is a state's own, and its federal hook is the Medicaid managed care
prong of CMS-9115-F, because Covered California is a state-based exchange while the
qualified-health-plan prong at 45 CFR 156.221 reaches issuers on the *federally-facilitated*
exchanges. Texas is one. So this wave takes the other prong, from the regulator's file rather than
any state page: **CMS/CCIIO's QHP Landscape PY2026 Individual Medical dataset, filtered to Texas.**
13,013 plan-county rows, 18 HIOS issuer IDs, **15 issuer organizations**, frozen 2026-08-19 before
any URL was probed. Texas HHSC's Medicaid managed care roster was considered for the same frame and
left out on a documentation ground: `hhs.texas.gov` returns 403 to automated retrieval, and a frame
a reader cannot reproduce is not a frame.

**Six of the 15 publish a base URL this project could verify from their own documentation. Nine
endpoints entered the registry, five of which answer.**

| Issuer | Outcome |
|---|---|
| UnitedHealthcare | **Verified** → registry: `flex.optum.com/fhirpublic/R4`, publisher `Optum`, 9 resource types |
| Wellpoint (Elevance) | **Listed, documented-unreachable**: the per-brand Patient Access base URL its own support document prints answers 401 |
| Wellpoint (Elevance) | **Verified** → registry: the shared Provider Directory base URL, publisher `Elevance Health, Inc`, listed under Elevance rather than a brand |
| Imperial Insurance Companies | **Verified** → registry: `members.imperialhealthplan.com/api/fhir`, and **listed, documented-unreachable**: `api.imperialhealthplan.com/fhir` from its own PDF refuses the TLS handshake |
| Blue Cross and Blue Shield of Texas (HCSC) | **Listed, documented-unreachable**: `api.hcsc.net`, printed as the Provider Directory "API endpoint base url", answers HTTP 500 at `/metadata` |
| Cigna Healthcare | Already listed from waves 1 and 5; re-fetched today. **Corrected**: the Provider Directory entry stood on `p-hi2.digitaledge.cigna.com/ProviderDirectory/v1`, which still answers, but Cigna's portal no longer prints it anywhere and its implementation guide prints `fhir.cigna.com/ProviderDirectory/v1` instead. Both were fetched today, both answered, and the entry moved to the address the organization publishes |
| Ambetter from Superior HealthPlan (Centene) | No base URL: both APIs deferred to a partner portal that shows an unregistered visitor a 1,128-byte shell |
| Baylor Scott & White Health Plan | No base URL: defers to its vendor's portal, and never mentions a Provider Directory API |
| CHRISTUS Health Plan | **Listed, documented-unreachable**: `christushealthplan.healthsparq.com/api/provider-fhir-service`, printed under the heading "Base FHIR Provider Directory URL", answers HTTP 404 at `/metadata`. Its Patient Access section names a different vendor and prints no address |
| Community First Health Plans | No base URL: names its vendor and the IGs, links only to vendor documentation |
| Community Health Choice | No base URL: its own portal calls the Provider Directory API "publicly available" and offers an unregistered visitor only Sign in and Sign up |
| Moda Health Plan | No base URL: prints one address, its vendor's developer portal |
| Molina Healthcare | No base URL: its own portal serves an anonymous visitor four **sandbox** APIs with relative paths, no host, and no production API at all |
| Oscar Insurance Company | No base URL: delegates to a vendor account; no Provider Directory page exists on its site |
| Sendero Health Plans | No base URL: its "learn more about API" link goes to a vendor **QA** host that answers 403, and credentials arrive by email after a compliance review |
| Harbor Health | **No documentation of any kind**: its complete sitemap contains no api, fhir, interop or developer URL; `/interoperability` and `/developers` both 404 |

### What the roster bought this time

**Three of the fifteen publish a Patient Access API base URL, and one of the three answers.**
Cigna's does; Wellpoint's returns 401 and Imperial's refuses the TLS handshake. The other twelve
route Patient Access through a registration gate and print no address, which the rule permits: it
requires the API, not a public address for it.

**The Provider Directory API is the surface where that permission does not apply**, since it is
required to be reachable *without* authentication. Six issuers publish a directory base URL and
**four of the six answer** (Cigna, UnitedHealthcare, Elevance, Imperial). Two do not: HCSC returns
HTTP 500, and CHRISTUS returns 404 behind a "public token" exchange its own page documents. Of the
nine issuers that publish no address at all, two describe their directory API in their own words as
public while printing nowhere to reach it.

**The vendor pattern from California repeated at national scale.** Eight of the nine unlisted
issuers hand developers to a portal - Centene's, Inovalon's twice, Edifecs', Azure API Management
twice, 1upHealth's and HealthTrio's - and none of those portals prints an endpoint to an
unregistered visitor. The ninth publishes nothing at all.

**Four of the nine listed endpoints are corporate, not brand- or state-level.** HCSC publishes one
directory address for five states' Blue Cross plans; Elevance publishes one for thirteen brands;
Cigna's two are corporate. For those issuers there is no Texas-specific address to grade, which is
a fact about the surface rather than a gap in this curation.

### A methodological note, in both directions

This vantage's TLS behaviour is host-dependent and was checked rather than assumed, because a TLS
error is a question and not an answer. `capital-bluecross` and `bcbs.com` fail certificate
verification here and succeed with verification disabled: that is this network, and nothing about
those endpoints was recorded from it. `api.imperialhealthplan.com` fails the handshake *with
verification disabled too*, which is the server declining, and is recorded against the endpoint.
`api.hcsc.net` negotiates TLS 1.3 cleanly both ways and then returns 500 at `/metadata`.

Also recorded against ourselves: `https://flex.optum.com/fhirpublic`, which UnitedHealthcare's page
labels "Base request URL", answers 403, and `https://flex.optum.com/fhirpublic2025` answers 502.
The metadata URL the same page prints, one path segment deeper, is the one that works. Had this
wave taken the string labelled "base" and reported the 403, it would have published a false
statement about a live and conformant endpoint.

### A published finding changed under us

The 2026-08-15 "one URL, three brands" write-up rests on three fetches of an Elevance per-brand
Patient Access URL on 2026-08-07. Re-probed once today, that URL and the Wellpoint one beside it
both answer **401**: the per-brand paths are now authenticated and the rotating `implementation.url`
is no longer observable from outside. The write-up carries a dated re-check section saying so; the
original observation stands as what was seen on the day it was seen.
