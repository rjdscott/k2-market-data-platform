.PHONY: chaos help up down logs ps test test-python test-rust test-legacy-kotlin dev-up check-docs check-alerts build-capture lint lake-verify

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

test: test-python test-rust  ## Run all unit tests


test-python:  ## Contract, parity, lake-offset and wire-format unit tests (needs uv)
	uv run --no-project --with pytest --with pyyaml pytest tests -q

# The WHOLE repo is mounted, not just the crate: src/record.rs compiles the wire
# contract in with `include_str!("../../../schemas/avro/trade.avsc")` and the
# replay tests load `../../config/instruments.yaml`. Mounting only the crate is
# an include_str! *compile* error, not a skipped test.
# Registry and target live in named volumes, not under the repo: the container
# runs as root, so a bind-mounted target/ ends up root-owned on the host and the
# next run (or the IDE) fails with EACCES on .cargo-build-lock. The volumes also
# keep a rebuild at seconds instead of minutes (librdkafka and rustls compile
# from source).
test-rust:  ## Rust capture unit tests (runs in rust:1-bookworm; no local cargo needed)
	docker run --rm -v "$(CURDIR)":/repo -w /repo/services/capture-rust \
	  -v k2-capture-cargo:/usr/local/cargo/registry \
	  -v k2-capture-target:/repo/services/capture-rust/target rust:1-bookworm \
	  sh -c 'apt-get update -qq && apt-get install -y -qq cmake clang libclang-dev >/dev/null && cargo test --locked'

# NOT part of `make test` and not run by CI: the Kotlin tier retired to
# legacy/v2-kotlin/ in ADR-019 and nothing in the running stack builds it. Kept
# runnable so the archive is verifiable rather than merely present.
# --user: Gradle writes build/, .gradle/, .kotlin/ and logs/ straight into the
# bind-mounted archive. As root those come out root-owned on the host and the
# next run — or `git clean` — fails with EACCES. HOME=/tmp because the mapped
# uid has no passwd entry and Gradle wants a writable home.
test-legacy-kotlin:  ## Archived v2 Kotlin feed handler tests (legacy/v2-kotlin; not in `make test`)
	docker run --rm --user $(shell id -u):$(shell id -g) \
	  -v "$(CURDIR)":/project -w /project/legacy/v2-kotlin \
	  -e GRADLE_USER_HOME=/tmp/.gradle -e HOME=/tmp \
	  gradle:8.12-jdk21 ./gradlew test --no-daemon

build-capture:  ## Build k2-capture:v3 from the repo root, stamping the git sha
	docker compose build capture-binance

check-alerts:  ## promtool: syntax-check every rule file and run the capture + lake alert unit tests
	docker run --rm -v "$(CURDIR)/docker/prometheus":/p --entrypoint sh prom/prometheus \
	  -c 'promtool check rules /p/rules/*.yml'
	docker run --rm -v "$(CURDIR)/docker/prometheus":/p --entrypoint sh prom/prometheus \
	  -c 'promtool test rules /p/tests/*.test.yml /p/rules/tests/*_test.yml'

lint:  ## Ruff over the v3 lake and the tests (same scope as CI)
	uv run --no-project --with ruff ruff check docker/lake tests

lake-verify:  ## Phase D exit criteria against the LIVE stack: offsets gapless, raw == bronze, double-run adds 0
	bash scripts/lake-verify.sh

chaos:  ## Inject each capture and lake failure, wait for its alert, measure recovery (LOCAL ONLY - breaks the running stack)
	@echo "chaos: breaks the running stack and drops real market data - public feeds do not replay it."
	@echo "       Maintainer-run, never CI (docs/research/2026-08-26-v3-requirements-clarification.md Q3)."
	scripts/chaos/capture-kill.sh        --exchange kraken
	scripts/chaos/capture-pause.sh       --exchange coinbase
	scripts/chaos/capture-pause.sh       --exchange binance
	scripts/chaos/capture-queue-full.sh  --exchange kraken
	scripts/chaos/redpanda-stop.sh       --exchange kraken
	scripts/chaos/capture-corrupt-frame.sh
	scripts/chaos/lake-lakekeeper-stop.sh
	scripts/chaos/lake-minio-stop.sh
	scripts/chaos/lake-ingest-kill.sh
	scripts/chaos/lake-corrupt-payload.sh
	@echo "results: scripts/chaos/results/$$(date -u +%F).tsv"
	@echo "copy the measured recovery times into docs/architecture/failure-modes.md by hand, with the date"
