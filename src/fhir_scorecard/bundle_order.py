"""The fulfillment workflow's side of one stored order (``.github/workflows/compliance-bundle.yml``).

The setup Lambda writes each paid, validated order to the private artifacts bucket as JSON and
dispatches the workflow with nothing but a random reference to it (infra/compliance-bundle,
``common.store_request``). This module is everything the workflow does with that JSON once it
has it, as four small commands, so that no step has to put a buyer's value into a shell script
or an environment block -- both of which a public repository's run log prints:

``mask REQUEST``
    Print an ``::add-mask::`` line for every value in the order that says something about the
    buyer or grants access -- the bundle id (the download capability), the delivery address, the
    organization name, the logo address, and each endpoint id -- and only then validate it.
    GitHub replaces every later occurrence of a masked value in the log with ``***``, so an
    error message that happens to quote one stays blank.
``cap REQUEST``
    Print the endpoint cap the order was sold with, for ``fhir-scorecard bundle
    --max-endpoints``: the archive is held to what was paid for a second time, not only by the
    setup route.
``archive-key REQUEST``
    Print where the archive goes in the bucket (``bundle.archive_key``).
``email REQUEST MANIFEST``
    Send the delivery email through SES, from ``SES_FROM``, with the download link under
    ``BUNDLE_API_BASE``.

Every command re-validates the order with :func:`bundle.parse_request` before it uses it, so an
object that is not a well-formed order fails the run instead of building something.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from fhir_scorecard.bundle import (
    MAX_ENDPOINTS,
    BundleError,
    BundleRequest,
    archive_key,
    delivery_email,
    expires_on,
    parse_request,
)

#: Sends one email. The workflow's is SES; a test passes its own.
Sender = Callable[[str, str, str, str], None]


def read_order(path: Path) -> dict[str, Any]:
    """The stored object as it is, unvalidated. Raises BundleError, naming no value, when it is
    not a JSON object."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise BundleError(f"the stored order could not be read: {type(err).__name__}") from err
    if not isinstance(raw, dict):
        raise BundleError("the stored order is not a JSON object")
    return raw


def load_order(path: Path) -> tuple[BundleRequest, dict[str, Any]]:
    """The validated request and the raw stored object (which also carries ``promised_by`` and
    ``max_endpoints``). Raises BundleError on anything that is not a well-formed order."""
    raw = read_order(path)
    return parse_request(raw, max_endpoints=order_cap(raw)), raw


def order_cap(raw: dict[str, Any]) -> int:
    """The endpoint cap the order was sold with, never more than the widest plan's. An order
    stored without one is held to the widest cap, which is what the setup route already
    enforced before it stored the order."""
    cap = raw.get("max_endpoints")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        return MAX_ENDPOINTS
    return min(cap, MAX_ENDPOINTS)


#: The stored fields whose values identify the buyer or grant access. ``accent``, ``cadence``,
#: ``promised_by`` and ``max_endpoints`` say nothing about who bought what.
MASKED_FIELDS = ("bundle_id", "deliver_to", "program_name", "logo")


def mask_lines(raw: dict[str, Any]) -> list[str]:
    """One ``::add-mask::`` workflow command per sensitive value in a stored order, read from the
    raw object *before* it is validated, so that a validation error quoting one of them is
    already masked. Longest first, so a value that contains another is masked whole. A data:
    URI logo is image bytes, never printed, and not masked."""
    values: set[str] = set()
    for field in MASKED_FIELDS:
        value = raw.get(field)
        if isinstance(value, str) and not value.startswith("data:"):
            values.add(value.strip())
    ids = raw.get("endpoint_ids")
    parts = ids.replace("\n", ",").split(",") if isinstance(ids, str) else ids
    if isinstance(parts, list):
        values.update(str(part).strip() for part in parts)
    return [
        f"::add-mask::{value}"
        for value in sorted(values, key=lambda v: (-len(v), v))
        if value and "\n" not in value and "\r" not in value
    ]


def download_url(api_base: str, bundle_id: str) -> str:
    if not api_base.startswith("https://"):
        raise BundleError("BUNDLE_API_BASE must be an https URL")
    return f"{api_base.rstrip('/')}/download/{bundle_id}"


def send_with_ses(source: str, to: str, subject: str, body: str) -> None:  # pragma: no cover
    """The workflow's sender: one plain-text email through SES. Only reachable with AWS
    credentials, so it is exercised by the owner's test-mode purchase, not by the suite."""
    import boto3  # type: ignore[import-not-found,import-untyped,unused-ignore]

    ses = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "us-west-2"))
    ses.send_email(
        Source=source,
        Destination={"ToAddresses": [to]},
        Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
    )


def send_delivery_email(
    request_path: Path,
    manifest_path: Path,
    *,
    api_base: str,
    source: str,
    send: Sender,
    now: dt.datetime | None = None,
) -> None:
    request, raw = load_order(request_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not source or "@" not in source:
        raise BundleError("SES_FROM must be an email address")
    email = delivery_email(
        request,
        manifest,
        download_url=download_url(api_base, request.bundle_id),
        expires_on=expires_on(now or dt.datetime.now(dt.UTC)),
        promised_by=str(raw.get("promised_by") or ""),
    )
    send(source, email.to, email.subject, email.body)


def main(argv: Sequence[str] | None = None, *, send: Sender = send_with_ses) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else ""
    try:
        if command == "mask" and len(args) == 2:
            raw = read_order(Path(args[1]))
            # Flushed before validating: the runner registers a mask when it reads the line, and
            # stdout is block-buffered in a pipe, so an unflushed mask could reach the log after
            # the stderr error it was meant to cover.
            print("\n".join(mask_lines(raw)), flush=True)
            load_order(Path(args[1]))
            return 0
        if command == "cap" and len(args) == 2:
            _, raw = load_order(Path(args[1]))
            print(order_cap(raw))
            return 0
        if command == "archive-key" and len(args) == 2:
            request, _ = load_order(Path(args[1]))
            print(archive_key(request.bundle_id))
            return 0
        if command == "email" and len(args) == 3:
            send_delivery_email(
                Path(args[1]),
                Path(args[2]),
                api_base=os.environ.get("BUNDLE_API_BASE", ""),
                source=os.environ.get("SES_FROM", ""),
                send=send,
            )
            print("the download link was sent")
            return 0
    except BundleError as err:
        # Any buyer value this message quotes was masked by the `mask` command, which runs
        # first in the workflow and masks before it validates.
        print(f"order error: {err}", file=sys.stderr)
        return 2
    print(
        "usage: python -m fhir_scorecard.bundle_order "
        "(mask|cap|archive-key) REQUEST | email REQUEST MANIFEST",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
