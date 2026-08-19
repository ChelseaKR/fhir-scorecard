# `make verify` is the gate of record: .github/workflows/verify.yml runs this exact target
# against the locked environment, so a local pass and a CI pass mean the same thing. Run it
# before opening a PR.
PYTHON ?= .venv/bin/python

.PHONY: lock-check sync verify lint format typecheck test audit site

# Does uv.lock still describe what pyproject.toml asks for? Nothing is installed, nothing is
# written, and no network is reached, so this is safe to make the first gate.
#
# It is the first gate on purpose: a later target that resolved dependencies would repair the
# lockfile it was meant to be checked against, and then pass. Nothing here invokes a bare
# `uv run`, which performs exactly that implicit repair.
#
# `--check`, not `--frozen`, and the difference is the whole control. `uv sync --frozen`
# installs the lockfile as it stands without ever comparing it to pyproject.toml. Measured on
# uv 0.12.1 against a deliberately drifted manifest: `uv lock --check --offline` exits 1,
# `uv sync --locked` exits 1, and `uv sync --frozen` exits 0 having installed the stale set.
# The portfolio control text (CQ-09) names `--frozen` and calls it a lockfile-drift check; by
# construction it cannot be one, so this repository uses the flags that fail.
lock-check:
	uv lock --check --offline

# Install exactly what uv.lock pins, refusing if the lock is stale.
sync:
	uv sync --locked

verify: lock-check lint format typecheck test audit
	@echo "make verify: all gates passed."

# `action` is in scope because `action/render_result.py` ships: `git archive` puts it in the tree
# a consumer downloads on every `uses: ChelseaKR/fhir-scorecard@<tag>`, and it runs on their
# runner. It was outside all three of these targets, so `make lint` printed "All checks passed!"
# and `make typecheck` printed "Success" over a file neither had opened. Nothing was wrong with
# the file; the gates simply could not have said so.
# `tests/test_shipped_code_is_gated.py` fails if this list stops covering everything that ships.
lint:
	$(PYTHON) -m ruff check src tests action

format:
	$(PYTHON) -m ruff format --check src tests action

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing -q

# Known-vulnerability scan over the *locked* dependency set, not over whatever happens to be
# installed, so the audit answers for what CI will install. There are no runtime dependencies,
# so what this audits is the dev toolchain, which is the whole dependency surface.
#
# --no-emit-project drops the local package, which is not on PyPI and cannot be looked up.
# --strict then means what it should: an advisory fails the gate, and so does a dependency
# that could not be audited at all. No `|| true`, and nothing muted.
#
# `--frozen` is correct on the export: it reads the lock without re-resolving, and `lock-check`
# has already established that the lock is current.
audit:
	@mkdir -p .cache
	uv export --frozen --no-emit-project --no-hashes --output-file .cache/locked-requirements.txt
	$(PYTHON) -m pip_audit --strict --requirement .cache/locked-requirements.txt

site:
	$(PYTHON) -m fhir_scorecard.cli grade --registry data/registry.json --out site
