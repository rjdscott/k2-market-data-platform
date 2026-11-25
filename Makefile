.PHONY: chaos help up down logs ps health gates test test-python test-rust test-airflow record-fixtures test-notebooks test-legacy-kotlin dev-up check-docs check-alerts build-capture lint lake-verify lake-rebuild test-clickhouse test-trino parity-ohlcv parity-bars notebooks notebooks-run test-k2gen generate new-product screenshots

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

health: ## Is it up: every service healthy, a message on every venue in the last 2 min, last lake commit + ingest run
	bash scripts/health.sh

check-docs: ## Doc gates (links, forbidden words, promtool, runbook paths, capacity-model gate, no em-dash on added lines, synthetic fixtures)
	bash scripts/check-docs.sh

down:  ## Stop the stack (volumes kept)
	docker compose down

logs:  ## Tail all service logs
	docker compose logs -f

ps:  ## Show service status
	docker compose ps

test: test-python test-rust test-clickhouse test-trino test-notebooks test-k2gen test-airflow  ## Run all unit tests

# One recipe line per gate, not prerequisites: recipe lines run in order and
# stop at the first failure even under `make -j`, where prerequisites would not
# keep their order.
gates:  ## Every gate before a merge, in order, stopping at the first failure: test, lake-verify, parity-ohlcv, parity-bars, check-alerts, check-docs (needs the LIVE stack)
	$(MAKE) test
	$(MAKE) lake-verify
	$(MAKE) parity-ohlcv
	$(MAKE) parity-bars
	$(MAKE) check-alerts
	$(MAKE) check-docs


test-python:  ## Contract, parity, lake-offset and wire-format unit tests (needs uv)
	uv run --no-project --with pytest --with pyyaml --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q

# Inside the Airflow image itself: it carries no pytest and the root suite has no
# Airflow, so the DAG tests are stdlib unittest. The repo is mounted read-only
# and the container has no network: nothing here reaches the stack. The build
# is a cache hit after the first; CI's docker job runs the same tests in the
# image it just built.
test-airflow:  ## DAG structure, exit 75 to skipped, params and the kill path, inside k2-airflow:3.3.2 (no stack needed)
	docker build -q -f docker/airflow/Dockerfile -t k2-airflow:3.3.2 . >/dev/null
	docker run --rm --network none -e AIRFLOW__LOGGING__LOGGING_LEVEL=WARNING -v "$(CURDIR)":/repo:ro k2-airflow:3.3.2 \
	  python -m unittest discover -s /repo/docker/airflow/tests

# The WHOLE repo is mounted, not just the crate: src/record.rs compiles the wire
# contract in with `include_str!("../../../schemas/avro/trade.avsc")` and the
# replay tests load `../../config/instruments.yaml`. Mounting only the crate is
# an include_str! *compile* error, not a skipped test.
# Registry and target live in named volumes, not under the repo: the container
# runs as root, so a bind-mounted target/ ends up root-owned on the host and the
# next run (or the IDE) fails with EACCES on .cargo-build-lock. The volumes also
# keep a rebuild at seconds instead of minutes (librdkafka and rustls compile
# from source).
test-clickhouse:  ## gold schema semantics on a throwaway clickhouse:24.3 (OHLCV regression, FINAL dedup, BBO math, quant user)
	bash scripts/clickhouse-schema-test.sh

test-trino:  ## Trino on a throwaway trinodb/trino, no stack: etc/ starts, catalog lake refuses every commit (table procedures too), only a domain's writer changes its tables with SQL statements
	bash scripts/trino-rules-test.sh

test-rust:  ## Rust capture unit tests (runs in rust:1-bookworm; no local cargo needed)
	docker run --rm -v "$(CURDIR)":/repo -w /repo/services/capture-rust \
	  -v k2-capture-cargo:/usr/local/cargo/registry \
	  -v k2-capture-target:/repo/services/capture-rust/target rust:1-bookworm \
	  sh -c 'apt-get update -qq && apt-get install -y -qq cmake clang libclang-dev >/dev/null && cargo test --locked'

# The committed replay fixtures are synthetic (NOTICE). This is the stronger
# evidence: real sessions recorded on this machine from the venues' public
# endpoints with `k2-capture record`, into a git-ignored directory, then the
# clean replay tests over them. Every invariant runs; the golden hash, which
# describes the synthetic session, is skipped. Never committed: the frames are
# the venues' data, under their terms. chown because the container is root.
record-fixtures:  ## Record real venue sessions into services/capture-rust/tests/recorded/ (git-ignored) and replay-test them
	docker run --rm -v "$(CURDIR)":/repo -w /repo/services/capture-rust \
	  -v k2-capture-cargo:/usr/local/cargo/registry \
	  -v k2-capture-target:/repo/services/capture-rust/target rust:1-bookworm \
	  sh -c 'apt-get update -qq && apt-get install -y -qq cmake clang libclang-dev >/dev/null && mkdir -p tests/recorded && \
	    rec() { cargo run -q --locked -- record --exchange $$1 --seconds $$2 --symbols $$3 \
	      --instruments-file ../../config/instruments.yaml > tests/recorded/$$1-$$2s.jsonl; } && \
	    rec kraken 20 BTC/USD && rec binance 10 BTCUSDT && rec coinbase 20 ATOM-USD && \
	    chown -R $(shell id -u):$(shell id -g) tests/recorded && wc -l tests/recorded/*.jsonl && \
	    K2_RECORDED_FIXTURES=/repo/services/capture-rust/tests/recorded \
	      cargo test --locked --test replay --test replay_binance --test replay_coinbase'

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
	docker run --rm -v "$(CURDIR)/docker/prometheus":/p --entrypoint sh prom/prometheus:v3.2.0 \
	  -c 'promtool check rules /p/rules/*.yml'
	docker run --rm -v "$(CURDIR)/docker/prometheus":/p --entrypoint sh prom/prometheus:v3.2.0 \
	  -c 'promtool test rules /p/tests/*.test.yml /p/rules/tests/*_test.yml'

# platform/k2gen is checked with the ruff and mypy pinned in its own lock, as CI's contracts job does.
lint:  ## Ruff over the v3 lake, the Airflow DAGs, scripts/ Python and the tests; ruff and mypy (strict) over platform/k2gen
	uv run --no-project --with ruff ruff check docker/lake docker/airflow scripts/airflow_shadow_check.py scripts/screenshots tests
	cd platform/k2gen && uv run --locked -q ruff check . && uv run --locked -q mypy

lake-verify:  ## Lake exit criteria against the LIVE stack: offsets gapless, every layer level with its parent, double-run adds 0
	bash scripts/lake-verify.sh

# Drops and re-decodes a whole lake layer from its parent, under the writer
# lock, one day per venue at a time. Pause the lake_ingest DAG first (see
# docker/lake/rebuild.py). EXCHANGE=kraken limits it to one venue.
lake-rebuild:  ## Rebuild LAYER=bronze|silver|gold|books|bars from its parent over the whole archive (LIVE stack; minutes to hours)
	docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer $(or $(LAYER),bronze) $(if $(EXCHANGE),--exchange $(EXCHANGE),)

notebooks:  ## JupyterLab on :8889 over the lake (DuckDB via Lakekeeper + MinIO on the published ports)
	cd notebooks && uv sync -q && uv run jupyter lab --port 8889 --no-browser

test-notebooks:  ## k2lake.trades/completeness against synthetic DuckDB tables (no stack needed)
	cd notebooks && uv sync -q && uv run pytest -q

# --locked: a pyproject.toml edited without `uv lock` fails here, not in someone else's sync.
test-k2gen:  ## k2gen: contracts/ against ODCS v3, the K2 schema and lake.sql, plus each check made to fail (no stack needed)
	cd platform/k2gen && uv run --locked -q pytest -q

# CI runs this and fails on any diff or untracked file: generated files are never edited by hand.
generate:  ## Validate contracts/, then rewrite docker/lake/ddl/generated/, docker/lake/generated/{retention.json,warehouses.txt}, docs/products/, and the per-domain Trino catalogs and rules.json under docker/trino/
	cd platform/k2gen && uv run --locked -q k2gen generate

new-product:  ## Scaffold contracts/DOMAIN/NAME/: a descriptor and one contract with inline columns that validates (DOMAIN=x NAME=y)
	cd platform/k2gen && uv run --locked -q k2gen new "$(DOMAIN)" "$(NAME)"

notebooks-run:  ## Execute the five notebooks headless — the runnable check (needs the stack up)
	cd notebooks && uv sync -q && for nb in 0*.ipynb; do uv run jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=600 $$nb || exit 1; done

# Read-only against the LIVE stack: logs in, opens pages, runs one SELECT as
# ClickHouse's read-only quant user (scripts/screenshots/capture.py). Secrets
# go from .env and the Airflow api-server into the container by NAME, never on
# a command line. --network host so every URL is the localhost one an operator
# opens; --user so the PNGs are not root-owned.
screenshots:  ## Capture every dashboard and console into docs/images/dashboards/ and rewrite docs/operations/dashboards.md (LIVE stack; ONLY=04-lake redoes one)
	docker build -q -f scripts/screenshots/Dockerfile -t k2-screenshots:1.65.2 scripts/screenshots >/dev/null
	set -a && . ./.env && set +a && \
	AIRFLOW_PASSWORD=$$(docker exec k2-airflow-api-server cat /opt/airflow/simple_auth_manager_passwords.json.generated \
	  | python3 -c 'import json, sys; print(json.load(sys.stdin)["admin"])') && export AIRFLOW_PASSWORD && \
	docker run --rm --network host --ipc=host --user $$(id -u):$$(id -g) -e HOME=/tmp \
	  -e GRAFANA_PASSWORD -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD -e K2_QUANT_PASSWORD -e AIRFLOW_PASSWORD \
	  -e K2_AIRFLOW_PORT -e K2_GIT_SHA -v "$(CURDIR)":/repo -w /repo k2-screenshots:1.65.2 \
	  python3 scripts/screenshots/capture.py $(if $(ONLY),--only $(ONLY),)

parity-ohlcv:  ## Three-way OHLCV parity (ClickHouse on-read, lake gold, DuckDB over silver) at tests/parity/pinned.json
	scripts/parity-ohlcv.sh

parity-bars:  ## Three-way event-bar parity (lake gold.bars, DuckDB window SQL, Python reference) at tests/parity/pinned.json
	scripts/parity-bars.sh

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
	scripts/chaos/clickhouse-stop.sh
	scripts/chaos/clickhouse-corrupt-record.sh
	@echo "results: scripts/chaos/results/$$(date -u +%F).tsv"
	@echo "copy the measured recovery times into docs/architecture/16-failure-modes.md by hand, with the date"
