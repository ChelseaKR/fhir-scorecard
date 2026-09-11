"""The site contract: what a built site must satisfy before it is published.

ROADMAP phase 4 asked for "SEO config validation in CI: sitemap completeness, canonical
correctness, JSON-LD validity, no orphan pages". This module is that check, written against
a directory of files rather than against the generator, so it holds whatever produced the
site and keeps holding after the generator is rewritten.

Every rule here is a property this project already promises somewhere else, and the promise
is what the rule cites:

* README, "The site": *"Every endpoint, organization, category, and cohort gets its own
  indexable page with a canonical URL, description, and structured data, plus a sitemap"*.
  A page missing from the sitemap, or a sitemap entry no file answers, breaks that sentence.
* ROADMAP phase 1: *"``sitemap.xml``, ``robots.txt``, canonical URLs ... written from the
  data rather than templated boilerplate"* and *"JSON-LD: ``Dataset`` on the index,
  ``WebAPI`` / ``Organization`` on endpoint pages"*.
* ROADMAP phase 4: *"no orphan pages"*.
* The feeds (#101) are published as a subscribable artifact, so they are held to the same
  shape: every feed the build wrote is listed in the sitemap, reads as an Atom feed, carries
  no two entries with one id, and links only at files this build wrote. Feeds are discovered
  by the Atom namespace rather than by a filename, and every ``href`` a page points feed
  autodiscovery at must be one of the feeds found - so a generator that stopped writing feeds,
  or started writing something that is not one, cannot leave this audit examining nothing and
  reporting clean.

Two things this module deliberately does **not** do.

It does not validate JSON-LD against schema.org, which publishes no required-field list a
checker could hold a document to. What it checks is narrower and is this project's own
contract: the block parses, it declares ``@context`` and ``@type``, and it carries the fields
this site promises for the types it emits. A block whose ``@type`` this site does not emit is
held only to the parse and the two declarations, because inventing requirements for a type
nobody here writes would be inventing a specification.

It does not reach the network. Off-origin links are recorded as external and not followed;
whether a third party's URL still resolves is not a property of this build, and a gate that
went and looked would fail on somebody else's outage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

#: Fields this site promises for each structured-data type it emits. Types not listed are
#: held to ``@context`` and ``@type`` only; see the module docstring.
REQUIRED_JSONLD_FIELDS: dict[str, tuple[str, ...]] = {
    "Dataset": ("name", "description", "url"),
    "WebAPI": ("name", "url"),
    "Organization": ("name", "url"),
}

#: Every finding code this module can emit, with the one-line statement of what it means.
#: ``audit_site`` may return no code outside this map, which ``tests/test_site_audit.py``
#: asserts, so a new rule cannot ship without a documented name.
FINDING_CODES: dict[str, str] = {
    "PAGE_MISSING_FROM_SITEMAP": "a built page the sitemap does not list",
    "FEED_MISSING_FROM_SITEMAP": "a built feed the sitemap does not list",
    "FEED_UNPARSEABLE": "a feed file that is not readable as an Atom feed",
    "FEED_ENTRY_INCOMPLETE": "a feed entry missing an element Atom requires of every entry",
    "FEED_ENTRY_ID_DUPLICATED": "two entries in one feed sharing an id",
    "FEED_ENTRY_LINK_UNBUILT": "a feed entry linking at a path the build did not write",
    "SITEMAP_ENTRY_NOT_BUILT": "a sitemap entry no built file answers",
    "SITEMAP_ENTRY_OFF_ORIGIN": "a sitemap entry that is not under this site's origin",
    "SITEMAP_UNPARSEABLE": "sitemap.xml is missing or is not readable as a urlset",
    "CANONICAL_MISSING": "a page with no canonical link",
    "CANONICAL_DUPLICATED": "a page declaring more than one canonical link",
    "CANONICAL_MISMATCH": "a canonical link that does not address the page it sits on",
    "JSONLD_UNPARSEABLE": "a structured-data block that is not valid JSON",
    "JSONLD_INCOMPLETE": "a structured-data block missing a field this site promises",
    "INTERNAL_LINK_UNBUILT": "a link or subresource pointing at a path the build did not write",
    "ORPHAN_PAGE": "a built page no path of internal links reaches from the home page",
    "ROBOTS_SITEMAP_MISMATCH": "robots.txt is missing or does not point at this site's sitemap",
    "SOCIAL_CARD_INCOMPLETE": "a page whose share card is missing a tag or contradicts the page",
    "ORGANIZATION_NAMED_AS_A_SURFACE": (
        "a schema.org Organization whose name is one of the organization's APIs, not the "
        "organization"
    ),
}

#: An ``Organization`` name ending in this is an API's name, not an organization's. A presence
#: check on ``name`` cannot tell the two apart, and this site publishes the value as structured
#: data about a named third party, so the build refuses it rather than asserting it to a search
#: engine. Stated independently of ``site.org_display_name`` on purpose: this module checks the
#: HTML that was built, so it must not be able to agree with the generator by construction.
_ORGANIZATION_NAME_IS_A_SURFACE = re.compile(
    r"\b(patient[- ]access|provider[- ]directory|member[- ]access|drug[- ]formulary|"
    r"formulary|apis?)\s*$",
    re.IGNORECASE,
)

#: The media type a page uses to point feed autodiscovery at a file. Spelled here for the same
#: reason :data:`_ATOM_NAMESPACE` is.
_ATOM_MEDIA_TYPE = "application/atom+xml"

#: What a page must declare once it declares any of it. A half-written card is one a
#: crawler completes from somewhere else; a card whose title or description differs from
#: the page's own is a second, unreviewed piece of copy about this site, which is exactly
#: what the repository's claim rules exist to prevent.
SOCIAL_TAGS: tuple[str, ...] = (
    "og:title",
    "og:description",
    "og:type",
    "og:url",
    "og:site_name",
    "twitter:card",
    "twitter:title",
    "twitter:description",
    "og:image",
    "twitter:image",
)


@dataclass(frozen=True)
class SiteFinding:
    """One defect in a built site.

    ``where`` is the site-relative file the defect was found in, or ``""`` for a defect of
    the site as a whole (a sitemap entry with no file behind it belongs to no page).
    """

    code: str
    where: str
    detail: str

    def __str__(self) -> str:
        location = self.where or "(site)"
        return f"{self.code}  {location}  {self.detail}"


class _PageParser(HTMLParser):
    """Collect the parts of a page the site contract is written about.

    Attribute values arrive already entity-decoded, so ``&amp;`` in a href is a single
    ``&`` here, which is what a path comparison needs.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonicals: list[str] = []
        self.jsonld: list[str] = []
        self.references: list[str] = []
        #: ``href``s this page advertises as Atom feeds. Collected as well as, not instead of,
        #: ``references``: existence is the link contract's question and being a readable feed
        #: is the feed contract's, and a page pointing autodiscovery at a file that is not a
        #: feed satisfies the first while failing the second.
        self.feeds: list[str] = []
        self.metas: dict[str, str] = {}
        self.title = ""
        self._in_jsonld = False
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        got = {k: (v or "") for k, v in attrs}
        if tag == "link":
            self._link(got)
        elif tag == "script":
            self._script(got)
        elif tag in {"a", "area"} and got.get("href"):
            self.references.append(got["href"])
        elif tag in {"img", "source", "iframe", "embed"} and got.get("src"):
            self.references.append(got["src"])
        elif tag == "meta":
            key = got.get("name") or got.get("property")
            if key and "content" in got:
                self.metas[key.lower()] = got["content"]
        elif tag == "title":
            self._in_title = True

    def _link(self, got: dict[str, str]) -> None:
        if "canonical" in got.get("rel", "").lower().split():
            self.canonicals.append(got.get("href", ""))
        elif got.get("href"):
            self.references.append(got["href"])
            if got.get("type", "").lower().split(";")[0].strip() == _ATOM_MEDIA_TYPE:
                self.feeds.append(got["href"])

    def _script(self, got: dict[str, str]) -> None:
        if got.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self.jsonld.append("")
        elif got.get("src"):
            self.references.append(got["src"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_jsonld = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_jsonld and self.jsonld:
            self.jsonld[-1] += data
        if self._in_title:
            self.title += data


def _origin_prefix(origin: str) -> str:
    """The path the origin is served under, matching ``site._site_path_prefix``."""
    return urlsplit(origin).path.rstrip("/")


def _url_for(site_path: str, origin: str) -> str:
    """The canonical URL of a built page, given its site-relative directory."""
    return f"{origin}/{site_path + '/' if site_path else ''}"


def page_paths(root: Path) -> list[str]:
    """Site-relative directories of every ``index.html`` under ``root``, home first.

    Public because the accessibility and weight gates walk the same set of pages: a second
    walk that discovered pages differently could report clean what the contract rejects.
    """
    found = []
    for html_file in sorted(root.rglob("index.html")):
        relative = html_file.parent.relative_to(root).as_posix()
        found.append("" if relative == "." else relative)
    return sorted(found, key=lambda p: (p != "", p))


#: The Atom 1.0 namespace, which is what makes a file in a build a feed rather than some other
#: XML. Spelled here rather than imported from ``fhir_scorecard.feeds`` for the reason
#: ``_ORGANIZATION_NAME_IS_A_SURFACE`` is: this module reads what was built, so it must not be
#: able to agree with the generator by construction. Discovery is by this string and not by a
#: filename, so renaming the generator's ``FEED_FILENAME`` cannot empty the set of feeds this
#: audit examines - which is how a gate quietly stops examining anything.
_ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"


def feed_paths(root: Path) -> list[str]:
    """Site-relative paths of every XML file under ``root`` that presents itself as Atom.

    A separate walk from :func:`page_paths` because a feed is a file and a page is a directory,
    and the two contracts differ: a page must be reachable by internal links, a feed must be
    listed in the sitemap and every entry in it must address something the build wrote.

    ``sitemap.xml`` is an XML file and is not in here, because it does not carry the Atom
    namespace. A file that carries it and is malformed *is* in here, and is reported as
    unparseable rather than skipped: a broken feed and no feed at all are different facts.
    """
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.xml")
        if path.is_file() and _ATOM_NAMESPACE in path.read_text(encoding="utf-8", errors="replace")
    )


def _resolve(reference: str, page_path: str, origin: str) -> str | None:
    """The site-relative file a reference addresses, or ``None`` if it addresses nothing here.

    ``None`` covers every reference this build cannot be held responsible for: another
    origin, a fragment on the page itself, and non-http schemes such as ``mailto:``.
    """
    split = urlsplit(reference)
    if split.scheme or split.netloc:
        if urlunsplit((split.scheme, split.netloc, "", "", "")) != _site_root(origin):
            return None
        path = split.path
    else:
        path = split.path
        if not path:
            return None
    prefix = _origin_prefix(origin)
    if path.startswith("/"):
        if prefix and path.startswith(prefix + "/"):
            path = path[len(prefix) :]
        target = path.lstrip("/")
    else:
        base = page_path + "/" if page_path else ""
        target = _normalize(base + path)
    return target + "index.html" if target.endswith("/") or not target else target or "index.html"


def _site_root(origin: str) -> str:
    split = urlsplit(origin)
    return urlunsplit((split.scheme, split.netloc, "", "", ""))


def _normalize(path: str) -> str:
    """Collapse ``.`` and ``..`` in a relative site path without touching the filesystem."""
    parts: list[str] = []
    for part in path.split("/"):
        if part == "." or (part == "" and parts):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    collapsed = "/".join(parts)
    return collapsed + "/" if path.endswith("/") and collapsed else collapsed


_LOC = re.compile(r"<loc>(.*?)</loc>", re.DOTALL)


def _sitemap_locs(root: Path) -> list[str] | None:
    sitemap_file = root / "sitemap.xml"
    if not sitemap_file.is_file():
        return None
    text = sitemap_file.read_text(encoding="utf-8")
    if "<urlset" not in text:
        return None
    return [loc.strip() for loc in _LOC.findall(text)]


def _check_sitemap(root: Path, origin: str, pages: list[str]) -> list[SiteFinding]:
    locs = _sitemap_locs(root)
    if locs is None:
        return [
            SiteFinding(
                "SITEMAP_UNPARSEABLE", "sitemap.xml", "missing, or not readable as a <urlset>"
            )
        ]
    findings = []
    listed = set(locs)
    for page in pages:
        url = _url_for(page, origin)
        if url not in listed:
            findings.append(
                SiteFinding("PAGE_MISSING_FROM_SITEMAP", page_file(page), f"{url} is not listed")
            )
    for loc in locs:
        if not loc.startswith(origin + "/") and loc != origin + "/":
            findings.append(SiteFinding("SITEMAP_ENTRY_OFF_ORIGIN", "sitemap.xml", loc))
            continue
        target = _resolve(loc, "", origin)
        if target is None or not (root / target).is_file():
            findings.append(SiteFinding("SITEMAP_ENTRY_NOT_BUILT", "sitemap.xml", loc))
    return findings


def _atom(name: str) -> str:
    return f"{{{_ATOM_NAMESPACE}}}{name}"


def _entry_findings(
    root: Path, origin: str, path: str, index: int, entry: Element, seen: set[str]
) -> list[SiteFinding]:
    """One entry's findings: what Atom requires of it, and where it points.

    ``index`` names the entry when it has no id to name it by, which is exactly the entry a
    finding about a missing id has to be able to talk about.
    """
    findings = []
    for name in ("id", "title", "updated"):
        if entry.find(_atom(name)) is None:
            findings.append(
                SiteFinding("FEED_ENTRY_INCOMPLETE", path, f"entry {index} has no <{name}>")
            )
    identifier = entry.findtext(_atom("id"), "").strip()
    if identifier and identifier in seen:
        findings.append(
            SiteFinding("FEED_ENTRY_ID_DUPLICATED", path, f"entry {index} repeats {identifier}")
        )
    seen.add(identifier)
    for link in entry.findall(_atom("link")):
        reference = link.get("href", "")
        target = _resolve(reference, "", origin) if reference else None
        if target is not None and not (root / target).is_file():
            findings.append(SiteFinding("FEED_ENTRY_LINK_UNBUILT", path, reference))
    return findings


def _feed_findings(root: Path, origin: str, path: str) -> list[SiteFinding]:
    """Whether one file is a readable Atom feed, and whether its entries address this build.

    The parse is the check. A substring test over the text would pass on a document no reader
    can open, which is the shape of a gate that cannot fail, and this project publishes the
    feed as a subscribable artifact rather than as decoration.
    """
    try:
        # S314 warns that `xml.etree` is unsafe against a hostile document. The document here
        # is a file inside the directory being audited - in CI, one this same build wrote
        # seconds earlier - and CPython's parser resolves no external entity and refuses any
        # entity declaration at all, so neither of the attacks the rule names is reachable.
        # The alternative, a regex "parse", is a check that cannot fail.
        document = ElementTree.fromstring((root / path).read_text(encoding="utf-8"))  # noqa: S314
    except (ElementTree.ParseError, UnicodeDecodeError) as exc:
        return [SiteFinding("FEED_UNPARSEABLE", path, f"not well-formed XML: {exc}")]
    if document.tag != _atom("feed"):
        return [SiteFinding("FEED_UNPARSEABLE", path, f"root element is <{document.tag}>")]
    findings = [
        SiteFinding("FEED_UNPARSEABLE", path, f"no <{name}> element")
        for name in ("id", "title", "updated")
        if document.find(_atom(name)) is None
    ]
    seen: set[str] = set()
    for index, entry in enumerate(document.findall(_atom("entry"))):
        findings += _entry_findings(root, origin, path, index, entry, seen)
    return findings


def _check_feeds(
    root: Path, origin: str, locs: list[str], advertised: dict[str, set[str]]
) -> list[SiteFinding]:
    """The feed half of the contract, in both directions.

    Every feed the build wrote has to be listed in the sitemap and has to parse; and every
    ``href`` a page points feed autodiscovery at has to be one of the feeds found. The second
    direction is the floor under the first: :func:`feed_paths` discovers by content, so a
    generator that started writing something that is not a feed would produce an empty set and
    a clean report, and the advertised links are what refuses that.
    """
    feeds = feed_paths(root)
    listed = set(locs)
    findings = []
    for path in feeds:
        url = f"{origin}/{path}"
        if url not in listed:
            findings.append(SiteFinding("FEED_MISSING_FROM_SITEMAP", path, f"{url} is not listed"))
        findings += _feed_findings(root, origin, path)
    known = set(feeds)
    for page, references in sorted(advertised.items()):
        for reference in sorted(references):
            target = _resolve(reference, page, origin)
            if target is not None and target not in known:
                findings.append(
                    SiteFinding(
                        "FEED_UNPARSEABLE",
                        page_file(page),
                        f"{reference} is advertised as an Atom feed and does not read as one",
                    )
                )
    return findings


def _check_robots(root: Path, origin: str) -> list[SiteFinding]:
    robots_file = root / "robots.txt"
    if not robots_file.is_file():
        return [SiteFinding("ROBOTS_SITEMAP_MISMATCH", "robots.txt", "no robots.txt was written")]
    wanted = f"Sitemap: {origin}/sitemap.xml"
    if wanted not in robots_file.read_text(encoding="utf-8"):
        return [SiteFinding("ROBOTS_SITEMAP_MISMATCH", "robots.txt", f"does not carry {wanted!r}")]
    return []


def page_file(page_path: str) -> str:
    """The site-relative file behind a page path, which is what a finding names."""
    return f"{page_path}/index.html" if page_path else "index.html"


def _check_canonical(page: str, parser: _PageParser, origin: str) -> list[SiteFinding]:
    where = page_file(page)
    if not parser.canonicals:
        return [SiteFinding("CANONICAL_MISSING", where, "no <link rel=canonical>")]
    if len(parser.canonicals) > 1:
        return [
            SiteFinding(
                "CANONICAL_DUPLICATED", where, f"{len(parser.canonicals)}: {parser.canonicals}"
            )
        ]
    expected = _url_for(page, origin)
    if parser.canonicals[0] != expected:
        return [
            SiteFinding(
                "CANONICAL_MISMATCH", where, f"declares {parser.canonicals[0]}, is at {expected}"
            )
        ]
    return []


def _check_card_image(
    root: Path, where: str, parser: _PageParser, origin: str
) -> list[SiteFinding]:
    """The card's image has to be absolute, on this origin, and actually published.

    ``og:image`` is the one address on this page that is resolved by something
    which is not this origin, so the root-relative form every other asset uses
    would resolve against the wrong site or against nothing. And an image this
    build did not write is a blank rectangle everywhere the page is shared, with
    nothing about the page itself looking wrong -- which is the only reason this
    is checked rather than trusted.
    """
    image = parser.metas.get("og:image")
    if image is None:
        return []
    findings = []
    twitter = parser.metas.get("twitter:image")
    if twitter is not None and twitter != image:
        findings.append(
            SiteFinding(
                "SOCIAL_CARD_INCOMPLETE",
                where,
                "og:image and twitter:image address different files",
            )
        )
    prefix = f"{origin.rstrip('/')}/"
    if not image.startswith(prefix):
        findings.append(
            SiteFinding(
                "SOCIAL_CARD_INCOMPLETE", where, f"og:image {image} is not an address on {origin}"
            )
        )
        return findings
    relative = image[len(prefix) :]
    if not (root / relative).is_file():
        findings.append(
            SiteFinding(
                "SOCIAL_CARD_INCOMPLETE", where, f"og:image names {relative}, which was not built"
            )
        )
    return findings


def _check_social(root: Path, page: str, parser: _PageParser, origin: str) -> list[SiteFinding]:
    """A share card must be complete, and must say what the page says.

    The site's copy is reviewed; a card is copy too, and one that drifts from the page
    is an unreviewed claim about this project published where nobody rereads it. So the
    card is not checked for existing so much as for agreeing.
    """
    where = page_file(page)
    if not any(tag in parser.metas for tag in SOCIAL_TAGS):
        return []
    findings = [
        SiteFinding("SOCIAL_CARD_INCOMPLETE", where, f"declares no {tag}")
        for tag in SOCIAL_TAGS
        if tag not in parser.metas
    ]
    title = " ".join(parser.title.split())
    description = parser.metas.get("description", "")
    for tag, expected, what in (
        ("og:title", title, "the page title"),
        ("twitter:title", title, "the page title"),
        ("og:description", description, "the page description"),
        ("twitter:description", description, "the page description"),
        ("og:url", _url_for(page, origin), "the page address"),
    ):
        found = parser.metas.get(tag)
        # `og:title` is the page's own title without the site suffix the <title> carries,
        # so it is a match when the <title> is it plus that suffix, and only then.
        if (
            found is None
            or found == expected
            or (tag.endswith("title") and title.startswith(f"{found} |"))
        ):
            continue
        findings.append(
            SiteFinding("SOCIAL_CARD_INCOMPLETE", where, f"{tag} does not match {what}")
        )
    return findings + _check_card_image(root, where, parser, origin)


def _check_jsonld(page: str, parser: _PageParser) -> list[SiteFinding]:
    where = page_file(page)
    findings = []
    for index, raw in enumerate(parser.jsonld):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            findings.append(SiteFinding("JSONLD_UNPARSEABLE", where, f"block {index}: {exc}"))
            continue
        if not isinstance(payload, dict):
            findings.append(
                SiteFinding(
                    "JSONLD_INCOMPLETE",
                    where,
                    f"block {index}: not a JSON object, so it declares no @type",
                )
            )
            continue
        required = ["@context", "@type"]
        declared = payload.get("@type")
        if isinstance(declared, str):
            required += list(REQUIRED_JSONLD_FIELDS.get(declared, ()))
        missing = [field for field in required if not payload.get(field)]
        if missing:
            findings.append(
                SiteFinding(
                    "JSONLD_INCOMPLETE",
                    where,
                    f"block {index} ({declared or 'no @type'}) omits {', '.join(missing)}",
                )
            )
        name = payload.get("name")
        if (
            declared == "Organization"
            and isinstance(name, str)
            and _ORGANIZATION_NAME_IS_A_SURFACE.search(name)
        ):
            findings.append(
                SiteFinding(
                    "ORGANIZATION_NAMED_AS_A_SURFACE",
                    where,
                    f"block {index} names the Organization {name!r}, which is one of its APIs",
                )
            )
    return findings


@dataclass(frozen=True)
class _PageReading:
    """What one page contributed to the site-wide checks, beside its own findings."""

    links: set[str]
    #: ``href``s this page advertises as Atom feeds, for :func:`_check_feeds`.
    feeds: set[str]


def _read_page(root: Path, page: str, origin: str) -> tuple[list[SiteFinding], _PageReading]:
    """One page's findings, and what the site-wide checks need from it."""
    parser = _PageParser()
    parser.feed((root / page_file(page)).read_text(encoding="utf-8"))
    findings = (
        _check_canonical(page, parser, origin)
        + _check_jsonld(page, parser)
        + _check_social(root, page, parser, origin)
    )
    links: set[str] = set()
    for reference in parser.references:
        target = _resolve(reference, page, origin)
        if target is None:
            continue
        if not (root / target).is_file():
            findings.append(SiteFinding("INTERNAL_LINK_UNBUILT", page_file(page), reference))
        elif target.endswith("index.html"):
            links.add(target[: -len("index.html")].rstrip("/"))
    return findings, _PageReading(links=links, feeds=set(parser.feeds))


def _unreachable(pages: list[str], outgoing: dict[str, set[str]]) -> list[str]:
    """Pages no path of internal links reaches from the home page."""
    reached = {""}
    frontier = [""]
    while frontier:
        for nxt in sorted(outgoing.get(frontier.pop(), ())):
            if nxt not in reached:
                reached.add(nxt)
                frontier.append(nxt)
    return [page for page in pages if page not in reached]


def audit_site(root: Path, origin: str) -> list[SiteFinding]:
    """Every way the built site under ``root`` breaks the contract, in a stable order.

    An empty list means the site satisfies every rule in :data:`FINDING_CODES`. It does not
    mean the site is correct; it means these named properties hold.
    """
    pages = page_paths(root)
    if not pages:
        return [SiteFinding("SITEMAP_UNPARSEABLE", "", "no pages were built, so nothing was read")]
    findings = _check_sitemap(root, origin, pages) + _check_robots(root, origin)
    outgoing: dict[str, set[str]] = {}
    advertised: dict[str, set[str]] = {}
    for page in pages:
        page_findings, reading = _read_page(root, page, origin)
        outgoing[page] = reading.links
        if reading.feeds:
            advertised[page] = reading.feeds
        findings += page_findings
    findings += _check_feeds(root, origin, _sitemap_locs(root) or [], advertised)
    findings += [
        SiteFinding("ORPHAN_PAGE", page_file(page), "no internal link path reaches it")
        for page in _unreachable(pages, outgoing)
    ]
    return sorted(findings, key=lambda f: (f.where, f.code, f.detail))
