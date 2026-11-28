#!/usr/bin/env bash
# W0-10 and W1-16 (phase 004): the restore drill on ONE throwaway lake (prefix restore-path), the
# lake a deploy of <release> leaves, filled by the run path's own steps, backed up by the backup's own
# code, emptied and restored by scripts/restore.py as `make restore-drill` runs it. The restore's
# in-network half runs in the tools image built from this tree (the image a release pins), with the
# restore key; the throwaway publishes no Trino port, so its holds go in-network too
# (--holds-in-network), as svc.operator. No Airflow here (--no-airflow): the pause, the drain and the
# selective unpause are tests/test_restore.py's, against a recording fake.
#
#   bash scripts/verify/restore-path.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 to 3 run-path.sh's claims 1 to 3: calendars built; ECB's good fixture loaded, audited, published; ECB built
#   4 the Kafka chain as run-path.sh builds it (a Redpanda joins, K2_THROWAWAY_REDPANDA=1): currency-master, kraken-instruments,
#     security-master, kraken-trades on the synthetic frames, canonical-trades, candles; every trade in ohlcv_1m
#   5 the backup: scripts/backup-lake.sh --dump of the catalog database, then scripts/backup_lake.py copy
#     in the tools image with the backup key, twice (two runs); backup_lake.py verify OK on the newest
#   6 refused (exit 2) before anything is paused, held or written: a run older than the newest, and a
#     copy of the backup whose newest run lacks one file
#   7 (R1) the drill refused (exit 2) before any pause, hold or damage, the lake as it was: without CONFIRM
#     (its statement names what it destroys and what it spares); after a commit since the run (an operator
#     hold and its release); with a live release whose backup set names a table the run lacks; (E2) with
#     a live release whose lake-init makes no restore user; (E1) with a restore key MinIO does not hold.
#     Then a third backup run, the one the drill restores
#   8 one restore table's files wiped: `restore.py lake --phase rest` with no product held is refused
#     (exit 2) and puts nothing back
#   9 (R2) Postgres stopped: the catalog's answer is an error, not a loss: `restore.py run` fails (exit 1)
#     and drops, replays and changes nothing
#  10 the drill (CONFIRM=<release id> restore.py drill): the catalog database emptied, every object at
#     each restore table's location, each landing and each rebuildable rebuild table of warehouses k2
#     and governance deleted with the root pair; then the restore, the catalog database replayed from
#     its dump; exit 0 and its times; (E1) before it, the key proven in each scope it writes, every put
#     refused as existing (412): nothing written
#  11 every restore table registered at the backup's metadata, its rows as before the damage
#  12 every landing object back, key for key and size for size
#  13 kraken-trades' bronze recreated empty with its columns; the whole rebuild tables kept, refs and all,
#     currency-master's bronze (a file product's, which the drill spares) among them
#  14 (R3) every product held (restore) before any k2 table came back; released after but kraken-trades and
#     everything downstream of it, held with make rebuild's mark, the rebuild order printed
#  15 catalog_drift.py --runs clean: each audited tag equal to its newest audited run record
#  16 a second restore on the restored lake puts nothing back, moves nothing, ends no hold left for rebuild
#  17 (R3) make rebuild (scripts/rebuild.py's rebuild(), scripts/lib/restore_path_rebuild.py's stand-ins for
#     Airflow and the scheduler's one-shot) refuses canonical-trades before kraken-trades, then rebuilds the
#     three in the printed order, each ending its restore hold; ohlcv_1m holds every trade, as before the
#     damage, and a scheduled cycle after takes one more trade, none skipped
#  18 (R6) ECB's gold missing one data file is not whole: a restore recreates it and holds ECB for rebuild
#  19 a restore table replaced by another table of its name: the restore is refused (exit 2), no hold written
#
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down restore-path`, the images to `docker rmi dbt-runner:restore-path tools:restore-path`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start restore-path "${1:-}" 19
TOOLS=tools:restore-path
trap 'rp_cleanup; docker rmi -f "$TOOLS" >/dev/null 2>&1' EXIT
rp_image
docker build -q -f docker/tools/Dockerfile -t "$TOOLS" . > "$W/tools-build.log" 2>&1 || { tail -20 "$W/tools-build.log" >&2; die "the tools image did not build"; }
export K2_THROWAWAY_REDPANDA=1
rp_lake  # exports the lake's root pair and every engine's pair, the restore key's included (s3.env)
rp_chain_files
# svc.operator's statements for make rebuild's holds, in-network (scripts/lib/restore_path_rebuild.py)
docker run -d --name "$P-sql" --label "k2.throwaway=$P" --network "$NET" --user "$(id -u):$(id -g)" --memory 256m \
  -v "$S/principals/svc.operator.password:/run/k2-secrets/operator.password:ro" -v "$S/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" \
  "$TOOLS" sleep infinity > /dev/null || die "the holds helper did not start"
MINIO=$(grep -oE 'image: *ghcr.io/rjdscott/minio@sha256:[^ ]*' docker-compose.yml | head -1 | sed 's/image: *//')
B=$STATE/backup
mkdir -p "$B/catalog" "$B/runs"
rpy() { bash "$L" py "$P" scripts/lib/restore_path_probe.py "$@"; }
ran() {  # ran <label> <command...>: its stdout, stderr and exit to $RP/<label>.{out,err,rc}
  local label=$1; shift
  "$@" > "$RP/$label.out" 2> "$RP/$label.err"; echo $? > "$RP/$label.rc"
  echo "$P: $label exit $(cat "$RP/$label.rc")"
}
restore() {  # restore.py <run|drill> on this lake, as make restore runs it on the stack
  python3 scripts/restore.py "$@" --releases "$STATE/releases" --backup "$BK" --secrets "$S" --network "$NET" \
    --tools-image "$TOOLS" --minio-image "$MINIO" --postgres "$P-postgres" --lakekeeper "$P-lakekeeper" --no-airflow --holds-in-network \
    --dbt-image "$IMAGE"  # the image this lake's builds run: the scratch release pins a placeholder
}
backup() {  # backup <stamp>: the backup's two halves, as scripts/backup-lake.sh runs them
  bash scripts/backup-lake.sh --dump "$P-postgres" lakekeeper "$B/catalog/$1.sql.gz" > /dev/null || die "the catalog dump $1 failed"
  docker run --rm --network "$NET" --user "$(id -u):$(id -g)" --cpus 1 --memory 512m --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL --security-opt no-new-privileges:true \
    -e K2_BACKUP_S3_ACCESS_KEY -e K2_BACKUP_S3_SECRET_KEY -v "$STATE/releases:/k2/releases:ro" \
    -v "$REPO/scripts:/opt/k2/scripts:ro" -v "$B:/backup" "$TOOLS" /opt/k2/venv/bin/python /opt/k2/scripts/backup_lake.py \
    copy --release "/k2/releases/$ID" --dir /backup --run "$1" --dump "lakekeeper=catalog/$1.sql.gz" > "$RP/backup-$1.out" 2>&1 \
    || die "the copy $1 failed: $(tail -3 "$RP/backup-$1.out")"
  echo "$P: backup $1: $(tail -1 "$RP/backup-$1.out")"
}
nobjects() {  # run-path-lib.sh has objects(): never shadow it
   docker exec "$P-minio" mc ls -r --json "local/$1" 2>/dev/null | grep -c '"type":"file"'; }
pclaim() { local out; out=$(rpy claim "$1" "$ID" 2>&1); claimed "$1" "$out" $?; }

rp_calendars  # 1
rp_good       # 2
rp_build      # 3
build "$ID" reference-data.currency-master cm-1  # security-master's required input, its bronze empty here
listing ki-1 listing-1.json; build "$ID" "$KI" ki-1; build "$ID" "$SM" sm-1
kseed "$RP/frames.jsonl"; trades kt-1; build "$ID" "$CT" ct-1; build "$ID" "$CA" ca-1
rpy state "$ID" built
pclaim chain   # 4

T1=$(date -u +%Y%m%dT%H%M%SZ); backup "$T1"; sleep 1
T2=$(date -u +%Y%m%dT%H%M%SZ); backup "$T2"
python3 scripts/backup_lake.py verify --dir "$B" --backup-set "$STATE/releases/$ID/compiled/lake/backup-set.json" > "$RP/verify.out" 2>&1
pclaim backup  # 5

BK=$B; ran older restore run --run "$T1"
BK=$W/backup-partial; cp -al "$B" "$BK"
victim=$(find "$BK/runs/$T2/objects/k2-lake" -type f | head -1); rm -f "$victim"
ran partial restore run; BK=$B
pclaim refused-run  # 6

BK=$B CONFIRM= ran noconfirm restore drill
rpy touch-holds
BK=$B CONFIRM=$ID ran stale restore drill
EXTRA=$W/releases-extra; cp -a "$STATE/releases" "$EXTRA"; chmod -R u+w "$EXTRA"
python3 - "$EXTRA/$ID/compiled/lake/backup-set.json" <<'PY2' || die "the extra table could not be planted"
import json, sys
s = json.load(open(sys.argv[1]))
s["entries"].append({"kind": "table", "mode": "restore", "ref": "main", "table": "k2.ecb_fx_rates.added_since"})
json.dump(s, open(sys.argv[1], "w"))
PY2
BK=$B CONFIRM=$ID ran lacks python3 scripts/restore.py drill --releases "$EXTRA" --backup "$B" --secrets "$S" --network "$NET" \
  --tools-image "$TOOLS" --minio-image "$MINIO" --postgres "$P-postgres" --lakekeeper "$P-lakekeeper" --no-airflow --holds-in-network
NOPOL=$W/releases-nopolicy; cp -a "$STATE/releases" "$NOPOL"; chmod -R u+w "$NOPOL"
rm "$NOPOL/$ID/platform/docker/lake/minio/policies/restore.json"  # a release from before the restore key
BK=$B CONFIRM=$ID ran nopolicy python3 scripts/restore.py drill --releases "$NOPOL" --backup "$B" --secrets "$S" --network "$NET" \
  --tools-image "$TOOLS" --minio-image "$MINIO" --postgres "$P-postgres" --lakekeeper "$P-lakekeeper" --no-airflow --holds-in-network
BK=$B CONFIRM=$ID K2_RESTORE_S3_SECRET_KEY=not-the-secret-minio-holds ran badkey restore drill  # make engine-keys ran, lake-init did not
rpy state "$ID" refused
pclaim refused-drill  # 7
sleep 1; T3=$(date -u +%Y%m%dT%H%M%SZ); backup "$T3"  # after the touch: the run the drill restores

where=$(rpy where k2.ecb_fx_rates.audit_checks)
docker exec "$P-minio" mc rm -r --force "local/$where" > /dev/null
before=$(nobjects "$where")
ran unheld docker run --rm --network "$NET" --user "$(id -u):$(id -g)" -e K2_RESTORE_S3_ACCESS_KEY -e K2_RESTORE_S3_SECRET_KEY \
  -e K2_TRINO_HOST=trino -e K2_TRINO_INTERNAL_PORT=8443 -v "$STATE/releases:/k2/releases:ro" -v "$REPO/scripts:/opt/k2/scripts:ro" \
  -v "$B:/backup:ro" -v "$S/principals/svc.runs.password:/run/k2-secrets/runs.password:ro" -v "$S/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" \
  "$TOOLS" /opt/k2/venv/bin/python /opt/k2/scripts/restore.py lake --phase rest --release "/k2/releases/$ID" --run-dir "/backup/runs/$T3"
echo "{\"before\": $before, \"after\": $(nobjects "$where")}" > "$RP/unheld-gone.json"
pclaim refused-unheld  # 8

rpy state "$ID" prepg
docker stop "$P-postgres" > /dev/null
BK=$B ran pgdown restore run
docker start "$P-postgres" > /dev/null
for _ in $(seq 60); do docker exec "$P-postgres" pg_isready -U lk -d lakekeeper >/dev/null 2>&1 && break; sleep 1; done
docker restart "$P-lakekeeper" > /dev/null  # its pool held connections to the stopped Postgres
for _ in $(seq 60); do docker exec "$P-lakekeeper" /home/nonroot/lakekeeper healthcheck >/dev/null 2>&1 && break; sleep 1; done
rpy state "$ID" postpg
pclaim catalog-error  # 9

BK=$B CONFIRM=$ID ran drill restore drill
cat "$RP/drill.out"
rpy state "$ID" after
pclaim drill     # 10
pclaim restored  # 11
pclaim landings  # 12
pclaim rebuilt   # 13
pclaim holds     # 14
ran drift-runs docker run --rm --network "$NET" --user "$(id -u):$(id -g)" -e K2_TRINO_HOST=trino -e K2_TRINO_INTERNAL_PORT=8443 \
  -v "$STATE/releases:/k2/releases:ro" -v "$REPO/scripts:/opt/k2/scripts:ro" \
  -v "$S/principals/svc.runs.password:/run/k2-secrets/runs.password:ro" -v "$S/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" \
  "$TOOLS" /opt/k2/venv/bin/python /opt/k2/scripts/verify/catalog_drift.py --release "/k2/releases/$ID" --runs
pclaim tags      # 15

BK=$B ran again restore run
rpy state "$ID" again
pclaim again     # 16

rebuildx() { P=$P NET=$NET STATE=$STATE IMAGE=$IMAGE ID=$ID ran "$1" python3 scripts/lib/restore_path_rebuild.py "$2"; }
rebuildx rb-ct-first "$CT"
rebuildx rb-kt "$KT"; rebuildx rb-ct "$CT"; rebuildx rb-ca "$CA"
rpy state "$ID" rebuilt
kseed "$RP/more.jsonl"; trades kt-2; build "$ID" "$CT" ct-2; build "$ID" "$CA" ca-2
rpy state "$ID" cycle
pclaim rebuild-order  # 17

gone=$(rpy data-file fx_reference.ecb_fx_rates__base.gold_eur_rates_daily)
docker exec "$P-minio" mc rm --force "local/$gone" > /dev/null
BK=$B ran notwhole restore run
rpy state "$ID" notwhole
pclaim not-whole  # 18

rpy plant "$ID" k2.ecb_fx_rates.audit_checks
BK=$B ran planted restore run
rpy state "$ID" planted
pclaim refused-lake  # 19
finish
