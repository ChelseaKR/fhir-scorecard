"""The site contract must be able to fail, one defect class at a time.

An audit that only ever runs against a correct site proves nothing about the audit. Every
rule in ``fhir_scorecard.audit.FINDING_CODES`` therefore gets a test that builds a real site,
breaks exactly one property, and asserts that rule fires - and the same built site, unbroken,
is asserted clean, so a red result cannot be red for some unrelated reason.

The site under test is built by the documented offline command, not hand-written, so the
audit is held against what the generator actually emits.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from fhir_scorecard.audit import FINDING_CODES, SiteFinding, audit_site
from fhir_scorecard.cli import main
from fhir_scorecard.site import DEFAULT_ORIGIN

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _build(
    out: Path,
    *,
    origin: str = DEFAULT_ORIGIN,
    cohorts: Path | None = None,
    fixtures: Path = FIXTURES,
    registry: Path | None = None,
) -> Path:
    argv = [
        "grade",
        "--offline",
        "--fixtures",
        str(fixtures),
        "--registry",
        str(registry or fixtures / "registry.json"),
        "--out",
        str(out),
        "--origin",
        origin,
    ]
    if cohorts is not None:
        argv += ["--cohorts", str(cohorts)]
    assert main(argv) == 0
    return out


@pytest.fixture
def site(tmp_path: Path) -> Path:
    return _build(tmp_path / "site")


def _codes(root: Path, origin: str = DEFAULT_ORIGIN) -> list[str]:
    return [finding.code for finding in audit_site(root, origin)]


def test_the_documented_offline_build_satisfies_the_contract(site: Path) -> None:
    """The positive control every other test in this file leans on."""
    assert audit_site(site, DEFAULT_ORIGIN) == []


def test_a_site_with_cohort_and_organization_pages_also_satisfies_it(tmp_path: Path) -> None:
    """The offline fixture registry has one endpoint per organization and no cohort, so the
    control above never reaches ``cohort_page`` or ``org_page``. This one does: a second
    surface for one organization produces an /org/ page, and a cohort file over the fixture
    endpoints produces a cohort page. The audit has to accept both shapes rather than only the
    ones it happened to be written against."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    shutil.copytree(fixtures / "cms-blue-button-2", fixtures / "cms-blue-button-2-pd")
    registry = json.loads((FIXTURES / "registry.json").read_text(encoding="utf-8"))
    first = registry["endpoints"][0]
    assert first["id"] == "cms-blue-button-2"
    second = dict(first)
    second["id"] = "cms-blue-button-2-pd"
    second["name"] = first["name"] + " Provider Directory"
    registry["endpoints"].append(second)
    (fixtures / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    cohorts = tmp_path / "cohorts"
    cohorts.mkdir()
    (cohorts / "fixture-cohort.json").write_text(
        json.dumps(
            {
                "cohort": {
                    "id": "fixture-cohort",
                    "name": "Fixture cohort",
                    "description": "A cohort over the captured fixture endpoints.",
                },
                "sources": [
                    {
                        "label": "tests/fixtures/registry.json",
                        "url": "https://example.test/roster",
                        "date": "2026-08-27",
                    }
                ],
                "members": [
                    {
                        "id": "cms",
                        "name": "CMS Blue Button 2.0 (Medicare)",
                        "programs": ["tx-marketplace"],
                        "endpoints": ["cms-blue-button-2", "cms-blue-button-2-pd"],
                    },
                    {
                        "id": "absent-plan",
                        "name": "Absent Plan",
                        "programs": ["tx-marketplace"],
                        "excluded": {
                            "reason": "publishes no base URL",
                            "basis": "portal_reviewed",
                            "reviewed": {
                                "method": "retrieved the plan's developer page",
                                "date": "2026-08-27",
                                "source": "https://example.test/developers",
                            },
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    built = _build(tmp_path / "site", cohorts=cohorts, fixtures=fixtures)
    assert (built / "fixture-cohort" / "index.html").is_file()
    org_pages = sorted(p.parent.name for p in (built / "org").rglob("index.html"))
    assert org_pages, "the two-surface organization should have produced an /org/ page"
    assert audit_site(built, DEFAULT_ORIGIN) == []


def test_every_emitted_code_is_documented(site: Path) -> None:
    """No rule may report under a name ``FINDING_CODES`` does not explain."""
    broken = site / "orphan-page"
    broken.mkdir()
    (broken / "index.html").write_text("<html><body>nothing</body></html>", encoding="utf-8")
    (site / "sitemap.xml").write_text("not xml at all", encoding="utf-8")
    (site / "robots.txt").unlink()
    for code in _codes(site):
        assert code in FINDING_CODES, code


# --- one defect class per test, each shown firing on a site that was clean before ---


def test_a_page_missing_from_the_sitemap_is_caught(site: Path) -> None:
    text = (site / "sitemap.xml").read_text(encoding="utf-8")
    dropped = re.sub(r"<url><loc>[^<]*/payers/</loc>.*?</url>", "", text, count=1)
    assert dropped != text
    (site / "sitemap.xml").write_text(dropped, encoding="utf-8")
    assert "PAGE_MISSING_FROM_SITEMAP" in _codes(site)


def test_a_sitemap_entry_nothing_answers_is_caught(site: Path) -> None:
    text = (site / "sitemap.xml").read_text(encoding="utf-8")
    (site / "sitemap.xml").write_text(
        text.replace(
            "</urlset>",
            f"<url><loc>{DEFAULT_ORIGIN}/endpoint/never-built/</loc></url></urlset>",
        ),
        encoding="utf-8",
    )
    assert "SITEMAP_ENTRY_NOT_BUILT" in _codes(site)


def test_a_sitemap_entry_on_another_origin_is_caught(site: Path) -> None:
    text = (site / "sitemap.xml").read_text(encoding="utf-8")
    (site / "sitemap.xml").write_text(
        text.replace("</urlset>", "<url><loc>https://elsewhere.test/payers/</loc></url></urlset>"),
        encoding="utf-8",
    )
    assert "SITEMAP_ENTRY_OFF_ORIGIN" in _codes(site)


def test_an_unreadable_sitemap_is_caught(site: Path) -> None:
    (site / "sitemap.xml").write_text("<html>not a urlset</html>", encoding="utf-8")
    assert "SITEMAP_UNPARSEABLE" in _codes(site)


def test_a_missing_sitemap_is_caught(site: Path) -> None:
    (site / "sitemap.xml").unlink()
    assert "SITEMAP_UNPARSEABLE" in _codes(site)


def test_a_page_with_no_canonical_is_caught(site: Path) -> None:
    page = site / "payers" / "index.html"
    page.write_text(
        re.sub(r'<link rel="canonical"[^>]*>', "", page.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    assert "CANONICAL_MISSING" in _codes(site)


def test_a_second_canonical_is_caught(site: Path) -> None:
    page = site / "payers" / "index.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        text.replace("</head>", f'<link rel="canonical" href="{DEFAULT_ORIGIN}/"></head>'),
        encoding="utf-8",
    )
    assert "CANONICAL_DUPLICATED" in _codes(site)


def test_a_canonical_addressing_another_page_is_caught(site: Path) -> None:
    page = site / "payers" / "index.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        text.replace(
            f'canonical" href="{DEFAULT_ORIGIN}/payers/"', f'canonical" href="{DEFAULT_ORIGIN}/"'
        ),
        encoding="utf-8",
    )
    assert "CANONICAL_MISMATCH" in _codes(site)


def test_a_canonical_pointing_at_the_wrong_origin_is_caught(site: Path) -> None:
    """The live regression this rule exists for: the day the custom domain started serving,
    internal addresses still named the project-page host. A canonical is the one address a
    search engine is told to keep, so it gets its own assertion."""
    assert "CANONICAL_MISMATCH" in _codes(site, "https://chelseakr.github.io/fhir-scorecard")


def test_structured_data_that_does_not_parse_is_caught(site: Path) -> None:
    page = site / "index.html"
    text = page.read_text(encoding="utf-8")
    broken = re.sub(
        r'(<script type="application/ld\+json">)\{',
        r"\1{,",
        text,
        count=1,
    )
    assert broken != text
    page.write_text(broken, encoding="utf-8")
    assert "JSONLD_UNPARSEABLE" in _codes(site)


def test_structured_data_missing_a_promised_field_is_caught(site: Path) -> None:
    page = site / "index.html"
    text = page.read_text(encoding="utf-8")
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
    assert block is not None
    payload = json.loads(block.group(1))
    assert payload["@type"] == "Dataset"
    del payload["description"]
    page.write_text(text.replace(block.group(1), json.dumps(payload)), encoding="utf-8")
    assert "JSONLD_INCOMPLETE" in _codes(site)


def test_structured_data_that_is_not_an_object_is_caught(site: Path) -> None:
    page = site / "index.html"
    text = page.read_text(encoding="utf-8")
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
    assert block is not None
    page.write_text(text.replace(block.group(1), '["Dataset"]'), encoding="utf-8")
    assert "JSONLD_INCOMPLETE" in _codes(site)


def test_a_link_to_a_path_the_build_never_wrote_is_caught(site: Path) -> None:
    page = site / "payers" / "index.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(text.replace('href="/claim/"', 'href="/claim-form/"'), encoding="utf-8")
    assert "INTERNAL_LINK_UNBUILT" in _codes(site)


def test_a_missing_subresource_is_caught(site: Path) -> None:
    """Same rule, but reached through ``src`` rather than ``href``: the vendored stylesheet
    and scripts are the site's only assets and are served from its own origin, so a build that
    stopped copying them would otherwise publish unstyled pages silently."""
    (site / "assets" / "site.css").unlink()
    assert "INTERNAL_LINK_UNBUILT" in _codes(site)


def test_a_page_nothing_links_to_is_caught(site: Path) -> None:
    orphan = site / "endpoint" / "cms-blue-button-2"
    stray = site / "endpoint" / "stray-copy"
    shutil.copytree(orphan, stray)
    text = (stray / "index.html").read_text(encoding="utf-8")
    # Readdress the copy completely. A page states where it is twice -- in its
    # canonical and in its share card -- and moving only one of them would break a
    # second rule, leaving this test asserting two defects while claiming one.
    (stray / "index.html").write_text(
        text.replace(
            f"{DEFAULT_ORIGIN}/endpoint/cms-blue-button-2/",
            f"{DEFAULT_ORIGIN}/endpoint/stray-copy/",
        ),
        encoding="utf-8",
    )
    sitemap = site / "sitemap.xml"
    sitemap.write_text(
        sitemap.read_text(encoding="utf-8").replace(
            "</urlset>",
            f"<url><loc>{DEFAULT_ORIGIN}/endpoint/stray-copy/</loc></url></urlset>",
        ),
        encoding="utf-8",
    )
    assert _codes(site) == ["ORPHAN_PAGE"]


def test_a_missing_robots_file_is_caught(site: Path) -> None:
    (site / "robots.txt").unlink()
    assert "ROBOTS_SITEMAP_MISMATCH" in _codes(site)


def test_a_robots_file_pointing_at_another_sitemap_is_caught(site: Path) -> None:
    (site / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nSitemap: https://elsewhere.test/sitemap.xml\n", encoding="utf-8"
    )
    assert "ROBOTS_SITEMAP_MISMATCH" in _codes(site)


def test_an_empty_directory_is_reported_rather_than_passing(tmp_path: Path) -> None:
    """A site nobody built must not audit clean. The whole gate is worthless if pointing it
    at the wrong directory is indistinguishable from pointing it at a correct site."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert audit_site(empty, DEFAULT_ORIGIN) != []


def test_findings_render_with_their_location(site: Path) -> None:
    finding = SiteFinding("ORPHAN_PAGE", "endpoint/x/index.html", "detail")
    assert str(finding) == "ORPHAN_PAGE  endpoint/x/index.html  detail"
    assert str(SiteFinding("SITEMAP_UNPARSEABLE", "", "d")) == "SITEMAP_UNPARSEABLE  (site)  d"


def test_the_command_exits_nonzero_on_a_broken_site(site: Path, capsys: object) -> None:
    (site / "robots.txt").unlink()
    assert main(["audit-site", str(site)]) == 1


def test_the_command_exits_zero_on_the_real_build(site: Path) -> None:
    assert main(["audit-site", str(site)]) == 0


def test_the_command_honours_a_different_origin(site: Path) -> None:
    assert main(["audit-site", str(site), "--origin", "https://elsewhere.test"]) == 1


def test_the_command_refuses_a_directory_that_is_not_there(tmp_path: Path) -> None:
    assert main(["audit-site", str(tmp_path / "absent")]) == 2


# --- shapes the generator does not emit today, which the audit still has to resolve ---


PROJECT_PAGE_ORIGIN = "https://chelseakr.github.io/fhir-scorecard"


def test_a_site_served_under_a_path_audits_against_that_path(tmp_path: Path) -> None:
    """The project-page shape this site was served under until 2026-08-19, where every
    internal href carries the repository path. The audit has to strip that prefix the same way
    the renderer adds it, or a correct build under that origin reads as entirely broken."""
    built = _build(tmp_path / "site", origin=PROJECT_PAGE_ORIGIN)
    assert 'href="/fhir-scorecard/payers/"' in (built / "index.html").read_text(encoding="utf-8")
    assert audit_site(built, PROJECT_PAGE_ORIGIN) == []


def _add_page(site: Path, path: str, body: str) -> None:
    """Write one hand-authored page into a built site, linked and sitemapped, so the audit's
    only complaint can be about ``body``."""
    page = site / path
    page.mkdir(parents=True)
    (page / "index.html").write_text(
        '<!DOCTYPE html><html lang="en"><head><title>Extra</title>'
        f'<link rel="canonical" href="{DEFAULT_ORIGIN}/{path}/">'
        f"</head><body>{body}</body></html>",
        encoding="utf-8",
    )
    home = site / "index.html"
    home.write_text(
        home.read_text(encoding="utf-8").replace("</body>", f'<a href="/{path}/">extra</a></body>'),
        encoding="utf-8",
    )
    sitemap = site / "sitemap.xml"
    sitemap.write_text(
        sitemap.read_text(encoding="utf-8").replace(
            "</urlset>", f"<url><loc>{DEFAULT_ORIGIN}/{path}/</loc></url></urlset>"
        ),
        encoding="utf-8",
    )


def test_references_this_generator_never_writes_are_still_resolved(site: Path) -> None:
    """A relative link, a parent-relative link, an inline script, and a link element with no
    href. None of these come out of `site.py` today; all of them are ordinary HTML, and an
    audit that mis-resolved one would report a defect in a page that has none."""
    _add_page(
        site,
        "extra",
        '<link rel="preload">'
        "<script>const inline = 1;</script>"
        '<a href="../payers/">up and over</a>'
        '<a href="./">itself</a>',
    )
    assert audit_site(site, DEFAULT_ORIGIN) == []


def test_a_relative_link_to_nothing_is_caught(site: Path) -> None:
    """Same resolution path, wrong target: the rule has to fire on a relative reference too,
    or the test above would only be proving the audit ignores them."""
    _add_page(site, "extra", '<a href="../not-built/">nowhere</a>')
    assert "INTERNAL_LINK_UNBUILT" in _codes(site)


def test_a_list_valued_type_is_held_only_to_the_declarations(site: Path) -> None:
    """JSON-LD allows `@type` to be a list. This site emits a single string, and the per-type
    field list is keyed by one; a list names several types at once, so holding the block to any
    one of their field lists would be this project inventing a requirement. It is held to
    `@context` and `@type`, and those still have to be there."""
    page = site / "index.html"
    text = page.read_text(encoding="utf-8")
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
    assert block is not None
    payload = json.loads(block.group(1))
    del payload["description"]
    payload["@type"] = ["Dataset", "CreativeWork"]
    page.write_text(text.replace(block.group(1), json.dumps(payload)), encoding="utf-8")
    assert "JSONLD_INCOMPLETE" not in _codes(site)

    payload.pop("@context")
    page.write_text(text.replace(block.group(1), json.dumps(payload)), encoding="utf-8")
    assert "JSONLD_INCOMPLETE" in _codes(site)


def test_a_dot_dot_at_the_site_root_is_dropped_rather_than_escaping_it(site: Path) -> None:
    """RFC 3986 section 5.2.4 removes leading `..` segments instead of resolving above the
    root, which is what a browser does with `../payers/` on the home page. The audit has to
    agree: resolving it to something outside the site would report a page that exists as a
    link to a path the build did not write."""
    home = site / "index.html"
    home.write_text(
        home.read_text(encoding="utf-8").replace(
            "</body>", '<a href="../payers/">the same page, addressed the long way</a></body>'
        ),
        encoding="utf-8",
    )
    assert audit_site(site, DEFAULT_ORIGIN) == []


# --- the share card, which is copy this site publishes and does not otherwise reread ---


def test_a_clean_site_carries_a_complete_share_card(site: Path) -> None:
    """Before any of the checks below mean anything, the built site has to pass them."""
    assert "SOCIAL_CARD_INCOMPLETE" not in _codes(site)
    home = (site / "index.html").read_text(encoding="utf-8")
    for tag in ("og:site_name", "og:locale", "twitter:card", "twitter:title"):
        assert tag in home, tag


def test_a_half_written_share_card_is_caught(site: Path) -> None:
    """A card missing a tag is one a crawler completes from somewhere else."""
    page = site / "payers" / "index.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        text.replace('<meta name="twitter:card" content="summary">\n', ""), encoding="utf-8"
    )
    assert "SOCIAL_CARD_INCOMPLETE" in _codes(site)


def test_a_card_that_says_something_the_page_does_not_is_caught(site: Path) -> None:
    """The site's copy is reviewed. A card that drifts from it is copy nobody rereads."""
    page = site / "payers" / "index.html"
    text = page.read_text(encoding="utf-8")
    block = re.search(r'<meta property="og:description" content="([^"]*)">', text)
    assert block is not None
    page.write_text(
        text.replace(
            block.group(0),
            '<meta property="og:description" content="The most trusted FHIR grades anywhere.">',
        ),
        encoding="utf-8",
    )
    assert "SOCIAL_CARD_INCOMPLETE" in _codes(site)


def test_a_card_addressing_another_page_is_caught(site: Path) -> None:
    page = site / "payers" / "index.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        text.replace(
            f'<meta property="og:url" content="{DEFAULT_ORIGIN}/payers/">',
            f'<meta property="og:url" content="{DEFAULT_ORIGIN}/providers/">',
        ),
        encoding="utf-8",
    )
    assert "SOCIAL_CARD_INCOMPLETE" in _codes(site)


def test_a_page_with_no_share_card_at_all_is_not_reported(site: Path) -> None:
    """The rule is that a card must be complete, not that every page must have one.

    A page declaring nothing publishes no claim to be wrong about; a crawler falls
    back to the title and description, which are checked by their own rules.
    """
    page = site / "payers" / "index.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        re.sub(r'<meta (?:property="og:|name="twitter:)[^>]*>\n?', "", text), encoding="utf-8"
    )
    assert "SOCIAL_CARD_INCOMPLETE" not in _codes(site)


@pytest.fixture
def site_with_an_org(tmp_path: Path) -> Path:
    """A build where one organization publishes two surfaces, so an org page exists.

    ``_write_site`` only writes ``/org/<slug>/`` for an organization with more than
    one endpoint, and the three fixture endpoints are three different organizations.
    This copies one endpoint's captured discovery documents under a second id whose
    name shares the organization prefix, which is exactly the shape the real registry
    has and the fixture registry does not.
    """
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    shutil.copytree(fixtures / "cms-blue-button-2", fixtures / "cms-blue-button-2-directory")
    registry = json.loads((FIXTURES / "registry.json").read_text(encoding="utf-8"))
    original = next(e for e in registry["endpoints"] if e["id"] == "cms-blue-button-2")
    registry["endpoints"].append(
        {
            **original,
            "id": "cms-blue-button-2-directory",
            "name": "CMS Blue Button 2.0 provider directory",
            "kind": "payer_provider_directory",
        }
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    return _build(tmp_path / "site", fixtures=fixtures, registry=registry_path)


def test_a_site_with_an_organization_page_is_still_clean(site_with_an_org: Path) -> None:
    assert audit_site(site_with_an_org, DEFAULT_ORIGIN) == []


def test_an_organization_page_publishes_the_organization_it_is_about(
    site_with_an_org: Path,
) -> None:
    """``REQUIRED_JSONLD_FIELDS`` has promised an Organization contract from the start.

    Nothing emitted one, so the promise was unkept and unkeepable: no page could fail
    a rule about a type no page carried.
    """
    org_pages = sorted((site_with_an_org / "org").glob("*/index.html"))
    assert org_pages, "the fixture registry builds no organization page"
    for page in org_pages:
        payloads = [
            json.loads(block)
            for block in re.findall(
                r'<script type="application/ld\+json">(.*?)</script>',
                page.read_text(encoding="utf-8"),
                re.DOTALL,
            )
        ]
        organizations = [p for p in payloads if p.get("@type") == "Organization"]
        assert len(organizations) == 1, page
        assert organizations[0]["url"].startswith(f"{DEFAULT_ORIGIN}/org/")
        # A grade is an observation of a surface, never a property of a company.
        assert "aggregateRating" not in organizations[0]
        assert "review" not in organizations[0]


def test_an_organization_missing_a_promised_field_is_caught(site_with_an_org: Path) -> None:
    page = next(iter(sorted((site_with_an_org / "org").glob("*/index.html"))))
    text = page.read_text(encoding="utf-8")
    block = re.search(
        r'<script type="application/ld\+json">(\{[^<]*"Organization"[^<]*\})</script>', text
    )
    assert block is not None
    payload = json.loads(block.group(1))
    payload.pop("url")
    page.write_text(text.replace(block.group(1), json.dumps(payload)), encoding="utf-8")
    assert "JSONLD_INCOMPLETE" in _codes(site_with_an_org)
