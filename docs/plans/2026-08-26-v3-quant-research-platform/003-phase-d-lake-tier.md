# Phase D — Lake tier (`docker/lake/`, ~1.5 weeks; replaces `docker/offload/`)

**Depends on:** Phase C
**Delivers:** Replaces `docker/offload/` with a lake-first Spark ingest into Iceberg (raw/bronze) via Lakekeeper, with maintenance, metrics, and audits.
**Exit:** two consecutive ingests, second adds 0; kill mid-run → no dupes/gaps; audits pass 3 days.

## Scope

- Tables (`docker/lake/ddl/lake.sql`): `raw.messages` (topic, partition, offset, kafka_ts, ingest_ts, key, schema_id, payload BINARY verbatim, headers; `PARTITIONED BY days(kafka_ts), topic`; zstd; metrics on offset/kafka_ts/partition; never expired); `bronze.trades` (unified, `exchange` partition field, DECIMAL(28,10), `src_{topic,partition,offset}` lineage, identifier fields exchange,symbol,trade_id); `bronze.book_snapshots_l2` (arrays of struct px/sz, `seq`, lineage; metrics none except event_ts/symbol/seq); `audit.checks`. No gold in lake; no `book_deltas` (raw holds them). `write.distribution-mode=hash`, 128–256 MB targets, sort orders. Schema-evolution policy: add nullable only; vendor map for exchange extras; `raw.messages` frozen.
- Ingest `docker/lake/ingest.py` (+ pure `offsets.py`, `spark_conf.py`): one Spark session, two stages: (1) Kafka → `raw.messages`, `startingOffsets` from latest ingest snapshot summary property `k2.kafka-offsets` (skip compaction snapshots), `endingOffsets=latest`, `failOnDataLoss=true`, commit with `snapshot-property.k2.kafka-offsets` + `k2.max-kafka-ts`; (2) `raw.messages` incremental read (`start-snapshot-id`→new) → decode (`substring(payload,6)` + `from_avro` with schema fetched by id from registry, FAILFAST) → `bronze.*`, commit with `k2.src-snapshot-id`. `--end-timestamp` for backlog slicing. Prefect deployments `lake-ingest-5min` (concurrency 1), `lake-maintenance-daily`; keep `docker exec k2-spark-iceberg` dispatch.
- Maintenance `docker/lake/maintenance.py` (~180 lines): binpack compaction raw, sort rewrite bronze (last 2 days), expire snapshots; audits → `audit.checks`: offset continuity per topic/partition (+cross-day seam), duplicates on identifier fields, sequence gaps (lag over seq); fail → non-zero exit → Prefect fail → alert.
- Metrics `docker/lake/metrics.py` via PyIceberg snapshot summaries (`k2_lake_ingest_lag_seconds`, `last_commit_age`, `rows_total`, `files_total`, `added_records`, `audit_failures`) + `clickhouse_active_parts{table}` gauge; `lake-alerts.yml` (LagHigh, AuditFailed, SmallFiles, ExporterDown); dashboard `k2-lake.json` replaces iceberg-offload.
- Recovery runbook `docs/runbooks/lake-recovery.md`: CH rebuild via `icebergS3()` (fallback `s3()` globs); Redpanda replay = cold start.
- Tests: `tests/test_lake_offsets.py`, `tests/test_wire_format.py` (pure python, existing conftest pattern); `make lake-verify` integration script (offsets property present/gapless, raw count == bronze count, idempotent double-run adds 0).
- Delete: `docker/offload/*` (offload_generic, watermark_pg, create_*, generate/verify, old maintenance/flows), `docker/postgres/ddl/offload-watermarks.sql`, `docker/iceberg/ddl/0{2,3,4}-*.sql`, warehouse bind mount, `spark-jars/`. Parallel-run old offload vs new ingest for 24h before deletion.

## Verification

- Every phase: `make test` (rust/python/clickhouse-schema), CI green, `docker compose up -d --build` from clean clone → all services healthy.
- Lake: snapshot summary offsets gapless; raw count == bronze count; double-run adds 0; audits pass; DuckDB notebook 01–04 run clean.
