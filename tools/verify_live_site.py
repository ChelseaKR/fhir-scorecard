#!/usr/bin/env python3
"""Fail when fhir.chelseakr.com stops being the site this repository publishes.

grade-and-publish runs `audit-site` on the local `site/` directory before the
upload, and the deploy job returns. After that nothing has ever looked at the
bytes a reader receives. A publish that failed, an upload that dropped an asset,
or a Pages configuration that stopped serving would leave every gate green while
the live site was stale, broken, or gone, and nothing in this repository could
tell.

This is the check for the deployment, and it is narrower than its siblings on
purpose, because most of this site is not reproducible from a checkout. Every
page is a daily rescore of live third-party endpoints, every page footer carries
a minute-resolution `Generated` stamp, and the availability and history pages
derive from `data/history.json`, which CI restores from the `capability-history`
branch rather than from `main`. Rebuilding any of that here would mean probing
45 third-party servers to produce numbers that legitimately differ from the ones
already published, so it is not attempted.

What IS a pure function of committed inputs is checked exactly:

  * every file under `src/fhir_scorecard/assets`, which `write_assets` copies
    verbatim into the published tree, compared byte for byte;
  * `robots.txt`, which is `site.robots(origin)` and nothing else;
  * `dataset.schema.json`, which is `dataset.schema_doc(origin)` and nothing
    else;
  * the identity half of `api/index.json`: schema version, the endpoint count,
    and per endpoint the id, name, kind, API url and page url, all derived from
    `data/registry.json` by this checkout;
  * the identity half of `dataset.csv`: its header is `dataset._COLUMNS`, it has
    one row per enabled registry entry, and the eight registry-derived columns in
    each row are what the registry says.

And one thing that is not a rebuild but is the whole point of a daily publish:
the site has to be recent. `api/index.json`'s `generated_at` must parse, must
not be in the future, and must be inside `--max-age-hours`.

What is deliberately NOT checked, because it moves for reasons that are not
drift: every grade, score, latency, badge, availability figure, `observed_since`,
`answered_on_this_run`, the history and over-time pages, and every rendered HTML
page (each carries the generated timestamp).

    python3 tools/verify_live_site.py

Vacuity is the failure mode a check like this is most exposed to, so these are
refused outright rather than reported as a pass: an empty or short registry, an
empty asset tree, any fetch that is not HTTP 200, and an origin that answers a
guaranteed-missing path with anything but 404.

Exit codes: 0 the live site still matches its committed inputs, 1 it does not,
4 the check could not run.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import http.client
import io
import json
import secrets
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from fhir_scorecard import dataset as dataset_module  # noqa: E402
from fhir_scorecard import site as site_module  # noqa: E402
from fhir_scorecard.registry import Endpoint, load_registry  # noqa: E402

# The origin the publish stamps into every canonical, and the one that serves the
# site: https://chelseakr.github.io/fhir-scorecard/ answers 301 to here.
LIVE_URL = "https://fhir.chelseakr.com/"

ASSETS = REPO / "src" / "fhir_scorecard" / "assets"
REGISTRY = REPO / "data" / "registry.json"

# Floors. A check that compares nothing must fail, not pass.
MINIMUM_ASSETS = 40
MINIMUM_ENDPOINTS = 20

MAXIMUM_FILE_BYTES = 16 * 1024 * 1024
EXIT_DIFFERS = 1
EXIT_CANNOT_RUN = 4


class LiveSiteError(RuntimeError):
    """The live site could not be verified against this checkout."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class Origin:
    """Bounded HTTPS reads from one fixed public origin. Redirects are not followed."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
            raise LiveSiteError(f"live URL {url!r} is not a canonical HTTPS origin")
        if not 1.0 <= timeout_seconds <= 60.0:
            raise LiveSiteError("timeout must be between 1 and 60 seconds")
        self.host = parts.hostname
        self.base = parts.path.rstrip("/")
        self.url = url
        self._timeout = timeout_seconds

    def target(self, relative: str, nonce: str) -> str:
        if relative.startswith("/") or "?" in relative or "#" in relative:
            raise LiveSiteError(f"relative path {relative!r} is not canonical")
        return f"{self.base}/{relative}?live-integrity={nonce}"

    def get(
        self,
        relative: str,
        *,
        nonce: str,
        maximum_bytes: int = MAXIMUM_FILE_BYTES,
    ) -> Response:
        target = self.target(relative, nonce)
        # The audit rule below is about HTTPSConnection used without certificate
        # verification: Python before 3.4.3 did not verify by default. This call
        # passes ssl.create_default_context(), which verifies both the chain and
        # the hostname, and is the condition the rule exists to require.
        # nosemgrep: httpsconnection-detected
        connection = http.client.HTTPSConnection(
            self.host, timeout=self._timeout, context=ssl.create_default_context()
        )
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept-Encoding": "identity",
                    "Cache-Control": "no-cache, no-store, max-age=0",
                    "Pragma": "no-cache",
                    "User-Agent": "fhir-scorecard-live-integrity/1",
                },
            )
            response = connection.getresponse()
            encoding = response.getheader("Content-Encoding")
            if encoding not in {None, "identity"}:
                raise LiveSiteError(f"{target} came back {encoding}-encoded, not identity")
            body = response.read(maximum_bytes + 1)
            if len(body) > maximum_bytes:
                raise LiveSiteError(f"{target} exceeds the {maximum_bytes} byte read limit")
            return Response(status=response.status, body=body)
        except (OSError, http.client.HTTPException) as exc:
            raise LiveSiteError(f"GET https://{self.host}{target} failed: {exc}") from exc
        finally:
            connection.close()


def short(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:16]


def fetch_exact(origin: Origin, relative: str, nonce: str) -> bytes | str:
    """The live bytes, or a difference line if the origin would not serve them."""
    response = origin.get(relative, nonce=nonce)
    if response.status != 200:
        return f"{relative}: the live origin returned HTTP {response.status}"
    return response.body


def prove_the_origin_discriminates(origin: Origin, nonce: str) -> None:
    """A host that answers everything with 200 makes every comparison vacuous."""
    missing = f".live-integrity-guaranteed-absent-{nonce}"
    response = origin.get(missing, nonce=nonce, maximum_bytes=1024 * 1024)
    if response.status != 404:
        raise LiveSiteError(
            f"the origin answered a guaranteed-missing path with HTTP {response.status} "
            f"instead of 404, so a matching fetch would prove nothing: /{missing}"
        )


def committed_assets() -> dict[str, bytes]:
    if not ASSETS.is_dir():
        raise LiveSiteError(f"{ASSETS} is not a directory")
    assets: dict[str, bytes] = {}
    for path in sorted(ASSETS.rglob("*")):
        if path.is_symlink():
            raise LiveSiteError(f"{path} is a symlink; refusing to publish-compare it")
        if path.is_file():
            assets[path.relative_to(ASSETS).as_posix()] = path.read_bytes()
    if len(assets) < MINIMUM_ASSETS:
        raise LiveSiteError(
            f"{ASSETS} holds {len(assets)} file(s), below the floor of {MINIMUM_ASSETS}. "
            f"A check that compares nothing must fail, not pass."
        )
    return assets


def compare_assets(origin: Origin, nonce: str, assets: dict[str, bytes]) -> list[str]:
    """write_assets copies these verbatim, so the deployment must serve them verbatim."""
    differences: list[str] = []
    for relative, expected in sorted(assets.items()):
        live = fetch_exact(origin, f"assets/{relative}", nonce)
        if isinstance(live, str):
            differences.append(f"{live}; this checkout publishes {len(expected)} bytes")
        elif live != expected:
            differences.append(
                f"assets/{relative}: live sha256 {short(live)} ({len(live)} bytes) is not "
                f"the committed {short(expected)} ({len(expected)} bytes)"
            )
    return differences


def compare_pure_documents(origin: Origin, nonce: str, canonical_origin: str) -> list[str]:
    """Two published files are a pure function of the origin string and nothing else."""
    differences: list[str] = []
    for relative, expected_text in (
        ("robots.txt", site_module.robots(canonical_origin)),
        ("dataset.schema.json", dataset_module.schema_doc(canonical_origin)),
    ):
        expected = expected_text.encode("utf-8")
        live = fetch_exact(origin, relative, nonce)
        if isinstance(live, str):
            differences.append(f"{live}; this checkout renders {len(expected)} bytes")
        elif live != expected:
            differences.append(
                f"{relative}: live sha256 {short(live)} ({len(live)} bytes) is not what "
                f"this checkout renders, {short(expected)} ({len(expected)} bytes)"
            )
    return differences


def strict_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveSiteError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LiveSiteError(f"{label} is not a JSON object")
    return value


def _index_entry_differences(
    endpoints: list[Endpoint],
    listed: list[Any],
    canonical_origin: str,
) -> list[str]:
    """Per endpoint: is the live index naming what the registry names?"""
    differences: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for entry in listed:
        if isinstance(entry, dict) and isinstance(entry.get("endpoint_id"), str):
            by_id[entry["endpoint_id"]] = entry
    for endpoint in endpoints:
        entry = by_id.get(endpoint.endpoint_id)
        if entry is None:
            differences.append(
                f"api/index.json: the registry has {endpoint.endpoint_id} and the live "
                f"index does not list it"
            )
            continue
        expected = {
            "name": endpoint.name,
            "kind": endpoint.kind,
            "url": f"{canonical_origin}/api/endpoint/{endpoint.endpoint_id}.json",
            "page": f"{canonical_origin}/endpoint/{endpoint.endpoint_id}/",
        }
        for key, want in expected.items():
            if entry.get(key) != want:
                differences.append(
                    f"api/index.json: {endpoint.endpoint_id}.{key} is {entry.get(key)!r}, "
                    f"the registry says {want!r}"
                )
    for extra in sorted(set(by_id) - {e.endpoint_id for e in endpoints}):
        differences.append(f"api/index.json: lists {extra!r}, which the registry does not")
    return differences


def _generated_at(index: dict[str, Any]) -> dt.datetime:
    """The publish timestamp, refused rather than guessed at if it is not one."""
    stamped = index.get("generated_at")
    if not isinstance(stamped, str):
        raise LiveSiteError(f"api/index.json generated_at is {stamped!r}")
    try:
        return dt.datetime.strptime(stamped, "%Y-%m-%d %H:%M UTC").replace(tzinfo=dt.UTC)
    except ValueError as exc:
        raise LiveSiteError(f"api/index.json generated_at {stamped!r} is not a timestamp") from exc


def compare_api_index(
    origin: Origin,
    nonce: str,
    endpoints: list[Endpoint],
    canonical_origin: str,
) -> tuple[list[str], dt.datetime]:
    """The identity half of the API index, and the timestamp the freshness check reads."""
    live = fetch_exact(origin, "api/index.json", nonce)
    if isinstance(live, str):
        raise LiveSiteError(live)
    index = strict_json(live, "api/index.json")
    listed = index.get("endpoints")
    if not isinstance(listed, list):
        raise LiveSiteError("api/index.json has no endpoints list")
    differences: list[str] = []
    if index.get("schema_version") != dataset_module.SCHEMA_VERSION:
        differences.append(
            f"api/index.json: schema_version is {index.get('schema_version')!r}, not "
            f"{dataset_module.SCHEMA_VERSION}"
        )
    for key in ("count", "endpoints_listed"):
        if index.get(key) != len(endpoints):
            differences.append(
                f"api/index.json: {key} is {index.get(key)!r}, the registry has {len(endpoints)}"
            )
    if len(listed) != len(endpoints):
        differences.append(
            f"api/index.json: lists {len(listed)} endpoints, the registry has {len(endpoints)}"
        )
    differences += _index_entry_differences(endpoints, listed, canonical_origin)
    return differences, _generated_at(index)


def compare_dataset_csv(origin: Origin, nonce: str, endpoints: list[Endpoint]) -> list[str]:
    """The registry-derived columns of the published CSV, which no probe can move."""
    live = fetch_exact(origin, "dataset.csv", nonce)
    if isinstance(live, str):
        raise LiveSiteError(live)
    rows = list(csv.reader(io.StringIO(live.decode("utf-8"))))
    if not rows:
        raise LiveSiteError("dataset.csv is empty")
    differences: list[str] = []
    expected_header = [name for name, _description in dataset_module._COLUMNS]
    if rows[0] != expected_header:
        differences.append(
            f"dataset.csv: header is {rows[0]}, this checkout's columns are {expected_header}"
        )
        return differences
    body = rows[1:]
    if len(body) != len(endpoints):
        differences.append(
            f"dataset.csv: {len(body)} row(s), the registry has {len(endpoints)} endpoint(s)"
        )
    index = expected_header.index
    by_id = {row[index("endpoint_id")]: row for row in body if row}
    for endpoint in endpoints:
        row = by_id.get(endpoint.endpoint_id)
        if row is None:
            differences.append(f"dataset.csv: no row for registry entry {endpoint.endpoint_id}")
            continue
        registry_derived = {
            "name": endpoint.name,
            "kind": endpoint.kind,
            "base_url": endpoint.base_url,
            "expects_fhir": endpoint.expects,
            "verified_method": endpoint.verified_method,
            "verified_date": endpoint.verified_date,
            "verification_basis": endpoint.verification_basis,
            "reverified_date": endpoint.reverified_date,
        }
        for column, want in registry_derived.items():
            got = row[index(column)]
            if got != want:
                differences.append(
                    f"dataset.csv: {endpoint.endpoint_id}.{column} is {got!r}, the registry "
                    f"says {want!r}"
                )
    for extra in sorted(set(by_id) - {e.endpoint_id for e in endpoints}):
        differences.append(f"dataset.csv: has a row for {extra!r}, which the registry does not")
    return differences


def check_freshness(generated: dt.datetime, maximum_hours: float) -> list[str]:
    """A daily publish that stopped publishing still audits clean. This is what catches it."""
    now = dt.datetime.now(dt.UTC)
    if generated > now + dt.timedelta(minutes=5):
        return [
            f"api/index.json says it was generated at {generated:%Y-%m-%d %H:%M} UTC, "
            f"which is in the future"
        ]
    age = (now - generated).total_seconds() / 3600
    if age > maximum_hours:
        return [
            f"the live site was generated {age:.1f} hours ago "
            f"({generated:%Y-%m-%d %H:%M} UTC), past the {maximum_hours:.0f} hour limit. "
            f"grade-and-publish runs daily, so this is a publish that stopped happening."
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=LIVE_URL, help=f"live site root (default {LIVE_URL})")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=48.0,
        help="how stale the published run may be before this fails (default 48)",
    )
    args = parser.parse_args(argv)

    try:
        origin = Origin(args.url, timeout_seconds=args.timeout_seconds)
        canonical_origin = args.url.rstrip("/")
        endpoints = [e for e in load_registry(REGISTRY) if e.enabled]
        if len(endpoints) < MINIMUM_ENDPOINTS:
            raise LiveSiteError(
                f"the registry holds {len(endpoints)} enabled endpoint(s), below the floor "
                f"of {MINIMUM_ENDPOINTS}. A check that compares nothing must fail, not pass."
            )
        assets = committed_assets()
        nonce = secrets.token_hex(16)
        prove_the_origin_discriminates(origin, nonce)

        differences = compare_assets(origin, nonce, assets)
        differences += compare_pure_documents(origin, nonce, canonical_origin)
        index_differences, generated = compare_api_index(origin, nonce, endpoints, canonical_origin)
        differences += index_differences
        differences += compare_dataset_csv(origin, nonce, endpoints)
        differences += check_freshness(generated, args.max_age_hours)
    except LiveSiteError as exc:
        print(f"live integrity check could not run: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    if differences:
        print(
            f"The live site at {origin.url} no longer matches what this checkout publishes.",
            file=sys.stderr,
        )
        for difference in differences:
            print(f"  {difference}", file=sys.stderr)
        print(
            "\nRe-run grade-and-publish, or find out why the deployment stopped agreeing "
            "with the registry and the committed assets.",
            file=sys.stderr,
        )
        return EXIT_DIFFERS

    print(
        f"{origin.url} still matches what this checkout publishes: {len(assets)} assets "
        f"byte for byte, robots.txt and dataset.schema.json byte for byte, and "
        f"{len(endpoints)} registry endpoints named identically in api/index.json and "
        f"dataset.csv. Published {generated:%Y-%m-%d %H:%M} UTC."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
