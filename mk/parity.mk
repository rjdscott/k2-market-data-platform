# Owned by phases 004 and 005, steps W0-11 and W1-18 (docs/research/2026-11-28-phases-004-005-build-breakdown.md).
# parity-ohlcv's SKIP stub left the root Makefile in the same change (tests/test_mk_split.py).
#
# make parity-silver and make parity-ohlcv run scripts/parity_silver.py and scripts/parity_ohlcv.py in a
# tools one-shot of the live release (pointers/trino), as make product-verify runs its script: the
# release's compose file, the tools image pinned to the manifest's (DuckDB and its extensions baked in,
# docker/tools/Dockerfile), this checkout's scripts/ and docker/lake/ mounted read-only. The one-shot is
# on the catalog's network (Lakekeeper has no host port) and on k2-net (MinIO, the registry). It holds
# the tools MinIO key alone, passed by -e name from your environment for that run: run from the live
# checkout with the stack's .env loaded (`set -a && . ./.env && set +a`), as the backup runbook runs
# everything; nothing here reads .env. Iceberg's files are read straight, never through Trino, and
# nothing is written. The pin is a build's run record: RUN=<dag_id>/<run_id> compares that build's
# snapshots, by default the newest audited build. K2_HOME and K2_COMPOSE_PROJECT as for make
# product-verify. A container job: one slot.

PARITY_KEY := K2_TOOLS_S3_ACCESS_KEY K2_TOOLS_S3_SECRET_KEY

# $(call parity_run,<target>,<script and its arguments>)
define parity_run
for n in $(PARITY_KEY); do [ -n "$$(printenv $$n)" ] || \
  { echo "$(1): $$n is not set: load the stack's .env first (set -a && . ./.env && set +a)" >&2; exit 2; }; done; \
rel=$$(readlink -f "$${K2_RELEASES:-$$HOME/k2-releases}/pointers/trino") && [ -f "$$rel/manifest.json" ] || \
  { echo "$(1): no release at pointers/trino (make deploy points it at one)" >&2; exit 2; }; \
id=$${rel##*/}; pin=$$(mktemp) && trap 'rm -f "$$pin"' EXIT && \
python3 -c 'import json, sys; print(json.dumps({"services": {"tools": {"image": json.load(open(sys.argv[1]))["images"]["tools"]}}}))' \
  "$$rel/manifest.json" > "$$pin" && \
docker compose $${K2_COMPOSE_PROJECT:+-p "$$K2_COMPOSE_PROJECT"} --project-directory "$${K2_HOME:-$(CURDIR)}" \
  -f "$$rel/compose/docker-compose.yml" -f "$$pin" run --rm --no-deps --pull never --user "$$(id -u):$$(id -g)" \
  $(foreach n,$(PARITY_KEY),-e $(n)) \
  -v "$(CURDIR)/scripts:/opt/k2/scripts:ro" -v "$(CURDIR)/docker/lake:/opt/k2/docker/lake:ro" \
  tools python3 /opt/k2/scripts/$(2) --release "/k2/releases/$$id" $${RUN:+--run "$$RUN"}
endef

# The targets, each one tools one-shot. This comment also ends what tests/test_mk_split.py reads as a rule from endef.
.PHONY: parity-silver parity-ohlcv
parity-silver:  ## PRODUCT=<domain/name> [RUN=<dag_id>/<run_id>] DuckDB recomputes the product's silver_trade from its landing (Avro, JSON and every silver rule read here, not from dbt) at the snapshots one build read, pinned by its run record, and compares keys, counts and every column with the silver it published, each difference by class; in a tools one-shot with the tools key alone, the stack's .env loaded (scripts/parity_silver.py)
	@[ -n "$(PRODUCT)" ] || { echo "parity-silver: PRODUCT=<domain/name>" >&2; exit 2; }; \
	  $(call parity_run,parity-silver,parity_silver.py --product "$(PRODUCT)")
parity-ohlcv:  ## [RUN=<dag_id>/<run_id>] DuckDB recomputes candles at 1m, 5m, 1h and 1d from canonical-trades at the snapshot one candles build read, pinned by its run records, and compares every window and value with the tables it published, each difference by class; as parity-silver runs (scripts/parity_ohlcv.py)
	@$(call parity_run,parity-ohlcv,parity_ohlcv.py)
