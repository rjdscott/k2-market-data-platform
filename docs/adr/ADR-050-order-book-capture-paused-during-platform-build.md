# ADR-050: Pause order-book capture while the platform is built

**Status:** Accepted
**Accepted:** by the owner on 2026-11-26, after an independent design review in two rounds;
the findings and their resolution are in pull request #223
**Date:** 2026-11-26
**Author:** Platform
**Category:** Ingestion / Capture
**Decided by:** the owner on 2026-11-26 ([decision record](#the-owners-decision-2026-11-26)); this
ADR states how. An independent review on 2026-11-26 asked for the changes in
[Review changes](#review-changes-2026-11-26); it moves to Accepted when the owner accepts them
**Plan and evidence:** [research note](../research/2026-11-26-order-book-capture-pause.md)
("note §n"), which carries every command behind the numbers below

---

## In plain words

Order-book data is about 95% of the bytes the three capture processes download, and the tables
built from it take ten times the lake space of the trade tables, yet nothing the platform build
is working on reads it. We will stop subscribing to it on every venue with a required per-venue
switch in `config/instruments.yaml`, which each capture also reports as a metric, and keep every
book row already stored, readable and unchanged. The ingest keeps running as it is; the capture
stops watching the streams it no longer asks for and pings Binance so a dead connection is still
noticed within a minute; the book audits pause with the switch; and the contracts, the venue
dimension and two alerts are keyed to it. The cost is a hole in book history from pause to resume,
no live proof that the book code still matches what the venues send, and a few seconds of trades
per venue each time a capture restarts. The pause is decided; what is left is the owner's
acceptance of the reviewed design.

---

## Context

Terms are explained where they first appear; the [glossary](glossary.md) has each one too.

### What a capture subscribes to today

K2 captures three exchanges ("venues") with one Rust process per venue, `k2-capture`, holding
one WebSocket connection to that venue's public feed. An **order book** is the list of resting
buy and sell orders at each price; **L2** means the feed publishes it aggregated per price level
(price and total quantity), not per order ([glossary](glossary.md#order-book-and-l2)). Each
venue publishes the book on its own channel, beside its trade channel:

| Venue | Subscribed today (`services/capture-rust/src/exchanges/`) | Book channel and its frames | Still subscribed when paused |
|---|---|---|---|
| Binance | `<symbol>@trade` and `<symbol>@depth20@100ms` for 12 symbols, named in the connection URL | `depth20`: a complete top-20 book per symbol every 100 ms | `trade` |
| Kraken | `instrument`, `book` at depth 25, `trade`, for 11 symbols; heartbeats arrive unasked | `book`: one snapshot, then deltas, each carrying a CRC32 checksum of the top 10 levels | `instrument`, `trade`, heartbeats |
| Coinbase | `level2`, `market_trades`, `heartbeats`, for 11 symbols | `l2_data`: a full-depth snapshot, then deltas | `market_trades`, `heartbeats` |

Every frame a venue sends is produced first, byte for byte, to the venue's raw topic
`market.crypto.v3.raw.<venue>` in Redpanda (the Kafka-compatible broker); trades decoded from it
go to `market.crypto.v3.trades.<venue>`; and once a second the capture samples each book's top
20 levels to `market.crypto.v3.book.<venue>` (`sink.rs`, `record.rs`). So when the book
channels stop, two flows stop with them: the book frames inside the raw topic, and the whole
book topic. Kraken's `instrument` channel is not book data: it publishes each pair's price and
quantity precision, which the security master `gold.dim_instrument`
([ADR-030](ADR-030-scd2-security-master.md)) reads.

### What the book costs

Measured read-only on the live stack, 2026-11-26 between 01:40 and 02:40 UTC (note §1 has each
command and its output):

| Measure | Book | Trades | Source |
|---|---|---|---|
| Bytes the captures received over the last 24 h, all venues | **95.0%** | 4.9% | `k2_capture_bytes_total`, per stream (Prometheus) |
| Same, per venue: book share | Binance 82.8%, Coinbase 97.3%, Kraken 99.6% | | same |
| Lake tables built from each, current snapshot, data files | **14.06 GiB** | 1.36 GiB (ratio 10.3 to 1) | Trino `"<table>$files"` sums |
| Raw archive bytes landed for UTC day 2026-11-25, by topic family | raw topics 90.9% (mostly book frames), book topics 5.4% | trades topics 3.7% | Trino `lake.raw."messages$partitions"` |
| Redpanda data directory by topic family | raw 82.4%, book 10.8% | trades 6.8% | `du -sb` per topic in `k2-redpanda` |
| ClickHouse hot tier | `gold.book_top20` 1.07 GiB | `gold.trades` 0.88 GiB | `system.parts` |

The lake bucket in MinIO (the object store) held 43.3 GiB in all (`du` in `k2-minio`), and
`raw.messages` 13.2 GiB of it. The 2026-11-25 day includes a 343-second outage at 22:33 UTC
([failure-recovery runbook](../runbooks/failure-recovery.md)); it changes the shares little.

### What reads the book today

- **The lake ingest**, every 5 minutes: [stage 2b](glossary.md#stage-2-and-stage-2b) decodes the
  book frames into `bronze.binance_depth20`, `bronze.kraken_book` and `bronze.coinbase_level2`;
  [stage 2e](glossary.md#stage-2e) replays them into `silver.book_<venue>`, `gold.book_top20`,
  `gold.bbo_1s` and the replay's carry-over `gold.book_state`. The mean scheduled run took
  192 s of its 300 s slot over the last 24 h; how much of that is books is not measured.
- **The nightly audits** ([`audit.checks`](glossary.md#auditchecks-and-the-nightly-audits)):
  eighteen of them read the book tables, including `kraken_checksum`, the venue's own checksum
  verified against our replayed book ([glossary](glossary.md#kraken-book-checksum)).
- **Five capture alerts** and the capture's own health check watch book streams or book state.
- **The security master** ([glossary](glossary.md#scd2-security-master)): `gold.dim_instrument`
  documents `book_depth` as "the L2 subscription depth in force", and `gold.dim_venue` carries a
  venue default depth.
- **The hot tier** ([glossary](glossary.md#hot-tier)): ClickHouse's `gold.book_top20` is fed from
  the book topics.
- **Three notebooks** and the helpers in `notebooks/k2lake.py`, and the release check's smoke
  queries. The release check is the fresh-clone gate run before a tag (the `/release-check`
  skill): it clones the repository, starts the whole stack from nothing and queries every tier.
- **No mart reads a book table.** A mart is a table built by dbt for consumers
  ([glossary](glossary.md#mart)); two of them read audit rows, including the book audits'.

### The owner's decision (2026-11-26)

Asked and answered on 2026-11-26, quoted as given:

- **Scope:** "L2 fully off, switchable". In the owner's words: "to save data ... focus only on
  trades from the exchanges, and stop the l2 data for now. that way we can focus on data
  engineering with lighter cleaner data for longer runs without downloading too much data"; and
  "i intend on switching it back on at some point, but just while we dev this platform. lets
  turn it off and speed up the dev cycles".
- **Existing book data in the lake:** keep it all.
- **Timing:** after phase P0 closes, on 2026-11-26. P0 is the first phase of the governed
  self-serve platform plan, its prerequisites and guardrails; P7, its last, ends in a tagged
  release ([glossary](glossary.md#p0-to-p7)).
- **Record:** a new ADR, independently reviewed before acceptance.
- **Limits and retention:** measure one full UTC day of trades-only data first, then retune in a
  separate reviewed change.
- **Book code upkeep while paused:** CI tests on synthetic fixtures; no scheduled live check.

### Constraints the design must respect

- **Raw first** ([ADR-021](ADR-021-raw-first-archive-and-lineage.md)): every frame received is
  archived. Not asking for a frame is allowed; receiving one and dropping it is not.
- **Snapshot expiry** ([glossary](glossary.md#snapshot-expiry-and-the-retention-floor)): each lake
  step resumes from the parent [snapshot](glossary.md#snapshot) it last read, and the nightly
  expiry removes snapshots older than 7 days but always keeps each table's last ten. So a step
  that stops while its parent keeps committing would, after a week, try to resume from a snapshot
  that is gone; a step whose parent also stops keeps that snapshot. Expiry never removes a file
  the current snapshot references, so kept rows are safe either way.
- **Silence is not health.** A check that passes because nothing arrived must not file a pass, or
  a paused product reads as a healthy one.
- **Configuration and process must agree visibly.** A capture reads its registry once, at start;
  a container not recreated, or a stale file mount, leaves the process doing something other than
  the file says, and nothing today would show it.
- **No Spark in CI.** The lake's Spark code is tested through pure functions and fakes; anything
  that needs a Spark session is verified on the live stack.

---

## Decision

**We will stop subscribing to order-book channels on all three venues until P7's release tag
(`3.0.0`), reviewing the pause on 2027-02-26 if that tag has not come, through a required
per-venue switch in `config/instruments.yaml` that each capture reads at start and reports as a
metric, and keep every book row already stored, because book frames are 95% of the bytes the
captures download and nothing the build is working on reads them.**

Scope: what the captures subscribe to and watch, and the honesty of everything that reads the
book tables. Not in scope: deleting anything, removing topics or tables, changing what the ingest
computes or writes, resource limits and retention (the separate retune after one measured day).

### 1. The switch

**What exists today:** nothing. The registry has `version`, `instruments` and an optional
per-instrument `book_depth`; the capture's only book setting is `K2_SNAPSHOT_INTERVAL_MS`. The
capture parses the registry with serde, which ignores keys it does not know, so today's binary
cannot see a new key: the old image is a clean rollback whatever the file says.

**What is added**, checked in with every venue `false`:

```yaml
# Order-book (L2) capture per venue, required. false: trades only (ADR-050).
book_capture:
  binance: false
  kraken: false
  coinbase: false
```

The section is **required**, with an entry for every venue: a capture whose registry lacks it, or
lacks its venue, or names a venue that does not exist, refuses to start. A default would let a
missing line silently mean "on". Values are YAML booleans, never `on` or `off`: PyYAML reads those
as booleans and serde_yaml as strings, so the lake and the capture would disagree. The checked-in
value is the live one: the repository is the configuration of the stack, so a fresh clone gets
the lighter stack, and the contracts and tests describe what runs. Per venue, not one global
value, because switching back is done one venue at a time (section 7).

**What a capture does with it** ([glossary](glossary.md#book-switch)):

1. **It reports it.** Every capture exports `k2_capture_book_capture{exchange}`, 1 or 0, set at
   start from what it read. This is what the process is actually doing, as opposed to what the
   file says: `scripts/health.sh` fails when the two differ, the rollout checks it after every
   recreate, the Grafana book row shows it, and the two book-only alerts are conditioned on it
   (section 3), so none of them needs an edit at resume.
2. **With `false`, it does not subscribe** to the book channel (third column of the table above).
   Kraken keeps `instrument`, which the security master and the book replay's checksum read.
   Coinbase keeps `heartbeats`, which keep its connection-wide sequence number moving.
3. **It does not watch the book stream.** Each capture judges its **continuous streams**, the ones
   that must keep delivering, against a silence bound: 60 s for book and heartbeat channels,
   300 s for trade channels, where a quiet market is not a fault. One table in `main.rs` drives
   three things: the **session watchdog**, which recycles the connection when a stream passes its
   bound; the container health check; and the `CaptureFeedStale` alert
   ([glossary](glossary.md#continuous-stream-and-session-watchdog)). Left in, an unsubscribed book
   stream would pass its 60 s bound on every connection: the watchdog would reconnect every
   minute, losing trades each time, and the container would go unhealthy.
4. **It does not seed the book series.** The capture creates some metric series at zero when it
   starts, so that an alert can fire on a feed that never delivered
   ([glossary](glossary.md#seeded-series)). For a venue not asked for its book, the book depth
   gauge, the Kraken checksum counters and the book streams' message counters are not created,
   exactly as Binance and Coinbase never get checksum counters today.
5. **It skips the once-a-second book sampling:** no snapshot records, no depth or level gauges.
6. **It decodes every frame exactly as before.** `handle_frame`, `replay` and `record` do not read
   the switch, so replaying archived book frames, and the synthetic fixture tests whose output
   hashes are committed, give identical bytes whatever the switch says. That is what keeps the
   paused code tested, as the owner chose.

**Binance's liveness without its book.** Binance has no heartbeat channel, so with `depth20` gone
its only watched stream would be `trade` at 300 s. A half-open connection (the socket looks open,
nothing arrives) would then lose up to five minutes of trades on the busiest venue before the
watchdog noticed, against one minute today; those trades are gone for good, because the venue
does not replay them. The WebSocket protocol's own ping is the fix: the Binance capture sends a
Ping control frame every 15 s from its 1 Hz ticker, and the venue's Pong answer counts as a
continuous stream, `pong`, with a 60 s bound. Today `ws.rs` drops every ping and pong
(`Some(Ok(_)) => continue`); the change surfaces a pong as a liveness event only: it is not a
venue frame, so it is never archived and never advances the frame counter. It costs a few lines
in `ws.rs` and `main.rs` and a control frame of a few bytes each way per 15 s; no market data.
It applies to Binance only, with books on or off, because Kraken and Coinbase keep 60 s
heartbeats.

**Coinbase's gap rule without its book.** Coinbase numbers every frame on a connection, across all
channels; `check_sequence` answers any gap with `Action::Reconnect`, a rule written to repair the
book. With books off it stays as it is: a gap now means a lost trade or heartbeat frame, and the
reconnect may still help: Coinbase's `market_trades` subscribe sends recent trades
(`Handled.history` in `exchanges/mod.rs`), which should refill the lost ones. That refill is **not
verified**: no measurement shows the resend covers every trade a gap lost, and the rollout must
check it (section 8). Each venue's flip is gated on no gaps and no reconnects for 15 minutes.

### 2. What restarting a capture costs

A capture reads the registry only at start, so every change is a container recreate,
`docker compose up -d --force-recreate --no-deps capture-<venue>`. On the stop signal the
process closes its connection and flushes its producer queue for up to 5 s
(`main.rs`, `sink.flush`), so frames already received still reach Redpanda. That is the
difference from a Redpanda restart, which the owner saw lose nothing on 2026-11-26 (13 s): there
the capture keeps reading and its queue waits for the broker, whereas during a capture restart
nothing is reading the venue at all. What is lost is what the venue sends while no connection is
open, from the old connection's close to the new one's subscribe:

- **Measured:** after a deliberate kill, the restarted container answered its metrics port 3 s
  after `docker compose up -d`, with fresh frames 0 s later
  ([failure modes](../architecture/16-failure-modes.md), capture SIGKILL row, 2026-08-26). Three
  in-process reconnects on 2026-11-25 left gaps of 2.1 s (Binance, its scheduled 23-hour
  reconnect), 9.7 s (Kraken) and 5.6 s (Coinbase) between the last trade frame of one connection
  and the first of the next (note §5).
- **Not measured:** a graceful recreate. Expected under about 10 s per venue.
- **What it loses:** Binance and Kraken trades printed in that window are gone (neither replays
  trades on subscribe), and silver records them, because trade ids are sequential per symbol on
  all three venues: the next trade carries `seq_gap` and `missing_before`, the count never
  received. Coinbase sends its recent trades on subscribe, which should refill its window; not
  verified (section 8). Each
  recreate also starts a new connection id, the key every book and sequence check is scoped to
  ([glossary](glossary.md#connection-and-conn_id)).

The rollout recreates each venue twice (section 8): once for the new image with books still on,
once for the switch.

### 3. Everything downstream of the book, and what changes

Full inventory, with every alert expression, in note §3. In short:

| Item | With no book traffic, if nothing else changes | Treatment |
|---|---|---|
| `CaptureFeedStale` | fires for `book`, `depth20`, `l2_data` 3 minutes after start | book streams not watched or seeded (section 1); rule unchanged |
| `CaptureBookDepthDegraded`, `CaptureChecksumFailure` | the first fires for all 34 symbols within 20 minutes (the depth gauge is seeded at 0); the second reads a reassuring 0 | series not seeded, and each rule gains `and on (exchange) k2_capture_book_capture == 1`, so it cannot fire for a venue that is off and needs no edit at resume |
| `CaptureSequenceGaps`, `CaptureResyncStorm` | cannot fire for Binance or Kraken (their gaps and resyncs come from the book); still meaningful for Coinbase, whose connection-wide gaps also count resyncs | leave unconditioned: conditioning them would silence Coinbase's trade-path gap detection |
| `LakeCommitAgeHigh` on the three bronze book tables | stays silent: stage 2b commits an [empty snapshot](glossary.md#empty-commit) to each every run, so the gauge it reads says "committed", not "has new data" | leave; documented, and P6 must not read it as freshness (section 5) |
| Every other capture, lake, ClickHouse and Airflow alert | unaffected (trades keep `ClickHouseGoldFeedStale` above zero) | leave |
| Ingest stage 2b | appends an empty snapshot to each bronze book table per run, as `bronze.kraken_instrument` already does most runs (verified) | no change |
| Ingest stage 2e | runs on empty input and commits empty snapshots | no change, and it must keep running: bronze keeps committing every run, so a silver book table that stopped reading would, after a week, be resuming from a bronze snapshot expiry has removed. First-run gate: this empty path has not run live |
| The eighteen nightly book audits | whole-table checks re-prove the same frozen rows every night; `bronze_schema_drift` files a pass having sampled no book frame | skipped while the venue is off (section 6) |
| `bronze_schema_drift` on any table whose stream had no frame in the sample | files `passed = true`, which the venue quality scorecard counts as a passing check of that day; `bronze.kraken_instrument` does this most nights today | files no row and logs why; `audit.checks.passed` is `NOT NULL`, so "not applicable" would be a schema change, and no row is what the governance view (`governance.quality_results.dq_results`, one Trino view unioning the lake's audit rows with every dbt test result, [ADR-046](ADR-046-governance-domain-and-quality-results.md)) already reads as "no result" |
| Compaction, snapshot expiry, orphan removal | none removes a kept row (constraints above); compaction rewrites files, never rows, and only partitions newer than 2 days | no change |
| `gold.dim_instrument.book_depth`, `gold.dim_venue` | keep claiming "the L2 subscription depth in force" for a subscription that is off | the venue dimension records the switch (section 4) |
| `scripts/health.sh` | the capture health check would go unhealthy (fixed by section 1); nothing compares file and process | new check: each capture's `k2_capture_book_capture` against the switch as both the host file and the Spark container's mounted copy read it, because a stale copy in Spark would mislead the audit skip and the `dim_venue` record |
| `scripts/lake-verify.sh`, `make gates` (every pre-merge gate in order: tests, the lake's exit criteria against the live stack, both parity checks, the alert tests, the doc gates) | pass on kept data | none |
| Release check (`.claude/skills/release-check/`) | its hot-tier smoke expects book rows from all three venues on a fresh clone | expects rows only for venues whose switch is on |
| `k2lake.trades(book=True)` and `completeness()` | the as-of join (each trade paired with the latest quote at or before it) has no age bound, so a trade in the pause is paired with a quote from before it, possibly days old, and counted as covered; Kraken's `checksum_failed` reads 0 for a window with no book frame | a quote older than a stated bound comes back NULL; `checksum_failed` NULL where no frame exists |
| Grafana "K2 Capture (v3)", book row | panels show no data | a panel shows `k2_capture_book_capture` per venue |
| Redpanda book topics, ClickHouse book feed and tables | idle; kept; `gold.bbo_live`'s newest second is the last one before the pause | keep; documented in the data catalog |

Two of these are bugs today, independent of the pause, and can land first on their own: the
as-of join with no age bound (a capture outage already pairs trades with stale quotes) and the
drift audit's pass on an empty sample (`bronze.kraken_instrument`, most nights).

### 4. The security master records the switch

`gold.dim_venue` and `gold.dim_instrument` are slowly changing dimensions of type 2: every change
to a tracked attribute closes the current row and opens a new one with its own validity interval,
so a query can ask what was configured at any past time
([glossary](glossary.md#scd2-security-master)). Stage 2d, the gold step of each ingest run (it
appends new trades to `gold.trades`, rebuilds the candles and bars they touch, and reloads the two
dimensions; [glossary](glossary.md#stage-2d)), reloads them from the registry every run.
Today `dim_instrument.book_depth` is documented as "the L2 subscription depth in force", which
would be false for the whole pause. Two ways to stay honest: owe
[ADR-030](ADR-030-scd2-security-master.md) an Outcome saying the dimensions describe configured
depth only, or record the switch where the dimension's own mechanism keeps its history. We record
it:

- `gold.dim_venue` gains `book_capture BOOLEAN`, nullable, tracked: the registry's value for the
  venue as of that version. NULL on versions recorded before the column existed, when every venue
  captured its book. Flipping the switch opens a new venue version, so the pause window per venue
  is in the dimension by mechanism, not by a hand-written note.
- `dim_instrument.book_depth`'s comment becomes "the configured L2 subscription depth; in force
  only while the venue's `dim_venue.book_capture` is true".
- It is a schema change, so it goes through the `/schema-change` procedure in its own pull request:
  `docker/lake/ddl/lake.sql`, the projection and tracked attributes in `docker/lake/gold.py`, the
  `dim_venue` contract, the schema chapter (`docs/architecture/13-schema-design.md`) and tests.
  `dim_venue` is not in ClickHouse or in an Avro schema.
- Two costs, stated: each version stores a change hash, `attr_hash`, a sha256 over its tracked
  attributes that the reload compares to decide whether anything changed; adding a tracked
  attribute changes every venue's hash, so each venue
  opens one new version when it ships, carrying `true`; and a version's `valid_from` is the first
  ingest run that read the new registry, not the moment the capture was recreated. The operator
  rows of section 6 carry the exact capture times; the difference is bounded by the rollout.

### 5. How a data product says it is paused

A data product is one owned unit of data, described by a descriptor and one contract per table
under `contracts/` ([glossary](glossary.md#data-domain-and-data-product)). The contracts follow
ODCS, the Open Data Contract Standard (v3.2.0), which allows custom properties. A product's
`lifecycle` (`draft`, `published`, `deprecated`) is maturity, and cannot say "paused", nor should
it: a paused product keeps the maturity it had, and `venue-frames` is paused for three of its seven
tables only. The pause is a fact about a table, so it lives on the table's contract, as one optional
K2 custom property in `contracts/schema/k2.schema.json`:

```yaml
  - property: paused
    value: {since: 2026-11-26, adr: ADR-050}
```

It goes on the nine book tables: per venue, its bronze and silver book tables
(`bronze.binance_depth20`, `bronze.kraken_book`, `bronze.coinbase_level2`, `silver.book_binance`,
`silver.book_kraken`, `silver.book_coinbase`); and the three shared gold tables `gold.book_state`,
`gold.book_top20`, `gold.bbo_1s`. Then k2gen, K2's contract tool
([glossary](glossary.md#k2gen-make-generate-and-the-drift-gate)): `k2gen validate` checks every
contract against the standard and the lake DDL, `make generate` writes the files the contracts
imply (product pages, catalog, Trino access rules, dbt sources), and CI fails if a generated file
differs from what it would write:

- `validate` checks the date and that the ADR file exists, and refuses a paused table that
  declares a `latency` target, because nothing may promise freshness for a table nothing feeds.
- `generate` writes a Status row on each product page ("paused since 2026-11-26, ADR-050: the
  data is readable, nothing new arrives") and a Status column in the product index and catalog:
  running, paused, or "3 of 7 tables paused".
- A test checks the marks against the switch **per venue**: a venue's bronze and silver book
  tables are marked exactly when its switch is `false`; the shared gold book tables exactly when
  all three are `false`.
- **For P6**, which will generate SLOs (service level objectives: a target such as "the newest
  row is under 15 minutes old", with an alert on breach) from the contracts: no SLO on a paused
  table, and any freshness rule reads the newest data time, not the last-commit gauge, which
  empty commits keep fresh.

### 6. Pausing the book audits, and recording the window

**The audits.** After a venue stops sending books, compaction still rewrites its newest two days of
book partitions for two nights; after that the book tables are frozen, and a nightly re-run of the
eighteen book audits proves again what it proved the night before, while filing pass rows that the
venue quality scorecard counts. So `maintenance.py` reads the switch from the registry (already
mounted in the Spark container at `/home/iceberg/config/instruments.yaml`) and skips a venue's book
audits while it is off, the shared gold ones while all three are off, logging each skip and filing
no row. A `--with-paused` flag runs them anyway: once by hand after compaction's two-night window
has closed, so the frozen tables are audited in their final form, and again in the resume
pre-flight.

**The window.** The history gap is recorded with the operator-row shape already used for the
2026-11-25 outage ([glossary](glossary.md#operator-row)): a row in `lake.audit.checks` with
`job = 'operator'`, the window in its scope, the seconds in `observed`, written with
`docker/lake/record_check.py`. The outage row is check `capture_outage`, scope
`stack/2026-11-25T22:33:43Z..2026-11-25T22:39:26Z`, observed 343, `passed = false`. For the
pause, per venue, check `book_capture_paused`, `passed = false` because the window holds no book
data:

- **At the pause:** scope `book/<venue>/<from>..`, open-ended, observed 0, where `<from>` is the
  receive time of the venue's last book frame.
- **At the resume:** a second row, same check, scope `book/<venue>/<from>..<to>`, observed the
  seconds between, where `<to>` is the first book frame after. `audit.checks` is append-only, so
  closing is a newer row with the full window; the newest row per venue is the record.

Operator rows are records, not checks: the governance view and the venue quality scorecard both
exclude them.

### 7. Switching back on

In order (commands in note §9):

1. **Restore first** any limit the trades-only retune lowered (memory, retention, per-run cap),
   in its own change, before a single book frame flows.
2. **Pre-flight:** record 20 s of one symbol per venue with books on, into a temporary registry
   copy, and replay it: no checksum failure on Kraken, no unknown or unparseable frame on any
   venue. That is the first live evidence of whether a venue's format drifted. Run the book audits
   once with `--with-paused` on the kept tables.
3. **Kraken first**, because its checksum is the strongest proof (a drifted format fails on the
   next update), then Coinbase, then Binance: one recreate each, then the checks below before the
   next.
4. Verify per venue: `k2_capture_book_capture` is 1; depth gauges above zero within a second; no
   capture alert, no sequence gap and no reconnect for 15 minutes; stage 2e writing rows again;
   on the next nightly run, `bronze_schema_drift` sampling book frames and `kraken_checksum`
   passing with new frames.
5. Remove the `paused` marks, regenerate, revert every pause note (`git grep -n ADR-050` lists
   them), file the closing operator rows, and append this ADR's Outcome with the window. The venue
   dimension opens its `book_capture = true` versions on its own.

The first day back starts every book from a fresh connection snapshot. The hole from pause to
resume is permanent: the venues do not replay their books, and the raw archive holds no book
frame for it.

### 8. Rollout

After P0 closes (it did on 2026-11-26, pull request #222) and the release gate run in progress has
finished. Three pull requests: the first adds the switch (all `true`, since the key is required),
the gauge, the Binance ping, the conditioned alerts, the audit skip, the honesty fixes and the
`paused` property support, so nothing on the stack changes; the second is the venue dimension's
schema change; the third flips the switch, marks the contracts and rewords the docs. On the live
stack: tag the running capture image for rollback, rebuild it (one heavy Docker job, alone),
recreate the three captures one at a time with books still on and verify, Binance's pongs
included; then the flip, one venue at a time, each followed by 15 minutes with no sequence gap and
no reconnect (longer than `CaptureSequenceGaps`'s 10-minute window plus its 5-minute `for`) and two
ingest runs of checks. The image gets its own step because it changes more than the switch: the
running captures report build `v3-phase-e-26-g9932f42` (2026-09-03), and seven commits have
touched the capture since, mostly dependency upgrades. Coinbase's flip also checks that its
subscribe resend refilled the trades its recreate missed (note §7 step 4). Then the first full trades-only UTC day is
measured, and the retune is its own reviewed change. Note §6 to §8 has the files, tests, commands
and the list of measurements.

---

## Rationale

- **The cost the owner named is downloading and storing,** and 95% of the downloaded bytes are
  book frames that also make up most of the raw archive. Only stopping the subscription removes
  them; anything downstream still downloads them.
- **One upstream control moves every layer.** With no book frame arriving, the topics, the lake
  steps and the hot tier follow without their own switch; the audits, the dimension, the alerts and
  the contracts read the same switch, so there is one place to flip.
- **A config switch, not code removal,** because the owner will switch back: a recreate per venue
  undoes it, and the code stays under test because decoding never reads the switch.
- **Required, and reported.** A default would turn a missing line into a silent "on"; a metric of
  what the process read is the only way to see a capture that was never recreated or a stale mount.
- **The ingest is left alone** because it already handles a run with no book frames the right
  way: stage 2b commits an [empty snapshot](glossary.md#empty-commit) per table, which keeps each
  step's resume point inside the 7-day expiry. A lake-side switch would add a second place to
  flip, and a resume step.
- **Fix the lies at their source.** The alerts that would fire, the audit that would pass on
  nothing and the helpers that would report stale quotes as live are corrected where the false
  signal starts, and the two book-only alerts read the switch, so nothing needs undoing at resume.

---

## Alternatives Considered

| Option | Why not |
|---|---|
| **Keep L2 on** | 95% of the bytes, ten times the lake space, the book stage and eighteen nightly audits, for a product no current work reads; against the owner's "lighter cleaner data for longer runs without downloading too much data" |
| **Thin slice: one symbol per venue on** | Keeps a live check of every book decoder for a few percent of the bytes, but every book stage, audit, alert and table keeps moving, which is the dev-cycle cost the owner wants gone, and Coinbase still sends a full-depth snapshot per subscribe. The owner chose "L2 fully off" |
| **One venue on, Kraken, for the live checksum** | Kraken's book is 99.6% of that venue's bytes and `bronze.kraken_book` is the largest book table (4.25 GB, or 3.96 GiB), so this keeps the largest share of the cost |
| **A daily canary: 20 s of each venue's book recorded and replayed into a scratch location** | Would show format drift within a day at almost no cost in bytes, but it is a scheduled live check, which the owner declined: "i intend on switching it back on at some point, but just while we dev this platform. lets turn it off and speed up the dev cycles". The same record and replay is the resume pre-flight instead (section 7), and the 2027-02-26 review bounds how long drift can go unseen |
| **Keep subscribing, drop book frames in the capture** | Still downloads the bytes, and drops frames already received, which breaks raw first ([ADR-021](ADR-021-raw-first-archive-and-lineage.md)) |
| **Stop at the lake: skip the book steps** | Saves lake compute only; Redpanda and the raw archive keep 90% of the bytes; and a step that stops while bronze keeps committing cannot resume after a week |
| **Delete the stored book data** | The owner chose to keep it all; the raw archive's book frames are the only copy, so their deletion could not be undone |
| **An environment variable per capture service** | Works the same for the capture, but three compose entries must agree, the lake and the tests cannot read it as one fact, and the registry already owns "what K2 subscribes to" |
| **`lifecycle: paused`, or the ODCS `status` field** | Lifecycle is maturity and would lose `draft`, and neither can say three of seven tables; ODCS `status` is a free string with no date or decision, set on no K2 contract today |
| **An ADR-030 Outcome instead of a `dim_venue` column** | No schema change, but the dimension would keep describing a subscription that is off, and the pause window would live only in prose; the dimension exists to answer "what was configured when" |

---

## Consequences

**Easier:** lighter data, estimated at about a twentieth of today's incoming bytes; shorter
ingest runs (by an unmeasured amount); shorter nightly maintenance once the book audits pause; the
raw topics' per-partition byte cap, which today decides how long an unread record survives on the
busiest Kraken partitions (2.24 GB on disk against 2 GiB, note §1.4), should stop binding, leaving
the 48 h and more slack before an outage ([raw retention](glossary.md#raw-retention-and-eviction))
loses data; fewer moving parts while the platform is developed; and a visible signal, the gauge,
when file and process disagree.

**Harder:** switching back is a procedure, not a flag (section 7). The book code changes only under
synthetic tests, which encode our reading of the venues' formats, not their current one. Binance's
liveness now rests on WebSocket pings, a new mechanism.

**Committed to:** the required `book_capture` key and the `k2_capture_book_capture` gauge; the
capture rules of section 1, including the Binance ping; the two conditioned alerts; the audit skip
and its `--with-paused` runs; `dim_venue.book_capture`; the `paused` contract property and its
k2gen checks; operator rows at pause and resume; P6 skipping paused tables and reading data time,
not commit time, for freshness; the retune as a separate reviewed change after one measured UTC
day; and stating the switch in the P7 release decision.

**Risks:** stage 2e's empty path has never run live; if it fails, stages 1 to 2d have already
committed, the run fails visibly (`AirflowTaskFailed`), and switching that venue back on restores
the old path. Binance might not answer client pings; the recreate with books still on is the gate
(its pong stream must stay fresh and its reconnect counter flat for 15 minutes), and the old image
is the rollback. A recreate during a venue incident costs more than a few seconds. A pause that
outlives its reason: the date review below is the guard.

**What is lost while paused, plainly:**

- No live proof that the book decoders match the venues: Kraken's checksum verifies nothing new,
  and no drift audit samples a book frame.
- The L2 products (`gold.book_top20`, `gold.bbo_1s`, the silver and bronze book tables) have a hole
  from pause to resume, and so does the raw archive: those frames were never received.
- Any research joining trades to the book (notebook 03, `k2lake.trades(book=True)`) gets no quote
  for trades in the pause.
- [ADR-028](ADR-028-non-goals-and-honest-limits.md) § 2's "nearest thing K2 offers" in place of
  L3 (depth to level 20 and per-second best bid and offer) is history, not a live product; § 3's,
  replay at any depth, still works over the kept archive, but only up to the pause.
- Docs and the README present the book as running: README (the "Complete" row and the diagram),
  architecture chapters 01, 04, 05, 06, 09 and 10, the data catalog, the capture README, the
  notebooks README and the capture checksum and sequence runbooks. Each gets one sentence and a
  link here, not a rewrite (note §6 lists them).

**What is not yet verified:**

- That stage 2e runs cleanly on empty input on the live stack: no book table shows an empty
  append in its retained history. The first run after each flip is the gate.
- That Binance answers a client Ping with a Pong; the protocol requires it, and step 2 of the
  rollout observes it.
- That Kraken sends heartbeats on a connection with only `instrument` and `trade`: the venue
  documents it; it has not been observed here.
- That Coinbase's sequence stays gapless with `level2` absent.
- That Coinbase's subscribe resend of recent trades refills every trade a gap or a recreate lost;
  the rollout checks it (note §7 step 4).
- The dark window of a graceful recreate; to be read from connection boundaries in the silver trade
  tables after rollout (note §5; Trino cannot read `bronze.binance_trade`, note §12).
- Whether trade capture got worse: the first paused day's trade-id gaps per venue against the days
  before (note §8).
- The byte and time savings: the 95% is a share of today's wire bytes; what Redpanda, the lake
  and the ingest actually save is the first trades-only day's measurement (note §8).

**Revisit when**, whichever comes first:

1. **P7's release tag**, `3.0.0`: by the owner's decision of 2026-11-26 the first tag is
   `3.0.0-alpha.1`, now, with one pre-release per phase and `3.0.0` at the P7 release gate. The
   release decision states whether L2 is on, and the release check runs with that value.
2. **2027-02-26**, 90 days after the pause: the pause is ended or restated with a new date,
   because format drift at the venues cannot be seen while paused.
3. **Any change, notebook or mart that needs book rows newer than the pause.**

### Outcomes owed when this is implemented

Appended, never editing the bodies: [ADR-019](ADR-019-rust-capture-tier.md) ("one connection per
exchange carrying trades and book" holds for trades only while paused);
[ADR-027](ADR-027-book-snapshot-and-sequencing.md) (the L2 product stops growing, and its revisit
triggers read counters that go silent); [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)
(two of its gold tables stop growing); [ADR-028](ADR-028-non-goals-and-honest-limits.md) (§ 2's
"nearest thing K2 offers" is not live, and § 3's trigger reads a gauge that goes silent); and
[ADR-018](ADR-018-v3-lake-first-rust-capture.md), still Proposed, the same way, for its Decision
§ 2. ADR-030 needs none: the dimension records the switch by its own mechanism (section 4). Nor do
ADR-021 (every received frame is still archived), ADR-029 (the replay fixtures stay hashed in CI)
or ADR-032 (an optional custom property is within its profile).

### Review changes (2026-11-26)

An independent review asked for thirteen changes and some wording. Each was checked against the
code; all are adopted, three with a correction:

- **The two book-only alerts are conditioned on the gauge, not all five.** `CaptureSequenceGaps`
  and `CaptureResyncStorm` also carry Coinbase's connection-wide gaps (`check_sequence` counts a gap
  and a resync and reconnects), which are trade-path signals; conditioning them would hide those.
- **The Binance ping applies to Binance only.** Kraken and Coinbase keep 60 s heartbeats, and a
  venue that did not answer pings would turn a watched `pong` into a reconnect every minute.
- **The Trino failure on `bronze.binance_trade` has two faces.** A query naming a field of `data`
  fails at analysis ("Ambiguous row field reference: t"); a query naming none still fails opening
  every file ("Index 7 out of bounds for length 5"). Both come from the struct's field names that
  differ only by case (`e`/`E`, `t`/`T`, `m`/`M`), which Trino matches without case. The table is
  unreadable through Trino; Spark reads it, and builds `silver.trades_binance` from it every run.
- **The book audits run once after compaction's two-night window**, not right after the flip, so
  the frozen tables are audited in their final form.
- **The P7 tag's name**, `3.0.0`, is the owner's decision of 2026-11-26: `3.0.0-alpha.1` now,
  one pre-release per phase, `3.0.0` at the P7 release gate.

---

## References

- [Research note](../research/2026-11-26-order-book-capture-pause.md): measurements, the full
  inventory, the implementation plan, rollout, measurement day, switch-back and rollback.
- [ADR-027](ADR-027-book-snapshot-and-sequencing.md): the L2 product and the per-venue sequencing.
- [ADR-030](ADR-030-scd2-security-master.md): the security master the switch is recorded in.
- [ADR-021](ADR-021-raw-first-archive-and-lineage.md): raw first.
- [Failure modes](../architecture/16-failure-modes.md): the measured capture restart.
- `services/capture-rust/src/main.rs` (`CONTINUOUS`, the sampler), `src/ws.rs` (ping and pong),
  `src/metrics.rs` (`zero`), `src/exchanges/` (subscriptions, Coinbase `check_sequence`);
  `docker/lake/books.py`, `bronze.py`, `gold.py`, `maintenance.py`.
