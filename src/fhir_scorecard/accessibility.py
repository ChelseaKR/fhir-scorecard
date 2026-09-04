"""Mechanical accessibility checks over built pages, each naming the criterion it implements.

ROADMAP phase 4 asked for accessibility budgets as merge gates. This is that gate, and
[ADR 0004](../../docs/adr/0004-accessibility-and-weight-gates-without-a-browser.md) records
why it is a static reader rather than a browser-driven score, and what a browser would catch
that this cannot.

The honest boundary, stated here as well as in the ADR because a green gate is read as a
claim: **this checks the subset of WCAG 2.2 Level A that can be decided from the markup a
static generator emits, and nothing else.** It does not measure colour contrast as rendered,
focus order, visible focus, computed ARIA roles, reflow, or anything that needs layout or a
user agent. It does not replace the assistive-technology review, which remains open in
`docs/RESPONSIBLE-TECH-AUDITS.md` section E. A page can satisfy every rule below and still be
unusable with a screen reader.

Each rule names the success criterion it implements, or says plainly that it is this project's
own rule rather than a criterion, which is the same standard the grading rules are held to.
Seven name a criterion; five are this project's own. A rule wearing a criterion number that does
not require it would be a fabricated citation, which is the one thing this repository cannot
publish, so the five say so in the text a reader of a finding sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from fhir_scorecard.audit import SiteFinding, page_file, page_paths

#: Every accessibility rule, with what it fires on and the criterion behind it. A rule that
#: is this project's own says so instead of naming a criterion it does not implement.
A11Y_CODES: dict[str, str] = {
    "A11Y_PAGE_NOT_IN_A_LANGUAGE": (
        "the <html> element declares no lang (WCAG 2.2 SC 3.1.1 Language of Page, Level A)"
    ),
    "A11Y_PAGE_NOT_TITLED": (
        "the page has no non-empty <title> (WCAG 2.2 SC 2.4.2 Page Titled, Level A)"
    ),
    "A11Y_TITLE_NOT_UNIQUE": (
        "two pages share a title. SC 2.4.2 requires a title that describes the topic; it does "
        "not require uniqueness, so the uniqueness half is this project's own rule: every page "
        "here describes a different endpoint, organization, category or cohort"
    ),
    "A11Y_NO_TOP_LEVEL_HEADING": (
        "the page has no h1. No Level A criterion requires one: SC 1.3.1 Info and "
        "Relationships asks that structure conveyed through presentation be programmatically "
        "determined, and G141 offers headings as one sufficient technique for doing that, "
        "which is not the same as requiring a top-level heading. So this is this project's "
        "own rule, by the same argument the skipped-level rule below makes, and it is here "
        "because every page is generated from one template set that always emits exactly one "
        "h1: a page without one is a defect in the generator"
    ),
    "A11Y_HEADING_LEVEL_SKIPPED": (
        "a heading is more than one level below the heading before it. No Level A criterion "
        "requires sequential heading levels: SC 1.3.1 Info and Relationships asks that "
        "structure conveyed through presentation be programmatically determined, which a "
        "correctly marked-up h1 then h3 already is. So this is this project's own rule, not a "
        "criterion, and it is here because every page is generated from one template set, "
        "where a skipped level is a defect in the generator rather than an authoring choice"
    ),
    "A11Y_IMAGE_WITHOUT_ALT": (
        'an <img> with no alt attribute at all. alt="" is correct for a decorative image and '
        "passes; an absent attribute leaves a screen reader announcing the file name "
        "(WCAG 2.2 SC 1.1.1 Non-text Content, Level A)"
    ),
    "A11Y_CONTROL_WITHOUT_NAME": (
        "a form control or button with no accessible name from its text, alt text, aria-label, "
        "aria-labelledby, title, or a label pointing at it "
        "(WCAG 2.2 SC 4.1.2 Name, Role, Value, Level A)"
    ),
    "A11Y_LINK_WITHOUT_TEXT": (
        "a link with no discernible text, alt text, or aria-label "
        "(WCAG 2.2 SC 2.4.4 Link Purpose (In Context), Level A)"
    ),
    "A11Y_DUPLICATE_ID": (
        "an id used more than once. SC 4.1.1 Parsing was removed in WCAG 2.2, so this is not "
        "that criterion but this project's own rule: it is here because every id reference "
        "below resolves to the first match, which makes a duplicate a silent mis-labelling "
        "(supports SC 1.3.1 and 4.1.2)"
    ),
    "A11Y_REFERENCE_TO_MISSING_ID": (
        "aria-labelledby, aria-describedby, aria-controls, or a label's for names an id that is "
        "not on the page, so the relationship it declares does not exist "
        "(WCAG 2.2 SC 1.3.1 and SC 4.1.2, Level A)"
    ),
    "A11Y_FRAGMENT_TARGET_MISSING": (
        "a same-page link points at a fragment nothing on the page answers. The skip link is "
        "the case that matters: a broken one leaves keyboard users no way past the header "
        "(WCAG 2.2 SC 2.4.1 Bypass Blocks, Level A)"
    ),
    "A11Y_NO_MAIN_LANDMARK": (
        "the page has no <main> and nothing with role=main. No Level A criterion requires a "
        "main landmark: SC 2.4.1 Bypass Blocks asks for a mechanism that bypasses repeated "
        "blocks, and a skip link satisfies it whatever it points at. So this is this project's "
        "own rule, not a criterion, and it is here because every page this build writes carries "
        "a skip link to its own <main>, which is a promise a page without one cannot keep"
    ),
}

_HEADING = re.compile(r"h([1-6])")

#: Tags whose accessible name can come from their own subtree.
_NAMED_FROM_CONTENT = frozenset({"a", "button"})

#: Attributes whose value is one or more ids that must exist on the page.
_ID_REFERENCES = ("aria-labelledby", "aria-describedby", "aria-controls", "for")

#: HTML void elements (WHATWG HTML section 13.1.2). They have no end tag, so a parser that
#: counted them as opening a subtree would never balance again: after one <img>, the depth is
#: permanently off by one and no enclosing element is ever closed. That is not hypothetical -
#: it is what made a <button> whose only content is an icon read as named when it was not.
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)


@dataclass
class _Named:
    """An element being checked for an accessible name, while its subtree is still open."""

    tag: str
    depth: int
    attrs: dict[str, str]
    text: str = ""


class _A11yParser(HTMLParser):
    """One pass over a page, collecting only what the rules above decide from."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.title = ""
        self.headings: list[int] = []
        self.ids: list[str] = []
        self.landmarks = 0
        self.images_without_alt: list[str] = []
        self.unnamed: list[tuple[str, str]] = []
        self.controls: list[tuple[str, dict[str, str]]] = []
        self.labelled_ids: set[str] = set()
        self.id_references: list[tuple[str, str]] = []
        self.fragments: list[str] = []
        self._in_title = False
        self._depth = 0
        self._open: list[_Named] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        got = {k: (v or "") for k, v in attrs}
        if tag in _VOID:
            self._record(tag, got)
            return
        self._depth += 1
        self._record(tag, got)
        if tag in _NAMED_FROM_CONTENT:
            self._open.append(_Named(tag, self._depth, got))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """A void or self-closed element opens no subtree, so it never joins the name stack."""
        self._record(tag, {k: (v or "") for k, v in attrs})

    def _record(self, tag: str, got: dict[str, str]) -> None:
        self._record_by_tag(tag, got)
        self._record_by_attribute(tag, got)

    def _record_by_tag(self, tag: str, got: dict[str, str]) -> None:
        """What an element contributes because of which element it is."""
        if tag == "html":
            self.lang = got.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "img":
            if "alt" not in got:
                self.images_without_alt.append(got.get("src", "(no src)"))
            elif got["alt"].strip():
                # An icon's alt text names the link or button it sits inside, which is how
                # this site's own menu close button gets its name.
                self._contribute(got["alt"])
        elif tag in {"input", "select", "textarea"}:
            self.controls.append((tag, got))
        elif tag == "label" and got.get("for", "").strip():
            self.labelled_ids.add(got["for"].strip())
        heading = _HEADING.fullmatch(tag)
        if heading:
            self.headings.append(int(heading.group(1)))

    def _record_by_attribute(self, tag: str, got: dict[str, str]) -> None:
        """What an element contributes because of what it declares."""
        if got.get("id"):
            self.ids.append(got["id"])
        if tag == "main" or got.get("role") == "main":
            self.landmarks += 1
        for attribute in _ID_REFERENCES:
            for token in got.get(attribute, "").split():
                self.id_references.append((attribute, token))
        if tag == "a" and got.get("href", "").startswith("#") and len(got["href"]) > 1:
            self.fragments.append(got["href"][1:])

    def _contribute(self, text: str) -> None:
        for element in self._open:
            element.text += text

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        self._contribute(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            # A stray "</img>" is not something this generator writes, but the audit reads
            # whatever is in the directory, and letting it decrement a depth no start tag
            # incremented would unbalance every enclosing element after it.
            return
        while self._open and self._open[-1].depth >= self._depth:
            element = self._open.pop()
            if not self._named(element):
                self.unnamed.append(
                    (element.tag, element.attrs.get("href") or element.attrs.get("id") or "(no id)")
                )
        self._depth = max(0, self._depth - 1)

    @staticmethod
    def _named(element: _Named) -> bool:
        return bool(
            element.text.strip()
            or element.attrs.get("aria-label", "").strip()
            or element.attrs.get("aria-labelledby", "").strip()
            or element.attrs.get("title", "").strip()
        )


def _control_is_named(tag: str, got: dict[str, str], labelled_ids: set[str]) -> bool:
    """Whether a form control has an accessible name from any source HTML can supply.

    Decided after the whole page is read, not at the tag, because the most common source is a
    ``<label for>`` that may appear either side of the control it names.
    """
    if any(got.get(attribute, "").strip() for attribute in ("aria-label", "aria-labelledby")):
        return True
    if got.get("title", "").strip():
        return True
    if got.get("id", "").strip() in labelled_ids:
        return True
    # A hidden input has no user-facing presence to name, and a submit or button input carries
    # its name in the value attribute rather than in text.
    return tag == "input" and (got.get("type") == "hidden" or bool(got.get("value", "").strip()))


def _page_findings(page: str, parser: _A11yParser) -> list[SiteFinding]:
    where = page_file(page)
    findings = []
    if not (parser.lang or "").strip():
        findings.append(SiteFinding("A11Y_PAGE_NOT_IN_A_LANGUAGE", where, "<html> has no lang"))
    if not parser.title.strip():
        findings.append(SiteFinding("A11Y_PAGE_NOT_TITLED", where, "no non-empty <title>"))
    if 1 not in parser.headings:
        findings.append(SiteFinding("A11Y_NO_TOP_LEVEL_HEADING", where, "no h1 on the page"))
    previous = 0
    for level in parser.headings:
        if previous and level > previous + 1:
            findings.append(
                SiteFinding("A11Y_HEADING_LEVEL_SKIPPED", where, f"h{previous} then h{level}")
            )
        previous = level
    findings += [
        SiteFinding("A11Y_IMAGE_WITHOUT_ALT", where, src) for src in parser.images_without_alt
    ]
    findings += [
        SiteFinding(
            "A11Y_LINK_WITHOUT_TEXT" if tag == "a" else "A11Y_CONTROL_WITHOUT_NAME",
            where,
            f"<{tag}> {identifier}",
        )
        for tag, identifier in parser.unnamed
    ]
    findings += [
        SiteFinding(
            "A11Y_CONTROL_WITHOUT_NAME",
            where,
            f"<{tag}> {got.get('id') or got.get('name') or '(unidentified)'}",
        )
        for tag, got in parser.controls
        if not _control_is_named(tag, got, parser.labelled_ids)
    ]
    seen = set()
    for identifier in parser.ids:
        if identifier in seen:
            findings.append(SiteFinding("A11Y_DUPLICATE_ID", where, identifier))
        seen.add(identifier)
    findings += [
        SiteFinding("A11Y_REFERENCE_TO_MISSING_ID", where, f"{attribute}={token}")
        for attribute, token in parser.id_references
        if token not in seen
    ]
    findings += [
        SiteFinding("A11Y_FRAGMENT_TARGET_MISSING", where, f"#{fragment}")
        for fragment in parser.fragments
        if fragment not in seen
    ]
    if parser.landmarks == 0:
        findings.append(SiteFinding("A11Y_NO_MAIN_LANDMARK", where, "no <main> and no role=main"))
    return findings


def audit_accessibility(root: Path) -> list[SiteFinding]:
    """Every accessibility rule a page under ``root`` breaks, in a stable order.

    An empty list means the rules in :data:`A11Y_CODES` hold. It does not mean the site is
    accessible; see the module docstring for what is out of reach of a static reader.
    """
    findings: list[SiteFinding] = []
    titles: dict[str, list[str]] = {}
    for page in page_paths(root):
        parser = _A11yParser()
        parser.feed((root / page_file(page)).read_text(encoding="utf-8"))
        findings += _page_findings(page, parser)
        titles.setdefault(" ".join(parser.title.split()), []).append(page_file(page))
    for title, pages in titles.items():
        if len(pages) > 1 and title:
            findings += [
                SiteFinding("A11Y_TITLE_NOT_UNIQUE", page, f"{title!r} is also used by another")
                for page in pages
            ]
    return sorted(findings, key=lambda f: (f.where, f.code, f.detail))
