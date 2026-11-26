#!/usr/bin/env bash
# Spike N0-4(f), review finding F12: roll a contracted table back across its create-or-replace.
# The model `contracted` has an enforced contract and on_table_exists: replace, as dbt/ does. v1
# (id, name, qty integer) is built; v2 (id, label, qty bigint, amount) is built over it while a
# reader polls through the read-only catalog; then rollback_to_snapshot(<v1 snapshot>), and what
# the table, its schema, its snapshot ids and its readers show. Last, the escape hatch: a
# create-or-replace from the table's own v1 snapshot.
# Writes up in docs/research/2026-11-26-release-spine-spikes.md, section (f).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../spine-lib.sh"
SCHEMAS=(spike_n04dbt_f)
spine_start f 18193
W=dbt_spike R=p_reader NS=spike_n04dbt_f T=spike.spike_n04dbt_f.contracted RO=spike_ro.spike_n04dbt_f.contracted

# meta: the catalog's view of the table: uuid, current snapshot, current schema and its columns.
meta() {
  lk_table $NS contracted | python3 -c '
import json, sys
m = json.load(sys.stdin)["metadata"]
s = next(x for x in m["schemas"] if x["schema-id"] == m["current-schema-id"])
print("uuid", m["table-uuid"], "| current snapshot", m.get("current-snapshot-id"), "| schema", m["current-schema-id"], "of",
      len(m["schemas"]), "(" + ", ".join("%s:%s %s" % (f["id"], f["name"], f["type"]) for f in s["fields"]) + ")",
      "| snapshots", len(m.get("snapshots", [])))'
}
cols() { q "$1" "SELECT array_join(array_agg(column_name || ' ' || data_type ORDER BY ordinal_position), ', ')
                 FROM ${2%.*.*}.information_schema.columns WHERE table_schema = '$NS' AND table_name = 'contracted'"; }

q $W "CREATE SCHEMA spike.$NS"
use_project "$HERE/project" $NS
dbt_ run >/dev/null
s1=$(snapshot $W spike.$NS contracted)
echo "v1 built: snapshot $s1; $(meta)"
echo "  reader sees: $(q $R "SELECT count(*) FROM $RO") rows; columns $(cols $R $RO)"

echo; echo "== v2 over v1 (dbt run, contract changed), a reader polling $RO throughout"
cp "$HERE"/v2/* "$WORK/project/models/"
py - "$PORT" "$RO" "$WORK/stop" > "$WORK/poll.txt" <<'EOF' &
import os, sys, time, trino
port, table, stop = sys.argv[1:]
cur = trino.dbapi.connect(host="127.0.0.1", port=int(port), user="p_reader").cursor()
seen, n, t0 = [], 0, time.monotonic()
while not os.path.exists(stop):
    try:
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        obs = f"{len(rows)} rows, columns {', '.join(d[0] for d in cur.description)}"
    except Exception as e:
        obs = "error: " + str(getattr(e, "message", e))[:120]
    n += 1
    if not seen or seen[-1][1] != obs:
        seen.append((round(time.monotonic() - t0, 3), obs, n))
    else:
        seen[-1] = (seen[-1][0], obs, seen[-1][2])
for at, obs, _ in seen:
    print(f"  from {at:7.3f} s: {obs}")
print(f"  ({n} reads)")
EOF
poller=$!
sleep 3
dbt_ run >/dev/null
sleep 2; touch "$WORK/stop"; wait $poller
cat "$WORK/poll.txt"
echo "  every commit, oldest first (snapshot, operation, parent, records added, total; \$snapshots):"
q $W "SELECT snapshot_id, operation, parent_id, element_at(summary, 'added-records'), element_at(summary, 'total-records')
      FROM spike.$NS.\"contracted\$snapshots\" ORDER BY committed_at" | sed 's/^/    /'
uuid_v1=$(lk_table $NS contracted | python3 -c 'import json, sys; print(json.load(sys.stdin)["metadata"]["table-uuid"])')
echo "  after v2: $(meta)"
echo "  v1 snapshot still listed: $(q $W "SELECT count(*) FROM spike.$NS.\"contracted\$snapshots\" WHERE snapshot_id = $s1")"
echo "  reader at FOR VERSION AS OF $s1: $(q $R "SELECT array_join(array_agg(format('%s/%s/%s', id, name, qty) ORDER BY id), ' ') FROM $RO FOR VERSION AS OF $s1" 2>&1 | tail -1)"

echo; echo "== ALTER TABLE ... EXECUTE rollback_to_snapshot($s1) as the writer"
rb=$(q $W "ALTER TABLE $T EXECUTE rollback_to_snapshot($s1)" 2>&1 && echo ok) || true
echo "  rollback: ${rb##*$'\n'}"
echo "  after rollback: $(meta)"
echo "  current snapshot: $(snapshot $W spike.$NS contracted) (v1 was $s1)"
echo "  reader sees: columns $(cols $R $RO)"
q $R "SELECT * FROM $RO ORDER BY id" | sed 's/^/    /'
rolled_cols=$(cols $R $RO)

echo; echo "== escape hatch: CREATE OR REPLACE TABLE ... AS SELECT * FROM the table FOR VERSION AS OF $s1"
before=$(q $W "SELECT count(*) FROM spike.$NS.\"contracted\$snapshots\"")
q $W "CREATE OR REPLACE TABLE $T AS SELECT * FROM $T FOR VERSION AS OF $s1"
echo "  commits it added: $(( $(q $W "SELECT count(*) FROM spike.$NS.\"contracted\$snapshots\"") - before ))"
echo "  after: $(meta)"
echo "  reader sees: columns $(cols $R $RO)"
q $R "SELECT * FROM $RO ORDER BY id" | sed 's/^/    /'
uuid_end=$(lk_table $NS contracted | python3 -c 'import json, sys; print(json.load(sys.stdin)["metadata"]["table-uuid"])')
restored_cols=$(cols $R $RO)

echo
echo "finding (f):"
echo "  dbt's create-or-replace keeps the table: one uuid throughout ($([ "$uuid_v1" = "$uuid_end" ] && echo same || echo CHANGED)), every snapshot id"
echo "  survives the replace and v1's stays readable FOR VERSION AS OF $s1 with v1's columns. The rebuild is two commits, an"
echo "  empty replace then the insert, so a reader saw the table empty, already in v2's columns, between them (the poll above)."
if [ -n "$rb" ] && [ "${rb##*$'\n'}" = ok ]; then
  echo "  rollback_to_snapshot($s1) works across the replace and restores v1's rows, but NOT v1's schema: the current schema"
  echo "  stays v2 ($rolled_cols), so readers see v1's data through v2's columns: name is gone, label and amount read null."
else
  echo "  rollback_to_snapshot($s1) across the replace failed: $rb"
fi
echo "  CREATE OR REPLACE ... AS SELECT * FROM t FOR VERSION AS OF $s1 restores both, in one commit ($restored_cols)."
