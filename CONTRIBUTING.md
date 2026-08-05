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

## Ground rules

- Public discovery surfaces only: `/metadata` and `/.well-known/smart-configuration`. No
  authenticated requests, no patient data, ever.
- One request per resource per run. Keep the fetcher polite.
- Grading changes need a finding code, a spec citation, and tests in the same commit.
- `make verify` (ruff, mypy strict, pytest with the coverage floor) gates every merge.
