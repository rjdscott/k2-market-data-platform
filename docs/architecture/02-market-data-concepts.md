# 02. Market data concepts

> **You will learn** the ideas a market data platform is built on: books, sequences, checksums, clocks, numbers, symbols, candles, and what "complete" means.
> **Read this if** any term in chapters 04 to 11 is new to you, or you are explaining the platform to someone.
> **Before this** chapter 01.

Each concept follows the same shape: the problem, the usual options, what K2 chose and why,
and where to see it. Read straight through, or jump from a link in a later chapter.

**Retired, 2026-11-26.** The v3 lake ingest is retired ([ADR-056](../adr/ADR-056-unified-data-product-platform.md),
phase 000 of the [platform plan](../plans/2026-11-26-unified-product-platform/README.md)): its raw, bronze,
silver, gold and audit tables were backed up and dropped, and its code and chapters 08 and 09 are archived in
[`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/). No live crypto data lands in the lake until phase 004,
and ClickHouse is paused ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)). The
concepts stand, and so do the capture-side choices; where a **K2.** paragraph says what a lake layer did, it is
the record of that retired pipeline, and [chapter 20](20-target-data-product-platform.md) is the target that
replaces it.

## Order books

**Problem.** A venue's order book is the set of resting bids and asks at each price. Level 2
(L2) gives price and total quantity per level; Level 3 (L3) gives every individual order.
Public WebSocket feeds are L2. Venues send a book either as complete snapshots on a fixed
cadence, or as one snapshot followed by deltas (a changed level, a removed level) that the
client must apply in order to keep a correct local copy.

**Options.** Store snapshots only (small, but no state between samples). Store deltas only
(exact, but a consumer must replay from the start to know anything). Keep a local book from
deltas and sample it (state to get wrong, but products are cheap).

**K2.** Every venue frame is published verbatim to its raw topic, so the deltas reach the
broker; the capture keeps a local book per symbol and samples the top 20 levels once a second
(paused with book capture, [ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md));
the retired lake ingest archived every frame and replayed every archived delta to rebuild the
same books offline. Binance sends complete 20-level partials, so it needs no local state at all.
See [05](05-capture.md), [06](06-capture-venues.md), [09](09-lake-layers.md).

## Sequencing

**Problem.** A dropped or reordered delta silently corrupts a local book. Venues expose
different continuity signals: Binance a per-symbol update id, Coinbase one sequence number
across every channel on a connection, Kraken none at all.

**Options.** Ignore it and hope. Reconnect on any anomaly (safe, expensive, loses time).
Check the venue's own signal and repair at the smallest scope it allows.

**K2.** Per-venue policy: Binance regression drops that symbol and waits for the next
complete partial (ADR-027 wrote reconnect; the adapter resubscribes the symbol and takes the
next partial as its snapshot); Coinbase gap drops every book and reconnects, because the gap cannot be
attributed to one product; Kraken has no sequence, so it relies on the checksum below.
Every gap is counted (`k2_capture_gaps_total`) and alerted. See [06](06-capture-venues.md).

## Checksums

**Problem.** Without sequence numbers, how does a client know its local book still matches
the venue's? Kraken answers by publishing a CRC32 over the top 10 asks and bids with every
update; a client computes the same over its local book and compares.

**Options.** Trust the feed. Verify on a sample. Verify every update and act on the first
mismatch.

**K2.** Every Kraken `book` frame is verified. On mismatch the capture emits one last
snapshot marked `checksum_ok = false`, drops the book and resubscribes, so the failure is
visible before it is repaired. The retired lake ingest re-verified every archived frame in
replay; a checksum that passed live and failed in replay meant the archive had a hole. Kraken's
documented example (`3310070434`) is a unit test. See [06](06-capture-venues.md#kraken).

## Timestamps and clocks

**Problem.** A trade has at least two times: the venue's `exchange_ts` (their clock, their
matching engine) and the moment it reached us. Venue clocks drift; the internet adds tens to
hundreds of milliseconds; neither is separable after the fact unless both are stored.

**Options.** Store the venue time only (cannot measure our own pipeline). Store an
"ingestion time" at the database (measures the wrong thing, after parsing and queueing).
Stamp the receive time as the very first act on frame arrival and carry both.

**K2.** `recv_ts_ns` is taken before parsing, travels in the record body and a Kafka header,
and in the retired lake `recv_ts_ns` partitioned bronze and the silver books and `exchange_ts`
partitioned silver trades and gold trades. Latency is reported as
venue to receive, per venue, with the caveat that it includes transit and skew.
See [05](05-capture.md), [15](15-capacity-model.md).

## Fixed-point numbers

**Problem.** Prices and quantities arrive as decimal strings. Binary floats cannot represent
most of them exactly (0.1 is not representable), and errors accumulate through sums,
products and comparisons. A candle built on floats can report a close that never traded.

**Options.** Floats (fast, wrong at the eighth digit). Arbitrary-precision decimals (exact,
slow, awkward across languages). Integers at a fixed scale: store `price × 10^8` as a
64-bit integer.

**K2.** `int64` at 1e-8 on the wire (Avro `long` fields `price`, `qty`), in ClickHouse and, until
it was retired, in lake gold (`price_e8`, `qty_e8`); decimal views on read. A venue value with more than 8 decimals is counted as precision loss, never
rounded silently. Eight places cover every crypto venue's tick and lot sizes with room.
See [07](07-wire-contracts.md), [10](10-clickhouse-gold.md).

## Symbols and venues

**Problem.** The same instrument is `BTCUSDT` on Binance, `BTC/USD` on Kraken (`XBT/USD` on
its older API) and `BTC-USD` on Coinbase, and the quote currencies differ (USDT versus USD).
Research wants one name; forensics wants the venue's.

**Options.** Rename at capture (loses the native name). Rename at query time (every query
repeats the mapping). Carry both, mapped from one registry.

**K2.** `config/instruments.yaml` holds native and canonical spellings for every instrument;
capture keys topics by the canonical symbol. In the retired lake, silver carried both columns
and gold used `canonical_symbol`, as ClickHouse does; the lifecycle lived in the retired SCD2
dimension `dim_instrument`, read as of the trade's timestamp ([ADR-030](../adr/ADR-030-scd2-security-master.md)),
which phase 004's security master replaces.
See [09](09-lake-layers.md), [12](12-data-strategy.md).

## Trades and replays

**Problem.** Venues re-send. A reconnect replays the last trades; some venues re-send within
a connection; a consumer restart re-reads a topic. The same logical trade arrives more than
once, and a naive count or sum is wrong.

**Options.** Deduplicate at capture (loses the evidence that a replay happened). Deduplicate
nowhere (every downstream number is inflated). Keep every delivery with lineage, deduplicate
once at the layer research reads.

**K2.** In the retired lake, silver kept every delivery and flagged `venue_replay`, and gold
kept the first delivery per `(exchange, canonical_symbol, trade_id)`; ClickHouse (paused) makes
the same rule a `ReplacingMergeTree` key so a replay collapses under `FINAL`. See [09](09-lake-layers.md),
[10](10-clickhouse-gold.md).

## OHLCV candles

**Problem.** A candle for a bucket is open (first trade), high, low, close (last trade),
volume and count. "First" and "last" need a total order; two trades in the same microsecond
must resolve the same way in every engine, and a late-arriving trade must be able to re-open
a bucket already computed.

**Options.** Sum-on-insert tables (fast; but non-additive columns like open and close
resolve per insert block, which is the v2 bug). Compute on read over deduplicated trades
(always right; costs a scan). Materialise per bucket and recompute the buckets a batch
touches.

**K2.** ClickHouse served candles on read for the live head until its pause; the retired lake
materialised `ohlcv_{1m,5m,1h,1d}` by recomputing every bucket a batch touched, with the total
order `(exchange_ts, recv_ts_ns, trade_seq)`. Three engines (lake, ClickHouse, DuckDB) were
compared at a pinned snapshot by `make parity-ohlcv`, which printed the number of differing
buckets; the gate now prints a SKIP naming phase 004, where it is re-pinned. See [10](10-clickhouse-gold.md), [08](08-lake-ingest.md).

## BBO and book features

**Problem.** Most research on order books uses a handful of derived numbers: best bid and
ask (BBO), mid price, spread in basis points, imbalance (bid quantity versus ask quantity at
the top), and microprice (the mid weighted by the opposite side's quantity, a short-horizon
fair-value estimate).

**Options.** Compute per query, or materialise once per second from the replayed book.

**K2.** The retired lake's `bbo_1s` carried all of them per venue, symbol and second, computed
once from the replayed book; ClickHouse's `bbo_live` computed the same on read for the head
until its pause. Definitions
are in [13](13-schema-design.md). See [09](09-lake-layers.md).

## Top-of-book sampling

**Problem.** Full depth at every delta is large and rarely what a query wants. A fixed
depth at a fixed cadence is a product a researcher can join against, at a known loss.

**Options.** Full depth (exact, expensive to store and query). Top N at every delta (still
large). Top N at a fixed cadence (small, joinable; intra-second moves are not in it).

**K2.** Top 20 at 1 Hz, sampled at the end of each second from the replayed book (book
capture is paused). The retired raw archive kept every delta, so a deeper or faster product
could be rebuilt without touching capture; the target keeps the property with a verbatim
landing per product ([20](20-target-data-product-platform.md)). See [09](09-lake-layers.md), [ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md).

## Latency and what it means

**Problem.** A latency number is only meaningful with its two endpoints named. Venue
timestamp to our receive includes the venue's matching-engine-to-publish delay, the
internet, and clock skew; none of it is a trading-path number.

**Options.** Report one number, or report endpoints, percentiles and sample size.

**K2.** Reported as `k2_capture_exchange_to_recv_seconds` per venue with p50/p95/p99 and the sample
size, and labelled as a research-platform number. The Coinbase subscribe snapshot, whose
trades predate the connection, is excluded from the histogram because it is history, not
latency. See [15](15-capacity-model.md), [benchmarks](../benchmarks/2026-08-27.md).

## Completeness

**Problem.** "We captured everything" is a claim, not a fact, until something can show a
hole. Holes come from venue disconnects, a full producer queue, broker retention expiring
before ingest, or a consumer bug.

**Options.** Assume. Count at one point. Count at every boundary and reconcile.

**K2.** Capture counts frames, records produced and records dropped; the retired lake's audits
asserted offset continuity per partition, raw-to-bronze parity per venue, bronze-to-silver parity,
one row per identifier in gold, and checksum pass rates, and every acknowledged hole was a row in
its audit table, retired with it. See [08](08-lake-ingest.md#proof), [11](11-observability.md).

## Key points

- Archive every frame verbatim; every product is a rebuildable function of it.
- Continuity is checked per venue with the venue's own signal, and failure is made visible
  before it is repaired.
- Two clocks per event, integers for money, one canonical symbol beside the native one.
- Deduplicate once, at gold, with lineage to every delivery below it.
- A number without its endpoints and sample size is not a number.
