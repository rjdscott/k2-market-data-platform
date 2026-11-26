#!/usr/bin/env bash
# dbt/ with no stack and no Trino. The project has no model today: the old marts retired
# to legacy/v3-lake-ingest/ (ADR-056), and phase 002 lands the first new one. So:
#
#   1. lint: sqlfluff over the models, no warehouse needed (dbt/.sqlfluff).
#   2. dbt deps, parse and compile --target trino, then the manifest check
#      (scripts/dbt_manifest_check.py): layering, places, every mart's grain test,
#      against the marts the contracts declare. With no model and no mart, this is
#      the zero-marts project k2gen writes (sources: [], selectors: []) still parsing.
#      Nothing listens on the profile's host and port here, so a step that tried to
#      reach a warehouse would fail rather than touch the stack.
#   3. dbt build --selector <domain> with its unit tests, on a throwaway Trino: SKIP,
#      with a line saying so, until phase 002 brings a model to build.
#
#   make test-dbt              # locally
#   CI job "dbt (marts)" runs the same script.
set -euo pipefail
cd "$(dirname "$0")/../dbt"

uv sync --locked -q
uv run --locked sqlfluff lint models
echo "ok lint: sqlfluff over $(find models -name '*.sql' | wc -l) model file(s), dbt/.sqlfluff"

export K2_TRINO_HOST=127.0.0.1 K2_TRINO_PORT=${K2_TRINO_TEST_PORT:-18089}
uv run --locked dbt deps -q
uv run --locked dbt parse -q
uv run --locked dbt compile -q --target trino
uv run --locked python ../scripts/dbt_manifest_check.py target/manifest.json
echo "ok parse: dbt deps, parse and compile, and the manifest check"

echo "SKIP dbt build with unit tests: no model to build until phase 002 lands the first (ADR-056)"
echo "dbt-test: PASS"
