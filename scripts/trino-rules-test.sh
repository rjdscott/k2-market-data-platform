#!/usr/bin/env bash
# docker/trino/etc on a throwaway Trino, the image tag docker-compose.yml uses,
# with no network and no stack. Three runs:
#
#   1. config: the real etc/ starts and loads catalogs `lake` and `marts`
#      (Trino refuses to start on an unknown or misspelt property), and
#      `lake` is read-only at the connector;
#   2. connector: `iceberg.security=read_only` denies every commit, table
#      procedures (ALTER TABLE ... EXECUTE) included, on a local Iceberg catalog
#      named `lake` whose writable twin `marts` shares its tables
#      (docker/trino/test/local);
#   3. rules: etc/rules.json on memory catalogs named `lake` and `marts`
#      (docker/trino/test/memory): writes denied in `lake`, allowed in `marts`.
#
#   make test-trino            # locally
#   CI job "Trino (access rules)" runs the same script.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=$(grep -oE 'trinodb/trino:[^ ]+' docker-compose.yml | head -1)
NAME=k2-trino-rules-test
ETC="$PWD/docker/trino/etc"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

sql() { docker exec "$NAME" trino --execute "$1" 2>&1; }

# start <docker run args...>: a fresh container, waited on for up to 120 s.
start() {
  cleanup
  docker run -d --name "$NAME" --network none --cpus 2 --memory 4g -v "$ETC":/etc/trino:ro "$@" "$IMAGE" >/dev/null
  for _ in $(seq 1 60); do
    sql 'SELECT 1' >/dev/null && return 0
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || break
    sleep 2
  done
  echo "FAIL: Trino did not start"; docker logs "$NAME" 2>&1 | tail -40; exit 1
}

fail=0
allow() {
  if out=$(sql "$1"); then echo "ok allowed: $1"; else echo "FAIL (denied or broken): $1"; echo "  $out"; fail=1; fi
}
deny() {
  out=$(sql "$1") || true
  case "$out" in
    *"Access Denied"*) echo "ok denied:  $1" ;;
    *) echo "FAIL (not denied): $1"; echo "  $out"; fail=1 ;;
  esac
}

# 1. The real configuration. Placeholder keys: nothing is reached.
start -e MINIO_ROOT_USER=unused -e MINIO_ROOT_PASSWORD=unused
catalogs=$(sql 'SHOW CATALOGS')
for c in lake marts; do
  grep -qx "\"$c\"" <<< "$catalogs" || { echo "FAIL: catalog $c did not load"; exit 1; }
done
grep -qx 'iceberg.security=read_only' "$ETC/catalog/lake.properties" \
  || { echo "FAIL: etc/catalog/lake.properties is not read_only at the connector"; exit 1; }
echo "ok config: etc/ starts, catalogs lake and marts load, lake is read_only"

# 2. The connector: one table, written through `marts`, refused through `lake`.
start -v "$PWD/docker/trino/test/local":/etc/trino/catalog:ro
allow 'CREATE SCHEMA marts.gold'
allow 'CREATE TABLE marts.gold.t AS SELECT 1 AS x'
allow 'INSERT INTO marts.gold.t VALUES 2'
allow 'SELECT count(*) FROM lake.gold.t'
snap=$(sql "SELECT snapshot_id FROM lake.gold.\"t\$refs\" WHERE name = 'main'" | tr -d '"')
deny  'ALTER TABLE lake.gold.t EXECUTE optimize'
deny  "ALTER TABLE lake.gold.t EXECUTE rollback_to_snapshot($snap)"
deny  "ALTER TABLE lake.gold.t EXECUTE expire_snapshots(retention_threshold => '7d')"
deny  "ALTER TABLE lake.gold.t EXECUTE remove_orphan_files(retention_threshold => '7d')"
deny  'INSERT INTO lake.gold.t VALUES 3'
deny  'DELETE FROM lake.gold.t'
deny  'DROP TABLE lake.gold.t'
# The control: the same procedure through the writable twin runs, so the
# denials above are the connector's and not a broken table.
allow 'ALTER TABLE marts.gold.t EXECUTE optimize'

# 3. The rules, on memory catalogs: `gold` stands for every schema Spark
# writes, `default` is the one that exists in a memory catalog.
start -v "$PWD/docker/trino/test/memory":/etc/trino/catalog:ro
allow 'SELECT count(*) FROM system.runtime.nodes'
allow 'SHOW SCHEMAS FROM lake'
deny  'CREATE SCHEMA lake.gold'
deny  'CREATE TABLE lake.default.t (x integer)'
deny  'CREATE TABLE lake.default.t AS SELECT 1 AS x'
deny  'CREATE VIEW lake.default.v AS SELECT 1 AS x'
deny  'DROP SCHEMA lake.default'
allow 'CREATE SCHEMA marts.s'
allow 'CREATE TABLE marts.s.t AS SELECT 1 AS x'
allow 'INSERT INTO marts.s.t VALUES 2'
allow 'CREATE VIEW marts.s.v AS SELECT x FROM marts.s.t'
allow 'CREATE VIEW marts.s.lake_schemas AS SELECT schema_name FROM lake.information_schema.schemata'
if [ "$(sql 'SELECT count(*) FROM marts.s.v')" = '"2"' ]; then echo "ok read back: 2 rows through the view"
else echo "FAIL: view read back"; fail=1; fi
deny  'CREATE TABLE lake.default.t2 AS SELECT * FROM marts.s.t'
allow 'DROP VIEW marts.s.lake_schemas'
allow 'DROP VIEW marts.s.v'
allow 'DROP TABLE marts.s.t'
allow 'DROP SCHEMA marts.s'

[ "$fail" = 0 ] || { echo "trino-rules-test: FAIL ($IMAGE)"; exit 1; }
echo "trino-rules-test: PASS ($IMAGE)"
