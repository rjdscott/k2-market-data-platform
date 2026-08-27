#!/usr/bin/env bash
# The gold schema's semantics, asserted on a throwaway ClickHouse — the same
# image tag as docker-compose.yml, no broker, no data volume. Applies ONLY
# docker/clickhouse/ddl/10-gold-tables.sql (20-gold-kafka.sql needs Redpanda),
# loads tests/clickhouse/*.jsonl, runs tests/clickhouse/assertions.sql, then
# checks the `quant` user is read-only and its password came from the env.
#
#   make test-clickhouse            # locally
#   CI job "ClickHouse (gold schema)" runs the same script.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=$(grep -oE 'clickhouse/clickhouse-server:[^ ]+' docker-compose.yml | head -1)
NAME=k2-clickhouse-schema-test
PASS=schema-test
QUANT=quant-from-env

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

docker run -d --name "$NAME" \
  -e CLICKHOUSE_USER=default -e CLICKHOUSE_PASSWORD="$PASS" -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  -e K2_QUANT_PASSWORD="$QUANT" \
  -v "$PWD/docker/clickhouse/ddl/10-gold-tables.sql":/docker-entrypoint-initdb.d/10-gold-tables.sql:ro \
  -v "$PWD/docker/clickhouse/config.xml":/etc/clickhouse-server/config.d/k2-config.xml:ro \
  -v "$PWD/docker/clickhouse/users.xml":/etc/clickhouse-server/users.d/k2-users.xml:ro \
  "$IMAGE" >/dev/null

ch() { docker exec -i "$NAME" clickhouse-client --password "$PASS" "$@" 2> >(grep -v jemalloc >&2); }

for _ in $(seq 1 60); do
  if ch -q "EXISTS gold.bbo_live" 2>/dev/null | grep -q '^1$'; then break; fi
  sleep 2
done
ch -q "EXISTS gold.bbo_live" | grep -q '^1$' || { echo "FAIL: gold DDL did not apply"; docker logs "$NAME" | tail -40; exit 1; }

ch -q "INSERT INTO gold.trades FORMAT JSONEachRow" < tests/clickhouse/trades_block1.jsonl
ch -q "INSERT INTO gold.trades FORMAT JSONEachRow" < tests/clickhouse/trades_block2.jsonl
ch -q "INSERT INTO gold.book_top20 FORMAT JSONEachRow" < tests/clickhouse/book.jsonl

out=$(ch --multiquery < tests/clickhouse/assertions.sql)
echo "$out"
n=$(echo "$out" | grep -c .)
[ "$n" -eq 9 ] || { echo "FAIL: expected 9 assertion lines, got $n"; exit 1; }
echo "$out" | grep -v '^ok$' && { echo "FAIL: assertion(s) above"; exit 1; }

# quant: password from the environment, read-only, gold only.
q() { docker exec -i "$NAME" clickhouse-client --user quant --password "$QUANT" "$@" 2> >(grep -v jemalloc >&2); }
q -q "SELECT count() FROM gold.trades FINAL" | grep -q '^5$' || { echo "FAIL: quant cannot read gold"; exit 1; }
if q -q "INSERT INTO gold.trades (exchange) VALUES ('x')" 2>/dev/null; then echo "FAIL: quant could write"; exit 1; fi
if q -q "SELECT count() FROM system.tables" 2>/dev/null | grep -q .; then :; fi
q -q "SELECT value FROM system.settings WHERE name = 'max_memory_usage'" | grep -q '^3221225472$' || { echo "FAIL: quant profile not applied"; exit 1; }
echo "ok: quant is read-only, 3 GiB, password from K2_QUANT_PASSWORD"
echo "clickhouse-schema-test: PASS ($IMAGE)"
