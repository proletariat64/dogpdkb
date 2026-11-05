PYTHON ?= python3.12
DEV_PYTHON = .venv/bin/python

.PHONY: bootstrap test lint

bootstrap:
	$(PYTHON) -B scripts/bootstrap.py

test:
	$(DEV_PYTHON) -m pytest -q --durations=20

lint:
	$(DEV_PYTHON) -m ruff check .
	$(DEV_PYTHON) -m ruff format --check .
