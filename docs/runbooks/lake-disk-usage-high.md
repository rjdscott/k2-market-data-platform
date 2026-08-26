# Runbook: Host disk above 80 % — the lake is filling it

The lake keeps `raw.messages` forever. There is no TTL, no expiry, and nothing on this
platform deletes a row of it
([Q8](../research/2026-08-26-v3-requirements-clarification.md#q8--raw-archive-retention-on-a-single-host),
[ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)). Disk is therefore the
platform's first binding constraint, it binds on a *calendar* rather than at a load
multiple, and this runbook is the operator half of that decision.

It does **not** cover ClickHouse disk pressure (the hot tier has a 7-day TTL and bounds
itself) or Redpanda disk (time- and byte-capped in `docker/redpanda/init.sh`).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Not yet verified.** The alert is Phase D and the lake has not yet grown. Commands
> marked ✅ were run read-only against the running stack on 2026-08-26 and their real
> output is pasted.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Host disk ≥ 80 % — decide and act | **decision within 24 h**, not minutes | not yet verified — Phase D burn-in |
| 2 | Host disk ≥ 90 % — writes about to fail | < 2 h | not yet verified — Phase D burn-in |

---

## 1. Disk at 80 %

**Symptom** — nothing is broken. That is the point of alerting here rather than at 95 %:
this is a **capacity decision with a lead time**, not an incident.

**Detection** — `LakeDiskUsageHigh` from `docker/prometheus/rules/lake-alerts.yml`.

**Expected behaviour** — growth continues, because nothing in the design stops it. The
capacity model predicts `raw.messages` at **6.47 GB/day** and the whole lake at
**6.89 GB/day**, and predicts the host filling in **~26 days** from a cold start
([capacity model §7](../architecture/capacity-model.md#7-bottleneck-prediction)). Those are
predictions, not measurements; the commands below produce the real slope.

### Read the disk — and read it in the right place

**The two obvious sources disagree on this host, and knowing which one is right matters
more than the reading.** Verified 2026-08-26:

```console
$ df -BG /var/lib/docker | tail -2                                        # ✅ verified
Filesystem     1G-blocks  Used Available Use% Mounted on
/dev/nvme0n1p5      961G  711G      202G  78% /

$ curl -s --get localhost:9090/api/v1/query --data-urlencode \
   'query=1 - ClickHouseAsyncMetrics_FilesystemMainPathAvailableBytes
            / ClickHouseAsyncMetrics_FilesystemMainPathTotalBytes' \
   | jq -r '.data.result[].value[1]'                                      # ✅ verified
0.3404562234470415
```

**78 % against 34 %, on what ought to be the same disk.** The reason is that Docker on
this host runs inside a VM: the ClickHouse container sees `/dev/vda1`, 944 GiB, 31 % used
(`docker exec k2-clickhouse df -h /` ✅), while the host sees `/dev/nvme0n1p5`, 961 G, 78 %
used. The ClickHouse Prometheus metric reports the **VM's** view of a thin-provisioned
virtual disk, not the physical one the archive actually consumes — and it is the physical
one that stops accepting writes.

**Consequence for the alert, stated so Phase D does not wire the wrong metric:** the 80 %
alert must be sourced from something that sees the host filesystem, and today's
`ClickHouseAsyncMetrics_FilesystemMainPath*` series is not it. Anything else is an alert
that fires 44 points late. This is a real, open finding — it is recorded here rather than
quietly worked around, and it is the first thing the Phase D burn-in must settle.

### Then read the growth rate, not just the level

```bash
# What the lake itself holds, per table prefix.                            ✅ verified
docker exec k2-minio mc alias set lk http://minio:9000 \
  "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && \
docker exec k2-minio mc du lk/k2-lake/
#   0B    0 objects    k2-lake
#   Zero is correct today: the tables were created in Phase D and no ingest has run.

docker exec k2-minio mc du --depth 3 lk/k2-lake/    # per-prefix once populated  ✅ verified (empty)
```

```bash
# Days remaining = free ÷ slope. Take df twice, at least 24 h apart, and divide.
# One sample is a level, not a slope, and a level cannot tell you when to act.
df -BG /var/lib/docker | tail -1 | awk '{print strftime("%F %T"), $4}' >> /tmp/k2-disk.log
```

Phase F publishes days-remaining as a first-class number with its command
([Q8](../research/2026-08-26-v3-requirements-clarification.md#q8--raw-archive-retention-on-a-single-host)).
Until it does, that two-sample subtraction is the honest version.

---

## The options, in order — and the one that is not on the list

**Do not add a TTL to `raw.messages`. Not 30 days, not 7, not "just this once".** A TTL
does not free disk *later*; it truncates the replay window *now* — `bronze.*` becomes
unrebuildable past the cutoff, and the archive stops being the system of record for
anything older. Q8 rejected 30 d and 7 d explicitly, on those grounds. This is the one
action that looks like a fix and is a permanent reduction in what the platform is.

### Option A — add disk (the honest answer on a single host)

Attach a volume, move `/var/lib/docker/volumes/…minio-data` onto it, and restart MinIO.
The lake keeps every guarantee it has, and the decision is reversible.

```bash
# not yet run — verify at the Phase D burn-in
docker compose stop minio
rsync -a --info=progress2 /var/lib/docker/volumes/k2-market-data-platform_minio-data/_data/ /mnt/k2-lake/
# repoint the volume in docker-compose.yml, then:
docker compose up -d --force-recreate --no-deps minio
docker exec k2-minio mc du lk/k2-lake/      # byte count must match pre-move
```

This is the option [capacity model §7](../architecture/capacity-model.md#7-bottleneck-prediction)
names as the one that keeps ADR-018's guarantee intact, and it is the recommended one.

### Option B — move object storage to S3 with a lifecycle

Capacity stops being the constraint and becomes a storage-class choice. The mapping,
the lifecycle tiers, and the two rules that make it work — never lifecycle the Iceberg
metadata prefix, and Glacier **Instant Retrieval** rather than Deep Archive if the archive
must stay queryable — are designed in
[`../architecture/scale-out-path.md`](../architecture/scale-out-path.md), §4. Labelled
*designed, not exercised*: nothing there has been deployed.

Mechanically it is `K2_S3_ENDPOINT`, `K2_S3_REGION` and `K2_S3_PATH_STYLE=false`, because
`docker/lake/spark_conf.py` reads all three from the environment. That is the whole point
of them being environment variables.

### Option C — a manual, recorded purge (last resort)

If neither A nor B is available before the disk fills, an operator may delete a **specific,
named window** of `raw.messages` — and the difference between this and a TTL is the entire
justification for allowing it:

- it is a **one-off human decision**, not a policy, so nothing repeats it silently;
- it is **recorded** as a row in `lake.audit.checks` with the exact window, so a query over
  that period reads a documented hole rather than an unexplained one;
- and it must be announced in this runbook's *Failure modes / incidents* section below,
  with the date, so the archive's completeness claim stays true in writing.

```sql
-- not yet run — verify at the Phase D burn-in. Record it BEFORE deleting.
INSERT INTO lake.audit.checks
VALUES (current_timestamp(), 'operator', 'manual_purge',
        'raw.messages kafka_ts [2026-08-01, 2026-08-08)', false, NULL,
        'disk at 94%; option A unavailable; purged by <operator> — see runbook incident log');

DELETE FROM lake.raw.messages
WHERE kafka_ts >= TIMESTAMP '2026-08-01' AND kafka_ts < TIMESTAMP '2026-08-08';
```

Note the second-order cost, so nobody discovers it afterwards: the lake tables are
**copy-on-write** (`docker/lake/ddl/lake.sql`), so a delete rewrites the affected files —
it needs free space to complete, which is exactly what is scarce. Purge the oldest whole
day partitions, one at a time, and check free space between each.

**Measured** — not yet verified.

---

## 2. Disk at 90 % — writes about to fail

**Symptom** — the same alert, louder. Below roughly 5 % free, expect ingest commits to
fail, MinIO to reject writes, and — because it shares the same filesystem here — Redpanda
and ClickHouse to start failing too.

**Detection** — `LakeDiskUsageCritical` from `docker/prometheus/rules/lake-alerts.yml`.

**Expected behaviour** — the ordering of failure is not in the platform's control, so
assume everything on this host fails at once. Nothing is corrupted by it: a failed ingest
commit is a no-op that the next run repeats
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)), and Redpanda holds 48 h
of raw, which is the real deadline.

**Recovery** — buy time first, then take Option A or B.

```bash
# 1. Reclaim what is safe to reclaim, in cost-of-mistake order.
#    not yet run — verify at the Phase D burn-in
docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --expire-snapshots
docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --remove-orphans

# 2. Docker's own reclaimable space — images and build cache, never volumes.  ✅ verified
docker system df
docker image prune -f          # safe; does not touch volumes
```

Snapshot expiry reclaims metadata and files that compaction superseded; orphan removal
reclaims files written by runs that never committed. Both are routine maintenance being
run early rather than emergency measures, and neither touches a live row.

**Never** run `docker system prune --volumes` on this host. The lake, the catalog database
and ClickHouse all live in Docker volumes; that command is the fastest way to turn a disk
alert into total data loss.

**Measured** — not yet verified.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten. **Every Option C
purge is recorded here**, with its window and the reason._

---

## Related

- [Q8, v3 requirements clarification](../research/2026-08-26-v3-requirements-clarification.md#q8--raw-archive-retention-on-a-single-host) — keep forever, 80 % alert, no lake TTL
- [ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md) — why the archive is unbounded, and the single-host limit stated plainly
- [capacity model §7](../architecture/capacity-model.md#7-bottleneck-prediction) — 6.89 GB/day, ~26 days, and the three responses
- [capacity model §8](../architecture/capacity-model.md#8-how-this-table-is-settled) — the `df` / `mc du` commands this runbook uses
- [`../architecture/scale-out-path.md`](../architecture/scale-out-path.md) — Option B in full, labelled *designed, not exercised*
- [lake-recovery.md](./lake-recovery.md) — if the disk filled and something broke

---

**Last verified:** not yet verified — the disk alert is Phase D and unbuilt. The `df`,
`docker exec … df`, `mc du`, `docker system df` and Prometheus query commands marked ✅
were run read-only against the running stack on 2026-08-26 and their real output is pasted.
The 78 %-vs-34 % discrepancy above is a genuine open finding, not a rounding difference:
Phase D must source the alert from a metric that sees the host filesystem.
