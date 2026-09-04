# The sampling frame

A hit rate over a set nobody defined is not a rate. This document is the rule that decides which
organizations this project goes looking for, written down before the looking, so that every count
published here has a denominator a reader can check and disagree with.

It exists because the early curation waves did not have one. Waves one through five picked payers
by size, by whoever turned up in a search, or by guessing hostnames from company names, and then
reported "6 of 22 candidates verified" as though 22 were a population. It was not; it was a list of
things that had been tried. `data/CANDIDATES.md` carries the correction in full. The rule below is
what replaced it.

## The rule

**Every cohort's membership comes from a public roster published by somebody other than this
project, retrieved on a stated date, and fixed before any base URL is probed.**

Four consequences, each of which has changed a published number at least once:

1. **The denominator exists first.** The roster is written into `data/cohorts/<id>.json` with its
   source URLs and retrieval dates before probing starts. Nothing may be added to a cohort because
   probing found it, and nothing may be dropped because probing found nothing.
2. **Non-members are out even when they are interesting.** A payer that publishes a beautiful FHIR
   endpoint but is not on the roster does not join the cohort. It can enter `data/registry.json`
   on its own merits, where it is graded and counted, but it never moves a cohort's hit rate.
3. **Members that publish nothing are part of the result.** They are carried as exclusions with a
   reason, a review method, a date, and a source. `src/fhir_scorecard/cohort.py` refuses a member
   that carries neither endpoints nor an exclusion, so "we could not find one" cannot be silently
   the same as "there is not one".
4. **A member that publishes an address that does not work is a different finding from a member
   that publishes nothing**, and is recorded as one. Publishing no URL is permitted by the rule
   these cohorts are drawn against. Publishing one that returns DNS failure, 404, or 401 is a
   defect in the public record, and it is only visible because the roster said to go look.

## What counts as a roster

A roster qualifies if it is **public**, **enumerable**, **attributable to its publisher**, and
**dated**. In practice that means a government agency's own list, a marketplace's own issuer list,
or an equivalent published register. A roster must be retrievable by a reader who has no
relationship with anyone on it.

These do **not** qualify, and each was considered and rejected:

| Not a roster | Why |
|---|---|
| Hostnames guessed from company names | 18 probed, 0 verified. A guess that fails says nothing about the payer |
| A vendor's customer list | Selects on having bought one platform, which is the variable most worth measuring |
| A paywalled industry directory | A reader cannot check the denominator |
| "The large payers" | No published boundary, so the boundary moves to fit the result |
| The set of endpoints this project already found | Circular: the hit rate would be 100% by construction |

A roster is allowed to be small, regional, or partial. It is not allowed to be undefined.

## What the frame does not claim

Membership in a frame is not a compliance obligation and is never published as one. The federal
CMS Interoperability and Patient Access rule (CMS-9115-F) requires impacted payers to operate a
Patient Access API and a Provider Directory API; it does **not** require an organization to print
its base URL where an unregistered visitor can read it. So a member with no discoverable endpoint
is not out of compliance with anything as far as this project is concerned, and the cohort pages
say so in those words. What a missing base URL costs is narrower and worth naming exactly:
conformance stops being checkable by anyone who has not already entered a business relationship
with the plan.

Where a frame is drawn from a program roster, the obligation claim is made per program and no
further. CMS-0057-F's Provider Access, Payer-to-Payer, and Prior Authorization APIs are not in
force until January 2027 and are not graded.

## How an endpoint enters the registry from a frame

`data/registry.json` records the basis alongside the date, because two very different things were
being flattened into one free-text sentence:

- **`live_capability`** - a CapabilityStatement was retrieved and the publisher established, either
  from the document itself or, where the document names a vendor or nobody, from the organization
  publishing that exact base URL in its own materials.
- **`publisher_documented`** - the organization publishes the base URL in its own materials and the
  document was **not** retrievable on the verification date. The entry must carry `source` (where
  the organization published it) and `observed` (what the probe actually saw). It is listed, it is
  graded every day like every other entry, and it publishes as `not observed` rather than `F`,
  because a run that retrieved nothing has nothing to grade.

The second basis is the one worth defending. An endpoint an organization publishes and that does
not answer is a finding about the public record, and a registry that drops it publishes a cohort
pruned of exactly the failures this project exists to detect. It is also the case that
unreachability is frequently a property of the *vantage* rather than the endpoint - see the Capital
Blue Cross incident in `docs/findings/` - so an entry that stays listed keeps getting probed from
every vantage, and can be corrected. An entry that was dropped cannot.

**One entry per organization per surface.** Issuers routinely print several addresses for the same
API: a "base request URL" beside a metadata URL that disagrees with it, a production host beside a
vendor host, an old year's path beside this year's. The registry lists one endpoint per
(organization, surface) pair, and the choice between competing published addresses is made on one
rule and recorded in the entry: **if any published address for that surface answers, the entry is
the one that answers, and the others go to the candidate log.** That is not dropping a failure -
the surface is observable, so the surface is graded - and it keeps the endpoint count from
inflating with an organization's documentation errors. Only when *no* published address for a
surface answers does the surface get a `publisher_documented` entry, and then it names the address
the organization's own documentation leads with.

**A TLS failure is a question, not an answer**, and the question has to be asked before the entry
is written. Re-attempt with certificate verification disabled: a handshake that still fails is the
server refusing, which is a fact about the endpoint; a handshake that succeeds once verification is
off is this vantage's trust store or a middlebox, which is a fact about the prober and must not be
recorded against the endpoint. Both outcomes occurred on 2026-08-19 and are distinguished in
`data/CANDIDATES.md`.

Re-checks are their own dated record (`verification.reverified`), never an overwrite of the
curation date. An entry with no re-check block has not been re-checked, and its page says so in
words. A stale date must not be able to read as a fresh one.

**Known inconsistency, stated rather than tidied away.** The California cohort was curated before
the second basis existed, so its four "documented, unreachable" members - Imperial Valley,
Partnership HealthPlan, Kern Family Health Care, Health Plan of San Joaquin - are carried as
exclusions with that outcome in their `reason`, not as `publisher_documented` registry entries.
They are published either way, which is why this is an inconsistency in form rather than a
suppressed result, but the two cohorts do not treat the same situation the same way. Promoting them
would rewrite a dated published finding and the evidence tests that recompute it, so it is left for
a pass that can do that properly.

## Frames used so far

| Frame | Roster | Retrieved | Status |
|---|---|---|---|
| California payer cohort | DHCS Medi-Cal managed care plan roster + Covered California issuer list, deduplicated to 27 organizations | 2026-08-07 | Published at `/california/` |
| Federal marketplace frame (national) | CMS/CCIIO **QHP Landscape PY2026 Individual Medical**: every issuer selling an individual-market QHP on HealthCare.gov, in every federally-facilitated-exchange state. 30 states, 176 state-issuer organizations, 183 HIOS issuer IDs, 97,082 plan-county rows; the per-issuer roster is committed at `data/frames/qhp-landscape-py2026-individual-medical.csv` | 2026-08-19 | Reviewed a state at a time. Texas (15 organizations) at `/texas-marketplace/`, Florida (15 organizations) at `/florida-marketplace/`, Ohio (11 organizations) at `/ohio-marketplace/`, Wisconsin (12 organizations) at `/wisconsin-marketplace/`; the other 26 states' 123 state-issuer organizations are **not yet reviewed**, which is a statement about this project's progress and never about what those issuers publish |
| National and reference surfaces | Not a frame. EHR vendor sandboxes, reference servers, and one federal API are listed individually for calibration, are graded only within their own kind, and are never counted in a cohort rate | - | `data/registry.json` |

A cohort is added by writing its roster and sources into `data/cohorts/`, not by adding endpoints
and describing them afterwards.

### The federal-exchange frame is national; the review proceeds a state at a time

The QHP Landscape file is not a Texas file or a Florida file: it enumerates the whole
federally-facilitated marketplace, which makes it the payer-side analogue of the rosters ONC's
Lantern ingests on the provider side - a denominator somebody else publishes, at a scale nobody
could assemble by hand. The whole national roster is committed under `data/frames/` so the
denominator exists first, per the rule at the top of this document.

What does not scale mechanically is the review. Each state-issuer's documentation has to be
found and read by a person, which is why cohorts are published per state as they are completed
rather than all at once. Until a state is reviewed, its issuers are carried as **not yet
reviewed** - a fact about this project, never rendered as "publishes nothing", which is a fact
about an issuer that only a completed review can establish. The two must never be merged, and
the frame's coverage arithmetic (4 of 30 states, 53 of 176 state-issuer organizations reviewed)
is recomputed from the committed roster by `tests/test_plan_evidence.py` rather than maintained
by hand.

### Why the Florida frame is drawn the way it is

Florida is the same prong as Texas - 45 CFR 156.221 reaching QHP issuers on the
federally-facilitated exchanges - and it is the largest HealthCare.gov market in the country,
which makes it the highest-coverage state the frame can add. Filtered to `State Code = FL`, the
same file carries 7,569 plan-county rows, 16 HIOS issuer IDs, and 15 issuer names; the
per-issuer rows are committed at `data/cohorts/florida-marketplace.roster.csv`. The unit is the
issuer name CMS prints, so the denominator is 15: one name (Ambetter Health) carries two HIOS
IDs.

One choice is new here and is written down because it moves a number a reader might compute
differently: CMS names corporate siblings separately - Florida Blue and Florida Blue HMO,
Cigna Healthcare and Cigna HealthCare of Florida, Inc. - and the cohort keeps them separate,
because collapsing them would substitute this project's corporate-structure judgment for the
regulator's enumeration. Each pair shares its family's published endpoints; a reader who
collapses the pairs gets 13 organizations the other way, and the cohort page says so.

### Why the Texas frame is drawn the way it is

The California frame is a state's own roster of a state program plus a **state-based** exchange's
issuer list. Its federal hook is therefore the Medicaid managed care prong of CMS-9115-F, because
the QHP prong (45 CFR 156.221) reaches issuers on the **federally-facilitated** exchanges and
Covered California is not one.

Texas is. So the second frame is the other prong, drawn from the regulator's own file rather than
from any state page:

> **Every issuer offering an individual-market qualified health plan through HealthCare.gov in
> Texas for plan year 2026, as enumerated by CMS/CCIIO's QHP Landscape PY2026 Individual Medical
> dataset** (`https://data.healthcare.gov/dataset/6fe7fb77-7291-4104-952f-7c7e2c5d0c45`, file
> `individual_market_medical.zip`, issued 2026-08-04, modified 2026-08-10).

Three choices in that sentence, each of which moves the denominator and so is written down:

- **The unit is the issuer organization CMS names, not the HIOS ID.** Texas has 18 HIOS issuer IDs
  and 15 issuer names for PY2026; three names carry two IDs each. Counting IDs would count one
  organization twice for having two product lines. 15 is the denominator; the 18 is recorded here
  so a reader can recompute the other way.
- **Individual medical only.** The file excludes stand-alone dental, small group, and every
  off-exchange product. An issuer selling only off-exchange in Texas is out of frame.
- **Frozen at retrieval.** The roster was fixed on 2026-08-19 and does not move when the market
  does.

Texas's Medicaid and CHIP managed care organizations were considered for the same frame and left
out, on a documentation ground rather than a substantive one: `hhs.texas.gov` returns 403 to
automated retrieval, and the most current roster document that could be retrieved is a January
2026 provider-relations contact list whose program tags its own pages contradict. A frame has to
be reproducible by a reader, and that one is not from here. It is a good candidate for a later
pass by someone with a browser.
