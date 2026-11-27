# Owned by phase 001 step S17: the dbt tree a release compiles (platform/k2gen/k2gen/emit/dbt.py).
# Only that step edits this file (docs/development/phase-001-interfaces.md); no file under mk/
# redefines a target of the root Makefile (tests/test_mk_split.py).

K2_DBT_DEV ?= $(HOME)/k2-dbt-dev

.PHONY: dbt-dev
dbt-dev:  ## Compile the working tree's products into the dbt project a release would hold, at $K2_DBT_DEV/compiled/dbt/ (default ~/k2-dbt-dev)
	cd platform/k2gen && uv run --locked -q python -m k2gen.emit.dbt "$(CURDIR)" "$(K2_DBT_DEV)"

.PHONY: test-dbt-tree
test-dbt-tree:  ## The compiled dbt tree of the k2gen fixture set and of contracts/: dbt parse, compile and ls --selector with dbt/'s pins; no stack, no Trino
	cd dbt && uv sync --locked -q
	cd platform/k2gen && K2_DBT_PROOF=1 uv run --locked -q pytest -q -rs tests/test_emit_dbt.py
