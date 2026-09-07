"""Polite, HTTPS-only fetcher for public FHIR discovery surfaces.

Two documents per endpoint per run - and at most four requests per document, because a
redirect the server sends costs another GET (see :data:`MAX_REDIRECTS`) - with an identifying
User-Agent carrying a contact address, and conservative timeouts. The opener is injectable so
tests never touch the network.

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

import http.client
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

#: Largest discovery document this project will accept. It bounds the memory one hostile or
#: broken server can make a run allocate; a CapabilityStatement is a conformance declaration, and
#: five megabytes is far above the largest one in the registry.
#:
#: Reaching it is a *retrieval failure*, not a truncation. ``HTTPResponse.read(amt)`` returns
#: exactly ``amt`` bytes when the body is longer, so reading ``MAX_BODY_BYTES`` of an oversized
#: document handed grading a fragment cut mid-JSON, indistinguishable from a document the server
#: got wrong: the endpoint scored ``T0``/``I0`` and published an ``F`` -- a statement about a
#: named payer that was really a fact about this project's read limit. So the read asks for one
#: byte more than the cap and fails closed when it gets it, and the endpoint routes to *not
#: observed* like any other document this project could not retrieve. SECURITY.md publishes the
#: number under "Known limits", and ``tests/test_probe_contract.py`` requires the two to agree.
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


#: The closed vocabulary a retrieval failure is classified into (#117).
#:
#: :func:`describe_error` has always told a hostname that does not resolve apart from a TLS
#: certificate that does not verify, a timeout, a refused connection and a refused redirect --
#: and then flattened all of it into one free-text sentence and one boolean. So "the payer gated
#: this behind registration" and "the public record for this payer is broken" published
#: identically, and nothing could count either population.
#:
#: The sentence is evidence and stays exactly as it was. The kind is data, and it is *only* data:
#: nothing here says which of those two conditions is worse, or whether a payer choosing to
#: require credentials is a finding. `data/CANDIDATES.md` and `docs/SAMPLING-FRAME.md` §4
#: currently disagree about that for a 401, and settling it is the owner's, not this module's.
#:
#: ``unclassified`` is a real member of the vocabulary and is published as itself. A classifier
#: that quietly files an unrecognised failure under the nearest label is the thing this exists to
#: remove, so anything not listed here lands there rather than near something.
FAILURE_KINDS: tuple[str, ...] = (
    "authentication_required",
    "forbidden",
    "not_found",
    "dns",
    "tls",
    "timeout",
    "connection_refused",
    "server_error",
    "redirect_refused",
    "unclassified",
)

#: What an unrecognised condition is called. Named rather than repeated as a literal, because
#: every fall-through in this module has to land on the same value or the population splits.
UNCLASSIFIED = "unclassified"


def failure_kind_for_status(status: int) -> str:
    """Classify an HTTP status this run received but could not use.

    Only the four statuses the vocabulary names. A 415, a 429 or a 400 is an answer this project
    does not have a label for, and it gets ``unclassified`` rather than the nearest-looking one:
    ``forbidden`` is a specific claim about access control, and a 429 is a rate limit. The status
    itself is carried alongside, so an unclassified answer is still readable as the number it was.
    """
    if status == 401:
        return "authentication_required"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if 500 <= status < 600:
        return "server_error"
    return UNCLASSIFIED


def normalise_failure_kind(value: object) -> str:
    """Read a failure kind that came from outside this process.

    Probe files are written by vantages this project does not operate (#100), so a kind arriving
    in one is an untrusted string. Anything that is not a member of the published vocabulary --
    absent, ``null``, a number, or a plausible-looking label somebody invented -- reads as
    ``unclassified``, which is what it is: this run has no classification for that failure.

    That is deliberately *not* the shape :func:`fhir_scorecard.vantage.probe_entry_failure`
    refuses for ``elapsed_ms`` and ``reachable``. Those two coercions turned an absent
    measurement into the most flattering possible reading of it. An absent kind has no flattering
    reading: ``unclassified`` is strictly less than any label, it can never lift a grade, and it
    is the honest third state beside "gated" and "broken" rather than a silent default into one.
    """
    return value if isinstance(value, str) and value in FAILURE_KINDS else UNCLASSIFIED


@dataclass(frozen=True)
class FetchResult:
    url: str
    ok: bool
    status: int | None
    elapsed_ms: int
    body: bytes
    error: str | None
    #: Which named condition stopped this retrieval, or ``None`` when nothing did. Set on every
    #: result that is not ``ok``; a successful fetch has no failure to classify and carries None
    #: rather than a placeholder, so "no failure" and "a failure nobody classified" stay apart.
    failure_kind: str | None = None


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
            # One byte over the cap, so an oversized body is detectable rather than silently
            # cut: `read(MAX_BODY_BYTES)` alone returns a full buffer both when the document
            # ends exactly at the cap and when it runs far past it.
            body = response.read(MAX_BODY_BYTES + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            status = int(response.status)
            if len(body) > MAX_BODY_BYTES:
                # A document too large to read is a document this run did not retrieve. Grading
                # a fragment of it would publish this project's read limit as a finding about
                # the endpoint.
                return FetchResult(
                    url=url,
                    ok=False,
                    status=status,
                    elapsed_ms=elapsed,
                    body=b"",
                    error=f"response exceeds the {MAX_BODY_BYTES} byte read limit",
                    # Not a condition of the endpoint: the server answered, and this project
                    # declined the answer. There is no label in the vocabulary for "our own read
                    # limit", and inventing one would put a fact about this tool into a
                    # population that counts facts about payers.
                    failure_kind=UNCLASSIFIED,
                )
            ok = 200 <= status < 300
            return FetchResult(
                url=url,
                ok=ok,
                status=status,
                elapsed_ms=elapsed,
                body=bytes(body),
                error=None,
                # urllib raises for most non-2xx, so this is the narrow case where a response
                # arrives here without being ok -- a 3xx the handler declined to follow, say.
                # It is still a status this run could not use, and it is classified as one
                # rather than left None, which would read as "no failure occurred".
                failure_kind=None if ok else failure_kind_for_status(status),
            )
    except RedirectRefused as exc:
        # Reported as a retrieval failure, with the reason, so the run says what it declined to
        # do rather than publishing a document it fetched from somewhere it promised not to go.
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchResult(
            url=url,
            ok=False,
            status=None,
            elapsed_ms=elapsed,
            body=b"",
            error=exc.refusal,
            failure_kind="redirect_refused",
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
            failure_kind=failure_kind_for_status(int(exc.code)),
        )
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        kind, sentence = classify_error(exc)
        return FetchResult(
            url=url,
            ok=False,
            status=None,
            elapsed_ms=elapsed,
            body=b"",
            error=sentence,
            failure_kind=kind,
        )
    except (http.client.HTTPException, ValueError) as exc:
        # A base URL urllib refuses to turn into a request at all. These do not descend from
        # OSError: `http.client.InvalidURL` is an HTTPException, and a non-latin-1 hostname
        # raises UnicodeEncodeError, so both escaped the clause above and propagated out of the
        # grading loop. Measured against four shapes a registry could hold - a control character
        # or space in the host, a non-numeric port, and a zero-width space - every one of them
        # ended the whole run before any observation was saved.
        #
        # It is a retrieval failure like any other: this endpoint could not be asked, and the
        # other forty-four still can be.
        elapsed = int((time.monotonic() - started) * 1000)
        return FetchResult(
            url=url,
            ok=False,
            status=None,
            elapsed_ms=elapsed,
            body=b"",
            error=f"malformed URL: {exc}",
            # A registry row this project cannot turn into a request at all. That is a defect in
            # this repository's own data, not a condition of any server, so it gets no label.
            failure_kind=UNCLASSIFIED,
        )


def _verify_message(exc: ssl.SSLCertVerificationError) -> str:
    """What OpenSSL said about the certificate, without assuming it said anything.

    ``verify_message`` and ``reason`` are both set by the ssl module when *it* raises, so the
    production path has always had them, and no run has been observed to hit this. They are
    absent on an instance constructed any other way, and reading them as plain attributes meant
    this function -- which only ever runs inside an ``except`` block, describing a failure --
    could raise ``AttributeError`` out of the handler and end the run for every remaining
    endpoint. Noticed while giving the classifier a fixture (#117). Defence rather than a fix
    for something seen in the wild, on the rule that the describing path is the last place that
    may throw.
    """
    for attribute in ("verify_message", "reason"):
        value = getattr(exc, attribute, None)
        if value:
            return str(value)
    return type(exc).__name__


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Name the cause once, as both a sentence and a kind.

    Bare ``URLError`` conflates three very different things: a host that does not exist, a host
    this vantage cannot reach, and a TLS handshake this vantage rejects. On 2026-08-05 a live
    payer endpoint (Capital Blue Cross, HTTP 415 under curl, a full CapabilityStatement from CI)
    was recorded as dead because a TLS-intercepting middlebox on the probing network produced a
    certificate error that surfaced only as "URLError". A rejection log is worth nothing if it
    cannot distinguish "does not exist" from "I could not get there from here".

    One ladder returning both, rather than a classifier beside the describer. Two ladders over
    the same exception hierarchy would agree on the day they were written and drift apart on the
    first branch somebody added to one of them, and the failure would be silent: a sentence
    saying "DNS did not resolve" beside a kind saying ``unclassified``, each defensible alone.
    ``tests/test_failure_kinds.py`` asserts every branch here yields both halves.
    """
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "tls", (
            f"TLS certificate verification failed ({_verify_message(reason)}); "
            "likely a vantage-local interception, not an endpoint fault"
        )
    if isinstance(reason, ssl.SSLError):
        return "tls", f"TLS error: {type(reason).__name__}"
    if isinstance(reason, socket.gaierror):
        return "dns", f"DNS did not resolve ({reason.strerror or 'gaierror'})"
    if isinstance(reason, TimeoutError):
        return "timeout", "connection timed out"
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused", "connection refused"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "tls", (
            f"TLS certificate verification failed ({_verify_message(exc)}); "
            "likely a vantage-local interception, not an endpoint fault"
        )
    if isinstance(exc, socket.gaierror):
        return "dns", f"DNS did not resolve ({exc.strerror or 'gaierror'})"
    if isinstance(exc, TimeoutError):
        return "timeout", "connection timed out"
    if reason is not None:
        return UNCLASSIFIED, f"{type(exc).__name__}: {reason}"
    return UNCLASSIFIED, type(exc).__name__


def describe_error(exc: BaseException) -> str:
    """The sentence half of :func:`classify_error`, byte-for-byte what it always returned.

    Kept as its own name because the sentence is evidence and is quoted verbatim into R1's
    message, the vantage consensus detail and the rejection log. Adding a kind alongside it must
    not change a single published word, and this signature is what makes that checkable.
    """
    return classify_error(exc)[1]
