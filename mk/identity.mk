# Owned by phase 001 step S13: identity: Trino over HTTPS with password login, ADR-060 (scripts/tls.sh).
# Only that step edits this file (docs/development/phase-001-interfaces.md); no file under mk/
# redefines a target of the root Makefile (tests/test_mk_split.py).
# The procedure around these targets: docs/runbooks/tls-and-passwords.md. FORCE and MIN_DAYS,
# given on make's command line, reach the recipe as environment variables, never spliced into it.

.PHONY: tls-init
tls-init:  ## [FORCE=1]: a local CA and Trino's server certificate under ${K2_SECRETS}/tls/, never the repository; a rerun keeps both, FORCE=1 replaces the CA
	@bash scripts/tls.sh init $$(test "$$FORCE" = 1 && echo --force)

.PHONY: tls
tls: tls-init  ## The name model.SECRET_FILES gives tls-init

.PHONY: tls-rotate
tls-rotate:  ## Reissue Trino's server certificate in place under the same CA; Trino picks it up without a restart
	@bash scripts/tls.sh rotate

.PHONY: tls-status
tls-status:  ## [MIN_DAYS=30]: subject, names, expiry and days left of the CA and the server certificate, never a key; exit 1 under MIN_DAYS or on any fault
	@bash scripts/tls.sh status
