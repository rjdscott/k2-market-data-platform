# Owned by phase 002's run path (docs/research/2026-11-27-phases-002-003-build-breakdown.md):
# W0-5 adds test-connectors, W1-3 engine-keys, W1-22 backup-verify. No file under mk/ redefines a target of the
# root Makefile (tests/test_mk_split.py).

.PHONY: engine-keys
engine-keys:  ## [ENV_FILE=.env] [ROTATE=<engine>] Each engine's MinIO key pair and K2_RUN_UID/GID into the env file, only where missing or still the .env.example placeholder; prints the names, never a value (scripts/engine-keys.py)
	@python3 scripts/engine-keys.py --env-file "$${ENV_FILE:-.env}" $${ROTATE:+--rotate "$$ROTATE"}

.PHONY: backup-verify
backup-verify:  ## The newest backup run against its manifest: each table's snapshot id and counts, each landing; every file's size and the dumps' and releases tar's sha256 re-checked; fails on a mismatch or a restore table of the live backup set it lacks (scripts/backup_lake.py)
	@bash scripts/backup-lake.sh --verify

.PHONY: test-connectors
test-connectors:  ## The fetch container's address guard and run contract (docker/fetch/fetch.py), with no network: every lookup and socket in it is faked
	uv run --no-project --with pytest==9.2.0 pytest tests/test_fetch.py -q
