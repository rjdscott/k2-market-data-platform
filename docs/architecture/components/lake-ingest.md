# Lake ingest — Redpanda to Iceberg, exactly once

`docker/lake/ingest.py` is the only writer of new data into the lake. Every 5 minutes it
reads Redpanda by explicit offset range, appends every record verbatim to `raw.messages`,
then derives bronze, silver and gold from that archive. It is idempotent by construction:
the position it consumed to is written into the same Iceberg commit as the rows, so a run
killed at any instant either happened or did not. Design: [ADR-022](../../adr/ADR-022-exactly-once-via-snapshot-offsets.md);
operations: [`docker/lake/README.md`](../../../docker/lake/README.md).

## Shape

```mermaid
flowchart TB
  RP[("Redpanda<br/>raw · trades · book per venue")]
  S1["stage 1 · raw<br/>startingOffsets = last committed end<br/>endingOffsets = broker latest"]
  RAW[("raw.messages<br/>commit + k2.kafka-offsets")]
  S2["stage 2 · bronze<br/>decode venue JSON, per venue"]
  BR[("bronze.&lt;venue&gt;_&lt;msg&gt;<br/>commit + k2.src-snapshot-id")]
  S3["stage 2c · silver<br/>type, flag against 1-day lookback"]
  SV[("silver.trades_* · book_*")]
  S4["stage 2d/2e · gold + books<br/>first delivery · MERGE candles<br/>book replay → top-20 · bbo_1s"]
  GD[("gold.*")]
  RP --> S1 --> RAW --> S2 --> BR --> S3 --> SV --> S4 --> GD
```

One process, one Spark session, one file lock (`lock.py`): a second writer exits 2 rather
than interleaving appends; nightly maintenance takes the same lock blocking, so compaction
never shares the 8 GiB container with an ingest.

## Stage 1 — offsets in the snapshot

1. **Where to start.** `offsets.latest_summary()` finds the newest `raw.messages` snapshot
   whose summary carries `k2.job = ingest` — maintenance snapshots (compaction, expiry) have
   no `k2.job` and are skipped by that property, not by guessing at Iceberg's `operation`
   field. Its `k2.kafka-offsets` map holds Kafka *end* offsets (exclusive), so the next
   start is the same number copied, no ±1. A partition the map has never seen starts at
   `EARLIEST`: for an archive that is never expired, the beginning is always right.
2. **Where to stop.** `bounded_offsets()` clips each partition at the broker's `latest`,
   at `--max-offsets-per-partition` for backlog slicing, or at `--end-timestamp`. What it
   leaves behind is the `k2_lake_ingest_backlog_offsets` gauge.
3. **What is gone.** If a committed start is below the broker's earliest offset, retention
   evicted records the lake never read. `failOnDataLoss=true` and the run fails; it keeps
   failing until an operator types `--accept-data-loss`, which records each hole as an
   `offset_gap` row in `audit.checks` before skipping it. There is deliberately no
   environment variable for this: the scheduled path can never absorb a loss silently.
4. **The commit.** One `append` carrying `k2.job`, `k2.kafka-offsets` (merged over the
   previous map — a committed offset is never dropped), `k2.kafka-backlog` and
   `k2.max-kafka-ts`. Rows and position are the same atomic metadata swap in Lakekeeper.

## Stages 2–2e — incremental by parent snapshot

Each layer reads its parent with `start-snapshot-id` = the id it last committed
(`k2.src-snapshot-id` on its own newest snapshot) and `end-snapshot-id` = the parent's
current id, then writes that end id into its own commit. Same contract as stage 1, one
level up. Start == end means nothing new and the stage is skipped (Spark rejects the empty
range).

| Stage | Module | What it does | Why it is shaped that way |
|---|---|---|---|
| bronze | `bronze.py` | decodes raw JSON per venue with `from_json` in PERMISSIVE mode into the venue's own field names; unparseable frames are counted, not dropped | the lake must not depend on the capture's parser; a venue field is never lost before silver |
| silver | `silver.py` | types and flags (`venue_replay`, `seq_gap`, `precision_loss`) by scoring `batch ∪ last 1 day of silver` with window functions | replay and gap detection need history; one day bounds the scan |
| gold | `gold.py` | first delivery per `(exchange, canonical_symbol, trade_id)`; dims from `config/instruments.yaml`; OHLCV `MERGE`d for the buckets the batch touched, total order `(exchange_ts, recv_ts_ns, trade_seq)` | a late delivery must re-open its candle, and open/close ties must resolve the same way in every engine |
| books | `books.py` | one streaming replay per connection: Kraken CRC32 verified per update, end-of-second top-20 and BBO sampled from the same pass; `gold.book_state` carries the book across runs | replaying once is cheaper than twice, and a checksum proves the replay is the venue's book |

`rebuild.py --layer bronze|silver|gold|books` drops a layer and recomputes it from its parent
over the whole archive; bronze 520 s, books 2,367 s
([benchmarks](../../benchmarks/2026-08-27.md#lake)).

## Proof

`maintenance.py` runs nightly and writes every assertion to `audit.checks`; any failure
exits non-zero and `LakeAuditFailed` fires ([runbook](../../runbooks/lake-audit-failed.md)).

| Audit | Asserts |
|---|---|
| `offset_continuity` | per partition, the committed ranges tile with no hole other than recorded `offset_gap` rows |
| `bronze_parity`, `bronze_unparseable`, `bronze_schema_drift` | raw frames in == bronze rows out per venue; no undeclared keys |
| `silver_parity`, `silver_flags` | bronze == silver row counts; flag rates reported |
| `gold_trades`, `ohlcv_parity` | one row per identifier; candles recompute from gold trades to the same values |
| `book_parity`, `kraken_checksum` | book rows tile the deltas; failed checksums outside acknowledged windows are zero |

Independently, `make lake-verify` runs the parity checks on demand and `make parity-ohlcv`
compares lake, ClickHouse and DuckDB candles at a pinned snapshot.

## Practices

| Practice | Where it is enforced |
|---|---|
| Position and data in one commit | `snapshot-property.k2.kafka-offsets` on the `raw.messages` append (`ingest.py`); `test_lake_offsets.py` |
| Exactly-once by construction, tested | `make lake-verify`: a second run adds 0 rows; chaos `lake-ingest-kill.sh` (42 s, no duplicates) |
| Loss needs a human | `--accept-data-loss` is a CLI flag only; scheduled runs fail until typed; each hole is an `offset_gap` audit row |
| One writer | `lock.py` `flock`; ingest exits 2 if held, maintenance waits |
| Layers read parents by snapshot id | `k2.src-snapshot-id` in `bronze.py`, `silver.py`, `gold.py`, `books.py` |
| Nightly proof, alerted | `maintenance.py` audits → `audit.checks`; non-zero exit → `LakeAuditFailed` |
| Pure logic unit-tested off Spark | `offsets.py`, `book.py`, `instruments.py` have no pyspark import; `tests/test_lake_*.py` |

## Trade-offs

- **Batch, five minutes.** Freshness in the lake is bounded by the cron; ClickHouse covers
  the head from the topics. A streaming writer would need its own checkpoint store — the
  thing the snapshot-summary design removes.
- **One writer, one container.** Ingest, rebuilds and maintenance share 2 CPU / 8 GiB in
  turn. Serial by lock is a capacity ceiling, not a correctness one.
- **Retention is the deadline.** `raw.*` topics keep 48 h; an ingest outage longer than that
  is a recorded, acknowledged hole, never a silent one
  ([failure-modes.md § lake](../failure-modes.md#lake-tier)).
- **Bronze keeps vendor schemas.** Cross-venue work waits for gold; in exchange nothing a
  venue sent is normalised away before it can be inspected.
