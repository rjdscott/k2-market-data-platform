# Owned by phase 001 step S20: access-verify, the tested-data marker and the catalog drift check (tests/access/).
# Only that step edits this file (docs/development/phase-001-interfaces.md).
# The procedure around these targets: docs/runbooks/verify-access-after-a-release.md. RELEASE,
# LIVE, OUT and PREFIX, given on make's command line, reach the recipes as environment variables.
#
#   make access-verify RELEASE=<release dir>   release time: a throwaway lake started from that
#                                              release (every principal, generated passwords), the
#                                              release's products staged, every row run, the lake removed
#   make access-verify LIVE=1                  the running stack: the canary's and the platform's rows
#                                              (RELEASE defaults to ${K2_RELEASES}/pointers/trino), in a
#                                              tools one-shot on the stack's networks, each engine's
#                                              storage pair by -e name from your environment
#                                              (scripts/deploy.py access_live); refused while .env is
#                                              open to anyone but its owner
#   make test-access RELEASE=<release dir>     the proofs behind the suite on a throwaway lake
#                                              (tests/access/prove.sh)
#
# Exit codes, for scripts/deploy.py (S19): 0 every row passed or was skipped with a reason; 1 a row
# failed; 2 a usage error, or Trino or the lake could not be reached. The JSON result goes to OUT,
# default <releases root>/verify/<release id>.access-<scope>.json beside the release.

ACCESS_LAKE := scripts/lib/throwaway-lake.sh
ACCESS_PY := uv run --project platform/k2gen --locked -q python

.PHONY: access-verify
access-verify:  ## RELEASE=<release dir>: every tests/access/expectations.yaml row on a throwaway lake run from it; LIVE=1: the canary and platform rows on the running stack
	@set -eu; \
	if [ "$${LIVE:-}" = 1 ]; then \
	  python3 scripts/check-env.py --mode-only >/dev/null || exit 2; \
	  rel=$${RELEASE:-$${K2_RELEASES:-$$HOME/k2-releases}/pointers/trino}; \
	  [ -f "$$rel/manifest.json" ] || { echo "access-verify: no release at $$rel (deploy points pointers/trino at one)" >&2; exit 2; }; \
	  rel=$$(realpath "$$rel"); \
	  out=$${OUT:-$$(dirname "$$rel")/verify/$$(basename "$$rel").access-live.json}; \
	  $(DEPLOY) access-live "$$(basename "$$rel")" --out "$$out"; \
	else \
	  [ -n "$${RELEASE:-}" ] || { echo "access-verify: RELEASE=<release dir>, or LIVE=1" >&2; exit 2; }; \
	  [ -f "$$RELEASE/manifest.json" ] || { echo "access-verify: $$RELEASE is not a compiled release" >&2; exit 2; }; \
	  rel=$$(realpath "$$RELEASE"); id=$$(basename "$$rel"); p=$${PREFIX:-access-verify}; \
	  out=$${OUT:-$$(dirname "$$rel")/verify/$$id.access-release.json}; \
	  trap 'bash $(ACCESS_LAKE) down "$$p" >/dev/null' EXIT; \
	  bash $(ACCESS_LAKE) up "$$p" "$$rel"; \
	  bash $(ACCESS_LAKE) py "$$p" scripts/access_verify.py stage --release "/work/releases/$$id" --env /work/env.json; \
	  code=0; bash $(ACCESS_LAKE) py "$$p" scripts/access_verify.py run --scope release --release "/work/releases/$$id" \
	    --env /work/env.json --out /work/access-verify.json || code=$$?; \
	  mkdir -p "$$(dirname "$$out")"; cp "$$(bash $(ACCESS_LAKE) state "$$p")/access-verify.json" "$$out"; \
	  echo "access-verify: result $$out"; exit $$code; \
	fi

.PHONY: test-access
test-access:  ## RELEASE=<release dir>: on a throwaway lake, access-verify passes, a loosened rule fails the right row, the marker and drift checks catch their plants, grants refresh live, resource groups hold
	@[ -n "$${RELEASE:-}" ] || { echo "test-access: RELEASE=<release dir>" >&2; exit 2; }; \
	bash tests/access/prove.sh "$$RELEASE"
