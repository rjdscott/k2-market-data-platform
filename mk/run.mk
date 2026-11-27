# Owned by phase 002's run path (docs/research/2026-11-27-phases-002-003-build-breakdown.md):
# W0-5 adds test-connectors, W1-3 engine-keys, W1-22 backup-verify; phase 003 W3-5 test-api. No file under mk/ redefines a target of the
# root Makefile (tests/test_mk_split.py).

.PHONY: engine-keys
engine-keys:  ## [ENV_FILE=.env] [ROTATE=<engine>] Each engine's MinIO key pair and K2_RUN_UID/GID into the env file, only where missing or still the .env.example placeholder; prints the names, never a value (scripts/engine-keys.py)
	@python3 scripts/engine-keys.py --env-file "$${ENV_FILE:-.env}" $${ROTATE:+--rotate "$$ROTATE"}

# A deploy runs lake-init only when its release changes lake-init, so a key written into .env reaches
# MinIO through this: deploy's own invocation (scripts/deploy.py ddl) against the live release, its
# compose, script, policies and storage plan, the images it names pinned to the manifest's.
.PHONY: lake-init
lake-init:  ## [RECREATE="<service> ..."] Rerun lake-init on the live release (pointers/trino) as deploy runs it, so MinIO holds each engine's secret from .env, then recreate RECREATE's services through the release so they read it. After `make engine-keys ROTATE=<engine>`: trino for trino, spark-iceberg for spark and maintenance, none for backup and tools
	@rel=$$(readlink -f "$${K2_RELEASES:-$$HOME/k2-releases}/pointers/trino") && [ -f "$$rel/manifest.json" ] || \
	  { echo "lake-init: no release at pointers/trino (make deploy points it at one)" >&2; exit 2; }; \
	  id=$${rel##*/}; pin=$$(mktemp) && trap 'rm -f "$$pin"' EXIT && \
	  python3 -c 'import json, sys; m = json.load(open(sys.argv[1]))["images"]; print(json.dumps({"services": {s: {"image": m[s]} for s in sys.argv[2:] if s in m}}))' \
	    "$$rel/manifest.json" lake-init $$RECREATE > "$$pin" && \
	  export K2_LAKE_INIT_RELEASE="$$id" K2_STORAGE_PLAN="/k2/releases/$$id/compiled/lake/storage-plan.json" && \
	  set -- docker compose $${K2_COMPOSE_PROJECT:+-p "$$K2_COMPOSE_PROJECT"} --project-directory "$(CURDIR)" \
	    -f "$$rel/compose/docker-compose.yml" -f "$$pin" up -d --no-deps --no-build --pull never --force-recreate && \
	  "$$@" lake-init && code=$$(docker wait k2-lake-init) && echo "lake-init: release $$id, exit $$code" && \
	  [ "$$code" = 0 ] && { [ -z "$$RECREATE" ] || "$$@" $$RECREATE; }

.PHONY: backup-verify
backup-verify:  ## The newest backup run against its manifest: each table's snapshot id and counts, each landing; every file's size and the dumps' and releases tar's sha256 re-checked; fails on a mismatch or a restore table of the live backup set it lacks (scripts/backup_lake.py)
	@bash scripts/backup-lake.sh --verify

.PHONY: test-connectors
test-connectors:  ## The fetch container's address guard and run contract (docker/fetch/fetch.py), with no network: every lookup and socket in it is faked
	uv run --no-project --with pytest==9.2.0 pytest tests/test_fetch.py -q

.PHONY: test-api
test-api:  ## k2-api's tests and ruff from its own lock (services/k2-api/), with no network and no Trino: a fake stands at the one Trino client
	cd services/k2-api && uv run --locked -q pytest -q && uv run --locked -q ruff check .

.PHONY: test-platform
test-platform:  ## The run-records client, the catalog site generator and the dbt wrapper (platform/runs, platform/catalog-site, docker/dbt-runner): fakes only, no stack
	uv run --no-project --with pytest==9.2.0 --with trino==0.340.0 pytest platform/runs platform/catalog-site -q
	uv run --project platform/k2gen --locked -q --with pytest==9.2.0 --with trino==0.340.0 pytest docker/dbt-runner -q
	uv run --no-project --with pytest==9.2.0 --with pyyaml==6.0.4 pytest domains -q

.PHONY: site
site:  ## The live release's catalog site (its k2site.py --strict, in tools as svc.catalog) and dbt docs (k2-dbt docs, in its dbt-runner image) into the k2-site volume k2-api serves; deploy runs both after each deploy and rollback (scripts/deploy.py)
	@$(DEPLOY) site

# The owner runs this, once, from the main checkout: the unit runs `make site` in the directory it
# was installed from, with that checkout's .env loaded as for make deploy (W3-12).
.PHONY: site-install
site-install:  ## Schedule `make site` daily at 05:15 UTC: a systemd user timer in ~/.config/systemd/user, enabled
	mkdir -p $(HOME)/.config/systemd/user
	sed 's|@K2_ROOT@|$(CURDIR)|g' scripts/systemd/k2-site.service > $(HOME)/.config/systemd/user/k2-site.service
	cp scripts/systemd/k2-site.timer $(HOME)/.config/systemd/user/k2-site.timer
	systemctl --user daemon-reload
	systemctl --user enable --now k2-site.timer
	@echo "Scheduled daily at 05:15 UTC. Deploy regenerates the site too; 'make site' runs it now."

.PHONY: hold unhold
hold:  ## PRODUCT=<domain/name> REASON="<text, at most 200 chars>" Hold a product of the live release: the hold row first, then its DAG paused; no deploy unpauses it, its resolve task refuses runs (scripts/hold.py)
	@uv run --no-project --with trino==0.340.0 python3 scripts/hold.py hold "$(PRODUCT)" "$(REASON)"
unhold:  ## PRODUCT=<domain/name> End the operator hold: the release row, then the DAG unpaused unless a rebuild or restore hold is still open (scripts/hold.py)
	@uv run --no-project --with trino==0.340.0 python3 scripts/hold.py unhold "$(PRODUCT)"
