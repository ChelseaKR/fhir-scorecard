# Contributing

Participation in this project is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

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

### If the claim arrived through the issue form

`fhir-scorecard claim <issue-body.md>` does the mechanical half of steps 1 and 2 for you: it
refuses an address this project must not request before making any request, retrieves
`[base]/metadata` and the SMART document, compares the publisher against the submitted
organization by the same rule `reverify` uses, and writes a proposal file and a comment. It
writes nothing to `data/registry.json` and opens no pull request.

Step 2 is still yours, and one part of it is not automated at all: **the verb does not retrieve
the submitted documentation page.** That page is how the publisher is established from the
organization's own materials, and reading it means requesting something that is not one of the
two discovery documents — an open decision on
[#118](https://github.com/ChelseaKR/fhir-scorecard/issues/118). The proposal says so in those
words, so a proposal is never evidence that the publisher was confirmed.

A claim whose CapabilityStatement was not retrieved proposes nothing, because the
`publisher_documented` basis rests on exactly the page the verb did not read.

### Re-checking an entry that is already listed

`fhir-scorecard reverify --older-than 90d` retrieves each selected entry's CapabilityStatement
and writes a proposal file. It never edits `data/registry.json`: set `"accepted": true` on the
rows whose attribution you have confirmed by the rules below, then `reverify --apply <file>`.

Step 2 is still yours. The verb reports whether the document repeats the entry's name, which is
a fact about a string, not a confirmation of attribution. An `unconfirmed` row is the ordinary
outcome for a vendor-hosted platform and is not a reason to remove an entry. A row whose
document was not observed proposes nothing and cannot be applied, so an endpoint that stopped
answering keeps its old re-check date rather than gaining today's.

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
