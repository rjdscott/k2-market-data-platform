# Owned by phase 001 step S19: deploy, verify and roll back releases, ADR-058 (scripts/deploy.py, docker/tools/).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: deploy, release-verify. Until S19 lands each is a
# stub that says so and exits 0; a stub never shadows a target the root Makefile defines.

.PHONY: deploy
deploy:  ## SKIP until step S19: RELEASE=<tag>: compile, preflight, move the pointers, verify; on a mismatch back to R-1, exit 1
	@echo "SKIP deploy: built in phase 001 step S19"

.PHONY: release-verify
release-verify:  ## SKIP until step S19: ask every part which release it runs, then the catalog drift check
	@echo "SKIP release-verify: built in phase 001 step S19"
