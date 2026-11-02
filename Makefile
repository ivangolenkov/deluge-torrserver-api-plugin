.DEFAULT_GOAL := help
PYTHON ?= python3
VENV ?= .venv
BUILD_DIR ?= build
DIST_DIR ?= dist
TEST_PROFILE_ROOT ?=
CI_PLATFORM ?= linux/amd64
CI_IMAGE ?= deluge-stream-check:local
CI_PYTHON ?= 3.11
RUN = $(VENV)/bin/python
VERSION = $(shell $(PYTHON) tools/version.py)
EGG = $(DIST_DIR)/$(shell $(RUN) tools/version.py --egg-name)
RELEASE_TAG ?= $(shell $(PYTHON) tools/version.py --tag)
COMPOSE ?= docker compose
PRODUCT_IMAGE ?= deluge-stream:local
LOAD_SECONDS ?= 900
LOAD_CYCLES ?= 1000
LOAD_REPORT ?= $(BUILD_DIR)/performance.json
LOAD_PIECE_KIB ?= 64
PREVIOUS_EGG ?=
PREVIOUS_REF ?=
DEV_REQUIREMENTS = $(shell $(PYTHON) -c 'import sys; print("requirements-dev314.txt" if sys.version_info[:2] == (3, 14) else "requirements-dev.txt")')
BUNDLE_PY311 ?= build/checked311/dist
BUNDLE_PY314 ?= build/checked314/dist

.PHONY: help bootstrap lint format-check check-docs test-unit test-persistence test-integration test-downloads test-web test-web-split test build smoke check ci-check clean
help:
	@echo 'docker-build/up/down: product Compose; prepare RPC secret; configure public_base_url in UI.'
	@echo 'candidate-check: build + installed smoke + manifest/checksums (LICENSE may be deferred).'
	@echo 'release-check: candidate-check + clean checkout/tag gates; LICENSE optional; never publishes.'
	@echo 'Stream: core/web + FIFO + TorrServer HTTP/streaming; client acceptance pending.'
	@echo 'bootstrap: venv + pinned tools; Linux runtime also needs python3-libtorrent.'
	@echo 'lint format-check check-docs test-unit: offline source checks after bootstrap.'
	@echo 'build: Python-versioned egg in DIST_DIR; smoke: install/enable/disable that egg.'
	@echo 'Version: Git release tag, or 0.0.0.dev0 without one; no manual source version bump.'
	@echo 'test-persistence: SQLite policy/migrations/worker and recovery-ledger tests.'
	@echo 'test-integration: SQLite, native HTTP/streaming/QoS/preload/restart, policy/FIFO, guards, ZIP64.'
	@echo 'test-updates: isolated core/web + Chromium release install/restart; local network fixtures.'
	@echo 'test: unit + integration; check: docs/lint/format/test/build/smoke.'
	@echo 'test-packaging: two clean-directory builds; check also verifies candidate manifest/checksums.'
	@echo 'test-docker: isolated product Compose recreation; PRODUCT_IMAGE selects a prebuilt image.'
	@echo 'test-delivery: installed core replacement/schema migration/disable/uninstall (optional PREVIOUS_EGG).'
	@echo 'test-performance: two rate-limited 50 Mbit/s readers; LOAD_SECONDS=900 LOAD_CYCLES=1000.'
	@echo 'LOAD_REPORT: JSON metrics; short runs are diagnostics, not 15-minute acceptance.'
	@echo 'test-downloads: native file/ZIP/tickets + large ZIP64; test-web: real Chromium + core/web.'
	@echo 'test-direct: installed native-ready disk reader, Range/ZIP/recheck/drain barriers.'
	@echo 'ci-check: Docker build + isolated make check, logs in build/ci (CI_PLATFORM/CI_IMAGE).'
	@echo 'CI_PYTHON=3.14: Arch x86_64 check image; 3.11 remains the default runtime.'
	@echo 'combine-bundles: verify and join BUNDLE_PY311/BUNDLE_PY314 without rebuilding eggs.'
	@echo 'clean: only default generated build/dist/tool caches (never Deluge profiles).'
	@echo 'Variables: PYTHON=$(PYTHON) VENV=$(VENV) BUILD_DIR=$(BUILD_DIR) DIST_DIR=$(DIST_DIR)'
	@echo 'TEST_PROFILE_ROOT: optional existing parent for disposable test profiles.'
bootstrap:
	$(PYTHON) -m venv --system-site-packages "$(VENV)"
	$(RUN) -m pip install -r "$(DEV_REQUIREMENTS)"
lint:
	$(RUN) -m ruff check .
format-check:
	$(RUN) -m ruff format --check .
check-docs:
	$(RUN) tools/check_docs.py
test-unit:
	$(RUN) -m pytest -q tests/unit
test-persistence:
	$(RUN) -m pytest -q tests/persistence
build:
	$(RUN) setup.py egg_info --egg-base "$(CURDIR)" build --build-base "$(BUILD_DIR)" bdist_egg --bdist-dir "$(BUILD_DIR)/bdist.egg" --dist-dir "$(DIST_DIR)"
test-integration: build test-persistence
	$(RUN) tools/runtime_check.py --mode integration --dist "$(DIST_DIR)" --profile-root "$(TEST_PROFILE_ROOT)"
test-downloads: build
	$(RUN) -I tests/runtime/downloads.py --egg "$(EGG)" --profile-root "$(TEST_PROFILE_ROOT)"
	$(RUN) tools/zip_probe.py
test-web: build
	$(RUN) -I tests/runtime/web_browser.py --egg "$(EGG)" --profile-root "$(TEST_PROFILE_ROOT)"
.PHONY: test-updates
test-updates: build
	$(RUN) -I tests/runtime/updates.py --egg "$(EGG)" --profile-root "$(TEST_PROFILE_ROOT)"
.PHONY: test-direct
test-direct: build
	$(RUN) -I tests/runtime/direct_reads.py --egg "$(EGG)" --profile-root "$(TEST_PROFILE_ROOT)"
test-web-split:
	$(PYTHON) tools/split_web_check.py --image "$(CI_IMAGE)" --platform "$(CI_PLATFORM)" --python "$(CI_PYTHON)" --dist "$(DIST_DIR)"
.PHONY: test-performance
test-performance: build
	$(RUN) -I tests/runtime/performance.py --egg "$(EGG)" --profile-root "$(TEST_PROFILE_ROOT)" --seconds "$(LOAD_SECONDS)" --cycles "$(LOAD_CYCLES)" --piece-kib "$(LOAD_PIECE_KIB)" --output "$(LOAD_REPORT)"
.PHONY: test-delivery
test-delivery: build
	$(RUN) -I tests/runtime/delivery_lifecycle.py --egg "$(EGG)" --previous-egg "$(PREVIOUS_EGG)" --profile-root "$(TEST_PROFILE_ROOT)"
.PHONY: test-delivery-historical
test-delivery-historical: build
	$(RUN) tools/historical_delivery.py --egg "$(EGG)" --previous-ref "$(PREVIOUS_REF)" --profile-root "$(TEST_PROFILE_ROOT)"
test: test-unit test-integration test-downloads test-web test-direct test-updates
smoke: build
	$(RUN) tools/runtime_check.py --mode smoke --dist "$(DIST_DIR)" --profile-root "$(TEST_PROFILE_ROOT)"
check: check-docs lint format-check test smoke test-packaging test-delivery candidate-check
.PHONY: test-packaging
test-packaging:
	$(RUN) tools/packaging_check.py
.PHONY: candidate-check release-check
candidate-check: smoke
	$(RUN) tools/release_bundle.py --create --dist "$(DIST_DIR)"
release-check: candidate-check
	$(RUN) tools/release_bundle.py --dist "$(DIST_DIR)" --strict $(if $(RELEASE_TAG),--tag "$(RELEASE_TAG)")
.PHONY: docker-build docker-up docker-down
docker-build:
	STREAM_IMAGE="$(PRODUCT_IMAGE)" $(COMPOSE) build --build-arg STREAM_RELEASE_TAG="$(shell $(PYTHON) tools/version.py --tag)" core
docker-up:
	$(COMPOSE) up -d
docker-down:
	$(COMPOSE) down
.PHONY: test-docker
test-docker:
	$(PYTHON) tools/docker_check.py --image "$(PRODUCT_IMAGE)" --platform "$(CI_PLATFORM)"
.PHONY: ci-candidate smoke-existing release-verify
ci-candidate:
	docker run --rm --platform "$(CI_PLATFORM)" --network none -e STREAM_RELEASE_TAG="$(shell $(PYTHON) tools/version.py --tag)" -v "$(CURDIR):/work" "$(CI_IMAGE)" make candidate-check VENV=/opt/venv
smoke-existing:
	$(RUN) tools/runtime_check.py --mode smoke --dist "$(DIST_DIR)" --profile-root "$(TEST_PROFILE_ROOT)"
release-verify: smoke-existing
	$(RUN) tools/release_bundle.py --dist "$(DIST_DIR)" --strict $(if $(RELEASE_TAG),--tag "$(RELEASE_TAG)")
.PHONY: combine-bundles
combine-bundles:
	$(PYTHON) tools/release_bundle.py --dist "$(DIST_DIR)" --combine "$(BUNDLE_PY311)" "$(BUNDLE_PY314)" --strict $(if $(RELEASE_TAG),--tag "$(RELEASE_TAG)")
ci-check:
	$(PYTHON) tools/ci_check.py --platform "$(CI_PLATFORM)" --image "$(CI_IMAGE)" --python "$(CI_PYTHON)"
clean:
	$(PYTHON) tools/clean.py
