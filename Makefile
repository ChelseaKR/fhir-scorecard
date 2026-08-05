PYTHON ?= .venv/bin/python

.PHONY: verify lint typecheck test site

verify: lint typecheck test

lint:
	$(PYTHON) -m ruff check src tests

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing -q

site:
	$(PYTHON) -m fhir_scorecard.cli grade --registry data/registry.json --out site
