# Owned by phase 004 steps W0-10 and W1-16: the restore of what `make backup` copied (scripts/restore.py).
# Run from the live checkout with the stack's .env loaded (`set -a && . ./.env && set +a`), as the
# backup runbook runs everything: the restore key, MinIO's root pair (the drill's damage alone) and
# the directories are read from that environment by name, never printed. The proof on a throwaway
# lake is `bash scripts/verify/restore-path.sh <release dir>`.

.PHONY: restore
restore:  ## [RUN=<stamp>] The lake back from the newest complete backup run, step by step from the live backup set: every DAG paused and every product held (restore, as svc.operator from the host) before a table moves; restore tables put back with the restore key (never the root pair, never overwriting) and registered; landings back; a rebuild table whose files are gone recreated empty for its rebuild; the catalog database replayed from its dump only when lost; released once catalog_drift --runs holds each audited tag to its run records. Refuses a run older than the newest or incomplete, and a lake whose tables are not the backup's. Same host only: the dump needs the LAKEKEEPER_ENCRYPTION_KEY Lakekeeper already holds; a restore after host loss is NOT proven (scripts/restore.py)
	@uv run --no-project --with trino==0.340.0 python3 scripts/restore.py run $${RUN:+--run "$$RUN"}

.PHONY: restore-drill
restore-drill:  ## CONFIRM=<live release id> Empty the lake (the catalog database, warehouses k2 and governance, k2-landing; MinIO's root pair from .env does the damage), then `make restore` from the newest backup run; prints the restore's time and the whole drill's. `make backup` first. Host loss stays unproven (scripts/restore.py)
	@CONFIRM="$(CONFIRM)" uv run --no-project --with trino==0.340.0 python3 scripts/restore.py drill
