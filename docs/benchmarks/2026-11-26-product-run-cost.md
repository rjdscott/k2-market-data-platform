# Benchmark: 2026-11-26, the cost of one product run

**Commit** `64a07c2` (main at 11:08 UTC, when the spikes ran; the scripts are committed under
[`scripts/spikes/2026-11-26/`](../../scripts/spikes/2026-11-26/)) · **Stack uptime at
measurement** at 11:08 UTC, `k2-spark-iceberg` 3 h 29 min, `k2-trino` and `k2-minio` 11 h 17 min,
`k2-lakekeeper` 7 h 9 min ([U1](#u1)) · **Window** 2026-11-26 11:08 to 11:15 UTC (the recorded
spike); re-runs at 14:03 UTC (dbt) and 14:09 to 14:20 and 14:34 to 14:45 UTC (run cost) ·
**Host** i7-14700K, 28 threads, 62 GiB; Docker Desktop VM 28 CPU / 49.5 GiB ([H1](#h1)); Spark
container limit 3 CPU / 8 GiB, Trino 2 CPU / 5 GiB

What one data product run costs on this host, measured so the unified platform
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)) could decide whether each product
gets its own Spark session. **Decision: one product run costs about 6.5 s of Spark (median
6.51 s, maximum 6.60 s, n = 5), far under the 200 s line, so per-product Spark sessions stay.**
The 200 s line is plan 004's: a five-minute chain whose p90 passes 200 s takes the escapes in
order (one Spark session for the venue connectors, Spark Connect, derived products every 15
minutes). These are quiet-host numbers, taken between scheduled `lake_ingest` runs; the
contended chain is measured in phase 004's 48-hour soak. The same document sets the
delete-file bound that soak tests (plan review finding F13).

**Every percentile here has n = 5 or 6, so the p90 is the maximum and is labelled so. No
timeout is derived from these numbers** (plan review finding F24): the p99 or p90 of so few
samples is just the largest one.

## Spark: one branch, write, audit, publish run

The pattern every bronze publish follows: create a branch, append a batch of 1,000 synthetic
rows to it, audit the branch (row count, key uniqueness), fast-forward `main`, drop the branch.
One Python driver per run (`lake_session()` from `docker/lake/spark_conf.py`, driver heap
512m) in `k2-spark-iceberg`, started only when no lake job was running there. The session row
counts from the host clock before `docker exec`, the total from then until the driver exited;
the other steps are timed inside the driver. Recorded 2026-11-26 11:08 to 11:15 UTC; seconds.

| Step | Small table: min / median / max (p90 = max) | Table with 200 snapshots, 200k rows: min / median / max (p90 = max) | n | Command |
|---|---|---|---|---|
| Spark session with the catalog loaded | 2.53 / 2.54 / 2.60 | 2.50 / 2.53 / 3.12 | 5 + 5 | [R1](#r1) |
| Create branch | 0.10 / 0.10 / 0.11 | 0.14 / 0.15 / 0.16 | 5 + 5 | [R1](#r1) |
| Append 1,000 rows | 1.65 / 1.68 / 1.75 | 1.63 / 1.69 / 2.32 | 5 + 5 | [R1](#r1) |
| Audit: count | 0.54 / 0.56 / 0.62 | 0.52 / 0.57 / 1.01 | 5 + 5 | [R1](#r1) |
| Audit: uniqueness | 0.35 / 0.47 / 0.60 | 4.27 / 4.40 / 5.64 | 5 + 5 | [R1](#r1) |
| Fast-forward | 0.09 / 0.09 / 0.10 | 0.10 / 0.11 / 0.13 | 5 + 5 | [R1](#r1) |
| Drop branch | 0.03 / 0.03 / 0.04 | 0.04 / 0.05 / 0.06 | 5 + 5 | [R1](#r1) |
| Stop | 0.36 / 0.44 / 0.64 | 0.53 / 0.65 / 0.72 | 5 + 5 | [R1](#r1) |
| **Total, wall clock** | **6.44 / 6.51 / 6.60** | **10.45 / 10.54 / 13.64** | 5 + 5 | [R1](#r1) |

The growth with table size is the uniqueness audit scanning the whole branch, not metadata:
the three branch steps (create, fast-forward, drop) together grew from 0.23 to 0.31 s at the
median. Session start plus the append is 65 percent of a small run (4.23 of 6.51 s at the
median). The slowest run of the large table was its first, which also had the slowest session
start.

### Re-run by `run.sh` on the same day

The committed script, run twice more on 2026-11-26 ([R1](#r1)): at 14:09 to 14:20 UTC on the
same Spark image as the recorded run (`k2-spark-iceberg:3.5.5_1.8.1`), and at 14:34 to 14:45 UTC
on the rebased image (`k2-spark-iceberg:3.5.9_1.8.1`), to which the container had been switched
at 14:30 UTC by other work. Seconds, min / median / max (p90 = max); the table above stays the
published one.

| Measure | Recorded, 11:08 UTC, 3.5.5 | Re-run, 14:09 UTC, 3.5.5 | Re-run, 14:34 UTC, 3.5.9 | n each | Command |
|---|---|---|---|---|---|
| Small table, total wall clock | 6.44 / 6.51 / 6.60 | 6.53 / 6.87 / 9.00 | 7.23 / 7.32 / 8.50 | 5 | [R1](#r1) |
| Small table, Spark session | 2.53 / 2.54 / 2.60 | 2.52 / 2.84 / 3.03 | 3.05 / 3.15 / 3.32 | 5 | [R1](#r1) |
| 200 snapshots, total wall clock | 10.45 / 10.54 / 13.64 | 11.45 / 12.08 / 12.50 | 10.78 / 11.92 / 12.13 | 5 | [R1](#r1) |
| 200 snapshots, uniqueness audit | 4.27 / 4.40 / 5.64 | 4.74 / 4.88 / 5.40 | 4.40 / 5.15 / 5.45 | 5 | [R1](#r1) |
| dbt build, incremental model and two tests | 3.02 / 3.50 / 4.71 | 1.90 / 1.97 / 2.19 | 1.70 / 1.75 / 1.89 | 6 | [R1](#r1) |

**Moved by more than 10 percent** against the recorded medians: the small-table total on the
rebased image (+12 percent), the 200-snapshot total on both re-runs (+15 and +13 percent), and
the dbt build on both (44 and 50 percent faster). The cause was not isolated. The conditions
differed: the re-runs started each Spark job in a gap of a `lake_ingest` that took most of
every five minutes, and other throwaway containers were running on the host. The decision does
not move: the largest median, 12.08 s, is 6 percent of the 200 s line.

Two other attempts did not finish, and neither is a cost finding. At 14:24 UTC the driver JVM of
a small-table run died with a SIGSEGV in HotSpot's symbol table cleanup (OpenJDK 17.0.14 in
image 3.5.5_1.8.1); its crash report was deleted, since it repeats the driver's configuration,
S3 keys included. At 14:30 UTC the container was recreated mid-run.

## dbt: one build of one model with two tests

| What | min / median / max (p90 = max) | n | When | Command |
|---|---|---|---|---|
| `dbt build`, one small incremental model (append, 1,000 new rows a run) and two tests, through `uv run`, wall clock | 3.02 / 3.50 / 4.71 s | 6 | 2026-11-26 11:10 UTC, recorded | [R1](#r1) |
| `dbt build --select pub_tbl`, one `table` model and two tests, through `uv run`, wall clock | 1.54 / 1.58 / 2.39 s | 5 | 2026-11-26 14:03 UTC, re-measured | [D1](#d1) |
| The same build, calling the locked environment's `dbt` directly | 1.51 / 1.55 / 1.59 s | 5 | 2026-11-26 14:03 UTC, re-measured | [D1](#d1) |

The first of the seven recorded builds created the table and is not counted. The spike's
write-up (SPIKE-DBT, item 6) also gave "3.0 to 3.9 s via uv, 2.6 s calling dbt directly" for a
`table` model, without recording n or the runs; those figures are not published here, and the
D1 rows re-measure the same build with n and the command. How far the recorded figure moved on
two re-runs is in the re-run table above.

## Arithmetic, not measured

From the medians above, for a sense of headroom only: one Spark slot running product runs back
to back fits 300 / 6.51 = 46 small runs per five minutes (300 / 10.54 = 28 at the larger
size); Trino fits 300 / 3.50 = 85 dbt builds per five minutes serially. Neither is a capacity
claim: both ignore Airflow's scheduling overhead and any contention.

## The delete-file bound per silver table (F13)

**Bound: a silver table's current snapshot carries no more delete files than the builds it has
had since its last compaction. For a table built every five minutes and compacted once a day
that is 288 (24 h / 5 min); for one built hourly, 24. The expected steady state is near 0.** Phase
004's 48-hour soak checks every silver table against its bound with

```sql
SELECT count(*) FROM <catalog>.<schema>."<table>$files" WHERE content <> 0  -- 1 position deletes, 2 equality deletes
```

The reasoning, with what was measured ([D1](#d1), 2026-11-26 14:03 UTC, Trino 484):

1. **Only an update writes delete files.** Trino writes a delete file for the rows a `MERGE`
   matches, one per data file holding a matched row; a merge whose rows are all new writes
   none. Measured on one table: 1 delete file after a merge that updated two rows of one data
   file, 2 after a second that updated a row of another, 0 after a merge of new keys only.
2. **Compaction clears them.** After `ALTER TABLE ... EXECUTE optimize` the count was 0. So
   the count grows only between two compactions, and the target design compacts every product
   daily, after its own build ([ARCHITECTURE-V2](../research/2026-11-26-unified-platform-architecture/ARCHITECTURE-V2.md),
   "Maintenance, per product"; [plan 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)).
3. **A correct model updates little.** dbt merges silver on its key past a watermark. In plan
   004, `silver_trade`'s key is the delivery (every delivery kept and flagged), so a run's
   merge only inserts and writes no delete file. The updates a correct model makes happen only
   when its source changed: a held row leaves the held table when its symbol resolves, a
   type-2 version closes when a listing changes. Most builds update nothing, so one delete
   file per build since the last compaction is a ceiling with margin, not an expected figure:
   288 a day for a five-minute table, 24 for an hourly one such as `silver_listing` (plus one
   per on-demand instruments run).
4. **Both failures it exists to catch cross it.** If compaction stops, a table that updates
   one file per build reaches 576 by the 48th hour. If a model re-selects rows it already
   wrote (a lookback window with no filter on keys already landed), every one of them is
   matched and rewritten, because dbt-trino 1.12.0's merge updates on any match
   (`when matched then update set`, `trino__get_merge_sql`): with one data file per build, a
   one-hour lookback at five minutes rewrites 12 files a build and passes 288 within the
   first day. Either is a defect to fix, not a reason to raise the bound.

Not measured, and needed before the soak relies on the bound: that the compaction phase 004
runs clears delete files as Trino's `optimize` did here. Today's `docker/lake/maintenance.py`
compacts with Spark's `rewrite_data_files`, which was not tried.

## Not measured

- Airflow's scheduling overhead per run.
- A product run beside another job: the contended chain moves to phase 004's soak, where the
  first five-minute chain exists.
- Memory per run, and the dbt project's on-run-end results write.

---

## Commands

<a id="r1"></a>**R1**: the run-cost spike. The recorded numbers are recomputed from the
original run's raw lines; `run.sh` re-measures them on the live stack. Every Spark job waits for
a gap between `lake_ingest` runs, so a run took 6 minutes at 11:08 UTC and 11 in the afternoon.
```bash
python3 scripts/spikes/2026-11-26/spike-runcost/summarise.py scripts/spikes/2026-11-26/spike-runcost/results-2026-11-26
bash scripts/spikes/2026-11-26/spike-runcost/run.sh
```

<a id="d1"></a>**D1**: the dbt spike; its last finding line is the build timing, and the
`delete files on inc_merge` line the counts in the bound.
```bash
bash scripts/spikes/2026-11-26/spike-dbt/run.sh
```

<a id="u1"></a>**U1**: container start times, read at 14:08 UTC; none had restarted since 11:08
UTC, and uptime is 11:08 UTC minus each. `k2-spark-iceberg` was recreated at 14:30 UTC, so a read
after that shows the new start.
```bash
docker inspect -f '{{.Name}} {{.State.StartedAt}}' k2-spark-iceberg k2-trino k2-lakekeeper k2-minio
```

<a id="h1"></a>**H1**: the host and the Docker VM.
```bash
lscpu | grep 'Model name'; nproc; free -g | head -2
docker info --format '{{.NCPU}} CPU {{.MemTotal}} bytes {{.OperatingSystem}}'
docker inspect -f '{{.Name}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}' k2-spark-iceberg k2-trino
```
