# Owned by phase 001 step S20: access-verify, the tested-data marker and the catalog drift check (tests/access/).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: access-verify. Until S20 lands each is a
# stub that says so and exits 0; a stub never shadows a target the root Makefile defines.

.PHONY: access-verify
access-verify:  ## SKIP until step S20: every tests/access/expectations.yaml row over SQL on a throwaway Trino, then live
	@echo "SKIP access-verify: built in phase 001 step S20"
