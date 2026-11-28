# Owned by phase 004 step W1-15b (docs/research/2026-11-28-phases-004-005-build-breakdown.md): make
# rebuild, its own file so that no parallel step edits it. No file under mk/ redefines a target of the
# root Makefile (tests/test_mk_split.py).

.PHONY: rebuild
rebuild:  ## PRODUCT=<domain/name> [UNLOCK=1] Rebuild a product of the live release under a rebuild hold, as svc.operator from the host: its DAG paused and drained, a Kafka product's bronze restaged from its landing, then k2-dbt build --full-refresh; the hold released, and the DAG unpaused, only where this run placed them. UNLOCK=1 ends the lock a killed rebuild left (scripts/rebuild.py)
	@uv run --no-project --with trino==0.340.0 python3 scripts/rebuild.py "$(PRODUCT)" $(if $(UNLOCK),--unlock)
