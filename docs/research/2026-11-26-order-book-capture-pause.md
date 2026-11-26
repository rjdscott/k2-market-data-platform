# Pausing order-book capture: effects, plan and rollout

**Date:** 2026-11-26
**For:** [ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md), Proposed
**Question:** if the three captures stop subscribing to order-book (L2) channels, what stops,
what breaks, what lies, and how is it switched off and on again safely?

Everything here was read from the code at `791c77a` and from the live stack read-only (Trino
`SELECT`s as user `reviewer`, Prometheus and `docker exec` reads), 2026-11-26 01:40 to 01:56 UTC.
Commands that change the stack are written down for the rollout and are marked **not run**.
Terms are in the [ADR glossary](../adr/glossary.md). Market-derived quantities are given as bytes
or ratios only.

---

## §1 Measurements

**1.1 Bytes the captures received, last 24 h, per venue and stream.** The counter
`k2_capture_bytes_total` adds each frame's payload length as it leaves the socket (`main.rs`).

```bash
curl -s -G 'http://127.0.0.1:9090/api/v1/query' \
  --data-urlencode 'query=sum by (exchange, stream) (increase(k2_capture_bytes_total[24h]))'
```

| Venue | Book stream share of the venue's bytes | Trade stream share | Other |
|---|---|---|---|
| Binance | `depth20` 82.83% | `trade` 17.17% | |
| Coinbase | `l2_data` 97.31% | `market_trades` 2.59% | `heartbeats` 0.10% |
| Kraken | `book` 99.62% | `trade` 0.35% | `instrument`, `heartbeat` 0.01% each |
| **All three** | **95.05%** | **4.90%** | |

**1.2 Lake tables, current snapshot, bytes of data files.**

```bash
# the figures below came from one UNION ALL of these SELECTs; the loop form is the same query
for t in bronze.binance_depth20 bronze.kraken_book bronze.coinbase_level2 silver.book_binance \
         silver.book_kraken silver.book_coinbase gold.book_top20 gold.bbo_1s gold.book_state \
         bronze.binance_trade bronze.kraken_trade bronze.coinbase_market_trades silver.trades_binance \
         silver.trades_kraken silver.trades_coinbase gold.trades gold.ohlcv_1m gold.ohlcv_5m \
         gold.ohlcv_1h gold.ohlcv_1d gold.bars raw.messages; do
  docker exec k2-trino trino --user reviewer --output-format TSV --execute \
    "SELECT '$t', sum(file_size_in_bytes) FROM lake.${t%%.*}.\"${t#*.}\$files\""
done
```

| Book-derived | GiB | Trade-derived | GiB |
|---|---|---|---|
| `bronze.kraken_book` | 3.96 | `gold.trades` | 0.42 |
| `silver.book_kraken` | 3.57 | `silver.trades_binance` | 0.39 |
| `bronze.coinbase_level2` | 2.17 | `bronze.binance_trade` | 0.30 |
| `silver.book_coinbase` | 2.03 | `silver.trades_coinbase` | 0.11 |
| `gold.book_top20` | 0.76 | `bronze.coinbase_market_trades` | 0.10 |
| `bronze.binance_depth20` | 0.71 | `silver.trades_kraken`, `bronze.kraken_trade` | 0.05 |
| `silver.book_binance` | 0.66 | candles and bars, 5 tables | 0.01 |
| `gold.bbo_1s` | 0.19 | | |
| `gold.book_state` | 0.01 | | |
| **Total** | **14.06** | **Total** | **1.36** |

Ratio 10.3 to 1. `raw.messages` held 13.22 GiB in its current snapshot.

**1.3 Raw archive bytes by topic, UTC day 2026-11-25.** `raw.messages` is partitioned by day and
topic, so its partitions metadata table gives this without reading a row:

```bash
docker exec k2-trino trino --user reviewer --output-format TSV_HEADER --execute \
  "SELECT partition.kafka_ts_day AS day, partition.topic AS topic, sum(total_size) AS bytes
   FROM lake.raw.\"messages\$partitions\" WHERE partition.kafka_ts_day >= DATE '2026-11-24'
   GROUP BY 1, 2 ORDER BY 1, 2"
```

Of 7.37 GB landed that day: `raw.*` 90.9%, `book.*` 5.4%, `trades.*` 3.7%. The raw topics carry
every frame, book frames included (1.1 says which share of those are book), so the book's real
share of the archive is most of the 90.9% plus the 5.4%. The day includes the 343 s outage at
22:33 UTC.

**1.4 Redpanda data directory by topic.**

```bash
docker exec k2-redpanda sh -c 'du -sb /var/lib/redpanda/data/kafka/market.crypto.v3.*'
docker exec k2-redpanda sh -c 'du -sb /var/lib/redpanda/data/kafka/market.crypto.v3.raw.kraken/* | sort -n -r | head -3'
docker exec k2-redpanda rpk topic describe market.crypto.v3.raw.kraken -c | grep retention
```

`raw.*` 27.5 GB (82.4%), `book.*` 3.6 GB (10.8%), `trades.*` 2.27 GB (6.8%). The three busiest
`raw.kraken` partitions held 2.24, 2.24 and 2.22 GB against `retention.bytes` 2147483648 per
partition and `retention.ms` 172800000 (48 h): on those partitions the byte cap, not the 48 h,
decides how long an unread record survives today. The book topics are 7 days and no byte cap.

**1.5 Object store, hot tier, ingest, capture containers.**

```bash
docker exec k2-minio du -s -B1 /data                       # 46541606912 = 43.3 GiB
docker exec k2-clickhouse clickhouse-client --query "SELECT table, sum(bytes_on_disk)
  FROM system.parts WHERE active AND database='gold' GROUP BY table ORDER BY 2 DESC FORMAT TSV"
# book_top20 1150428496 (1.07 GiB), trades 949925833 (905.9 MiB)
curl -s -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=
  increase(airflow_dagrun_duration_success_sum{dag_id="lake_ingest",run_type="scheduled"}[24h])
  / increase(airflow_dagrun_duration_success_count{dag_id="lake_ingest",run_type="scheduled"}[24h])'
# 192.4 (seconds, mean scheduled run)
curl -s -G 'http://127.0.0.1:9090/api/v1/query' --data-urlencode \
  'query=max by (name) (max_over_time(container_memory_working_set_bytes{name=~"k2-capture-.*"}[24h]))'
# coinbase 48.0 MiB, kraken 27.5 MiB, binance 17.2 MiB, against limits 512M / 256M / 256M
```

**1.6 Empty commits.** Stage 2b appends to every bronze table of a venue each run, empty or not,
so the venue's tables stay at the same raw snapshot. `bronze.kraken_instrument`, whose channel
is quiet for hours, shows it:

```bash
docker exec k2-trino trino --user reviewer --output-format TSV_HEADER --execute \
  "SELECT committed_at, operation, element_at(summary, 'added-records') AS added,
          element_at(summary, 'k2.src-snapshot-id') AS src
   FROM lake.bronze.\"kraken_instrument\$snapshots\" ORDER BY committed_at DESC LIMIT 4"
# four appends at 5-minute intervals, added empty, src set: committed with no rows
```

The book tables have never had one in their retained history (count of appends with no
`added-records`: 0 on `silver.book_binance`, `silver.book_kraken`, `silver.book_coinbase`,
`gold.book_top20`, `bronze.kraken_book`, history since 2026-11-24 05:26 UTC), so stage 2e's
empty path has not run live.

---

## §2 What the capture does today

- **Subscriptions** (`services/capture-rust/src/exchanges/`): Binance names its streams in the
  URL, `stream_query()` emitting `<sym>@trade` and `<sym>@depth20@100ms` per symbol; Kraken sends
  `instrument`, then one `book` subscribe per depth (25 by default), then `trade`
  (`subscribe_messages()`); Coinbase sends `level2`, `market_trades`, `heartbeats`.
- **Topics** (`record.rs` `topic_kind`, `sink.rs`): every frame first as a `Raw` record to
  `raw.<venue>` (adapter contract rule 1 in `exchanges/mod.rs`: every frame, parseable or not);
  `Trade` records to `trades.<venue>`; `Book` snapshots from the 1 Hz sampler to `book.<venue>`.
- **Watching** (`main.rs`): `CONTINUOUS` lists the streams that must keep delivering and their
  silence bound: `book`, `depth20`, `l2_data`, `heartbeat`, `heartbeats` 60 s; `trade`,
  `market_trades` 300 s. `run` seeds `k2_capture_last_message_ts_seconds` for each at start; the
  session watchdog reconnects when one passes its bound; `healthcheck` exits 1 when any series
  is past its bound.
- **Seeding** (`metrics.rs` `zero`): message, byte and unknown-frame counters per stream;
  `k2_capture_book_depth` at 0 per symbol; Kraken's `k2_capture_checksum_failures_total` and
  `k2_capture_book_updates_ignored_total` per symbol.
- **Shutdown** (`main.rs` `run`): SIGTERM closes the socket, then `sink.flush(5 s)`.

---

## §3 Effects inventory

### 3.1 Alerts

Every rule in `docker/prometheus/rules/`. "Absent" means the series is not created when the
venue's book is off (§6, capture changes), so the rule has nothing to evaluate.

| Alert | Expression | With no book traffic, nothing else changed | Treatment |
|---|---|---|---|
| `CaptureDown` | `up{job=~"capture-.*"} == 0`, 2m | unaffected | leave |
| `CaptureFeedStale` | `(time() - k2_capture_last_message_ts_seconds{stream!~"trade\|market_trades"} > 60) or (... {stream=~"trade\|market_trades"} > 300)`, 2m | fires for `book`, `depth20`, `l2_data` about 3 min after start (gauge seeded at start, never stamped) | silence by condition at the source: book streams neither seeded nor stamped |
| `CaptureSequenceGaps` | `increase(k2_capture_gaps_total[10m]) > 0`, 5m | Binance: its only sequence is `lastUpdateId` on `depth20`, so it can no longer fire; Coinbase: connection-wide, still live on trades and heartbeats; Kraken: never sequenced | leave |
| `CaptureChecksumFailure` | `increase(k2_capture_checksum_failures_total[10m]) > 0`, 5m | cannot fire; the seeded 0 reads as "verified" | absent for Kraken when off |
| `CaptureProduceErrors` | `increase(k2_capture_produce_errors_total[10m]) > 0`, 5m | unaffected | leave |
| `CaptureProduceStalled` | `sum by (exchange) (rate(...delivered_total[1m])) == 0 and sum by (exchange) (rate(...produced_total[1m])) > 0`, 30s | unaffected (rates fall, stay above 0) | leave |
| `CaptureResyncStorm` | `increase(k2_capture_resyncs_total[15m]) > 3`, 5m | cannot fire (resyncs are book-only) | leave |
| `CaptureIngressLatencyHigh` | p99 of `k2_capture_exchange_to_recv_seconds` > 2, 10m | unaffected (trades only already) | leave |
| `CaptureBookDepthDegraded` | `max_over_time(k2_capture_book_depth[10m]) < 20`, 10m | fires for all 34 symbols (gauge seeded at 0) | absent when off |
| `CapturePrecisionLoss` | `increase(k2_capture_precision_loss_total[1h]) > 0`, 5m | still live on trades | leave |
| `LakeCommitAgeHigh` | `time() - k2_lake_last_commit_ts_seconds{table=~"bronze\\..*", table!="bronze.kraken_instrument"} > 1800`, 10m | silent on the three bronze book tables, which take an empty commit every run (§1.6); it measures commits, not data | leave; the data catalog says so |
| `LakeIngestFailed`, `LakeIngestLagHigh`, `LakeIngestBatchAuditFailed`, `LakeOffsetGap`, `LakeIngestBacklogSustained`, `LakeUnresolvableSchemaId`, `LakeCompactionStale`, `LakeDiskUsageHigh/Critical`, `LakeExporter*`, `LakeScrapeErrors` | read `raw.messages`, the ingest summary, disk or the exporter | unaffected | leave |
| `LakeAuditFailed` | `k2_lake_audit_failures_total > 0`, 5m | book audits keep passing on kept rows (§3.3) | leave; first night checked |
| `ClickHouseGoldFeedStale` | `sum(rate(ClickHouseProfileEvents_KafkaMessagesRead[10m])) == 0 and on() min(time() - k2_capture_last_message_ts_seconds{stream="trade"}) < 300`, 5m | the sum includes the trades feed, so it stays above 0; an idle book feed is invisible to it, which is correct while paused | leave |
| `ClickHouseKafkaMessagesFailed` and the other ClickHouse rules | | unaffected | leave |
| `AirflowTaskFailed` | `increase(airflow_ti_finish{state="failed",dag_id!="dbt_marts"}[15m]) > 0` | unaffected, and it is the alarm if stage 2e fails on empty input | leave |
| Other Airflow rules | | unaffected | leave |

### 3.2 The ingest (`docker/lake/ingest.py`, every 5 minutes)

- **Stage 1** reads the book topics' empty offset ranges and commits as now; the batch audit
  checks only what was planned.
- **Stage 2b** (`bronze.stage`, `decode`): the venue's raw range now holds no book frame, so the
  book table gets an empty append stamped with the raw snapshot, and the parity line still
  balances (`frames = decoded + control`). The lockstep check (`bronze.<venue>_* tables are at
  different raw snapshots`) stays satisfied because every table still commits. No change.
- **Stage 2e** (`books.stage`, `process`): the incremental read of each bronze book table is
  empty. Binance: an empty silver append, then `merge_binance` returns at `lo is None` without a
  gold commit. Kraken and Coinbase: `replay()` over an empty partition set, an empty silver
  append, an empty `gold.book_top20` append, an empty incremental read between the two gold
  snapshots, an empty `gold.bbo_1s` append, a `MERGE` into `gold.book_state` with an empty
  source. Expected log lines: `stage 2e: 0 rows -> lake.silver.book_<venue>` and
  `stage 2e: 0 rows -> lake.gold.book_top20 (<venue>)`. **Do not skip it:** each table records
  the parent snapshot it read (`k2.src-snapshot-id`), expiry removes snapshots older than 7 days,
  and a table that stopped committing would, on resume, try to read from an expired snapshot.
  Not run live (§1.6); stage 2e is the last step, so a failure there leaves stages 1 to 2d
  committed and fires `AirflowTaskFailed`.
- **Gauges** (`metrics.py`): book tables keep `k2_lake_last_commit_ts_seconds` fresh, their
  `k2_lake_rows_total` flat and `k2_lake_added_records` at 0.

### 3.3 Nightly maintenance (`docker/lake/maintenance.py`, 03:00 UTC)

Eighteen audits read book tables: per bronze book table `duplicate_identifiers`,
`bronze_unparseable`, `bronze_schema_drift` (nine); per silver book table
`duplicate_identifiers` and `silver_parity` (six); `kraken_checksum`; `duplicate_identifiers` on
`gold.book_top20` and `gold.bbo_1s`.

- The whole-table ones read the kept rows and pass as before. They keep their nightly cost over
  about 14 GiB, which the pause does not reduce (cost unmeasured; ADR open question 3).
- `kraken_checksum` passes, with a frame count that stops moving: it verifies nothing new.
- `bronze_schema_drift` samples 0.1% of the venue's last day of raw frames, filters to the
  table's stream, and passes with "no undeclared keys" when it found **no frame** of that stream.
  The same vacuous pass already happens on `bronze.kraken_instrument` most nights. Change: report
  how many frames of the stream the sample held, and say "no `<stream>` frames in the sample:
  nothing to check" when it is zero. Still a pass, now an honest one.
- **Compaction** rewrites only partitions newer than 2 days and changes files, never rows; with
  no new book files it has nothing to rewrite after two nights. **Expiry** drops snapshots older
  than 7 days, keeping 10, and deletes only files no retained snapshot references; the current
  snapshot's files stay. **Orphan removal** deletes only files no snapshot references, older than
  24 h. None of them can remove a kept book row.

### 3.4 Scripts, gates, dashboards, notebooks, marts, topics, hot tier

| Surface | Effect | Treatment |
|---|---|---|
| `scripts/health.sh` (1) container health | capture health check exits 1 on a seeded, never-stamped book stream, so the container goes unhealthy and (1) fails | fixed at the source (§6) |
| `scripts/health.sh` (2), (3) | trades and lake commit age only | none |
| `scripts/lake-verify.sh` | book parity passes on kept rows; the book topics already carry committed offsets | none |
| `make gates` (test, lake-verify, parity-ohlcv, parity-bars, check-alerts, check-docs) | none reads live book traffic | none |
| Release check, `.claude/skills/release-check/SKILL.md` § 3 | `SELECT exchange, count() FROM gold.book_top20 ...` expects three non-zero rows on a fresh clone | expect rows only for venues whose `book_capture` is true |
| Grafana "K2 Capture (v3)" (`k2-l2-capture.json`): Sequence Gaps, Checksum Failures, Resyncs, Book Depth, Book Levels Total | no data | leave the panels; the Book row's description says paused |
| `notebooks/k2lake.py` `trades(book=True)` | `ASOF LEFT JOIN` on `exchange_ts >= second + 1 s` with no lower bound: a trade in the pause gets the last quote before it, however old | quote columns NULL when the quote is older than a stated bound (`MAX_QUOTE_AGE`, chosen in the PR from the venue's healthy maximum), tested on synthetic tables |
| `k2lake.completeness()` | `quote_coverage_pct` counts those stale quotes as covered; Kraken `checksum_failed` is `count(*)` and reads 0 for a window with no book frame | coverage follows from the bound above; `checksum_failed` NULL when the window holds no book frame |
| Notebooks 02, 03, 04 | read pinned snapshots; seconds inside the pause have no book | none beyond the helpers; notebooks README says so |
| dbt marts | none reads a book table; `venue_quality_scorecard` counts audit rows (the drift audit's vacuous pass counts as a passing day audit) and `book_records_lost` reads 0, which is true | none; the drift detail makes the row honest |
| Governance view `quality_results` | unions `audit.checks`; book audit rows keep appearing as pass | none |
| Redpanda `book.*` topics | idle; 7-day retention deletes closed segments; the committed offset equals the high watermark, so stage 1's eviction check sees no loss | keep |
| Redpanda `raw.*` topics | shrink to trade, heartbeat, instrument and acknowledgement frames | keep; retention retuned later |
| ClickHouse `gold.q_book` feed | idle consumer | keep |
| ClickHouse `gold.book_top20`, `gold.bbo_1s`, view `gold.bbo_live` | kept (no TTL); `bbo_live`'s newest second is the last before the pause | data catalog says so |

---

## §4 Product status in the contracts

`contracts/schema/k2.schema.json` allows `lifecycle: draft | published | deprecated` on a product
descriptor, and every book product is `draft`. Lifecycle is maturity; it cannot carry a date or a
reason, and `venue-frames` is paused for three of its seven tables. The smallest honest change is
one optional custom property on a table contract (ODCS `customProperties`, which K2 validates
with `additionalProperties: false`, so the property must be declared):

```json
"paused": {
  "description": "The table receives no new data by decision; its rows stay readable. Removed on resume.",
  "type": "object", "additionalProperties": false, "required": ["since", "adr"],
  "properties": {
    "since": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
    "adr": {"type": "string", "pattern": "^ADR-[0-9]{3}$"}
  }
}
```

The nine tables: `bronze.binance_depth20`, `bronze.kraken_book`, `bronze.coinbase_level2`,
`silver.book_binance`, `silver.book_kraken`, `silver.book_coinbase`, `gold.book_state`,
`gold.book_top20`, `gold.bbo_1s`. Not `bronze.kraken_instrument`, which stays live.

k2gen (`platform/k2gen/k2gen/`): `validate` checks the ADR file exists and refuses a paused table
with a `latency` SLA property; `generate` adds a Status row to the product page and a Status
column to `docs/products/README.md` and `all.md` (running, paused, or "3 of 7 tables paused"),
and leaves freshness out of `dbt/models/_sources.yml` for a paused table (none is read by a mart
today). P6 inherits two rules: no SLO on a paused table, and freshness from the newest data time,
never from `k2_lake_last_commit_ts_seconds`, which empty commits keep fresh.

---

## §5 What a capture recreate costs

- Stop: SIGTERM, socket closed, producer flushed for up to 5 s; nothing received is lost.
- Start, measured: scrapeable 3 s after `docker compose up -d`, fresh frames 0 s later
  ([failure modes](../architecture/16-failure-modes.md), capture SIGKILL row, run 2026-08-26).
- In-process reconnects on 2026-11-25, last trade frame of one connection to the first of the
  next: Kraken 9.7 s, Coinbase 5.6 s (both 11:32 to 11:35 UTC, counted as `involuntary` in
  `k2_capture_reconnects_total`; `up` stayed 1, so no container restarted):

```bash
docker exec k2-trino trino --user reviewer --output-format TSV_HEADER --execute \
  "SELECT conn_id, min(recv_ts) AS first_frame, max(recv_ts) AS last_frame FROM lake.bronze.kraken_trade
   WHERE recv_ts >= current_timestamp - INTERVAL '30' HOUR GROUP BY conn_id ORDER BY 2"
```

  The same query on `lake.bronze.binance_trade` failed in Trino ("Error opening Iceberg split
  ... Index 7 out of bounds for length 5", one file of 2026-11-25), which is a separate finding
  for the Trino reader, not for this note.
- Lost trades: Binance and Kraken trades in the window; recorded by silver's `seq_gap` and
  `missing_before`, because trade ids are sequential per symbol (`docker/lake/silver.py`
  docstring). Coinbase's `market_trades` subscribe snapshot refills its window. After the rollout
  the same query, run on all three bronze trade tables, gives the measured dark window per
  recreate, and `sum(missing_before)` over the window gives what it cost.

---

## §6 Implementation

Two pull requests, so the code lands and runs unchanged before anything is switched.

### Pull request 1: the switch and the honesty fixes (no behaviour change)

| File | Change |
|---|---|
| `services/capture-rust/src/config.rs` | `book_capture: BTreeMap<String, bool>` on the file, `#[serde(default)]`; unknown venue key is an error; `Instruments::book()` returns the venue's value, true when absent |
| `src/exchanges/binance.rs` | `stream_query()` omits `@depth20@100ms` when off |
| `src/exchanges/kraken.rs` | `subscribe_messages()` omits the `book` subscribes when off; `instrument` and `trade` unchanged |
| `src/exchanges/coinbase.rs` | `subscribe_messages()` omits `level2` when off |
| `src/main.rs` | one `BOOK_STREAMS = ["book", "depth20", "l2_data"]`; `streams_for` drops them when off, so seeding, `continuous_streams_for`, the watchdog and the health check follow; `checksummed` and the depth-gauge symbols empty when off; the sampler's book block skipped when off. `handle_frame`, `replay`, `record` untouched |
| `services/capture-rust/README.md` | the switch, what it changes, what it never changes |
| `docker/lake/maintenance.py` | `audit_schema_drift` reports the sampled frame count of its stream; result built by a pure helper |
| `notebooks/k2lake.py` | `MAX_QUOTE_AGE` bound in `_trades_sql`; `checksum_failed` NULL with no frames |
| `contracts/schema/k2.schema.json`, `platform/k2gen/k2gen/validate.py`, `generate.py` | the `paused` property, its checks and its rendering |
| `docker/prometheus/tests/capture-alerts.test.yml` | the trades-only venue shape |
| `.claude/skills/release-check/SKILL.md` | the book smoke reads the switch |

Tests, each one that fails if the logic breaks:

1. **The switch removes exactly the book subscriptions, per venue** (`config.rs`, adapters): from
   one registry with the section absent, with `false` and with `true`, build each adapter; the
   absent and `true` outputs equal today's strings byte for byte; the `false` output equals the
   `true` output minus exactly the book subscription (Binance: the 12 `@depth20@100ms` names;
   Kraken: the `book` subscribe frames, `instrument` and `trade` kept; Coinbase: `level2`).
2. **Registry parsing:** absent means on; an unknown venue key fails; `on` or `off` as a value
   fails (it is not a YAML 1.2 boolean).
3. **Watched and seeded streams** (`main.rs`, `metrics.rs`): with books off, no venue watches or
   seeds a book stream, every venue still watches at least one continuous stream (Binance: `trade`
   only), and `zero` renders no `k2_capture_book_depth`, `k2_capture_checksum_failures_total` or
   `k2_capture_book_updates_ignored_total` series: the inverse of
   `zero_seeds_every_series_an_alert_reads`.
4. **Decoding ignores the switch:** the committed replay hashes (`tests/replay*.rs`, which load
   `config/instruments.yaml`) are unchanged in pull request 2, where the shipped registry says
   off. No new test: the existing golden hashes are the test.
5. **An ingest run with no book frames is clean:** CI has no Spark, so two parts. In CI,
   `books.replay_partition` over an empty partition yields no book row and no state row (pure,
   `tests/test_lake_books.py`). On the live stack, the first scheduled run after each venue's
   flip is the gate (§7 step 4). The empty Spark path has no CI harness; that is stated, not
   hidden.
6. **Drift audit honesty** (`tests/test_lake_maintenance.py`, existing fake-Spark pattern): a
   sample with no frame of the stream returns a pass whose detail says nothing was sampled.
7. **Notebook helpers** (`notebooks/test_k2lake.py`, synthetic DuckDB tables): a trade one hour
   after the last quote comes back with NULL quote columns, `quote_coverage_pct` 0, and Kraken
   `checksum_failed` NULL for a window with no book frame.
8. **Alert shape** (`capture-alerts.test.yml`): a venue with only fresh `trade` and `heartbeat`
   gauges and no book series fires neither `CaptureFeedStale` nor `CaptureBookDepthDegraded`.
9. **k2gen** (`platform/k2gen/tests/`): a paused table validates; a paused table with a
   `latency` SLA fails; the generated page carries the Status row.
10. **Switch and contracts agree** (`tests/test_contracts.py`): every venue off means all nine
    tables marked `paused`; every venue on means none; mixed is allowed (a staged resume).

Then `make test`, `make lint`, `make check-docs`, `make test-k2gen`, `make check-alerts`.

### Pull request 2: the flip

- `config/instruments.yaml`: `book_capture` all `false`, with a header line citing ADR-050.
- The nine contracts gain `paused: {since: <flip date>, adr: ADR-050}`; `make generate`.
- One sentence and a link to ADR-050 in: README (the "Complete" row and the diagram's capture
  node), architecture chapters 01, 04, 05, 06, 09 and 10, `docs/operations/data-catalog.md` (also:
  last commit is not freshness; `bbo_live`'s newest second), `docs/operations/dashboards.md`,
  `notebooks/README.md`, `services/capture-rust/README.md`, `docs/runbooks/capture-checksum-failure.md`
  and `capture-sequence-gaps.md` (cannot fire while paused), the Grafana Book row description.
  Every such line cites ADR-050, so `git grep -n ADR-050` is the resume checklist.
- A runbook for the switch, written with `/runbook` from the measured rollout (steps, timings).
- The Outcomes the ADR lists, appended once the flip has run.

---

## §7 Rollout on the live stack (not run)

Preconditions: P0 closed; the release gate run finished; no other heavy Docker job running.

1. **Pull request 1 merged; image rebuilt with books on.** The running captures report
   `k2_capture_build_info{git_sha="v3-phase-e-26-g9932f42"}`, built 2026-09-03; `git log
   9932f42..HEAD -- services/capture-rust` lists seven commits since, mostly dependency
   upgrades. That is why the image is recreated on its own, before the switch.
   ```bash
   docker tag k2-capture:v3 k2-capture:pre-adr050      # rollback image
   docker compose build capture-binance                  # the one service that builds k2-capture:v3
   ```
2. **Recreate each capture with the new image, books still on, one at a time** (binance, then
   coinbase, then kraken), and before the next:
   ```bash
   docker compose up -d --force-recreate --no-deps capture-binance
   docker inspect -f '{{.State.Health.Status}}' k2-capture-binance          # healthy (start_period 60 s)
   curl -s -G 'http://127.0.0.1:9090/api/v1/query' \
     --data-urlencode 'query=k2_capture_build_info'                          # new git_sha
   curl -s -G 'http://127.0.0.1:9090/api/v1/query' \
     --data-urlencode 'query=time() - k2_capture_last_message_ts_seconds{exchange="binance"}'
   curl -s 'http://127.0.0.1:9090/api/v1/alerts' | python3 -c \
     'import json,sys; print([a["labels"]["alertname"] for a in json.load(sys.stdin)["data"]["alerts"] if a["state"]=="firing"])'
   ```
   Every stream as before, book depth gauges above zero, nothing firing.
3. **Pull request 2 merged; the registry updated in place.** Captures mount `./config` as a
   directory and `spark-iceberg` mounts the file; a rename (what `git pull` does) leaves every
   holder reading the old file until all are recreated (`docker/README.md`, verified 2026-08-26).
   So write the new content into the same inode first, stage it, then fast-forward; git then has
   nothing to rewrite. Verified in a scratch repository on 2026-11-26, not on the live checkout:
   ```bash
   ino=$(stat -c %i config/instruments.yaml)
   git fetch origin && git show origin/main:config/instruments.yaml > config/instruments.yaml
   git add config/instruments.yaml && git pull --ff-only
   [ "$(stat -c %i config/instruments.yaml)" = "$ino" ] && echo "inode kept"
   docker exec k2-spark-iceberg grep -c book_capture /home/iceberg/config/instruments.yaml   # 1
   ```
4. **Flip one venue at a time:** binance (its empty stage 2e path is the simplest), then
   coinbase, then kraken. For each:
   ```bash
   docker compose up -d --force-recreate --no-deps capture-binance
   ```
   then, within 2 minutes: healthy; `k2_capture_last_message_ts_seconds{exchange="binance"}`
   lists `trade` only (kraken: `heartbeat`, `trade`; coinbase: `heartbeats`, `market_trades`), all
   fresh; `k2_capture_book_depth{exchange="binance"}` returns no series; nothing firing; the book
   topic's high watermarks stop moving:
   ```bash
   docker exec k2-redpanda rpk topic describe market.crypto.v3.book.binance -p
   ```
   Then two scheduled ingest runs (10 minutes): both succeed; the task log shows
   `stage 2e: 0 rows -> lake.silver.book_binance`; `max(recv_ts)` of the bronze book table stops
   while the bronze trade table's advances:
   ```bash
   docker exec k2-trino trino --user reviewer --execute \
     "SELECT (SELECT max(recv_ts) FROM lake.bronze.binance_depth20), (SELECT max(recv_ts) FROM lake.bronze.binance_trade)"
   ```
   Only then the next venue.
5. **Record the pause**, per venue, with the last book frame's time from step 4:
   ```bash
   docker exec -e K2_LAKE_DRIVER_MEMORY=512m k2-spark-iceberg python3 /home/iceberg/lake/record_check.py \
     --job operator --check book_capture_paused --scope market.crypto.v3.book.binance --observed 0 \
     --detail 'from <max recv_ts>Z: L2 not subscribed by decision, ADR-050'
   ```
6. **The first nightly run:** `LakeAuditFailed` silent; the three book `bronze_schema_drift` rows
   say nothing was sampled.

---

## §8 The first trades-only UTC day (for the retune)

Day D is the first whole UTC day after the last flip. Take each reading at D 00:00 and D+1
00:00 UTC, or once over `[24h]` ending D+1 00:00 (Prometheus `@ <epoch>`).

| Measure | Why the retune needs it | Command |
|---|---|---|
| Wire bytes per venue and stream | the new arrival rate, against §1.1 | `sum by (exchange, stream) (increase(k2_capture_bytes_total[24h] @ <D+1>))` |
| Capture memory and CPU peaks | Coinbase's 512M was sized for a full book | `max by (name) (max_over_time(container_memory_working_set_bytes{name=~"k2-capture-.*"}[24h] @ <D+1>))`; `max by (name) (max_over_time(rate(container_cpu_usage_seconds_total{name=~"k2-capture-.*"}[5m])[24h:5m] @ <D+1>))` |
| Raw archive bytes per topic for D | lake growth per day | the §1.3 query with `kafka_ts_day = DATE '<D>'` |
| Lake bytes per table, both ends | growth by layer; compaction at 03:00 is inside the day | the §1.2 query |
| Redpanda bytes per topic and per busiest partition | whether `retention.bytes` still binds or the 48 h does; the broker's disk slice | the §1.4 `du` commands |
| MinIO bytes, both ends | the object store's daily growth | `docker exec k2-minio du -s -B1 /data` |
| ClickHouse bytes per table, both ends | hot tier growth | the §1.5 `system.parts` query |
| Mean ingest run | the time saved, against 192.4 s | the §1.5 `airflow_dagrun_duration_success` ratio `@ <D+1>` |
| Nightly maintenance run, and the book audits' share | whether to keep the book audits (ADR open question 3) | `airflow_dagrun_duration_success_sum{dag_id="lake_maintenance"}` increase over the night; the audit lines' order in the task log |
| Per-run offsets on the busiest raw partition | the per-run cap of 600,000 | the `k2.kafka-offsets` of two consecutive `raw.messages` ingest snapshots |

The retune is its own reviewed pull request: limits update the ADR-010 Outcome and the
`docker-compose.yml` summary, retention `docker/redpanda/init.sh`, each with these numbers as
provenance (`/benchmark-report`).

---

## §9 Switching back on (not run)

1. Revert the retune's lowered limits first, in their own change.
2. Pre-flight per venue, on a registry copy with that venue's `book_capture: true`:
   ```bash
   mkdir -p /tmp/k2-preflight && sed 's/kraken: false/kraken: true/' config/instruments.yaml > /tmp/k2-preflight/instruments.yaml
   docker run --rm -v /tmp/k2-preflight:/app/config:ro k2-capture:v3 \
     record --exchange kraken --seconds 20 --symbols BTC/USD > /tmp/k2-preflight/kraken.jsonl
   docker run --rm -i -v /tmp/k2-preflight:/app/config:ro k2-capture:v3 \
     replay --exchange kraken --fixture - < /tmp/k2-preflight/kraken.jsonl > /tmp/k2-preflight/out.jsonl
   grep -c '"checksum_ok":false' /tmp/k2-preflight/out.jsonl     # 0
   ```
   The replay's `actions` count is 0 and no frame is unknown. Nothing from the pre-flight is
   committed (no real venue data in the repository).
3. Set the venue's switch to true in a pull request; update the registry in place (§7 step 3).
4. Recreate kraken, then coinbase, then binance, each followed by: depth gauges above zero within
   a second; no `CaptureChecksumFailure`, `CaptureBookDepthDegraded`, `CaptureFeedStale`; two
   ingest runs writing book rows again; produce errors 0 (Coinbase's multi-megabyte subscribe
   snapshots are back).
5. The next nightly run: `bronze_schema_drift` samples book frames and passes; `kraken_checksum`
   passes with a growing frame count.
6. Remove the `paused` marks, `make generate`, revert every `ADR-050` line, file
   `book_capture_resumed` rows (`--detail 'from <pause>Z to <first book frame>Z: ADR-050'`,
   `--observed <seconds paused>`), append the ADR-050 Outcome.

---

## §10 Rollback

- **Of the flip, per venue:** set its switch back to true, update in place, recreate: the old
  path, measured every day until now. Seconds of trades lost per recreate (§5).
- **Of the new image:** `docker tag k2-capture:pre-adr050 k2-capture:v3` and recreate. The old
  binary ignores `book_capture` (serde skips unknown keys), so it subscribes to books whatever
  the registry says.
- **Of the contracts and docs:** revert pull request 2.

---

## §11 Not verified

- Stage 2e on empty input, live (§1.6).
- Kraken heartbeats with only `instrument` and `trade` subscribed; Coinbase sequence continuity
  without `level2`.
- A graceful recreate's dark window (§5).
- Every saving in §8, which is why §8 exists.
- The git in-place recipe on the live checkout (verified in a scratch repository only).
- The pre-flight's `grep` on the replay output's field name.
