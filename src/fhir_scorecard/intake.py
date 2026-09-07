"""Read an add-endpoint claim, verify what can be verified, and propose nothing else.

``.github/ISSUE_TEMPLATE/add-endpoint.yml`` has collected exactly what a registry entry needs
since phase 3 -- organization, base URL, category, FHIR release, and the public page where the
organization publishes that base URL -- and nothing has ever read it. Every submission has been
processed by hand (#118).

This is the offline half of that intake: ``fhir-scorecard claim`` takes an issue form body,
refuses what must be refused *before any request is made*, retrieves the same two discovery
documents every other verb retrieves, and writes a proposal file plus the text of a comment.
It edits no registry, opens no pull request, and merges nothing.

Four rules, and each one is load-bearing.

**Nothing is fetched until the address has been proved public.** Every other fetch in this
project starts from a URL a person curated. This one starts from a URL an unauthenticated
stranger typed into a form, which makes it a request-forgery surface: a submission naming
``https://localhost:8080/admin`` or an address inside a runner's cloud metadata range would
otherwise have this project make that request from inside its own network and print what came
back. So the scheme, the userinfo, the path and **every address the host resolves to** are
checked first, the check fails closed on a name that will not resolve, and
``tests/test_intake.py`` drives the whole verb with a fetcher that raises if it is called at all.

**The one page this verb does not read is the one the form exists to collect.** The submitter's
documentation URL is the evidence that the publisher is who the claim says. Reading it means
retrieving a page that is not one of the two discovery documents, which is a different activity
from the one README.md, SECURITY.md and the site all describe, and whether this project may do
it is an open decision recorded on #118. Until it is settled the URL is carried through
untouched and recorded as **not retrieved**, in those words, in both the proposal and the
comment. It is never described as checked, and no proposed ``verification`` block ever rests on
it.

**A claim that observed no document proposes nothing at all.** ``registry`` allows a second
basis, ``publisher_documented``, for an entry nobody could retrieve -- and that basis is
defined by the documentation page, which is precisely what this verb does not read. So an
unretrievable claim cannot honestly reach either basis, and it produces a refusal naming what
happened rather than a half-filled entry. This is ``reverify``'s rule one layer earlier: a date
nobody earned is never written, and here an entry nobody observed is never proposed.

**A document that does not repeat the organization's name is not an accusation.** It is the
ordinary shape of a vendor-hosted multi-tenant platform, and ``CONTRIBUTING.md`` says so. Such
a claim yields a proposal *flagged for attribution review* with both readings recorded, not a
rejection and not an entry -- the same vocabulary ``reverify`` settled on, reusing the same
comparison rather than a second one that could drift from it.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fhir_scorecard.capability import parse_capability
from fhir_scorecard.fetch import FetchResult, fetch_json
from fhir_scorecard.registry import Endpoint
from fhir_scorecard.reverify import Observed, names_the_entry, observed_from

_PROPOSAL_SCHEMA = "fhir-scorecard/claim-proposal/v1"

#: What a claim can be refused for, before or after retrieval. Closed, like ``FAILURE_KINDS``,
#: and for the same reason: a refusal that is not in a named vocabulary cannot be counted, and a
#: reason invented at the call site is a reason nobody can search ``data/CANDIDATES.md`` for.
#:
#: There is no ``unclassified`` member here and that difference is deliberate. ``FAILURE_KINDS``
#: classifies what a third-party server did, which this project cannot enumerate; these classify
#: what *this code* decided, which it can. A refusal reaching the end of this list is a bug in
#: this module, and :func:`refuse` raises rather than inventing a label.
REFUSALS: tuple[str, ...] = (
    "form_incomplete",
    "unknown_category",
    "unknown_release",
    "not_https",
    "credentials_in_url",
    "not_a_base_url",
    "host_not_public",
    "host_unresolvable",
    "already_registered",
    "not_observed",
    "not_a_capability_statement",
)

#: Outcomes that carry a proposal.
ATTRIBUTION_CONFIRMED = "attribution_confirmed"
ATTRIBUTION_REVIEW = "attribution_review"
#: Outcome that carries none.
REFUSED = "refused"

OUTCOMES = (ATTRIBUTION_CONFIRMED, ATTRIBUTION_REVIEW, REFUSED)

#: The form's category labels, mapped to ``registry.KINDS``. Held here rather than in the
#: template because the template is prose a person reads and this is the contract a parser
#: needs; :func:`category_labels` and a test keep the two from drifting apart.
CATEGORIES: dict[str, str] = {
    "Payer Patient Access API": "payer",
    "Payer Provider Directory API": "payer_provider_directory",
    "Provider or health system API": "provider",
    "EHR vendor sandbox": "ehr",
    "Reference or test server": "reference",
}

#: The form's release labels, mapped to ``registry``'s ``expects``.
RELEASES: dict[str, str] = {"R4": "r4", "R5": "r5", "STU3": "stu3"}

#: Longest value this verb will take from a form field. The form is unauthenticated and its
#: values end up inside a JSON file and a Markdown comment; the same bound and the same reason
#: as ``reverify._MAX_QUOTED``.
_MAX_FIELD = 300

#: GitHub renders an issue form as ``### <label>`` followed by the value. A field the submitter
#: left blank on an optional input renders as ``_No response_``.
_HEADING = re.compile(r"^###[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_NO_RESPONSE = "_No response_"

_SLUG_SPLIT = re.compile(r"[^a-z0-9]+")


class ClaimError(ValueError):
    """A claim could not be read at all -- a malformed body, not a refused submission."""


def category_labels() -> tuple[str, ...]:
    return tuple(CATEGORIES)


def release_labels() -> tuple[str, ...]:
    return tuple(RELEASES)


def _clean(value: str) -> str:
    """A submitted string, bounded and stripped of anything that could restructure text."""
    cleaned = "".join(" " if ch < " " or ch == "\x7f" else ch for ch in value)
    cleaned = cleaned.replace("`", "'").strip()
    if len(cleaned) > _MAX_FIELD:
        cleaned = cleaned[:_MAX_FIELD] + "..."
    return cleaned


def parse_form(body: str) -> dict[str, str]:
    """Split a rendered issue-form body into ``{label: value}``.

    Only the shape GitHub actually emits: a level-three heading per field, the value in the
    lines beneath it, and ``_No response_`` for a blank optional input. A heading with nothing
    under it and a heading whose value is ``_No response_`` both come back as ``""``, because
    "the submitter typed nothing" is one state and not two.
    """
    fields: dict[str, str] = {}
    matches = list(_HEADING.finditer(body))
    for i, match in enumerate(matches):
        label = _clean(match.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        raw = body[match.end() : end].strip()
        if raw == _NO_RESPONSE:
            raw = ""
        fields[label] = _clean(raw.splitlines()[0] if raw else "")
    return fields


@dataclass(frozen=True)
class Claim:
    """What the submitter said. No judgement of any kind, and nothing retrieved."""

    organization: str
    base_url: str
    category: str
    release: str
    documentation: str
    context: str = ""

    def to_payload(self) -> dict[str, str]:
        return {
            "organization": self.organization,
            "base_url": self.base_url,
            "category": self.category,
            "release": self.release,
            "documentation": self.documentation,
            "context": self.context,
        }


def claim_from_form(body: str) -> Claim:
    """Read a rendered add-endpoint form. Raises :class:`ClaimError` if it is not one."""
    fields = parse_form(body)
    if "Organization" not in fields or "FHIR base URL" not in fields:
        raise ClaimError(
            "this does not look like an add-endpoint submission: no 'Organization' and "
            "'FHIR base URL' headings were found"
        )
    return Claim(
        organization=fields.get("Organization", ""),
        base_url=fields.get("FHIR base URL", ""),
        category=fields.get("Category", ""),
        release=fields.get("FHIR release", ""),
        documentation=fields.get("Public documentation URL", ""),
        context=fields.get("Anything else", ""),
    )


@dataclass(frozen=True)
class Verdict:
    """One claim, read and -- when the address allowed it -- retrieved."""

    claim: Claim
    outcome: str
    reason: str
    refusal: str = ""
    """Which member of :data:`REFUSALS` stopped this claim, or ``""`` when nothing did."""
    requested: tuple[str, ...] = ()
    """Every URL this run asked for, in order. Empty when the address was refused first.

    Published rather than merely tested. The claim that this verb retrieves two discovery
    documents and no third page is the whole of its boundary, and a reader of the proposal can
    check it against this list instead of taking the prose for it.
    """
    observed: Observed = field(default_factory=Observed)
    entry: dict[str, object] | None = None
    """The proposed ``data/registry.json`` entry, or ``None``.

    ``None`` for every refused claim, including one whose server did not answer. There is no
    honest entry to write for a document nobody retrieved: ``live_capability`` would name a
    fetch that failed, and ``publisher_documented`` rests on the documentation page this verb
    deliberately does not read.
    """
    attribution: str = ""
    """Which observed element carried the organization's name, or ``""``."""

    def to_payload(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "refusal": self.refusal,
            "reason": self.reason,
            "submitted": self.claim.to_payload(),
            "requested": list(self.requested),
            "documentation": {
                "url": self.claim.documentation,
                "retrieved": False,
                "why_not": DOCUMENTATION_NOT_RETRIEVED,
            },
            "observed": self.observed.to_payload(),
            "attribution": self.attribution,
            "entry": self.entry,
        }


#: Said in one place so the proposal, the comment and the tests cannot drift into three
#: different accounts of the same abstention.
DOCUMENTATION_NOT_RETRIEVED = (
    "not retrieved. Reading it would mean requesting a page that is not one of the two "
    "discovery documents this project publishes a contract for, and whether that is in scope "
    "is an open decision recorded on issue #118. The URL is carried through unchecked; no "
    "proposed verification rests on it."
)


def refuse(claim: Claim, refusal: str, reason: str, *, requested: Sequence[str] = ()) -> Verdict:
    if refusal not in REFUSALS:
        raise ClaimError(f"{refusal!r} is not one of the named refusals: {REFUSALS}")
    return Verdict(
        claim=claim,
        outcome=REFUSED,
        refusal=refusal,
        reason=reason,
        requested=tuple(requested),
    )


# --- the address boundary, which runs before anything is requested -------------------------


def _addresses(host: str, resolve: Callable[[str], Iterable[str]]) -> list[str]:
    try:
        return [str(addr) for addr in resolve(host)]
    except OSError as exc:  # socket.gaierror is an OSError
        raise LookupError(str(exc) or "name resolution failed") from exc


def default_resolver(host: str) -> list[str]:
    """Every address ``host`` resolves to, both families."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _public(address: str) -> bool:
    """Whether one address is routable on the public internet.

    ``is_global`` is False for loopback, private, link-local, unspecified, reserved and
    multicast ranges -- which is every range a request-forgery attempt aims at, including
    ``169.254.169.254``. A string that is not an address at all is not public either: this is
    reached only for something a resolver returned, so an unparseable one is a condition to fail
    closed on rather than to reason about.
    """
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_global
    except ValueError:
        return False


def check_url_shape(claim: Claim) -> Verdict | None:
    """Refuse a base URL on its text alone. ``None`` means the host may be looked up.

    Deliberately separate from :func:`check_host`, and deliberately first: everything here is
    decidable without a resolver, so a submission refused by this function costs the project no
    DNS query and reveals nothing about a submitted name to anybody's resolver.
    """
    url = claim.base_url
    if not url:
        return refuse(claim, "form_incomplete", "the submission carries no base URL")
    if not url.startswith("https://"):
        return refuse(
            claim,
            "not_https",
            f"the base URL is not https: {url!r}. This project makes no plaintext request, so "
            f"there is nothing to check here even if the endpoint exists",
        )
    parts = urlsplit(url)
    if "@" in parts.netloc:
        return refuse(
            claim,
            "credentials_in_url",
            "the base URL carries userinfo before the host. This project never sends "
            "credentials, and a URL that embeds them is also the usual way one host is made to "
            "look like another",
        )
    if not parts.hostname:
        return refuse(claim, "not_a_base_url", f"the base URL names no host: {url!r}")
    path = parts.path.rstrip("/").casefold()
    if path.endswith("/metadata") or ".well-known" in path:
        return refuse(
            claim,
            "not_a_base_url",
            "this is a discovery document's URL, not a base URL. The form asks for the base "
            "because /metadata is appended here; submit the address with that path removed",
        )
    return None


def check_host(
    claim: Claim, host: str, *, resolve: Callable[[str], Iterable[str]] | None = None
) -> Verdict | None:
    """Refuse a host that is not on the public internet. ``None`` means it may be requested.

    The one network operation this verb performs before opening a socket to the submitted host,
    and it fails closed: a name that will not resolve is refused rather than attempted.

    ``resolve`` defaults to :func:`default_resolver` *at call time*, not as a bound default
    argument, so a test can replace the module-level resolver and have the change reach a caller
    that did not pass one. A default bound at definition time would have made the command-line
    path unreachable from a test without also reaching the network.
    """
    resolver = resolve if resolve is not None else default_resolver
    try:
        addresses = [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        try:
            addresses = _addresses(host, resolver)
        except LookupError as exc:
            return refuse(
                claim,
                "host_unresolvable",
                f"the base URL's host {host!r} did not resolve ({exc}), so nothing was "
                f"requested. A name that does not resolve is not asked for on the chance that "
                f"it might later",
            )
        if not addresses:
            return refuse(
                claim,
                "host_unresolvable",
                f"the base URL's host {host!r} resolved to no address at all",
            )
    private = sorted({addr for addr in addresses if not _public(addr)})
    if private:
        return refuse(
            claim,
            "host_not_public",
            f"the base URL's host {host!r} resolves to {', '.join(private)}, which is not a "
            f"public address. Nothing was requested. Any non-public address disqualifies the "
            f"host even when others are public, because which one a later connection would use "
            f"is not this code's to decide",
        )
    return None


def check_address(
    claim: Claim, *, resolve: Callable[[str], Iterable[str]] | None = None
) -> Verdict | None:
    """Both halves of the boundary, in order. ``None`` means the base URL may be requested."""
    shape = check_url_shape(claim)
    if shape is not None:
        return shape
    return check_host(claim, urlsplit(claim.base_url).hostname or "", resolve=resolve)


# --- the proposal ---------------------------------------------------------------------------


def slugify(value: str) -> str:
    parts = [part for part in _SLUG_SPLIT.split(value.casefold()) if part]
    slug = "-".join(parts)[:64].strip("-")
    return slug if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", slug) else ""


def suggest_id(organization: str, kind: str, taken: Iterable[str]) -> str:
    """A deterministic id suggestion. Ids in this registry are curated; this is a starting point.

    Deterministic because two runs over the same issue must produce the same bytes, and stable
    against the registry it was generated against because a suggestion that changed every time
    an unrelated entry was added would be re-reviewed for no reason.
    """
    base = slugify(organization)
    if not base:
        return ""
    if kind == "payer_provider_directory" and not base.endswith("-provider-directory"):
        base = f"{base}-provider-directory"[:64].strip("-")
    used = set(taken)
    if base not in used:
        return base
    for n in range(2, 100):
        candidate = f"{base[: 64 - len(str(n)) - 1]}-{n}"
        if candidate not in used:
            return candidate
    return ""


def _method(claim: Claim, observed: Observed, where: str, requested: Sequence[str]) -> str:
    """The ``verification.method`` sentence, saying what was read and what was not.

    It names the documentation URL as unread on purpose. ``method`` is the field a reader of
    ``data/registry.json`` trusts to say how an entry was established, and an entry whose
    method omitted the abstention would read as though the publisher had been confirmed from
    the organization's own page, which is the one thing this verb did not do.
    """
    asked = "; ".join(requested)
    if where:
        quoted = getattr(observed, where)
        found = f"the {where} {quoted!r} carries the submitted organization name"
    else:
        stated = "; ".join(observed.strings()) or "no name-bearing element"
        found = (
            f"no element repeats the submitted organization name (observed: {stated}); "
            f"attribution is unconfirmed and a person must settle it"
        )
    return (
        f"claim intake from an add-endpoint submission: live CapabilityStatement fetch of "
        f"{asked}; {found}. The submitted documentation page {claim.documentation!r} was not "
        f"retrieved (see issue #118)."
    )


def _entry_for(
    claim: Claim,
    kind: str,
    expects: str,
    observed: Observed,
    where: str,
    *,
    today: str,
    requested: Sequence[str],
    taken: Iterable[str],
) -> dict[str, object]:
    return {
        "id": suggest_id(claim.organization, kind, taken),
        "name": claim.organization,
        "kind": kind,
        "base_url": claim.base_url.rstrip("/"),
        "expects": expects,
        "verification": {
            "method": _method(claim, observed, where, requested),
            "date": today,
            # Only ever this basis. `publisher_documented` is defined by the documentation page,
            # and this verb does not read one.
            "basis": "live_capability",
        },
        "enabled": True,
    }


def assess(
    claim: Claim,
    *,
    today: str,
    registry: Sequence[Endpoint] = (),
    resolve: Callable[[str], Iterable[str]] | None = None,
    fetch: Callable[..., FetchResult] | None = None,
    timeout: float = 20.0,
) -> Verdict:
    """Read one claim, retrieve at most the two discovery documents, and propose or refuse.

    ``resolve`` and ``fetch`` default to the module-level implementations at call time rather
    than as bound defaults, for the reason :func:`check_address` gives.
    """
    fetcher = fetch if fetch is not None else fetch_json
    if not claim.organization:
        return refuse(claim, "form_incomplete", "the submission names no organization")
    if claim.category not in CATEGORIES:
        return refuse(
            claim,
            "unknown_category",
            f"category {claim.category!r} is not one the registry has: "
            f"{', '.join(category_labels())}",
        )
    if claim.release not in RELEASES:
        return refuse(
            claim,
            "unknown_release",
            f"FHIR release {claim.release!r} is not one of {', '.join(release_labels())}",
        )
    if not claim.documentation:
        return refuse(
            claim,
            "form_incomplete",
            "the submission cites no public documentation URL. It is not retrieved here, but "
            "it is what a person establishes the publisher from, and CONTRIBUTING.md refuses "
            "an entry without one",
        )
    refused = check_address(claim, resolve=resolve)
    if refused is not None:
        return refused

    base = claim.base_url.rstrip("/")
    already = [entry for entry in registry if entry.base_url.rstrip("/") == base]
    if already:
        return refuse(
            claim,
            "already_registered",
            f"this base URL is already registry entry {already[0].endpoint_id!r} "
            f"({already[0].name}). A correction to an existing entry is a different flow and "
            f"a person handles it",
        )

    kind = CATEGORIES[claim.category]
    expects = RELEASES[claim.release]
    metadata_url = f"{base}/metadata"
    smart_url = f"{base}/.well-known/smart-configuration"
    requested = (metadata_url, smart_url)

    result = fetcher(metadata_url, timeout=timeout)
    # The SMART document is retrieved because it is the second half of the contract every other
    # verb honours and because its absence is itself a fact about the endpoint. Nothing here
    # grades it, and no proposal turns on it.
    fetcher(smart_url, timeout=timeout)

    if not result.ok:
        return refuse(
            claim,
            "not_observed",
            f"the CapabilityStatement was not retrieved: "
            f"{result.error or f'HTTP {result.status}'}"
            + (f" ({result.failure_kind})" if result.failure_kind else "")
            + ". Nothing is proposed. An endpoint this project could not retrieve can only be "
            "listed on the strength of the organization's own published page, and that page is "
            "not read here",
            requested=requested,
        )
    facts = parse_capability(result.body)
    if not facts.parsed or not facts.resource_type_ok:
        return refuse(
            claim,
            "not_a_capability_statement",
            f"the base URL answered, but not with a CapabilityStatement: "
            f"{facts.parse_error or 'no resourceType CapabilityStatement'}",
            requested=requested,
        )

    observed = observed_from(facts)
    where = names_the_entry(claim.organization, observed)
    taken = [entry.endpoint_id for entry in registry]
    entry = _entry_for(
        claim,
        kind,
        expects,
        observed,
        where,
        today=today,
        requested=requested,
        taken=taken,
    )
    if where:
        return Verdict(
            claim=claim,
            outcome=ATTRIBUTION_CONFIRMED,
            reason=f"the CapabilityStatement's {where} carries the submitted organization name",
            requested=requested,
            observed=observed,
            entry=entry,
            attribution=where,
        )
    return Verdict(
        claim=claim,
        outcome=ATTRIBUTION_REVIEW,
        reason=(
            "the CapabilityStatement was retrieved and does not repeat the submitted "
            "organization name. This is the ordinary shape of a vendor-hosted multi-tenant "
            "platform and is not evidence against the claim; both readings are recorded and a "
            "person decides"
        ),
        requested=requested,
        observed=observed,
        entry=entry,
    )


def build_proposal(verdict: Verdict, *, today: str, issue: str = "") -> dict[str, object]:
    return {
        "schema": _PROPOSAL_SCHEMA,
        "generated": today,
        "issue": issue,
        "how_to_use": (
            "Nothing here has been written to data/registry.json and no pull request has been "
            "opened. Where an accepted claim belongs -- data/registry.json, or data/"
            "CANDIDATES.md until a curation wave promotes it -- is an open decision on issue "
            "#118 and this file takes no side on it. The proposed entry is offered in "
            "registry shape because that is the shape the observation has, not because that is "
            "where it goes. The submitted documentation URL was not retrieved; confirming the "
            "publisher from the organization's own page is still a person's step."
        ),
        "verdict": verdict.to_payload(),
    }


def format_comment(verdict: Verdict) -> str:
    """The comment text for the issue. Says what was asked for and what was not."""
    lines = [f"**{verdict.outcome}** — {verdict.reason}", ""]
    if verdict.requested:
        lines.append("Requested, and nothing else:")
        lines.extend(f"- `{url}`" for url in verdict.requested)
    else:
        lines.append("No request was made.")
    lines.append("")
    lines.append(
        f"Submitted documentation page `{verdict.claim.documentation or '(none)'}` — "
        f"{DOCUMENTATION_NOT_RETRIEVED}"
    )
    if verdict.observed.strings():
        lines.append("")
        lines.append("What the CapabilityStatement said about who publishes it:")
        for label, value in sorted(verdict.observed.to_payload().items()):
            if value:
                lines.append(f"- {label}: `{value}`")
    lines.append("")
    if verdict.entry is None:
        lines.append(
            "No registry entry is proposed. Nothing was written to `data/registry.json`, and "
            "no pull request was opened."
        )
    else:
        lines.append(
            "A proposed entry is attached to the run's proposal file. Nothing was written to "
            "`data/registry.json`, and no pull request was opened — a maintainer promotes it."
        )
    return "\n".join(lines)


def format_report(verdict: Verdict) -> str:
    asked = len(verdict.requested)
    return "\n".join(
        [
            f"claim: {verdict.outcome}"
            + (f" ({verdict.refusal})" if verdict.refusal else "")
            + f" — {verdict.reason}",
            f"requests made: {asked} "
            + ("(the two discovery documents)" if asked else "(the address was refused first)"),
            "documentation page: not retrieved (issue #118 decides whether it may be)",
            "registry: unchanged; no pull request opened",
        ]
    )
