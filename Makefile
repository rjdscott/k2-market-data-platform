.PHONY: chaos help up down logs ps test test-kotlin test-python test-rust dev-up check-docs check-alerts build-capture

# Stamped into the capture binary's k2_capture_build_info gauge. `git describe`
# and not `rev-parse`: an image built from a dirty tree must not claim to be the
# commit it was started from. Overridable, and `unknown` outside a git checkout.
K2_GIT_SHA ?= $(shell git describe --always --dirty 2>/dev/null || echo unknown)
export K2_GIT_SHA
.DEFAULT_GOAL := help

help:  ## Show available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

up:  ## Start the full stack (builds images on first run)
	docker compose up -d

dev-up: ## Bring the stack up the safe way (override, rebuild, recreate mount holders, health, data-flow probe)
	bash scripts/dev-up.sh

check-docs: ## Doc gates (links, forbidden words, promtool, runbook paths, capacity-model gate)
	bash scripts/check-docs.sh

down:  ## Stop the stack (volumes kept)
	docker compose down

logs:  ## Tail all service logs
	docker compose logs -f

ps:  ## Show service status
	docker compose ps

test: test-kotlin test-python test-rust  ## Run all unit tests

test-kotlin:  ## Feed handler unit tests (runs in the JDK 21 build image; no local JDK needed)
	docker run --rm -v "$(CURDIR)":/project -w /project/services/feed-handler-kotlin \
	  -e GRADLE_USER_HOME=/tmp/.gradle gradle:8.12-jdk21 ./gradlew test --no-daemon

test-python:  ## Iceberg offload flow unit tests (needs uv)
	uv run --no-project --with prefect --with psycopg2-binary --with pytest pytest tests -q

# The WHOLE repo is mounted, not just the crate: src/record.rs compiles the wire
# contract in with `include_str!("../../../schemas/avro/trade.avsc")` and the
# replay tests load `../../config/instruments.yaml`. Mounting only the crate is
# an include_str! *compile* error, not a skipped test.
test-rust:  ## Rust capture unit tests (runs in rust:1-bookworm; no local cargo needed)
	docker run --rm -v "$(CURDIR)":/repo -w /repo/services/capture-rust rust:1-bookworm \
	  sh -c 'apt-get update -qq && apt-get install -y -qq cmake clang libclang-dev >/dev/null && cargo test --locked'

build-capture:  ## Build k2-capture:v3 from the repo root, stamping the git sha
	docker compose build capture-binance

check-alerts:  ## promtool: syntax-check every rule file and run the capture alert unit tests
	docker run --rm -v "$(CURDIR)/docker/prometheus":/p --entrypoint sh prom/prometheus \
	  -c 'promtool check rules /p/rules/*.yml'
	docker run --rm -v "$(CURDIR)/docker/prometheus":/p --entrypoint promtool prom/prometheus \
	  test rules /p/tests/capture-alerts.test.yml

chaos:  ## Inject each capture failure, wait for its alert, measure recovery (LOCAL ONLY - breaks the running stack)
	@echo "chaos: breaks the running stack and drops real market data - public feeds do not replay it."
	@echo "       Maintainer-run, never CI (docs/research/2026-08-26-v3-requirements-clarification.md Q3)."
	scripts/chaos/capture-kill.sh        --exchange kraken
	scripts/chaos/capture-pause.sh       --exchange coinbase
	scripts/chaos/capture-pause.sh       --exchange binance
	scripts/chaos/capture-queue-full.sh  --exchange kraken
	scripts/chaos/redpanda-stop.sh       --exchange kraken
	scripts/chaos/capture-corrupt-frame.sh
	@echo "results: scripts/chaos/results/$$(date -u +%F).tsv"
	@echo "copy the measured recovery times into docs/architecture/failure-modes.md by hand, with the date"
