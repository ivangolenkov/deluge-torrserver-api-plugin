.DEFAULT_GOAL := help
PYTHON ?= python3
VENV ?= .venv
BUILD_DIR ?= build
DIST_DIR ?= dist
TEST_PROFILE_ROOT ?=
CI_PLATFORM ?= linux/amd64
CI_IMAGE ?= deluge-stream-check:local
RUN = $(VENV)/bin/python

.PHONY: help bootstrap lint format-check check-docs test-unit test-persistence test-integration test build smoke check ci-check clean
help:
	@echo 'Stream: core/web + FIFO + TorrServer HTTP/streaming; client acceptance pending.'
	@echo 'bootstrap: venv + pinned tools; Linux runtime also needs python3-libtorrent.'
	@echo 'lint format-check check-docs test-unit: offline source checks after bootstrap.'
	@echo 'build: Python-versioned egg in DIST_DIR; smoke: install/enable/disable that egg.'
	@echo 'test-persistence: SQLite policy/migrations/worker and recovery-ledger tests.'
	@echo 'test-integration: SQLite, native HTTP/streaming/QoS/preload/restart, policy/FIFO, guards, ZIP64.'
	@echo 'test: unit + integration; check: docs/lint/format/test/build/smoke.'
	@echo 'ci-check: Docker build + isolated make check, logs in build/ci (CI_PLATFORM/CI_IMAGE).'
	@echo 'clean: only default generated build/dist/tool caches (never Deluge profiles).'
	@echo 'Variables: PYTHON=$(PYTHON) VENV=$(VENV) BUILD_DIR=$(BUILD_DIR) DIST_DIR=$(DIST_DIR)'
	@echo 'TEST_PROFILE_ROOT: optional existing parent for disposable test profiles.'
bootstrap:
	$(PYTHON) -m venv --system-site-packages "$(VENV)"
	$(RUN) -m pip install -r requirements-dev.txt
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
	$(RUN) tools/zip_probe.py
test: test-unit test-integration
smoke: build
	$(RUN) tools/runtime_check.py --mode smoke --dist "$(DIST_DIR)" --profile-root "$(TEST_PROFILE_ROOT)"
check: check-docs lint format-check test smoke
ci-check:
	$(PYTHON) tools/ci_check.py --platform "$(CI_PLATFORM)" --image "$(CI_IMAGE)"
clean:
	$(PYTHON) tools/clean.py
