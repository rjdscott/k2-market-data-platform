# Runbook: An ingest batch does not match its planned offsets

`LakeIngestBatchAuditFailed` fired: stage 1 committed a batch to `raw.messages` whose rows
do not match the offset range it read ([ADR-047](../adr/ADR-047-ingest-batch-audit-after-publish.md)).
This covers reading the finding, re-reading the missing records while Redpanda still holds
them, and recording them when it no longer does. It does not cover records evicted before
the read (that is `offset_gap`, [lake-ingest-lag.md §3](./lake-ingest-lag.md#3-failondataloss-the-offsets-point-below-what-the-broker-holds))
or the nightly `offset_continuity` audit ([lake-audit-failed.md §1](./lake-audit-failed.md#1-offset_continuity-a-hole-in-the-archive)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Written with the check, before it has ever fired.** Commands marked ✅ were run
> read-only against the live stack on 2026-11-25 UTC, on healthy data, so they returned
> nothing to act on. Steps 5 and 6 write to the lake and have **not** been run.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | A committed batch with a missing, extra or shifted offset | inside raw retention: 48 h or 2 GiB per partition, whichever binds first | not yet measured: no batch has failed the audit |
| 2 | The audit itself could not run (scope `lake.raw.messages`) | the next batch | not yet measured |

**Before trusting this alert after a deploy:** `lake-metrics` imports `metrics.py` once, so
the gauge is absent until the exporter restarts (`docker compose up -d --force-recreate
--no-deps lake-metrics`), and Prometheus loads rules only at start and on SIGHUP
(`docker kill -s HUP k2-prometheus`). <!-- VERIFY: run both after merge, then confirm
`k2_lake_batch_audit_failures_total` is served and the rule is loaded -->

---

## 1. A committed batch does not match its plan

**Symptom**, the `lake_ingest` task **succeeds** and its log carries one stderr line per bad
partition:

```text
stage 1: BATCH AUDIT FAILED market.crypto.v3.raw.kraken/0: planned <n> at <first>..<last>, found <m> at <first>..<last>
```

**Detection**, `LakeIngestBatchAuditFailed` (critical) in
`docker/prometheus/rules/lake-alerts.yml`, on `k2_lake_batch_audit_failures_total`. If the
hole is not repaired, `LakeAuditFailed` follows at the next 03:00 maintenance run, when
`offset_continuity` finds the same hole.

**Expected behaviour**, nothing in the data heals itself. The commit stood by design, the
offsets moved past the batch, and no later run reads those records again. The alert is a
pulse: the ingest files a row only for a failing batch, `metrics.py` ages the count out
after 15 minutes, and the alert resolves with the hole still there. A run of bad batches
holds it firing.

Two things it is **not**. An `--accept-data-loss` eviction never shows here: the audit
compares against the plan after the skip, so that range was never planned. And there are no
legitimate gaps on the v3 topics to explain it away: all nine are `cleanup.policy=delete`
(`rpk topic describe <topic> -c`, 2026-11-25) and the capture producer has no
`transactional.id` (asserted in `services/capture-rust/src/sink.rs`), so there are no
compacted offsets and no transaction markers. Every finding is real.

**Recovery**

```bash
# 1. Read the findings.                                                     ✅ verified
docker exec k2-trino trino --user reviewer --execute "
  SELECT run_ts, scope, observed, detail FROM lake.audit.checks
  WHERE check_name = 'batch_offsets' ORDER BY run_ts DESC LIMIT 20"
#   observed: planned minus found, so records missing; negative means extra rows
#   detail:   planned <n> at <first>..<last>, found <m> at <first>..<last>
```

Read the scope before the sign. Scope `lake.raw.messages` with `observed = -1` and a detail
starting `check raised` is not a finding about the data: the audit could not run, go to
[§2](#2-the-audit-itself-could-not-run). On a `<topic>/<partition>` scope, a negative
`observed` is a duplicate in the system of record: exactly-once broke. Do not delete
anything; go to [lake-audit-failed.md §1](./lake-audit-failed.md#1-offset_continuity-a-hole-in-the-archive)
and treat it as a contract failure.

```bash
# 2. One batch, or every batch? The task log, newest run.                   ✅ verified
docker exec k2-airflow-scheduler sh -c \
  "find /opt/airflow/logs -path '*dag_id=lake_ingest*' -name '*.log' | sort | tail -1 | xargs grep 'batch audit\|BATCH AUDIT'"
#   stage 1: batch audit 0 failure(s) in <s>s         <- this run was clean
```

Findings on consecutive `run_ts` values mean the fault is in the code path, and every
further batch commits past more records. Pause the schedule (same two commands as
[lake-ingest-lag.md §3](./lake-ingest-lag.md#3-failondataloss-the-offsets-point-below-what-the-broker-holds)
step 2, run there 2026-11-25): the records wait in Redpanda, and the retention clock keeps
running.

```bash
docker exec k2-airflow-scheduler airflow dags pause lake_ingest
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance|rebuild)\.py'   # until it prints nothing
```

```bash
# 3. The exact holes inside the planned range, from the data. Fill T, P, FIRST, LAST
#    from the row's scope and its planned range. ✅ verified on a healthy partition:
#    no rows, 1.3 s. A partition that wrote nothing returns no rows either: its hole
#    is the whole planned range, which the row's detail already says ("found none").
T=market.crypto.v3.raw.kraken P=0 FIRST=<first> LAST=<last>
docker exec k2-trino trino --user reviewer --execute "
SELECT first_missing, last_missing FROM (
  SELECT lag(\"offset\") OVER (ORDER BY \"offset\") + 1 AS first_missing, \"offset\" - 1 AS last_missing
  FROM lake.raw.messages
  WHERE topic = '$T' AND partition = $P AND \"offset\" BETWEEN $FIRST AND $LAST
) WHERE last_missing >= first_missing
UNION ALL
SELECT $FIRST, min(\"offset\") - 1 FROM lake.raw.messages
  WHERE topic = '$T' AND partition = $P AND \"offset\" BETWEEN $FIRST AND $LAST HAVING min(\"offset\") > $FIRST
UNION ALL
SELECT max(\"offset\") + 1, $LAST FROM lake.raw.messages
  WHERE topic = '$T' AND partition = $P AND \"offset\" BETWEEN $FIRST AND $LAST HAVING max(\"offset\") < $LAST"
```

```bash
# 4. Does Redpanda still hold them? LOG-START-OFFSET at or below first_missing: go to 5.
#    Above it: those records are gone, go to 6.                             ✅ verified
docker exec k2-redpanda rpk topic describe -p "$T"
```

```bash
# 5. Re-read one hole into raw.messages. Run once per hole from step 3, never over a
#    range that includes offsets already present: that writes duplicates. NOT YET RUN.
#    <!-- VERIFY: run against a scratch table first -->
#
#    It must not race the other two writers of raw.messages: the ingest appends to it,
#    and maintenance compacts it and audits it. So pause both schedules, wait out any
#    driver already running, and then take the lake's one writer lock
#    (docker/lake/lock.py) inside the re-read, non-blocking, as ingest.main does: if
#    anything still holds it the re-read exits before Spark starts, and while the re-read
#    holds it an ingest started by hand exits 75 and maintenance waits for it.
docker exec k2-airflow-scheduler airflow dags pause lake_ingest
docker exec k2-airflow-scheduler airflow dags pause lake_maintenance
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance|rebuild)\.py'   # until it prints nothing

#    k2.job=operator, not ingest: the ingest resumes from the newest k2.job=ingest
#    snapshot only (offsets.latest_summary), so this append cannot move the committed
#    offsets, and stage 2 decodes it on its next run like any other raw.messages snapshot.
#    The projection is stage 1's own (docker/lake/ingest.py, stage_raw).
docker exec -e T="$T" -e P="$P" -e FIRST=<first_missing> -e LAST=<last_missing> \
  k2-spark-iceberg python3 -c "
import json, os, sys; sys.path.insert(0, '/home/iceberg/lake')
from datetime import datetime, timezone
from lock import LOCK_PATH, acquire_lock
lock = acquire_lock(LOCK_PATH)   # held until this process exits
if lock is None:
    sys.exit(f'another lake writer holds {LOCK_PATH}: wait for it and run this again')
from pyspark.sql import functions as F
import wire
from ingest import KAFKA_BROKERS, RAW_TABLE
from spark_conf import lake_session
t, p, first, last = os.environ['T'], int(os.environ['P']), int(os.environ['FIRST']), int(os.environ['LAST'])
s = lake_session('k2-lake-reread')
df = (s.read.format('kafka').option('kafka.bootstrap.servers', KAFKA_BROKERS)
      .option('assign', json.dumps({t: [p]}))
      .option('startingOffsets', json.dumps({t: {str(p): first}}))
      .option('endingOffsets', json.dumps({t: {str(p): last + 1}}))
      .option('includeHeaders', 'true').option('failOnDataLoss', 'true').load())
(df.select('topic', 'partition', 'offset', F.col('timestamp').alias('kafka_ts'),
           F.lit(datetime.now(timezone.utc)).alias('ingest_ts'), 'key',
           F.expr(wire.schema_id_guarded_expr('value')).alias('schema_id'),
           F.col('value').alias('payload'),
           F.map_from_entries(F.expr('transform(headers, h -> struct(h.key AS key, h.value AS value))')).alias('headers'))
   .writeTo(RAW_TABLE).option('snapshot-property.k2.job', 'operator').append())
s.stop()"
# Then step 3 again: it must return no rows.
```

```bash
# 6. Gone: record the hole so the nightly offset_continuity nets it rather than failing
#    on it every night. The detail must carry the offsets.GAP_OFFSETS wording, which is
#    what offsets.recorded_gaps parses: "committed <first_missing>, broker LOG-START
#    <last_missing + 1>". NOT YET RUN. <!-- VERIFY: run after a real occurrence -->
docker exec k2-spark-iceberg python3 /home/iceberg/lake/record_check.py \
  --job operator --check offset_gap --scope "$T/$P" --observed <records> --failed \
  --detail "batch audit hole, records evicted before a re-read: committed <first_missing>, broker LOG-START <last_missing + 1>"
```

7. Unpause what steps 2 and 5 paused (`docker exec k2-airflow-scheduler airflow dags
   unpause lake_ingest`, and the same for `lake_maintenance`), confirm the next run logs
   `stage 1: batch audit 0 failure(s)`, and append the incident below.

**Measured**, not yet: no batch has failed the audit. The audit's own cost on every run is
the `stage 1: batch audit ... in <s>s` log line. <!-- VERIFY: record the first runs' figure
here and in docker/lake/README.md -->

---

## 2. The audit itself could not run

**Symptom**, one `batch_offsets` row with scope `lake.raw.messages`, `observed = -1`, and a
detail beginning `check raised`; the same text on the task's stderr. The prefix is the one
the nightly audit uses for a check that raised (`offsets.CHECK_RAISED`), so the governance
view `dq_results` shows the row as `error`, not `fail`.

**Detection**, `LakeIngestBatchAuditFailed`, as above.

**Expected behaviour**, the batch is committed and **unverified**, not known bad. The ingest
carried on to stage 2. `offset_continuity` checks the whole table at 03:00 and is the
backstop for any batch the audit could not read.

**Recovery**, read the detail (step 1 above). A catalog or object-store error clears with
that outage ([lake-recovery.md](./lake-recovery.md)); a Python error is a bug in
`docker/lake/ingest.py` `audit_batch`. Run steps 3 and 4 above for any partition the run
touched if the nightly audit is more than a few hours away.

**Measured**, not yet.

---

## Failure modes / incidents

None yet. Append each occurrence with its date, the scopes, the ranges and whether the
records were re-read or recorded as gone.

---

**Last verified:** 2026-11-25 UTC, read-only steps 1 to 4, against the live stack at
`f7a362c` with this change not yet deployed.
