#!/usr/bin/env bash
# Spike N0-4(a): a dbt-trino incremental merge with incremental_predicates on an Iceberg table
# partitioned by event_date. Three models read the same source (project/models):
#   m_plain    unique key id, no predicate (the control)
#   m_window   unique key id, predicate DBT_INTERNAL_DEST.event_date >= DATE '2026-01-08'
#   m_partkey  unique key (event_date, id), no predicate
# Batch 1 is 1,000 rows, even ids 2 to 2000, over 10 days (100 a day, one data file a day); batch 2
# updates id 14 (2026-01-08, inside the window), corrects id 4 late (2026-01-03, outside it) and
# adds id 1001. Every file's id range holds 1001, so Trino's dynamic filter on id, which prunes by a
# file's min and max, skips no file here and the counts below are partition pruning alone. Then: the MERGE dbt generated, what
# its target scan read (EXPLAIN and the executed query's own statistics), and the rows that result.
# Writes up in docs/research/2026-11-26-release-spine-spikes.md, section (a).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../spine-lib.sh"
SCHEMAS=(spike_n04dbt_a)
spine_start a 18191
W=dbt_spike S=spike.spike_n04dbt_a MODELS="m_plain m_window m_partkey"

q $W "CREATE SCHEMA $S"
q $W "CREATE TABLE $S.events (id bigint, event_date date, amount bigint, loaded_at timestamp(6))"
q $W "INSERT INTO $S.events SELECT 2 * x, date_add('day', x % 10, DATE '2026-01-01'), 2 * x, TIMESTAMP '2026-01-11 00:00:00'
      FROM UNNEST(sequence(1, 1000)) AS t(x)"
use_project "$HERE/project" spike_n04dbt_a
dbt_ run >/dev/null
echo "batch 1: 1000 synthetic rows, even ids, event_date 2026-01-01 to 2026-01-10, 100 a day; each model created (CTAS)"
q $W "INSERT INTO $S.events VALUES
      (14, DATE '2026-01-08', 14000, TIMESTAMP '2026-01-12 00:00:00'),
      (4, DATE '2026-01-03', 4000, TIMESTAMP '2026-01-12 00:00:00'),
      (1001, DATE '2026-01-10', 1001, TIMESTAMP '2026-01-12 00:00:00')"
echo "batch 2: id 14 updated (2026-01-08, in the window), id 4 corrected late (2026-01-03, outside), id 1001 new"
out=$(dbt_ run --vars '{since: "2026-01-11 00:00:00"}' 2>&1) || { echo "$out" | tail -20; exit 1; }
grep -oE 'OK created sql incremental model .*' <<< "$out" | sed 's/\x1b\[[0-9;]*m//g; s/  */ /g; s/^/  /'

echo; echo "== the MERGE dbt generated for m_window (target/run, whitespace folded)"
tr -s ' \n' ' ' < "$WORK/target/run/spine_a/models/m_window.sql" | grep -oE 'merge into .* on \([^W]*' | sed 's/ when$//; s/^/  /'

# The target scan's rows, from the executed MERGE's own statistics (GET /v1/query/<id>): the scan
# operators of the stage that reads the target table, as queryStats reports them.
target_rows() {
  python3 - "$PORT" "$1" "$2" <<'EOF'
import json, sys, urllib.request
port, qid, table = sys.argv[1:]
req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/query/{qid}", headers={"X-Trino-User": "dbt_spike"})
info = json.load(urllib.request.urlopen(req))
stage = next(i["fragmentId"] for i in info["inputs"] if i["table"] == table)
print(sum(o.get("physicalInputPositions", 0) for o in info["queryStats"]["operatorSummaries"]
          if str(o["stageId"]) == stage and "Scan" in o["operatorType"]))
EOF
}
# EXPLAIN (TYPE IO) of the same MERGE text, with dbt's temporary view made again (dbt drops it).
explain_io() {
  local m=$1 merge
  merge=$(tr -s ' \n' ' ' < "$WORK/target/run/spine_a/models/$m.sql" | sed 's/^.*\(merge into\)/\1/')
  q $W "CREATE VIEW $S.${m}__dbt_tmp AS SELECT id, event_date, amount, loaded_at FROM $S.events
        WHERE loaded_at > TIMESTAMP '2026-01-11 00:00:00'"
  q $W "EXPLAIN (TYPE IO, FORMAT JSON) $merge" | python3 -c '
import json, sys
t = next(i for i in json.load(sys.stdin)["inputTableColumnInfos"] if i["table"]["schemaTable"]["table"] == sys.argv[1])
c = t["constraint"]["columnConstraints"]
print("constraint", "; ".join("%s %s" % (x["columnName"], x["domain"]["ranges"]) for x in c) or "none",
      "| estimated rows", int(t["estimate"]["outputRowCount"]))' "$m"
  q $W "EXPLAIN $merge" | grep -oE "ScanFilter\[table = spike:spike_n04dbt_a\.$m\\\$data@[0-9]+[^]]*\]" | sed 's/@[0-9]*//'
  q $W "DROP VIEW $S.${m}__dbt_tmp"
}

echo; echo "== the MERGE's target scan, per model"
declare -A read
for m in $MODELS; do
  qid=$(q $W "SELECT query_id FROM system.runtime.queries WHERE state = 'FINISHED'
              AND query LIKE '%merge into \"spike\".\"spike_n04dbt_a\".\"$m\"%' ORDER BY created DESC LIMIT 1")
  read[$m]=$(target_rows "$qid" "$m")
  echo "$m: executed MERGE $qid read ${read[$m]} target rows of 1000"
  explain_io "$m" | sed 's/^/  EXPLAIN: /'
done

echo; echo "== what the merge left"
declare -A dups
for m in $MODELS; do
  rows=$(q $W "SELECT count(*) FROM $S.$m")
  dups[$m]=$(q $W "SELECT count(*) FROM (SELECT id FROM $S.$m GROUP BY id HAVING count(*) > 1)")
  echo "$m: $rows rows, $(q $W "SELECT count(DISTINCT id) FROM $S.$m") ids, ${dups[$m]} id(s) twice;" \
    "id 4 amounts: $(q $W "SELECT array_join(array_agg(amount ORDER BY amount), ',') FROM $S.$m WHERE id = 4");" \
    "id 14: $(q $W "SELECT array_join(array_agg(amount), ',') FROM $S.$m WHERE id = 14")"
done

echo
echo "finding (a):"
echo "  The predicate reaches the MERGE target scan: dbt-trino ANDs it into the ON clause, Trino turns it into a partition"
echo "  constraint on the target (EXPLAIN: constraint on [event_date]) and the MERGE read ${read[m_window]} target rows, against ${read[m_plain]} without it."
if [ "${dups[m_window]}" -gt 0 ]; then
  echo "  It changes correctness: the late row for id 4, outside the window, matched nothing, so WHEN NOT MATCHED inserted it."
  echo "  m_window now holds id 4 twice (old and corrected amount) and dbt reported success: a duplicate, not a lost update."
fi
if [ "${dups[m_partkey]}" = 0 ] && [ "${dups[m_plain]}" = 0 ]; then
  echo "  The partition column in the unique key is correct by construction (no duplicate) and the MERGE read ${read[m_partkey]} target rows:"
  echo "  Trino's dynamic filter on event_date prunes to the partitions the batch touches, late ones included."
fi
