# Contributing

## Adding an endpoint to the registry

Endpoints ship only after live verification. The loader refuses entries without a verification
record, so this policy is enforced in code, not by review vigilance.

1. Fetch `[base]/metadata` yourself and confirm it returns a FHIR CapabilityStatement.
2. Confirm the publisher matches the organization the entry claims (software name,
   implementation description, or the URL's ownership). Never derive a base URL from an
   organization's name or acronym; resolvers that guess produce plausible, wrong endpoints.
3. Record the verification method and date in the entry:

```json
{
  "id": "example-payer",
  "name": "Example Payer",
  "kind": "payer",
  "base_url": "https://fhir.example.com/r4",
  "verification": {
    "method": "live CapabilityStatement fetch; publisher confirmed via implementation.description",
    "date": "2026-08-04"
  }
}
```

4. Run `make verify` and a live grade before opening a PR.

### When the CapabilityStatement names a vendor, or nobody

Vendor-hosted multi-tenant payer platforms are where step 2 earns its keep. The document usually
describes the platform rather than the tenant, and sometimes names no one at all. Three rules, in
order:

- **Never attribute on a URL path segment.** `.../lac/fhir/pd/R4` is not evidence about L.A. Care.
- If the **plan's own site** publishes the base URL, the plan has put its name behind that address
  and the entry may be attributed to the plan. Say so in the verification record, including the part
  that the conformance document does not.
- If only the **vendor** connects the server to the plan, list it under the vendor when the document
  names one, and otherwise do not list it at all.

`implementation.url` is not a reliable tenant identifier. One platform returned three different
brand names across three consecutive fetches of a fixed URL.

## Adding a cohort

A cohort (`data/cohorts/<id>.json`) is a named view over the registry. Its membership must come from
a **public roster** you cite in `sources`, not from what you happened to find, because a hit rate
over an undefined set is not a rate.

Every member carries either `endpoints` (ids that must already exist in `data/registry.json`) or an
`excluded` record, never both and never neither; the loader enforces that. An exclusion needs a
`reason`, a `basis`, and a `reviewed` block with `method`, `date`, and `source`:

```json
{
  "id": "example-plan",
  "name": "Example Plan",
  "programs": ["medi-cal"],
  "excluded": {
    "reason": "developer portal requires registration to view the base URL",
    "basis": "portal_reviewed",
    "reviewed": {
      "method": "retrieved the plan's interoperability page; no base URL is rendered without an account",
      "date": "2026-08-07",
      "source": "https://example.test/interoperability"
    }
  }
}
```

`basis` says how far the review went, not what it found: `portal_reviewed` means you retrieved the
organization's own documentation, `not_located` means you could not retrieve any. The outcome goes
in `reason`, because "publishes a base URL that returns 404" and "publishes nothing" are different
findings and one field would flatten them.

## Ground rules

- Public discovery surfaces only: `/metadata` and `/.well-known/smart-configuration`. No
  authenticated requests, no patient data, ever.
- One request per resource per run. Keep the fetcher polite.
- Grading changes need a finding code, a spec citation, and tests in the same commit.
- `make verify` (ruff, mypy strict, pytest with the coverage floor) gates every merge.
