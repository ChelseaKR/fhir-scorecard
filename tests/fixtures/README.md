# Captured discovery documents

These are **real `/metadata` and `.well-known/smart-configuration` documents**, and real
refusals, captured from live endpoints — not hand-written examples. They exist so that `--offline`
has data behind it, so the Quick start command in the README does what it says, and so the parser
is exercised against documents real servers actually publish rather than only against the
synthetic ones in `conftest.py`.

A capture is one of two things, never both:

- **`metadata.json`** (and `smart.json` where the server serves one) — the documents that were
  retrieved.
- **`refusal.json`** — what stopped the retrieval, for an endpoint no vantage reached. It carries
  `status` (`null` when no HTTP response was produced), `error` (the sentence, verbatim) and
  `failure_kind` from the closed vocabulary in `fetch.py`.

**Both kinds are required.** A fixture set where everything answers cannot exercise the
unreachable path at all, and that is not hypothetical: under a set of three all-reachable
fixtures, `grade_reachability` published `reachability_score: 0` — a measured zero beside a named
health insurer — on 14 live endpoints, and the one test in the suite that reads a published card
could not see it (#135, #137). `tests/test_failure_kinds.py::test_the_offline_fixtures_cover_both_populations`
fails if either population disappears.

They are a **snapshot with a date on it, not a live observation.** Nothing built from them
describes any endpoint today, and an offline run never touches `data/history.json` (see
`--offline` in `cli.py`, which resolves the history path under `.cache/` unless you name one
explicitly, and refuses outright to write fixture observations into a history file a live run
wrote).

| Endpoint | Source | Captured | What it exercises |
|---|---|---|---|
| `cms-blue-button-2` | `https://api.bluebutton.cms.gov/v2/fhir` | 2026-08-14 | A deliberately narrow API: three resource types, all documenting their interactions (T3 narrow-but-complete), and `rest.resource.profile` rather than `supportedProfile` |
| `inferno-reference` | `https://inferno.healthit.gov/reference-server/r4` | 2026-08-14 | US Core profiles declared in `supportedProfile`, SMART discovery present and complete |
| `oracle-health-open` | `https://fhir-open.cerner.com/r4/ec2458f2-1e24-41c8-b71b-0e701af7583d` | 2026-08-14 | No SMART discovery document: the live server answers 404 there, so no `smart.json` is committed and I2 fails the way it does in production |
| `aspirus-patient-access` | `https://prodpfotzinterop.healthtranzformdev.com/ug/patientaccess` | 2026-09-12 | A refusal with **no HTTP response**: the TLS handshake does not verify. `failure_kind: tls`, `status: null` |
| `bcbs-arizona-patient-access` | `https://azblue.innovaccer.com/fhir/` | 2026-09-12 | A refusal that **is** an HTTP response: 403 to an unauthenticated GET. `failure_kind: forbidden`, `status: 403` |

All five address public, unauthenticated discovery surfaces published by their operators, probed
with this project's own fetcher and User-Agent, two requests per endpoint. The two refusals were
recorded from the three-vantage run of 2026-09-12 (`generated_at 2026-09-12 14:27 UTC`), where
every vantage reported the same condition; the `vantages` array in each `refusal.json` names them.
Neither is a claim that the endpoint is down — `pages.yml` explains why three GitHub-hosted
runners are one network — only that this is what those vantages saw. Bodies are unmodified
apart from being re-serialized with sorted keys and two-space indentation, so that refreshing a
capture produces a readable diff instead of a wall of one line.

## Refreshing a capture

Two requests per endpoint, from a checkout, with the same fetcher the grader uses:

```bash
.venv/bin/python - <<'PY'
import json, pathlib
from fhir_scorecard.fetch import fetch_json

base = "https://inferno.healthit.gov/reference-server/r4"
out = pathlib.Path("tests/fixtures/inferno-reference")
for filename, url in (("metadata.json", f"{base}/metadata"),
                      ("smart.json", f"{base}/.well-known/smart-configuration")):
    result = fetch_json(url)
    print(filename, result.ok, result.status, len(result.body), result.error or "")
    if result.ok:
        (out / filename).write_text(
            json.dumps(json.loads(result.body), indent=2, sort_keys=True) + "\n")
PY
```

If you refresh, update the capture date in this file and in the row above. A capture whose date
is wrong is worse than no capture, because the grades built from it look current.
