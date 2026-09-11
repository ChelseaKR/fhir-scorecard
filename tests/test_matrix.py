"""The declared-capability matrix has to say what the document declared, all of it, once.

Every test here is written against a property the module docstring states, because each one
is a way a matrix can be wrong while looking complete:

* four states, and the two that have no table say different things;
* every declaration in the document reaches the data, and every row in the data reaches a page;
* the page and the data are one reading of the document, not two;
* the data matches its published schema, and the schema can fail;
* paging never drops a resource and never splits one;
* nothing here moves a grade or the drift fingerprint;
* a file or a link exists only where the build wrote the thing it names.

The counts a test compares against are computed from the raw document with code written here,
not from ``capability.py``, so the extraction cannot agree with itself by construction.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from conftest import good_capability

from fhir_scorecard.capability import NO_CAPABILITY_RETRIEVED, parse_capability, parse_smart
from fhir_scorecard.cli import _declaration_pages, main
from fhir_scorecard.drift import fingerprint
from fhir_scorecard.fetch import FetchResult
from fhir_scorecard.grading import build_scorecard
from fhir_scorecard.matrix import (
    API_DIR,
    DECLARED,
    DECLARES_NOTHING,
    NOT_RETRIEVED,
    ROW_BYTE_CEILING,
    ROW_KINDS,
    SCHEMA_FILE,
    STATES,
    TABLE_BYTE_BUDGET,
    UNREADABLE,
    capabilities_json,
    counts_of,
    pages_for,
    paginate,
    rows_of,
    schema_doc,
    views_of,
)
from fhir_scorecard.site import DEFAULT_ORIGIN
from fhir_scorecard.weight import MAX_PAGE_BYTES

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_IDS = ("cms-blue-button-2", "inferno-reference", "oracle-health-open")
DATE = "2026-09-10"


def _fixture_bytes(endpoint_id: str) -> bytes:
    return (FIXTURES / endpoint_id / "metadata.json").read_bytes()


def _payload(facts: Any, endpoint_id: str = "alpha") -> dict[str, Any]:
    text = capabilities_json(
        facts,
        endpoint_id=endpoint_id,
        name="Alpha Health",
        retrieved_on=DATE,
        generated_at=f"{DATE} 00:00 UTC",
    )
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _server_block(doc: dict[str, Any]) -> dict[str, Any]:
    """Written here, independently of ``capability._server_rest``, on purpose."""
    for rest in doc.get("rest", []):
        if isinstance(rest, dict) and (rest.get("mode") == "server" or "resource" in rest):
            return rest
    return {}


def _independent_counts(raw: bytes) -> dict[str, int]:
    """What the document declares, counted from the raw JSON by code that shares nothing with
    the extraction under test."""
    rest = _server_block(json.loads(raw))
    resources = [r for r in rest.get("resource", []) if isinstance(r, dict) and r.get("type")]
    pairs = {
        (r["type"], i["code"])
        for r in resources
        for i in r.get("interaction", [])
        if isinstance(i, dict) and i.get("code")
    }
    entries = sum(
        1
        for r in resources
        for i in r.get("interaction", [])
        if isinstance(i, dict) and i.get("code")
    )
    params = sum(
        1
        for r in resources
        for p in r.get("searchParam", [])
        if isinstance(p, dict) and p.get("name")
    )
    ops = sum(
        1
        for r in resources
        for o in r.get("operation", [])
        if isinstance(o, dict) and o.get("name")
    ) + sum(1 for o in rest.get("operation", []) if isinstance(o, dict) and o.get("name"))
    server_interactions = {
        i["code"] for i in rest.get("interaction", []) if isinstance(i, dict) and i.get("code")
    }
    return {
        "resources": len({r["type"] for r in resources}),
        "interaction_entries_declared": entries,
        "interaction_rows": len(pairs) + len(server_interactions),
        "search_parameters": params,
        "operations": ops,
    }


class _TableRows(HTMLParser):
    """The body rows of the declaration table: resource label and the four cells after it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_body = False
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tbody":
            self._in_body = True
        elif self._in_body and tag == "tr":
            self.rows.append([])
        elif self._in_body and tag in {"th", "td"}:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "tbody":
            self._in_body = False
        elif self._in_body and tag in {"th", "td"} and self._cell is not None:
            self.rows[-1].append("".join(self._cell))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _table_rows(body: str) -> list[list[str]]:
    parser = _TableRows()
    parser.feed(body)
    return parser.rows


# --- the four states ---


def test_a_document_nobody_retrieved_is_not_described_as_anything() -> None:
    payload = _payload(NO_CAPABILITY_RETRIEVED)
    assert payload["state"] == NOT_RETRIEVED
    assert payload["rows"] is None
    assert payload["counts"] is None
    assert payload["retrieved_on"] is None
    assert payload["document_sha256"] is None
    (page,) = pages_for("alpha", "Alpha Health", NO_CAPABILITY_RETRIEVED, DATE)
    assert "<table" not in page.body
    assert "No CapabilityStatement was retrieved from any vantage on this run" in page.body


def test_an_operation_outcome_renders_the_unreadable_state_with_its_digest() -> None:
    """A document arrived. It is not a CapabilityStatement, and saying so is a finding about the
    document: "not retrieved" would be a claim about the network that nobody made."""
    body = json.dumps({"resourceType": "OperationOutcome", "issue": []}).encode()
    facts = parse_capability(body)
    payload = _payload(facts)
    assert payload["state"] == UNREADABLE
    assert payload["rows"] is None
    assert payload["counts"] is None
    assert payload["document_sha256"] == hashlib.sha256(body).hexdigest()
    assert payload["retrieved_on"] == DATE
    (page,) = pages_for("alpha", "Alpha Health", facts, DATE)
    assert "<table" not in page.body
    assert "could not be read as a CapabilityStatement" in page.body
    assert "resourceType is &#x27;OperationOutcome&#x27;" in page.body


def test_bytes_that_are_not_json_are_unreadable_and_still_hashed() -> None:
    body = b"<html>maintenance</html>"
    payload = _payload(parse_capability(body))
    assert payload["state"] == UNREADABLE
    assert payload["document_sha256"] == hashlib.sha256(body).hexdigest()


def test_a_statement_that_declares_nothing_is_an_empty_list_and_not_a_null() -> None:
    """The other two tableless states publish ``null``. This one publishes ``[]`` and real
    zeros, because here the declaration was read and there is nothing in it."""
    facts = parse_capability(
        json.dumps({"resourceType": "CapabilityStatement", "rest": [{"mode": "server"}]}).encode()
    )
    payload = _payload(facts)
    assert payload["state"] == DECLARES_NOTHING
    assert payload["rows"] == []
    assert payload["counts"] is not None
    assert set(payload["counts"].values()) == {0}
    (page,) = pages_for("alpha", "Alpha Health", facts, DATE)
    assert "<table" not in page.body
    assert "declares no resource, interaction, search parameter or operation" in page.body


def test_the_states_say_four_different_things() -> None:
    documents = {
        NOT_RETRIEVED: NO_CAPABILITY_RETRIEVED,
        UNREADABLE: parse_capability(b"not json"),
        DECLARES_NOTHING: parse_capability(
            json.dumps({"resourceType": "CapabilityStatement"}).encode()
        ),
        DECLARED: parse_capability(json.dumps(good_capability()).encode()),
    }
    sentences = {}
    for state, facts in documents.items():
        payload = _payload(facts)
        assert payload["state"] == state
        sentences[state] = payload["state_detail"]
    assert len(set(sentences.values())) == 4
    assert set(documents) == set(STATES)


def test_unreadable_entries_are_counted_not_dropped() -> None:
    doc = good_capability()
    rest = doc["rest"][0]  # type: ignore[index]
    rest["resource"].append({"interaction": [{"code": "read"}]})  # no type
    rest["resource"][0]["searchParam"] = [{"type": "token"}, {"name": "_id", "type": "token"}]
    facts = parse_capability(json.dumps(doc).encode())
    assert facts.unreadable_declarations == 2
    payload = _payload(facts)
    assert payload["unreadable_declarations"] == 2
    page = pages_for("alpha", "Alpha Health", facts, DATE)[0]
    assert "2 entries in the document could not be read" in page.body


# --- completeness, against counts computed independently ---


@pytest.mark.parametrize("endpoint_id", FIXTURE_IDS)
def test_every_declaration_in_a_captured_statement_reaches_the_data(endpoint_id: str) -> None:
    """The issue's first acceptance line, over three real captured documents: the interaction
    rows equal the ``rest.resource[].interaction[]`` count. They differ only where a document
    repeats a code on one resource, and then both numbers are published."""
    raw = _fixture_bytes(endpoint_id)
    counts = counts_of(parse_capability(raw))
    assert counts is not None
    expected = _independent_counts(raw)
    for key, value in expected.items():
        assert counts[key] == value, (key, counts[key], value)
    assert expected["interaction_entries_declared"] > 0
    assert expected["search_parameters"] > 0


@pytest.mark.parametrize("endpoint_id", FIXTURE_IDS)
def test_every_row_in_the_data_reaches_a_page(endpoint_id: str) -> None:
    facts = parse_capability(_fixture_bytes(endpoint_id))
    rows = rows_of(facts)
    listed: list[list[str]] = []
    for page in pages_for(endpoint_id, endpoint_id, facts, DATE):
        listed += _table_rows(page.body)
    by_resource = {cells[0]: cells for cells in listed}
    resources = {row.resource or "(whole server)" for row in rows}
    assert set(by_resource) == resources
    for row in rows:
        cells = by_resource[row.resource or "(whole server)"]
        column = {"interaction": 1, "search_parameter": 2, "operation": 3}.get(row.kind)
        if column is not None:
            assert row.name in cells[column].split(", "), (row, cells)


def test_the_page_and_the_data_are_one_reading() -> None:
    """``views_of`` groups the rows; it does not read the document again."""
    facts = parse_capability(_fixture_bytes("oracle-health-open"))
    views = views_of(rows_of(facts))
    counts = counts_of(facts)
    assert counts is not None
    assert sum(len(v.interactions) for v in views) == counts["interaction_rows"]
    assert sum(len(v.search_parameters) for v in views) == counts["search_parameters"]
    assert sum(len(v.operations) for v in views) == counts["operations"]
    assert sum(v.profiles for v in views) == counts["profiles"]


def test_rows_run_from_the_whole_server_then_resource_types_a_to_z() -> None:
    facts = parse_capability(_fixture_bytes("oracle-health-open"))
    resources = [row.resource for row in rows_of(facts) if row.kind == "resource"]
    assert resources == sorted(resources)
    first = rows_of(facts)[0]
    assert first.resource is None, "oracle-health-open declares `batch` on the whole server"


# --- the published schema ---

_SCHEMA_WORDS = {
    "$schema",
    "$id",
    "title",
    "description",
    "type",
    "required",
    "properties",
    "enum",
    "items",
}


def _is(value: object, kind: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
    }[kind]


def _validate(value: object, schema: dict[str, Any], where: str = "$") -> list[str]:
    kinds = schema.get("type")
    if kinds is not None:
        allowed = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is(value, kind) for kind in allowed):
            return [f"{where}: {type(value).__name__} is not one of {allowed}"]
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not in {schema['enum']}")
    if isinstance(value, dict):
        errors += [
            f"{where}: missing {key}" for key in schema.get("required", []) if key not in value
        ]
        properties = schema.get("properties", {})
        errors += [
            f"{where}: undocumented key {key}"
            for key in value
            if properties and key not in properties
        ]
        for key, sub in properties.items():
            if key in value:
                errors += _validate(value[key], sub, f"{where}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += _validate(item, schema["items"], f"{where}[{index}]")
    return errors


def _words(schema: object) -> set[str]:
    if isinstance(schema, dict):
        found = set(schema)
        for key, value in schema.items():
            if key == "properties":
                for sub in value.values():
                    found |= _words(sub)
            elif key == "items":
                found |= _words(value)
        return found
    return set()


def _schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(schema_doc(DEFAULT_ORIGIN))
    return loaded


def test_the_schema_uses_only_what_its_reader_implements() -> None:
    """The floor under every validation below. A keyword this reader ignores is a constraint
    the published schema states and no test holds a file to."""
    assert _words(_schema()) <= _SCHEMA_WORDS, _words(_schema()) - _SCHEMA_WORDS


def test_the_reader_can_fail() -> None:
    schema = _schema()
    good = _payload(parse_capability(_fixture_bytes("cms-blue-button-2")))
    assert _validate(good, schema) == []
    broken = dict(good, state="probably fine", extra=1)
    del broken["counts"]
    problems = _validate(broken, schema)
    assert any("missing counts" in p for p in problems)
    assert any("not in" in p for p in problems)
    assert any("undocumented key extra" in p for p in problems)
    assert _validate(dict(good, rows=[{"kind": "interaction"}]), schema)


@pytest.mark.parametrize(
    "facts",
    [
        NO_CAPABILITY_RETRIEVED,
        parse_capability(b"{}"),
        parse_capability(json.dumps({"resourceType": "OperationOutcome"}).encode()),
        parse_capability(json.dumps({"resourceType": "CapabilityStatement"}).encode()),
        *(parse_capability(_fixture_bytes(e)) for e in FIXTURE_IDS),
    ],
)
def test_every_state_validates_against_the_published_schema(facts: Any) -> None:
    assert _validate(_payload(facts), _schema()) == []


def test_the_schema_names_every_state_and_every_row_kind() -> None:
    properties = _schema()["properties"]
    assert properties["state"]["enum"] == list(STATES)
    assert properties["rows"]["items"]["properties"]["kind"]["enum"] == list(ROW_KINDS)
    assert properties["counts"]["required"] == list(
        counts_of(parse_capability(_fixture_bytes("cms-blue-button-2"))) or {}
    )


def test_the_data_is_deterministic() -> None:
    facts = parse_capability(_fixture_bytes("inferno-reference"))
    assert capabilities_json(
        facts, endpoint_id="x", name="X", retrieved_on=DATE, generated_at="g"
    ) == capabilities_json(facts, endpoint_id="x", name="X", retrieved_on=DATE, generated_at="g")


# --- paging ---


def _giant(resources: int, params: int, name_width: int = 18) -> dict[str, Any]:
    return {
        "resourceType": "CapabilityStatement",
        "fhirVersion": "4.0.1",
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": f"Resource{index:04d}",
                        "interaction": [{"code": "read"}, {"code": "search-type"}],
                        "searchParam": [
                            {
                                "name": f"p{index:04d}-{p:03d}".ljust(name_width, "x"),
                                "type": "token",
                            }
                            for p in range(params)
                        ],
                    }
                    for index in range(resources)
                ],
            }
        ],
    }


def test_a_large_declaration_is_paged_by_resource_and_nothing_is_dropped() -> None:
    """Examined equals examinable: every resource appears on exactly one page, in order."""
    facts = parse_capability(json.dumps(_giant(300, 40)).encode())
    pages = pages_for("giant", "Giant", facts, DATE)
    assert len(pages) > 1
    listed = [cells[0] for page in pages for cells in _table_rows(page.body)]
    assert listed == [f"Resource{index:04d}" for index in range(300)]
    for number, page in enumerate(pages, start=1):
        assert f"Page {number} of {len(pages)}" in page.body
        assert len(page.body.encode()) < MAX_PAGE_BYTES
    assert len({page.title for page in pages}) == len(pages)
    assert len({page.path for page in pages}) == len(pages)


def test_no_page_carries_more_rows_than_its_budget() -> None:
    facts = parse_capability(json.dumps(_giant(300, 40)).encode())
    for placed in paginate(views_of(rows_of(facts))):
        assert sum(len(p.html.encode()) for p in placed) <= TABLE_BYTE_BUDGET


def test_one_resource_too_large_for_a_page_is_counted_and_every_name_is_kept() -> None:
    """No third party can declare its way past the weight gate: the row is written with counts,
    the page says so, and every one of the names is still in the data."""
    facts = parse_capability(json.dumps(_giant(1, 5000)).encode())
    (page,) = pages_for("giant", "Giant", facts, DATE)
    (cells,) = _table_rows(page.body)
    assert cells[2] == "5000 search parameters"
    assert "written with counts rather than names" in page.body
    assert len(page.body.encode()) < MAX_PAGE_BYTES
    payload = _payload(facts)
    assert sum(1 for row in payload["rows"] if row["kind"] == "search_parameter") == 5000


def test_a_resource_type_name_nobody_could_print_is_described_by_its_length() -> None:
    doc = _giant(1, 5000)
    doc["rest"][0]["resource"][0]["type"] = "Z" * 100_000
    facts = parse_capability(json.dumps(doc).encode())
    (page,) = pages_for("giant", "Giant", facts, DATE)
    assert "a resource type name of 100000 characters" in page.body
    assert len(page.body.encode()) < MAX_PAGE_BYTES
    assert ROW_BYTE_CEILING < TABLE_BYTE_BUDGET < MAX_PAGE_BYTES


def test_a_payers_markup_is_escaped_on_the_page() -> None:
    doc = good_capability()
    doc["rest"][0]["resource"][0]["searchParam"] = [  # type: ignore[index]
        {"name": "<script>alert(1)</script>", "type": "token"}
    ]
    page = pages_for("alpha", "Alpha", parse_capability(json.dumps(doc).encode()), DATE)[0]
    assert "<script>alert(1)</script>" not in page.body
    assert "&lt;script&gt;" in page.body


# --- nothing here grades ---


def _strip_declarations(raw: bytes) -> bytes:
    doc = json.loads(raw)
    for rest in doc.get("rest", []):
        rest.pop("operation", None)
        rest.pop("interaction", None)
        for resource in rest.get("resource", []):
            resource.pop("searchParam", None)
            resource.pop("operation", None)
    return json.dumps(doc).encode()


@pytest.mark.parametrize("endpoint_id", FIXTURE_IDS)
def test_no_score_and_no_fingerprint_moves(endpoint_id: str) -> None:
    """Removing everything the matrix added to the extraction changes no dimension score and
    no field of the drift fingerprint, so neither a published grade nor the stored history
    can have moved because of it."""
    metadata = FetchResult(
        url="https://x.test/metadata", ok=True, status=200, elapsed_ms=10, body=b"", error=None
    )
    smart = parse_smart(b"{}")
    full = parse_capability(_fixture_bytes(endpoint_id))
    stripped = parse_capability(_strip_declarations(_fixture_bytes(endpoint_id)))
    assert full.search_parameters and not stripped.search_parameters
    one = build_scorecard(endpoint_id, endpoint_id, metadata, full, smart, kind="payer")
    two = build_scorecard(endpoint_id, endpoint_id, metadata, stripped, smart, kind="payer")
    assert [d.score for d in one.dimensions] == [d.score for d in two.dimensions]
    assert one.grade == two.grade
    assert fingerprint(full) == fingerprint(stripped)


# --- the wiring: a page or a file exists only where the build wrote it ---


def _build(out: Path, fixtures: Path = FIXTURES) -> Path:
    assert (
        main(
            [
                "grade",
                "--offline",
                "--fixtures",
                str(fixtures),
                "--registry",
                str(fixtures / "registry.json"),
                "--out",
                str(out),
                "--history",
                str(out.parent / f"{out.name}-history.json"),
            ]
        )
        == 0
    )
    return out


@pytest.fixture
def built(tmp_path: Path) -> Path:
    return _build(tmp_path / "site")


def test_the_build_writes_a_declaration_for_every_endpoint(built: Path) -> None:
    registry = json.loads((FIXTURES / "registry.json").read_text(encoding="utf-8"))
    ids = sorted(entry["id"] for entry in registry["endpoints"])
    written = sorted(path.stem for path in (built / API_DIR).glob("*.json"))
    assert written == ids
    assert (built / SCHEMA_FILE).is_file()
    for endpoint_id in ids:
        assert (built / "endpoint" / endpoint_id / "capabilities" / "index.html").is_file()
        page = (built / "endpoint" / endpoint_id / "index.html").read_text(encoding="utf-8")
        assert f'href="/endpoint/{endpoint_id}/capabilities/"' in page


def test_the_published_files_validate_against_the_published_schema(built: Path) -> None:
    schema = json.loads((built / SCHEMA_FILE).read_text(encoding="utf-8"))
    files = sorted((built / API_DIR).glob("*.json"))
    assert files
    for path in files:
        assert _validate(json.loads(path.read_text(encoding="utf-8")), schema) == [], path


def test_the_index_names_every_declaration_and_only_those(built: Path) -> None:
    index = json.loads((built / "api" / "index.json").read_text(encoding="utf-8"))
    assert index["capabilities_schema"] == f"{DEFAULT_ORIGIN}/{SCHEMA_FILE}"
    for entry in index["endpoints"]:
        relative = entry["capabilities"].removeprefix(f"{DEFAULT_ORIGIN}/")
        assert (built / relative).is_file(), entry["capabilities"]


def test_the_index_names_no_declaration_when_the_build_wrote_none(tmp_path: Path) -> None:
    """The other side of the guard, which no build in this suite reaches on its own: every
    build writes a declaration for every endpoint, so the condition and ``if True`` would
    otherwise agree on all of them."""
    from fhir_scorecard.dataset import write_dataset
    from fhir_scorecard.registry import Endpoint

    card = build_scorecard(
        "alpha",
        "Alpha",
        FetchResult(
            url="https://a.test/metadata", ok=True, status=200, elapsed_ms=1, body=b"", error=None
        ),
        parse_capability(b"{}"),
        parse_smart(b"{}"),
        kind="payer",
    )
    endpoint = Endpoint(
        endpoint_id="alpha",
        name="Alpha",
        kind="payer",
        base_url="https://a.test/r4",
        verified_method="fixture",
        verified_date=DATE,
        expects="r4",
    )
    for declarations, present in (((), False), (("alpha",), True)):
        write_dataset(
            tmp_path,
            [card],
            [endpoint],
            origin=DEFAULT_ORIGIN,
            generated_at="g",
            vantage="v",
            declarations=declarations,
        )
        index = json.loads((tmp_path / "api" / "index.json").read_text(encoding="utf-8"))
        assert ("capabilities_schema" in index) is present
        assert ("capabilities" in index["endpoints"][0]) is present


def test_an_endpoint_page_links_a_declaration_only_when_told_one_exists() -> None:
    from fhir_scorecard.site import endpoint_page

    card = build_scorecard(
        "alpha",
        "Alpha",
        FetchResult(
            url="https://a.test/metadata", ok=True, status=200, elapsed_ms=1, body=b"", error=None
        ),
        parse_capability(json.dumps(good_capability()).encode()),
        parse_smart(b"{}"),
        kind="payer",
    )
    kwargs = {"base_url": "https://a.test/r4", "verified": "v", "origin": DEFAULT_ORIGIN}
    assert "/capabilities/" not in endpoint_page(card, **kwargs).body  # type: ignore[arg-type]
    assert '/endpoint/alpha/capabilities/"' in endpoint_page(card, declared=True, **kwargs).body  # type: ignore[arg-type]


def test_a_card_whose_declaration_was_not_kept_stops_the_build() -> None:
    """Publishing "nothing was retrieved" for an endpoint whose facts were merely lost on the
    way to the page would state an absence nobody observed."""
    card = build_scorecard(
        "alpha",
        "Alpha",
        FetchResult(
            url="https://a.test/metadata", ok=True, status=200, elapsed_ms=1, body=b"", error=None
        ),
        parse_capability(b"{}"),
        parse_smart(b"{}"),
        kind="payer",
    )
    with pytest.raises(ValueError, match="no declaration was recorded for 'alpha'"):
        _declaration_pages([card], {}, DATE)


def test_a_large_declaration_passes_every_site_gate(tmp_path: Path) -> None:
    """Built through the real command with one fixture replaced by a declaration four pages
    long, and held to the whole site contract, the accessibility rules and the weight budget."""
    from fhir_scorecard.accessibility import audit_accessibility
    from fhir_scorecard.audit import audit_site
    from fhir_scorecard.weight import audit_weight

    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    (fixtures / "inferno-reference" / "metadata.json").write_text(
        json.dumps(_giant(300, 40)), encoding="utf-8"
    )
    site = _build(tmp_path / "site", fixtures)
    pages = sorted((site / "endpoint" / "inferno-reference" / "capabilities").rglob("index.html"))
    assert len(pages) > 1
    assert audit_site(site, DEFAULT_ORIGIN) == []
    assert audit_accessibility(site) == []
    assert audit_weight(site) == []
    sitemap = (site / "sitemap.xml").read_text(encoding="utf-8")
    for page in pages:
        directory = page.parent.relative_to(site).as_posix()
        assert f"<loc>{DEFAULT_ORIGIN}/{directory}/</loc>" in sitemap


def test_a_dated_snapshot_does_not_silently_carry_the_declarations(
    built: Path, tmp_path: Path
) -> None:
    """What a signed dated dataset carries is the maintainer's call. If somebody moves the
    declarations into a tree the snapshot copies, this says so rather than letting twelve
    megabytes arrive in the next release unannounced."""
    from fhir_scorecard.snapshot import build as build_snapshot

    manifest = build_snapshot(built, tmp_path / "snap", DATE)
    assert any(name.startswith("api/endpoint/") for name in manifest.files)
    assert not any(name.startswith(f"{API_DIR}/") for name in manifest.files)
    assert not any("capabilities" in name for name in manifest.files)


def test_the_mcp_tool_serves_the_published_declaration(built: Path) -> None:
    from fhir_scorecard.mcp import call_tool

    result = call_tool(built, "declared_capabilities", {"endpoint_id": "inferno-reference"})
    payload = json.loads(result["content"][0]["text"])
    assert payload == json.loads((built / API_DIR / "inferno-reference.json").read_text())
    for hostile in ("../api/index", "a/b", "..", ""):
        refused = json.loads(
            call_tool(built, "declared_capabilities", {"endpoint_id": hostile})["content"][0][
                "text"
            ]
        )
        assert refused == {"error": "endpoint_id must be a bare identifier"}
    missing = json.loads(
        call_tool(built, "declared_capabilities", {"endpoint_id": "nobody"})["content"][0]["text"]
    )
    assert missing == {"error": "no declaration is published for 'nobody'"}
