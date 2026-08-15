# Captured discovery documents

These are **real `/metadata` and `.well-known/smart-configuration` documents, captured on
2026-08-14**, not hand-written examples. They exist so that `--offline` has data behind it, so the
Quick start command in the README does what it says, and so the parser is exercised against
documents real servers actually publish rather than only against the synthetic ones in
`conftest.py`.

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

All three are public, unauthenticated discovery documents published by their operators, retrieved
with this project's own fetcher and User-Agent, two requests per endpoint. Bodies are unmodified
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
