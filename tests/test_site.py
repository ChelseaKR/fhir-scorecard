from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import good_capability, good_smart

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.cli import main
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.site import (
    SOCIAL_CARD_SIZE,
    endpoint_page,
    org_slug,
    robots,
    sitemap,
    social_card_url,
    status_badge,
)


def _card(eid: str = "acme", kind: str = "payer", name: str = "Acme Health"):
    return build_scorecard(
        eid,
        name,
        FetchResult(
            url="https://a.test/metadata", ok=True, status=200, elapsed_ms=10, body=b"", error=None
        ),
        parse_capability(json.dumps(good_capability()).encode()),
        parse_smart(json.dumps(good_smart()).encode()),
        kind=kind,
        availability="answered 5 of 5 checks",
    )


def test_org_slug_strips_api_noise() -> None:
    assert org_slug("Cigna Patient Access API") == "cigna"
    assert org_slug("Cigna Provider Directory API") == "cigna"
    assert org_slug("Epic on FHIR public sandbox") == "epic-on-fhir"
    assert org_slug("!!!") == "unknown"


def test_org_display_name_is_the_shared_prefix_not_one_endpoints_name() -> None:
    """Naming an org page after whichever endpoint came first titled it with one surface."""
    from fhir_scorecard.site import org_display_name

    assert org_display_name(["Cigna Patient Access API", "Cigna Provider Directory API"]) == "Cigna"
    assert (
        org_display_name(
            ["Sharp Health Plan Patient Access API", "Sharp Health Plan Provider Directory API"]
        )
        == "Sharp Health Plan"
    )
    # Parenthetical qualifiers are dropped, so two releases of one server still share a name.
    assert (
        org_display_name(["HAPI FHIR public test server (R4)", "HAPI FHIR public test server (R5)"])
        == "HAPI FHIR public test server"
    )
    # Nothing shared: fall back rather than render an empty heading.
    assert org_display_name(["Alpha", "Beta"]) == "Alpha"
    assert org_display_name([]) == ""


def test_a_group_that_is_all_one_surface_is_not_named_after_that_surface() -> None:
    """The shared prefix is only about the organization when the surfaces differ.

    When every endpoint in a group is the *same* surface and differs only inside parentheses,
    the prefix is the whole name -- surface words included -- and the heuristic returned an API
    name. `/org/communitycare/` published "CommunityCare Provider Directory API" as its `h1`,
    `<title>`, meta description, every breadcrumb linking to it, and a schema.org `Organization`
    that a search engine ingests about a named third party.
    """
    from fhir_scorecard.site import org_display_name

    same_surface = [
        "CommunityCare Provider Directory API (marketplace)",
        "CommunityCare Provider Directory API (commercial)",
    ]
    assert org_display_name(same_surface) == "CommunityCare"

    name = org_display_name(same_surface)
    for surface in ("API", "Provider Directory", "Patient Access"):
        assert surface.casefold() not in name.casefold()

    # The same shape on the other surface a payer publishes.
    assert (
        org_display_name(
            [
                "Florida Blue Patient Access API (individual)",
                "Florida Blue Patient Access API (group)",
            ]
        )
        == "Florida Blue"
    )


def test_the_surface_strip_leaves_projects_whose_name_is_a_server_alone() -> None:
    """ "HAPI FHIR public test server" is what that project calls itself.

    The vocabulary removed here is narrower than `org_slug`'s on purpose: cutting these back to
    "HAPI FHIR" or "SMART Health IT" would invent a name rather than repair one.
    """
    from fhir_scorecard.site import org_display_name, strip_surface_tail

    assert strip_surface_tail("HAPI FHIR public test server") == "HAPI FHIR public test server"
    assert strip_surface_tail("SMART Health IT open sandbox") == "SMART Health IT open sandbox"
    assert strip_surface_tail("Firely public test server") == "Firely public test server"
    # A name that is nothing but surface words falls back rather than rendering empty.
    assert org_display_name(["Provider Directory API", "Provider Directory API"]) == (
        "Provider Directory API"
    )


def test_every_org_page_the_real_registry_builds_is_named_for_an_organization() -> None:
    """The measured claim, held against the shipped data rather than a hand-written example.

    Of the 24 groups that get an org page, exactly one was named after an API. The corrected
    name is the one `data/cohorts/oklahoma-marketplace.json` already cites for those two
    endpoint ids, from the CMS QHP landscape roster -- so this is not a coincidence of string
    trimming, it agrees with the project's own source of record.
    """
    import collections
    import json as _json

    from fhir_scorecard.site import org_display_name, org_slug

    repo = Path(__file__).resolve().parent.parent
    registry = _json.loads((repo / "data" / "registry.json").read_text(encoding="utf-8"))
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for entry in registry["endpoints"]:
        groups[org_slug(entry["name"])].append(entry["name"])
    multi = {slug: names for slug, names in groups.items() if len(names) > 1}
    assert len(multi) >= 20, "the scan found almost no org pages; it would pass over nothing"

    for slug, names in multi.items():
        name = org_display_name(names)
        assert name, slug
        assert not re.search(r"\b(api|apis|provider directory|patient access)\s*$", name, re.I), (
            f"/org/{slug}/ would publish {name!r} as an Organization"
        )

    cohort = _json.loads(
        (repo / "data" / "cohorts" / "oklahoma-marketplace.json").read_text(encoding="utf-8")
    )
    roster = next(m for m in cohort["members"] if m["id"] == "communitycare-oklahoma")
    assert set(roster["endpoints"]) == {
        "communitycare-provider-directory-qhp",
        "communitycare-provider-directory-commercial",
    }
    assert org_display_name(groups["communitycare"]) == roster["roster_name"] == "CommunityCare"


def test_endpoint_page_escapes_and_carries_structured_data() -> None:
    page = endpoint_page(
        _card(name="<script>x</script>"),
        base_url="https://a.test/r4",
        verified="live fetch",
        origin="https://example.test",
    )
    assert "<script>x</script>" not in page.body
    assert "&lt;script&gt;" in page.body
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', page.body, re.S)
    assert ld
    parsed = json.loads(ld.group(1))
    assert parsed["@type"] == "WebAPI"
    # Escaped, so registry data can never terminate the block early.
    assert parsed["name"] == "<script>x</script>"
    assert "\\u003c" in ld.group(1)


def test_sitemap_and_robots_are_wellformed() -> None:
    from fhir_scorecard.site import Page

    pages = [
        Page(path="", title="t", description="d", body=""),
        Page(path="endpoint/acme", title="t", description="d", body=""),
    ]
    xml = sitemap(pages, "https://example.test")
    assert xml.startswith("<?xml")
    assert "<loc>https://example.test/</loc>" in xml
    assert "<loc>https://example.test/endpoint/acme/</loc>" in xml
    assert "Sitemap: https://example.test/sitemap.xml" in robots("https://example.test")


def test_status_badge_is_accessible_and_escapes_registry_data() -> None:
    badge = status_badge(_card(name="A & B <Health>"))
    assert badge.startswith("<svg")
    assert 'role="img"' in badge
    assert "A &amp; B &lt;Health&gt;: FHIR grade A" in badge
    assert "#19734b" in badge
    assert "<Health>" not in badge


def _registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "id": "alpha",
                        "name": "Alpha Health Patient Access API",
                        "kind": "payer",
                        "base_url": "https://alpha.test/r4",
                        "verification": {"method": "fixture", "date": "2026-08-05"},
                    },
                    {
                        "id": "alpha-dir",
                        "name": "Alpha Health Provider Directory API",
                        "kind": "payer_provider_directory",
                        "base_url": "https://alpha.test/pd",
                        "verification": {"method": "fixture", "date": "2026-08-05"},
                    },
                ]
            }
        )
    )
    return path


def test_site_build_produces_indexable_pages(tmp_path: Path) -> None:
    for eid in ("alpha", "alpha-dir"):
        d = tmp_path / "fixtures" / eid
        d.mkdir(parents=True)
        (d / "metadata.json").write_text(json.dumps(good_capability()))
        (d / "smart.json").write_text(json.dumps(good_smart()))
    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--registry",
                str(_registry(tmp_path)),
                "--offline",
                "--fixtures",
                str(tmp_path / "fixtures"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "h.json"),
                "--origin",
                "https://example.test",
            ]
        )
        == 0
    )

    for rel in (
        "index.html",
        "how-we-grade/index.html",
        "payers/index.html",
        "provider-directories/index.html",
        "endpoint/alpha/index.html",
        "org/alpha-health/index.html",
        "badge/alpha.svg",
        "sitemap.xml",
        "robots.txt",
    ):
        assert (out / rel).is_file(), f"missing {rel}"

    home = (out / "index.html").read_text()
    assert '<link rel="canonical" href="https://example.test/"' in home
    assert '"@type": "Dataset"' in home
    assert '<html lang="en">' in home
    assert 'class="signal-panel"' in home
    assert 'class="usa-skipnav" href="#content">Skip to main content</a>' in home
    assert "/assets/uswds/css/uswds.min.css" in home
    assert "/assets/site.css" in home
    assert (out / "assets" / "uswds" / "css" / "uswds.min.css").is_file()
    assert (out / "assets" / "site.css").is_file()
    assert "prefers-reduced-motion" in (out / "assets" / "site.css").read_text()

    ep = (out / "endpoint" / "alpha" / "index.html").read_text()
    assert '<meta name="description"' in ep
    assert "https://alpha.test/r4" in ep
    assert "answered" in ep  # availability surfaced
    assert "/badge/alpha.svg" in ep
    assert "Share this endpoint's grade" in ep

    # Every generated page must appear in the sitemap: an orphan page is not indexable.
    xml = (out / "sitemap.xml").read_text()
    generated = {str(p.parent.relative_to(out)).replace(".", "") for p in out.rglob("index.html")}
    for rel in generated:
        loc = f"https://example.test/{rel + '/' if rel else ''}"
        assert f"<loc>{loc}</loc>" in xml, f"orphan page {rel!r}"


def test_single_surface_orgs_get_no_thin_org_page(tmp_path: Path) -> None:
    """An org page duplicating one endpoint page is thin content, not a search surface."""
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "id": "solo",
                        "name": "Solo Health Patient Access API",
                        "kind": "payer",
                        "base_url": "https://solo.test/r4",
                        "verification": {"method": "fixture", "date": "2026-08-05"},
                    }
                ]
            }
        )
    )
    d = tmp_path / "fixtures" / "solo"
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps(good_capability()))
    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--registry",
                str(path),
                "--offline",
                "--fixtures",
                str(tmp_path / "fixtures"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "h.json"),
            ]
        )
        == 0
    )
    assert (out / "endpoint" / "solo" / "index.html").is_file()
    assert not (out / "org").exists()


def test_claim_page_states_what_we_do_to_servers(tmp_path: Path) -> None:
    """The claim flow has to say plainly what probing does, or it is asking for trust blindly."""
    from fhir_scorecard.site import claim_page

    page = claim_page("https://example.test")
    # Normalized, because the source wraps at 100 columns and the promises span line breaks.
    flat = " ".join(page.body.split())
    assert "never authenticate" in flat
    assert "never request patient data" in flat
    # The bound itself is asserted in tests/test_probe_contract.py, derived from MAX_REDIRECTS
    # and the vantage count. Pinning the sentence here is what let the retracted "at most two
    # unauthenticated GET requests" claim survive on the served page after SECURITY.md dropped
    # it: two passing tests, contradicting each other, about the same promise.
    assert "eight per endpoint per probing run" in flat
    assert "at most two unauthenticated GET requests" not in flat
    assert "add-endpoint.yml" in flat
    assert "remove-or-dispute.yml" in flat
    # It must own the mistake that motivated multi-vantage probing.
    assert "intercepted TLS" in flat


def test_internal_links_follow_the_origin_shape(tmp_path: Path) -> None:
    """Root-hosted origins get root links; a path-carrying origin gets that path prepended.

    The prefix used to be hardcoded as /fhir-scorecard/, which was correct for exactly one
    hosting shape: the day the site started serving at the root of fhir.chelseakr.com, every
    internal link on it pointed at a path that exists only on the old project-page host.
    """
    from fhir_scorecard.site import write_page

    card = _card()
    page = endpoint_page(card, "https://a.test/metadata", "2026-08-19", "https://fhir.example.test")

    root_dir = tmp_path / "root"
    write_page(root_dir, page, "https://fhir.example.test", "2026-08-19 00:00 UTC")
    rooted = (root_dir / page.path / "index.html").read_text()
    assert 'href="/how-we-grade/' in rooted
    assert 'src="/badge/acme.svg"' in rooted
    assert "/fhir-scorecard/" not in rooted.replace("github.com/ChelseaKR/fhir-scorecard", "")

    prefixed_dir = tmp_path / "prefixed"
    prefixed_page = endpoint_page(
        card, "https://a.test/metadata", "2026-08-19", "https://host.example/fhir-scorecard"
    )
    write_page(
        prefixed_dir, prefixed_page, "https://host.example/fhir-scorecard", "2026-08-19 00:00 UTC"
    )
    prefixed = (prefixed_dir / prefixed_page.path / "index.html").read_text()
    assert 'href="/fhir-scorecard/how-we-grade/' in prefixed
    assert 'src="/fhir-scorecard/badge/acme.svg"' in prefixed
    # Absolute URLs are never rewritten, in either shape.
    assert 'href="https://github.com/ChelseaKR/fhir-scorecard"' in prefixed
    assert '="/fhir-scorecard/fhir-scorecard/' not in prefixed


def test_the_category_cards_keep_their_grade_colours_and_title_colour() -> None:
    """Two cascade defects that shipped on the live home page, pinned.

    The per-grade ``.grade-count-*`` rules set the pill's colour, and a later
    ``.grade-count`` rule at equal specificity reset it to ink, so every pill
    rendered ink on ink; the letter cell painted ``background: currentcolor``
    over its own ink colour, which is black by construction. And the card title
    set no colour, so USWDS's ``a:visited`` turned it purple after one click.
    """
    from importlib import resources

    css = (resources.files("fhir_scorecard") / "assets" / "site.css").read_text(encoding="utf-8")
    start = css.index(".grade-count {")
    block = css[start : css.index("}", start)]
    assert "color:" not in block.replace("currentcolor", ""), (
        "the shared .grade-count rule must not set colour; it follows the per-grade rules "
        "at equal specificity and would reset every pill to ink"
    )
    assert "background:" not in block, "the shared .grade-count rule must not set background"
    letter = css[css.index(".grade-count span {") :]
    letter = letter[: letter.index("}")]
    assert "background: currentcolor" not in letter, (
        "the letter cell's own colour is ink, so currentcolor paints it black"
    )
    assert ".category-card > a:visited" in css, "the card title must pin its visited colour"


def _png_size(data: bytes) -> tuple[int, int]:
    """Width and height out of a PNG's IHDR, so reading the card needs no image library."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert data[12:16] == b"IHDR", "the first chunk of a PNG is IHDR"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_the_share_card_is_published_and_the_head_addresses_it(tmp_path: Path) -> None:
    """og:image has to name a file this build actually wrote, at an absolute address.

    Until 2026-09 this site emitted og:title, og:description and twitter:card=summary and
    no image at all, so every share of fhir.chelseakr.com rendered as a line of text.
    The failure mode a card invites is the opposite and quieter: a head that names an
    image nothing publishes previews as a blank rectangle, and nothing about the page
    itself looks wrong.
    """
    for eid in ("alpha", "alpha-dir"):
        d = tmp_path / "fixtures" / eid
        d.mkdir(parents=True)
        (d / "metadata.json").write_text(json.dumps(good_capability()))
        (d / "smart.json").write_text(json.dumps(good_smart()))
    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--registry",
                str(_registry(tmp_path)),
                "--offline",
                "--fixtures",
                str(tmp_path / "fixtures"),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "h.json"),
                "--origin",
                "https://example.test",
            ]
        )
        == 0
    )

    published = out / "assets" / "social-card.png"
    assert published.is_file(), "write_assets did not publish the card the head names"
    assert _png_size(published.read_bytes()) == SOCIAL_CARD_SIZE, (
        "the card is not the size the head declares, and every preview crops to what the "
        "head declares"
    )

    for rel in ("index.html", "endpoint/alpha/index.html"):
        page = (out / rel).read_text()
        for tag in ("og:image", "twitter:image"):
            assert 'content="https://example.test/assets/social-card.png"' in page, (
                f"{rel} does not address the built card"
            )
            assert tag in page, f"{rel} declares no {tag}"
        assert '<meta name="twitter:card" content="summary_large_image">' in page, (
            f"{rel} declares a card layout other than the large one, which crops a "
            f"1200x630 image to a square thumbnail"
        )
        alt = re.search(r'<meta property="og:image:alt" content="([^"]*)">', page)
        assert alt is not None and alt.group(1).strip(), (
            f"{rel} has no og:image:alt: in a preview the card carries the page's only "
            f"words, and a reader who cannot see it gets none of them"
        )
        assert f'<meta property="og:image:width" content="{SOCIAL_CARD_SIZE[0]}">' in page
        assert f'<meta property="og:image:height" content="{SOCIAL_CARD_SIZE[1]}">' in page


def test_the_card_address_follows_the_origin_shape() -> None:
    """A hardcoded host is a broken preview the day the hosting shape changes.

    Which is not hypothetical here: internal links carried a hardcoded /fhir-scorecard/
    until the custom domain started serving, and every one of them broke that day.
    """
    assert social_card_url("https://fhir.chelseakr.com") == (
        "https://fhir.chelseakr.com/assets/social-card.png"
    )
    assert social_card_url("https://fhir.chelseakr.com/") == (
        "https://fhir.chelseakr.com/assets/social-card.png"
    )
    assert social_card_url("https://host.example/fhir-scorecard") == (
        "https://host.example/fhir-scorecard/assets/social-card.png"
    )


def test_each_kind_gets_its_own_page_and_no_page_ranks_across_kinds() -> None:
    """Grades are only comparable within a kind, so they are never ranked together.

    `report._summary_table` built one table per kind and carried this invariant under test.
    That renderer's output was written to a path the site immediately overwrote, so it was
    removed; the invariant it guarded lives on the kind pages, and now has a test here.
    """
    from fhir_scorecard.site import _KIND_SLUGS, kind_page

    payer = _card(eid="alpha", kind="payer", name="Alpha Payer")
    vendor = _card(eid="beta", kind="ehr", name="Beta Sandbox")

    page = kind_page("payer", [payer], "https://example.test")
    assert page.path == _KIND_SLUGS["payer"]
    assert "Alpha Payer" in page.body
    assert "Beta Sandbox" not in page.body, "a kind page must not list another kind's endpoint"

    other = kind_page("ehr", [vendor], "https://example.test")
    assert other.path == _KIND_SLUGS["ehr"]
    assert other.path != page.path
    assert "Alpha Payer" not in other.body
