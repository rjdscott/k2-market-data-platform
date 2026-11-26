# Owned by phase 001 step S14: principals, grants and keys (contracts/access/, scripts/principal-secret.py).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# The procedure around these targets: docs/runbooks/create-or-rotate-a-principal.md. No secret
# is ever on a command line: the script makes it. PRINCIPAL, ROTATE and TO_FILE, given on make's
# command line, reach the recipe as environment variables ($$PRINCIPAL), never spliced into it.

PRINCIPAL_SECRET = uv run --project platform/k2gen --locked -q --with bcrypt==5.0.0 python scripts/principal-secret.py

.PHONY: password
password:  ## PRINCIPAL=<id> [ROTATE=1] [TO_FILE=1]: a 32-byte password printed once, or with TO_FILE=1 a service's ${K2_SECRETS}/principals/<id>.password; its bcrypt line goes to ${K2_SECRETS}/trino/password.db, never the repository
	@$(PRINCIPAL_SECRET) password --principal "$$PRINCIPAL" \
	  $$(test "$$ROTATE" = 1 && echo --rotate) $$(test "$$TO_FILE" = 1 && echo --to-file)

.PHONY: api-key
api-key:  ## PRINCIPAL=<id> [ROTATE=1]: a k2_ key printed once; its sha256 goes to ${K2_SECRETS}/api/keys.json, never the repository
	@$(PRINCIPAL_SECRET) api-key --principal "$$PRINCIPAL" $$(test "$$ROTATE" = 1 && echo --rotate)

.PHONY: principals-status
principals-status:  ## Which known principals have a password line, a password file and an API key under ${K2_SECRETS}: names only
	@$(PRINCIPAL_SECRET) status
