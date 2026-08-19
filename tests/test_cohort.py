from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from conftest import good_capability, good_smart

from fhir_scorecard.capability import parse_capability, parse_smart
from fhir_scorecard.cli import main
from fhir_scorecard.cohort import load_cohort, load_cohort_dir
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.site import cohort_page, home_page

_REGISTRY_IDS = frozenset({"alpha", "alpha-dir"})


def _cohort_payload() -> dict[str, Any]:
    return {
        "cohort": {
            "id": "california",
            "name": "California payer cohort",
            "description": "Patient Access endpoints of California payer programs.",
        },
        "notes": ["These endpoints are required by the CMS Patient Access rule (CMS-9115-F)."],
        "sources": [
            {
                "label": "DHCS plan directory",
                "url": "https://example.test/dhcs",
                "date": "2026-08-07",
            },
        ],
        "members": [
            {
                "id": "alpha-health",
                "name": "Alpha Health",
                "programs": ["medi-cal", "covered-ca"],
                "endpoints": ["alpha", "alpha-dir"],
            },
            {
                "id": "gated-plan",
                "name": "Gated Plan",
                "programs": ["medi-cal"],
                "excluded": {
                    "reason": "developer portal requires registration to view the base URL",
                    "basis": "portal_reviewed",
                    "reviewed": {
                        "method": "retrieved the plan's interoperability page",
                        "date": "2026-08-07",
                        "source": "https://example.test/gated",
                    },
                },
            },
        ],
    }


def _write(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "california.json"
    path.write_text(json.dumps(payload))
    return path


def _card(eid: str, name: str, kind: str = "payer"):
    return build_scorecard(
        eid,
        name,
        FetchResult(
            url=f"https://{eid}.test/metadata",
            ok=True,
            status=200,
            elapsed_ms=10,
            body=b"",
            error=None,
        ),
        parse_capability(json.dumps(good_capability()).encode()),
        parse_smart(json.dumps(good_smart()).encode()),
        kind=kind,
    )


def test_valid_cohort_loads_with_both_populations(tmp_path: Path) -> None:
    cohort = load_cohort(_write(tmp_path, _cohort_payload()), _REGISTRY_IDS)
    assert cohort.cohort_id == "california"
    assert [m.member_id for m in cohort.included] == ["alpha-health"]
    assert [m.member_id for m in cohort.excluded] == ["gated-plan"]
    assert cohort.included[0].endpoint_ids == ("alpha", "alpha-dir")
    exclusion = cohort.excluded[0].exclusion
    assert exclusion is not None and exclusion.basis == "portal_reviewed"
    assert cohort.sources[0].label == "DHCS plan directory"


def test_duplicate_member_ids_refused(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"].append(dict(payload["members"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_unknown_endpoint_reference_refused(tmp_path: Path) -> None:
    """A cohort can never claim an endpoint the registry does not stand behind."""
    payload = _cohort_payload()
    payload["members"][0]["endpoints"] = ["ghost"]
    with pytest.raises(ValueError, match="not a graded registry endpoint"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_member_with_endpoints_and_exclusion_refused(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"][0]["excluded"] = payload["members"][1]["excluded"]
    with pytest.raises(ValueError, match="cannot be both"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_member_with_neither_refused(tmp_path: Path) -> None:
    """A member with no endpoints and no exclusion is a claim with no evidence either way."""
    payload = _cohort_payload()
    del payload["members"][1]["excluded"]
    with pytest.raises(ValueError, match="neither endpoints nor an exclusion"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_unknown_program_refused(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"][0]["programs"] = ["medicare-advantage"]
    with pytest.raises(ValueError, match="programs entries"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_empty_programs_refused(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"][0]["programs"] = []
    with pytest.raises(ValueError, match="non-empty"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_exclusion_without_review_record_refused(tmp_path: Path) -> None:
    """An exclusion without a review record is an assertion, not a finding."""
    payload = _cohort_payload()
    del payload["members"][1]["excluded"]["reviewed"]
    with pytest.raises(ValueError, match="no review record"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_exclusion_with_unknown_basis_refused(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"][1]["excluded"]["basis"] = "vibes"
    with pytest.raises(ValueError, match="basis must be one of"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_exclusion_with_bad_date_refused(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"][1]["excluded"]["reviewed"]["date"] = "August 2026"
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_cohort_without_members_refused(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"] = []
    with pytest.raises(ValueError, match="non-empty list"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_cohort_head_and_slug_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'cohort' object"):
        load_cohort(_write(tmp_path, {"members": []}), _REGISTRY_IDS)
    payload = _cohort_payload()
    payload["cohort"]["id"] = "Not A Slug"
    with pytest.raises(ValueError, match="lowercase slug"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_source_dates_validated(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["sources"][0]["date"] = "yesterday"
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_malformed_shapes_are_refused(tmp_path: Path) -> None:
    """Every structural mistake fails the load rather than silently dropping a member."""
    cases: list[tuple[dict[str, Any], str]] = []

    missing_name = _cohort_payload()
    del missing_name["members"][0]["name"]
    cases.append((missing_name, "name missing or empty"))

    bad_exclusion = _cohort_payload()
    bad_exclusion["members"][1]["excluded"] = "gated"
    cases.append((bad_exclusion, "excluded must be an object"))

    bad_endpoints = _cohort_payload()
    bad_endpoints["members"][0]["endpoints"] = "alpha"
    cases.append((bad_endpoints, "endpoints must be a list"))

    bad_member_id = _cohort_payload()
    bad_member_id["members"][0]["id"] = "Not_A_Slug"
    cases.append((bad_member_id, "lowercase slug"))

    bad_sources = _cohort_payload()
    bad_sources["sources"] = {"label": "x"}
    cases.append((bad_sources, "sources must be a list"))

    bad_source_item = _cohort_payload()
    bad_source_item["sources"] = ["https://example.test"]
    cases.append((bad_source_item, "is not an object"))

    bad_notes = _cohort_payload()
    bad_notes["notes"] = "one note"
    cases.append((bad_notes, "notes must be a list of strings"))

    bad_member = _cohort_payload()
    bad_member["members"][0] = "Alpha Health"
    cases.append((bad_member, "is not an object"))

    for payload, message in cases:
        with pytest.raises(ValueError, match=message):
            load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)


def test_repeated_program_is_recorded_once(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"][0]["programs"] = ["medi-cal", "medi-cal", "covered-ca"]
    cohort = load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)
    assert cohort.members[0].programs == ("medi-cal", "covered-ca")


def test_sources_are_optional(tmp_path: Path) -> None:
    payload = _cohort_payload()
    del payload["sources"]
    cohort = load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)
    assert cohort.sources == ()
    page = cohort_page(cohort, {}, "https://example.test")
    assert "Membership sources" not in page.body


def test_cohort_dir_absent_means_no_cohorts(tmp_path: Path) -> None:
    assert load_cohort_dir(tmp_path / "missing", _REGISTRY_IDS) == ()


def test_cohort_dir_loads_files_sorted(tmp_path: Path) -> None:
    first = _cohort_payload()
    first["cohort"]["id"] = "aaa"
    (tmp_path / "b-california.json").write_text(json.dumps(_cohort_payload()))
    (tmp_path / "a-first.json").write_text(json.dumps(first))
    cohorts = load_cohort_dir(tmp_path, _REGISTRY_IDS)
    assert [c.cohort_id for c in cohorts] == ["aaa", "california"]


def test_cohort_page_lists_grades_and_exclusions(tmp_path: Path) -> None:
    cohort = load_cohort(_write(tmp_path, _cohort_payload()), _REGISTRY_IDS)
    cards = {
        "alpha": _card("alpha", "Alpha Health Patient Access API"),
        "alpha-dir": _card(
            "alpha-dir", "Alpha Health Provider Directory API", kind="payer_provider_directory"
        ),
    }
    page = cohort_page(cohort, cards, "https://example.test")
    assert page.path == "california"
    flat = " ".join(page.body.split())
    assert "1 of 2 member organizations publish" in flat
    assert "Medi-Cal managed care, Covered California" in flat
    assert 'href="/fhir-scorecard/endpoint/alpha/"' in page.body
    assert "Gated Plan" in page.body
    assert "requires registration" in page.body
    assert 'href="https://example.test/gated"' in page.body
    # The exclusion review is dated and sourced, so a reader can check it rather than trust it.
    assert "2026-08-07" in page.body
    assert "CMS-9115-F" in page.body  # the methodology note from the data file is rendered


def test_cohort_page_with_nothing_listable_says_so(tmp_path: Path) -> None:
    """An empty included table would read as a claim; the page states the gap instead."""
    payload = _cohort_payload()
    payload["members"] = [payload["members"][1]]
    cohort = load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)
    page = cohort_page(cohort, {}, "https://example.test")
    flat = " ".join(page.body.split())
    assert "0 of 1 member organizations publish" in flat
    assert "That gap is the finding" in flat


def test_cohort_page_escapes_member_names(tmp_path: Path) -> None:
    payload = _cohort_payload()
    payload["members"][1]["name"] = "<script>x</script>"
    cohort = load_cohort(_write(tmp_path, payload), _REGISTRY_IDS)
    page = cohort_page(
        cohort,
        {"alpha": _card("alpha", "Alpha"), "alpha-dir": _card("alpha-dir", "Alpha Dir")},
        "https://example.test",
    )
    assert "<script>x</script>" not in page.body
    assert "&lt;script&gt;" in page.body


def test_home_page_links_cohorts_only_when_present(tmp_path: Path) -> None:
    cohort = load_cohort(_write(tmp_path, _cohort_payload()), _REGISTRY_IDS)
    cards = [_card("alpha", "Alpha")]
    with_cohort = home_page(cards, "https://example.test", (cohort,))
    assert 'href="/fhir-scorecard/california/"' in with_cohort.body
    assert "Curated cohorts" in with_cohort.body
    without = home_page(cards, "https://example.test")
    assert "Curated cohorts" not in without.body


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
                        "verification": {"method": "fixture", "date": "2026-08-07"},
                    },
                    {
                        "id": "alpha-dir",
                        "name": "Alpha Health Provider Directory API",
                        "kind": "payer_provider_directory",
                        "base_url": "https://alpha.test/pd",
                        "verification": {"method": "fixture", "date": "2026-08-07"},
                    },
                ]
            }
        )
    )
    return path


def _fixtures(tmp_path: Path) -> Path:
    for eid in ("alpha", "alpha-dir"):
        d = tmp_path / "fixtures" / eid
        d.mkdir(parents=True)
        (d / "metadata.json").write_text(json.dumps(good_capability()))
        (d / "smart.json").write_text(json.dumps(good_smart()))
    return tmp_path / "fixtures"


def test_cli_builds_cohort_page_into_site_and_sitemap(tmp_path: Path) -> None:
    cohorts_dir = tmp_path / "cohorts"
    cohorts_dir.mkdir()
    (cohorts_dir / "california.json").write_text(json.dumps(_cohort_payload()))
    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--registry",
                str(_registry(tmp_path)),
                "--offline",
                "--fixtures",
                str(_fixtures(tmp_path)),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "h.json"),
                "--cohorts",
                str(cohorts_dir),
                "--origin",
                "https://example.test",
            ]
        )
        == 0
    )
    page = (out / "california" / "index.html").read_text()
    assert '<link rel="canonical" href="https://example.test/california/"' in page
    assert "Alpha Health Patient Access API" in page
    assert "Gated Plan" in page
    assert "<loc>https://example.test/california/</loc>" in (out / "sitemap.xml").read_text()
    home = (out / "index.html").read_text()
    assert 'href="/fhir-scorecard/california/"' in home


def test_cli_without_cohort_dir_builds_no_cohort_page(tmp_path: Path) -> None:
    out = tmp_path / "site"
    assert (
        main(
            [
                "grade",
                "--registry",
                str(_registry(tmp_path)),
                "--offline",
                "--fixtures",
                str(_fixtures(tmp_path)),
                "--out",
                str(out),
                "--history",
                str(tmp_path / "h.json"),
                "--cohorts",
                str(tmp_path / "no-such-dir"),
            ]
        )
        == 0
    )
    assert not (out / "california").exists()
    assert "Curated cohorts" not in (out / "index.html").read_text()


def test_shipped_cohorts_load_against_the_shipped_registry() -> None:
    """The curation files this project publishes must satisfy their own rules.

    Addressed absolutely because the suite runs from a throwaway directory. Without this, a cohort
    referencing an endpoint that had been removed from the registry would break the nightly build
    rather than a pull request, and the site would stop publishing over a data edit.
    """
    from fhir_scorecard.registry import load_registry

    repo = Path(__file__).resolve().parent.parent
    endpoints = load_registry(repo / "data" / "registry.json")
    cohorts = load_cohort_dir(
        repo / "data" / "cohorts", frozenset(e.endpoint_id for e in endpoints)
    )
    assert cohorts, "data/cohorts should ship at least the California cohort"
    for cohort in cohorts:
        # Every member is in exactly one population, and an exclusion always carries its evidence.
        assert len(cohort.included) + len(cohort.excluded) == len(cohort.members)
        assert cohort.sources, f"{cohort.cohort_id} must cite where its membership list came from"
        for member in cohort.excluded:
            assert member.exclusion is not None
            assert member.exclusion.source.startswith("https://")


def test_cli_fails_loudly_on_invalid_cohort_file(tmp_path: Path) -> None:
    """A cohort that half-loads would publish a membership list with members missing."""
    cohorts_dir = tmp_path / "cohorts"
    cohorts_dir.mkdir()
    payload = _cohort_payload()
    payload["members"][0]["endpoints"] = ["not-in-registry"]
    (cohorts_dir / "california.json").write_text(json.dumps(payload))
    assert (
        main(
            [
                "grade",
                "--registry",
                str(_registry(tmp_path)),
                "--offline",
                "--fixtures",
                str(_fixtures(tmp_path)),
                "--out",
                str(tmp_path / "site"),
                "--history",
                str(tmp_path / "h.json"),
                "--cohorts",
                str(cohorts_dir),
            ]
        )
        == 2
    )


def test_the_texas_frame_still_says_where_its_denominator_came_from() -> None:
    """The denominator is the claim. It has to keep naming the file it was taken from.

    15 is not a count of what was found; it is CMS's own count of the issuers selling an
    individual-market QHP on HealthCare.gov in Texas for 2026, fixed before any URL was probed.
    A member added or dropped without re-deriving it from that file would quietly turn a rate
    into an anecdote, which is the exact failure `docs/SAMPLING-FRAME.md` exists to prevent.
    """
    from fhir_scorecard.registry import load_registry

    repo = Path(__file__).resolve().parent.parent
    endpoints = load_registry(repo / "data" / "registry.json")
    cohort = load_cohort(
        repo / "data" / "cohorts" / "texas-marketplace.json",
        frozenset(e.endpoint_id for e in endpoints),
    )
    assert len(cohort.members) == 15
    assert all(member.programs == ("tx-marketplace",) for member in cohort.members)

    labels = " ".join(s.label for s in cohort.sources)
    assert "QHP Landscape PY2026 Individual Medical" in labels
    # Host compared exactly, not by substring: "data.healthcare.gov" appearing anywhere in a URL
    # is satisfied by a hostname that merely ends with it, which is not the same claim.
    assert any(urlsplit(s.url).netloc == "data.healthcare.gov" for s in cohort.sources)
    assert all(s.date for s in cohort.sources)

    notes = " ".join(cohort.notes)
    # The two numbers that would otherwise be confused: issuer organizations and HIOS IDs.
    assert "15" in notes and "18 HIOS issuer IDs" in notes
    assert "45 CFR 156.221" in notes, "the obligation this cohort claims must stay named"


def test_a_cohort_member_may_point_at_an_endpoint_that_did_not_answer() -> None:
    """An unreachable endpoint is a finding, not a reason to drop the member from the roster."""
    from fhir_scorecard.registry import load_registry

    repo = Path(__file__).resolve().parent.parent
    endpoints = {e.endpoint_id: e for e in load_registry(repo / "data" / "registry.json")}
    cohort = load_cohort(repo / "data" / "cohorts" / "texas-marketplace.json", frozenset(endpoints))
    listed = [endpoints[eid] for m in cohort.included for eid in m.endpoint_ids]
    documented_only = [e for e in listed if e.verification_basis == "publisher_documented"]
    assert documented_only, (
        "this cohort is supposed to carry endpoints its issuers publish and that do not answer; "
        "if none is left, check whether one was quietly dropped rather than fixed"
    )
    for endpoint in documented_only:
        assert endpoint.verification_source.startswith("https://")
        assert endpoint.verification_observed
        assert endpoint.enabled, "a documented-unreachable entry is graded like any other"
