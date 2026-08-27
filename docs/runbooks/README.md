# Runbooks

Incident procedures for the running v2 stack, plus the v3 capture and lake tiers being
built alongside it. Runbooks record *how*; ADRs record *why* (`../adr/`). Every Prometheus
alert annotation points at one of these; start from the alert that fired, or from the
"when to use" column below.

Load secrets before running any command here: `set -a && . ./.env && set +a`

## Conventions

- One task per file, `<slug>.md`. Updating an existing runbook beats a near-duplicate.
- Shape: **symptom → detection (name the alert) → expected behaviour → recovery commands
  → measured MTTR**. [`template.md`](./template.md) is that shape, empty.
- Exact copy-pasteable commands, never paraphrases. Verify them before writing them down;
  a runbook nobody ran is fiction.
- MTTR is measured by inducing the failure, not estimated.
- No decision rationale here — link the ADR.
- A PR that invalidates a runbook's steps updates it and its index row in the same PR.
- Every alert in `docker/prometheus/rules/` names a runbook in its annotations, and that
  path must resolve. The v2 rule files carry it as a `**Runbook:**` line inside the
  `description`; `capture-alerts.yml` promotes it to a first-class `runbook:`
  annotation so the path is machine-checkable. New rule files use the annotation form.
- Write it with the `/runbook` skill.

## Index

### v2 stack

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [failure-recovery.md](./failure-recovery.md) | Any of the six tested infrastructure failures: broker restart, ClickHouse restart, capture crash, batch-job failure, MinIO down, network partition | `ClickHouseDown`, `CaptureDown`, `CaptureProduceErrors` |
| [redpanda.md](./redpanda.md) | Topic, partition, consumer-group or schema-registry problems; broker health | `CaptureProduceErrors` |

The six runbooks for the v2 ClickHouse→Iceberg offload were archived with the code they
described; they are kept unmodified in
[`legacy/v2-offload/runbooks/`](../../legacy/v2-offload/README.md) and describe a path that
no longer exists — do not follow them against this stack.

### v3 capture tier (Phase C — written ahead of the code; see the note below)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [capture-down.md](./capture-down.md) | A `k2-capture` container is down or crash-looping, or is running but failing to produce to Redpanda | `CaptureDown`, `CaptureProduceErrors` |
| [capture-produce-stalled.md](./capture-produce-stalled.md) | Capture is receiving from the exchange but producing nothing to Redpanda, before the queue fills and starts dropping | `CaptureProduceStalled` |
| [capture-feed-stale.md](./capture-feed-stale.md) | Container up and scrapeable but a stream has gone silent; or exchange→receive p99 has stepped up | `CaptureFeedStale`, `CaptureIngressLatencyHigh` |
| [capture-sequence-gaps.md](./capture-sequence-gaps.md) | Exchange sequence continuity broke (messages lost), or the book keeps being resynced | `CaptureSequenceGaps`, `CaptureResyncStorm` |
| [capture-checksum-failure.md](./capture-checksum-failure.md) | Kraken CRC32 mismatch, book thinner than top-20, or a venue quoting finer than 8 dp | `CaptureChecksumFailure`, `CaptureBookDepthDegraded`, `CapturePrecisionLoss` |

> **These four carry no measured MTTR yet, and say so on every row.** The capture tier
> ([ADR-019](../adr/ADR-019-rust-capture-tier.md)) is built and running; what has not
> happened is a fault injection. Commands run against it are marked ✅. The Phase C
> chaos run
> (`make chaos`) induces each failure, waits for the alert, and fills in the
> **Measured** rows and the **Last verified** stamp. Until then they are procedures,
> not measurements — which is exactly the distinction this directory's conventions
> exist to protect.

### v3 lake tier (Phase D — written alongside the code; see the note below)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [lake-recovery.md](./lake-recovery.md) | Rebuilding ClickHouse from the lake, Redpanda replay as a cold start, Lakekeeper down, MinIO down, an ingest killed mid-run, the nightly rewrite not running | `ClickHouseDown`, `LakeIngestFailed`, `LakeExporterDown`, `LakeExporterStalled`, `LakeScrapeErrors`, `LakeCompactionStale` |
| [lake-disk-usage-high.md](./lake-disk-usage-high.md) | Host disk at 80 % or 90 %. The archive is kept forever, so this is a capacity decision with a lead time — **never** a TTL | `LakeDiskUsageHigh`, `LakeDiskUsageCritical` |
| [lake-ingest-lag.md](./lake-ingest-lag.md) | Ingest behind cadence, scheduler stopped, `failOnDataLoss`, small files accumulating | `LakeIngestLagHigh`, `LakeCommitAgeHigh`, `LakeIngestFailed` |
| [lake-audit-failed.md](./lake-audit-failed.md) | The nightly audit failed: offset continuity, duplicate identifiers, or venue sequence gaps — six checks across four kinds, one of them informational | `LakeAuditFailed` |

> **Two of the lake failures are investigations, not repairs**, and the runbooks say so on
> the row: a real offset gap and a venue sequence gap are unrecoverable — public feeds do
> not replay — so the deliverable is a *recorded* window in `lake.audit.checks`, not a
> restart. The one rule that spans all four:
> [`iceberg()` only, the `s3()` glob is banned](./lake-recovery.md#the-one-rule-on-this-page),
> because the glob returns plausible wrong numbers after any compaction.

## Triage

```bash
# What is actually firing?
curl -s localhost:9090/api/v1/alerts | jq '.data.alerts[] | {alertname: .labels.alertname, state, since: .activeAt}'

# Is anything down?
make ps

# Is data still moving?
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count(), max(timestamp) FROM k2.silver_trades
   WHERE timestamp > now() - INTERVAL 5 MINUTE GROUP BY exchange"

# Is the lake keeping up? Ingest lag, and how long since each table last committed.
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_max_kafka_ts_seconds' | jq -r '.data.result[].value[1]'
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_last_commit_ts_seconds' | \
  jq -r '.data.result[] | "\(.metric.table) \(.value[1])"'

# Did the last nightly audit pass? Non-zero is LakeAuditFailed's input; the
# failing rows themselves are in lake.audit.checks (see lake-audit-failed.md).
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=k2_lake_audit_failures_total' | jq -r '.data.result[].value[1]'
```

> **Four `IcebergOffload*` alerts fire continuously and are expected**, not
> incidents: `IcebergOffloadLagElevated`, `IcebergOffloadLagCritical`,
> `IcebergOffloadThroughputLow` and `IcebergOffloadWatermarkStale`. The v2 hot
> tier has had no producer since the Kotlin handlers retired on 2026-08-26
> ([ADR-019](../adr/ADR-019-rust-capture-tier.md)), so `k2.*` gains no rows and
> the offload watermark cannot advance. They are deleted with `docker/offload/`
> in the Phase D PR. Triage the v3 `Capture*` alerts first; those four are noise
> until then.

Then:

- **Hot tier affected** (no trades arriving, ClickHouse down) → [failure-recovery.md](./failure-recovery.md)
- **Broker-level** (topics, partitions, schema registry) → [redpanda.md](./redpanda.md)
- **v3 lake** (ingest behind, audit failed, catalog or object store down, disk filling) →
  the `lake-*` runbooks

Hot-tier problems are user-visible and take priority.

The v3 lake inverts that priority, and it is worth knowing before triaging one. The lake
is the system of record ([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)), so
lake lag is a **countdown against Redpanda's 48 h raw retention** rather than an
inconvenience, while the v3 hot tier is derived and rebuildable
([ADR-025](../adr/ADR-025-clickhouse-derived-hot-tier.md)) — losing it costs a restore,
not data. Once Phase E lands, "hot tier first" stops being the right instinct.

## Escalation

| Condition | Action |
|-----------|--------|
| Recovery not achieved within the runbook's MTTR + 30 min | Escalate to the platform owner |
| Root cause is a code bug | Open an issue, link the runbook section that failed |
| More than 3 occurrences in 24 h | Stop restarting; find the cause |
| Data loss suspected | Escalate immediately — reconcile hot vs cold using [../operations/data-inspection.md](../operations/data-inspection.md#warm-vs-cold-reconciliation) before taking any destructive action |

## Archived v2 Kotlin runbooks

The feed-handler crash procedure and the three `FeedHandler*` alert rules moved to
[`legacy/v2-kotlin/runbooks/`](../../legacy/v2-kotlin/runbooks/) when the Kotlin capture
tier retired ([ADR-019](../adr/ADR-019-rust-capture-tier.md)). The measured MTTR is kept
as measured; the alerts and the `feed_handler_*` metric family no longer exist, so do not
follow it against this stack.

## v1 runbooks

The 14 archived v1 runbooks — Kafka, Spark Streaming, Prefect OHLCV pipeline, blue-green
deploys, checkpoint corruption — are kept for reference in
[`legacy/v1/docs/runbooks/`](../../legacy/v1/docs/runbooks/). They describe an
architecture that no longer exists; do not follow them against the v2 stack.
