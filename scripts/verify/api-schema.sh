#!/usr/bin/env bash
# W3-8 (phase 003): every catalog document k2-api serves validates against the schema frozen in phase
# 001 (contracts/schema/catalog-product.schema.json), and /v1/openapi.json declares OpenAPI 3.1.0:
#
#   bash scripts/verify/api-schema.sh [<base url>]       a running k2-api, default http://127.0.0.1:8095
#   bash scripts/verify/api-schema.sh --release <dir>    a release's compiled/catalog/, with no k2-api
#
# One PASS or FAIL line per document, then `<n> PASS, <m> FAIL`; exit 1 on any FAIL
# (scripts/verify/api_schema.py). No key is sent: `access` belongs to a keyed caller's answer only.
set -euo pipefail
if [ "${1:-}" = --release ] && [ -n "${2:-}" ]; then set -- --release "$(cd "$2" && pwd)"; fi  # before the cd
cd "$(dirname "$0")/../.."
exec uv run --no-project -q --with jsonschema==4.26.0 --with referencing==0.37.0 \
  python scripts/verify/api_schema.py "$@"
