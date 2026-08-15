# Security and responsible use

## What this project does to endpoints

It issues at most two unauthenticated GET requests per endpoint per probing run: `/metadata` and
`/.well-known/smart-configuration`. Both are public discovery documents that FHIR servers are
expected to expose. Requests carry an identifying User-Agent with a contact address.

It never authenticates, never registers for API access, never requests patient data, and never
probes beyond those two paths. Rate is one probing run per day per vantage, from three vantages,
so at most six requests per endpoint per scheduled day; the run that publishes the site makes no
requests of its own. Publishing is triggered on a schedule and by hand, not by commits.

## If you would rather not be listed

Open a [dispute issue](https://github.com/ChelseaKR/fhir-scorecard/issues/new?template=remove-or-dispute.yml)
and the entry will be corrected or removed. You do not need to prove anything first. This
project measures public surfaces and has no interest in an adversarial relationship with the
organizations it observes.

## Reporting a vulnerability in this project

Email ckellyreif@gmail.com. Please do not open a public issue for a security defect in the
tooling itself.

## Known limits

Grades are observational snapshots of public surfaces, taken from three GitHub-hosted runner
images that share one provider's network rather than from independent networks.
They are not audits, not compliance determinations, and not statements about care quality. The
project has published and corrected several of its own measurement errors; see
[docs/payer-verifiability.md](docs/payer-verifiability.md).
