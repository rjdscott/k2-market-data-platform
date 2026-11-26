# Owned by phase 001 step S19: deploy, verify and roll back releases, ADR-058 (scripts/deploy.py, docker/tools/).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: deploy, release-verify. Until S19 lands each is a
# stub that says so on stderr and exits 2, so nothing mistakes it for a pass; no file
# under mk/ redefines a target of the root Makefile (tests/test_mk_split.py).

.PHONY: deploy
deploy:  ## NOT BUILT until step S19: RELEASE=<tag>: compile, preflight, move the pointers, verify; on a mismatch back to R-1, exit 1
	@echo "NOT BUILT deploy: phase 001 step S19 builds it" >&2; exit 2

.PHONY: release-verify
release-verify:  ## NOT BUILT until step S19: ask every part which release it runs, then the catalog drift check
	@echo "NOT BUILT release-verify: phase 001 step S19 builds it" >&2; exit 2
