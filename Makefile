PYTHON ?= python3.12
export DEV_PYTHON=.venv/bin/python
UV ?= uv
DIST_DIR ?= dist
TARGET ?= antcode
CARGO ?= cargo
MDCOMPARE_RUST_DIR := experiments/mdcompare-rust
MDCOMPARE_RUST_TARGET := x86_64-unknown-linux-musl
CARGO_TARGET_DIR ?= $(MDCOMPARE_RUST_DIR)/target

.PHONY: bootstrap doc-guard test lint check build smoke checkr buildr smoker doctor release publish-release publish-site-release

bootstrap:
	$(PYTHON) -B scripts/bootstrap.py

doc-guard:
	$(DEV_PYTHON) scripts/doc_guard.py

check: doc-guard lint test

build:
	UV_OFFLINE="$${UV_OFFLINE:-1}" $(UV) build --python "$(DEV_PYTHON)" --out-dir "$(DIST_DIR)"

smoke: build
	$(DEV_PYTHON) scripts/smoke_install.py --dist "$(DIST_DIR)"

checkr:
	$(CARGO) fmt --manifest-path "$(MDCOMPARE_RUST_DIR)/Cargo.toml" --check
	$(CARGO) clippy --locked --manifest-path "$(MDCOMPARE_RUST_DIR)/Cargo.toml" --target-dir "$(CARGO_TARGET_DIR)" --all-targets -- -D warnings
	$(CARGO) test --locked --manifest-path "$(MDCOMPARE_RUST_DIR)/Cargo.toml" --target-dir "$(CARGO_TARGET_DIR)" --all-targets

buildr:
	$(CARGO) build --locked --release --manifest-path "$(MDCOMPARE_RUST_DIR)/Cargo.toml" --target "$(MDCOMPARE_RUST_TARGET)" --target-dir "$(CARGO_TARGET_DIR)"

smoker: buildr
	bash scripts/smoke_mdcompare_rust.sh "$(CARGO_TARGET_DIR)/$(MDCOMPARE_RUST_TARGET)/release/mdcompare"

doctor:
	$(DEV_PYTHON) -m dogpdkb doctor $(DOCTOR_ARGS)

release:
	VERSION="$(VERSION)" DRY_RUN="$(DRY_RUN)" RETRY="$(RETRY)" CI_RUN_ID="$(CI_RUN_ID)" \
		$(DEV_PYTHON) scripts/release.py --target "$(TARGET)"

publish-release:
	$(DEV_PYTHON) scripts/publish_release.py --target "$(TARGET)" --tag "$(TAG)" --dist "$(DIST_DIR)" $(PUBLISH_ARGS)

publish-site-release:
	$(DEV_PYTHON) scripts/publish_site_release.py --tag "$(TAG)"

test:
	$(DEV_PYTHON) -m pytest -q --durations=20

lint:
	$(DEV_PYTHON) -m ruff check .
	$(DEV_PYTHON) -m ruff format --check .

