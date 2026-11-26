# Runbooks

Incident procedures for the running stack: the capture tier, Redpanda, the Iceberg catalog and
object store, Airflow and the host under them. The ClickHouse hot tier is paused behind the compose
profile `hot-gold` ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)): its sections and runbooks apply only while that profile
runs, and [pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md) is how it is paused and started.
The v3 lake ingest pipeline is retired and archived in
[`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/) with its runbooks
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)): no live data reaches the lake until
[phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan, and the captures keep writing to Redpanda meanwhile. Runbooks record
*how*; ADRs record *why* (`../adr/`). Every Prometheus alert annotation points at one of
these; start from the alert that fired, or from the "when to use" column below. Day-to-day
operation (start, stop, health, consoles, recreating one service, rolling out a change) is
not a runbook: it is the [operator guide](../operations/README.md).

Load secrets before running any command here: `set -a && . ./.env && set +a`

## Conventions

- One task per file, `<slug>.md`. Updating an existing runbook beats a near-duplicate.
- Shape: **symptom → detection (name the alert) → expected behaviour → recovery commands
  → measured MTTR**. [`template.md`](./template.md) is that shape, empty.
- Exact copy-pasteable commands, never paraphrases. Verify them before writing them down;
  a runbook nobody ran is fiction.
- MTTR is measured by inducing the failure, not estimated.
- No decision rationale here, link the ADR.
- A PR that invalidates a runbook's steps updates it and its index row in the same PR.
- Every alert in `docker/prometheus/rules/` names a runbook as a `runbook:` annotation,
  and that path must resolve. Two gates in `scripts/check-docs.sh`, because one was not
  enough: **(d)** the paths present resolve, **(d2)** every `- alert:` block has one.
  Only (d) existed until 2026-09-03, and the six ClickHouse rules passed it by carrying
  no annotation at all — the last `**Runbook:**`-inside-the-description holdout was
  converted then, so the annotation form is now the only form.
- Write it with the `/runbook` skill.

## Index

### Infrastructure (written for the v2 stack)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [failure-recovery.md](./failure-recovery.md) | The infrastructure failures tested on 2026-02-19: broker restart, ClickHouse restart, capture crash, MinIO down, network partition; the batch-job failure (§4) retired with the v3 lake ingest. Plus §7, ClickHouse resource pressure: diagnosed, **not induced**, no MTTR. Plus §8, a host or Docker VM killed: when it happened, and recording the loss window. The ClickHouse sections need the `hot-gold` profile running (ADR-057), and the six ClickHouse alerts are removed while it is paused | `CaptureDown`, `CaptureProduceErrors` |
| [redpanda.md](./redpanda.md) | Topic, partition, consumer-group or schema-registry problems; broker health | `CaptureProduceErrors` |

The six runbooks for the v2 ClickHouse→Iceberg offload were archived with the code they
described; they are kept unmodified in
[`legacy/v2-offload/runbooks/`](../../legacy/v2-offload/README.md) and describe a path that
no longer exists, do not follow them against this stack.

### v3 capture tier (Phase C: written ahead of the code; see the note below)

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
> not measurements, which is exactly the distinction this directory's conventions
> exist to protect.

### Orchestration (Airflow 3)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [airflow-scheduling.md](./airflow-scheduling.md) | The Airflow scheduler stopped (§1, drilled on 2026-11-25), a task failed after its retries (§2), or a stale DAG folder (§3). Airflow runs with no DAG until phase 002 brings one per product, so §2 and §3 have nothing to act on before then; the v3 lake's lock and catch-up sections retired with its DAGs | `AirflowSchedulerHeartbeatLost`, `AirflowTaskFailed` |

### Host / operations

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [fresh-install.md](./fresh-install.md) | Bringing the stack up from a fresh clone or after a Docker wipe: prerequisites, the measured timeline, what `docker compose ps` should show, and the capture image build race | none, manual check |
| [docker-desktop-disk.md](./docker-desktop-disk.md) | Host `/` filling while `docker system df` reports far less: reclaiming space from a bloated `Docker.raw` with `fstrim`. Read the warning before touching the disk-image-size slider | none, manual check |
| [lake-backup-and-restore.md](./lake-backup-and-restore.md) | The host copy of what nothing can rebuild, and its restore: what it holds and what it does not (same disk, writable by anything running as you), `make backup`, `make backup-install`, `make health` step (5). The four tables it copied retired with the v3 lake, so until [phase 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) compiles the backup set from contracts it copies no table and says so with a SKIP line; the copies taken before stay in `~/k2-backup`. First copy measured 2026-11-26 (1 min 41 s, 25.3 GiB); **the restore is not yet drilled** | none: `make health` step (5), manual check |
| [docker-vm-oom-kill.md](./docker-vm-oom-kill.md) | Every container gone at once and `systemctl --user status docker-desktop` says `Result: oom-kill`: the host's out-of-memory killer ended the Docker VM. Confirm the cause, start Docker Desktop, start what did not come back, record the capture outage; then size the VM against host RAM. One occurrence, 2026-11-25: 10 min 13 s from the kill to `make health` PASS, capture off 343 s | none: no container can see host memory, manual check |
| [pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md) | Pausing the ClickHouse hot tier behind the compose profile `hot-gold`, or starting it again (`docker compose --profile hot-gold up -d clickhouse`, never a bare `up -d`): the two 7-day clocks (committed offsets, topic retention) and the hole a resume after them leaves, the consumer-group and lag checks after a resume, and why no reload from the lake can fill that hole any more. Read-only steps run 2026-11-26; the stop, remove and start rendered with `--dry-run` only | none: ADR-057 removes the ClickHouse alerts with the pause, manual check |
| [drop-and-rebuild.md](./drop-and-rebuild.md) | Deleting a lake table, view or namespace, or a MinIO bucket: `make drop` copies it to `$K2_BACKUP_DIR/drop/<date>/`, compares count and bytes, then purges it through the catalog or removes the bucket; `DRY_RUN=1` lists only. What it refuses, a drop that stopped, and restoring a table or bucket from its copy (drilled 2026-11-26 on scratch objects) | none, manual check |
| [upgrade-a-pinned-component.md](./upgrade-a-pinned-component.md) | Moving a base image pinned by digest, or a jar baked in with its sha256, to a new release: find the tag, resolve the digest, the jar versions a Spark release names and their sha256, every place a pin lives, a build and a read-only proof under a tag no service runs, the rollback. Worked example: the Spark rebase onto the official image, 2026-11-26; the build and proof measured, **the rollback not yet run** | none, manual check (Dependabot's monthly docker pull requests) |

### Archived with the v3 lake ingest pipeline

The runbooks of the v3 lake tier and its hourly dbt marts retired with them on 2026-11-26 and are
kept unchanged in
[`legacy/v3-lake-ingest/docs/runbooks/`](../../legacy/v3-lake-ingest/docs/runbooks/); their
alerts were removed and their commands read tables, DAGs and scripts that no longer exist, so do
not follow them against this stack. A short page at each old path says what replaces it:

| Runbook (archived) | What it covered | What replaces it |
|---------|-------------|------------------|
| [lake-ingest-lag.md](./lake-ingest-lag.md) | The lake ingest behind Redpanda | the connector-failure runbook of phase 002, the Kafka runbooks of phase 004 |
| [lake-ingest-batch-audit.md](./lake-ingest-batch-audit.md) | An ingest batch that did not match the offsets it read | the Kafka connector's exactly-once resume, phase 004 |
| [lake-audit-failed.md](./lake-audit-failed.md) | The nightly lake audit failed | the failed-audit runbook of phase 002 |
| [lake-recovery.md](./lake-recovery.md) | Lakekeeper or MinIO down, an ingest killed mid-run, a replay, ClickHouse rebuilt from the lake | the backup, restore and rebuild-from-empty runbook of phase 004 |
| [lake-disk-usage-high.md](./lake-disk-usage-high.md) | The two lake disk alerts | the lake metrics and alerts of phase 004; meanwhile `df -h /` |
| [dbt-marts.md](./dbt-marts.md) | The hourly dbt marts not rebuilt | the dbt runner and its run records, phase 002 |
| [clickhouse-rebuild-from-lake.md](./clickhouse-rebuild-from-lake.md) | ClickHouse `gold` reloaded from the lake's gold | nothing: the lake's gold was dropped, and a resume reads Redpanda only, within 7 days ([pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md)) |

The phases are those of the
[unified platform plan](../plans/2026-11-26-unified-product-platform/README.md).

## Triage

```bash
# What is actually firing?
curl -s localhost:9090/api/v1/alerts | jq '.data.alerts[] | {alertname: .labels.alertname, state, since: .activeAt}'

# Is anything down?
make ps

# Is data still moving? Records each venue's capture had acknowledged by Redpanda in the last
# 5 minutes; a venue at 0 while its feed is live is CaptureProduceStalled's case.
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange) (increase(k2_capture_records_delivered_total[5m]))' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'
```

Then:

- **A capture** (down, stale, gaps, produce errors) → the `capture-*` runbooks above, by the
  alert that fired
- **Broker-level** (topics, partitions, schema registry) → [redpanda.md](./redpanda.md)
- **Hot tier affected** (no trades arriving, ClickHouse down), only while the `hot-gold`
  profile runs: ClickHouse is paused (ADR-057), and a stopped one is the paused state, not an
  incident → [failure-recovery.md](./failure-recovery.md)
- **Catalog or object store** (`lakekeeper` or `minio` unhealthy in `make health` step (1)) →
  [failure-recovery.md](./failure-recovery.md) §5 for MinIO; for either, `docker logs --tail 100
  k2-<service>` first

Until phase 004 the lake receives nothing and the hot tier is paused, so **Redpanda holds the only
copy** of what the captures record: 48 h or 2 GiB per partition on the `raw.*` topics, 7 days on
`trades.*` and `book.*` ([`docker/redpanda/init.sh`](../../docker/redpanda/init.sh)). A capture or
broker failure is therefore the one that loses data, and it goes first. When phase 004's first
product runs it starts at the start of the log; nothing older is recovered.

## Escalation

| Condition | Action |
|-----------|--------|
| Recovery not achieved within the runbook's MTTR + 30 min | Escalate to the platform owner |
| Root cause is a code bug | Open an issue, link the runbook section that failed |
| More than 3 occurrences in 24 h | Stop restarting; find the cause |
| Data loss suspected | Escalate immediately. With the lake empty and the hot tier paused there is no second store to reconcile against: bound the window from the capture metrics ([capture-sequence-gaps.md](./capture-sequence-gaps.md)) and record it, as the capture runbooks' "Record the gap" says, before taking any destructive action |

## Archived v2 Kotlin runbooks

The feed-handler crash procedure and the three `FeedHandler*` alert rules moved to
[`legacy/v2-kotlin/runbooks/`](../../legacy/v2-kotlin/runbooks/) when the Kotlin capture
tier retired ([ADR-019](../adr/ADR-019-rust-capture-tier.md)). The measured MTTR is kept
as measured; the alerts and the `feed_handler_*` metric family no longer exist, so do not
follow it against this stack.

## v1 runbooks

The 14 archived v1 runbooks, Kafka, Spark Streaming, the v1 OHLCV pipeline, blue-green
deploys, checkpoint corruption, are kept for reference in
[`legacy/v1/docs/runbooks/`](../../legacy/v1/docs/runbooks/). They describe an
architecture that no longer exists; do not follow them against the v2 stack.
