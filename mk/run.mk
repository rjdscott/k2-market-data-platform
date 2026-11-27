# Owned by phase 002's run path (docs/research/2026-11-27-phases-002-003-build-breakdown.md):
# W0-5 adds test-connectors, W1-22 backup-verify. No file under mk/ redefines a target of the
# root Makefile (tests/test_mk_split.py).

.PHONY: test-connectors
test-connectors:  ## The fetch container's address guard and run contract (docker/fetch/fetch.py), with no network: every lookup and socket in it is faked
	uv run --no-project --with pytest==9.2.0 pytest tests/test_fetch.py -q
