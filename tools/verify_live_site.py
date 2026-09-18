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
every registry endpoint to produce numbers that legitimately differ from the ones
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
    each row are what the registry says;
  * and, since 2026-09-13, every published *grade* in that CSV, against
    `fhir_scorecard.published.audit_rows` - the same rules `audit-site` applies to
    a build, plus agreement with the letter `api/index.json` publishes for the same
    endpoint. These are internal-consistency rules, not a rebuild: no score is
    recomputed and none could be. They belong here because the CSV is the artifact
    a reader downloads and cites, and until now its identity columns were checked
    here every night while its grade column was checked by nothing, anywhere.
  * the home page's share card: `og:image` and `twitter:image` are
    `site.social_card_url(origin)`, and `og:title`, `og:description` and
    `og:image:alt` are present and non-empty. These carry no grade, no latency and
    no timestamp, so they are a pure function of the origin like the two documents
    above; the card is singled out because a head naming an image the deployment
    does not serve is invisible from inside a checkout and blank everywhere the page
    is shared.

And one thing that is not a rebuild but is the whole point of a daily publish:
the site has to be recent. `api/index.json`'s `generated_at` must parse, must
not be in the future, and must be inside `--max-age-hours`.

A difference is not on its own a fault, and this used to read it as one.
`grade-and-publish` runs at 14:17 UTC and this check runs at 20:07, so a change
to a checked input merged between them is in `main` and not yet on the site: for
5h50m of every day -- 24% of the clock, and the working part of it -- the two
legitimately disagree. That is 1 of the 1 failures this check had recorded by
2026-09-12, and a check whose only red was spurious is a check people learn to
dismiss. So the newest commit touching a checked input is dated, and two
conditions are told apart:

  * the live `generated_at` predates that commit -- no publish has carried the
    change yet. Reported as a pending publish, named and annotated, and not a
    failure, because nothing is wrong and the next publish resolves it.
  * the publish ran after that commit and the bytes still differ -- the publish
    ran and produced the wrong thing. That is what this check is for, and it
    fails exactly as it always did.

The freshness bound sits outside that distinction deliberately. A site past
`--max-age-hours` fails whether or not a change is waiting to be published;
otherwise a publish that had silently stopped would be excused by the next
merge, which is the one failure the bound exists to catch. A checkout that
cannot be asked when an input last changed is treated as unknown and keeps the
difference a failure, which is why `live-integrity.yml` checks out full history:
at `fetch-depth: 1` the grafted root reads as having added every file, and a
date of "now" would excuse every difference there is.

What is deliberately NOT checked, because it moves for reasons that are not
drift: the *value* of any grade, score, latency, badge or availability figure,
`observed_since`, `answered_on_this_run`, the history and over-time pages, and
every rendered HTML page beyond the home page's share card.

That last one is a ratio worth stating plainly rather than leaving to be
discovered: this reads one of the several hundred pages the site publishes, and
that is the design, not a gap. Every other page carries a minute-resolution
`Generated` stamp and a daily rescore of live third-party endpoints, so there is
no byte a checkout could compare them to; a check that fetched them could only
assert they are served, which `audit-site` already establishes about the build
and `prove_the_origin_discriminates` cannot upgrade into a statement about their
content. The gap that was real was the grade column above, and it is closed by
reading the data rather than by fetching more HTML.

    python3 tools/verify_live_site.py

Vacuity is the failure mode a check like this is most exposed to, so these are
refused outright rather than reported as a pass: an empty or short registry, an
empty asset tree, any fetch that is not HTTP 200, and an origin that answers a
guaranteed-missing path with anything but 404.

Exit codes: 0 the live site still matches its committed inputs, or does not yet
because a publish is pending; 1 it does not and a publish has run since the
change; 4 the check could not run.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import http.client
import io
import json
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from fhir_scorecard import dataset as dataset_module  # noqa: E402
from fhir_scorecard import published as published_module  # noqa: E402
from fhir_scorecard import site as site_module  # noqa: E402
from fhir_scorecard.registry import Endpoint, load_registry  # noqa: E402

# The origin the publish stamps into every canonical, and the one that serves the
# site: https://chelseakr.github.io/fhir-scorecard/ answers 301 to here.
LIVE_URL = "https://fhir.chelseakr.com/"

ASSETS = REPO / "src" / "fhir_scorecard" / "assets"
REGISTRY = REPO / "data" / "registry.json"

# The workflow whose schedule is the only thing that puts a merged change on the live site.
# Its cron is read from here rather than restated, because a cron written in two places is a
# cron that will disagree with itself, and the sentence it feeds is only worth printing if it
# is true.
PUBLISH_WORKFLOW = REPO / ".github" / "workflows" / "pages.yml"

# Every committed path this check turns into an expected byte string, relative to the
# repository root. A commit touching one of them changes what the live site is supposed to
# serve, so the newest such commit is the date the deployment has to have caught up to:
#
#   src/fhir_scorecard/assets       compare_assets, byte for byte
#   data/registry.json              the endpoint identities in api/index.json and dataset.csv
#   src/fhir_scorecard/registry.py  load_registry, which is what turns that file into them
#   src/fhir_scorecard/dataset.py   schema_doc, SCHEMA_VERSION and _COLUMNS
#   src/fhir_scorecard/site.py      robots and social_card_url
#
# It is the two data paths this module reads plus the source of every `fhir_scorecard` module
# it imports, which is a derivation rather than a list someone has to remember to extend:
# tests/test_live_integrity_pending_publish.py fails if an import arrives without its file.
CHECKED_INPUTS: tuple[str, ...] = (
    "data/registry.json",
    "src/fhir_scorecard/assets",
    "src/fhir_scorecard/dataset.py",
    "src/fhir_scorecard/registry.py",
    "src/fhir_scorecard/site.py",
)

# Field separator for `git log --format`. A record is split on it rather than on whitespace so
# a commit subject cannot be mistaken for another field.
GIT_FIELD = "\x1f"
GIT_TIMEOUT_SECONDS = 30.0

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


def check_share_card(origin: Origin, nonce: str, canonical_origin: str) -> list[str]:
    """The home page's share card: the one part of a rendered page that does not move.

    Rendered pages are otherwise not compared here, for the reasons in the module
    docstring. The card is the exception. ``og:image`` is a pure function of the
    origin string, the image it names is one of the assets already compared byte for
    byte above, and none of these tags carries a grade, a latency or a timestamp.

    It is worth its own check because of how it fails. A head that names an image the
    deployment does not serve previews as a blank rectangle on every platform the link
    is shared to, and looks like nothing at all from inside this repository: the page
    renders, the assets are there, and no gate that reads a checkout can see it. The
    site published no image at all until 2026-09 for the same reason - nothing was
    looking at what a crawler receives.
    """
    home = fetch_exact(origin, "", nonce)
    if isinstance(home, str):
        return [f"{home}; the home page is where the share card is checked"]
    text = home.decode("utf-8", "replace")
    expected = site_module.social_card_url(canonical_origin)
    differences: list[str] = []
    for tag, attribute in (
        ("og:image", "property"),
        ("twitter:image", "name"),
    ):
        found = re.search(rf'<meta {attribute}="{tag}" content="([^"]*)">', text)
        if found is None:
            differences.append(f"the live home page declares no {tag}")
        elif found.group(1) != expected:
            differences.append(
                f"live {tag} is {found.group(1)}, and this checkout publishes {expected}"
            )
    for tag, attribute in (
        ("og:title", "property"),
        ("og:description", "property"),
        ("og:image:alt", "property"),
    ):
        found = re.search(rf'<meta {attribute}="{tag}" content="([^"]*)">', text)
        if found is None or not found.group(1).strip():
            differences.append(f"the live home page's {tag} is absent or empty")
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
) -> tuple[list[str], dt.datetime, dict[str, published_module.PublishedGrade]]:
    """The identity half of the API index, the timestamp the freshness check reads, and the
    letter it publishes per endpoint, which ``dataset_csv_differences`` compares the CSV to."""
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
    grades = {
        entry["endpoint_id"]: published_module.PublishedGrade(str(entry.get("grade")))
        for entry in listed
        if isinstance(entry, dict) and isinstance(entry.get("endpoint_id"), str)
    }
    return differences, _generated_at(index), grades


def dataset_csv_differences(
    text: str,
    endpoints: list[Endpoint],
    index_grades: dict[str, published_module.PublishedGrade],
) -> list[str]:
    """Every way a published CSV disagrees with this checkout, with itself, or with the index.

    Pure, so that the deployment check has tests: it is handed the bytes the origin served
    rather than fetching them. Three families, and the second and third are new since
    2026-09-12.

    * **Identity.** The header is ``dataset._COLUMNS``, there is one row per enabled registry
      entry, and the eight registry-derived columns say what the registry says. Nothing here
      involves a probe, so a difference is drift.
    * **Grades.** :func:`fhir_scorecard.published.audit_rows`, the same rules ``audit-site``
      applies to a build. They are internal-consistency rules rather than a rebuild - no grade is
      recomputed and none could be - which is what makes them runnable against a live site whose
      numbers legitimately differ from any checkout's.
    * **Agreement.** ``api/index.json`` publishes each endpoint's letter too, and a deployment
      that served a fresh CSV beside a stale index is exactly the partial-publish this check
      exists for. Nothing compared them until now.
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise LiveSiteError("dataset.csv is empty")
    differences: list[str] = []
    expected_header = [name for name, _description in dataset_module._COLUMNS]
    if rows[0] != expected_header:
        return [f"dataset.csv: header is {rows[0]}, this checkout's columns are {expected_header}"]
    body = rows[1:]
    if len(body) != len(endpoints):
        differences.append(
            f"dataset.csv: {len(body)} row(s), the registry has {len(endpoints)} endpoint(s)"
        )
    index = expected_header.index
    for number, row in enumerate(body, start=2):
        if len(row) != len(expected_header):
            differences.append(
                f"dataset.csv: line {number} has {len(row)} field(s), the header has "
                f"{len(expected_header)}"
            )
    well_formed = [row for row in body if len(row) == len(expected_header)]
    by_id = {row[index("endpoint_id")]: row for row in well_formed}
    differences += _registry_derived_differences(by_id, index, endpoints)

    # The grade rules, over every row the deployment serves. Before this the published grade
    # column was read by nothing, here or anywhere else, while the identity columns beside it
    # were checked here every night.
    records = [dict(zip(expected_header, row, strict=True)) for row in well_formed]
    differences += [str(finding) for finding in published_module.audit_rows(records)]
    differences += [
        str(finding)
        for finding in published_module.surface_differences(
            {
                "dataset.csv": published_module.rows_as_published(records),
                "api/index.json": index_grades,
            }
        )
    ]
    return differences


def _registry_derived_differences(
    by_id: dict[str, list[str]], index: Callable[[str], int], endpoints: list[Endpoint]
) -> list[str]:
    """The eight columns that come from data/registry.json rather than from a probe."""
    differences: list[str] = []
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


def compare_dataset_csv(
    origin: Origin,
    nonce: str,
    endpoints: list[Endpoint],
    index_grades: dict[str, published_module.PublishedGrade],
) -> list[str]:
    """Fetch the published CSV and hand it to :func:`dataset_csv_differences`."""
    live = fetch_exact(origin, "dataset.csv", nonce)
    if isinstance(live, str):
        raise LiveSiteError(live)
    return dataset_csv_differences(live.decode("utf-8"), endpoints, index_grades)


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


@dataclass(frozen=True)
class InputCommit:
    """The newest commit that changed what this check expects the live site to serve."""

    sha: str
    committed: dt.datetime
    subject: str

    @property
    def short_sha(self) -> str:
        return self.sha[:12]


def last_input_commit(
    repo: Path = REPO, paths: Sequence[str] = CHECKED_INPUTS
) -> InputCommit | None:
    """Date the newest commit touching a checked input, or None if this checkout cannot say.

    None is not "nothing has ever changed"; it is "this checkout does not know", and the caller
    keeps a difference red when it hears that. Reporting a real breakage as a publish that has
    not happened yet is the one wrong answer this must never give, so every uncertainty here
    resolves to None rather than to a date.

    `git log -1` is the right question even in a truncated history: it walks back from HEAD, so
    the first commit it finds touching these paths is the newest one, and shallowness only
    removes older commits. The exception is the boundary itself. A shallow clone grafts its
    oldest fetched commit into a parentless root, and a commit with no parents reads as having
    added every file in the tree -- so on `fetch-depth: 1` every path looks like it changed at
    HEAD, dating the inputs to now and excusing any difference at all. That is the blunting
    this whole function must not do, so a parentless answer from a shallow repository is
    refused. `live-integrity.yml` checks out full history and never reaches that case.
    """
    git = shutil.which("git")
    if git is None:
        return None

    def run(*arguments: str) -> str | None:
        try:
            done = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
                [git, "-C", str(repo), *arguments],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    shallow = run("rev-parse", "--is-shallow-repository")
    if shallow is None:
        return None
    record = run("log", "-1", f"--format=%H{GIT_FIELD}%cI{GIT_FIELD}%P{GIT_FIELD}%s", "--", *paths)
    if record is None:
        return None
    fields = record.strip("\n").split(GIT_FIELD)
    if len(fields) != 4 or not fields[0]:
        return None
    sha, stamp, parents, subject = fields
    if shallow.strip() != "false" and not parents.strip():
        return None
    try:
        committed = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if committed.tzinfo is None:
        return None
    return InputCommit(sha=sha, committed=committed.astimezone(dt.UTC), subject=subject.strip())


_DAILY_CRON = re.compile(r'cron:\s*"(\d{1,2}) (\d{1,2}) \* \* \*"')


def next_scheduled_publish(
    now: dt.datetime, workflow: Path = PUBLISH_WORKFLOW
) -> dt.datetime | None:
    """When grade-and-publish will next carry a merged change to the site.

    Read out of the workflow rather than restated, for the reason on PUBLISH_WORKFLOW. None for
    anything that is not one plain daily cron: that is the only shape this can answer for, and
    a confident wrong time is worse than no time at all.
    """
    try:
        text = workflow.read_text(encoding="utf-8")
    except OSError:
        return None
    found = _DAILY_CRON.findall(text)
    if len(found) != 1:
        return None
    minute, hour = int(found[0][0]), int(found[0][1])
    if not (0 <= minute < 60 and 0 <= hour < 24):
        return None
    moment = now.astimezone(dt.UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment if moment > now else moment + dt.timedelta(days=1)


@dataclass(frozen=True)
class Observation:
    """One complete look at the live site: what differs, how stale it is, and when it was made."""

    differences: list[str]
    staleness: list[str]
    generated: dt.datetime
    assets_compared: int
    endpoints_compared: int


def observe(origin: Origin, canonical_origin: str, maximum_age_hours: float) -> Observation:
    """Every comparison this check makes, in one pass over the live origin.

    Staleness is returned apart from the differences rather than mixed into them. They read the
    same to a person and they are not the same thing: a difference can be a publish that has
    not run yet, and a site past its age limit never can be.
    """
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
    index_differences, generated, index_grades = compare_api_index(
        origin, nonce, endpoints, canonical_origin
    )
    differences += index_differences
    differences += compare_dataset_csv(origin, nonce, endpoints, index_grades)
    differences += check_share_card(origin, nonce, canonical_origin)
    return Observation(
        differences=differences,
        staleness=check_freshness(generated, maximum_age_hours),
        generated=generated,
        assets_compared=len(assets),
        endpoints_compared=len(endpoints),
    )


def pending_publish(observation: Observation, commit: InputCommit | None) -> InputCommit | None:
    """Is the only complaint that no publish has run since a checked input changed?

    Two conditions were conflated here until 2026-09-13, and only one of them is a fault:

      (a) the live site does not match what `main` says it should -- a publish that failed, an
          upload that dropped an asset, Pages serving something stale. This is the check.
      (b) the live site does not match `main` *yet* -- a publish that has not happened. The
          site is republished once a day and this runs six hours before the next one, so a
          change merged in between produces exactly this, self-resolving and evidence of
          nothing.

    The publish stamp and the commit date tell them apart, and both sides already exist: the
    stamp is read for the freshness bound and the date is one `git log` away. A site generated
    before the commit cannot contain it. A site generated after it and still serving something
    else is (a), and stays red.

    Staleness is excluded on purpose. A site past `--max-age-hours` fails whether or not a
    change is waiting for it; excusing that would let a publish that had silently stopped be
    covered by the next merge, which is the failure the bound exists to catch.
    """
    if observation.staleness or not observation.differences:
        return None
    if commit is None or observation.generated >= commit.committed:
        return None
    return commit


def report_pending(pending: InputCommit, observation: Observation, url: str) -> None:
    """Say that the site is behind main, loudly enough to be seen without failing the run.

    An annotation as well as stdout: this outcome is a green check, and a green check with
    nothing attached to it is indistinguishable from a site that was already up to date.
    """
    following = next_scheduled_publish(dt.datetime.now(dt.UTC))
    when = f"{following:%Y-%m-%d %H:%M} UTC" if following else "the next grade-and-publish run"
    print(
        f"::notice title=pending publish::{url} is {len(observation.differences)} difference(s) "
        f"behind main; {pending.short_sha} has not been published yet, next publish {when}"
    )
    print(
        f"{url} does not match this checkout yet, and that is not a fault.\n"
        f"  live site generated {observation.generated:%Y-%m-%d %H:%M} UTC\n"
        f"  newest commit touching a checked input: {pending.short_sha} at "
        f"{pending.committed:%Y-%m-%d %H:%M} UTC, {pending.subject}\n"
        f"  next scheduled publish: {when}"
    )
    for difference in observation.differences:
        print(f"  {difference}")
    print(
        "\nA publish that has not run yet is not a deployment that disagrees with main. The "
        "site is still recent, so nothing here says the publish stopped working; dispatch "
        "grade-and-publish to close the gap sooner than the schedule would."
    )


def report_differences(observation: Observation, url: str) -> None:
    print(
        f"The live site at {url} no longer matches what this checkout publishes.", file=sys.stderr
    )
    for difference in [*observation.differences, *observation.staleness]:
        print(f"  {difference}", file=sys.stderr)
    print(
        "\nRe-run grade-and-publish, or find out why the deployment stopped agreeing "
        "with the registry and the committed assets.",
        file=sys.stderr,
    )


def report_success(observation: Observation, url: str) -> None:
    print(
        f"{url} still matches what this checkout publishes: "
        f"{observation.assets_compared} assets byte for byte, robots.txt and "
        f"dataset.schema.json byte for byte, and {observation.endpoints_compared} registry "
        f"endpoints named identically in api/index.json and dataset.csv, every published row "
        f"satisfying the grade contract and agreeing with the index, and a home page whose "
        f"share card addresses the card it serves. "
        f"Published {observation.generated:%Y-%m-%d %H:%M} UTC."
    )


def refuse_unbounded_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Bounds on the knobs, so a typo cannot quietly turn the check into nothing."""
    if not 1 <= args.attempts <= 10:
        parser.error("--attempts must be between 1 and 10")
    if not 0 <= args.retry_seconds <= 120:
        parser.error("--retry-seconds must be between 0 and 120")


@dataclass(frozen=True)
class Looked:
    """What the retry loop came away with."""

    observation: Observation | None
    pending: InputCommit | None
    error: LiveSiteError | None
    url: str


def settled(
    observation: Observation | None, pending: InputCommit | None, error: LiveSiteError | None
) -> bool:
    """Has this look reached an answer that looking again cannot change?

    The retries exist for a seconds-scale race: `deploy-pages` returns before every edge has
    the new bytes. A pending publish is an hours-scale one -- the next publish is up to a day
    away -- so retrying it is a minute of sleep and three times the requests to the origin to
    be told the same thing. It is an answer, so the loop stops on it.
    """
    if error is not None or observation is None:
        return False
    if pending is not None:
        return True
    return not observation.differences and not observation.staleness


def announce_retry(
    attempt: int,
    args: argparse.Namespace,
    observation: Observation | None,
    error: LiveSiteError | None,
) -> None:
    count = len(observation.differences) + len(observation.staleness) if observation else 0
    reason = str(error) if error is not None else f"{count} difference(s)"
    print(
        f"attempt {attempt}/{args.attempts}: {reason}; waiting "
        f"{args.retry_seconds:.0f}s in case a deploy is still settling",
        file=sys.stderr,
    )


def look_until_settled(args: argparse.Namespace, commit: InputCommit | None) -> Looked:
    last_error: LiveSiteError | None = None
    observation: Observation | None = None
    pending: InputCommit | None = None
    url = args.url
    for attempt in range(1, args.attempts + 1):
        last_error = None
        pending = None
        try:
            origin = Origin(args.url, timeout_seconds=args.timeout_seconds)
            url = origin.url
            observation = observe(origin, args.url.rstrip("/"), args.max_age_hours)
            pending = pending_publish(observation, commit)
        except LiveSiteError as exc:
            last_error = exc
            observation = None
        if settled(observation, pending, last_error):
            break
        if attempt < args.attempts:
            announce_retry(attempt, args, observation, last_error)
            time.sleep(args.retry_seconds)
    return Looked(observation=observation, pending=pending, error=last_error, url=url)


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
    parser.add_argument(
        "--attempts",
        type=int,
        default=3,
        help="how many times to look before reporting a difference (default 3)",
    )
    parser.add_argument(
        "--retry-seconds",
        type=float,
        default=20.0,
        help="seconds to wait between attempts, for a deploy to settle (default 20)",
    )
    args = parser.parse_args(argv)
    refuse_unbounded_options(parser, args)

    # Asked once, before any network read, and of `main` as this job checked it out. It is the
    # reference the publish stamp is measured against, not a property of the live site.
    commit = last_input_commit()
    looked = look_until_settled(args, commit)
    observation = looked.observation

    if looked.error is not None:
        print(f"live integrity check could not run: {looked.error}", file=sys.stderr)
        return EXIT_CANNOT_RUN
    if observation is None:
        print("live integrity check could not run: nothing was observed", file=sys.stderr)
        return EXIT_CANNOT_RUN

    if looked.pending is not None:
        report_pending(looked.pending, observation, looked.url)
        return 0

    if observation.differences or observation.staleness:
        if commit is None:
            print(
                "::warning title=undatable checkout::this checkout cannot be asked when a "
                "checked input last changed, which a shallow clone never can, so a difference "
                "that is only a publish that has not run yet is being reported as a failure",
                file=sys.stderr,
            )
        report_differences(observation, looked.url)
        return EXIT_DIFFERS

    report_success(observation, looked.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
