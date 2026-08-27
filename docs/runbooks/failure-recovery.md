# Runbook: Failure Recovery

Six failure modes, each deliberately induced and recovered on 2026-02-19. All six passed;
the worst observed MTTR was 32 seconds. Recovery is automatic in every case, this runbook
exists to tell you what "normal recovery" looks like so you can spot when it isn't.

> **Mode 3 was measured against the Kotlin feed handlers, which have since retired**
> ([ADR-019](../adr/ADR-019-rust-capture-tier.md)). Its procedure and its measured
> 30 s MTTR are archived verbatim at
> [`legacy/v2-kotlin/runbooks/feed-handler-crash.md`](../../legacy/v2-kotlin/runbooks/feed-handler-crash.md);
> the live equivalent for the Rust capture tier is [capture-down.md](./capture-down.md),
> whose MTTR is not yet measured.
>
> **§4 and §5 no longer describe what was measured in February either.** The batch job they
> were induced against was the v2 ClickHouse→Iceberg offload, which is deleted; both now
> point at the v3 lake path that replaced it, and that path's numbers come from the Phase D
> burn-in rather than from 2026-02-19. The remaining three infrastructure failures are
> unchanged and still measured.
>
> **The ClickHouse tables the February measurements read were dropped on 2026-08-27** at
> the Phase E cutover (`k2.silver_trades` and the rest of the `k2` database , 
> [`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md)). The verification
> queries below are written against the served `gold` tier that replaced them
> ([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md)); the "Measured"
> rows are the dated v2 observations, kept as written.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Redpanda restart | <2 min | ~10 s |
| 2 | ClickHouse restart | <3 min | ~32 s |
| 3 | Capture container crash | <1 min | ~30 s (Kotlin, 2026-02-19; not re-measured on Rust) |
| 4 | Spark / Prefect batch-job failure | <15 min | next scheduled run |
| 5 | MinIO unavailable | <5 min | ~5 s |
| 6 | Network partition | <5 min | ~20–30 s |

---

## 1. Redpanda restart

**Symptom**, capture logs show produce failures; ClickHouse insert rate drops to zero.

**Detection**, `CaptureProduceErrors`, `CaptureProduceStalled`.

**Expected behaviour**, capture reconnects on its own; in-flight messages sit in the
librdkafka queue; the ClickHouse `gold.q_trades` / `gold.q_book` consumers resume from
their last committed offset.

**Recovery**

```bash
docker compose restart redpanda
docker exec k2-redpanda rpk cluster health          # wait for healthy
docker exec k2-redpanda rpk topic list              # topics intact?

# Confirm ingestion resumed
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM gold.trades FINAL WHERE exchange_ts > now() - INTERVAL 2 MINUTE GROUP BY exchange"
```

**Measured**, 10 s, 2026-02-19, on the v2 tier: 12 new rows ingested post-restart; all three
ClickHouse consumers resumed. No trade loss. If capture does not reconnect within a minute,
restart it (see §3).

---

## 2. ClickHouse restart

**Symptom**, queries fail; Grafana ClickHouse panels go blank.

**Detection**, `ClickHouseDown`.

**Expected behaviour**, Redpanda retains messages for its retention window, so capture
keeps producing normally. The `gold.q_*` consumers resume on restart and the MVs replay
from the retained offsets; `ReplacingMergeTree` makes any overlap one row under `FINAL` , 
no data loss, no duplicates.

**Recovery**

```bash
docker compose restart clickhouse
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q "SELECT 1"

# Consumers reattached, no exceptions?
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT table, num_messages_read, exceptions.text FROM system.kafka_consumers WHERE database='gold' FORMAT Vertical"

# Prometheus listener back up (it starts with the server)
curl -s localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="clickhouse") | .health'
```

**Measured**, 32 s, the slowest of the six (2026-02-19, v2: `k2.silver_trades` resumed
cleanly and the Prometheus listener on 9363 came back with it). Check for row-count continuity across the
outage window rather than assuming it.

---

## 3. Capture container crash

**Symptom**, one exchange stops appearing in the v3 topics; the other two are fine.

**Detection**, `CaptureDown` for that exchange (scrape target down), or the container
healthcheck flipping to unhealthy.

**Expected behaviour**, full cross-exchange isolation. The Redpanda topics stay alive,
the other two containers are untouched, and the crashed one resubscribes on start with a
fresh book snapshot.

**Recovery**, [capture-down.md](./capture-down.md) is the procedure; it carries the
`k2-capture` specifics (book resync, `conn_id` rotation, what a restart costs the book).

```bash
docker compose ps capture-binance
docker logs --tail 100 k2-capture-binance
docker compose start capture-binance
```

**Measured**, 30 s from `docker compose start`, on 2026-02-19, **against the Kotlin
feed handler this replaced**, see the archived
[feed-handler-crash.md](../../legacy/v2-kotlin/runbooks/feed-handler-crash.md). The Rust
tier has not been through a fault injection yet; `make chaos` is what fills this row in,
and until it runs this is an inherited number, not a measurement of what is deployed.

---

## 4. Spark / Prefect batch-job failure

**Symptom**, a Prefect flow run for `lake-ingest-5min` is marked Failed; lake row counts
stop advancing.

**Detection**, `LakeIngestFailed`, then `LakeIngestLagHigh` from
[`docker/prometheus/rules/lake-alerts.yml`](../../docker/prometheus/rules/lake-alerts.yml)
(see [observability.md](../operations/observability.md#alert-rules)); also visible in
Prefect run history.

**Expected behaviour**, the consumed Kafka offsets are written into the Iceberg snapshot
summary by the same commit that writes the rows ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)),
so a run that never committed left the offsets where they were and the next scheduled run
re-reads the same range. Idempotent by construction: no duplicates, no gap.

**Recovery**, usually none. Wait for the next 5-minute run.

```bash
# Force a run rather than waiting
docker exec k2-prefect-server prefect deployment run 'lake-ingest/lake-ingest-5min'
```

**Measured**, not yet verified for the lake path; the Phase D burn-in
(`scripts/chaos/lake-ingest-kill.sh`) is what fills this in. The full procedure, including
the three invariant checks that prove a re-run was safe, is
[lake-recovery.md §5](./lake-recovery.md#5-ingest-killed-mid-run), this section is the
triage entry point, not a second copy of it.

---

## 5. MinIO unavailable

**Symptom**, lake ingest and maintenance runs fail; the hot tier is completely unaffected.

**Detection**, `LakeIngestFailed` and Prefect flow-run failures. There is no MinIO
exporter, and `LakeExporterDown` does not fire: `docker/lake/metrics.py` reads the catalog,
never MinIO.

**Expected behaviour**, the ingest fails cleanly with no committed Iceberg snapshot;
ClickHouse ingest continues; the lake defers until MinIO is back. Uncommitted Parquet files
are orphans no reader sees, reclaimed by the nightly maintenance pass, see
[lake-recovery.md §4](./lake-recovery.md#4-minio-down).

**Recovery**

```bash
docker compose start minio
curl -fsS localhost:9000/minio/health/live && echo OK
```

**Measured**, ~5 s to restore, against the v2 offload. The hot tier gained 2 rows during a
30-second outage, confirming ingest was never in the blast radius; that isolation is a
property of the topology and did not change with the lake, but the lake-side recovery time
is Phase D's to measure.

---

## 6. Network partition

**Symptom**, one container's consumers stall while everything else keeps running.

**Detection**, `CaptureDown` or `ClickHouseDown` depending on which container
is isolated.

**Expected behaviour**, the isolated container reconnects when the partition heals and
consumers resume from their last committed offset. No corruption.

**Recovery**

```bash
docker network connect k2-net k2-clickhouse    # or whichever container was cut off
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT table, num_messages_read, exceptions.text FROM system.kafka_consumers WHERE database='gold' FORMAT Vertical"
```

**Measured**, 20–30 s from reconnect (2026-02-19, v2). All three Kafka Engine consumers
recovered from their last committed offset with no data corruption.

---

## Re-running these tests

Each mode is induced with a single command, `docker compose restart <svc>`,
`docker compose stop <svc>`, or `docker network disconnect k2-net <container>`. Take a row
count before and after, and confirm continuity across the outage window rather than just
"it came back up". Detailed lake-specific procedures live in the sibling `lake-*.md`
runbooks, and `make chaos` scripts the lake and capture inductions.

## Related

- [../operations/observability.md](../operations/observability.md), the alerts referenced above
- [../operations/latency-budgets.md](../operations/latency-budgets.md), why lag rather than loss is the failure mode
- [README.md](./README.md), full runbook index
