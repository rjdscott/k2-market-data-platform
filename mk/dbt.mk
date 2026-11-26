# Owned by phase 001 step S17: the dbt tree a release compiles (k2gen emit/dbt.py).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: dbt-dev. Until S17 lands each is a
# stub that says so and exits 0; a stub never shadows a target the root Makefile defines.

.PHONY: dbt-dev
dbt-dev:  ## SKIP until step S17: symlink a compiled dbt tree for local work on product models
	@echo "SKIP dbt-dev: built in phase 001 step S17"
