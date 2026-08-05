"""Polite, HTTPS-only fetcher for public FHIR discovery surfaces.

One request per resource per run, an identifying User-Agent with a contact address, and
conservative timeouts. The opener is injectable so tests never touch the network.
"""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

USER_AGENT = (
    "fhir-scorecard/0.1 (+https://github.com/ChelseaKR/fhir-scorecard; "
    "observational scorecard of public FHIR discovery surfaces; contact: ckellyreif@gmail.com)"
)
TIMEOUT_S = 15.0
MAX_BODY_BYTES = 5_000_000


@dataclass(frozen=True)
class FetchResult:
    url: str
    ok: bool
    status: int | None
    elapsed_ms: int
    body: bytes
    error: str | None


def fetch_json(
    url: str,
    *,
    opener: urllib.request.OpenerDirector | None = None,
    timeout: float = TIMEOUT_S,
) -> FetchResult:
    """Fetch one URL. HTTPS is enforced before any connection is attempted (fail closed)."""
    if not url.startswith("https://"):
        return FetchResult(url=url, ok=False, status=None, elapsed_ms=0, body=b"",
                           error="non-https URL refused")
    request = urllib.request.Request(  # noqa: S310 - scheme enforced to https above
        url,
        headers={
            "Accept": "application/fhir+json, application/json;q=0.9",
            "User-Agent": USER_AGENT,
        },
    )
    op = opener if opener is not None else urllib.request.build_opener()
    started = time.monotonic()
    try:
        with op.open(request, timeout=timeout) as response:
            body = response.read(MAX_BODY_BYTES)
            elapsed = int((time.monotonic() - started) * 1000)
            status = int(response.status)
            return FetchResult(url=url, ok=200 <= status < 300, status=status,
                               elapsed_ms=elapsed, body=bytes(body), error=None)
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchResult(url=url, ok=False, status=int(exc.code), elapsed_ms=elapsed,
                           body=b"", error=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchResult(url=url, ok=False, status=None, elapsed_ms=elapsed,
                           body=b"", error=describe_error(exc))


def describe_error(exc: BaseException) -> str:
    """Name the cause, not just the exception class.

    Bare ``URLError`` conflates three very different things: a host that does not exist, a host
    this vantage cannot reach, and a TLS handshake this vantage rejects. On 2026-08-05 a live
    payer endpoint (Capital Blue Cross, HTTP 415 under curl, a full CapabilityStatement from CI)
    was recorded as dead because a TLS-intercepting middlebox on the probing network produced a
    certificate error that surfaced only as "URLError". A rejection log is worth nothing if it
    cannot distinguish "does not exist" from "I could not get there from here".
    """
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (f"TLS certificate verification failed ({reason.verify_message or reason.reason}); "
                "likely a vantage-local interception, not an endpoint fault")
    if isinstance(reason, ssl.SSLError):
        return f"TLS error: {type(reason).__name__}"
    if isinstance(reason, socket.gaierror):
        return f"DNS did not resolve ({reason.strerror or 'gaierror'})"
    if isinstance(reason, TimeoutError):
        return "connection timed out"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return (f"TLS certificate verification failed ({exc.verify_message or exc.reason}); "
                "likely a vantage-local interception, not an endpoint fault")
    if isinstance(exc, socket.gaierror):
        return f"DNS did not resolve ({exc.strerror or 'gaierror'})"
    if isinstance(exc, TimeoutError):
        return "connection timed out"
    if reason is not None:
        return f"{type(exc).__name__}: {reason}"
    return type(exc).__name__
