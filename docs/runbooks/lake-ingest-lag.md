# Runbook: Lake ingest is behind

The lake ingest runs every 5 minutes and reads Redpanda by offset range into
`raw.messages`, then decodes into `bronze.*`. This covers the tier falling behind: a
backlog, a stalled scheduler, an ingest that fails on data Redpanda has already evicted,
and a missed nightly rewrite leaving files too small to be useful.

It does **not** cover a crashed run (that is safe and automatic —
[lake-recovery.md §5](./lake-recovery.md#5-ingest-killed-mid-run)) or a failing audit
([lake-audit-failed.md](./lake-audit-failed.md)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Not yet verified — the Phase D burn-in fills this in.** The ingest job exists and every
> flag below is read from its argparse, but nothing here has been run against a populated
> lake. Commands marked ✅ were run read-only against the running stack on 2026-08-26.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Ingest lag above the 5-minute cadence | < 30 min | not yet verified — Phase D burn-in |
| 2 | Scheduler stopped — no runs at all | < 15 min | not yet verified — Phase D burn-in |
| 3 | `failOnDataLoss` — offsets point below broker retention | **investigation, not repair** | not yet verified — Phase D burn-in |
| 4 | Nightly rewrite missed — small files accumulating | < 24 h (next maintenance window) | not yet verified — Phase D burn-in |
| 5 | A flow run failed with `exited 2` — the ingest lock held | **nothing to repair** | n/a — the lock working is not an incident |

---

## Why lag is annoying rather than dangerous — up to a point

The archive's position lives in the Iceberg snapshot summary, so a late run reads the same
range a punctual one would have
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). Falling behind costs
freshness, not correctness.

**The deadline is broker retention, and it is the number to hold in your head:** raw topics
keep **48 h** and a **512 MiB-per-partition** byte cap (`docker/redpanda/init.sh`), whichever
binds first — an open question until the Phase D burn-in measures it. Past that, unread
records are evicted and are gone permanently; public WebSocket feeds do not replay. So lag
is a countdown, and the only question that matters while triaging is *how much of the 48 h
is left*.

---

## 1. Ingest lag above cadence

**Symptom** — the newest row in `lake.raw.messages` is minutes-to-hours old; notebooks and
the lake dashboard show a gap at the right-hand edge.

**Detection** — `LakeIngestLagHigh` from `docker/prometheus/rules/lake-alerts.yml`, over
`time() - k2_lake_max_kafka_ts_seconds` (newest `kafka_ts` committed vs now). Its companion
is `time() - k2_lake_last_commit_ts_seconds{table="raw.messages"}` (wall-clock age of the
last commit). The two say different things and both matter: lag high with commit age *low*
means runs are happening and not keeping up; lag high with commit age *high* means runs are
not happening — go to §2.

Both are exported as **timestamps** and aged in PromQL, never as pre-computed ages
(`docker/lake/metrics.py`). An age gauge is only recomputed on a *successful* catalog read,
so it would freeze at its last small value during exactly the outage it is the backstop
for; a timestamp ages by itself. Any query you write here takes the `time() - …` form.

**Expected behaviour** — a single slow cycle self-corrects, because the next run reads a
wider offset range. Sustained lag does not: at 5-minute cadence, a run that takes longer
than 5 minutes never catches up on its own.

**Recovery**

```bash
# 1. Which is it — slow runs, or no runs?                                  ✅ verified
curl -s localhost:9090/api/v1/alerts | \
  jq -r '.data.alerts[] | "\(.labels.alertname) \(.labels.severity) \(.activeAt)"'
#   CaptureFeedStale critical 2026-08-26T...   (the only alert firing on 2026-08-26)

# Lag (label-free — raw.messages is the only table with a Kafka watermark), then
# commit age per table.                                       not yet run — Phase D
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
# offsets in the newest ingest snapshot, data has already been lost — go to §3.
```

```sql
-- 3. Where did the last successful run get to?      not yet run — Phase D burn-in
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
| Runs completing, each slower than 5 min | the backlog is larger than one cycle can drain | it is already draining — every run is bounded at `--max-offsets-per-partition` (50,000). Watch `k2_lake_ingest_backlog_offsets{topic}` fall; raise the bound on a hand-run to drain faster |
| Backlog gauge flat or rising across runs | the bound is below the arrival rate on that topic | raise `--max-offsets-per-partition` on hand-runs until it falls, then raise `K2_LAKE_MAX_OFFSETS_PER_PARTITION` for the scheduled path |
| Runs completing fast, lag flat | the scheduler is not firing at cadence | §2 |
| Spark at its CPU limit during a run | contention with capture or ClickHouse | check the cpuset layout — capture is pinned away from Spark deliberately ([Phase D isolation experiment](../plans/2026-08-26-v3-quant-research-platform/003-phase-d-lake-tier.md)) |
| `failOnDataLoss` in the logs | offsets below broker retention | §3 — **stop and read it before doing anything** |

```bash
# What is left, per topic, as of the last commit — read it before doing anything.
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode 'query=k2_lake_ingest_backlog_offsets' \
  | jq -r '.data.result[] | "\(.metric.topic) \(.value[1])"' | sort

# Drain faster than the 50,000-per-partition default, with someone watching.
# Peak driver RSS does not move with the bound (docker/lake/README.md), but wall
# time does, and the flow's INGEST_TIMEOUT_S is 3600.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py \
  --max-offsets-per-partition 200000

# Or bound by time instead of by count — resolved to offsets on the broker, so
# the window means the same thing on every partition.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py \
  --end-timestamp '2026-08-26T10:00:00Z'
```

**Do not raise the ingest cadence to catch up.** Runs are deployed at concurrency 1 for
correctness ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)); a faster
schedule against a concurrency-1 deployment queues runs rather than parallelising them, and
raising the concurrency is the one change that breaks the exactly-once argument. Raise the
per-run bound instead — one bigger run, not two overlapping ones.

**Measured, 2026-08-27** — draining a 41.5 M-record cold-start backlog at the default
bound, `lake-ingest-5min` paused: 2,721,812 / 1,770,914 / 1,564,334 records committed in
92 s / 57 s / 49 s, peak ingest driver RSS 1,243 MiB of a 4 GiB container. A run with
nothing to do costs 5.7 s. `market.crypto.v3.raw.kraken` fell 23,175,551 → 22,193,385 over
the three, about 347 k per run.

---

## 2. Scheduler stopped

**Symptom** — `time() - k2_lake_last_commit_ts_seconds{table="raw.messages"}` climbing
steadily, no failed runs, no errors anywhere. The quietest failure on this page.

**Detection** — `LakeIngestFailed` from `docker/prometheus/rules/lake-alerts.yml`: it is the
rule over `raw.messages` commit age, and a stopped scheduler is exactly a `raw.messages`
commit that never lands. **Not `LakeCommitAgeHigh`** — that rule selects
`table=~"bronze\\..*"` and is structurally blind to a `raw.messages` stall. If the metrics
exporter itself is what is gone, `LakeExporterDown` fires instead; if it is up but no longer
refreshing (the usual shape of a Lakekeeper outage), `LakeExporterStalled` fires first —
both are [lake-recovery.md](./lake-recovery.md).

**Expected behaviour** — nothing recovers on its own. A paused deployment stays paused.

**Recovery**

```bash
# 1. Do the deployments exist, and are they scheduled?    not yet run — Phase D burn-in
docker exec k2-prefect-server prefect deployment ls
#   expect exactly two, both registered by docker/lake/flows/deploy_lake.py:
#   lake-ingest/lake-ingest-5min            cron 1-59/5 * * * *, concurrency 1
#   lake-maintenance/lake-maintenance-daily nightly
#   Both run on the `lake` work pool. Anything else listed here is a leftover from a
#   stack upgraded in place and can be deleted with `prefect deployment delete`.

# 2. Is a worker actually claiming runs?                                   ✅ verified
docker ps --filter name=k2-prefect-worker --format '{{.Names}}\t{{.Status}}'
#   k2-prefect-worker  Up 4 hours
```

```bash
# 3. Resume and kick one run.                       not yet run — Phase D burn-in
docker exec k2-prefect-server prefect deployment resume 'lake-ingest/lake-ingest-5min'
docker exec k2-prefect-server prefect deployment run    'lake-ingest/lake-ingest-5min'
```

**If the exporter is down but ingest is fine**, the lag metric is stale rather than the lag
being real — check `docker/lake/metrics.py` and the Prometheus target list before chasing
an ingest problem that does not exist:

```bash
curl -s localhost:9090/api/v1/targets | \
  jq -r '.data.activeTargets[] | "\(.labels.job) \(.health)"' | sort -u    # ✅ verified
#   capture-binance up / clickhouse up / lake-metrics up / redpanda up ...
```

**Measured** — not yet verified.

---

## 3. `failOnDataLoss` — the offsets point below what the broker holds

**Symptom** — the ingest run fails immediately with a data-loss error naming a topic and
partition. It does not retry into success.

**Detection** — the Prefect run fails → `LakeIngestFailed` from
`docker/prometheus/rules/lake-alerts.yml`.

**Expected behaviour — this is the alert working, and it must not be worked around.**
`failOnDataLoss=true` is set deliberately: the alternative is Spark silently skipping to
the earliest surviving offset, which produces an archive with an **unrecorded hole** — the
one outcome the whole v3 design exists to prevent
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). A loud failure is the
correct behaviour; the recovery is a human decision, and its deliverable is a record, not a
restart.

**Recovery**

```bash
# 1. Establish exactly what was lost: committed offset vs broker LOG-START.  ✅ verified
docker exec k2-redpanda rpk topic describe -p market.crypto.v3.raw.kraken
```

```sql
-- 2. The last offsets the archive committed.        not yet run — Phase D burn-in
SELECT summary['k2.kafka-offsets']
FROM lake.raw.messages.snapshots ORDER BY committed_at DESC LIMIT 1;
```

3. **Record the gap.** Per partition: the committed offset, the broker's current
   `LOG-START-OFFSET`, the count between them, and the wall-clock window it covers. Write
   it into `lake.audit.checks` and into this runbook's incident log below. A later query
   over that period must read a *documented* hole rather than an unexplained one; that
   traceability is the reason the archive exists.

```sql
-- not yet run — Phase D burn-in. check_name reuses the audit's own
-- 'offset_continuity'; job='operator' says a human filed it rather than the
-- nightly run, and must be listed in docker/lake/ddl/lake.sql's column comment.
INSERT INTO lake.audit.checks
VALUES (current_timestamp(), 'operator', 'offset_continuity',
        'market.crypto.v3.raw.kraken/7', false, 41233,
        'failOnDataLoss: committed 918442, broker LOG-START 959675, ~2h window lost to retention');
```

4. **Then resume explicitly** from the earliest surviving offset — never by clearing the
   check.

**And fix the cause, which is always one of two things:** ingest was down longer than
retention (§1, §2 — the countdown ran out), or retention is too short for the real message
rate and the 512 MiB-per-partition byte cap is binding well inside 48 h. The second is a
capacity finding, not an incident: raise the disk slice or lower the partition count —
**never** silently shorten retention, which `docker/redpanda/init.sh` says in as many words.

**Measured** — not yet verified. This scenario is deliberately not in `make chaos`: inducing
it means waiting out real retention or destroying a topic, and both prove something other
than the fault.

---

## 4. The nightly rewrite has not run — small files accumulating

**Symptom** — queries getting slower without getting wrong. `k2_lake_files_total` climbing
faster than `k2_lake_rows_total`, `k2_lake_avg_file_bytes` falling.

**Detection** — `LakeCompactionStale` from `docker/prometheus/rules/lake-alerts.yml`:
`time() - k2_lake_last_compaction_ts_seconds{table="raw.messages"} > 129600`, `for: 30m`.
Its annotation points at [lake-recovery.md](./lake-recovery.md); the diagnosis is here.

**Read what that rule measures, because it is not what the name suggests.** It fires on the
compaction **job** — 36 hours with no file-rewrite snapshot on `raw.messages`, i.e. at least
one missed nightly — not on mean file size. `rewrite_data_files` does not go through
`writeTo` and so cannot carry a `k2.job` property; `docker/lake/metrics.py` keys on
Iceberg's own `operation` field (`replace`/`overwrite`) instead.

The obvious alternative, `k2_lake_avg_file_bytes{table="raw.messages"} < 32MiB`, was
rejected because **it fires by construction on a healthy table for about the first 15
days**: 288 cycles/day × 9 topics under `write.distribution-mode=hash` is ~2,592 new ~2.5 MB
files a day, and a table-wide *mean* cannot clear 32 MiB until the compacted archive
outweighs them. The capacity model puts the disk runway at ~26 days, so that alert would
have been noise for 15 of them — which is how an alert gets muted. `k2_lake_avg_file_bytes`
and `k2_lake_files_total` are still the right things to *read* here; they are the symptom,
and `LakeCompactionStale` is the cause. No series exists at all until the table has been
compacted once, so a fresh table cannot fire it either.

**Expected behaviour** — the 5-minute cadence writes ~288 commits per table per day, each
one small, and nightly compaction is what converges them toward the target (256 MB for
`raw.messages`, 128 MB for `bronze.*` —
[partitioning-strategy.md](../architecture/partitioning-strategy.md)). If maintenance ran,
this self-corrects overnight. If it did not, nothing corrects it.

**Recovery**

```sql
-- 1. Files and average size per partition.        not yet run — Phase D burn-in
SELECT partition, count(*) AS files,
       round(avg(file_size_in_bytes) / 1048576, 1) AS avg_mb
FROM lake.raw.messages.files GROUP BY partition ORDER BY partition DESC LIMIT 10;
-- Flag any settled partition averaging under 10 MB per file.
```

```bash
# 2. Did maintenance run last night?               not yet run — Phase D burn-in
docker exec k2-prefect-server prefect flow-run ls --limit 5

# 3. Compact now rather than waiting. --days N is the compaction window, days
#    back; the nightly default is 2. Widen it to cover every night that was
#    missed — anything older was compacted by an earlier run and rewriting the
#    whole archive nightly costs without bound for no gain.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --days 3
```

`--days` is the only compaction flag. `maintenance.py` accepts `--days`, `--retain-days`,
`--orphan-hours` and `--audit-only`, and argparse exits 2 on anything else — a full run does
compaction, then expiry, then orphan removal, then the audits.

**Sort-order degradation is the second-order cost, and it is the one that is easy to miss.**
`bronze.*` prune by symbol through the sort order rather than a partition field
([ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md)), so unsorted small files
widen every file's symbol bounds and single-instrument queries stop pruning. Compaction on
`bronze.*` must therefore be a **sort rewrite**, not a plain binpack — a binpack merges the
files and leaves the clustering as bad as it found it. `compact()` does exactly that:
binpack on `raw.messages`, which is already written in offset order, and a sort rewrite on
both bronze tables using the sort order declared in `lake.sql`.

**Measured** — not yet verified.

---

## 5. A flow run failed with `exited 2` — the ingest lock held

**Symptom** — one `lake-ingest-5min` flow run is Failed in the Prefect UI, its logs end in

```
ingest.py exited 2 after 3s
another ingest holds /tmp/k2-lake-ingest.lock — refusing to run a second one.
Two concurrent appends both commit and both write offsets; see LOCK_PATH above.
```

and the next scheduled run five minutes later succeeds normally.

**Detection** — none, and there should be none. No Prometheus alert covers this: a single
refused run costs one cycle of freshness, which is an order of magnitude inside
`LakeIngestFailed`'s 30-minute threshold. A red run in Prefect is the whole signal.

**Expected behaviour — this is the lock doing its job, and the correct response is
nothing.** `main()` in `docker/lake/ingest.py` takes a non-blocking exclusive `flock` on
`/tmp/k2-lake-ingest.lock` before it opens a Spark session, and exits 2 rather than
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
fires. **The run that exited 2 wrote nothing** — the lock is taken before the Spark session
— so there is nothing to clean up and nothing to replay.

**Recovery** — only if it repeats. A run refused every cycle means a holder that never
exits:

```bash
# 1. Who holds it?                                  not yet run — Phase D burn-in
docker exec k2-spark-iceberg fuser /tmp/k2-lake-ingest.lock
docker exec k2-spark-iceberg ps -o pid,etime,cmd -p "$(
  docker exec k2-spark-iceberg fuser /tmp/k2-lake-ingest.lock 2>/dev/null)"

# 2. A genuinely stuck run — hours of elapsed time, no progress in its log — is the
#    killed-mid-run case, which is safe: kill it and let the next cycle resume.
#    lake-recovery.md §5 is the same procedure.
docker exec k2-spark-iceberg pkill -9 -f /home/iceberg/lake/ingest.py
docker exec k2-spark-iceberg pkill -9 -f org.apache.spark.deploy.SparkSubmit
```

**Do not delete the lock file.** `flock` is held on the open file descriptor, not on the
path, so unlinking it does not release anything — it just lets the next run take a *fresh*
lock on a new inode and start the second concurrent ingest the lock exists to prevent.

**Measured** — not yet verified. Exercised against a throwaway `scratch` namespace: two
concurrent appends duplicate without the lock and the second run exits 2 with it.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten. **Every §3 data-loss
window is recorded here**, with its offsets and its wall-clock span._

---

## Related

- [ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md) — why lag is safe, why `failOnDataLoss` is loud, why concurrency 1 is a correctness setting
- [ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md) — why a lost window has to be recorded rather than absorbed
- [lake-recovery.md](./lake-recovery.md) — a killed run, Lakekeeper down, MinIO down
- [lake-audit-failed.md](./lake-audit-failed.md) — when the continuity audit finds the hole instead of the ingest job
- [`../architecture/partitioning-strategy.md`](../architecture/partitioning-strategy.md) — target file sizes and the sort-order pruning that §4 protects
- [`docker/redpanda/init.sh`](../../docker/redpanda/init.sh) — the 48 h / 512 MiB retention arithmetic and why it is deliberately unresolved

---

**Last verified:** not yet verified — the ingest and maintenance code exists but has never
run against a populated lake on this host. Commands
marked ✅ were run read-only against the running stack on 2026-08-26 with their real output
pasted. Stamp this line at the Phase D burn-in.
