# Runbook: Redpanda Operations

**Severity**: High (message broker — impacts all 3 exchanges if down)
**Last Updated**: 2026-08-26
**Replaces**: `kafka-runbook.md` (Kafka replaced by Redpanda in v2 — ADR-001)

Every `rpk` command below was run against the live single-broker cluster on
2026-08-26 and the output pasted is what it printed. Commands that could not be
run are marked inline; nothing here is paraphrased from memory.

---

## Overview

Redpanda is the Kafka-compatible message broker. It runs as a single-broker cluster
(`k2-redpanda`) carrying nine live v3 topics — raw frames, trades and L2 book per
exchange — plus six frozen v2 topics.

| Topic | Partitions | Payload | Consumers |
|-------|-----------:|---------|-----------|
| `market.crypto.v3.raw.<ex>` | 12 | Avro `RawMessage` — every frame verbatim | — (Phase D lake ingest) |
| `market.crypto.v3.trades.<ex>` | 12 | Avro `Trade` | — (Phase E hot tier) |
| `market.crypto.v3.book.<ex>` | 12 | Avro `BookSnapshotL2`, top-20 at 1 Hz | — (Phase E hot tier) |

`<ex>` ∈ {`binance`, `kraken`, `coinbase`} — 9 topics, 108 partitions, produced by the
three `k2-capture-*` containers. Keys are plain UTF-8: `trades.*` and `book.*` key on the
**canonical** symbol (`BTC/USDT`), `raw.*` on the **wire** symbol the venue used
(`BTCUSDT`) — a raw frame carries the venue's own name for the instrument. Frames that
belong to no instrument (heartbeats, subscribe acks) have no key at all. Retention
is 48 h + 512 MiB/partition on `raw.*` and 7 d on `trades.*`/`book.*`, set by
[`docker/redpanda/init.sh`](../../docker/redpanda/init.sh), which is also the authority
on the topic table above.

> **`market.crypto.trades.<ex>` and `market.crypto.trades.<ex>.raw` are frozen v2
> topics** — 40 partitions each for Binance, 20 for Kraken and Coinbase, 160 in total.
> Their only producers were the Kotlin feed handlers, and nothing has produced to them
> **since the retirement deploy at `2026-08-26T18:58:29Z`**
> ([ADR-019](../adr/ADR-019-rust-capture-tier.md)) — that is the timestamp of the last
> row the ClickHouse consumers wrote, `SELECT max(ingestion_timestamp) FROM
> k2.bronze_trades_kraken`, and it is when this became true rather than when the PR was
> written. They still exist, still hold their retained data, and the Kafka-engine
> consumers are still attached with nothing arriving. `redpanda-init` still creates them.
> They are deleted with the `k2` database at the Phase E cutover. Do not use them as a
> liveness signal.

Topics are created by the `redpanda-init` one-shot service at startup, which also hardens
the internal `_schemas` topic (compact cleanup policy, infinite retention) to prevent
`offset_out_of_range` on schema-registry restart.

Ports: Kafka API `9092` (external listener `19092`), admin API `9644`, schema registry `8081`.

**Redpanda Console**: http://localhost:8080

---

## Topic Management (rpk)

```bash
# List topics
docker exec k2-redpanda rpk topic list

# Describe a topic (config; add -p for per-partition offsets)
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance -p

# Create a topic (normally done by redpanda-init)
docker exec k2-redpanda rpk topic create my-topic --partitions 12 --replicas 1

# Delete a topic (use with caution — drops all data)
docker exec k2-redpanda rpk topic delete my-topic

# Consume — records are Confluent-framed Avro, so print the key, not the value
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance -n 3 -f '%p %o %k\n'
```

`rpk topic list`, 2026-08-26:

```
NAME                               PARTITIONS  REPLICAS
_schemas                           1           1
market.crypto.trades.binance       40          1     <- frozen v2
market.crypto.trades.binance.raw   40          1     <- frozen v2
market.crypto.trades.coinbase      20          1     <- frozen v2
market.crypto.trades.coinbase.raw  20          1     <- frozen v2
market.crypto.trades.kraken        20          1     <- frozen v2
market.crypto.trades.kraken.raw    20          1     <- frozen v2
market.crypto.v3.book.binance      12          1
market.crypto.v3.book.coinbase     12          1
market.crypto.v3.book.kraken       12          1
market.crypto.v3.raw.binance       12          1
market.crypto.v3.raw.coinbase      12          1
market.crypto.v3.raw.kraken        12          1
market.crypto.v3.trades.binance    12          1
market.crypto.v3.trades.coinbase   12          1
market.crypto.v3.trades.kraken     12          1
```

The v3 values are Avro with a Confluent framing prefix, so `rpk topic consume` without a
format string prints binary. `-f '%p %o %k\n'` gives partition, offset and the symbol key,
which is what you actually want when checking flow:

```
3 0 SOL/USDT
3 1 SOL/USDT
3 2 SOL/USDT
```

The same command on `market.crypto.v3.raw.binance` prints the wire spelling instead, which
is the difference above and not a bug:

```
2 0 BNBUSDT
2 1 BNBUSDT
2 2 DOGEUSDT
```

To decode a value, use Redpanda Console — it resolves the schema id against the built-in
registry. `rpk` will not.

---

## Consumer Lag Inspection

```bash
# List all consumer groups
docker exec k2-redpanda rpk group list

# Describe a group (lag per partition)
docker exec k2-redpanda rpk group describe clickhouse_bronze_kraken
```

`rpk group list`, 2026-08-26 — **read the group names carefully**:

```
BROKER  GROUP                                STATE
0       clickhouse_bronze_coinbase_consumer  Stable
0       clickhouse_bronze_kraken             Stable
0       clickhouse_bronze_offload_test       Stable
```

Three groups, three naming conventions, and the Binance one is called
`clickhouse_bronze_offload_test`. Mapping, read off each group's `CLIENT-ID`
(`rpk group describe <group>`) because the names do not tell you:

| Consumer group | ClickHouse table | Topic |
|---|---|---|
| `clickhouse_bronze_offload_test` | `k2.binance_trades_queue` | `market.crypto.trades.binance.raw` |
| `clickhouse_bronze_kraken` | `k2.kraken_trades_queue` | `market.crypto.trades.kraken.raw` |
| `clickhouse_bronze_coinbase_consumer` | `k2.trades_coinbase_queue` | `market.crypto.trades.coinbase.raw` |

The table names are inconsistent too — Coinbase's is `trades_coinbase_queue`, the other
two are `<exchange>_trades_queue`. Confirm with `SHOW TABLES FROM k2` before typing one.

**Expected state**: all three groups are on frozen v2 topics and their lag drains to 0 and
stays there. Growing lag on any of them **after `2026-08-26T18:58:29Z`** — the retirement
deploy, and the last moment anything produced to a v2 topic — would mean something is
producing to one again, which nothing should be. **There are no consumer groups on the v3 topics
yet** — the lake ingest arrives in Phase D and the hot tier in Phase E, so `rpk group list`
showing three v2 groups and nothing else is the correct picture today.

---

## Common Issues

### Redpanda Container Not Starting

**Symptoms**: `docker compose ps` shows `k2-redpanda` in `Exited` or `Restarting` state.

**Diagnosis**:
```bash
docker compose logs redpanda --tail=50
```

**Common causes**:
- Port 9092 or 19092 already in use: `lsof -i :9092` — stop the conflicting process
- Volume corruption: inspect the `redpanda-data` named volume (`docker volume inspect k2-market-data-platform_redpanda-data`)
- Insufficient disk: `df -h` — Redpanda needs at least 5GB free

**Resolution**:
```bash
# Restart cleanly
docker compose up -d redpanda
```

*Not yet run — verify at the Phase C burn-in.* The failure has not been induced against
the current stack.

---

### Topics Missing After Restart

**Symptoms**: `rpk topic list` shows no topics; capture containers log produce errors and
`k2_capture_produce_errors_total` climbs.

**Cause**: Redpanda's data volume was wiped, so the topics are gone. `redpanda-init` is a
`restart: "no"` one-shot: `docker compose up` starts it again and it is idempotent, but it
does not re-run on its own while the stack is already up.

**Resolution**:
```bash
# Force redpanda-init to re-run — it is idempotent by design
docker compose up --force-recreate redpanda-init
```

It re-creates all 15 topics and re-registers the nine v3 Avro subjects, and exits 0 on a
cluster that already has them. It also re-creates the six frozen v2 topics, on purpose:
the ClickHouse Kafka-engine tables error on a missing topic.

*Not yet run against the current stack — verify at the Phase C burn-in.*

---

### Consumer Group Reset

Use when an offset is corrupted or you need to replay data from a specific point.

```bash
# Reset consumer group to beginning (replay all messages)
docker exec k2-redpanda rpk group seek clickhouse_bronze_offload_test \
  --to start --topic market.crypto.trades.binance.raw

# Reset to end (skip all backlog)
docker exec k2-redpanda rpk group seek clickhouse_bronze_offload_test \
  --to end --topic market.crypto.trades.binance.raw
```

*Not yet run — a seek is a write and this pass was read-only.* The group name above is the
real one (see the mapping table); the runbook previously named
`clickhouse_bronze_binance_consumer`, which does not exist on this cluster.

> **Warning**: Resetting to start re-inserts every retained message into ClickHouse, and
> the bronze tables are plain `MergeTree` — **they do not deduplicate**. The `ORDER BY`
> key is a sort key, not a uniqueness constraint. Expect duplicate rows in bronze, which
> propagate to silver and inflate gold candle counts. Since 2026-08-26 there is a second
> reason not to: the v2 tier is frozen, so a replay would be the only thing writing to it,
> and Phase E drops the database anyway.

---

### Disk Full

**Symptoms**: producers fail with "disk full" or producer error codes; on the capture side
this surfaces as `k2_capture_produce_errors_total` and `CaptureProduceStalled`.

**Diagnosis**:
```bash
docker exec k2-redpanda df -h /var/lib/redpanda/data
```

**Resolution**:
1. Check retention settings — the v3 `raw.*` topics cap at 512 MiB per partition
   (`retention.bytes`) as well as 48 h, so they are bounded by design. Confirm with
   `rpk topic describe market.crypto.v3.raw.binance`.
2. The six frozen v2 topics are the first thing to reclaim: they have no producer and are
   scheduled for deletion at Phase E. 160 partitions of retained trade JSON.
3. If persistent, expand the Docker volume or add disk.

*Steps 1 is read-only and verified; 2 and 3 are not yet run.*

---

### ClickHouse Consumer Not Consuming

**Symptoms**: consumer lag grows indefinitely on a v2 group; no new rows in bronze tables.

> Since 2026-08-26 this is expected, not a fault: nothing produces to the v2 topics. Lag
> should sit at 0 because there is nothing to consume. Investigate only if lag is
> *growing*, which would mean an unexpected producer.

**Diagnosis**:
```bash
docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "SELECT * FROM system.kafka_consumers WHERE database = 'k2' FORMAT Vertical"

docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "SELECT * FROM system.errors WHERE name LIKE '%Kafka%' ORDER BY last_error_time DESC LIMIT 10"
```

*Not yet run — this pass had no ClickHouse credential. Verify at the Phase C burn-in.*
The equivalent information without ClickHouse auth is `rpk group describe <group>`, whose
`CLIENT-ID` column names the attached ClickHouse table; that is verified above.

**Common resolution**:
```bash
docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "DETACH TABLE k2.binance_trades_queue; ATTACH TABLE k2.binance_trades_queue"
```

*Not yet run.* Note the table name is `k2.binance_trades_queue` for Binance but
`k2.trades_coinbase_queue` for Coinbase — check `SHOW TABLES FROM k2` first.

---

## Partition Rebalancing

Redpanda handles rebalancing automatically. In a single-broker setup, all partitions are
owned by the single broker — no manual rebalancing is needed.

If adding a second broker (future scale-out):
```bash
docker exec k2-redpanda rpk cluster partitions balance
```

*Not yet run — there is only one broker.*

---

## Measured MTTR

Broker restart, measured against the **v3 capture tier** by
`scripts/chaos/redpanda-stop.sh --exchange kraken` and
`scripts/chaos/capture-queue-full.sh --exchange kraken` on 2026-08-26.
The v2 feed handlers lost the broker at the same instant and are **not** measured here —
they have no equivalent counter.

**Every row cites the file the number is actually in.** The results TSV holds five
columns and no more — `ts`, `script`, `expected_alert`, `t_fire_s`, `t_recover_s` — so
the fault durations and the loss counts are not in it and must not be cited to it.

| # | Failure | Measured | Source |
|---|---------|----------|--------|
| 1 | Broker stopped (`docker stop`, 45 s), then started | producers past their mid-outage level **14 s** after the broker returned, with **no producer restart** | `t_recover_s` for `redpanda-stop.sh`, [`results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv) |
| 2 | Broker paused (`docker pause`, 388 s), then unpaused | producing again **0 s** after unpause — the first scrape already showed recovery | `t_recover_s` for `capture-queue-full.sh`, same file |
| 3 | `rpk cluster health` after both | clean, on a single-node Raft cluster frozen for over six minutes | [`scripts/chaos/README.md`](../../scripts/chaos/README.md) — *"The broker survives a six-minute pause"* |
| 4 | Records lost during the outage | **7,821** (45 s stopped) and **231,744** (388 s paused), kraken alone. Public feeds do not replay; the windows are permanently absent | [`failure-modes.md`](../architecture/failure-modes.md) — the *Broker down* row for 7,821, the *Producer queue full* row for 231,744. **Not in the TSV** |
| 5 | Time for `CaptureProduceErrors` to fire | **256 s** from the fault (`for: 5m` on a `[10m]` `increase`) | `t_fire_s` for `capture-queue-full.sh`, [`results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv) |
| 6 | Consumer-group recovery, ClickHouse and the v2 handlers | not yet verified — no script measures the consumer side | — |

**Two things this establishes.** The broker comes back clean from both a stop and a
multi-minute pause without manual intervention, and **nothing downstream needs
restarting** — the "restart the producers after the broker returns" instinct is wrong and
costs a second data-loss window. What it does *not* establish is the consumer side; row 6
stays open.

**A caveat on row 4.** Those loss figures predate the `message.timeout.ms` 30 s → 5 min
fix in `sink.rs`
([ADR-019 Outcome](../adr/ADR-019-rust-capture-tier.md#measured-correction-2026-08-26--the-32-mib-buffer-was-unreachable)):
a 45 s outage should have lost nothing at all. Re-run to score the fix.

**Last verified:** 2026-08-26 (`make chaos`).

---

## Health Check

```bash
# Cluster health
docker exec k2-redpanda rpk cluster health

# Is data flowing? Per-partition high watermarks on a live v3 topic
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance -p

# Or watch three records arrive, by key
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance -n 3 -f '%p %o %k\n'

# Consumer groups summary
docker exec k2-redpanda rpk group list
```

> The previous version of this runbook used `rpk topic offset-list`. **There is no such
> subcommand** — `rpk topic offset-list` returns `Error: unknown command "offset-list" for
> "rpk topic"` on `redpandadata/redpanda:v25.3.4`. `rpk topic describe -p` is the command
> that does the job.

`rpk cluster health`, 2026-08-26:

```
CLUSTER HEALTH OVERVIEW
=======================
Healthy:                          true
Unhealthy reasons:                []
Controller ID:                    0
All nodes:                        [0]
Nodes down:                       []
Nodes in recovery mode:           []
Leaderless partitions (0):        []
Under-replicated partitions (0):  []
```

`rpk topic describe market.crypto.v3.trades.binance -p`, re-run in full at
**2026-08-26T19:19Z**, nothing elided:

```
PARTITION  LEADER  EPOCH  REPLICAS  LOG-START-OFFSET  HIGH-WATERMARK
0          0       6      [0]       0                 305044
1          0       6      [0]       0                 984123
2          0       6      [0]       0                 0
3          0       6      [0]       0                 414142
4          0       6      [0]       0                 130998
5          0       6      [0]       0                 0
6          0       6      [0]       0                 0
7          0       6      [0]       0                 2101978
8          0       6      [0]       0                 0
9          0       6      [0]       0                 64810
10         0       6      [0]       0                 261785
11         0       6      [0]       0                 7995
```

**Empty partitions are normal, not a fault.** Records are keyed by symbol, so
12 Binance instruments hash across 12 partitions with collisions; 4 partitions being empty
is what that looks like. What matters is that the non-empty watermarks advance between two
runs of the command.

### The v2 key bug, visible from here

The same command on `market.crypto.trades.kraken.raw`, same run
(**2026-08-26T19:19Z**), returns exactly one non-empty partition of 20 — all 20 rows,
nothing elided:

```
PARTITION  LEADER  EPOCH  REPLICAS  LOG-START-OFFSET  HIGH-WATERMARK
0          0       11     [0]       0                 0
1          0       11     [0]       0                 0
2          0       11     [0]       0                 0
3          0       11     [0]       0                 0
4          0       11     [0]       0                 0
5          0       11     [0]       0                 0
6          0       11     [0]       0                 0
7          0       11     [0]       0                 0
8          0       11     [0]       0                 0
9          0       11     [0]       0                 0
10         0       11     [0]       61743             168572
11         0       11     [0]       0                 0
12         0       11     [0]       0                 0
13         0       11     [0]       0                 0
14         0       11     [0]       0                 0
15         0       11     [0]       0                 0
16         0       11     [0]       0                 0
17         0       11     [0]       0                 0
18         0       11     [0]       0                 0
19         0       11     [0]       0                 0
```

The high watermark on partition 10 is **frozen at 168,572** and stays there: this topic
has had no producer since `2026-08-26T18:58:29Z`. The log-start offset of 61,743 is
retention having already expired the head of the log — `retention.ms=604800000` (7 d,
`rpk topic describe market.crypto.trades.kraken.raw -c`), which is also the deadline on
re-reading any of this: the whole topic empties around **2026-09-02**.

Kraken and Coinbase raw records were keyed by the *exchange name* rather than the symbol
(`KafkaProducerService.produceRawJson`), so both topics hashed every record onto one
partition of 20. Binance keyed by symbol and spreads across its 40. This is the defect the
v3 contract fixes by keying every record on the instrument rather than the venue, and it is
worth knowing about before reading any v2 partition count as capacity.

---

## Related

- [ADR-001: Replace Kafka with Redpanda](../adr/ADR-001-replace-kafka-with-redpanda.md)
- [ADR-019: Rust capture tier](../adr/ADR-019-rust-capture-tier.md) — why the v2 topics are frozen
- [Capture container crash recovery](./failure-recovery.md#3-capture-container-crash)
- [capture-produce-stalled.md](./capture-produce-stalled.md) — capture cannot reach the broker
- [Adding a new exchange](../operations/adding-new-exchanges.md)
- [`docker/redpanda/init.sh`](../../docker/redpanda/init.sh) — the authority on topics, partitions and retention
