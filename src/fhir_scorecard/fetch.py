"""Polite, HTTPS-only fetcher for public FHIR discovery surfaces.

One request per resource per run, an identifying User-Agent with a contact address, and
conservative timeouts. The opener is injectable so tests never touch the network.

**The probe contract is enforced here or nowhere.** README.md, SECURITY.md and the site all
promise that this project never authenticates, never requests patient data, and never probes
beyond ``/metadata`` and ``/.well-known/smart-configuration``. Those sentences used to be
guaranteed by nothing but the absence of code that broke them, and the absence was not real: a
stock :func:`urllib.request.build_opener` carries :class:`urllib.request.HTTPRedirectHandler`,
which follows a ``Location`` anywhere the server names. A server answering ``/metadata`` with
``302 Location: /Patient?_count=50`` would have had that request made and its body read, stored
as the CapabilityStatement, and uploaded as a probe artifact. The same handler follows an
``https`` to ``http`` downgrade, so the "HTTPS is enforced before any connection is attempted"
promise below held for the first hop only, and it copies every request header onto the new hop.

:class:`DiscoveryRedirectHandler` makes the contract a thing the code refuses to break rather
than a thing it happens not to do. ``tests/test_probe_contract.py`` drives the real handler chain
against a real socket and fails if any of it regresses.
"""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from http.client import HTTPMessage
from typing import IO
from urllib.parse import urlsplit

USER_AGENT = (
    "fhir-scorecard/0.1 (+https://github.com/ChelseaKR/fhir-scorecard; "
    "observational scorecard of public FHIR discovery surfaces; contact: ckellyreif@gmail.com)"
)
TIMEOUT_S = 15.0
MAX_BODY_BYTES = 5_000_000

#: The only two paths this project ever asks a server for. Both are unauthenticated discovery
#: documents that FHIR R4 and SMART App Launch require a server to publish; neither can return
#: patient data. A request for anything else, including one a server asks for by redirect, is
#: outside the published probe contract.
DISCOVERY_PATHS = ("/metadata", "/.well-known/smart-configuration")

#: Redirect hops allowed before giving up. urllib's default is 10, which would let a server turn
#: one probe into eleven requests. This project asks each endpoint for two documents per run, and
#: a redirect the server itself sends costs another GET on top, so this bound is what keeps the
#: worst case an operator can see small and statable: at most four requests per document, eight
#: per endpoint per run, which is the number SECURITY.md publishes. Changing it changes a promise
#: made to the servers being measured, so `tests/test_probe_contract.py` requires the two to
#: agree.
MAX_REDIRECTS = 3


@dataclass(frozen=True)
class FetchResult:
    url: str
    ok: bool
    status: int | None
    elapsed_ms: int
    body: bytes
    error: str | None


def is_discovery_url(url: str) -> bool:
    """Whether ``url`` is one of the two public discovery documents, over HTTPS.

    Scheme and path only. The host is deliberately not constrained: a payer moving its FHIR
    service behind a different hostname is ordinary, and the promise this enforces is about
    *what* is requested, not where it is served from.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    path = parts.path.rstrip("/").casefold()
    return path.endswith(DISCOVERY_PATHS)


class RedirectRefused(urllib.error.HTTPError):
    """A redirect this project will not follow, carrying why in plain language.

    Raised from :meth:`DiscoveryRedirectHandler.redirect_request`, which is the mechanism urllib
    documents for declining a redirect, so it surfaces out of ``opener.open`` as an ordinary
    :class:`urllib.error.HTTPError` and needs no special handling from callers other than
    :func:`fetch_json`, which reports the reason instead of a bare status.
    """

    def __init__(self, target: str, code: int, reason: str, headers: Message, fp: IO[bytes]):
        self.refusal = reason
        super().__init__(target, code, reason, headers, fp)


class DiscoveryRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only if it still points at a public discovery document over HTTPS.

    Two refusals, and each one is a promise this project publishes:

    * **Not HTTPS.** The stock handler accepts ``http`` and ``ftp`` targets, so an ``https``
      probe could be walked onto a plaintext hop carrying every header the first hop sent.
    * **Not a discovery path.** ``/metadata`` redirecting to ``/Patient``, to a search bundle, or
      to an OAuth authorize endpoint is a request this project promises never to make. Refusing
      it means the run records that it did not retrieve the document, which is true, rather than
      grading whatever the server pointed it at.

    A refusal is not a finding against the endpoint. It surfaces as a retrieval error, the same
    as a timeout, and grading treats it the same way: nothing was observed, so nothing is scored.
    """

    max_redirections = MAX_REDIRECTS

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if urlsplit(newurl).scheme != "https":
            raise RedirectRefused(
                newurl,
                code,
                f"redirect to a non-https URL refused ({newurl}); this project probes over "
                "HTTPS only, on every hop",
                headers,
                fp,
            )
        if not is_discovery_url(newurl):
            raise RedirectRefused(
                newurl,
                code,
                f"redirect off the discovery surface refused ({newurl}); this project requests "
                f"only {' and '.join(DISCOVERY_PATHS)} and never follows a server anywhere else",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_default_opener() -> urllib.request.OpenerDirector:
    """The opener :func:`fetch_json` uses when the caller injects none.

    ``build_opener`` replaces the stock handler of a given class with any instance passed to it,
    so this is the default chain with :class:`urllib.request.HTTPRedirectHandler` swapped out.
    Nothing that could authenticate is added: no cookie processor, no basic or digest auth
    handler, no password manager. There is nothing for such a handler to send, and the point is
    that there is also nowhere for one to appear by accident.
    """
    return urllib.request.build_opener(DiscoveryRedirectHandler())


def fetch_json(
    url: str,
    *,
    opener: urllib.request.OpenerDirector | None = None,
    timeout: float = TIMEOUT_S,
) -> FetchResult:
    """Fetch one URL. HTTPS is enforced before any connection is attempted (fail closed).

    Enforced on every hop, not just the first: see :class:`DiscoveryRedirectHandler`.
    """
    if not url.startswith("https://"):
        return FetchResult(
            url=url, ok=False, status=None, elapsed_ms=0, body=b"", error="non-https URL refused"
        )
    request = urllib.request.Request(  # noqa: S310 - scheme enforced to https above
        url,
        headers={
            "Accept": "application/fhir+json, application/json;q=0.9",
            "User-Agent": USER_AGENT,
        },
    )
    op = opener if opener is not None else build_default_opener()
    started = time.monotonic()
    try:
        with op.open(request, timeout=timeout) as response:
            body = response.read(MAX_BODY_BYTES)
            elapsed = int((time.monotonic() - started) * 1000)
            status = int(response.status)
            return FetchResult(
                url=url,
                ok=200 <= status < 300,
                status=status,
                elapsed_ms=elapsed,
                body=bytes(body),
                error=None,
            )
    except RedirectRefused as exc:
        # Reported as a retrieval failure, with the reason, so the run says what it declined to
        # do rather than publishing a document it fetched from somewhere it promised not to go.
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchResult(
            url=url, ok=False, status=None, elapsed_ms=elapsed, body=b"", error=exc.refusal
        )
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchResult(
            url=url,
            ok=False,
            status=int(exc.code),
            elapsed_ms=elapsed,
            body=b"",
            error=f"HTTP {exc.code}",
        )
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchResult(
            url=url, ok=False, status=None, elapsed_ms=elapsed, body=b"", error=describe_error(exc)
        )


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
        return (
            f"TLS certificate verification failed ({reason.verify_message or reason.reason}); "
            "likely a vantage-local interception, not an endpoint fault"
        )
    if isinstance(reason, ssl.SSLError):
        return f"TLS error: {type(reason).__name__}"
    if isinstance(reason, socket.gaierror):
        return f"DNS did not resolve ({reason.strerror or 'gaierror'})"
    if isinstance(reason, TimeoutError):
        return "connection timed out"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return (
            f"TLS certificate verification failed ({exc.verify_message or exc.reason}); "
            "likely a vantage-local interception, not an endpoint fault"
        )
    if isinstance(exc, socket.gaierror):
        return f"DNS did not resolve ({exc.strerror or 'gaierror'})"
    if isinstance(exc, TimeoutError):
        return "connection timed out"
    if reason is not None:
        return f"{type(exc).__name__}: {reason}"
    return type(exc).__name__
