from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import good_capability, good_smart

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.cli import main
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.site import endpoint_page, org_slug, robots, sitemap, status_badge


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
    assert 'href="#content">Skip to content</a>' in home
    assert "prefers-reduced-motion" in home

    ep = (out / "endpoint" / "alpha" / "index.html").read_text()
    assert '<meta name="description"' in ep
    assert "https://alpha.test/r4" in ep
    assert "answered" in ep  # availability surfaced
    assert "/fhir-scorecard/badge/alpha.svg" in ep
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
    assert "two unauthenticated GET requests" in flat
    assert "add-endpoint.yml" in flat
    assert "remove-or-dispute.yml" in flat
    # It must own the mistake that motivated multi-vantage probing.
    assert "intercepted TLS" in flat
