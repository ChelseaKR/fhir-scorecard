"""Every accessibility rule and every budget, shown failing on a page that was clean before.

The method is the one ``tests/test_site_audit.py`` established. There are two positive
controls: the site the documented offline command builds, and ``GOOD_PAGE`` below, a minimal
page written to satisfy every rule at once. Each rule then gets a test that changes exactly
one thing in ``GOOD_PAGE`` and asserts that rule, and only that rule, fires. A mutation that
tripped a second rule would make the assertion pass for the wrong reason, so the assertions
are on the whole list rather than on membership.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fhir_scorecard.accessibility import A11Y_CODES, audit_accessibility
from fhir_scorecard.cli import main
from fhir_scorecard.site import DEFAULT_ORIGIN
from fhir_scorecard.weight import (
    MAX_PAGE_BYTES,
    MAX_SHARED_SUBRESOURCE_BYTES,
    WEIGHT_CODES,
    audit_weight,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: A page that satisfies every rule in ``A11Y_CODES``. Every mutation below starts here.
GOOD_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><title>A title used by no other page</title></head>
<body>
<a href="#content">Skip to main content</a>
<main id="content">
<h1>Top level</h1>
<h2>One level down</h2>
<img src="/assets/favicon.svg" alt="">
<a href="/payers/">Payer endpoints</a>
<button type="button"><img src="/assets/favicon.svg" alt="Close"></button>
<label for="q">Search</label><input id="q" name="q">
<p id="hint">Enter part of an organization name.</p>
<div aria-describedby="hint">Described by the hint above.</div>
</main>
</body>
</html>
"""


def _build(out: Path) -> Path:
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(FIXTURES),
                "--registry",
                str(FIXTURES / "registry.json"),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    return out


@pytest.fixture
def site(tmp_path: Path) -> Path:
    return _build(tmp_path / "site")


@pytest.fixture
def page(tmp_path: Path) -> Path:
    """A directory holding only ``GOOD_PAGE``, so a finding can come from nowhere else."""
    root = tmp_path / "one-page"
    root.mkdir()
    (root / "index.html").write_text(GOOD_PAGE, encoding="utf-8")
    return root


def _mutate(root: Path, before: str, after: str) -> Path:
    text = (root / "index.html").read_text(encoding="utf-8")
    assert before in text, before
    (root / "index.html").write_text(text.replace(before, after, 1), encoding="utf-8")
    return root


def _codes(root: Path) -> list[str]:
    return [finding.code for finding in audit_accessibility(root)]


# --- positive controls ---


def test_the_real_build_passes_every_accessibility_rule(site: Path) -> None:
    assert audit_accessibility(site) == []


def test_the_real_build_is_inside_both_budgets(site: Path) -> None:
    assert audit_weight(site) == []


def test_the_reference_page_passes_every_accessibility_rule(page: Path) -> None:
    assert audit_accessibility(page) == []


# --- one rule per test ---


def test_a_page_with_no_language_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, '<html lang="en">', "<html>")) == ["A11Y_PAGE_NOT_IN_A_LANGUAGE"]


def test_a_page_with_an_empty_language_is_caught(page: Path) -> None:
    """``lang=""`` is an attribute that declares nothing, and a rule testing only for the
    attribute's presence would pass it."""
    assert _codes(_mutate(page, 'lang="en"', 'lang=" "')) == ["A11Y_PAGE_NOT_IN_A_LANGUAGE"]


def test_a_page_with_no_title_is_caught(page: Path) -> None:
    assert _codes(
        _mutate(page, "<title>A title used by no other page</title>", "<title></title>")
    ) == ["A11Y_PAGE_NOT_TITLED"]


def test_two_pages_sharing_a_title_are_both_caught(page: Path) -> None:
    second = page / "copy"
    second.mkdir()
    (second / "index.html").write_text(GOOD_PAGE, encoding="utf-8")
    assert _codes(page) == ["A11Y_TITLE_NOT_UNIQUE", "A11Y_TITLE_NOT_UNIQUE"]


def test_a_page_with_no_h1_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, "<h1>Top level</h1>\n", "")) == ["A11Y_NO_TOP_LEVEL_HEADING"]


def test_a_skipped_heading_level_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, "<h2>One level down</h2>", "<h4>Three levels down</h4>")) == [
        "A11Y_HEADING_LEVEL_SKIPPED"
    ]


def test_an_image_with_no_alt_attribute_is_caught(page: Path) -> None:
    assert _codes(
        _mutate(page, '<img src="/assets/favicon.svg" alt="">', '<img src="/x.svg">')
    ) == ["A11Y_IMAGE_WITHOUT_ALT"]


def test_an_empty_alt_is_accepted_because_a_decorative_image_has_no_name(page: Path) -> None:
    """The rule has to distinguish "declared decorative" from "nobody said". A rule that
    rejected alt="" would push authors into describing a divider."""
    assert audit_accessibility(page) == []
    assert 'alt=""' in (page / "index.html").read_text(encoding="utf-8")


def test_an_unlabelled_control_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, '<label for="q">Search</label>', "")) == [
        "A11Y_CONTROL_WITHOUT_NAME"
    ]


def test_a_button_whose_only_content_loses_its_alt_is_caught(page: Path) -> None:
    """A button named by the alt text of the icon inside it, which is how the site's own menu
    close button is named. Removing the alt leaves the button with no name at all, and the
    rule has to see through the nesting to say so."""
    assert _codes(
        _mutate(page, '<img src="/assets/favicon.svg" alt="Close">', '<img src="/x.svg" alt="">')
    ) == ["A11Y_CONTROL_WITHOUT_NAME"]


def test_a_link_with_no_text_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, ">Payer endpoints</a>", "></a>")) == ["A11Y_LINK_WITHOUT_TEXT"]


def test_a_link_named_only_by_aria_label_is_accepted(page: Path) -> None:
    _mutate(
        page,
        '<a href="/payers/">Payer endpoints</a>',
        '<a href="/payers/" aria-label="Payer endpoints"></a>',
    )
    assert audit_accessibility(page) == []


def test_a_duplicate_id_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, '<p id="hint">', '<p id="content">')) == [
        "A11Y_DUPLICATE_ID",
        "A11Y_REFERENCE_TO_MISSING_ID",
    ]


def test_a_reference_to_an_absent_id_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, 'aria-describedby="hint"', 'aria-describedby="absent"')) == [
        "A11Y_REFERENCE_TO_MISSING_ID"
    ]


def test_a_broken_skip_link_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, 'href="#content"', 'href="#main-content"')) == [
        "A11Y_FRAGMENT_TARGET_MISSING"
    ]


def test_a_page_with_no_main_landmark_is_caught(page: Path) -> None:
    assert _codes(_mutate(page, '<main id="content">', '<div id="content">')) == [
        "A11Y_NO_MAIN_LANDMARK"
    ]


def test_a_role_main_counts_as_the_landmark(page: Path) -> None:
    _mutate(page, '<main id="content">', '<div id="content" role="main">')
    _mutate(page, "</main>", "</div>")
    assert audit_accessibility(page) == []


#: The eight rules a WCAG 2.2 success criterion actually requires, and the four that are this
#: project's own. Pinned here rather than counted at runtime: a criterion number on a rule the
#: criterion does not require is a fabricated citation, and the point of the split is that it
#: cannot drift by somebody editing a description string.
CRITERION_BACKED = frozenset(
    {
        "A11Y_PAGE_NOT_IN_A_LANGUAGE",
        "A11Y_PAGE_NOT_TITLED",
        "A11Y_IMAGE_WITHOUT_ALT",
        "A11Y_CONTROL_WITHOUT_NAME",
        "A11Y_LINK_WITHOUT_TEXT",
        "A11Y_REFERENCE_TO_MISSING_ID",
        "A11Y_FRAGMENT_TARGET_MISSING",
    }
)
PROJECT_RULES = frozenset(
    {
        "A11Y_TITLE_NOT_UNIQUE",
        "A11Y_NO_TOP_LEVEL_HEADING",
        "A11Y_HEADING_LEVEL_SKIPPED",
        "A11Y_DUPLICATE_ID",
        "A11Y_NO_MAIN_LANDMARK",
    }
)


def test_a_rule_either_names_a_criterion_or_says_it_is_this_projects_own() -> None:
    """Every rule is in exactly one of the two sets, and its own text says which.

    This is the accessibility gate held to the standard the grading rules are held to: a
    finding may cite a specification only where the specification requires what the finding
    reports. Two rules used to fail it. ``A11Y_HEADING_LEVEL_SKIPPED`` cited SC 1.3.1, which is
    satisfied by structure that is programmatically determined however the levels are numbered,
    and ``A11Y_NO_MAIN_LANDMARK`` cited SC 1.3.1 and SC 2.4.1, neither of which requires a main
    landmark; SC 2.4.1 Bypass Blocks is met by a skip link to any target. Both are worth
    checking on this site and neither is a Level A requirement, which is what "this project's
    own rule" is for.
    """
    assert set(A11Y_CODES) == CRITERION_BACKED | PROJECT_RULES
    assert not CRITERION_BACKED & PROJECT_RULES
    for code in CRITERION_BACKED:
        assert "WCAG 2.2 SC" in A11Y_CODES[code], code
        assert "this project's own rule" not in A11Y_CODES[code], code
    for code in PROJECT_RULES:
        assert "this project's own rule" in A11Y_CODES[code], code
        assert "WCAG 2.2 SC" not in A11Y_CODES[code], code


def test_every_emitted_accessibility_code_is_documented(page: Path) -> None:
    _mutate(page, '<html lang="en">', "<html>")
    _mutate(page, "<title>A title used by no other page</title>", "<title></title>")
    _mutate(page, "<h1>Top level</h1>\n", "")
    _mutate(page, '<main id="content">', "<div>")
    for code in _codes(page):
        assert code in A11Y_CODES, code


# --- budgets ---


def test_a_page_over_its_budget_is_caught(site: Path) -> None:
    home = site / "index.html"
    home.write_text(
        home.read_text(encoding="utf-8").replace(
            "</body>", "<!--" + "x" * MAX_PAGE_BYTES + "--></body>"
        ),
        encoding="utf-8",
    )
    assert [f.code for f in audit_weight(site)] == ["WEIGHT_PAGE_OVER_BUDGET"]


def test_a_subresource_only_one_page_links_counts_against_that_page(site: Path) -> None:
    """The badge on an endpoint page is that page's cost, not the site's. A page just inside
    the budget on its HTML alone must fail once a resource nobody else links pushes it over."""
    endpoint = site / "endpoint" / "cms-blue-button-2" / "index.html"
    (site / "badge" / "cms-blue-button-2.svg").write_bytes(b"x" * MAX_PAGE_BYTES)
    assert [(f.code, f.where) for f in audit_weight(site)] == [
        ("WEIGHT_PAGE_OVER_BUDGET", "endpoint/cms-blue-button-2/index.html")
    ]
    assert endpoint.stat().st_size < MAX_PAGE_BYTES, "the HTML alone was inside the budget"


def test_a_shared_subresource_growing_past_the_budget_is_caught(site: Path) -> None:
    """The case this budget exists for: a design-system bump. Every page links site.css, so
    its bytes are shared, and growing it is what has to fail."""
    (site / "assets" / "site.css").write_bytes(b"x" * MAX_SHARED_SUBRESOURCE_BYTES)
    assert [f.code for f in audit_weight(site)] == ["WEIGHT_SUBRESOURCES_OVER_BUDGET"]


def test_a_subresource_the_build_did_not_write_is_caught(site: Path) -> None:
    (site / "assets" / "site.css").unlink()
    assert "WEIGHT_SUBRESOURCE_MISSING" in [f.code for f in audit_weight(site)]


def test_the_shared_total_stays_off_the_page_budget(site: Path) -> None:
    """A regression guard on the split itself. If shared assets were counted per page, every
    page would carry 624,450 bytes and the page budget would fail on the first run."""
    assert audit_weight(site) == []
    biggest = max((site / f).stat().st_size for f in ("index.html", "how-we-grade/index.html"))
    assert biggest < MAX_PAGE_BYTES


def test_every_emitted_weight_code_is_documented(site: Path) -> None:
    (site / "assets" / "site.css").write_bytes(b"x" * MAX_SHARED_SUBRESOURCE_BYTES)
    (site / "assets" / "favicon.svg").unlink()
    for finding in audit_weight(site):
        assert finding.code in WEIGHT_CODES, finding.code


# --- the command runs all three families ---


def test_the_command_fails_on_an_accessibility_defect_alone(site: Path) -> None:
    """The site contract is untouched here, so a nonzero exit can only come from the
    accessibility family."""
    from fhir_scorecard.audit import audit_site

    page = site / "payers" / "index.html"
    page.write_text(page.read_text(encoding="utf-8").replace('<html lang="en">', "<html>"), "utf-8")
    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert main(["audit-site", str(site)]) == 1


def test_the_command_fails_on_a_budget_alone(site: Path) -> None:
    from fhir_scorecard.audit import audit_site

    (site / "assets" / "site.css").write_bytes(b"x" * MAX_SHARED_SUBRESOURCE_BYTES)
    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert main(["audit-site", str(site)]) == 1


def test_a_link_named_only_by_aria_labelledby_is_accepted(page: Path) -> None:
    _mutate(
        page,
        '<a href="/payers/">Payer endpoints</a>',
        '<a href="/payers/" aria-labelledby="hint"></a>',
    )
    assert audit_accessibility(page) == []


def test_a_button_named_only_by_title_is_accepted(page: Path) -> None:
    _mutate(page, '<button type="button">', '<button type="button" title="Close the menu">')
    _mutate(page, '<img src="/assets/favicon.svg" alt="Close">', "")
    assert audit_accessibility(page) == []


def test_a_stray_closing_void_tag_does_not_unbalance_the_page(page: Path) -> None:
    """`</img>` is not valid and this generator never writes one, but the audit reads whatever
    is in the directory. Treating it as a real end tag would close the enclosing button early
    and report every element after it as unnamed."""
    _mutate(
        page, '<img src="/assets/favicon.svg" alt="Close">', '<img src="/x.svg" alt="Close"></img>'
    )
    assert audit_accessibility(page) == []


@pytest.mark.parametrize(
    ("control", "why"),
    [
        ('<input id="q" name="q" aria-label="Search">', "aria-label"),
        ('<input id="q" name="q" title="Search">', "title"),
        ('<input id="q" name="q" type="hidden">', "a hidden input has nothing to name"),
        ('<input id="q" name="q" type="submit" value="Search">', "value names a submit"),
    ],
)
def test_a_control_named_without_a_label_element_is_accepted(
    page: Path, control: str, why: str
) -> None:
    """Every source of an accessible name a form control can carry in markup. Each of these
    is a real pattern; a rule that only understood <label for> would report all four."""
    _mutate(page, '<label for="q">Search</label>', "")
    assert _codes(_mutate(page, '<input id="q" name="q">', control)) == [], why


def test_the_published_split_is_the_split_the_rules_actually_have() -> None:
    """Seven and five, recomputed from the rule text and required of every document that says it.

    The counts were stated in seven places and computed in none. `A11Y_NO_TOP_LEVEL_HEADING`
    cited SC 1.3.1, which no more requires a top-level heading than it requires sequential
    heading levels - the rule directly below it makes exactly that argument and is classed as
    this project's own. A fabricated citation is the one thing this repository cannot publish,
    so the miscount mattered more than its size.
    """
    root = Path(__file__).resolve().parent.parent
    backed = sum(1 for text in A11Y_CODES.values() if "WCAG 2.2 SC" in text)
    own = sum(1 for text in A11Y_CODES.values() if "this project's own rule" in text)
    assert backed == len(CRITERION_BACKED) == 7
    assert own == len(PROJECT_RULES) == 5
    assert backed + own == len(A11Y_CODES) == 12

    words = {7: "seven", 12: "twelve", 5: "five"}
    for name in (
        "README.md",
        "ROADMAP.md",
        "docs/RESPONSIBLE-TECH-AUDITS.md",
        "docs/adr/0004-accessibility-and-weight-gates-without-a-browser.md",
    ):
        prose = " ".join((root / name).read_text(encoding="utf-8").split()).lower()
        # Each document words the total differently ("twelve mechanical rules", "twelve rules
        # over the built HTML"), so the number is what is required, not the phrasing.
        assert words[len(A11Y_CODES)] in prose, name
        assert words[backed] in prose, name
        assert words[own] in prose, name
        # The count that was wrong, in the shape each document used to write it.
        assert "eight naming the wcag" not in prose, name
        assert "eight name the wcag" not in prose, name
