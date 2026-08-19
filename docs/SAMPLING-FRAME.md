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

Re-checks are their own dated record (`verification.reverified`), never an overwrite of the
curation date. An entry with no re-check block has not been re-checked, and its page says so in
words. A stale date must not be able to read as a fresh one.

## Frames used so far

| Frame | Roster | Retrieved | Status |
|---|---|---|---|
| California payer cohort | DHCS Medi-Cal managed care plan roster + Covered California issuer list, deduplicated | 2026-08-07 | Published at `/california/` |
| National and reference surfaces | Not a frame. EHR vendor sandboxes, reference servers, and one federal API are listed individually for calibration, are graded only within their own kind, and are never counted in a cohort rate | - | `data/registry.json` |

A cohort is added by writing its roster and sources into `data/cohorts/`, not by adding endpoints
and describing them afterwards.
