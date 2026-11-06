PYTHON ?= python3.12
export DEV_PYTHON=.venv/bin/python
UV ?= uv
DIST_DIR ?= dist

.PHONY: bootstrap doc-guard test lint check build smoke doctor

bootstrap:
	$(PYTHON) -B scripts/bootstrap.py

doc-guard:
	$(DEV_PYTHON) scripts/doc_guard.py

check: doc-guard lint test

build:
	$(UV) build --python "$(DEV_PYTHON)" --out-dir "$(DIST_DIR)"

smoke: build
	$(DEV_PYTHON) scripts/smoke_install.py --dist "$(DIST_DIR)"

doctor:
	$(DEV_PYTHON) -m dogpdkb doctor $(DOCTOR_ARGS)

test:
	$(DEV_PYTHON) -m pytest -q --durations=20

lint:
	$(DEV_PYTHON) -m ruff check .
	$(DEV_PYTHON) -m ruff format --check .
