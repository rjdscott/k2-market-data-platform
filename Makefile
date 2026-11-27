.PHONY: chaos help up down logs ps health backup backup-install gates test test-python test-rust test-airflow record-fixtures test-notebooks test-legacy-kotlin dev-up check-docs check-alerts build-capture lint lake-verify test-clickhouse test-trino test-dbt parity-ohlcv parity-bars test-k2gen screenshots

# Stamped into the capture binary's k2_capture_build_info gauge. `git describe`
# and not `rev-parse`: an image built from a dirty tree must not claim to be the
# commit it was started from. Overridable, and `unknown` outside a git checkout.
K2_GIT_SHA ?= $(shell git describe --always --dirty 2>/dev/null || echo unknown)
export K2_GIT_SHA
.DEFAULT_GOAL := help

help:  ## Show available targets
	@grep -h -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

up:  ## Start the full stack (builds images on first run); refuses a .env with placeholder or guessable secrets
	python3 scripts/check-env.py
	docker compose up -d

dev-up: ## Bring the stack up the safe way (override, rebuild, recreate mount holders, health, data-flow probe)
	bash scripts/dev-up.sh

health: ## Is it up: every service healthy, a message on every venue in the last 2 min, the book switch, the backup (the lake checks SKIP until phase 004)
	bash scripts/health.sh

backup:  ## Dump the catalog and Airflow databases, then copy the live release's backup set (restore tables at their ref, landings, the releases root) in tools with the backup key, to K2_BACKUP_DIR/runs/<UTC>; read-only against the stack
	bash scripts/backup-lake.sh

# The owner runs this, once, from the main checkout: the unit runs the script from
# the directory it was installed from. docs/runbooks/lake-backup-and-restore.md.
backup-install:  ## Schedule `make backup` daily at 04:30 UTC: a systemd user timer in ~/.config/systemd/user, enabled
	mkdir -p $(HOME)/.config/systemd/user
	sed 's|@K2_ROOT@|$(CURDIR)|g' scripts/systemd/k2-backup.service > $(HOME)/.config/systemd/user/k2-backup.service
	cp scripts/systemd/k2-backup.timer $(HOME)/.config/systemd/user/k2-backup.timer
	systemctl --user daemon-reload
	systemctl --user enable --now k2-backup.timer
	@echo "Scheduled daily at 04:30 UTC. Run 'make backup' now for the first copy: make health FAILs (5) until one succeeds."

check-docs: ## Doc gates (links, forbidden words, promtool, runbook paths, capacity-model gate, no em-dash on added lines, synthetic fixtures)
	bash scripts/check-docs.sh

down:  ## Stop the stack (volumes kept)
	docker compose down

logs:  ## Tail all service logs
	docker compose logs -f

ps:  ## Show service status
	docker compose ps

test: test-python test-rust test-clickhouse test-trino test-dbt test-notebooks test-k2gen test-airflow test-connectors test-api test-platform  ## Run all unit tests

# One recipe line per gate, not prerequisites: recipe lines run in order and
# stop at the first failure even under `make -j`, where prerequisites would not
# keep their order.
# The two static gates CI also runs go first: seconds, and no point testing a
# tree that fails them.
gates:  ## Every gate before a merge, in order, stopping at the first failure: lint, check-budget, test, lake-verify, parity-ohlcv, parity-bars, check-alerts, check-docs (needs the LIVE stack; the lake gates SKIP until phase 004)
	$(MAKE) lint
	python3 scripts/check-budget.py
	$(MAKE) test
	$(MAKE) lake-verify
	$(MAKE) parity-ohlcv
	$(MAKE) parity-bars
	$(MAKE) check-alerts
	$(MAKE) check-docs


test-python:  ## Contract, lake-offset, wire-format and script unit tests (needs uv)
	uv run --no-project --with pytest==9.2.0 --with pyyaml==6.0.4 --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q

# Inside the Airflow image itself: it carries no pytest and the root suite has no
# Airflow, so the tests are stdlib unittest. The repo is mounted read-only, and
# dbt/ again at /opt/k2/dbt, where compose mounts it for the scheduler; the
# container has no network and no metadata database. The DAG tests retired with the old DAGs
# (legacy/v3-lake-ingest/airflow/); what runs today is the image's dbt check
# (docker/airflow/tests/test_dbt_image.py). Nothing here reaches the stack. The
# build is a cache hit after the first; CI's docker job runs the same tests in
# the image it just built. The tests build their own tag, k2-airflow:test: the
# stack's services start from k2-airflow:3.3.2, and building that tag here (from
# whatever branch or worktree runs the tests) silently replaced the image the
# next recreate of an Airflow service would start from (P2 audit, 2026-11-26).
AIRFLOW_IMAGE ?= k2-airflow:test
test-airflow:  ## Every test under docker/airflow/tests (today the image's dbt against dbt/uv.lock and package-lock.yml), inside a k2-airflow:test build (no stack needed)
	docker build -q -f docker/airflow/Dockerfile -t $(AIRFLOW_IMAGE) . >/dev/null
	docker run --rm --network none -e AIRFLOW__LOGGING__LOGGING_LEVEL=WARNING \
	  -v "$(CURDIR)":/repo:ro -v "$(CURDIR)/dbt":/opt/k2/dbt:ro $(AIRFLOW_IMAGE) \
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

test-trino:  ## The rules suite on a throwaway lake (Trino over TLS, Lakekeeper, MinIO), no stack: a release compiled from fixtures starts, each role sees exactly its policy, the base is fenced, a negative control fails
	bash scripts/trino-rules-test.sh

test-dbt:  ## dbt/: sqlfluff lint, dbt parse and compile, the manifest check, then each compiled product built with its tests on a throwaway lake and its results table read back (scripts/dbt-test.sh; needs Docker, about 75 s)
	bash scripts/dbt-test.sh

test-rust:  ## Rust capture unit tests (runs in rust:1.100.0-bookworm; no local cargo needed)
	docker run --rm -v "$(CURDIR)":/repo -w /repo/services/capture-rust \
	  -v k2-capture-cargo:/usr/local/cargo/registry \
	  -v k2-capture-target:/repo/services/capture-rust/target rust:1.100.0-bookworm \
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
	  -v k2-capture-target:/repo/services/capture-rust/target rust:1.100.0-bookworm \
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

check-alerts:  ## promtool: syntax-check every rule file and run the alert unit tests
	docker run --rm -v "$(CURDIR)/docker/prometheus":/p --entrypoint sh prom/prometheus:v3.2.0 \
	  -c 'promtool check rules /p/rules/*.yml'
	docker run --rm -v "$(CURDIR)/docker/prometheus":/p --entrypoint sh prom/prometheus:v3.2.0 \
	  -c 'promtool test rules /p/tests/*.test.yml /p/rules/tests/*_test.yml'

# platform/k2gen is checked with the ruff and mypy pinned in its own lock, as CI's contracts job does.
lint:  ## Ruff over docker/lake, the Airflow image tests, the gate scripts and other scripts/ Python, and the tests; ruff and mypy (strict) over platform/k2gen
	uv run --no-project --with ruff==0.17.6 ruff check docker/lake docker/airflow scripts/screenshots scripts/check_docs.py scripts/check-budget.py scripts/check-env.py scripts/make-synthetic-fixtures.py scripts/principal-secret.py scripts/engine-keys.py scripts/backup_lake.py scripts/deploy.py scripts/access_verify.py scripts/trino_rules_test.py scripts/trino_rules_c1_test.py scripts/verify docker/tools docker/fetch docker/dbt-runner platform/runs platform/catalog-site tests
	cd platform/k2gen && uv run --locked -q ruff check . && uv run --locked -q mypy

# Stood down, not deleted: the old lake ingest, the tables these gates read and
# their scripts are archived in legacy/v3-lake-ingest/ (ADR-056), and no live data
# lands in the lake until phase 004 brings the gates back on the new tables.
lake-verify:  ## SKIP until phase 004: the lake exit criteria read the old pipeline's tables, retired
	@echo "SKIP lake-verify: the old lake ingest and its tables are retired (legacy/v3-lake-ingest/, ADR-056); returns in phase 004"

test-notebooks:  ## SKIP until phase 004: notebooks/ is archived with the old lake ingest
	@echo "SKIP test-notebooks: notebooks/ is archived with the old lake ingest (legacy/v3-lake-ingest/notebooks/); returns in phase 004"

# --locked: a pyproject.toml edited without `uv lock` fails here, not in someone else's sync.
test-k2gen:  ## k2gen: the checks, the emitters against their goldens, the compiler and each check made to fail (no stack needed)
	cd platform/k2gen && uv run --locked -q pytest -q

# Read-only against the LIVE stack: logs in and opens pages
# (scripts/screenshots/capture.py). Secrets go from .env and the Airflow
# api-server into the container by NAME, never on a command line. --network
# host so every URL is the localhost one an operator opens; --user so the PNGs
# are not root-owned.
screenshots:  ## Capture every dashboard and console into docs/images/dashboards/ and rewrite docs/operations/dashboards.md (LIVE stack; ONLY=02-capture redoes one)
	docker build -q -f scripts/screenshots/Dockerfile -t k2-screenshots:1.65.2 scripts/screenshots >/dev/null
	set -a && . ./.env && set +a && \
	AIRFLOW_PASSWORD=$$(docker exec k2-airflow-api-server cat /opt/airflow/simple_auth_manager_passwords.json.generated \
	  | python3 -c 'import json, sys; print(json.load(sys.stdin)["admin"])') && export AIRFLOW_PASSWORD && \
	docker run --rm --network host --ipc=host --user $$(id -u):$$(id -g) -e HOME=/tmp \
	  -e GRAFANA_PASSWORD -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD -e AIRFLOW_PASSWORD \
	  -e K2_AIRFLOW_PORT -e K2_GIT_SHA -v "$(CURDIR)":/repo -w /repo k2-screenshots:1.65.2 \
	  python3 scripts/screenshots/capture.py $(if $(ONLY),--only $(ONLY),)

# Stood down like lake-verify above: the pinned lake tables both compare were
# dropped with the old ingest, and the scripts and pins are archived in
# legacy/v3-lake-ingest/. Phase 004 re-pins them on Kraken.
parity-ohlcv:  ## SKIP until phase 004: OHLCV parity over the old pipeline's tables, retired
	@echo "SKIP parity-ohlcv: the pinned lake tables were dropped with the old lake ingest (legacy/v3-lake-ingest/); returns in phase 004"

parity-bars:  ## SKIP until phase 004: event-bar parity over the old pipeline's tables, retired
	@echo "SKIP parity-bars: the pinned lake tables were dropped with the old lake ingest (legacy/v3-lake-ingest/); returns in phase 004"

chaos:  ## Inject each capture failure, wait for its alert, measure recovery (LOCAL ONLY - breaks the running stack; the lake faults SKIP until phase 004)
	@echo "chaos: breaks the running stack and drops real market data - public feeds do not replay it."
	@echo "       Maintainer-run, never CI (docs/research/2026-08-26-v3-requirements-clarification.md Q3)."
	scripts/chaos/capture-kill.sh        --exchange kraken
	scripts/chaos/capture-pause.sh       --exchange coinbase
	scripts/chaos/capture-pause.sh       --exchange binance
	scripts/chaos/capture-queue-full.sh  --exchange kraken
	scripts/chaos/redpanda-stop.sh       --exchange kraken
	scripts/chaos/capture-corrupt-frame.sh
	@echo "SKIP the four lake faults: archived with the old lake ingest (legacy/v3-lake-ingest/scripts/chaos/); returns in phase 004"
	@echo "results: scripts/chaos/results/$$(date -u +%F).tsv"
	@echo "copy the measured recovery times into docs/architecture/16-failure-modes.md by hand, with the date"

.PHONY: drop
drop:  ## Copy, compare, then drop TARGET=<warehouse>.<namespace>[.<table>] or TARGET=bucket:<name>; DRY_RUN=1 lists only (docs/runbooks/drop-and-rebuild.md)
	bash scripts/drop.sh

# One file per owning step of phase 001, so parallel steps never edit this file
# (docs/development/phase-001-interfaces.md). A target already defined above is
# never redefined there. `help` greps every included file (-h: no file names).
include mk/*.mk

.PHONY: catalog-fence
catalog-fence:  ## Dial every probe row of tests/access/expectations.yaml from its network: the catalog fence, refused or open as expected (ADR-059 part 7; FILE=<other expectations.yaml>)
	bash scripts/verify/catalog-fence.sh $(FILE)
