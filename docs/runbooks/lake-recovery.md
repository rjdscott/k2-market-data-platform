# Runbook: Recover the lake tier, and rebuild the hot tier from it

Covers the five ways the v3 lake path breaks and what each one costs: rebuilding
ClickHouse from the archive, Redpanda replay as a cold start, Lakekeeper down, MinIO
down, and an ingest run killed mid-flight. It does **not** cover capture-side failures
(`capture-*.md`), the v2 offload (`iceberg-offload-*.md`), or disk filling up
([lake-disk-usage-high.md](./lake-disk-usage-high.md)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Not yet verified — the Phase D burn-in fills this in.** The ingest and maintenance
> jobs are being built; no rebuild has been performed and no MTTR here is measured.
> Commands marked ✅ were run read-only against the running stack on 2026-08-26 and their
> real output is pasted. Everything else is marked **not yet run** and is written against
> the Phase D design.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | ClickHouse hot tier lost — rebuild from the lake | < 60 min for a 7-day window | not yet verified — Phase D burn-in |
| 2 | Hot tier behind by hours — Redpanda replay (cold start) | < 15 min | not yet verified — Phase D burn-in |
| 3 | Lakekeeper down — nothing commits to the lake | < 10 min | not yet verified — `make chaos` |
| 4 | MinIO down — nothing reads or writes lake data | < 10 min | not yet verified — `make chaos` |
| 5 | Ingest killed mid-run | recovery automatic; **verification < 5 min** | not yet verified — `make chaos` |

---

## The one rule on this page

**`iceberg()` is the only supported way to read the lake from ClickHouse. The
`s3('…/data/*.parquet')` glob is banned.** Not discouraged — banned, in writing, because
it is exactly what gets reached for at 2 a.m. when `iceberg()` is failing and the
dashboards are empty.

The glob reads the object listing instead of the current Iceberg metadata, so it returns
files that no live snapshot references: spike S11 measured **8 rows against a truth of 5**
after a single copy-on-write delete
([ADR-018 Appendix A](../adr/ADR-018-v3-lake-first-rust-capture.md#s11--clickhouse-243-reads-iceberg-v2)).
Compaction rewrites files nightly, so this is not a corner case — it is the state of every
table the morning after any maintenance run. And it fails as a *plausible number*, not as
an error, which is the one failure shape a research platform must not have.

**If `iceberg()` fails, that is a stop-the-line bug.** Stop, capture the error, and fix
the reader. Do not substitute the glob "just to get the dashboard back": a dashboard
showing double-counted rows is worse than a dashboard showing nothing, because someone
will believe it.

Verified on the running stack, 2026-08-26 — the function exists on this ClickHouse:

```console
$ docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
    -q "SELECT version()"                                                    # ✅ verified
24.3.18.7
$ docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
    -q "SELECT name FROM system.table_functions WHERE name ILIKE '%iceberg%'" # ✅ verified
iceberg
```

Note the name: it is `iceberg`, **not** `icebergS3` — that function does not exist on
24.3 and asking for it returns `Code: 46 ... Maybe you meant: ['iceberg']`.

---

## 1. ClickHouse hot tier lost — rebuild from the lake

**Symptom** — the ClickHouse volume is gone, corrupted, or has been dropped deliberately
after a schema change. Dashboards are empty or partial; the lake is fine.

**Detection** — `ClickHouseDown` from
[`docker/prometheus/rules/clickhouse-alerts.yml`](../../docker/prometheus/rules/clickhouse-alerts.yml),
or a `k2_lake_rows_total` / ClickHouse row-count divergence noticed by hand.

**Expected behaviour** — **no data is lost.** The hot tier originates nothing
([ADR-025](../adr/ADR-025-clickhouse-derived-hot-tier.md)); every row it held exists in
`lake.bronze.*`, which in turn is a pure function of `lake.raw.messages`. What is lost is
freshness, for as long as the rebuild takes. This is the scenario the whole v3 storage
inversion was designed for, and it is the one that most deserves to be timed rather than
assumed.

**Recovery**

```bash
# 1. Confirm the lake is actually healthy before rebuilding from it.       ✅ verified
curl -s localhost:18181/health | jq .
#   {"health":"ok","services":{"catalog":[{"name":"read_pool","status":"ok"}, ...]}}

# 2. Confirm the tables and namespaces the rebuild will read.              ✅ verified
PREFIX=$(curl -fsS 'localhost:18181/catalog/v1/config?warehouse=k2' | jq -r '.defaults.prefix')
curl -fsS "localhost:18181/catalog/v1/$PREFIX/namespaces" | jq -c .
#   {"namespaces":[["raw"],["bronze"],["audit"],["scratch"]]}
#   The catalog path prefix is the warehouse UUID, not its name — using the name
#   returns 400 WarehouseIdIsNotUUID.
```

```bash
# 3. Bring ClickHouse back with its schema, empty.        not yet run — Phase D burn-in
docker compose up -d clickhouse
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
  -q "SHOW TABLES FROM k2"
```

```sql
-- 4. Reload from the lake through iceberg(). One table at a time, newest
--    window first, so dashboards come back before the backfill finishes.
--    not yet run — Phase D burn-in
INSERT INTO k2.trades
SELECT *
FROM iceberg('http://minio:9000/k2-lake/warehouse/k2/<table-uuid>',
             '{MINIO_ROOT_USER}', '{MINIO_ROOT_PASSWORD}')
WHERE exchange_ts >= now() - INTERVAL 7 DAY;
```

> **Finding the table UUID.** MinIO object paths are keyed on the Iceberg table UUID, not
> the table name (spike S8), so `mc ls` will not tell you which prefix is `bronze.trades`.
> Ask the catalog: `curl -fsS "localhost:18181/catalog/v1/$PREFIX/namespaces/bronze/tables/trades" | jq -r '.metadata.location'`.
> Not yet run — Phase D burn-in.

```bash
# 5. Verify: hot-tier count must equal the lake count for the same window.
#    not yet run — Phase D burn-in
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM k2.trades
   WHERE exchange_ts >= now() - INTERVAL 1 DAY GROUP BY exchange ORDER BY exchange"
# compare against the same window in DuckDB over lake.bronze.trades
```

**If `iceberg()` errors** — stop. Capture the full error, check that Lakekeeper and MinIO
are up (§3, §4), and check whether the table has merge-on-read delete files, which S11 did
**not** test and which the lake DDL deliberately avoids (`write.delete.mode =
copy-on-write` on every table). If a MOR delete has appeared in the lake, that is the bug:
the reader is fine and the writer broke the contract. Do not reach for the glob.

**Measured** — not yet verified. The Phase D burn-in performs one full 7-day rebuild and
records: wall-clock duration, rows restored per table, and whether hot and lake counts
matched exactly. Until that number exists, "rebuildable" is a design claim, and this line
says so.

---

## 2. Redpanda replay — the cold-start path

**Symptom** — the hot tier is behind by hours, not days. ClickHouse was down and is back;
the lake is fine.

**Detection** — ClickHouse Kafka-engine consumer lag, or a stale `max(recv_ts_ns)` in the
hot tier while the lake keeps advancing.

**Expected behaviour** — this is a **cold start**, not a repair, and it is bounded by
broker retention, not by need: raw topics keep **48 h** with a 512 MiB-per-partition byte
cap; `trades.*` and `book.*` keep **7 d** (`docker/redpanda/init.sh`). Whichever binds
first is an open question the Phase D burn-in settles, so **check what is actually there
before planning a replay** rather than trusting the time figure.

Replay re-derives through the capture-to-broker path rather than re-reading the archive,
so a replayed hot tier and the lake can differ in the last bits for the same second. That
is acceptable for a dashboard and is not acceptable for a research number — which is why
§1, not this section, is the supported rebuild.

**Recovery**

```bash
# 1. What does the broker still hold? Start and end offsets per partition. ✅ verified
docker exec k2-redpanda rpk topic describe market.crypto.v3.raw.binance
#   PARTITIONS 12  REPLICAS 1 ... max.message.bytes 8388608 (DYNAMIC_TOPIC_CONFIG)

docker exec k2-redpanda rpk topic describe -p market.crypto.v3.trades.binance   # ✅ verified
# Read LOG-START-OFFSET vs HIGH-WATERMARK: if LOG-START has moved above where the
# consumer stopped, that window is gone from the broker and only the lake has it.
```

```bash
# 2. Reset the ClickHouse consumer group to the earliest surviving offset.
#    not yet run — Phase D burn-in
docker exec k2-redpanda rpk group describe clickhouse_v3
docker exec k2-redpanda rpk group seek clickhouse_v3 --to start --topics market.crypto.v3.trades.binance
docker compose restart clickhouse
```

**If the gap is larger than broker retention, stop and use §1.** The archive is the only
thing that holds it, and a partial replay that silently starts at `LOG-START-OFFSET`
produces a hot tier with an unrecorded hole.

**Measured** — not yet verified.

---

## 3. Lakekeeper down

**Symptom** — ingest runs fail at commit time. Spark logs show a connection error against
the catalog URI. Reads of already-committed data through `iceberg()` and PyIceberg keep
working, which makes this easy to misdiagnose as healthy.

**Detection** — `LakeIngestLagHigh` and `LakeCommitAgeHigh` from
`docker/prometheus/rules/lake-alerts.yml`; the compose healthcheck on the `lakekeeper`
service turning unhealthy.

**Expected behaviour** — **nothing is lost and nothing is duplicated.** A run that cannot
commit has written orphan Parquet files that no snapshot references, so no reader ever
sees them, and the stored offsets never moved — the next run reads the same range and
redoes the work ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). The
constraint is Redpanda retention: at 48 h on raw topics, the catalog can be down for
roughly a weekend before data starts being lost at the broker, and that is the number
that decides whether this is urgent or merely annoying.

**Recovery**

```bash
# 1. Is it actually down, or just unbootstrapped?                          ✅ verified
curl -s localhost:18181/health | jq .
#   {"health":"ok","services":{"secrets":[...],"catalog":[{"status":"ok"},...]},
#    "maintenance_mode":"off"}

docker ps --filter name=k2-lakekeeper --format '{{.Names}}\t{{.Status}}'   # ✅ verified
#   k2-lakekeeper  Up 4 hours (healthy)

# 2. Its database. Lakekeeper shares the Prefect PostgreSQL server.        ✅ verified
docker ps --filter name=k2-prefect-db --format '{{.Names}}\t{{.Status}}'
#   k2-prefect-db  Up 4 hours (healthy)
```

```bash
# 3. Restart, in dependency order.                        not yet run — Phase D burn-in
docker compose restart lakekeeper
curl -s localhost:18181/health | jq -r .health          # wait for "ok"

# 4. If the catalog came back empty — a fresh volume — re-run the idempotent
#    bootstrap. Every step treats "already exists" as success.
docker compose up lake-init
```

**A healthy container that has never been bootstrapped answers `/health` with `ok` and
rejects every table operation.** If `lake-init` has not run against this catalog, no
amount of restarting fixes it; run step 4. `docker/lake/init-lake.sh` creates the bucket,
bootstraps, creates the `k2` warehouse and the `raw` / `bronze` / `audit` namespaces, and
is safe to re-run on a live stack.

**Losing the catalog database while MinIO is intact is the bad case**, and it is not a
restart: the data files exist and nothing knows which of them are current. Recovery is a
database restore, not a re-bootstrap — a fresh catalog over a populated bucket produces an
empty warehouse alongside orphaned objects.

**Measured** — not yet verified. `scripts/chaos/lake-lakekeeper-stop.sh` (Phase D) stops
the catalog mid-ingest, waits for the alert, restarts it, and measures the time until the
next ingest commits.

---

## 4. MinIO down

**Symptom** — everything touching lake data fails: ingest, maintenance, DuckDB notebooks,
and `iceberg()` from ClickHouse. The catalog answers `/health` with `ok`, because it holds
metadata *about* objects it cannot reach.

**Detection** — `LakeIngestLagHigh`, `LakeExporterDown` from
`docker/prometheus/rules/lake-alerts.yml`; the `minio` compose healthcheck.

**Expected behaviour** — same shape as §3, for the same reason: a commit that cannot write
files never happens, offsets never move, the next run redoes it. Live capture is
unaffected — Redpanda is upstream of the lake and keeps buffering. The bound is again
broker retention.

**Recovery**

```bash
# 1. Is the object store up, and is the bucket there?                      ✅ verified
docker ps --filter name=k2-minio --format '{{.Names}}\t{{.Status}}'
#   k2-minio  Up 4 hours (healthy)

docker exec k2-minio mc alias set lk http://minio:9000 \
  "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && \
docker exec k2-minio mc ls lk/                                            # ✅ verified
#   [2026-08-26 08:07:42 UTC]     0B k2-lake/
#   0B is correct today: the lake tables were created in Phase D and no ingest
#   has run yet. After the first ingest this is the fastest sanity check there is.
```

```bash
docker compose restart minio                             # not yet run — Phase D burn-in
docker exec k2-minio mc ls lk/k2-lake/warehouse/k2/ | head
```

**Orphan files after a MinIO outage are expected and are not a leak to panic about.**
Runs that wrote files and could not commit left them unreferenced. The nightly maintenance
job's `remove_orphan_files` reclaims them; do not delete objects by hand, because a table's
files are keyed by table UUID and "looks unreferenced" is not something `mc ls` can tell
you.

**Measured** — not yet verified. `scripts/chaos/lake-minio-stop.sh` (Phase D).

---

## 5. Ingest killed mid-run

**Symptom** — a Prefect run for `lake-ingest-5min` shows Crashed or Failed; the container
was killed, OOMed, or the host restarted.

**Detection** — Prefect flow-run state; `LakeIngestLagHigh` if it repeats.

**Expected behaviour — re-running is safe, and this is the property Phase D exists to
prove.** The reasoning, once, because it is the thing an operator most needs to trust at
3 a.m.:

The consumed Kafka offsets live in the Iceberg snapshot summary of the commit that wrote
the rows they describe (`snapshot-property.k2.kafka-offsets`,
[ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). There is no second store.
So after any death there is exactly one durable fact:

- **The commit did not happen** → the offsets did not move. The next run reads the same
  range, rewrites it, commits. The dead run's files are orphans no reader can see. No
  duplicates, no gap.
- **The commit happened** → the offsets moved with it, atomically. The next run starts at
  the successor. Re-reading that range is not merely safe, it is *impossible* — the
  commit moved the start.

There is no intermediate state, because there is no ordering between two stores to get
wrong. That is the whole argument for putting the offsets in the snapshot.

**The one case that is not automatically safe** is two ingests running concurrently over
the same window. `lake-ingest-5min` is deployed at **concurrency 1** for exactly this
reason — it is a correctness setting, not a politeness one. If concurrency has been raised,
lower it before doing anything else.

**Recovery**

```bash
# 1. Just re-run it. Then verify the invariants rather than trusting them.
#    not yet run — Phase D burn-in
docker exec k2-prefect-server prefect deployment run 'lake-ingest/lake-ingest-5min'

# 2. Deployment list — confirms concurrency and that the deployment exists.  ✅ verified
docker exec k2-prefect-server prefect deployment ls
#   iceberg-maintenance-main/iceberg-maintenance-daily
#   iceberg-offload-main/iceberg-offload-15min
#   (v2 deployments; the lake-* deployments land with Phase D)
```

```sql
-- 3. The three checks that prove it worked. not yet run — Phase D burn-in
--    a) offsets present on the newest ingest snapshot, and abutting the previous one
SELECT snapshot_id, committed_at, summary['k2.kafka-offsets']
FROM lake.raw.messages.snapshots ORDER BY committed_at DESC LIMIT 3;

--    b) a second run adds nothing
SELECT summary['added-records'] FROM lake.raw.messages.snapshots
ORDER BY committed_at DESC LIMIT 1;      -- expect 0 on an immediate re-run

--    c) raw and bronze agree for the window
SELECT count(*) FROM lake.raw.messages WHERE kafka_ts >= current_date();
```

`make lake-verify` (Phase D) wraps exactly these three checks.

**If the ingest fails with a data-loss error** rather than a crash — `failOnDataLoss` —
that is §2's territory, not this one: the offsets point at records Redpanda has already
evicted. Do **not** work around it by resetting to the earliest offset silently. Record
the gap window, then resume explicitly, and follow
[lake-ingest-lag.md](./lake-ingest-lag.md).

**Measured** — not yet verified. `scripts/chaos/lake-ingest-kill.sh` (Phase D) kills the
ingest mid-write and asserts the double-run-adds-zero invariant afterwards; that is also
the Phase D exit criterion.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten._

---

**Last verified:** not yet verified end to end — the lake ingest is Phase D and unbuilt.
Commands marked ✅ were run read-only against the running stack on 2026-08-26 and their
real output is pasted above. Stamp this line with a date and a commit at the Phase D
burn-in, and replace every "not yet run" marker with the output it produced.
