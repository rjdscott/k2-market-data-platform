# Runbook: Lake ingest is behind

The lake ingest runs every 5 minutes and reads Redpanda by offset range into
`raw.messages`, then decodes into `bronze.*`. This covers the tier falling behind: a
backlog, a stalled scheduler, an ingest that fails on data Redpanda has already evicted,
and a missed nightly rewrite leaving files too small to be useful.

It does **not** cover a crashed run (that is safe and automatic , 
[lake-recovery.md §5](./lake-recovery.md#5-ingest-killed-mid-run)) or a failing audit
([lake-audit-failed.md](./lake-audit-failed.md)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **§3 was run end to end on 2026-08-26 and its every command and number is real.** The
> rest of this page is not yet verified, the Phase D burn-in fills it in. Every flag below
> is read from the ingest's argparse; commands marked ✅ were run against the running stack
> on 2026-08-26, read-only except where §3 says otherwise.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Ingest lag above the 5-minute cadence | < 30 min | not yet verified, Phase D burn-in |
| 2 | Scheduler stopped, no runs at all | < 15 min | not yet verified, Phase D burn-in |
| 3 | `failOnDataLoss`, offsets point below broker retention | **a decision, then < 15 min** | **12 min, measured 2026-08-26**, detection to a green schedule (§3) |
| 4 | Nightly rewrite missed, small files accumulating | < 24 h (next maintenance window) | not yet verified, Phase D burn-in |
| 5 | A run exited 75, the ingest lock held: a skipped task under Airflow | **nothing to repair** | n/a, the lock working is not an incident |

---

## Why lag is annoying rather than dangerous: up to a point

The archive's position lives in the Iceberg snapshot summary, so a late run reads the same
range a punctual one would have
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). Falling behind costs
freshness, not correctness.

**The deadline is broker retention, and it is the number to hold in your head:** raw topics
keep **48 h** and a **2 GiB-per-partition** byte cap (`docker/redpanda/init.sh`), whichever
binds first, an open question until the Phase D burn-in measures it. Past that, unread
records are evicted and are gone permanently; public WebSocket feeds do not replay. So lag
is a countdown, and the only question that matters while triaging is *how much of the 48 h
is left*.

---

## 1. Ingest lag above cadence

**Symptom**, the newest row in `lake.raw.messages` is minutes-to-hours old; notebooks and
the lake dashboard show a gap at the right-hand edge.

**Detection**, `LakeIngestLagHigh` from `docker/prometheus/rules/lake-alerts.yml`, over
`time() - k2_lake_max_kafka_ts_seconds` (newest `kafka_ts` committed vs now). Its companion
is `time() - k2_lake_last_commit_ts_seconds{table="raw.messages"}` (wall-clock age of the
last commit). The two say different things and both matter: lag high with commit age *low*
means runs are happening and not keeping up; lag high with commit age *high* means runs are
not happening, go to §2.

Both are exported as **timestamps** and aged in PromQL, never as pre-computed ages
(`docker/lake/metrics.py`). An age gauge is only recomputed on a *successful* catalog read,
so it would freeze at its last small value during exactly the outage it is the backstop
for; a timestamp ages by itself. Any query you write here takes the `time() - …` form.

**Expected behaviour**, a single slow cycle self-corrects, because the next run reads a
wider offset range. Sustained lag does not: at 5-minute cadence, a run that takes longer
than 5 minutes never catches up on its own.

**Recovery**

```bash
# 1. Which is it: slow runs, or no runs?                                  ✅ verified
curl -s localhost:9090/api/v1/alerts | \
  jq -r '.data.alerts[] | "\(.labels.alertname) \(.labels.severity) \(.activeAt)"'
#   CaptureFeedStale critical 2026-08-26T...   (the only alert firing on 2026-08-26)

# Lag (label-free: raw.messages is the only table with a Kafka watermark), then
# commit age per table.                                       not yet run: Phase D
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_max_kafka_ts_seconds' | \
  jq -r '.data.result[].value[1]'

curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_last_commit_ts_seconds' | \
  jq -r '.data.result[] | "\(.metric.table) \(.value[1])"'
```

```bash
# 2. How much of the 48 h buffer is left? Compare the committed offsets against
#    what the broker still holds.                                          ✅ verified
docker exec k2-redpanda rpk topic describe -p market.crypto.v3.raw.binance
# LOG-START-OFFSET vs HIGH-WATERMARK per partition. If LOG-START has passed the
# offsets in the newest ingest snapshot, data has already been lost: go to §3.
```

```sql
-- 3. Where did the last successful run get to?      not yet run, Phase D burn-in
SELECT snapshot_id, committed_at,
       summary['k2.kafka-offsets'] AS offsets,
       summary['added-records']    AS rows
FROM lake.raw.messages.snapshots
ORDER BY committed_at DESC LIMIT 5;
```

```bash
# 4. Is the Spark container being starved, or is the batch simply large?
docker stats --no-stream k2-spark-iceberg                                 # ✅ verified
docker logs k2-spark-iceberg --tail 200 | grep -iE 'error|exception|offset|records'
```

Then read the cause:

| What you see | Meaning | Do |
|---|---|---|
| Runs completing, each slower than 5 min | the backlog is larger than one cycle can drain | it is already draining, every run is bounded at `--max-offsets-per-partition` (600,000). Watch `k2_lake_ingest_backlog_offsets{topic}` fall; raise the bound on a hand-run to drain faster |
| Backlog gauge flat or rising across runs | the bound is below the arrival rate on that topic | raise `--max-offsets-per-partition` on hand-runs until it falls, then raise `K2_LAKE_MAX_OFFSETS_PER_PARTITION` for the scheduled path. This is `LakeIngestBacklogSustained`, below |
| Runs completing fast, lag flat | the scheduler is not firing at cadence | §2 |
| Spark at its CPU limit during a run | contention with capture or ClickHouse | check the cpuset layout, capture is pinned away from Spark deliberately ([Phase D isolation experiment](../plans/2026-08-26-v3-quant-research-platform/003-phase-d-lake-tier.md)) |
| `failOnDataLoss` in the logs | offsets below broker retention | §3, **stop and read it before doing anything** |

```bash
# What is left, per topic, as of the last commit: read it before doing anything.
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode 'query=k2_lake_ingest_backlog_offsets' \
  | jq -r '.data.result[] | "\(.metric.topic) \(.value[1])"' | sort

# Drain faster than the 600,000-per-partition default, with someone watching.
# Peak driver RSS does not move with the bound (docker/lake/README.md), but wall
# time does, and the flow's INGEST_TIMEOUT_S is 3600.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py \
  --max-offsets-per-partition 500000

# The same run under Airflow (ADR-034), which takes the DAG's only slot, so no scheduled
# run starts beside it, and can raise the driver heap too (airflow-scheduling.md §4).
# Unset params keep the script's defaults. Run on the live stack 2026-11-25 with
# '{"driver_memory": "2g", "max_offsets_per_partition": 1000000}': the run's `running:`
# line carried --env=K2_LAKE_DRIVER_MEMORY=2g and --max-offsets-per-partition=1000000, and
# it logged `stage 1: committing <n> offsets` and ended success. The 500000 (counts-ok: a configured per-run bound) form below
# was not run.
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest \
  --conf '{"max_offsets_per_partition": 500000}'

# Or bound by time instead of by count: resolved to offsets on the broker, so
# the window means the same thing on every partition.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py \
  --end-timestamp '2026-08-26T10:00:00Z'
```

**Do not raise the ingest cadence to catch up.** Runs are deployed at concurrency 1 for
correctness ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)); a faster
schedule against a concurrency-1 deployment queues runs rather than parallelising them, and
raising the concurrency is the one change that breaks the exactly-once argument. Raise the
per-run bound instead, one bigger run, not two overlapping ones.

**Measured, 2026-08-27**, draining the cold-start backlog at the default
bound, `lake-ingest-5min` paused: three runs of 92 s / 57 s / 49 s (what each committed is
in [ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)'s Outcome), peak ingest driver RSS 1,243 MiB of a 4 GiB container. A run with
nothing to do costs 5.7 s. The `market.crypto.v3.raw.kraken` backlog fell on each of
the three.

---

### `LakeIngestBacklogSustained`: a topic's backlog held above 0 for 30 minutes

A topic has had unread offsets, as of the last commit, for six cycles. Every run commits
and the lake looks healthy; the cap is simply below what a partition receives per cycle.
On 2026-11-25 `market.crypto.v3.raw.kraken` held a backlog from 14:32 to 17:12 UTC at a cap of
200,000 <!-- counts-ok: the configured per-run cap, not data --> with arrivals just under it per
cycle (runs had slowed to ~250 s), with no alert.

```bash
# 1. Is the start offset behind the read position? Then nothing was evicted, drain it.
#    If LOG-START-OFFSET is past the committed offset, stop: go to §3.
docker exec k2-redpanda rpk topic describe market.crypto.v3.raw.kraken -p

# 2. Drain it. Run on the live stack 2026-11-25 and drained 1.3 M to 0 (17:12 to 17:25 UTC).
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest \
  --conf '{"max_offsets_per_partition": 2000000}'

# 3. Confirm the gauge is 0, then compare the arrival rate with the default (600,000).
#    Raise K2_LAKE_MAX_OFFSETS_PER_PARTITION if a cycle's arrival is within ~2x of it.
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode 'query=k2_lake_ingest_backlog_offsets' \
  | jq -r '.data.result[] | "\(.metric.topic) \(.value[1])"' | sort
```

---

## 2. Scheduler stopped

**Symptom**, `time() - k2_lake_last_commit_ts_seconds{table="raw.messages"}` climbing
steadily, no failed runs, no errors anywhere. The quietest failure on this page.

**Detection**, `LakeIngestFailed` from `docker/prometheus/rules/lake-alerts.yml`: it is the
rule over `raw.messages` commit age, and a stopped scheduler is exactly a `raw.messages`
commit that never lands. **Not `LakeCommitAgeHigh`**, that rule selects
`table=~"bronze\\..*"` and is structurally blind to a `raw.messages` stall. If the metrics
exporter itself is what is gone, `LakeExporterDown` fires instead; if it is up but no longer
refreshing (the usual shape of a Lakekeeper outage), `LakeExporterStalled` fires first , 
both are [lake-recovery.md](./lake-recovery.md).

**Expected behaviour**, nothing recovers on its own. A paused DAG stays paused, and a stale
DAG folder stays stale ([airflow-scheduling.md §5](./airflow-scheduling.md#5-lake_ingest-not-running-a-stale-dag-folder-or-a-forgotten-pause)).

**Recovery**. The lake schedules are Airflow's since the cutover (ADR-034); a scheduler
that has stopped heartbeating is [airflow-scheduling.md §1](./airflow-scheduling.md#1-scheduler-heartbeat-lost).

Run on the live stack 2026-11-25 (read-only): `dags list` showed `lake_ingest`,
`lake_maintenance` and `lake_rebuild`, all `is_paused` `False`; `details` showed `is_stale`
`False`; and a `manual__` run from the trigger ended `success` (the catch-up above).

```bash
# 1. Do the DAGs exist, unpaused and not stale?
docker exec k2-airflow-scheduler airflow dags list
docker exec k2-airflow-scheduler airflow dags details lake_ingest | grep -E 'is_stale|is_paused|next_dagrun'

# 2. Is the scheduler up, and creating runs?
docker compose ps airflow-scheduler airflow-dag-processor
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest | head -5

# 3. Unpause (only what nobody paused on purpose) and kick one run.
docker exec k2-airflow-scheduler airflow dags unpause lake_ingest
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest
```

**If the exporter is down but ingest is fine**, the lag metric is stale rather than the lag
being real, check `docker/lake/metrics.py` and the Prometheus target list before chasing
an ingest problem that does not exist:

```bash
curl -s localhost:9090/api/v1/targets | \
  jq -r '.data.activeTargets[] | "\(.labels.job) \(.health)"' | sort -u    # ✅ verified
#   capture-binance up / clickhouse up / lake-metrics up / redpanda up ...
```

**Measured**, not yet verified.

---

## 3. `failOnDataLoss`: the offsets point below what the broker holds

**Symptom**, the ingest run fails immediately with a data-loss error naming a topic and
partition. It does not retry into success.

**Detection**, the `lake_ingest` task fails (`AirflowTaskFailed`), then `LakeIngestFailed`
from `docker/prometheus/rules/lake-alerts.yml`.

**Expected behaviour, this is the alert working, and it must not be worked around.**
`failOnDataLoss=true` is set deliberately: the alternative is Spark silently skipping to
the earliest surviving offset, which produces an archive with an **unrecorded hole**, the
one outcome the whole v3 design exists to prevent
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). A loud failure is the
correct behaviour; the recovery is a human decision, and its deliverable is a record, not a
restart.

**Recovery, the whole procedure, run end to end on 2026-08-26** under Prefect. ✅ verified
for the ingest steps (3 and 4); steps 1, 2 and 5 read and pause Airflow since the cutover
(2 and 5 ran on 2026-11-25; 1 awaits a failed ingest).

Step 1's log command is not yet exercised under Airflow: it needs a failed ingest that
hit `DATA LOSS`, and none has occurred since the cutover.

```bash
# 1. Establish exactly what was lost. The ingest already did: `offsets.evicted`
#    compares the resume point against the broker's log start BEFORE Spark
#    starts, so the numbers are the first lines of the failed task's log.
docker exec k2-airflow-scheduler sh -c \
  "find /opt/airflow/logs -path '*dag_id=lake_ingest*' -name '*.log' | sort | tail -1 | xargs grep 'DATA LOSS'"
#   stage 1: DATA LOSS market.crypto.v3.raw.kraken/0: committed <committed>,
#            broker LOG-START <log-start>, <n> records evicted   (2026-08-26, Prefect's log; values here are placeholders)

#    The broker's own view, for a second opinion and for the partitions that are
#    close to it but not over yet.
docker exec k2-redpanda rpk topic describe -p market.crypto.v3.raw.kraken
#   PARTITION  LOG-START-OFFSET  HIGH-WATERMARK
#   0          <log-start>       <high-water>     <- committed below LOG-START: gone
#   6          <log-start-6>     <high-water-6>   <- committed above LOG-START: still safe
```

Run on the live stack 2026-11-25: the same pause printed `lake_ingest | False` (its old
state) at the cutover's step 2, and `make lake-verify` paused and resumed the DAG. The
`pgrep` wait printed nothing at the end of the cutover's step 3.

```bash
# 2. Pause the DAG. The repair must not race a cron run: the flock would refuse
#    the second one, but a refused run is noise, and the repair wants a quiet
#    stack to read its own output in. Then wait out a run already in flight.
docker exec k2-airflow-scheduler airflow dags pause lake_ingest
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance|rebuild)\.py'   # until it prints nothing
```

```bash
# 3. Accept the loss, explicitly, once. The flag is the whole decision.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --accept-data-loss
#   stage 1: DATA LOSS market.crypto.v3.raw.kraken/0: committed <committed>,
#            broker LOG-START <log-start>, <n> records evicted
#   stage 1: ACCEPTED DATA LOSS market.crypto.v3.raw.kraken/0: <n> records
#            (<committed>..<log-start - 1>) recorded in lake.audit.checks as offset_gap,
#            resuming at <log-start>
#   stage 1: committing <m> offsets
#   stage 1: <m> rows -> lake.raw.messages (max kafka_ts 2026-08-26 21:49:03.378)
```

**What that flag does, exactly.** For every partition whose committed offset is below
the broker's log start it writes one `offset_gap` row into `lake.audit.checks` , 
`job='ingest'`, `passed=false`, `observed` = the record count, `detail` = the topic, the
partition, both offsets and the Spark application id of the run that made the call, and
*then* advances that partition's start to the log start. Every other partition is left
exactly where it was committed. **The record is written first and a record that cannot be
written aborts the run**, skipping nothing: the two live in different Iceberg tables and
so cannot be one commit, and the only failure direction worth having is a recorded gap
that was not skipped. The reverse is an unrecorded hole, which is the outcome
[ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md) exists to prevent.

There is no environment variable and no default, deliberately. The scheduled run keeps
failing until a person types the flag.

```bash
# 4. Read the record back. This is the deliverable: the run log scrolls away.
docker exec k2-spark-iceberg python3 -c "
import sys; sys.path.insert(0, '/home/iceberg/lake')
from spark_conf import CATALOG, lake_session
s = lake_session('k2-lake-audit-read')
s.sql(f'SELECT run_ts, job, check_name, scope, observed, detail FROM {CATALOG}.audit.checks '
      f\"WHERE check_name = 'offset_gap' ORDER BY run_ts\").show(50, truncate=False)"
#   2026-08-26 21:48:59.632824 | ingest | offset_gap | market.crypto.v3.raw.kraken/0
#   | <n> | --accept-data-loss: ... committed <committed>, broker LOG-START <log-start>,
#     <n> records evicted by Redpanda retention and permanently gone;
#     resumed at <log-start> by run local-1787780940821
```

Run on the live stack 2026-11-25: the unpause (cutover step 5) printed `lake_ingest | True`
and the run for the 06:06 slot ended `success`.

```bash
# 5. Resume, and confirm the next run is green. The unpause runs the latest slot at once.
docker exec k2-airflow-scheduler airflow dags unpause lake_ingest
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest | head -3
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run lake_ingest <run_id>   # ingest | success
```

6. **Append the incident below**, with the offsets and the wall-clock window. The
   `audit.checks` row is the machine-readable record; this page is where the *why* lives.

**What fires while this is happening.** `LakeOffsetGap` (warning), the ingest stamps the
number of repaired partitions into the commit as `k2.offset-gaps` and
`k2_lake_offset_gaps_total` reads it. It is a **pulse, not a condition**: the row is filed
once, by the one repairing run, so the gauge ages out after 15 minutes exactly the way
`k2_lake_unresolvable_schema_ids_total` does and the alert resolves with nothing having
been fixed. That is intended, **the `offset_gap` row in `lake.audit.checks` is the
durable record**, and the alert only exists so that a permanent hole accepted at a
keyboard is not visible solely in a terminal that has since been closed.

> It did **not** fire on 2026-08-26, and the reason is worth knowing before trusting it:
> `lake-metrics` is a long-lived process that imports `metrics.py` once, and this one had
> been running since before the gauge existed. It served neither `k2_lake_offset_gaps_total`
> nor `k2_lake_ingest_backlog_offsets`, both appear on its next restart
> (`docker compose up -d lake-metrics`). **After changing `docker/lake/metrics.py`, restart
> the exporter or its new gauges are simply absent**, which reads on a dashboard exactly
> like "the condition is not happening". The backlog was read straight off the snapshot
> summary instead, which is where the gauge gets it: `k2.kafka-backlog` on the newest
> `k2.job=ingest` snapshot of `raw.messages`.

**And the nightly audit will fail from here on, correctly.** `offset_continuity` groups
`raw.messages` by (topic, partition) and reports `max - min + 1 - count`, which for a
repaired partition is exactly the number of evicted records, forever, because the hole is
permanent. Measured immediately after the repair above: partition 0's `max - min + 1 - count`
came out at **the same number as the
`offset_gap` row** (the count is in [ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)'s Outcome). Reconcile the two before treating a `LakeAuditFailed` on
`offset_continuity` as new: same scope, same count, means it is this hole and not another.

```sql
-- The reconciliation query. A continuity failure whose count matches a recorded
-- offset_gap for the same scope is this incident; anything else is not.
SELECT scope, observed, detail FROM lake.audit.checks
WHERE check_name IN ('offset_gap', 'offset_continuity') ORDER BY scope, run_ts;
```

**Then fix the cause, which is always one of two things:** ingest was down or too slow for
longer than retention (§1, §2, the countdown ran out), or retention is too short for the
real message rate and the 512 MiB-per-partition byte cap is binding well inside 48 h. The
second is a capacity finding, not an incident: raise the disk slice or lower the partition
count, **never** silently shorten retention, which `docker/redpanda/init.sh` says in as
many words.

**Measured, 2026-08-26, this happened, and the output above is its own, counts and offsets
as placeholders.** The Phase D
cold start drained `market.crypto.v3.raw.kraken` at 50,000 offsets per run while partition <!-- counts-ok: 50,000 is the configured per-run bound, not data -->
0 received more than that per cycle (its rate is in ADR-021's Outcome), and the 512 MiB-per-partition cap evicted the head of the
queue faster than the ingest read it. Every cron run failed at plan time from **21:36Z**:

```text
stage 1: DATA LOSS market.crypto.v3.raw.kraken/0: committed <committed>,
         broker LOG-START <log-start>, <n> records evicted
failOnDataLoss: 1 partition(s) are below broker retention and <n> records are
permanently gone. This is not retried into success, record the gap in
lake.audit.checks and resume explicitly with --accept-data-loss:
docs/runbooks/lake-ingest-lag.md §3.
```

Repaired with `--accept-data-loss` at **21:48:59Z**, one partition; the
schedule was paused 21:48:38Z and resumed 21:50:56Z, and the run itself took **77 s**
(21:48:59 → 21:50:16Z) including its rows into `raw.messages` and the stage-2 decode.
**MTTR from detection to a green schedule: 12 minutes**, of which the repair was 77 s and
the rest was reading. That is the number in the table above.

The two cron runs after it were green and the backlog fell on both, which is what says the
repair worked rather than merely exited 0, `k2.kafka-backlog` for
`market.crypto.v3.raw.kraken` on the newest `k2.job=ingest` snapshot of `raw.messages`,
which is exactly what `k2_lake_ingest_backlog_offsets` exports:

| Snapshot | `raw.kraken` backlog | Run |
|---|---|---|
| 21:50:06Z | the starting point | the `--accept-data-loss` repair |
| 21:56:58Z | lower | first scheduled run after it, Completed in 66 s |
| 22:01:04Z | lower again | second, Completed in 60 s |

Three things changed as a result, and none of them is a workaround.
`offsets.evicted` compares the resume point against the broker's log start **before** the
Spark job starts, so step 1 arrives as the first line of the failure instead of as an
`OffsetOutOfRangeException` 384 lines into a stack trace naming one partition and no
counts. The per-run bound then defaulted to 200,000 (600,000 since 2026-11-25), above the measured arrival rate, which
50,000 was not, because a bound below the arrival rate guarantees this outcome rather
than merely risking it (`docker/lake/README.md`). And `--accept-data-loss` exists, so the
step this section used to describe in prose is a command that files the record itself.

**The cause here was the second of the two above**: the byte cap binding well inside 48 h
on one hot partition. It is a capacity finding, measured on 2026-08-26, that partition
holds **7.0 h** of records at the 512 MiB cap, not 48 ([capacity-model.md
§4b](../architecture/15-capacity-model.md)):

```console
$ docker exec k2-redpanda rpk topic describe -p market.crypto.v3.raw.kraken   # ✅ verified
#   partition 0: LOG-START <log-start>, HIGH-WATERMARK <high-water>
$ docker exec k2-redpanda rpk topic consume market.crypto.v3.raw.kraken -p 0 \
    -o <log-start> -n 1 -f '%d\n'                                                # ✅ verified
1787755113319
$ docker exec k2-redpanda rpk topic consume market.crypto.v3.raw.kraken -p 0 \
    -o <high-water> -n 1 -f '%d\n'                                             # ✅ verified
1787780340593
#   25,227,274 ms = 7.01 h between the two (the record count: ADR-021, Outcome)
```

**512 MiB stands. The bus is a buffer for the 5-minute ingest cadence; the lake is the
archive.** Seven hours is ~84 ingest cycles of slack, and the fix for a lake that cannot
keep up is a lake that keeps up. **Revisit the retention when
`k2_lake_ingest_backlog_offsets` for any topic exceeds one hour of that topic's own
arrival rate for two consecutive cycles**, for `raw.kraken` that is one hour of the
hottest partition's arrival on that partition, and the topic gauge sums twelve of
them. Two cycles, because one is a slow run and two is a trend. That is the trigger; below
it, a rising backlog is §1, not a retention question.

*2026-11-25:* raised to 2 GiB per partition after a second eviction; see `docker/redpanda/init.sh`.

This scenario is deliberately not in `make chaos`: inducing it means waiting out real
retention or destroying a topic, and both prove something other than the fault.

---

## 4. The nightly rewrite has not run: small files accumulating

**Symptom**, queries getting slower without getting wrong. `k2_lake_files_total` climbing
faster than `k2_lake_rows_total`, `k2_lake_avg_file_bytes` falling.

**Detection**, `LakeCompactionStale` from `docker/prometheus/rules/lake-alerts.yml`:
`time() - k2_lake_last_compaction_ts_seconds{table="raw.messages"} > 129600` <!-- counts-ok: an alert threshold in seconds -->, `for: 30m`.
Its annotation points at [lake-recovery.md](./lake-recovery.md); the diagnosis is here.

**Read what that rule measures, because it is not what the name suggests.** It fires on the
compaction **job**, 36 hours with no file-rewrite snapshot on `raw.messages`, i.e. at least
one missed nightly, not on mean file size. `rewrite_data_files` does not go through
`writeTo` and so cannot carry a `k2.job` property; `docker/lake/metrics.py` keys on
Iceberg's own `operation` field (`replace`/`overwrite`) instead.

The obvious alternative, `k2_lake_avg_file_bytes{table="raw.messages"} < 32MiB`, was
rejected because **it fires by construction on a healthy table for about the first 15
days**: 288 cycles/day × 9 topics under `write.distribution-mode=hash` is ~2,592 new ~2.5 MB
files a day, and a table-wide *mean* cannot clear 32 MiB until the compacted archive
outweighs them. The capacity model puts the disk runway at ~26 days, so that alert would
have been noise for 15 of them, which is how an alert gets muted. `k2_lake_avg_file_bytes`
and `k2_lake_files_total` are still the right things to *read* here; they are the symptom,
and `LakeCompactionStale` is the cause. No series exists at all until the table has been
compacted once, so a fresh table cannot fire it either.

**Expected behaviour**, the 5-minute cadence writes ~288 commits per table per day, each
one small, and nightly compaction is what converges them toward the target (256 MB for
`raw.messages`, 128 MB for `bronze.*` , 
[partitioning-strategy.md](../architecture/14-partitioning-strategy.md)). If maintenance ran,
this self-corrects overnight. If it did not, nothing corrects it.

**Recovery**

```sql
-- 1. Files and average size per partition.        not yet run, Phase D burn-in
SELECT partition, count(*) AS files,
       round(avg(file_size_in_bytes) / 1048576, 1) AS avg_mb
FROM lake.raw.messages.files GROUP BY partition ORDER BY partition DESC LIMIT 10;
-- Flag any settled partition averaging under 10 MB per file.
```

```bash
# 2. Did maintenance run last night? (Airflow since the cutover.)
#    Run on the live stack 2026-11-25: it listed `scheduled__2026-11-25T03:00:00+00:00`
#    with `success`.
docker exec k2-airflow-scheduler airflow dags list-runs lake_maintenance | head -5

# 3. Compact now rather than waiting. --days N is the compaction window, days
#    back; the nightly default is 2. Widen it to cover every night that was
#    missed: anything older was compacted by an earlier run and rewriting the
#    whole archive nightly costs without bound for no gain.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --days 3
```

`--days` is the only compaction flag. `maintenance.py` accepts `--days`, `--retain-days`,
`--orphan-hours` and `--audit-only`, and argparse exits 2 on anything else, a full run does
compaction, then expiry, then orphan removal, then the audits.

**Sort-order degradation is the second-order cost, and it is the one that is easy to miss.**
`bronze.*` prune by symbol through the sort order rather than a partition field
([ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md)), so unsorted small files
widen every file's symbol bounds and single-instrument queries stop pruning. Compaction on
`bronze.*` must therefore be a **sort rewrite**, not a plain binpack, a binpack merges the
files and leaves the clustering as bad as it found it. `compact()` does exactly that:
binpack on `raw.messages`, which is already written in offset order, and a sort rewrite on
both bronze tables using the sort order declared in `lake.sql`.

**Measured**, not yet verified.

---

## 5. A run exited 75: the ingest lock held

> **Since the Airflow cutover (2026-11-25)** a run that exits 75 is a *skipped* task in a
> successful `lake_ingest` run, not a failed one, and three skips in a row with no
> maintenance or rebuild running fire `AirflowLakeIngestSkippedRepeatedly`: the procedure for
> that is [airflow-scheduling.md section 3](./airflow-scheduling.md#3-lake_ingest-skipped-three-runs-in-a-row).
> What follows is why the lock exists, written when Prefect ran the ingest.

**Symptom**, under Prefect, one `lake-ingest-5min` flow run is Failed in the Prefect UI, its logs end in

```
ingest.py exited 75 after 3s
another ingest holds /tmp/k2-lake-ingest.lock, refusing to run a second one.
Two concurrent appends both commit and both write offsets; see LOCK_PATH above.
```

and the next scheduled run five minutes later succeeds normally.

**Detection**, none for one refused run, and there should be none: it costs one cycle of
freshness, an order of magnitude inside `LakeIngestFailed`'s 30-minute threshold. Three in a
row is `AirflowLakeIngestSkippedRepeatedly` (the note above).

**Expected behaviour, this is the lock doing its job, and the correct response is
nothing.** `main()` in `docker/lake/ingest.py` takes a non-blocking exclusive `flock` on
`/tmp/k2-lake-ingest.lock` before it opens a Spark session, and exits 75 rather than
queueing if another process holds it. Two concurrent ingests would both read the same
committed offsets from the last snapshot summary and both append the same records, so
concurrency is the one thing that breaks the exactly-once argument
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). The deployment's
`concurrency_limit=1` only gates runs *Prefect* launched; the runbooks, the chaos scripts
and `make lake-verify` all `docker exec` an ingest directly, and the flock is the guard
that covers every path.

The ordinary causes are all benign: a hand-run ingest during triage that overlapped a
scheduled cycle, a chaos script (each pauses the schedule for its duration precisely to
avoid this), `make lake-verify`, or a long backlog slice still running when the next cycle
fires. **The run that exited 75 wrote nothing**, the lock is taken before the Spark session
so there is nothing to clean up and nothing to replay.

**Recovery**, only if it repeats. A run refused every cycle means a holder that never
exits:

```bash
# 1. Who holds it?                                  not yet run: Phase D burn-in
docker exec k2-spark-iceberg fuser /tmp/k2-lake-ingest.lock
docker exec k2-spark-iceberg ps -o pid,etime,cmd -p "$(
  docker exec k2-spark-iceberg fuser /tmp/k2-lake-ingest.lock 2>/dev/null)"

# 2. A genuinely stuck run: hours of elapsed time, no progress in its log, is the
#    killed-mid-run case, which is safe: kill it and let the next cycle resume.
#    lake-recovery.md §5 is the same procedure.
docker exec k2-spark-iceberg pkill -9 -f /home/iceberg/lake/ingest.py
docker exec k2-spark-iceberg pkill -9 -f org.apache.spark.deploy.SparkSubmit
```

**Do not delete the lock file.** `flock` is held on the open file descriptor, not on the
path, so unlinking it does not release anything, it just lets the next run take a *fresh*
lock on a new inode and start the second concurrent ingest the lock exists to prevent.

**Measured**, not yet verified. Exercised against a throwaway `scratch` namespace: two
concurrent appends duplicate without the lock and the second run exits 75 with it.

---

## Failure modes / incidents

_Appended with their date as they happen; never overwritten. **Every §3 data-loss window is
recorded here**, with its offsets and its wall-clock span._

**2026-08-26, `market.crypto.v3.raw.kraken/0`, ~1 h 40 m of the feed** (the record count and
both offsets are in [ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)'s Outcome).
The Phase D cold start ran with `--max-offsets-per-partition 50000` against a partition
receiving more than that per cycle, so the ingest fell behind the 512 MiB-per-partition cap and
Redpanda evicted the head of the queue. The committed offset fell below the broker's LOG-START.
**The lost window ends at 14:38:33Z**, the Kafka timestamp of the surviving log start
(`rpk topic consume -p 0 -o <log-start> -n 1 -f '%d'` = 1787755113319). Where it *starts*
cannot be read back, because reading it would mean reading a record that is gone: at the
measured arrival rate it is ~100 minutes earlier, **≈12:58Z**, and that estimate is
the only version of that boundary there will ever be. Every scheduled run failed at plan
time from 21:36Z. Repaired at 21:48:59Z with
`ingest.py --accept-data-loss`; `lake.audit.checks` holds the `offset_gap` row, and
`offset_continuity` reports the same count on that partition from now on. No other
partition on any topic was below its log start, partition 6 was the next closest, its
committed offset still above its log start.

Cause: the per-run bound was below the arrival rate, which was fixed at a default of
200,000 (~3.6x measured; raised to 600,000 on 2026-11-25, §1). Not a retention change: 512 MiB stands, and the trigger for
revisiting it is in §3.

---

## Related

- [ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md), why lag is safe, why `failOnDataLoss` is loud, why concurrency 1 is a correctness setting
- [ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md), why a lost window has to be recorded rather than absorbed
- [lake-recovery.md](./lake-recovery.md), a killed run, Lakekeeper down, MinIO down
- [lake-audit-failed.md](./lake-audit-failed.md), when the continuity audit finds the hole instead of the ingest job
- [`../architecture/14-partitioning-strategy.md`](../architecture/14-partitioning-strategy.md), target file sizes and the sort-order pruning that §4 protects
- [`docker/redpanda/init.sh`](../../docker/redpanda/init.sh), the 48 h / 2 GiB retention arithmetic and why it is deliberately unresolved

---

**Last verified:** §3 in full on **2026-08-26**, pause, `--accept-data-loss`, the audit
row, resume, and the next green cron run, against the live stack with its real output
pasted. §§1, 2, 4, 5 are not yet verified: their commands are read from the code and the
commands marked ✅ were run read-only on 2026-08-26. Stamp the rest at the Phase D
burn-in.
