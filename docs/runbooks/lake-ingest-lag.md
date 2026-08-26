# Runbook: Lake ingest is behind

The lake ingest runs every 5 minutes and reads Redpanda by offset range into
`raw.messages`, then decodes into `bronze.*`. This covers the tier falling behind: a
backlog, a stalled scheduler, an ingest that fails on data Redpanda has already evicted,
and files arriving too small to be useful.

It does **not** cover a crashed run (that is safe and automatic —
[lake-recovery.md §5](./lake-recovery.md#5-ingest-killed-mid-run)), the v2 offload
(`iceberg-offload-lag.md`), or a failing audit
([lake-audit-failed.md](./lake-audit-failed.md)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Not yet verified — the Phase D burn-in fills this in.** The ingest job is being built.
> Commands marked ✅ were run read-only against the running stack on 2026-08-26.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Ingest lag above the 5-minute cadence | < 30 min | not yet verified — Phase D burn-in |
| 2 | Scheduler stopped — no runs at all | < 15 min | not yet verified — Phase D burn-in |
| 3 | `failOnDataLoss` — offsets point below broker retention | **investigation, not repair** | not yet verified — Phase D burn-in |
| 4 | Small files accumulating | < 24 h (next maintenance window) | not yet verified — Phase D burn-in |

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
`k2_lake_ingest_lag_seconds` (newest `kafka_ts` committed vs now) and
`k2_lake_last_commit_age_seconds` (wall-clock age of the last commit). The two say
different things and both matter: lag high with commit age *low* means runs are happening
and not keeping up; lag high with commit age *high* means runs are not happening — go to
§2.

**Expected behaviour** — a single slow cycle self-corrects, because the next run reads a
wider offset range. Sustained lag does not: at 5-minute cadence, a run that takes longer
than 5 minutes never catches up on its own.

**Recovery**

```bash
# 1. Which is it — slow runs, or no runs?                                  ✅ verified
curl -s localhost:9090/api/v1/alerts | \
  jq -r '.data.alerts[] | "\(.labels.alertname) \(.labels.severity) \(.activeAt)"'
#   CaptureFeedStale critical 2026-08-26T...   (the only alert firing on 2026-08-26)

curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=k2_lake_ingest_lag_seconds' | \
  jq -r '.data.result[] | "\(.metric.table) \(.value[1])"'   # not yet run — Phase D
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
| Runs completing, each slower than 5 min | the backlog is larger than one cycle can drain | slice it: run with `--end-timestamp` to ingest a bounded window at a time, repeatedly, until the lag closes |
| Runs completing fast, lag flat | the scheduler is not firing at cadence | §2 |
| Spark at its CPU limit during a run | contention with capture or ClickHouse | check the cpuset layout — capture is pinned away from Spark deliberately ([Phase D isolation experiment](../plans/2026-08-26-v3-quant-research-platform/003-phase-d-lake-tier.md)) |
| `failOnDataLoss` in the logs | offsets below broker retention | §3 — **stop and read it before doing anything** |

```bash
# Backlog slicing: bounded windows, oldest first, so each run is a normal-sized
# batch rather than one enormous one.               not yet run — Phase D burn-in
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py \
  --end-timestamp '2026-08-26T10:00:00Z'
```

**Do not raise the ingest cadence to catch up.** Runs are deployed at concurrency 1 for
correctness ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)); a faster
schedule against a concurrency-1 deployment queues runs rather than parallelising them, and
raising the concurrency is the one change that breaks the exactly-once argument. Slice the
window instead.

**Measured** — not yet verified.

---

## 2. Scheduler stopped

**Symptom** — `k2_lake_last_commit_age_seconds` climbing steadily, no failed runs, no
errors anywhere. The quietest failure on this page.

**Detection** — `LakeCommitAgeHigh` from `docker/prometheus/rules/lake-alerts.yml`;
`LakeExporterDown` if the metrics exporter itself is the thing that is gone.

**Expected behaviour** — nothing recovers on its own. A paused deployment stays paused.

**Recovery**

```bash
# 1. Do the deployments exist, and are they scheduled?                     ✅ verified
docker exec k2-prefect-server prefect deployment ls
#   iceberg-maintenance-main/iceberg-maintenance-daily
#   iceberg-offload-main/iceberg-offload-15min
#   (the lake-* deployments land with Phase D)

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
#   capture-binance up / clickhouse up / iceberg-scheduler up / redpanda up ...
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
-- not yet run — Phase D burn-in
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

## 4. Small files accumulating

**Symptom** — queries getting slower without getting wrong. `k2_lake_files_total` climbing
faster than `k2_lake_rows_total`.

**Detection** — `LakeSmallFiles` from `docker/prometheus/rules/lake-alerts.yml`.

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

# 3. Compact now rather than waiting.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --compact
```

**Sort-order degradation is the second-order cost, and it is the one that is easy to miss.**
`bronze.*` prune by symbol through the sort order rather than a partition field
([ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md)), so unsorted small files
widen every file's symbol bounds and single-instrument queries stop pruning. Compaction on
`bronze.*` must therefore be a **sort rewrite**, not a plain binpack — a binpack merges the
files and leaves the clustering as bad as it found it.

**Measured** — not yet verified.

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

**Last verified:** not yet verified — the lake ingest is Phase D and unbuilt. Commands
marked ✅ were run read-only against the running stack on 2026-08-26 with their real output
pasted. Stamp this line at the Phase D burn-in.
