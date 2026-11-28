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
> were induced against was the v2 ClickHouse→Iceberg offload, which is deleted. Its v3
> replacement, the lake ingest, retired in turn with the v3 lake ingest pipeline on 2026-11-26
> ([`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/),
> [ADR-056](../adr/ADR-056-unified-data-product-platform.md)), so §4 is retired and §5 covers a
> MinIO that holds no live data until phase 004. The remaining three infrastructure failures are
> unchanged and still measured.
>
> **The ClickHouse tables the February measurements read were dropped on 2026-08-27** at
> the Phase E cutover (`k2.silver_trades` and the rest of the `k2` database , 
> [`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md)). The verification
> queries below are written against the served `gold` tier that replaced them
> ([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md)); the "Measured"
> rows are the dated v2 observations, kept as written.
>
> **ClickHouse is paused** behind the compose profile `hot-gold`
> ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)): its container is stopped and removed, its volume kept. §2, §7, the
> ClickHouse case of §6, the hot-tier half of §8 and the ClickHouse checks in §1 and §5 apply
> only while that profile runs ([pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md)). The six
> ClickHouse alerts named here are removed with the pause, so none of them fires.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Redpanda restart | <2 min | ~10 s |
| 2 | ClickHouse restart (only with `hot-gold` running) | <3 min | ~32 s |
| 3 | Capture container crash | <1 min | ~30 s (Kotlin, 2026-02-19; not re-measured on Rust) |
| 4 | Spark batch-job failure (the lake ingest) | retired with the v3 lake ingest | |
| 5 | MinIO unavailable | <5 min | ~5 s (v2) |
| 6 | Network partition | <5 min | ~20–30 s |

---

## 1. Redpanda restart

**Symptom**, capture logs show produce failures; the records Redpanda acknowledges drop to zero.

**Detection**, `CaptureProduceErrors`, `CaptureProduceStalled`.

**Expected behaviour**, capture reconnects on its own; in-flight messages sit in the
librdkafka queue; while `hot-gold` runs, the ClickHouse `gold.q_trades` / `gold.q_book`
consumers resume from their last committed offset.

**Recovery**

```bash
docker compose restart redpanda
docker exec k2-redpanda rpk cluster health          # wait for healthy
docker exec k2-redpanda rpk topic list              # topics intact?

# Confirm capture delivers again: records each venue had acknowledged in the last 2 minutes
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange) (increase(k2_capture_records_delivered_total[2m]))' | \
  jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'
```

**Measured**, 10 s, 2026-02-19, on the v2 tier: 12 new rows ingested post-restart; all three
ClickHouse consumers resumed. No trade loss. If capture does not reconnect within a minute,
restart it (see §3).

---

## 2. ClickHouse restart

Applies only while the compose profile `hot-gold` runs (ClickHouse is paused, [ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)).

**Symptom**, queries fail; Grafana ClickHouse panels go blank.

**Detection**, manual: the alert that watched this, `ClickHouseDown`, was removed with the pause
(ADR-057) and returns with the tier. The `SELECT 1` in the recovery below is the check.

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

```

Prometheus no longer scrapes ClickHouse: its scrape job was removed with the pause (ADR-057).

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

## 4. Spark batch-job failure (the lake ingest)

**Retired** with the v3 lake ingest pipeline on 2026-11-26: no Spark job runs on this stack until
[phase 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) of the
unified platform plan, whose connector-failure runbook covers a failed product run. The section
as it stood is in git history, and the lake recovery it pointed to is archived at
[`legacy/v3-lake-ingest/docs/runbooks/lake-recovery.md`](../../legacy/v3-lake-ingest/docs/runbooks/lake-recovery.md).

---

## 5. MinIO unavailable

**Symptom**, `make health` step (1) lists `minio` unhealthy; Trino and Lakekeeper calls that touch
storage fail, and so do `make backup` and `make drop`. Capture and Redpanda are unaffected. Since
phase 002 a product DAG's task that touches storage while MinIO is down fails, and its run with it
([airflow-scheduling.md](./airflow-scheduling.md) section 2); the landed files and bronze `main` it had
not yet moved are as before, so nothing published is lost. A Kafka product's run (`kraken_trades`,
since phase 004) commits its landing in one step or not at all, so its records wait in Redpanda and
the next run lands them from the last committed offsets, as long as the topic still holds them
(`scripts/verify/kafka-path.sh` claim a5 proves the same for a load killed before its landing commit;
past the topic's retention it is [accept-data-loss.md](./accept-data-loss.md)).

**Detection**, manual: `make health` step (1). There is no MinIO exporter, and the lake alerts
that would have followed retired with the v3 lake.

**Expected behaviour**, `restart: unless-stopped` brings a crashed MinIO back; a stopped one stays
down until started.

**Recovery**

```bash
docker compose start minio
# MinIO publishes no host port since phase 002, so ask it from inside its own container
docker exec k2-minio curl -fsS localhost:9000/minio/health/live && echo OK
```

**Measured**, ~5 s to restore, against the v2 offload. The hot tier gained 2 rows during a
30-second outage, confirming ingest was never in the blast radius; that isolation is a
property of the topology. The lake-side recovery time with MinIO stopped is not measured on this
stack. What phase 004 measured is the restore of a lake whose objects are gone, on a throwaway lake
only: `make restore-drill` restored it in 32.0 s on 2026-11-28
([lake-backup-and-restore.md](./lake-backup-and-restore.md) section 3b); no drill has run on the live
stack.

---

## 6. Network partition

**Symptom**, one container's consumers stall while everything else keeps running.

**Detection**, `CaptureDown` for a capture; for any other container, `make health` step (1)
once its healthcheck fails (the ClickHouse alerts were removed with the pause).

**Expected behaviour**, the isolated container reconnects when the partition heals and
consumers resume from their last committed offset. No corruption.

**Recovery**

```bash
docker network connect k2-net k2-capture-binance    # or whichever container was cut off
docker exec k2-capture-binance /k2-capture healthcheck
# With the hot-gold profile running, for ClickHouse: the consumer check of §2.
```

**Measured**, 20–30 s from reconnect (2026-02-19, v2). All three Kafka Engine consumers
recovered from their last committed offset with no data corruption.

---

## 7. ClickHouse resource pressure

Applies only while the compose profile `hot-gold` runs (ClickHouse is paused, [ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)); the three alerts below are removed with the pause.

**Not one of the six.** This section was written on 2026-09-03 to give
`ClickHouseHighMemoryUsage`, `ClickHouseQueryFailureRateHigh` and
`ClickHouseMergeQueueLarge` somewhere to point: all three had fired-and-nowhere-to-go
status, with no `runbook:` annotation, while `docs/architecture/11-observability.md`
claimed every alert carried one. **No MTTR here is measured** — none of these three has
been induced. The commands below were run against the live 24.3 stack on 2026-09-03 and
their output is what the healthy state looks like.

**Symptom**, queries get slower or start failing; `gold` reads time out.

**Detection**, manual while the pause lasts: the commands below. Until it, three alerts,
`ClickHouseHighMemoryUsage` (RSS > 85% of `OSMemoryTotal`, 5m; on Docker
Desktop that is the VM's memory, not the host's),
`ClickHouseQueryFailureRateHigh` (> 0.1 failed queries/s, 3m),
`ClickHouseMergeQueueLarge` (> 10 background merge tasks, 5m).

**Expected behaviour**, none of the three loses data while Redpanda still holds it: the
Kafka-engine consumers resume from their committed offsets. ClickHouse `gold` was derived from
the lake too (ADR-026), but the v3 lake's gold retired, so a lost row older than the topics' 7
days stays lost ([pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md)
section 3).

**Diagnose** — load `set -a && . ./.env && set +a`, then:

```bash
ch() { docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" "$@"; }

# Memory: what the alert's two series actually are
ch -q "SELECT metric, formatReadableSize(value) FROM system.asynchronous_metrics
       WHERE metric IN ('MemoryResident','OSMemoryTotal') ORDER BY metric"
# 2026-09-03: MemoryResident 1.46 GiB / OSMemoryTotal 39.16 GiB — 3.7%, nowhere near 85%

# Memory: who is holding it right now
ch -q "SELECT query_id, formatReadableSize(memory_usage) AS mem, elapsed
       FROM system.processes ORDER BY memory_usage DESC LIMIT 5"

# Query failures: the split, then the distinct exceptions behind it
ch -q "SELECT type, count() FROM system.query_log
       WHERE event_time > now() - INTERVAL 1 HOUR GROUP BY type ORDER BY type"
ch -q "SELECT any(exception), count() FROM system.query_log
       WHERE event_time > now() - INTERVAL 1 HOUR AND type = 'ExceptionWhileProcessing'
       GROUP BY substring(exception, 1, 60) ORDER BY 2 DESC LIMIT 3"

# Merge queue: what is merging, and which partition is fragmenting
ch -q "SELECT database, table, elapsed, progress, formatReadableSize(memory_usage)
       FROM system.merges ORDER BY elapsed DESC LIMIT 5"
ch -q "SELECT table, partition, count() AS parts FROM system.parts
       WHERE active AND database='gold' GROUP BY table, partition ORDER BY parts DESC LIMIT 5"
# 2026-09-03: no merges in flight, worst partition 7 active parts. Healthy.
```

**Recovery**

- **Memory.** Kill the query, not the server: `ch -q "KILL QUERY WHERE query_id = '<id>'"`.
  The `quant` role is already capped at 3 GiB (`max_memory_usage`, asserted by
  `make test-clickhouse`), so a runaway `default` query is the usual cause. A restart
  (`docker compose restart clickhouse`, §2) is the last resort and costs ~32 s.
- **Query failures.** Read the exception before acting. A `TIMEOUT_EXCEEDED` on a
  300 s read is a query problem; `MEMORY_LIMIT_EXCEEDED` is the row above;
  a Kafka decode error is a row in `gold.feed_errors`, not this section.
- **Merge queue.** Merges are throttled, not lost — a queue that is draining needs
  nothing. A queue that is flat while inserts continue means the insert rate has
  outrun merge throughput: the fix is fewer, bigger inserts
  (`kafka_max_block_size` in [`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql)),
  not more merge threads on a 16-CPU host.

**Measured**, not measured. Inducing memory pressure on the served tier is the obvious
next chaos script; until it exists, treat the numbers above as the healthy baseline and
nothing more.

---

## 8. Host or Docker VM killed: what the outage cost

**Not one of the six**, and not induced: written after the host's out-of-memory killer
killed the Docker VM at 22:33:43 UTC on 2026-11-25 (stack back at 22:39:26 UTC).

**Symptom**, every container restarted at once, or the whole stack was down and came back.

**Detection**, manual: no alert covers it ([docker-vm-oom-kill.md](./docker-vm-oom-kill.md)).

**Expected behaviour**, the captures reconnect and Redpanda keeps what it acknowledged and wrote
to disk; what the venues sent while the VM was down is gone for good. On 2026-11-25 this section
went on to compare the hot tier with the v3 lake minute by minute and to file the outage as a row
of the lake's audit table. Both stores are stood down now (ClickHouse paused, ADR-057; the v3 lake
retired and archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/)), so there is
no second store to compare; that comparison and its 2026-11-25 result are in git history, and
the audit row survives only in the host copies under `~/k2-backup/` (the nightly copy's `lake/`
and the copy `make drop` took of the dropped namespaces).

**Recovery**

```bash
# 1. When was the VM killed? The kernel log of the current boot (after a host reboot, add -b -1).
journalctl -k --utc -o short-iso --since '2026-11-25 22:00 UTC' --until '2026-11-25 23:00 UTC' \
  -g 'Out of memory' --no-pager
# 2026-11-25: one line at 22:33:43, "Out of memory: Killed process <pid> (qemu-system-x86)".
```

2. **When was capture back?** The first fresh `k2_capture_last_message_ts_seconds` per venue
   after the restart, from Prometheus; the window is the kill to that time.
3. **Record the outage**: the venue (`stack` when all of them), the window to the second in UTC
   and the cause. The lake's audit table that held these records retired with the v3 lake, and
   an operator's record of lost data returns with the "accept data loss" runbook in
   [phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md);
   until then, write it in the incident's issue.

**Measured**, no MTTR: this is investigation, not repair. Step 1 was run read-only on
2026-11-26 against the host's journal.

**Last verified:** 2026-11-26, step 1.

---

## Re-running these tests

Each mode is induced with a single command, `docker compose restart <svc>`,
`docker compose stop <svc>`, or `docker network disconnect k2-net <container>`. Check that
capture delivers before and after (§1's query), and confirm continuity across the outage window
rather than just "it came back up". `make chaos` scripts the capture inductions; the lake
inductions retired with the v3 lake.

## Related

- [../operations/observability.md](../operations/observability.md), the alerts referenced above
- [../operations/latency-budgets.md](../operations/latency-budgets.md), why lag rather than loss is the failure mode
- [README.md](./README.md), full runbook index
