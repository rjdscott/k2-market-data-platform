# Owned by phase 001 step S14: principals, grants and keys (contracts/access/, scripts/principal-secret.py).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: password, api-key. Until S14 lands each is a
# stub that says so and exits 0; a stub never shadows a target the root Makefile defines.

.PHONY: password
password:  ## SKIP until step S14: PRINCIPAL=<id>: print a 32-byte password once; only its bcrypt goes into principals.yaml
	@echo "SKIP password: built in phase 001 step S14"

.PHONY: api-key
api-key:  ## SKIP until step S14: PRINCIPAL=<id>: print a k2_ key once; only its sha256 goes into principals.yaml
	@echo "SKIP api-key: built in phase 001 step S14"
