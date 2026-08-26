# Runbook: Failure Recovery

Six failure modes, each deliberately induced and recovered on 2026-02-19. All six passed;
the worst observed MTTR was 32 seconds. Recovery is automatic in every case — this runbook
exists to tell you what "normal recovery" looks like so you can spot when it isn't.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Redpanda restart | <2 min | ~10 s |
| 2 | ClickHouse restart | <3 min | ~32 s |
| 3 | Feed handler crash | <1 min | ~30 s |
| 4 | Spark / Prefect offload failure | <15 min | next scheduled run |
| 5 | MinIO unavailable | <5 min | ~5 s |
| 6 | Network partition | <5 min | ~20–30 s |

---

## 1. Redpanda restart

**Symptom** — feed handler logs show produce failures; ClickHouse insert rate drops to zero.

**Detection** — `FeedHandlerHighErrorRate`, `ClickHouseBronzeInsertRateLow`.

**Expected behaviour** — feed handlers reconnect on their own; in-flight messages sit in
the OS send queue; ClickHouse Kafka Engine consumers resume from their last committed offset.

**Recovery**

```bash
docker compose restart redpanda
docker exec k2-redpanda rpk cluster health          # wait for healthy
docker exec k2-redpanda rpk topic list              # topics intact?

# Confirm ingestion resumed
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 2 MINUTE GROUP BY exchange"
```

**Measured** — 10 s. 12 new rows ingested post-restart; all three ClickHouse consumers resumed.
No trade loss. If handlers do not reconnect within a minute, restart them (see §3).

---

## 2. ClickHouse restart

**Symptom** — queries fail; Grafana ClickHouse panels go blank.

**Detection** — `ClickHouseDown`.

**Expected behaviour** — Redpanda retains messages for its retention window, so feed
handlers keep producing normally. Consumers resume on restart and materialized views
replay from the retained offsets — no data loss.

**Recovery**

```bash
docker compose restart clickhouse
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q "SELECT 1"

# Consumers reattached, no exceptions?
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT table, num_messages_read, last_exception FROM system.kafka_consumers WHERE database='k2' FORMAT Vertical"

# Prometheus listener back up (it starts with the server)
curl -s localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="clickhouse") | .health'
```

**Measured** — 32 s, the slowest of the six. `silver_trades` resumed cleanly and the
Prometheus listener on 9363 came back with it. Check for row-count continuity across the
outage window rather than assuming it.

---

## 3. Feed handler crash

**Symptom** — one exchange stops appearing in `silver_trades`; the other two are fine.

**Detection** — `FeedHandlerDown` for that exchange (scrape target down for 2m), or the
container healthcheck flipping to unhealthy.

**Expected behaviour** — full cross-exchange isolation. The Redpanda topic stays alive,
other handlers are untouched, and the crashed handler resumes on start.

**Recovery**

```bash
docker compose ps feed-handler-binance
docker logs --tail 100 k2-feed-handler-binance
docker compose start feed-handler-binance

# If it was a config change rather than a crash:
docker compose up -d --force-recreate --no-deps feed-handler-binance
```

**Measured** — 30 s from `docker compose start`. Kraken and Coinbase were unaffected
throughout.

**Known trap** — if the handler is stuck in a schema-registration retry loop at startup
(seen with Coinbase), `docker restart` will not clear it. Force-recreate for a fresh JVM.

---

## 4. Spark / Prefect offload failure

**Symptom** — a Prefect flow run is marked Failed; cold-tier row counts stop advancing.

**Detection** — `IcebergOffloadConsecutiveFailures`, `IcebergOffloadWatermarkStale` (see
[observability.md](../observability.md#iceberg-offload-alertsyml--cold-tier-9)); also
visible in Prefect run history.

**Expected behaviour** — the watermark is only advanced *after* a successful Iceberg
write, so a failed run leaves it untouched and the next scheduled run re-reads the same
window. Idempotent by construction: no duplicates, no gap.

**Recovery** — usually none. Wait for the next 15-minute run.

```bash
# Watermark should be unchanged, status 'failed'
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c \
  "SELECT table_name, status, last_offload_timestamp, last_successful_run FROM offload_watermarks"

# Force a run rather than waiting
docker exec k2-prefect-server prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'
```

**Measured** — watermark held at its pre-failure value; exactly-once confirmed on resume.

If the watermark is stuck in `running` after a hard kill, see
[iceberg-offload-watermark-recovery.md](./iceberg-offload-watermark-recovery.md).

---

## 5. MinIO unavailable

**Symptom** — offload flows fail; the hot tier is completely unaffected.

**Detection** — offload flow failures in Prefect. There is no MinIO exporter.

**Expected behaviour** — offload fails cleanly with no partial Iceberg writes; ClickHouse
ingest continues; the cold tier defers until MinIO is back.

**Recovery**

```bash
docker compose start minio
curl -fsS localhost:9000/minio/health/live && echo OK
```

**Measured** — ~5 s to restore. The hot tier gained 2 rows during a 30-second outage,
confirming ingest was never in the blast radius.

---

## 6. Network partition

**Symptom** — one container's consumers stall while everything else keeps running.

**Detection** — `FeedHandlerDown` or `ClickHouseDown` depending on which container
is isolated.

**Expected behaviour** — the isolated container reconnects when the partition heals and
consumers resume from their last committed offset. No corruption.

**Recovery**

```bash
docker network connect k2-net k2-clickhouse    # or whichever container was cut off
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT table, num_messages_read, last_exception FROM system.kafka_consumers WHERE database='k2' FORMAT Vertical"
```

**Measured** — 20–30 s from reconnect. All three Kafka Engine consumers recovered from
their last committed offset with no data corruption.

---

## Re-running these tests

Each mode is induced with a single command — `docker compose restart <svc>`,
`docker compose stop <svc>`, or `docker network disconnect k2-net <container>`. Take a row
count before and after, and confirm continuity across the outage window rather than just
"it came back up". Detailed offload-specific procedures live in the sibling
`iceberg-*.md` runbooks.

## Related

- [../observability.md](../observability.md) — the alerts referenced above
- [../latency-budgets.md](../latency-budgets.md) — why lag rather than loss is the failure mode
- [README.md](./README.md) — full runbook index
