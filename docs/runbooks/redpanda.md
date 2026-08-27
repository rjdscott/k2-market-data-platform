# Runbook: Redpanda Operations

**Severity**: High (message broker, impacts all 3 exchanges if down)
**Last Updated**: 2026-08-27
**Replaces**: `kafka-runbook.md` (Kafka replaced by Redpanda in v2, ADR-001)

Every `rpk` command below was run against the live single-broker cluster on
2026-08-26 or 2026-08-27 and the output pasted is what it printed, dated. Commands that
could not be run are marked inline; nothing here is paraphrased from memory.

> **The six v2 topics are gone.** `market.crypto.trades.<ex>` and
> `market.crypto.trades.<ex>.raw` were deleted on 2026-08-27 at the Phase E cutover,
> together with the ClickHouse `k2` database that consumed them
> ([`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md)). Outputs below dated
> 2026-08-26 still show them; they are kept as the record of that day.

---

## Overview

Redpanda is the Kafka-compatible message broker. It runs as a single-broker cluster
(`k2-redpanda`) carrying nine v3 topics, raw frames, trades and L2 book per exchange.

| Topic | Partitions | Payload | Consumers |
|-------|-----------:|---------|-----------|
| `market.crypto.v3.raw.<ex>` | 12 | Avro `RawMessage`, every frame verbatim | lake ingest, by offset range (no consumer group) |
| `market.crypto.v3.trades.<ex>` | 12 | Avro `Trade` | lake ingest; ClickHouse `gold.q_trades`, group `k2-gold-trades` |
| `market.crypto.v3.book.<ex>` | 12 | Avro `BookSnapshotL2`, top-20 at 1 Hz | lake ingest; ClickHouse `gold.q_book`, group `k2-gold-book` |

`<ex>` ∈ {`binance`, `kraken`, `coinbase`}, 9 topics, 108 partitions, produced by the
three `k2-capture-*` containers. Keys are plain UTF-8: `trades.*` and `book.*` key on the
**canonical** symbol (`BTC/USDT`), `raw.*` on the **wire** symbol the venue used
(`BTCUSDT`), a raw frame carries the venue's own name for the instrument. Frames that
belong to no instrument (heartbeats, subscribe acks) have no key at all. Retention
is 48 h + 512 MiB/partition on `raw.*` and 7 d on `trades.*`/`book.*`, set by
[`docker/redpanda/init.sh`](../../docker/redpanda/init.sh), which is also the authority
on the topic table above.

> **History.** `market.crypto.trades.<ex>` and `market.crypto.trades.<ex>.raw` were the
> six v2 topics, 40 partitions each for Binance, 20 for Kraken and Coinbase, 160 in
> total. Their only producers were the Kotlin feed handlers; the last record landed at
> `2026-08-26T18:58:29Z`, the retirement deploy ([ADR-019](../adr/ADR-019-rust-capture-tier.md)),
> read at the time as `max(ingestion_timestamp)` on `k2.bronze_trades_kraken`. They were
> deleted with the `k2` database on 2026-08-27; `redpanda-init` no longer creates them.

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

# Delete a topic (use with caution: drops all data)
docker exec k2-redpanda rpk topic delete my-topic

# Consume: records are Confluent-framed Avro, so print the key, not the value
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance -n 3 -f '%p %o %k\n'
```

`rpk topic list`, 2026-08-27, after the cutover:

```
NAME                              PARTITIONS  REPLICAS
_schemas                          1           1
market.crypto.v3.book.binance     12          1
market.crypto.v3.book.coinbase    12          1
market.crypto.v3.book.kraken      12          1
market.crypto.v3.raw.binance      12          1
market.crypto.v3.raw.coinbase     12          1
market.crypto.v3.raw.kraken       12          1
market.crypto.v3.trades.binance   12          1
market.crypto.v3.trades.coinbase  12          1
market.crypto.v3.trades.kraken    12          1
```

Until 2026-08-27 the same command also listed the six v2 topics (`market.crypto.trades.<ex>`
and `.raw`, 40/40/20/20/20/20 partitions).

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

To decode a value, use Redpanda Console, it resolves the schema id against the built-in
registry. `rpk` will not.

---

## Consumer Lag Inspection

```bash
# List all consumer groups
docker exec k2-redpanda rpk group list

# Describe a group (lag per partition)
docker exec k2-redpanda rpk group describe k2-gold-trades
```

`rpk group list`, 2026-08-27:

```
BROKER  GROUP           STATE
0       k2-gold-book    Stable
0       k2-gold-trades  Stable
```

| Consumer group | ClickHouse table | Topics |
|---|---|---|
| `k2-gold-trades` | `gold.q_trades` → `gold.trades` | `market.crypto.v3.trades.{binance,kraken,coinbase}` |
| `k2-gold-book` | `gold.q_book` → `gold.book_top20` | `market.crypto.v3.book.{binance,kraken,coinbase}` |

Both are declared in [`docker/clickhouse/ddl/20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql).
The lake ingest reads every v3 topic by offset range and keeps its position in the Iceberg
snapshot summary, so it never appears here ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)).

**Expected state**: both groups `Stable` with lag near zero while capture is producing.
Lag that grows while capture is delivering is `ClickHouseGoldFeedStale`'s territory
([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md#alerts)); a group in
state `Empty` means the Kafka-engine table is detached or ClickHouse is down. Until
2026-08-27 this list showed the three v2 groups (`clickhouse_bronze_offload_test`,
`clickhouse_bronze_kraken`, `clickhouse_bronze_coinbase_consumer`) on the `.raw` topics;
they went with the `k2` database.

---

## Common Issues

### Redpanda Container Not Starting

**Symptoms**: `docker compose ps` shows `k2-redpanda` in `Exited` or `Restarting` state.

**Diagnosis**:
```bash
docker compose logs redpanda --tail=50
```

**Common causes**:
- Port 9092 or 19092 already in use: `lsof -i :9092`, stop the conflicting process
- Volume corruption: inspect the `redpanda-data` named volume (`docker volume inspect k2-market-data-platform_redpanda-data`)
- Insufficient disk: `df -h`, Redpanda needs at least 5GB free

**Resolution**:
```bash
# Restart cleanly
docker compose up -d redpanda
```

*Not yet run, verify at the Phase C burn-in.* The failure has not been induced against
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
# Force redpanda-init to re-run: it is idempotent by design
docker compose up --force-recreate redpanda-init
```

It re-creates the nine v3 topics and re-registers the nine v3 Avro subjects, and exits 0
on a cluster that already has them. The `gold.q_*` Kafka-engine tables error on a missing
topic, so run it before restarting ClickHouse.

*Not yet run against the current stack, verify at the Phase C burn-in.*

---

### Consumer Group Reset

Use when an offset is corrupted or you need to replay data from a specific point.

```bash
# Detach the consumer first: a seek on a group with live members is refused
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q "DETACH TABLE gold.q_trades"

# Reset consumer group to beginning (replay all retained messages)
docker exec k2-redpanda rpk group seek k2-gold-trades \
  --to start --topic market.crypto.v3.trades.binance

# Reset to end (skip all backlog)
docker exec k2-redpanda rpk group seek k2-gold-trades \
  --to end --topic market.crypto.v3.trades.binance

docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q "ATTACH TABLE gold.q_trades"
```

*Not yet run, a seek is a write and this pass was read-only.*

> A replay into `gold.trades` is safe: it is `ReplacingMergeTree` keyed on the logical
> trade, so re-delivered rows collapse under `FINAL` and the earliest delivery wins. Counts
> *without* `FINAL` will double until the merge runs. The v2 warning that used to live here
>, bronze was plain `MergeTree` and a replay duplicated every row, went with the `k2`
> database. For anything older than the topic's 7-day retention, reload from the lake
> instead: [clickhouse-rebuild-from-lake.md](./clickhouse-rebuild-from-lake.md).

---

### Disk Full

**Symptoms**: producers fail with "disk full" or producer error codes; on the capture side
this surfaces as `k2_capture_produce_errors_total` and `CaptureProduceStalled`.

**Diagnosis**:
```bash
docker exec k2-redpanda df -h /var/lib/redpanda/data
```

**Resolution**:
1. Check retention settings, the v3 `raw.*` topics cap at 512 MiB per partition
   (`retention.bytes`) as well as 48 h, so they are bounded by design. Confirm with
   `rpk topic describe market.crypto.v3.raw.binance`.
2. `trades.*` / `book.*` are 7 d, time-only; they are rebuildable from the lake, so a
   shorter `retention.ms` on them is the cheap lever if 1 is not enough.
3. If persistent, expand the Docker volume or add disk.

*Step 1 is read-only and verified; 2 and 3 are not yet run.* (The six v2 topics, 160
partitions of retained trade JSON, were the first thing reclaimed, deleted 2026-08-27.)

---

### ClickHouse Consumer Not Consuming

**Symptoms**: lag grows on `k2-gold-trades` / `k2-gold-book` while capture is delivering;
`gold.trades` stops advancing. `ClickHouseGoldFeedStale` fires after 10 minutes of that.

**Diagnosis**:
```bash
docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "SELECT table, num_messages_read, num_commits, last_poll_time, exceptions.text
   FROM system.kafka_consumers WHERE database = 'gold' FORMAT Vertical"

# A record the decoder rejected does not stall the feed: it lands here, bytes included
docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "SELECT seen_at, topic, partition, offset, error FROM gold.feed_errors ORDER BY seen_at DESC LIMIT 10"

docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --query \
  "SELECT * FROM system.errors WHERE name LIKE '%Kafka%' ORDER BY last_error_time DESC LIMIT 10"
```

The first query was run on 2026-08-27 (two consumers per table, `exceptions.text: []`).
The equivalent without ClickHouse auth is `rpk group describe <group>`.

**Common resolution**:
```bash
docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --multiquery --query \
  "DETACH TABLE gold.q_trades; ATTACH TABLE gold.q_trades"
```

*Not yet run.* `gold.q_book` is the other feed table; both are in
[`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql).

---

## Partition Rebalancing

Redpanda handles rebalancing automatically. In a single-broker setup, all partitions are
owned by the single broker, no manual rebalancing is needed.

If adding a second broker (future scale-out):
```bash
docker exec k2-redpanda rpk cluster partitions balance
```

*Not yet run, there is only one broker.*

---

## Measured MTTR

Broker restart, measured against the **v3 capture tier** by
`scripts/chaos/redpanda-stop.sh --exchange kraken` and
`scripts/chaos/capture-queue-full.sh --exchange kraken` on 2026-08-26.
The v2 feed handlers lost the broker at the same instant and are **not** measured here , 
they have no equivalent counter.

**Every row cites the file the number is actually in.** The results TSV holds five
columns and no more, `ts`, `script`, `expected_alert`, `t_fire_s`, `t_recover_s`, so
the fault durations and the loss counts are not in it and must not be cited to it.

| # | Failure | Measured | Source |
|---|---------|----------|--------|
| 1 | Broker stopped (`docker stop`, 45 s), then started | producers past their mid-outage level **14 s** after the broker returned, with **no producer restart** | `t_recover_s` for `redpanda-stop.sh`, [`results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv) |
| 2 | Broker paused (`docker pause`, 388 s), then unpaused | producing again **0 s** after unpause, the first scrape already showed recovery | `t_recover_s` for `capture-queue-full.sh`, same file |
| 3 | `rpk cluster health` after both | clean, on a single-node Raft cluster frozen for over six minutes | [`scripts/chaos/README.md`](../../scripts/chaos/README.md), *"The broker survives a six-minute pause"* |
| 4 | Records lost during the outage | **7,821** (45 s stopped) and **231,744** (388 s paused), kraken alone. Public feeds do not replay; the windows are permanently absent | [`16-failure-modes.md`](../architecture/16-failure-modes.md), the *Broker down* row for 7,821, the *Producer queue full* row for 231,744. **Not in the TSV** |
| 5 | Time for `CaptureProduceErrors` to fire | **256 s** from the fault (`for: 5m` on a `[10m]` `increase`) | `t_fire_s` for `capture-queue-full.sh`, [`results/2026-08-26.tsv`](../../scripts/chaos/results/2026-08-26.tsv) |
| 6 | Consumer-group recovery, the ClickHouse `k2-gold-*` groups | not yet verified, no script measures the consumer side |, |

**Two things this establishes.** The broker comes back clean from both a stop and a
multi-minute pause without manual intervention, and **nothing downstream needs
restarting**, the "restart the producers after the broker returns" instinct is wrong and
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
> subcommand**, `rpk topic offset-list` returns `Error: unknown command "offset-list" for
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

### The v2 key bug, as it was visible from here (topic deleted 2026-08-27)

The same command on `market.crypto.trades.kraken.raw`, same run
(**2026-08-26T19:19Z**), returned exactly one non-empty partition of 20, all 20 rows,
nothing elided. The topic no longer exists; this is the record of what it showed:

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

The high watermark on partition 10 was **frozen at 168,572**: the topic had had no producer
since `2026-08-26T18:58:29Z`. The log-start offset of 61,743 was retention having already
expired the head of the log (`retention.ms=604800000`, 7 d). The topic was deleted the
next day with the rest of the v2 tier.

Kraken and Coinbase raw records were keyed by the *exchange name* rather than the symbol
(`KafkaProducerService.produceRawJson`), so both topics hashed every record onto one
partition of 20. Binance keyed by symbol and spreads across its 40. This is the defect the
v3 contract fixes by keying every record on the instrument rather than the venue, and it is
worth knowing about before reading any v2 partition count as capacity.

---

## Related

- [ADR-001: Replace Kafka with Redpanda](../adr/ADR-001-replace-kafka-with-redpanda.md)
- [ADR-019: Rust capture tier](../adr/ADR-019-rust-capture-tier.md), why the v2 topics lost their producer
- [ADR-026: gold served from ClickHouse](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md), the cutover that deleted them
- [Capture container crash recovery](./failure-recovery.md#3-capture-container-crash)
- [capture-produce-stalled.md](./capture-produce-stalled.md), capture cannot reach the broker
- [Adding a new exchange](../operations/adding-new-exchanges.md)
- [`docker/redpanda/init.sh`](../../docker/redpanda/init.sh), the authority on topics, partitions and retention
