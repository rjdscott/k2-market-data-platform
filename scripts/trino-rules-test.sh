#!/usr/bin/env bash
# docker/trino/etc on a throwaway Trino, the image tag docker-compose.yml uses,
# with no network and no stack. Two starts:
#
#   1. config: the real etc/ starts (Trino refuses an unknown or misspelt
#      property); its catalogs are `lake`, `system` and one per domain in
#      contracts/domains.yaml; `lake` is read-only at the connector and is the
#      only catalog on warehouse k2; every domain catalog is on its own warehouse;
#      the startup log does not print the S3 access key.
#   2. fences: the same rules.json over local Iceberg catalogs that k2gen
#      generates beside the hand-written read-only `lake` (docker/trino/test/local,
#      one shared metastore). Each domain's writer writes its own catalog and no
#      other; nothing writes `lake`, table procedures and CALL included; a user
#      no rule names writes nowhere.
#
#   make test-trino            # locally
#   CI job "Trino (access rules)" runs the same script.
#
# Identity here is the X-Trino-User header (`trino --user`), as on the stack: not
# authenticated, so these are fences against mistakes, not against a liar.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=$(grep -oE 'trinodb/trino:[^ ]+' docker-compose.yml | head -1)
NAME=k2-trino-rules-test
ETC="$PWD/docker/trino/etc"
# The domains, and their fence names, straight from the registry.
mapfile -t DOMAINS < <(sed -n 's/^  \([a-z][a-z0-9-]*\):$/\1/p' contracts/domains.yaml | tr - _ | sort)

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# sql <user> <statement>; sql_with <user> <catalog.property=value> <statement>
sql() { docker exec "$NAME" trino --user "$1" --execute "$2" 2>&1; }
sql_with() { docker exec "$NAME" trino --user "$1" --session "$2" --execute "$3" 2>&1; }

# start <docker run args...>: a fresh container, waited on for up to 120 s.
start() {
  cleanup
  docker run -d --name "$NAME" --network none --cpus 2 --memory 4g -v "$ETC":/etc/trino:ro "$@" "$IMAGE" >/dev/null
  for _ in $(seq 1 60); do
    sql trino 'SELECT 1' >/dev/null && return 0
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || break
    sleep 2
  done
  echo "FAIL: Trino did not start"; docker logs "$NAME" 2>&1 | tail -40; exit 1
}

fail=0
bad() { echo "FAIL: $*"; fail=1; }
# allow <user> <statement> / deny <user> <statement>
allow() {
  if out=$(sql "$1" "$2"); then echo "ok allowed ($1): $2"; else bad "denied or broken ($1): $2"; echo "  $out"; fi
}
deny() {
  out=$(sql "$1" "$2") || true
  case "$out" in
    *"Access Denied"*) echo "ok denied  ($1): $2" ;;
    *) bad "not denied ($1): $2"; echo "  $out" ;;
  esac
}

# 1. The real configuration. Placeholder keys: nothing is reached. The access key
# is a canary: Trino logs each catalog property at start and masks the secret key
# only, so etc/log.properties silences that logger and the log must not hold it.
start -e MINIO_ROOT_USER=k2-canary-access-key -e MINIO_ROOT_PASSWORD=unused
# grep -c, not -q: -q exits at the first match and pipefail reads docker's SIGPIPE as no match.
leaked=$(docker logs "$NAME" 2>&1 | grep -c k2-canary-access-key || true)
[ "$leaked" = 0 ] || bad "Trino's log prints s3.aws-access-key on $leaked lines: etc/log.properties must set io.trino.bootstrap.catalog=WARN"
want=$(printf '%s\n' lake system "${DOMAINS[@]}" | sort | tr '\n' ' ')
have=$(sql trino 'SHOW CATALOGS' | tr -d '"' | sort | tr '\n' ' ')
[ "$have" = "$want" ] || bad "catalogs are [$have], contracts/domains.yaml says [$want]: run make generate"
grep -qx 'iceberg.security=read_only' "$ETC/catalog/lake.properties" || bad "lake.properties is not read_only at the connector"
grep -qx 'iceberg.rest-catalog.warehouse=k2' "$ETC/catalog/lake.properties" || bad "lake.properties is not on warehouse k2"
for f in "$ETC"/catalog/*.properties; do
  c=$(basename "$f" .properties)
  [ "$c" = lake ] && continue
  # A domain catalog swapped onto the lake's warehouse would be a writable door
  # into it; one on another domain's warehouse would be the wrong fence.
  grep -qx "iceberg.rest-catalog.warehouse=$c" "$f" || bad "$c.properties is not on its own warehouse $c"
  grep -qx 'iceberg.rest-catalog.warehouse=k2' "$f" && bad "$c.properties is on the lake's warehouse k2"
done
[ "$fail" = 0 ] || { echo "trino-rules-test: FAIL ($IMAGE): the configuration, before any fence"; exit 1; }
echo "ok config: etc/ starts; catalogs ${want}match contracts/domains.yaml; only lake is on k2, read-only"

# 2. The fences, on local catalogs from the same generator.
start -v "$PWD/docker/trino/test/local":/etc/trino/catalog:ro
n=${#DOMAINS[@]}
for i in "${!DOMAINS[@]}"; do
  d=${DOMAINS[$i]} other=${DOMAINS[$(( (i + 1) % n ))]} w=dbt_${DOMAINS[$i]}
  allow "$w" "CREATE SCHEMA $d.s_$d"
  allow "$w" "CREATE TABLE $d.s_$d.t AS SELECT 1 AS x"
  allow "$w" "INSERT INTO $d.s_$d.t VALUES 2"
  deny  "$w" "CREATE SCHEMA $other.s_from_$d"
  deny  "$w" "CREATE TABLE $other.s_$d.t2 AS SELECT 1 AS x"
  deny  "$w" "INSERT INTO $other.s_$d.t VALUES 3"
  deny  "$w" "CREATE TABLE lake.s_$d.t3 AS SELECT 1 AS x"
done

# Every other SQL write on another domain's existing table, by its neighbour's writer
# and by a user no rule names (the tables exist: the local catalogs share a metastore).
for i in "${!DOMAINS[@]}"; do
  d=${DOMAINS[$i]} other=${DOMAINS[$(( (i + 1) % n ))]}
  for u in "dbt_$other" someone; do
    deny "$u" "DELETE FROM $d.s_$d.t WHERE x = 1"
    deny "$u" "UPDATE $d.s_$d.t SET x = 5 WHERE x = 2"
    deny "$u" "ALTER TABLE $d.s_$d.t ADD COLUMN y integer"
    deny "$u" "CREATE VIEW $d.s_$d.v_$u AS SELECT 1 AS x"
    deny "$u" "DROP TABLE $d.s_$d.t"
  done
done

# Reads cross every fence: a view in one domain over another domain's table and the lake,
# read by a user who owns neither (a definer view: its owner's rights reach the base tables).
a=${DOMAINS[0]} b=${DOMAINS[1]}
allow "dbt_$a" "CREATE VIEW $a.s_$a.v AS SELECT t.x FROM $b.s_$b.t t JOIN lake.s_$a.t l ON t.x = l.x"
if [ "$(sql someone "SELECT count(*) FROM $a.s_$a.v")" = '"2"' ]; then echo "ok read back: 2 rows through a view over $b and lake, read by someone"
else bad "the cross-domain view did not read back 2 rows for a reader"; fi

# Nothing writes lake: the connector refuses table procedures, which the rules cannot.
w=dbt_$a
snap=$(sql "$w" "SELECT snapshot_id FROM lake.s_$a.\"t\$refs\" WHERE name = 'main'" | tr -d '"')
deny  "$w" "ALTER TABLE lake.s_$a.t EXECUTE optimize"
deny  "$w" "ALTER TABLE lake.s_$a.t EXECUTE rollback_to_snapshot($snap)"
deny  "$w" "ALTER TABLE lake.s_$a.t EXECUTE expire_snapshots(retention_threshold => '7d')"
deny  "$w" "ALTER TABLE lake.s_$a.t EXECUTE remove_orphan_files(retention_threshold => '7d')"
deny  "$w" "DELETE FROM lake.s_$a.t"
deny  "$w" "DROP TABLE lake.s_$a.t"
# Iceberg's CALL procedures. rules.json has no procedures list, so only Trino's
# built-in ones run: that default is what denies these in a domain catalog, and a
# procedures rule added later would reopen them, which these lines would catch.
deny  "$w" "CALL lake.system.unregister_table('s_$a', 't')"
deny  "$w" "CALL $a.system.register_table(schema_name => 's_$a', table_name => 'r', table_location => 'local:///s_$a/t')"
for u in "dbt_$a" "dbt_$b" someone; do
  deny "$u" "CALL lake.system.rollback_to_snapshot('s_$a', 't', $snap)"
done
for u in "dbt_$b" someone; do
  deny "$u" "CALL $a.system.rollback_to_snapshot('s_$a', 't', $snap)"
done

# The retention floors are session properties; nobody may lower them, not even a
# domain's writer in its own catalog, so an expire or orphan clean below 7 days
# fails on the floor. Any other catalog session property still sets.
for u in "dbt_$a" someone; do
  deny "$u" "SET SESSION $a.expire_snapshots_min_retention = '0s'"
  deny "$u" "SET SESSION $a.remove_orphan_files_min_retention = '0s'"
done
out=$(sql_with "dbt_$a" "$a.expire_snapshots_min_retention=0s" "ALTER TABLE $a.s_$a.t EXECUTE expire_snapshots(retention_threshold => '0s')") || true
case "$out" in
  *"Access Denied"*|*"shorter than the minimum retention"*)
    echo "ok refused (dbt_$a): --session $a.expire_snapshots_min_retention=0s, then expire_snapshots at 0s: $(grep -m1 -oE 'Access Denied[^.]*|Retention specified[^)]*\)' <<< "$out")" ;;
  *) bad "an expire at 0s with the floor lowered by --session was not refused"; echo "  $out" ;;
esac
out=$(sql "dbt_$a" "ALTER TABLE $a.s_$a.t EXECUTE expire_snapshots(retention_threshold => '0s')") || true
case "$out" in
  *"shorter than the minimum retention"*) echo "ok floor (dbt_$a): expire_snapshots at 0s refused at the 7-day minimum" ;;
  *) bad "expire_snapshots at 0s did not fail on the floor"; echo "  $out" ;;
esac
allow "dbt_$a" "SET SESSION $a.dynamic_filtering_wait_timeout = '1s'"
# The control: the same procedure in the writer's own catalog runs, so the
# denials through lake are the connector's and not a broken table.
allow "$w" "ALTER TABLE $a.s_$a.t EXECUTE optimize"

# The known gap (ADR-044): no built-in access control in Trino 484 restricts
# ALTER TABLE ... EXECUTE per user on a writable catalog, so any user can run a
# table procedure on any domain's table (at or above the retention floors). Asserted, so that a release which
# closes it fails here and the ADR is revisited. EXPLAIN plans without running it.
case "$(sql "dbt_$b" "EXPLAIN ALTER TABLE $a.s_$a.t EXECUTE optimize")" in
  *TableExecute*) echo "ok known gap: dbt_$b can plan a table procedure on $a (ADR-044)" ;;
  *) bad "dbt_$b can no longer run a table procedure on $a: the gap closed, revisit ADR-044 and this test" ;;
esac

# A user no rule names reads every catalog and writes none.
allow someone "SELECT count(*) FROM $a.s_$a.t"
for d in "${DOMAINS[@]}"; do
  deny someone "CREATE SCHEMA $d.s_someone"
  deny someone "INSERT INTO $d.s_$d.t VALUES 4"
done
deny  someone "CREATE TABLE lake.s_$a.t_someone AS SELECT 1 AS x"

# A user sees and kills only its own queries: someone sees none of dbt_$a's.
if [ "$(sql someone "SELECT count(*) FROM system.runtime.queries WHERE \"user\" = 'dbt_$a'")" = '"0"' ] \
  && [ "$(sql "dbt_$a" "SELECT count(*) > 0 FROM system.runtime.queries WHERE \"user\" = 'dbt_$a'")" = '"true"' ]; then
  echo "ok queries: someone sees none of dbt_$a's queries, dbt_$a sees its own"
else bad "query visibility is not limited to the owner"; fi

for d in "${DOMAINS[@]}"; do
  [ "$d" = "$a" ] && allow "dbt_$d" "DROP VIEW $d.s_$d.v"
  allow "dbt_$d" "DROP TABLE $d.s_$d.t"
  allow "dbt_$d" "DROP SCHEMA $d.s_$d"
done

[ "$fail" = 0 ] || { echo "trino-rules-test: FAIL ($IMAGE)"; exit 1; }
echo "trino-rules-test: PASS ($IMAGE, ${#DOMAINS[@]} domains)"
