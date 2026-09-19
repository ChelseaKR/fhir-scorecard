"""Tests for audit.py's noindex exemption (added for /bundle/setup/): a page carrying
<meta name="robots" content="noindex..."> is exempt from PAGE_MISSING_FROM_SITEMAP and
ORPHAN_PAGE, and a page without that tag is held to both rules exactly as before."""

from __future__ import annotations

from pathlib import Path

from fhir_scorecard.audit import audit_site


def _write_page(root: Path, path: str, *, noindex: bool, links: str = "") -> None:
    target = root / path
    target.mkdir(parents=True, exist_ok=True)
    robots_meta = '<meta name="robots" content="noindex,follow">' if noindex else ""
    (target / "index.html").write_text(
        f"""<!doctype html><html lang="en"><head>
<title>{path}</title><meta name="description" content="d">
{robots_meta}
<link rel="canonical" href="https://example.test/{path}/">
</head><body>{links}</body></html>"""
    )


def _write_sitemap(root: Path, urls: list[str]) -> None:
    entries = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    (root / "sitemap.xml").write_text(
        f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'
    )


def _write_robots(root: Path) -> None:
    (root / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nSitemap: https://example.test/sitemap.xml\n"
    )


def test_noindex_page_is_exempt_from_orphan_and_sitemap_checks(tmp_path: Path) -> None:
    _write_page(tmp_path, "", noindex=False, links='<a href="/a/">a</a>')
    _write_page(tmp_path, "a", noindex=False)
    _write_page(tmp_path, "hidden", noindex=True)  # unlinked, noindex -- like /bundle/setup/
    _write_sitemap(tmp_path, ["https://example.test/", "https://example.test/a/"])
    _write_robots(tmp_path)

    findings = audit_site(tmp_path, "https://example.test")
    codes_for_hidden = {f.code for f in findings if f.where == "hidden/index.html"}
    assert "ORPHAN_PAGE" not in codes_for_hidden
    assert "PAGE_MISSING_FROM_SITEMAP" not in codes_for_hidden


def test_a_normal_page_still_fails_both_checks_when_orphaned_and_unlisted(tmp_path: Path) -> None:
    """Negative control: the exemption is specific to the noindex tag. A page missing the tag
    must still be caught exactly as before this change."""
    _write_page(tmp_path, "", noindex=False, links="")
    _write_page(tmp_path, "orphan", noindex=False)  # no robots meta, unlinked, not in sitemap
    _write_sitemap(tmp_path, ["https://example.test/"])
    _write_robots(tmp_path)

    findings = audit_site(tmp_path, "https://example.test")
    codes_for_orphan = {f.code for f in findings if f.where == "orphan/index.html"}
    assert "ORPHAN_PAGE" in codes_for_orphan
    assert "PAGE_MISSING_FROM_SITEMAP" in codes_for_orphan
