.PHONY: chaos help up down logs ps test test-kotlin test-python test-rust dev-up check-docs
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

test-rust:  ## Rust capture unit tests (runs in rust:1-bookworm; no local cargo needed)
	docker run --rm -v "$(CURDIR)/services/capture-rust":/w -w /w rust:1-bookworm \
	  sh -c 'apt-get update -qq && apt-get install -y -qq cmake clang libclang-dev >/dev/null && cargo test'

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
