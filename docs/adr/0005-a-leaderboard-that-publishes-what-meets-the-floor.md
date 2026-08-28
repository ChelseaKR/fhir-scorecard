# 0005. An availability page that publishes what meets the floor and names what does not

## Status

Accepted - 2026-08-27

## Context

`ROADMAP.md` phase 5 lists an *"availability leaderboard once the 14-observation floor is met
across the registry"*. Two things about that sentence needed settling before it could be built.

**The condition as written never arrives.** The floor is fourteen recorded observations
(`drift.MIN_OBSERVATIONS_TO_REPORT`), and the registry grows: a curation wave adds a state's
issuers, and every one of them starts at zero. Measured on the record as it stands on
2026-08-27, across 45 endpoints and 21 observation dates, **30 endpoints meet the floor and 15
do not**. Waiting for a registry-wide floor means the page publishes on the days between the
last endpoint reaching fourteen observations and the next one being added, which in a project
whose stated growth path is "one roster at a time" is approximately never.

**A leaderboard is a ranking, and this project refuses several kinds of ranking.** The README
says the grades are not "rankings of care quality", `gate.evaluate` records that "grades are
comparable within a kind only", and the site never orders endpoints of different kinds against
each other. An availability table that put an EHR vendor's sandbox above a payer's production
Patient Access API would be the comparison those rules exist to prevent.

## Decision

Publish `/availability/`, and hold it to three rules.

**The floor is applied per endpoint.** An endpoint at or above fourteen observations gets a
share and a position. An endpoint below it gets neither, and is named in its own section with
its observation count and how many more it needs. The below-floor population is published, not
dropped: an endpoint missing from a table reads as an endpoint nobody watched, which is the
opposite of what is true.

**Ordering happens within a kind and never across one.** One table per registry kind, using the
same `KIND_LABELS` every other surface groups by, so this page cannot disagree with the kind
pages about what is comparable with what.

**The page says what a position is.** It orders one measurement: whether a public address
answered a request for its `/metadata` document, from three hosts on one provider's network,
over the recorded window. Not a service level, not an uptime guarantee, not a statement about an
organization. The vantage caveat is on the page rather than in a footnote, because the failure
this project has already made once was publishing an endpoint as dead when a middlebox on the
probing network intercepted TLS.

If no endpoint meets the floor, the page renders an empty state saying nothing is ordered yet.
It does not rank a shorter record more cautiously; it declines to rank it.

## The floor has to be able to exclude something, and it does

A threshold every entry clears is not a threshold, so the exclusion is measured rather than
asserted, on both sides:

- **The live record**, on the `capability-history` branch: **30 of 45 endpoints above the
  floor, 15 below**, measured 2026-08-27 over 21 observation dates. Named on the page with
  their counts.
- **The history committed to this repository**, `data/history.json`, which is the seed the
  first live run started from and is no longer updated: **19 endpoints, two observations each,
  every one of them below the floor.** That seed alone publishes the empty state and orders
  nothing.

`tests/test_leaderboard.py` asserts the second directly against the committed file, so a floor
lowered until it excluded nothing would fail the build rather than quietly publishing a
confident share off two days. Dropping `MIN_OBSERVATIONS_TO_REPORT` to 1 fails seven tests.

## Consequences

The page exists years before a registry-wide floor could ever be met, and its numbers are
honest at every point in between, because what is missing is named on the page beside what is
present.

Position is a property of the table and is not stored on a record. An endpoint that leaves the
table - because it was retired, or because the floor moved - takes no position with it, and
nothing else on the site can inherit one.

The choice to order within a kind means the page cannot answer "which endpoint in this registry
is most available". That question has no defensible answer over a set containing both vendor
sandboxes and production payer APIs, and declining it is the point.
