# Owned by phase 001 step S13: identity: Trino over HTTPS with password login, ADR-060 (scripts/tls.sh).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: tls. Until S13 lands each is a
# stub that says so on stderr and exits 2, so nothing mistakes it for a pass; no file
# under mk/ redefines a target of the root Makefile (tests/test_mk_split.py).

.PHONY: tls
tls:  ## NOT BUILT until step S13: write a local CA and a Trino server cert to ${K2_SECRETS}/tls/, never the repository
	@echo "NOT BUILT tls: phase 001 step S13 builds it" >&2; exit 2
