# Owned by phases 004 and 005, step W1-14 (docs/research/2026-11-28-phases-004-005-build-breakdown.md).
#
# make product-verify runs scripts/product_verify.py in a tools one-shot of the live release
# (pointers/trino), as scripts/deploy.py runs its catalog drift check: the release's compose file,
# the tools image pinned to the manifest's, this checkout's scripts/ and docker/lake/ mounted
# read-only. The one-shot is on the catalog's network (Lakekeeper has no host port) and reads Trino
# as svc.catalog with the password and CA the tools service mounts; nothing else is given it.
# K2_HOME is the compose project directory, the checkout whose .env the stack runs with (default
# this one), and K2_COMPOSE_PROJECT the project, as for make deploy. A container job: one slot.

.PHONY: product-verify
product-verify:  ## PRODUCT=<domain/name | domain.name | name> [WAIT=<s>] A Kafka product of the live release, read-only: every offset accounted for (landing spans whole and abutting, gaps recorded; bronze staged plus skipped equal to each span) and every layer level (bronze with the landing, each dbt output's tag with its run record), from summaries and refs, in a tools one-shot as svc.catalog (scripts/product_verify.py); K2_HOME=<checkout with the stack's .env>
	@[ -n "$(PRODUCT)" ] || { echo "product-verify: PRODUCT=<domain/name>" >&2; exit 2; }; \
	  rel=$$(readlink -f "$${K2_RELEASES:-$$HOME/k2-releases}/pointers/trino") && [ -f "$$rel/manifest.json" ] || \
	  { echo "product-verify: no release at pointers/trino (make deploy points it at one)" >&2; exit 2; }; \
	  id=$${rel##*/}; pin=$$(mktemp) && trap 'rm -f "$$pin"' EXIT && \
	  python3 -c 'import json, sys; print(json.dumps({"services": {"tools": {"image": json.load(open(sys.argv[1]))["images"]["tools"]}}}))' \
	    "$$rel/manifest.json" > "$$pin" && \
	  docker compose $${K2_COMPOSE_PROJECT:+-p "$$K2_COMPOSE_PROJECT"} --project-directory "$${K2_HOME:-$(CURDIR)}" \
	    -f "$$rel/compose/docker-compose.yml" -f "$$pin" run --rm --no-deps --pull never --user "$$(id -u):$$(id -g)" \
	    -v "$(CURDIR)/scripts:/opt/k2/scripts:ro" -v "$(CURDIR)/docker/lake:/opt/k2/docker/lake:ro" \
	    tools python3 /opt/k2/scripts/product_verify.py --release "/k2/releases/$$id" --product "$(PRODUCT)" \
	    $${WAIT:+--wait "$$WAIT"}
