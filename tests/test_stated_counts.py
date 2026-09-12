"""A count stated in committed prose must be a measurement or carry the date it was taken.

The registry grew from 45 endpoints to 81. Seven sentences across four workflow files and one
tool went on saying **45**, in the present tense, describing what this project does to other
organizations' production healthcare servers: "parses documents fetched from 45 third-party
servers", "a daily rescore of 45 live third-party endpoints". Nothing read those numbers, so
nothing could notice. They understated this project's actual footprint on other people's
infrastructure by 45%.

That is the same defect this repository already guards one field over. `SECURITY.md` publishes a
request bound and `tests/test_probe_contract.py` derives it from `DISCOVERY_PATHS` and
`MAX_REDIRECTS` rather than trusting the prose; the docstring there explains that a promise made
to the servers being measured "has to be arithmetic". A count of *how many* servers is the other
half of the same promise, and it was ungated.

**The rule, in one sentence:** inside the files that describe what this project does *now*, any
count of endpoints or servers must either equal the registry size or appear in a sentence that
names the date it was measured.

Two escapes, and both are deliberate.

*Dated measurements pass.* "Measured 2026-08-27 ... 45 endpoints" is true and stays true; this
repository's house style is to date an observation rather than to keep it current, and a gate
that forced every past number to the present would destroy exactly the evidence the style exists
to preserve.

*Scope is narrow.* `ROADMAP.md`, `docs/PR-TRIAGE.md`, `docs/adr/`, `data/CANDIDATES.md` and the
test suite are excluded, because their counts are history, planning arguments, or fixture sizes —
none of which describe the live registry, and all of which would be made *worse* by being forced
to track it. A gate over everything would have to be silenced so often that the silencing would
become the interface.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Files whose prose describes what this project does *now*: the workflows that do the probing,
#: the tool that documents what is reproducible, the promises published to operators, and the
#: modules carrying the probe contract. A count here is read as a current claim.
#:
#: Deliberately not a glob over the repository. See the module docstring: history, planning and
#: fixtures legitimately state other numbers, and forcing them to the registry would replace
#: correct sentences with wrong ones.
SCOPED = (
    ".github/workflows/pages.yml",
    ".github/workflows/live-integrity.yml",
    ".github/workflows/recheck.yml",
    ".github/workflows/verify.yml",
    ".github/workflows/release.yml",
    ".github/workflows/security.yml",
    "tools/verify_live_site.py",
    "README.md",
    "SECURITY.md",
    "docs/ci-action.md",
    "src/fhir_scorecard/fetch.py",
    "src/fhir_scorecard/cli.py",
    "src/fhir_scorecard/vantage.py",
    "src/fhir_scorecard/grading.py",
    "src/fhir_scorecard/site.py",
    "src/fhir_scorecard/entity_report.py",
    "src/fhir_scorecard/weight.py",
    "src/fhir_scorecard/audit.py",
)

#: Numbers written as words, since half of this repository's prose spells them out. A gate that
#: only reads digits is a gate that can be walked past by writing "forty-five".
_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-four": 44,
    "forty-five": 45,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "eighty-one": 81,
    "ninety": 90,
    "a hundred": 100,
}

_COUNT = re.compile(
    r"\b(\d{2,4}|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")\s+"
    r"(?:live\s+|third-party\s+|registry\s+|graded\s+|distinct\s+|public\s+)*"
    r"(endpoints?|servers)\b",
    re.IGNORECASE,
)

#: What makes a number a past observation rather than a present claim. An ISO date, a spelled
#: month-and-year, or an explicit "as of". Checked over the surrounding lines, not the matching
#: line alone, because a comment wraps and the date routinely sits a line above the number.
_DATED = re.compile(
    r"20\d\d-\d\d-\d\d"
    r"|\b\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December) 20\d\d"
    r"|\bas of\b",
    re.IGNORECASE,
)

#: How much context around a match is searched for a date. Three lines back covers a wrapped
#: comment or a wrapped Markdown paragraph without reaching into an unrelated one.
_CONTEXT_LINES = 3


def registry_size() -> int:
    endpoints = json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))
    return sum(1 for e in endpoints["endpoints"] if e.get("enabled", True))


def _claims(path: Path) -> list[tuple[int, str, int, bool]]:
    """Every count of endpoints or servers in one file: line, text, value, whether it is dated."""
    lines = path.read_text(encoding="utf-8").splitlines()
    found = []
    for number, line in enumerate(lines, start=1):
        for match in _COUNT.finditer(line):
            raw = match.group(1).lower()
            value = _WORDS.get(raw) if raw in _WORDS else int(raw)
            if value is None or value < 10:
                continue
            context = " ".join(lines[max(0, number - _CONTEXT_LINES - 1) : number])
            found.append((number, match.group(0).strip(), value, bool(_DATED.search(context))))
    return found


@pytest.mark.parametrize("relative", SCOPED)
def test_a_stated_count_is_current_or_dated(relative: str) -> None:
    """Parametrised per file so a failure names the file, not a wall of every file at once."""
    path = ROOT / relative
    assert path.is_file(), f"{relative} is in the gate's scope but does not exist"
    current = registry_size()
    wrong = [
        f"{relative}:{line} says {text!r} (registry holds {current}, and no date is given)"
        for line, text, value, dated in _claims(path)
        if value != current and not dated
    ]
    assert not wrong, "\n".join(wrong)


def test_every_probing_workflow_is_in_scope() -> None:
    """The scope list is hand-written, so the cheapest way past this gate is to shorten it.

    Deriving the workflow half removes that: a new workflow, or one dropped from ``SCOPED``,
    fails here rather than quietly going unwatched. The rest of the scope stays explicit,
    because "which prose makes a present-tense claim" is a judgment and not a directory listing.
    """
    on_disk = {
        f".github/workflows/{p.name}" for p in (ROOT / ".github" / "workflows").glob("*.yml")
    }
    in_scope = {s for s in SCOPED if s.startswith(".github/workflows/")}
    assert on_disk == in_scope, (
        f"workflows not covered by the stated-count gate: {sorted(on_disk - in_scope)}; "
        f"listed but absent from disk: {sorted(in_scope - on_disk)}"
    )
    for relative in SCOPED:
        assert (ROOT / relative).is_file(), f"{relative} is in scope but does not exist"


def test_the_gate_is_honest_about_how_much_it_examines() -> None:
    """Two numbers, and the smaller one is the point.

    The per-file assertions above pass for two different reasons, and only one of them is
    evidence: a file whose counts are all current or dated has been checked, and a file with no
    count in it has not been checked at all. After the fix that prompted this gate, every
    workflow is in the second group -- the stale numbers were removed rather than updated, which
    is the right repair and also means the gate now proves nothing about those files today. What
    it does is stop a count being *reintroduced* there without being current or dated.

    Stated here so nobody reads a green run as "the workflows were verified". Measured when
    written: 8 counts, in 5 of 17 scoped files.
    """
    per_file = {relative: len(_claims(ROOT / relative)) for relative in SCOPED}
    examinable = len(per_file)
    with_a_count = sum(1 for n in per_file.values() if n)
    total = sum(per_file.values())

    assert examinable == len(SCOPED)
    assert with_a_count < examinable, (
        "every scoped file now states a count; if that is real, delete this assertion, but "
        "check first that the pattern has not started matching something it should not"
    )
    assert total >= 5, (
        f"the pattern found only {total} counts across {examinable} scoped files; it has "
        "probably stopped matching, and every per-file assertion above is vacuous"
    )


def test_the_gate_catches_a_stale_count_and_accepts_a_dated_one(tmp_path: Path) -> None:
    """The gate's own negative control, run every time rather than by hand.

    Without this, the parametrised test above passes on a clean tree whether or not the regex
    works, and nothing distinguishes "no stale counts" from "no counts found".
    """
    current = registry_size()
    stale = tmp_path / "stale.md"
    stale.write_text(f"This job parses documents fetched from {current - 36} third-party servers.")
    assert [c for c in _claims(stale) if c[2] != current and not c[3]], (
        "the gate did not catch a present-tense stale count"
    )

    # Spelled out, because "forty-five" must not be a way past it.
    spelled = tmp_path / "spelled.md"
    spelled.write_text("The site carries cohort pages and forty-five endpoints.")
    assert [c for c in _claims(spelled) if c[2] != current and not c[3]]

    # Dated, so it is a past observation and passes.
    dated = tmp_path / "dated.md"
    dated.write_text("Measured 2026-08-27, the floor put 30 of 45 endpoints in the tables.")
    assert not [c for c in _claims(dated) if c[2] != current and not c[3]]

    # Current, so it passes whether dated or not.
    fresh = tmp_path / "fresh.md"
    fresh.write_text(f"A daily rescore of {current} live third-party endpoints.")
    assert not [c for c in _claims(fresh) if c[2] != current and not c[3]]


def test_the_registry_size_is_read_from_the_registry() -> None:
    """The denominator is derived, never written down here.

    A gate holding its own copy of the number it checks cannot catch the registry moving; it can
    only catch prose disagreeing with a second hand-maintained constant, which is the arrangement
    it exists to remove.
    """
    assert registry_size() == len(
        [
            e
            for e in json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))[
                "endpoints"
            ]
            if e.get("enabled", True)
        ]
    )
    assert registry_size() > 0
