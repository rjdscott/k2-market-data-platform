# Owned by phase 001 step S20: access-verify, the tested-data marker and the catalog drift check (tests/access/).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: access-verify. Until S20 lands each is a
# stub that says so on stderr and exits 2, so nothing mistakes it for a pass; no file
# under mk/ redefines a target of the root Makefile (tests/test_mk_split.py).

.PHONY: access-verify
access-verify:  ## NOT BUILT until step S20: every tests/access/expectations.yaml row over SQL on a throwaway Trino, then live
	@echo "NOT BUILT access-verify: phase 001 step S20 builds it" >&2; exit 2
