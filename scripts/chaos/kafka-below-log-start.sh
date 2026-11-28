#!/usr/bin/env bash
# W2-13, plan 004's Verification "kafka-below-log-start.sh: fails loudly; the accept-data-loss run
# records offset_gap": on ONE throwaway lake with a Redpanda (prefix kafka-below-log-start,
# K2_THROWAWAY_REDPANDA=1) from <release>, the release's own kraken-trades load, audit and publish
# (docker/lake/connector.py in the DAG factory's shape, scripts/lib/run-path-lib.sh). The log start is
# moved past the resume point by the pinned Redpanda's delete-records command (docker-compose.yml's
# redpanda v25.3.4: `rpk topic trim-prefix <topic> -p <partition> -o <offset> --no-confirm`). Claims 1
# to 3 are kafka-path.sh group b's 1 to 3 (its fixture product there, kraken-trades here). Claim 4 is
# the record task's half: with no Airflow here, the release's own factory.py, imported in the Airflow
# image built from this tree (docker/airflow/Dockerfile, as make test-airflow builds it; compose's
# k2-airflow:3.3.2 tag is whatever a host last built under it) as the scheduler parses it, builds
# the offset_gap row from load's result line (the XCom `result` SparkTask pushes) under the manual
# run's conf {"acceptDataLoss": true, "reason"},
# checked by its own data_loss_reason; the release's k2runs writes it as svc.runs, and
# k2runs.offset_gaps, the product page's reader, reads it back. Airflow's XCom between the two tasks
# is docker/airflow/tests/test_factory.py's to prove, and the live stack's.
#
#   bash scripts/chaos/kafka-below-log-start.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Frames: services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl, b1 a clean run, then the
# frames again and every partition with two records or more past b1's end offsets trimmed halfway
# into them: a hole after the resume point. Claims, one PASS or FAIL line each, then
# `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 b3's load fails loudly: exit 2, naming each trimmed partition, its committed offset and its log
#     start; nothing lands
#   2 b4's load with --accept-data-loss lands from the log start, records the gap in the landing
#     commit's k2.kafka-gaps and returns it as offsetGaps
#   3 b4's audit passes with the gap excluded from its accounting, and publish moves main
#   4 b4's offset_gap row: task load, event offset_gap, the landing as its table; k2runs.offset_gaps
#     reads one gap, each trimmed partition from its committed offset to its log start - 1, and the
#     operator's reason
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down kafka-below-log-start`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start kafka-below-log-start "${1:-}" 4
IMAGE=k2-airflow:kafka-below-log-start  # no dbt build here: rp_cleanup removes this tag in the dbt-runner's place
docker build -q -f docker/airflow/Dockerfile -t "$IMAGE" . > "$W/build.log" 2>&1 \
  || { tail -20 "$W/build.log" >&2; die "the Airflow image did not build"; }
export K2_THROWAWAY_REDPANDA=1
rp_lake
rp_chain_files
TOPIC=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["connector"]["topic"])' \
  "$STATE/releases/$ID/compiled/products/$KT/pipeline.json")
kseed "$RP/frames.jsonl"; kruns b1; kfacts b1
kseed "$RP/frames.jsonl"
docker exec "$P-redpanda" rpk topic describe "$TOPIC" -p > "$RP/describe.txt"
python3 - "$RP/facts-b1.json" "$RP/describe.txt" > "$RP/trims.json" <<'PY'
# {partition: new log start}: halfway into the records past b1's end offsets, where there are two or more
import json, sys
f = json.load(open(sys.argv[1]))
[ends] = json.loads(next(s for s in reversed(f["landing"]["snapshots"]) if s[4].get("k2.job") == "connector")[4]["k2.kafka-offsets"]).values()
hw = {x.split()[0]: int(x.split()[-1]) for x in open(sys.argv[2]) if x.split() and x.split()[0].isdigit()}
print(json.dumps({p: ends.get(p, 0) + (h - ends.get(p, 0)) // 2 for p, h in hw.items() if h - ends.get(p, 0) >= 2}))
PY
python3 -c 'import json, sys; [print(p, o) for p, o in json.load(open(sys.argv[1])).items()]' "$RP/trims.json" |
  while read -r p o; do docker exec "$P-redpanda" rpk topic trim-prefix "$TOPIC" -p "$p" -o "$o" --no-confirm >> "$RP/trim.log" 2>&1; done
echo "$P: trimmed $(cat "$RP/trims.json") (partition: new log start)"
TP=$KT task "$ID" b3 load; kfacts b3
TP=$KT task "$ID" b4 load --accept-data-loss; TP=$KT task "$ID" b4 audit; TP=$KT task "$ID" b4 publish; kfacts b4
# The record task's offset_gap row, by the release's factory.py and k2runs in the Airflow image, as svc.runs.
REASON="kafka-below-log-start.sh: the log start moved past the resume point on a throwaway lake"
docker run -i --rm --name "$P-record" --label "k2.throwaway=$P" --pull never --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):0" --network "$NET" -e K2_TRINO_HOST -e K2_TRINO_INTERNAL_PORT -e K2_RUNS_USER \
  -e AIRFLOW__LOGGING__LOGGING_LEVEL=WARNING \
  --mount "type=bind,source=$STATE/releases/$ID,target=/k2/releases/$ID,readonly" \
  --mount "type=bind,source=$S/principals/svc.runs.password,target=/run/k2-secrets/runs.password,readonly" \
  --mount "type=bind,source=$S/tls/ca.pem,target=/run/k2-secrets/tls/ca.pem,readonly" \
  --entrypoint python "$IMAGE" - "/k2/releases/$ID" "$ID" "$KT" "$REASON" \
  "$(grep -v '^\s*$' "$RP/b4.load.out" | tail -1)" > "$RP/b4.record.out" 2> "$RP/b4.record.err" <<'PY'
import json, sys
path, release, product, reason, last = sys.argv[1:6]
sys.path.insert(0, f"{path}/platform/docker/airflow/dags")
import factory  # noqa: E402 - the release's own, parsed as the scheduler parses it
result = json.loads(last)  # SparkTask.execute: load's last stdout line is its XCom `result`


class Run:  # the manual run's dag_run, as `airflow dags trigger --conf` makes it
    conf, run_type = {"acceptDataLoss": True, "reason": reason}, "manual"


class TI:  # the record task's ti: the run's ids and load's XCom
    dag_id, run_id = "kraken_trades", "b4"

    def xcom_pull(self, task_ids, key):
        return result if (task_ids, key) == ("load", "result") else None


row = factory.offset_gap(TI(), product, release, factory.data_loss_reason(Run(), True))
if row is None:
    sys.exit(f"factory.offset_gap built no row from load's result {result}")
k2runs = factory.load_k2runs(path)
conn = k2runs.connect()
k2runs.record(conn, **row)
cur = conn.cursor()
cur.execute(f"SELECT run_id, task, event, recorded_at, detail FROM {k2runs.TABLE} WHERE product_id = ?", [product])
records = [dict(zip(("run_id", "task", "event", "recorded_at", "detail"), r)) for r in cur.fetchall()]
print(json.dumps({"reason": reason, "row": row, "gaps": k2runs.offset_gaps(records)}, default=str))
PY
echo $? > "$RP/b4.record.rc"
echo "$P: b4 record exit $(cat "$RP/b4.record.rc")"
kclaim below
kclaim accepted
kclaim gap-audit
kclaim offset-gap
finish
