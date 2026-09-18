"""Whether one third party serves many graded endpoints at once, measured two ways.

This is the arithmetic behind the milestone check that issue #87's review waves are supposed to
carry. #87 grows the graded population a state at a time; the question that grows with it is
whether any single intermediary ends up standing between this project and a large share of the
organizations it grades. That question has a decision attached to it outside this repository, so
the number has to be recomputed every time the registry moves rather than quoted from the last
time somebody looked.

**Two axes, because neither one alone answers it.**

``HOST``
    The registrable domain of the endpoint's ``base_url``. Answers *who serves the address*. It
    is complete - every registry entry has a base URL - and it is blind to a platform that gives
    each customer a vanity hostname. Measured 2026-09-13 over the committed registry, the largest
    registrable domain carrying endpoints of more than one graded organization was
    ``amerihealthcaritas.com`` with 6 endpoints, and the three organizations under it are
    corporate siblings of one parent rather than customers of a third party.

``PLATFORM``
    The ``software.name`` the endpoint's own CapabilityStatement declares, read from the
    observation record. Answers *whose software answers*. It sees through vanity hostnames and it
    is incomplete - an endpoint nobody has read declares nothing, and a document can omit the
    element entirely. Measured 2026-09-13 over the same registry and the live observation
    record, ``HAPI FHIR Server`` covered 9 endpoints across 7 organizations and ``Epic`` 6 across
    6, which is several times the ceiling the host axis alone reports.

The two disagree, and the disagreement is the finding rather than a defect in either. An
assessment written 2026-09-12 concluded from the host axis that "no named platform appears more
than twice"; the platform axis says otherwise on the same data. Publishing one axis would have
kept that wrong.

**What this module will not do: decide which platforms are intermediaries.** A declared
``software.name`` is the name of *software*, not the name of a service relationship. Seven
organizations running an open-source server are not seven customers of one vendor, and nothing in
a CapabilityStatement distinguishes "we bought this as a service" from "we installed this". So
:func:`by_platform` reports the distribution and names the organizations under each entry, and
the judgment about which entries are commercial platforms stays with the reader. A module that
guessed would publish a claim about a named vendor's book of business on the strength of a
version string.

**Three states, kept apart, on the platform axis.** They are three different sentences and the
count that merges any two of them is wrong:

* a document was read and it declared a ``software.name`` - the endpoint joins that platform;
* a document was read and it declared no software element - the endpoint joins
  :data:`DECLARED_NO_SOFTWARE`, which is a measured property of that document;
* no document has been read - the endpoint is :attr:`Concentration.unmeasured` and joins nothing.

The third is a fact about this project, exactly like ``coverage.NOT_YET_REVIEWED``, and the
reason :class:`Concentration` carries ``endpoints_measured`` and ``endpoints_in_registry`` as two
numbers that travel together. A share printed without both halves would read as a statement about
the market when it is partly a statement about what has been probed.

**Nothing here is wired to a page.** Like :mod:`fhir_scorecard.statistics`, this ships as the
arithmetic and the refusals, with the two decisions that would publish it left to the maintainer:
whether a concentration table belongs on the site at all, and what threshold - if any - is the
one worth acting on. :func:`crosses` therefore has no default threshold and cannot be called
without one, because "roughly fifteen endpoints under one third party" is a judgment about a
product, not a property of FHIR.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from fhir_scorecard.registry import Endpoint
from fhir_scorecard.site import org_slug

#: The two axes, as the strings that name them in output and in JSON.
HOST = "host"
PLATFORM = "platform"
AXES: tuple[str, ...] = (HOST, PLATFORM)

#: The bucket for an endpoint whose CapabilityStatement *was* read and carried no ``software``
#: element. Spelled as a sentence rather than as ``None`` or ``""`` so that it cannot be confused
#: with :attr:`Concentration.unmeasured`, which is the endpoints nobody has read. An earlier
#: hand count of this distribution reported "(no software element)" as the modal value without
#: separating the two, which made a fact about twelve unprobed endpoints look like a fact about
#: what payers declare.
DECLARED_NO_SOFTWARE = "(document read, declared no software element)"

#: Public suffixes with more than one label that this project's registry could plausibly meet.
#: Not the Public Suffix List: this package has no runtime dependencies and is not going to
#: acquire one for a hostname split. The consequence is stated rather than hidden - a host under
#: a two-label suffix that is *absent* from this set is grouped one label too coarsely, which
#: would merge two unrelated organizations into one apparent footprint and *overstate*
#: concentration. That is the dangerous direction, so :func:`by_host` names every endpoint under
#: every domain it reports and ``tests/test_concentration.py`` asserts that no domain computed
#: from the committed registry is itself a bare public suffix.
MULTI_LABEL_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "gov.uk",
        "ac.uk",
        "co.nz",
        "co.za",
        "com.au",
        "net.au",
        "org.au",
        "com.br",
        "com.mx",
        "co.jp",
        "co.in",
    }
)


def registrable_domain(host: str) -> str:
    """The registrable domain of ``host``: its last two labels, or three under a known suffix.

    Lower-cased, with a trailing dot and any port already gone (``urlsplit().hostname`` removes
    both). A host with one label is returned as it stands rather than padded, because an endpoint
    whose base URL has no dot in its hostname is a finding about that entry, not a domain.
    """
    labels = host.strip().strip(".").lower().split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    tail = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if tail in MULTI_LABEL_SUFFIXES else tail


def host_of(endpoint: Endpoint) -> str:
    """The hostname of an endpoint's base URL, or ``""`` when it has none.

    ``""`` rather than an exception: the registry loader has already refused anything that is not
    an https URL, so a base URL with no hostname would be a loader defect, and a measurement
    module is the wrong place to discover one. It is surfaced instead - an endpoint that returns
    ``""`` here lands in :attr:`Concentration.unmeasured` on the host axis, where it is counted
    and named rather than silently dropped from a denominator.
    """
    return urlsplit(endpoint.base_url).hostname or ""


@dataclass(frozen=True)
class Footprint:
    """One domain's, or one platform's, share of the graded population.

    Carries the endpoint ids and the organizations rather than only their counts, so that a
    reader can check any row - and so that a grouping this module got wrong is visible in the
    output instead of folded into a number.
    """

    axis: str
    #: The registrable domain, or the declared ``software.name`` / :data:`DECLARED_NO_SOFTWARE`.
    key: str
    endpoint_ids: tuple[str, ...]
    #: Slugs from :func:`fhir_scorecard.site.org_slug`, deduplicated and sorted. The same slug
    #: that decides which endpoints share an ``/org/`` page, so "one organization" means here
    #: what it means on the site - including where that slug splits one organization in two.
    #: ``securityhealth.org`` reads as two organizations on 2026-09-13 because Security Health
    #: Plan named one of its two entries "... Member", and no normalization this module could
    #: apply would be safer than printing the slugs and letting a reader see it.
    organizations: tuple[str, ...]
    #: Whether ``key`` names a party at all. False for exactly one bucket,
    #: :data:`DECLARED_NO_SOFTWARE`, which names the *absence* of a software element in documents
    #: that were read. The flag exists because the first run of this module reported that bucket
    #: as "the largest footprint spanning more than one organization: 17 endpoints across 13
    #: organizations" and crossed a threshold with it. Seventeen endpoints declaring no software
    #: have nothing in common; reading that as an intermediary is this portfolio's own dominant
    #: defect - an absence rendered as a value - committed inside the module that measures it.
    names_a_party: bool = True

    @property
    def endpoints(self) -> int:
        return len(self.endpoint_ids)

    @property
    def organization_count(self) -> int:
        return len(self.organizations)

    @property
    def serves_several_organizations(self) -> bool:
        """Whether this footprint spans more than one graded organization.

        The filter that separates an intermediary from a payer's own family of surfaces. One
        organization publishing a Patient Access API and a Provider Directory API on its own
        domain is two endpoints and no intermediary; two organizations on one domain is the shape
        the question is about.

        It does **not** separate corporate siblings from strangers. Three AmeriHealth Caritas
        state plans are three slugs and one company, and no field in the registry says so. Where
        that matters, :attr:`organizations` is printed so the reader can see it.
        """
        return self.organization_count > 1

    @property
    def could_be_an_intermediary(self) -> bool:
        """Spans several organizations *and* names a party who could be one of them.

        The conjunction is the whole point; see :attr:`names_a_party`. Everything that ranks or
        thresholds footprints reads this rather than
        :attr:`serves_several_organizations`.
        """
        return self.names_a_party and self.serves_several_organizations


@dataclass(frozen=True)
class Concentration:
    """The distribution along one axis, with the size of the population it was measured over."""

    axis: str
    #: Descending by endpoint count, then by key, so two runs over the same data print the same.
    footprints: tuple[Footprint, ...]
    #: How many registry endpoints this axis could place. Never assumed to be the registry size.
    endpoints_measured: int
    #: The registry size, carried so no share can be printed without its denominator.
    endpoints_in_registry: int
    #: Endpoints this axis could not place, by id. On ``PLATFORM`` these are the endpoints no
    #: vantage has read a document from; they are named, never bucketed and never counted as
    #: declaring nothing.
    unmeasured: tuple[str, ...]

    @property
    def largest_shared(self) -> Footprint | None:
        """The biggest footprint that could be an intermediary, or ``None`` if there is none.

        ``None`` is a real answer here and is why this is not simply ``footprints[0]``: a
        population in which every domain belongs to exactly one organization has no intermediary
        at all, and reporting its largest single-organization domain as a "footprint" would
        answer a different question from the one asked.

        Reads :attr:`Footprint.could_be_an_intermediary`, so the bucket that names an absence is
        skipped however large it gets.
        """
        for footprint in self.footprints:
            if footprint.could_be_an_intermediary:
                return footprint
        return None

    def as_dict(self) -> dict[str, object]:
        """A JSON-ready view. Both population numbers are always present, never one of them."""
        return {
            "axis": self.axis,
            "endpoints_measured": self.endpoints_measured,
            "endpoints_in_registry": self.endpoints_in_registry,
            "unmeasured": list(self.unmeasured),
            "footprints": [
                {
                    "key": f.key,
                    "endpoints": f.endpoints,
                    "endpoint_ids": list(f.endpoint_ids),
                    "organizations": list(f.organizations),
                    "names_a_party": f.names_a_party,
                    "serves_several_organizations": f.serves_several_organizations,
                    "could_be_an_intermediary": f.could_be_an_intermediary,
                }
                for f in self.footprints
            ],
        }


def _group(
    axis: str,
    placed: Mapping[str, list[tuple[str, str]]],
    measured: int,
    total: int,
    unmeasured: Iterable[str],
) -> Concentration:
    footprints = tuple(
        sorted(
            (
                Footprint(
                    axis=axis,
                    key=key,
                    endpoint_ids=tuple(endpoint_id for endpoint_id, _ in sorted(rows)),
                    organizations=tuple(sorted({org for _, org in rows})),
                    names_a_party=key != DECLARED_NO_SOFTWARE,
                )
                for key, rows in placed.items()
            ),
            key=lambda f: (-f.endpoints, f.key),
        )
    )
    return Concentration(
        axis=axis,
        footprints=footprints,
        endpoints_measured=measured,
        endpoints_in_registry=total,
        unmeasured=tuple(sorted(unmeasured)),
    )


def by_host(endpoints: Iterable[Endpoint]) -> Concentration:
    """Group the registry by the registrable domain of each base URL."""
    entries = list(endpoints)
    placed: dict[str, list[tuple[str, str]]] = {}
    unmeasured: list[str] = []
    for endpoint in entries:
        host = host_of(endpoint)
        if not host:
            unmeasured.append(endpoint.endpoint_id)
            continue
        key = registrable_domain(host)
        placed.setdefault(key, []).append((endpoint.endpoint_id, org_slug(endpoint.name)))
    measured = len(entries) - len(unmeasured)
    return _group(HOST, placed, measured, len(entries), unmeasured)


def by_platform(
    endpoints: Iterable[Endpoint],
    fingerprints: Mapping[str, Mapping[str, object] | None],
) -> Concentration:
    """Group the registry by the ``software.name`` each endpoint's own document declares.

    ``fingerprints`` maps endpoint id to that endpoint's recorded fingerprint, in the shape
    ``fhir_scorecard.drift.fingerprint`` writes - so ``data/history.json`` and the
    ``capability-history`` branch's copy of it are both valid inputs, and so is a mapping built
    from a live run. A missing key and a ``None`` value mean the same thing and are the same
    outcome: nobody has read this endpoint's document, so it is named in ``unmeasured``.

    A fingerprint whose ``software_name`` is absent, ``None`` or blank is a *different* outcome -
    a document was read and it declared no software element - and joins
    :data:`DECLARED_NO_SOFTWARE`. Reading those two as one is the error this module was written
    around; see the module docstring.
    """
    entries = list(endpoints)
    placed: dict[str, list[tuple[str, str]]] = {}
    unmeasured: list[str] = []
    for endpoint in entries:
        fingerprint = fingerprints.get(endpoint.endpoint_id)
        if fingerprint is None:
            unmeasured.append(endpoint.endpoint_id)
            continue
        raw = fingerprint.get("software_name")
        name = raw.strip() if isinstance(raw, str) and raw.strip() else DECLARED_NO_SOFTWARE
        placed.setdefault(name, []).append((endpoint.endpoint_id, org_slug(endpoint.name)))
    measured = len(entries) - len(unmeasured)
    return _group(PLATFORM, placed, measured, len(entries), unmeasured)


def fingerprints_from_history(history: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    """The fingerprint of every endpoint the observation record holds one for.

    Entries with no ``fingerprint`` are left out rather than mapped to an empty dict, because
    :func:`by_platform` reads a present-but-empty fingerprint as "a document was read" and an
    absent one as "nobody has read it". A record can hold an endpoint with observations and no
    fingerprint - that is what a run that reached an endpoint and retrieved no document leaves
    behind - and it belongs in the second group.
    """
    out: dict[str, Mapping[str, object]] = {}
    for endpoint_id, record in history.items():
        if endpoint_id.startswith("_") or not isinstance(record, dict):
            continue
        fingerprint = record.get("fingerprint")
        if isinstance(fingerprint, dict) and fingerprint:
            out[endpoint_id] = fingerprint
    return out


def crosses(concentration: Concentration, threshold: int) -> Footprint | None:
    """The largest multi-organization footprint at or above ``threshold`` endpoints, or ``None``.

    ``threshold`` is required and has no default, deliberately. The number that matters is a
    statement about what would make a product viable, not a property of this data, and a default
    here would turn one person's judgment into this repository's published opinion. The caller
    states the threshold it was told to use, and it is printed beside the verdict.

    Raises :class:`ValueError` below 1: a threshold of zero would report every shared domain as a
    crossing, which is a gate that cannot fail.
    """
    if threshold < 1:
        raise ValueError("threshold must be at least 1; 0 would make every footprint a crossing")
    largest = concentration.largest_shared
    if largest is None or largest.endpoints < threshold:
        return None
    return largest


def render(concentration: Concentration, threshold: int | None = None) -> str:
    """The measurement as text, with both population numbers on the first line.

    ``threshold`` is optional here and not in :func:`crosses`, because a report with no threshold
    is a description and a report with one is a verdict; only the verdict needs a stated number.
    """
    lines = [
        f"{concentration.axis} concentration: "
        f"{concentration.endpoints_measured} of {concentration.endpoints_in_registry} "
        f"registry endpoints placed, {len(concentration.footprints)} distinct "
        f"{'domains' if concentration.axis == HOST else 'declared platforms'}"
    ]
    if concentration.unmeasured:
        lines.append(
            f"  not placed ({len(concentration.unmeasured)}, no document has been read): "
            + ", ".join(concentration.unmeasured)
        )
    for footprint in concentration.footprints:
        if footprint.endpoints == 1 and not footprint.serves_several_organizations:
            continue
        if not footprint.names_a_party:
            note = "  (names no party: these documents were read and declared no software)"
        elif footprint.serves_several_organizations:
            note = ""
        else:
            note = "  (one organization)"
        lines.append(
            f"  {footprint.endpoints:3d} {_plural(footprint.endpoints, 'endpoint')} / "
            f"{footprint.organization_count:2d} "
            f"{_plural(footprint.organization_count, 'organization')}  {footprint.key}{note}"
        )
        if footprint.serves_several_organizations:
            lines.append("        " + ", ".join(footprint.organizations))
    singletons = sum(
        1
        for f in concentration.footprints
        if f.endpoints == 1 and not f.serves_several_organizations
    )
    if singletons:
        lines.append(f"  {singletons} more carry one endpoint of one organization each")
    largest = concentration.largest_shared
    noun = "domain" if concentration.axis == HOST else "declared platform"
    if largest is None:
        lines.append(
            f"  largest footprint that could be an intermediary: none - every {noun} here "
            "belongs to a single graded organization"
        )
    else:
        lines.append(
            f"  largest footprint that could be an intermediary: {largest.key}, "
            f"{largest.endpoints} endpoints across {largest.organization_count} organizations"
        )
    if threshold is not None:
        crossing = crosses(concentration, threshold)
        lines.append(
            f"  threshold {threshold}: "
            + (
                f"crossed by {crossing.key} at {crossing.endpoints} endpoints"
                if crossing is not None
                else "not crossed"
            )
        )
    return "\n".join(lines)


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else noun + "s"
