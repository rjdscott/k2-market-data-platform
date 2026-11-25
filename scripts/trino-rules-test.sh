#!/usr/bin/env bash
# docker/trino/etc on a throwaway Trino, the image tag docker-compose.yml uses,
# with no network and no stack:
#
#   1. the real etc/ starts and loads catalog `lake` (Trino refuses to start on
#      an unknown or misspelt property, so this checks every file in etc/);
#   2. rules.json, with `lake` swapped for the memory connector: a smoke query,
#      `marts` read-write, every other schema read-only.
#
#   make test-trino            # locally
#   CI job "Trino (access rules)" runs the same script.
#
# What a memory catalog cannot show: DML against an existing table outside
# `marts` (no such table can be created here, which is the point) and
# ALTER TABLE ... EXECUTE, which the memory connector does not have. The
# live-stack commands for both are in docker/trino/README.md.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=$(grep -oE 'trinodb/trino:[^ ]+' docker-compose.yml | head -1)
NAME=k2-trino-rules-test

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

sql() { docker exec "$NAME" trino --execute "$1" 2>&1; }

# start <docker run args...>: a fresh container, waited on for up to 120 s.
start() {
  cleanup
  docker run -d --name "$NAME" --network none --cpus 2 --memory 4g "$@" "$IMAGE" >/dev/null
  for _ in $(seq 1 60); do
    sql 'SELECT 1' >/dev/null && return 0
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || break
    sleep 2
  done
  echo "FAIL: Trino did not start"; docker logs "$NAME" 2>&1 | tail -40; exit 1
}

# 1. The real configuration. Placeholder keys: nothing is reached.
start -e MINIO_ROOT_USER=unused -e MINIO_ROOT_PASSWORD=unused -v "$PWD/docker/trino/etc":/etc/trino:ro
sql 'SHOW CATALOGS' | grep -qx '"lake"' || { echo "FAIL: catalog lake did not load"; exit 1; }
echo "ok config: etc/ starts, catalog lake loads"

# 2. The rules, on a memory catalog named lake.
start -v "$PWD/docker/trino/etc":/etc/trino:ro \
  -v "$PWD/docker/trino/memory-lake.properties":/etc/trino/catalog/lake.properties:ro

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

allow 'SELECT count(*) FROM system.runtime.nodes'
allow 'SHOW SCHEMAS FROM lake'
allow 'CREATE SCHEMA lake.marts'
allow 'CREATE TABLE lake.marts.t AS SELECT 1 AS x'
allow 'INSERT INTO lake.marts.t VALUES 2'
allow 'CREATE VIEW lake.marts.v AS SELECT x FROM lake.marts.t'
if [ "$(sql 'SELECT count(*) FROM lake.marts.v')" = '"2"' ]; then echo "ok read back: 2 rows through the view"
else echo "FAIL: view read back"; fail=1; fi

# `gold` stands for every schema Spark writes; `default` is the one that exists
# in a memory catalog.
deny 'CREATE SCHEMA lake.gold'
deny 'CREATE TABLE lake.gold.t (x integer)'
deny 'CREATE TABLE lake.default.t AS SELECT 1 AS x'
deny 'CREATE VIEW lake.default.v AS SELECT 1 AS x'
deny 'ALTER TABLE lake.marts.t RENAME TO lake.default.t'
deny 'DROP SCHEMA lake.default'

allow 'DROP VIEW lake.marts.v'
allow 'DROP TABLE lake.marts.t'
allow 'DROP SCHEMA lake.marts'

[ "$fail" = 0 ] || { echo "trino-rules-test: FAIL ($IMAGE)"; exit 1; }
echo "trino-rules-test: PASS ($IMAGE)"
