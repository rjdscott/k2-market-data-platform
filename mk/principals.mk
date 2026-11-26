# Owned by phase 001 step S14: principals, grants and keys (contracts/access/, scripts/principal-secret.py).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# Targets it will hold: password, api-key. Until S14 lands each is a
# stub that says so on stderr and exits 2, so nothing mistakes it for a pass; no file
# under mk/ redefines a target of the root Makefile (tests/test_mk_split.py).

.PHONY: password
password:  ## NOT BUILT until step S14: PRINCIPAL=<id>: print a 32-byte password once; its bcrypt line goes to ${K2_SECRETS}/trino/password.db and the password to ${K2_SECRETS}/principals/<id>.password, never the repository
	@echo "NOT BUILT password: phase 001 step S14 builds it" >&2; exit 2

.PHONY: api-key
api-key:  ## NOT BUILT until step S14: PRINCIPAL=<id>: print a k2_ key once; its sha256 goes to ${K2_SECRETS}/api/keys.json, never the repository
	@echo "NOT BUILT api-key: phase 001 step S14 builds it" >&2; exit 2
