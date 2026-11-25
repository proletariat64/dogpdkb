PYTHON ?= python3.12
export DEV_PYTHON=.venv/bin/python
UV ?= uv
DIST_DIR ?= dist
TARGET ?= antcode
CARGO ?= cargo
MDCOMPARE_RUST_DIR := experiments/mdcompare-rust
MDCOMPARE_RUST_TARGET := x86_64-unknown-linux-musl
MDOCR_RUST_DIR := experiments/mdocr-rust
MDOCR_RUST_TARGET := x86_64-unknown-linux-gnu
MDCOMPARE_CARGO_TARGET_DIR ?= $(or $(CARGO_TARGET_DIR),$(MDCOMPARE_RUST_DIR)/target)
MDOCR_CARGO_TARGET_DIR ?= $(or $(CARGO_TARGET_DIR),$(MDOCR_RUST_DIR)/target)

.PHONY: bootstrap doc-guard test lint check build smoke checkr buildr smoker doctor release publish-release publish-site-release
.PHONY: checkr-mdcomparer buildr-mdcomparer smoker-mdcomparer checkr-mdocrr buildr-mdocrr smoker-mdocrr

bootstrap:
	$(PYTHON) -B scripts/bootstrap.py

doc-guard:
	$(DEV_PYTHON) scripts/doc_guard.py

check: doc-guard lint test

build:
	UV_OFFLINE="$${UV_OFFLINE:-1}" $(UV) build --python "$(DEV_PYTHON)" --out-dir "$(DIST_DIR)"

smoke: build
	$(DEV_PYTHON) scripts/smoke_install.py --dist "$(DIST_DIR)"

checkr: checkr-mdcomparer checkr-mdocrr

buildr: buildr-mdcomparer buildr-mdocrr

smoker: smoker-mdcomparer smoker-mdocrr

checkr-mdcomparer:
	$(CARGO) fmt --manifest-path "$(MDCOMPARE_RUST_DIR)/Cargo.toml" --check
	$(CARGO) clippy --locked --manifest-path "$(MDCOMPARE_RUST_DIR)/Cargo.toml" --target-dir "$(MDCOMPARE_CARGO_TARGET_DIR)" --all-targets -- -D warnings
	$(CARGO) test --locked --manifest-path "$(MDCOMPARE_RUST_DIR)/Cargo.toml" --target-dir "$(MDCOMPARE_CARGO_TARGET_DIR)" --all-targets

buildr-mdcomparer:
	$(CARGO) build --locked --release --manifest-path "$(MDCOMPARE_RUST_DIR)/Cargo.toml" --target "$(MDCOMPARE_RUST_TARGET)" --target-dir "$(MDCOMPARE_CARGO_TARGET_DIR)"

smoker-mdcomparer: buildr-mdcomparer
	bash scripts/smoke_mdcompare_rust.sh "$(MDCOMPARE_CARGO_TARGET_DIR)/$(MDCOMPARE_RUST_TARGET)/release/mdcomparer"

checkr-mdocrr:
	$(CARGO) fmt --manifest-path "$(MDOCR_RUST_DIR)/Cargo.toml" --check
	$(CARGO) clippy --locked --manifest-path "$(MDOCR_RUST_DIR)/Cargo.toml" --target-dir "$(MDOCR_CARGO_TARGET_DIR)" --all-targets -- -D warnings
	$(CARGO) test --locked --manifest-path "$(MDOCR_RUST_DIR)/Cargo.toml" --target-dir "$(MDOCR_CARGO_TARGET_DIR)" --all-targets

buildr-mdocrr:
	RUSTFLAGS="$(strip $(RUSTFLAGS) -C target-feature=+crt-static)" $(CARGO) build --locked --release --manifest-path "$(MDOCR_RUST_DIR)/Cargo.toml" --target "$(MDOCR_RUST_TARGET)" --target-dir "$(MDOCR_CARGO_TARGET_DIR)"

smoker-mdocrr: buildr-mdocrr
	bash scripts/smoke_mdocr_rust.sh "$(MDOCR_CARGO_TARGET_DIR)/$(MDOCR_RUST_TARGET)/release/mdocrr"

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

