# Owned by phase 001 step S17: the dbt tree a release compiles (k2gen emit/dbt.py).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: dbt-dev. Until S17 lands each is a
# stub that says so on stderr and exits 2, so nothing mistakes it for a pass; no file
# under mk/ redefines a target of the root Makefile (tests/test_mk_split.py).

.PHONY: dbt-dev
dbt-dev:  ## NOT BUILT until step S17: symlink a compiled dbt tree for local work on product models
	@echo "NOT BUILT dbt-dev: phase 001 step S17 builds it" >&2; exit 2
