# `make verify` is the gate of record: .github/workflows/verify.yml runs this exact target
# against the locked environment, so a local pass and a CI pass mean the same thing. Run it
# before opening a PR.
PYTHON ?= .venv/bin/python

.PHONY: sync verify lint format typecheck test audit site

# Install exactly what uv.lock pins, and fail if the lock has drifted from pyproject.toml.
#
# --locked, not --frozen, and the difference is the whole control. `uv sync --frozen` installs
# the lockfile as it stands without comparing it to pyproject.toml: measured on uv 0.12.1,
# adding a dependency to pyproject.toml and re-running it exits 0 and installs the stale set.
# `uv sync --locked` exits 1 with "the lockfile needs to be updated". A drift check that
# passes on a drifted lock is not a check.
sync:
	uv sync --locked

verify: lint format typecheck test audit
	@echo "make verify: all gates passed."

lint:
	$(PYTHON) -m ruff check src tests

format:
	$(PYTHON) -m ruff format --check src tests

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
audit:
	@mkdir -p .cache
	uv export --frozen --no-emit-project --no-hashes --output-file .cache/locked-requirements.txt
	$(PYTHON) -m pip_audit --strict --requirement .cache/locked-requirements.txt

site:
	$(PYTHON) -m fhir_scorecard.cli grade --registry data/registry.json --out site
