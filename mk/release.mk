# Owned by phase 001 step S19: compile, deploy, verify and roll back releases, ADR-058 (scripts/deploy.py, docker/tools/).
# Only that step edits this file (docs/development/phase-001-interfaces.md); no file under mk/
# redefines a target of the root Makefile (tests/test_mk_split.py).
# The procedures: docs/runbooks/deploy-a-release.md, docs/runbooks/roll-back-a-release.md.
# TAG, RELEASE, FROM, IMAGES, PLAN and ALLOW_STATEFUL, given on make's command line, reach the
# recipe as environment variables ($$RELEASE), never spliced into it. The script runs in
# platform/k2gen's locked environment: it imports k2gen's own needs and view rule, and bcrypt
# (the pin mk/principals.mk uses) for the release canary's password line.

DEPLOY = uv run --project platform/k2gen --locked -q --with bcrypt==5.0.0 python scripts/deploy.py

.PHONY: release
release:  ## TAG=<annotated tag> [IMAGES=<images.json>]: compile the tag into a sealed release under ${K2_RELEASES}, its images built or pinned by digest; prints the release id
	@$(DEPLOY) release --tag "$$TAG" $${IMAGES:+--images "$$IMAGES"}

.PHONY: deploy
deploy:  ## RELEASE=<release id or tag> [PLAN=1] [ALLOW_STATEFUL=<svc>,...]: preflight, images, plan, DDL, views, canary, pointer swap, recreate only what changed, verify; on a failure back to the previous release, exit 1
	@$(DEPLOY) deploy "$$RELEASE" $$(test "$$PLAN" = 1 && echo --plan) $${ALLOW_STATEFUL:+--allow-stateful "$$ALLOW_STATEFUL"}

.PHONY: release-verify
release-verify:  ## Ask every part which release it runs: pointers in the VM and each container, files, the canary, image digests, health, then the access suite and the catalog drift check (S20)
	@$(DEPLOY) verify

.PHONY: rollback
rollback:  ## Back to the previous good release (or to the good one, after an unfinished deploy), images included, then verify
	@$(DEPLOY) rollback

.PHONY: release-status
release-status:  ## The good release, the pointers, the canary lines (names only), the releases kept and the last deploy records
	@$(DEPLOY) status

.PHONY: release-diff
release-diff:  ## RELEASE=<id> [FROM=<id>]: k2gen diff from FROM (default: the good release) to RELEASE, as Markdown
	@$(DEPLOY) diff "$$RELEASE" $${FROM:+--from "$$FROM"}

.PHONY: deploy-rehearsal
deploy-rehearsal:  ## [KEEP=1] [CUTOVER=1] Every deploy claim on a throwaway compose project k2-rehearsal under your home directory, never the live stack: the release-time and live suites, a failing first deploy, rules-only, one image, the view shape crossing, forced mismatch, rollback, a dropped masked column polled, a kill after each step, two at once, M4 (about 25 min); CUTOVER=1: the one-time cutover from today's shape, and back
	@uv run --project platform/k2gen --locked -q --with bcrypt==5.0.0 python scripts/verify/deploy_rehearsal.py
