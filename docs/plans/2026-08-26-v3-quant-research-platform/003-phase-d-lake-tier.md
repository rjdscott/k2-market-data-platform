# Phase D — Lake tier (`docker/lake/`, ~1.5 weeks; replaces `docker/offload/`)

**Depends on:** Phase C
**Delivers:** Replaces `docker/offload/` with a lake-first Spark ingest into Iceberg (raw/bronze) via Lakekeeper, with maintenance, metrics, and audits.
**Exit:** two consecutive ingests, second adds 0; kill mid-run → no dupes/gaps; audits pass over a 2 h window (maintainer decision 2026-08-26, Q6), labelled.

## Scope

- Tables (`docker/lake/ddl/lake.sql`): `raw.messages` (topic, partition, offset, kafka_ts, ingest_ts, key, schema_id, payload BINARY verbatim, headers; `PARTITIONED BY days(kafka_ts), topic`; zstd; metrics on offset/kafka_ts/partition; never expired); `bronze.trades` (unified, `exchange` partition field, DECIMAL(28,10), `src_{topic,partition,offset}` lineage, identifier fields exchange,symbol,trade_id); `bronze.book_snapshots_l2` (arrays of struct px/sz, `seq`, lineage; metrics none except event_ts/symbol/seq); `audit.checks`. No gold in lake; no `book_deltas` (raw holds them). `write.distribution-mode=hash`, 128–256 MB targets, sort orders. Schema-evolution policy: add nullable only; vendor map for exchange extras; `raw.messages` frozen.
- Ingest `docker/lake/ingest.py` (+ pure `offsets.py`, `spark_conf.py`): one Spark session, two stages: (1) Kafka → `raw.messages`, `startingOffsets` from latest ingest snapshot summary property `k2.kafka-offsets` (skip compaction snapshots), `endingOffsets` pinned per partition at `min(latest, start + --max-offsets-per-partition)`, `failOnDataLoss=true`, commit with `snapshot-property.k2.kafka-offsets` + `k2.max-kafka-ts` + `k2.kafka-backlog`; (2) `raw.messages` incremental read (`start-snapshot-id`→new) → decode (`substring(payload,6)` + `from_avro` with schema fetched by id from registry, FAILFAST) → `bronze.*`, commit with `k2.src-snapshot-id`. `--end-timestamp` for backlog slicing. Prefect deployments `lake-ingest-5min` (concurrency 1), `lake-maintenance-daily`; keep `docker exec k2-spark-iceberg` dispatch.
- Maintenance `docker/lake/maintenance.py` (~180 lines): binpack compaction raw, sort rewrite bronze (last 2 days), expire snapshots; audits → `audit.checks`: offset continuity per topic/partition (+cross-day seam), duplicates on identifier fields, sequence gaps (lag over seq); fail → non-zero exit → Prefect fail → alert.
- Metrics `docker/lake/metrics.py` via PyIceberg snapshot summaries (`k2_lake_ingest_lag_seconds`, `last_commit_age`, `rows_total`, `files_total`, `added_records`, `audit_failures`) + `clickhouse_active_parts{table}` gauge; `lake-alerts.yml` (LagHigh, AuditFailed, SmallFiles, ExporterDown); dashboard `k2-lake.json` replaces iceberg-offload.
- Recovery runbook `docs/runbooks/lake-recovery.md`: CH rebuild via `iceberg()` — no glob fallback — `iceberg()` only; if it ever fails, that is a stop-the-line bug; Redpanda replay = cold start.
- Tests: `tests/test_lake_offsets.py`, `tests/test_wire_format.py` (pure python, existing conftest pattern); `make lake-verify` integration script (offsets property present/gapless, raw count == bronze count, idempotent double-run adds 0).
- **Partitioning, with the rejected alternative on the page.** Rewrite `docs/architecture/partitioning-strategy.md` (today it documents the v2 shape — `bronze_trades_*`, `silver_trades`, `cold.*` — and is wrong the moment `docker/offload/` is deleted). Kafka section: key = canonical symbol, 12 partitions per topic, why not key-by-exchange (v2 pinned Kraken and Coinbase to one partition each) and why not partition-per-symbol (rebalance cost, no ordering gain). Iceberg section: `raw.messages` `days(kafka_ts), topic` and `bronze.*` spec + sort order, why not `hours()` (file count on a single host), why not partitioning by `symbol` (skew: BTC-USD dwarfs the tail), why sort order rather than a partition field carries symbol pruning. ClickHouse section lands in Phase E; the file states which tier is not yet rewritten rather than describing the v2 tables.
- **Failure modes as an FMEA.** New `docs/architecture/failure-modes.md`: one row per `component × failure`, columns `detection signal` (the exact metric or alert name), `blast radius` (what is lost vs delayed vs unaffected), `recovery step` (linking the runbook), `proof` (the test or `make chaos` target that demonstrates it). Capture and lake rows land here: capture killed / paused (`kill -STOP`) / producing to a full queue; Redpanda broker down and topic truncation; Lakekeeper down mid-commit; MinIO down; ingest killed mid-run; corrupt Avro payload; clock skew on `recv_ts_ns`. Blank cells are not allowed — a row without a proof column is a row that does not ship.
- **Scale-out path.** New `docs/architecture/scale-out-path.md` (requirements clarification Q9): one table mapping each tier to its AWS equivalent at TB/PB — raw archive on S3 with a Glacier/Deep Archive lifecycle (what makes Q8's keep-forever viable past one disk), MSK or Redpanda Cloud, ClickHouse on EC2/ClickHouse Cloud, EMR Serverless for ingest and maintenance, one Fargate task per exchange across AZs, Lakekeeper on ECS with RDS — and, per component, what changes versus what does not (the Avro contracts, the capture binary and the lake-as-system-of-record contract do not). Iceberg partition spec, target file size, manifest counts and compaction cadence are justified at PB scale in the same doc, not only at today's rate. Every endpoint, region, path-style flag and catalog URI in `docker/lake/*` stays env-driven so the mapping is a config change, not a rewrite. Labelled *designed, not exercised*.
- **`make chaos`.** `scripts/chaos/*.sh` plus a `chaos` Makefile target running them serially against the local stack: kill and pause each capture container, `docker stop` Redpanda, `docker pause` ClickHouse, stop Lakekeeper mid-ingest, corrupt a frame via the fixture path. Each script prints the alert it expects to fire and measures time-to-recovery (`k2_capture_last_message_ts_seconds` / lake commit age returning under threshold); the measured recovery time is written into the `failure-modes.md` row. Local only — the CI runner (7 GB) cannot host the stack, so this is a maintainer-run gate, not a nightly job (`docs/research/2026-08-26-v3-requirements-clarification.md`, Q3).
- **Noisy-neighbour experiment (resource isolation, measured).** With capture pinned by `cpuset` away from Spark and ClickHouse, run `docker/lake/maintenance.py` compaction over a full day of `raw.messages` while sampling `k2_capture_exchange_to_recv_seconds` p99 and `k2_capture_messages_total` rate; compare against a quiet-period baseline of the same duration. Record both numbers, the delta, and the cpuset layout in `capacity-model.md` with the commands. A p99 regression beyond the noise band means the pinning is wrong, and that is a finding, not a rerun.
- Delete: `docker/offload/*` (offload_generic, watermark_pg, create_*, generate/verify, old maintenance/flows), `docker/postgres/ddl/offload-watermarks.sql`, `docker/iceberg/ddl/0{2,3,4}-*.sql`, warehouse bind mount, `spark-jars/`. Parallel-run old offload vs new ingest for a 2 h window (maintainer decision 2026-08-26, Q6), labelled, before deletion.

## Verification

- Every phase: `make test` (rust/python/clickhouse-schema), CI green, `docker compose up -d --build` from clean clone → all services healthy.
- Lake: snapshot summary offsets gapless; raw count == bronze count; double-run adds 0; audits pass; DuckDB notebook 01–04 run clean.
- Chaos: `make chaos` exits 0 — every script sees its expected alert in `curl -s localhost:9090/api/v1/alerts | jq -r '.data.alerts[].labels.alertname'` and the stack returns to green within the script's stated bound; `docs/architecture/failure-modes.md` has no empty `detection signal`, `recovery step` or `proof` cell (`awk -F'|' '/^\|/ && NF>5 {for(i=2;i<NF;i++) if($i ~ /^ *$/) exit 1}' docs/architecture/failure-modes.md`).
- Isolation: noisy-neighbour p99 during compaction and the quiet baseline are both in `capacity-model.md`, each with its `curl`/`clickhouse-client` command; the compaction run is pinned off the capture cpuset (`docker inspect -f '{{.HostConfig.CpusetCpus}}' k2-spark-iceberg k2-capture-binance` shows disjoint sets).

## Deferred to deployment, and why

The Scope and Verification bullets above are the design as written. Three of them
cannot be met by code alone, so they are named here rather than quietly dropped.

- **The exit criteria themselves are unexecuted.** `raw`, `bronze` and `audit` exist
  as namespaces on the live Lakekeeper warehouse and hold **no tables**: `lake-ddl`
  and `lake-metrics` have never run on this host, because Phase C is mid-burn-in on
  the same containers. So "two consecutive ingests, second adds 0", "kill mid-run →
  no dupes/gaps", "audits pass over a 2 h window" and the parallel run against
  `docker/offload/` are all still ahead. Every lake proof cell in
  `docs/architecture/failure-modes.md` says "written; not yet run", every threshold in
  `docker/prometheus/rules/lake-alerts.yml` says "NOT yet observed firing", and every
  lake runbook says "not yet verified end to end". Nothing here claims a measurement it
  does not have — but the phase does not close until `make lake-verify` and `make
  chaos` have both run green against real tables. That is a deployment gate.

  What HAS been exercised, against a throwaway `scratch` namespace in the same
  catalog: the DDL applies (9/9 statements), two concurrent appends duplicate without
  the ingest lock and the second run exits 2 with it, the fixed-point conversion
  round-trips int64 max instead of NULLing, an un-framed frame is archived and skipped
  instead of poisoning stage 2, an unresolvable schema id becomes an `audit.checks`
  row, a raising audit becomes a failed row, and `remove_orphan_files` finds and
  deletes a planted orphan. Those are unit-level proofs of the mechanisms the exit
  criteria measure end to end; they are not the exit criteria.

  **Measure the ingest driver's peak RSS at the cutover — `spark.driver.memory`
  is a first sizing, not a measurement.** `k2-spark-iceberg` is capped at 2 CPU
  / 4 GiB, and it is not empty before a job starts: the base image's always-on
  JVMs (Master, Worker, History Server, Thrift Server, Jupyter) idle at 633 MiB
  (`docker stats --no-stream k2-spark-iceberg`, 2026-08-26). Two drivers can
  still be alive in there — `lake-maintenance-daily` at 03:00 and the 03:01
  ingest tick are a minute apart and a compaction run outlives that easily, and
  an operator's `docker exec` during an incident is the other way. So
  `docker/lake/spark_conf.py` pins the heap at **768m**: `2 x (768 + ~550) +
  ~400 Python + 633 baseline` is ~3.58 GiB of the cap, where the image's
  inherited 1 g default is ~4.08 GiB and over it. That is arithmetic over one
  measured baseline, not an observed peak, and the failure mode it guesses at is
  an OOM-kill of whichever driver asks for the last page — which reads as a
  random ingest failure rather than as a memory problem.

  Take the real number during a run, not between them, and record it here and in
  `docs/operations/docker-resources.md`:

  ```bash
  # Peak DURING an ingest is the number; RSS in kB, one row per JVM and Python
  # driver, so the baseline is visible alongside. Sampled every 2 s for the whole
  # run — one reading taken between stages is not a peak.
  docker exec k2-spark-iceberg ps -o rss,cmd
  docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' k2-spark-iceberg
  ```

  **Measured 2026-08-27, gate satisfied.** Four hand-run ingests against the live
  41.5 M-record backlog with `lake-ingest-5min` paused, sampled every 2 s:

  | Run | `--max-offsets-per-partition` | Offsets committed | raw rows | Wall | Peak driver RSS | Peak container |
  |---|---|---|---|---|---|---|
  | smoke | 1,000 | 77,542 | 77,542 | 17 s | 1,122 MiB | 1.97 GiB |
  | 1 | 50,000 | 2,721,812 | 2,721,812 | 92 s | 1,243 MiB | 2.13 GiB |
  | 2 | 50,000 | 1,770,914 | 1,770,914 | 57 s | 1,227 MiB | 1.94 GiB |
  | 3 | 50,000 | 1,564,334 | 1,564,334 | 49 s | 1,221 MiB | 1.92 GiB |
  | 4 | 200,000 | 4,097,437 | 4,097,437 | 71 s | 1,213 MiB | 1.69 GiB |

  **Peak driver RSS 1,243 MiB**, peak container 2.13 GiB of 4 GiB over the
  633 MiB idle baseline. Note run 4: 53× the smoke batch at four times the bound,
  and the peak went *down* — inside the `768 + ~550` this arithmetic assumed, so two
  concurrent drivers still fit and the 768m stands. 35× the batch moved the peak
  11%: with nothing payload-bearing cached, peak RSS is a function of the heap
  setting rather than of arrival volume, so a backlog drain at a higher bound
  does not re-open this gate.

  The revisit trigger below **fired once, on 2026-08-26**, and the cause was not
  the heap: the first cron run had no prior snapshot, read all 108 partitions to
  `latest`, and died on `java.lang.OutOfMemoryError` inside a
  `persist(DISK_ONLY)` over 5.2 MB payload rows. The fix was to bound the run
  (`--max-offsets-per-partition`, default 200,000) and stop caching payloads, not
  to raise the heap — `docker/lake/README.md`, "What one run may read, and why
  nothing is cached".

  **The first bound was itself too low, and it cost records.** 50,000 per partition
  per 5-minute cycle is below `market.crypto.v3.raw.kraken-0`'s measured 55,250, so
  that partition slid backwards on every cycle until Redpanda's 512 MiB cap evicted
  1,168,954 unread records. The default is now 200,000, ~3.6× the measured rate, and
  the resume point is checked against the broker's log start before each run
  (`offsets.evicted`) so the loss is reported with its numbers rather than as a Kafka
  stack trace. **Open, for a person:** that gap is not yet filed in `lake.audit.checks`
  and the ingest will not advance past it — `docs/runbooks/lake-ingest-lag.md` §3.

  Revisit trigger for the 768m itself: raise it back to `1g` if
  `lake-ingest-5min` fails with an OOM-kill or `java.lang.OutOfMemoryError` in
  the first week after cutover. A measured peak near 4 GiB, or `docker inspect
  -f '{{.State.OOMKilled}}' k2-spark-iceberg` reporting `true` after a failed
  run, means the container budget needs revisiting before the job needs
  debugging — and moving the maintenance cron off 03:00 is the cheaper fix than
  raising a stated 16 CPU / 40 GB constraint.

- **The noisy-neighbour experiment (Scope bullet 8, Verification bullet 3) was deferred
  from the build to the deployment, and ran on 2026-08-26.** At build time `spark-iceberg`
  carried no `cpuset` and assigning one recreates running containers, which could not
  happen mid-burn-in. Deployed: `K2_HEAVY_CPUSET=0-11` on ClickHouse / Spark / `lake-ddl`,
  `K2_CAPTURE_CPUSET=12-14` on capture, applied 22:03Z. Measured: two 10-minute windows,
  quiet then `maintenance.py --days 2`, both numbers, the delta, the layout and the
  commands are the dated note at the foot of `capacity-model.md`. Verdict there: p50 flat,
  p99 moved within the message-rate band, 0 produce errors — the pinning holds. The run
  found compaction OOM-ing at 768m instead, fixed in the same PR. `make check-docs` gate (e)
  still reports *predicted-only*: the note is a dated measurement, not a `measured` column,
  which Phase F's benchmark file backs.

- **The 80% disk alert measures the wrong filesystem on this host, and only on this
  host.** `k2_lake_disk_used_ratio` reads 0.344 inside the container against the
  machine's 79% (`df -h /`, 2026-08-26T14:43Z) because Docker Desktop puts a
  thin-provisioned VM disk in between. The *rule* is delivered and tested —
  `docker/prometheus/rules/tests/lake-alerts_test.yml` asserts it fires at 0.81 and
  not at 0.79 — and on bare metal or EC2 the metric is honest and Q8's requirement is
  met. Closing it here needs a host-side exporter, which is a separate decision.

## Diverged from the plan, deliberately

Three Scope details are described above as they were designed and are not what
shipped. The plan is left as written; the reasons are here.

- **`bronze.trades` identifier fields are `exchange, symbol, trade_id, src_topic,
  src_partition, src_offset`, not `exchange, symbol, trade_id`** (first `conn_id` was added,
  then the first day of the archive showed Coinbase re-sending trades within one connection
  too — 5,034 keys — so the key became the lineage), and `bronze.book_snapshots_l2` uses
  `exchange, symbol, conn_id, snapshot_ts_ns` rather than `conn_msg_seq`. Both were
  settled by measurement over a 30-minute capture: 956 duplicated keys in 287,184
  trades, 484 in 47,331 snapshots. [ADR-024](../../adr/ADR-024-unified-bronze-tables-in-the-lake.md)
  carries the evidence and `docker/lake/ddl/lake.sql` repeats it above each table.
- **`metrics.py` does not use PyIceberg.** `import pyiceberg.io.pyarrow` alone costs
  121.9 MiB RSS against the exporter's 128 MB limit; one REST GET against Lakekeeper
  returns the same snapshot summaries at 26.6 MiB. `docker/lake/README.md` has both
  commands.
- **`k2_lake_ingest_lag_seconds` is not exported, and nothing replaces it under that
  name.** Scope bullet 4 above names it, and so does
  [005-phase-f](005-phase-f-notebooks-numbers-docs.md) where the lake SLO is defined against
  it. What `docker/lake/metrics.py` exports instead is
  `k2_lake_max_kafka_ts_seconds` — the *instant* of the newest Kafka record — and lag is
  `time() - k2_lake_max_kafka_ts_seconds` in PromQL. Same for `last_commit_age`, which is
  `k2_lake_last_commit_ts_seconds`. Timestamps, not ages, and the reason is B2 in
  `docker/lake/metrics.py`: an age gauge is recomputed only on a *successful* catalog read,
  so during a Lakekeeper outage every age freezes at its last small value and its threshold
  becomes unreachable — the exporter goes blind in exactly the outage it is the backstop
  for. Phase F's SLO should be written against the timestamp gauge.

- **`lake-alerts.yml` has eleven rules, not four, and `SmallFiles` is not among them.**
  An alert on mean file size fires by construction for the first ~15 days of the
  table's life; `LakeCompactionStale` measures the rewrite job instead.
  `LakeExporterStalled` and `LakeScrapeErrors` were added because a Lakekeeper outage
  freezes every other gauge — which is why nothing here exports an age any more.

- **The 2-hour parallel run of the old offload against the new ingest was dropped, and
  `docker/offload/` was deleted without it** (maintainer decision, 2026-08-27). The two
  paths have nothing comparable to put side by side: `cold.*` copies ClickHouse's
  normalised, TTL'd `k2.*` tables that the Kotlin handlers feed, and `bronze.*` is derived
  from the verbatim frames the Rust capture tier writes to `raw.messages`. Different
  source, different schema, different catalog — a row-count agreement would not have shown
  the new path correct and a disagreement would not have shown it wrong. The window would
  have produced a green table meaning nothing, and cost two Spark drivers in one 4 GiB
  container for two hours to produce it. The comparison that does decide the question is
  the Kotlin/Rust parity window (ADR-019), which runs on its own schedule. v2 data is
  disposable (`../../research/2026-08-26-v3-requirements-clarification.md`, Q7), so there
  was nothing to protect by keeping the old path alive.

  The parallel window in the RSS note above is therefore gone, but the sizing it forced is
  not: two drivers can still coexist in that container — the 03:00 maintenance run overlaps
  the 03:01 ingest tick, and an operator's `docker exec` during an incident is a third — so
  the cron stays `1-59/5` and `spark.driver.memory` stays pinned. It is pinned at **768m**
  rather than the 1 g this note originally recorded, because the arithmetic left the base
  image's 633 MiB of always-on JVMs out and two 1 g drivers plus that baseline do not fit
  under 4 GiB. The peak-RSS measurement is still owed.

  Deleted with it: `docker/offload/`, `docker/iceberg/` (hadoop DDL, validation, the
  bind-mounted warehouse), `docker/postgres/ddl/offload-watermarks.sql`,
  `iceberg-offload-alerts.yml` (9 rules), `iceberg-offload.json`, the `iceberg-metrics` and
  `iceberg-init` compose services, the `iceberg-scheduler` scrape job,
  `tests/test_iceberg_maintenance_flow.py` (28 tests), the two v2 Prefect deployments and
  the `iceberg-offload` work pool, and `clickhouse-jdbc` / `psycopg2` from the images. The
  six operational runbooks are archived in `legacy/v2-offload/`. The reasoning is recorded
  in the Outcome sections of [ADR-014](../../adr/ADR-014-spark-based-iceberg-offload.md)
  and [ADR-017](../../adr/ADR-017-iceberg-maintenance-pipeline.md), and the budget in
  [ADR-010](../../adr/ADR-010-resource-budget.md)'s Phase D cutover addendum.
