"""Compliance report bundle: one buyer's branded evidence reports, for a cohort of endpoints.

The site already ships one endpoint's evidence page for free (``site.endpoint_page``) and this
module's sibling ``bundle_report`` renders the same evidence as one self-contained file. A
compliance consultancy tracking several payer clients, or a state Medicaid or CHIP office
overseeing several managed-care organizations, wants the same thing for its whole caseload, with
its own name on the cover, delivered as one archive. This module is the pure core of that
product (docs/compliance-bundle-plan.md): it turns a request into a validated
:class:`BundleRequest`, classifies every requested endpoint id against the loaded registry and
the published dataset, renders each included one through :func:`bundle_report.render_report`,
and zips the results with a manifest that names every id that was asked for and what happened to
it.

Nothing here computes a new finding or a new grade. Each report is the same evidence the free
site publishes; the bundle is packaging, branding, and delivery. Two rules shape this file, both
carried over from the free path's own discipline:

- **An id is never silently dropped.** A request for 20 endpoints that yields 17 reports says so,
  per id, in the manifest and the delivery email: unknown id, disabled entry, or no published
  scorecard yet. A bundle that quietly shrank would teach a buyer to distrust the tool.
- **An endpoint that answered and was graded "not observed" still gets a report.** This is a
  deliberate divergence from the equivalent module in gtfs-scorecard, where an agency with no
  published scorecard is excluded from the archive. Here, "not observed" is itself a finding
  (grading.py: "an unreachable endpoint is a finding, not a reason to drop it"), and a bundle
  that quietly omitted every endpoint currently unreachable would hide exactly the rows a
  compliance buyer most needs to see. Only ``unknown_id`` (not in the registry) and
  ``disabled`` (registry entry present but turned off) are excluded from the archive; every
  endpoint the registry tracks and the dataset has graded gets a page, whatever its grade.
- **Nothing here talks to a payment provider.** Whether a request was paid for is settled before
  it reaches this module (infra/compliance-bundle). The core can be run locally against the
  committed snapshot with no account and no key, the same way ``fhir-scorecard grade`` can.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import secrets
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fhir_scorecard import bundle_report
from fhir_scorecard.bundle_report import DEFAULT_ACCENT, Brand, ReportError, _validate_accent
from fhir_scorecard.registry import Endpoint, load_registry

MAX_ENDPOINTS = 70
MAX_PROGRAM_NAME = 120
MAX_LOGO_BYTES = 512 * 1024
CADENCES = ("one_time", "quarterly")
BUNDLE_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATA_URI_RE = re.compile(
    r"^data:(image/svg\+xml|image/png|image/jpeg);base64,([A-Za-z0-9+/=\s]+)$"
)
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
# The link a bundle lives behind is a 128-bit capability; it expires with the object. Kept in
# step with infra/compliance-bundle's object lifecycle rule and DynamoDB TTL.
DOWNLOAD_DAYS = 30

FetchLogo = Callable[[str], bytes]


class BundleError(ValueError):
    """A request problem the message explains in plain language."""


@dataclass(frozen=True)
class BundleRequest:
    """One validated request for a bundle.

    ``logo`` is either a data: URI (already embedded) or an https URL still to be fetched at
    build time, or None. The setup Lambda validates the URL's shape but never fetches it; the
    build step, which runs in GitHub Actions rather than in a Lambda with network egress rules
    to worry about, does.
    """

    bundle_id: str
    program_name: str
    accent: str
    logo: str | None
    endpoint_ids: tuple[str, ...]
    deliver_to: str
    cadence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "program_name": self.program_name,
            "accent": self.accent,
            "logo": self.logo or "",
            "endpoint_ids": list(self.endpoint_ids),
            "deliver_to": self.deliver_to,
            "cadence": self.cadence,
        }


def archive_key(bundle_id: str) -> str:
    """Where a built bundle lives in the artifacts bucket.

    One definition, because three things have to agree on it or a buyer's link 404s: the
    workflow that uploads, the download route that presigns, and any future reconciler that
    decides whether an order was delivered.
    """
    return f"compliance-bundles/{bundle_id}/bundle.zip"


def new_bundle_id() -> str:
    """A fresh 128-bit hex token; the bundle's download capability."""
    return secrets.token_hex(16)


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _endpoint_ids(raw: object, limit: int = MAX_ENDPOINTS) -> tuple[str, ...]:
    if isinstance(raw, str):
        parts = raw.replace("\n", ",").split(",")
    elif isinstance(raw, list | tuple):
        parts = [str(item) for item in raw]
    else:
        raise BundleError("endpoint_ids must be a comma-separated string or a list")
    seen: list[str] = []
    for part in parts:
        endpoint_id = part.strip().lower()
        if not endpoint_id:
            continue
        if not _ID_RE.match(endpoint_id):
            raise BundleError(
                f"endpoint id {endpoint_id!r} is not a registry id "
                "(lowercase letters, digits, and -)"
            )
        if endpoint_id not in seen:
            seen.append(endpoint_id)
    if not seen:
        raise BundleError("endpoint_ids must name at least one endpoint")
    if len(seen) > limit:
        if limit < MAX_ENDPOINTS:
            raise BundleError(f"your plan covers at most {limit} endpoints; {len(seen)} were given")
        raise BundleError(f"a bundle covers at most {limit} endpoints; {len(seen)} were given")
    return tuple(seen)


def _logo(raw: str) -> str | None:
    if not raw:
        return None
    if raw.startswith("data:"):
        match = _DATA_URI_RE.match(raw)
        if match is None:
            raise BundleError("logo must be an SVG, PNG, or JPEG data: URI, or an https URL")
        try:
            decoded = base64.b64decode(match.group(2), validate=False)
        except ValueError as err:
            raise BundleError("logo data: URI is not valid base64") from err
        if len(decoded) > MAX_LOGO_BYTES:
            raise BundleError(f"logo must be {MAX_LOGO_BYTES // 1024} KiB or smaller")
        return raw
    if not raw.startswith("https://"):
        raise BundleError("logo must be an https URL or an SVG, PNG, or JPEG data: URI")
    _validate_public_https_url(raw)
    return raw


def _validate_public_https_url(url: str) -> None:
    """Refuse a logo URL whose host is not on the public internet.

    Reuses the same address-boundary primitives ``intake.py`` applies to a submitted endpoint
    base URL (``intake._public``, ``intake.default_resolver``): an https URL supplied by an
    unauthenticated buyer through the setup form is exactly the shape of input that check exists
    for, so this is the same boundary applied to a second untrusted URL rather than a second
    implementation of it.
    """
    from urllib.parse import urlsplit

    from fhir_scorecard.intake import _addresses, _public, default_resolver

    parts = urlsplit(url)
    if "@" in parts.netloc:
        raise BundleError("logo URL refused: must not carry credentials before the host")
    host = parts.hostname
    if not host:
        raise BundleError("logo URL refused: names no host")
    try:
        import ipaddress

        addresses = [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        try:
            addresses = _addresses(host, default_resolver)
        except LookupError as err:
            raise BundleError(f"logo URL refused: host {host!r} did not resolve ({err})") from err
    private = sorted({addr for addr in addresses if not _public(addr)})
    if private:
        raise BundleError(
            f"logo URL refused: host {host!r} resolves to a non-public address ({private[0]})"
        )


def parse_request(
    raw: Mapping[str, object], *, max_endpoints: int = MAX_ENDPOINTS
) -> BundleRequest:
    """Validate a raw request (form body, workflow inputs, or a stored row).

    Raises BundleError with one plain sentence on the first problem, so the setup form can show
    it and a workflow log can be read without the code.

    ``max_endpoints`` narrows the cohort cap to what the buyer's plan covers (the setup route
    passes 15 for a ``bundle_15`` purchase). It can only narrow: anything above MAX_ENDPOINTS is
    held to MAX_ENDPOINTS.
    """
    if max_endpoints < 1:
        raise ValueError("max_endpoints must be at least 1")
    bundle_id = _text(raw, "bundle_id").lower()
    if not BUNDLE_ID_RE.match(bundle_id):
        raise BundleError("bundle_id must be 32 lowercase hex characters")
    program_name = _text(raw, "program_name")
    if not program_name:
        raise BundleError("program_name is required; it goes on every report cover")
    if len(program_name) > MAX_PROGRAM_NAME:
        raise BundleError(f"program_name must be {MAX_PROGRAM_NAME} characters or fewer")
    try:
        accent = _validate_accent(_text(raw, "accent") or DEFAULT_ACCENT)
    except ReportError as err:
        raise BundleError(str(err)) from err
    deliver_to = _text(raw, "deliver_to")
    if not _EMAIL_RE.match(deliver_to):
        raise BundleError("deliver_to must be an email address; the download link goes there")
    cadence = _text(raw, "cadence") or "one_time"
    if cadence not in CADENCES:
        raise BundleError(f"cadence must be one of {', '.join(CADENCES)}")
    return BundleRequest(
        bundle_id=bundle_id,
        program_name=program_name,
        accent=accent,
        logo=_logo(_text(raw, "logo")),
        endpoint_ids=_endpoint_ids(raw.get("endpoint_ids"), min(max_endpoints, MAX_ENDPOINTS)),
        deliver_to=deliver_to,
        cadence=cadence,
    )


# ---------------------------------------------------------------------------
# Classification: which requested ids can become a report, and why not.
# ---------------------------------------------------------------------------

STATUS_INCLUDED = "included"
STATUS_UNKNOWN = "unknown_id"
STATUS_DISABLED = "disabled"
STATUS_NOT_PUBLISHED = "not_published"

_STATUS_DETAIL = {
    STATUS_UNKNOWN: "not a tracked registry id; check the id at fhir.chelseakr.com",
    STATUS_DISABLED: "in the registry but currently disabled; see its history for why",
    STATUS_NOT_PUBLISHED: "tracked and enabled, but no scorecard is published for it yet",
}


def classify(
    endpoint_ids: tuple[str, ...],
    registry: list[Endpoint],
    scorecards_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Map each id to included / unknown_id / disabled / not_published.

    Unlike gtfs-scorecard's equivalent, a "not observed" scorecard is ``included``, not
    excluded: see the module docstring for why. Only ``unknown_id`` and ``disabled`` ever
    exclude an id from the archive.
    """
    by_id = {e.endpoint_id: e for e in registry}
    out: dict[str, str] = {}
    for endpoint_id in endpoint_ids:
        entry = by_id.get(endpoint_id)
        if entry is None:
            out[endpoint_id] = STATUS_UNKNOWN
        elif not entry.enabled:
            out[endpoint_id] = STATUS_DISABLED
        elif endpoint_id not in scorecards_by_id:
            out[endpoint_id] = STATUS_NOT_PUBLISHED
        else:
            out[endpoint_id] = STATUS_INCLUDED
    return out


def plan(
    request: BundleRequest,
    registry: list[Endpoint],
    scorecards_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """The build plan a workflow reads before rendering: which ids will get a report, and which
    were refused up front and why."""
    statuses = classify(request.endpoint_ids, registry, scorecards_by_id)
    return {
        "bundle_id": request.bundle_id,
        "included": [a for a, s in statuses.items() if s == STATUS_INCLUDED],
        "refused": [
            {"id": a, "status": s, "detail": _STATUS_DETAIL[s]}
            for a, s in statuses.items()
            if s != STATUS_INCLUDED
        ],
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _sniff_media_type(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8"):
        return "image/jpeg"
    head = raw[:2048].lstrip().lower()
    if head.startswith(b"<") and b"<svg" in head:
        return "image/svg+xml"
    raise BundleError("logo URL did not return an SVG, PNG, or JPEG image")


def _default_fetch(url: str) -> bytes:
    """Fetch a logo URL through this package's one guarded network door (fetch.py), rather than
    opening a connection of its own here. See ``fetch.fetch_bytes`` and
    ``tests/test_probe_contract.py``: fetch.py is the only module in this package permitted to
    reach the network directly, so every request this project makes -- the FHIR discovery
    probes and this one -- is covered by the same HTTPS-enforcement and no-blind-redirect
    guarantees."""
    from fhir_scorecard.fetch import fetch_bytes

    return fetch_bytes(url, max_bytes=MAX_LOGO_BYTES, timeout=20)


def resolve_logo(logo: str | None, fetch: FetchLogo | None = None) -> str | None:
    """Turn the request's logo into a data: URI, fetching an https URL. A data: URI passes
    through unchanged."""
    if logo is None or logo.startswith("data:"):
        return logo
    fetcher = fetch or _default_fetch
    try:
        raw = fetcher(logo)
    except Exception as err:  # any fetch failure is one plain sentence to the buyer
        raise BundleError(f"logo could not be fetched from {logo}: {err}") from err
    if len(raw) > MAX_LOGO_BYTES:
        raise BundleError(f"logo must be {MAX_LOGO_BYTES // 1024} KiB or smaller")
    media_type = _sniff_media_type(raw)
    return f"data:{media_type};base64,{base64.b64encode(raw).decode('ascii')}"


def _readme(request: BundleRequest, manifest: dict[str, Any]) -> str:
    lines = [
        f"FHIR Scorecard compliance evidence reports prepared for {request.program_name}",
        f"Generated {manifest['generated_at']} (UTC). Bundle {request.bundle_id}.",
        "",
        "reports/<endpoint-id>-report.html: one self-contained file per endpoint.",
        "Open any of them in a browser and print to PDF; nothing needs a network.",
        "manifest.json: every endpoint id that was requested, and what happened to it.",
        "",
        f"{manifest['included']} of {manifest['requested']} requested endpoints are included.",
    ]
    skipped = [a for a in manifest["endpoints"] if a["status"] != STATUS_INCLUDED]
    if skipped:
        lines.append("Not included:")
        lines.extend(f"  {a['id']}: {a['detail']}" for a in skipped)
    lines += [
        "",
        "Every number comes from the endpoint's published scorecard at",
        "https://fhir.chelseakr.com/endpoint/<id>/. The generator computes nothing new, and the",
        "free evidence page for one endpoint is the same document. Purchase buys no influence",
        "over grades, methodology, or which endpoints are listed.",
        "",
    ]
    return "\n".join(lines)


def build_bundle(
    request: BundleRequest,
    out_zip: Path,
    *,
    registry_path: Path = Path("data/registry.json"),
    scorecards_path: Path = Path("site/scorecards.json"),
    now: dt.datetime | None = None,
    fetch_logo: FetchLogo | None = None,
    workdir: Path | None = None,
) -> dict[str, Any]:
    """Render every included endpoint's branded report and zip them with a manifest.

    Returns the manifest (also written inside the archive). Raises BundleError only for a
    request-level problem (an unusable logo, or an unreadable registry/dataset); a single
    endpoint with no published scorecard is recorded in the manifest, never raised.
    """
    generated_at = (now or dt.datetime.now(dt.UTC)).replace(microsecond=0)
    brand = Brand(
        name=request.program_name,
        logo_data_uri=resolve_logo(request.logo, fetch_logo),
        accent=request.accent,
    )
    registry = load_registry(registry_path)
    by_id = {e.endpoint_id: e for e in registry}
    published = json.loads(scorecards_path.read_text(encoding="utf-8"))
    scorecards_by_id: dict[str, Mapping[str, Any]] = {
        str(row["endpoint_id"]): row for row in published.get("scorecards", [])
    }
    statuses = classify(request.endpoint_ids, registry, scorecards_by_id)

    work = workdir or out_zip.parent / f".{request.bundle_id}.work"
    reports_dir = work / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Imported lazily so the CLI's fast paths (parse_request, classify, plan) never import
    # cli.py, which pulls in argparse and the whole command surface.
    from fhir_scorecard.cli import _verification_sentence

    rows: list[dict[str, Any]] = []
    for endpoint_id in request.endpoint_ids:
        status = statuses[endpoint_id]
        if status != STATUS_INCLUDED:
            rows.append({"id": endpoint_id, "status": status, "detail": _STATUS_DETAIL[status]})
            continue
        card = bundle_report.scorecard_from_dict(scorecards_by_id[endpoint_id])
        entry = by_id.get(endpoint_id)
        target = reports_dir / f"{endpoint_id}-report.html"
        target.write_text(
            bundle_report.render_report(
                card,
                base_url=entry.base_url if entry else "",
                verified=_verification_sentence(entry),
                brand=brand,
                generated_at=generated_at,
            ),
            encoding="utf-8",
        )
        rows.append(
            {
                "id": endpoint_id,
                "status": STATUS_INCLUDED,
                "detail": "",
                "file": f"reports/{target.name}",
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": "1",
        "bundle_id": request.bundle_id,
        "program_name": request.program_name,
        "cadence": request.cadence,
        "generated_at": generated_at.isoformat(),
        "requested": len(rows),
        "included": sum(1 for r in rows if r["status"] == STATUS_INCLUDED),
        "endpoints": rows,
    }

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", _readme(request, manifest))
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        for row in rows:
            if row["status"] == STATUS_INCLUDED:
                archive.write(reports_dir / Path(str(row["file"])).name, row["file"])
    return manifest


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Email:
    """A plain-text email this module composes but never sends.

    Sending is the workflow's job (.github/workflows/compliance-bundle.yml), which is where an
    AWS account and a verified sending domain belong; this module stays runnable with no
    account and no key.
    """

    to: str
    subject: str
    body: str


def delivery_email(
    request: BundleRequest,
    manifest: Mapping[str, Any],
    download_url: str,
    expires_on: str,
    promised_by: str = "",
) -> Email:
    """The email that carries the download link.

    Says what was included and, per id, what was not. The link is a capability that expires; the
    date is stated so nobody discovers that by clicking a dead link.
    """
    included = int(manifest["included"])
    requested = int(manifest["requested"])
    lines = [
        f"Your FHIR Scorecard compliance report bundle for {request.program_name} is ready.",
        "",
        f"Download (valid until {expires_on}):",
        f"  {download_url}",
        "",
    ]
    if promised_by:
        lines += [f"This order was promised by {promised_by}.", ""]
    lines += [
        f"{included} of {requested} requested endpoints are included, one self-contained HTML",
        "file each. Open any of them in a browser and print to PDF.",
    ]
    skipped = [a for a in manifest["endpoints"] if a["status"] != STATUS_INCLUDED]
    if skipped:
        lines += ["", "Not included:"]
        lines += [f"  {a['id']}: {a['detail']}" for a in skipped]
    if request.cadence == "quarterly":
        lines += [
            "",
            "This bundle refreshes quarterly. Each refresh arrives at this address with a new",
            "link; manage or cancel the subscription from the receipt Stripe sent you.",
        ]
    lines += [
        "",
        "Every number comes from the endpoint's published scorecard. The generator computes",
        "nothing new, and the free evidence page for one endpoint is the same document. No",
        "patient data is ever accessed to produce any report this project publishes. Purchase",
        "buys no influence over grades, methodology, or listing.",
        "",
        "Questions or a wrong id: reply to this email.",
        "",
    ]
    return Email(
        to=request.deliver_to,
        subject=f"Compliance report bundle for {request.program_name}: {included} of {requested} ready",
        body="\n".join(lines),
    )


def expires_on(generated_at: dt.datetime, days: int = DOWNLOAD_DAYS) -> str:
    """The calendar date the download link stops working, in UTC."""
    return (generated_at + dt.timedelta(days=days)).date().isoformat()
