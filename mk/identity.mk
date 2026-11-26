# Owned by phase 001 step S13: identity: Trino over HTTPS with password login, ADR-060 (scripts/tls.sh).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: tls. Until S13 lands each is a
# stub that says so and exits 0; a stub never shadows a target the root Makefile defines.

.PHONY: tls
tls:  ## SKIP until step S13: write a local CA and a Trino server cert to ${K2_SECRETS}/tls/, never the repository
	@echo "SKIP tls: built in phase 001 step S13"
