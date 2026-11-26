# ADR-050: Pause order-book capture while the platform is built

**Status:** Proposed
**Date:** 2026-11-26
**Author:** Platform
**Category:** Ingestion / Capture
**Decided by:** the owner on 2026-11-26 ([decision record](#the-owners-decision-2026-11-26)); this
ADR states how, and moves to Accepted after an independent review
**Plan and evidence:** [research note](../research/2026-11-26-order-book-capture-pause.md)
("note §n"), which carries every command behind the numbers below

---

## In plain words

Order-book data is about 95% of the bytes the three capture processes download, and the tables
built from it take ten times the lake space of the trade tables, yet nothing the platform build
is working on reads it. We will stop subscribing to it on every venue with a per-venue switch in
`config/instruments.yaml`, and keep every book row already stored, readable and unchanged. The
lake keeps running as it is; the capture must stop watching the streams it no longer asks for,
two notebook helpers and one audit must stop reporting silence as health, and the contracts mark
the book tables paused. The cost is a hole in book history from pause to resume, no live proof
that the book code still matches what the venues send, and a few seconds of trades per venue
each time a capture restarts. The pause itself is decided; what is left is the review of this
design and three open questions at the end.

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

Measured read-only on the live stack, 2026-11-26 between 01:40 and 01:56 UTC (note §1 has each
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
- **The hot tier** ([glossary](glossary.md#hot-tier)): ClickHouse's `gold.book_top20` is fed from
  the book topics.
- **Three notebooks** and the helpers in `notebooks/k2lake.py`, and the release check's smoke
  queries.
- **No mart reads a book table.** Two marts read audit rows, including the book audits'.

### The owner's decision (2026-11-26)

Asked and answered on 2026-11-26, quoted as given:

- **Scope:** "L2 fully off, switchable". In the owner's words: "to save data ... focus only on
  trades from the exchanges, and stop the l2 data for now. that way we can focus on data
  engineering with lighter cleaner data for longer runs without downloading too much data"; and
  "i intend on switching it back on at some point, but just while we dev this platform. lets
  turn it off and speed up the dev cycles".
- **Existing book data in the lake:** keep it all.
- **Timing:** after phase P0 ([glossary](glossary.md#p0-to-p7)) closes, on 2026-11-26.
- **Record:** a new ADR, independently reviewed before acceptance.
- **Limits and retention:** measure one full UTC day of trades-only data first, then retune in a
  separate reviewed change.
- **Book code upkeep while paused:** CI tests on synthetic fixtures; no scheduled live check.

### Constraints the design must respect

- **Raw first** ([ADR-021](ADR-021-raw-first-archive-and-lineage.md)): every frame received is
  archived. Not asking for a frame is allowed; receiving one and dropping it is not.
- **Snapshot expiry** ([glossary](glossary.md#snapshot-expiry-and-the-retention-floor)): each lake
  step resumes from the parent [snapshot](glossary.md#snapshot) it last read, and snapshots older
  than 7 days are expired. A step that stops committing for more than a week cannot resume.
- **Silence is not health.** A check that passes because nothing arrived must say so, or a paused
  product reads as a healthy one.
- **No Spark in CI.** The lake's Spark code is tested through pure functions and fakes; anything
  that needs a Spark session is verified on the live stack.

---

## Decision

**We will stop subscribing to order-book channels on all three venues until the platform build is
declared done, through a per-venue switch in `config/instruments.yaml` that each capture reads at
start, and keep every book row already stored, because book frames are 95% of the bytes the
captures download and nothing the build is working on reads them.**

Scope: what the captures subscribe to and watch, and the honesty of what reads the book tables.
Not in scope: deleting anything, removing topics or tables, changing what the lake jobs compute
or write (one audit's report wording aside), resource limits and retention (the separate retune
after one measured day).

### 1. The switch

**What exists today:** nothing. The registry has `version`, `instruments` and an optional
per-instrument `book_depth`; the capture's only book setting is `K2_SNAPSHOT_INTERVAL_MS`. The
capture parses the registry with serde, which ignores keys it does not know, so today's binary
cannot see a new key and the config can never switch a build that predates the code.

**What is added:**

```yaml
# Order-book (L2) capture per venue. false: the capture subscribes to trades only (ADR-050).
book_capture:
  binance: false
  kraken: false
  coinbase: false
```

The section absent means every venue on, today's behaviour. A key naming no venue stops the
capture at start. Values are YAML booleans, never `on` or `off`: PyYAML reads those as booleans
and serde_yaml as strings, so the lake and the capture would disagree. Per venue, not one global
value, because switching back is done one venue at a time (section 6).

**What a capture with `false` does** ([glossary](glossary.md#book-switch)):

1. It does not subscribe to the book channel (third column of the table above). Kraken keeps
   `instrument`, which the security master and the book replay's checksum read. Coinbase keeps
   `heartbeats`, which keep its connection-wide sequence number moving, so a lost trade frame is
   still detected as a sequence gap.
2. It does not watch the book stream. Each capture judges its **continuous streams**, the ones
   that must keep delivering, against a silence bound: 60 s for book and heartbeat channels,
   300 s for trade channels. One table in `main.rs` drives three things: the **session
   watchdog**, which recycles the connection when a stream passes its bound; the container
   health check; and the `CaptureFeedStale` alert ([glossary](glossary.md#continuous-stream-and-session-watchdog)).
   Left in, an unsubscribed book stream would pass its 60 s bound on every connection: the
   watchdog would reconnect every minute, losing trades each time, and the container would go
   unhealthy. With books off, Binance keeps only `trade`, so a silently dead Binance connection
   is noticed after 300 s instead of 60 s; Kraken and Coinbase keep a 60 s heartbeat.
3. It does not seed the book series. The capture creates some metric series at zero when it
   starts, so that an alert can fire on a feed that never delivered
   ([glossary](glossary.md#seeded-series)). For a venue that is not asked for its book, the book
   depth gauge, the Kraken checksum counters and the book streams' message counters are not
   created, exactly as Binance and Coinbase never get checksum counters today.
4. It skips the once-a-second book sampling: no snapshot records, no depth or level gauges.
5. It decodes every frame exactly as before. `handle_frame`, `replay` and `record` do not read
   the switch, so replaying archived book frames, and the synthetic fixture tests whose output
   hashes are committed, give identical bytes whatever the switch says. That is what keeps the
   paused code tested, as the owner chose.

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
  ([failure modes](../architecture/16-failure-modes.md), capture SIGKILL row, 2026-08-26). Two
  in-process reconnects on 2026-11-25 left gaps of 9.7 s (Kraken) and 5.6 s (Coinbase) between the
  last trade frame of one connection and the first of the next (note §5).
- **Not measured:** a graceful recreate. Expected under about 10 s per venue.
- **What it loses:** Binance and Kraken trades printed in that window are gone (neither replays
  trades on subscribe), and silver records them, because trade ids are sequential per symbol on
  all three venues: the next trade carries `seq_gap` and `missing_before`, the count never
  received. Coinbase sends its recent trades on subscribe, so its window is refilled. Each
  recreate also starts a new connection id, the key every book and sequence check is scoped to
  ([glossary](glossary.md#connection-and-conn_id)).

The rollout recreates each venue twice (section 7): once for the new image with books still on,
once for the switch.

### 3. Everything downstream of the book, and what changes

Full inventory, with every alert expression, in note §3. In short:

| Item | With no book traffic, if nothing else changes | Treatment |
|---|---|---|
| `CaptureFeedStale` (capture) | fires for `book`, `depth20`, `l2_data` 3 minutes after start | silenced at the source: book streams not watched or seeded (section 1); rule unchanged |
| `CaptureBookDepthDegraded` | fires for all 34 symbols within 20 minutes: the depth gauge is seeded at 0 | silenced at the source: gauge not seeded; rule unchanged |
| `CaptureChecksumFailure`, `CaptureResyncStorm` | cannot fire; Kraken's checksum series reads a reassuring 0 | checksum series not seeded; rules unchanged |
| `CaptureSequenceGaps` | Binance: only its book stream carries a sequence, so it means nothing; Coinbase: still meaningful | leave |
| `LakeCommitAgeHigh` on the three bronze book tables | stays silent: stage 2b commits an [empty snapshot](glossary.md#empty-commit) to each every run, so the gauge it reads says "committed", not "has new data" | leave; documented, and P6 must not read it as freshness (section 4) |
| Every other capture, lake, ClickHouse and Airflow alert | unaffected (trades keep `ClickHouseGoldFeedStale` above zero) | leave |
| Ingest stage 2b | appends an empty snapshot to each bronze book table per run, as `bronze.kraken_instrument` already does most runs (verified) | no change |
| Ingest stage 2e | runs on empty input and commits empty snapshots | no change, and it must not be skipped: a skipped table's resume point expires after 7 days. First-run gate: this empty path has not run live |
| Nightly audits on book tables | whole-table checks keep passing on the kept rows; `bronze_schema_drift` passes having sampled no book frame | the drift audit states how many frames of its stream it sampled; a sample of none says so |
| Compaction, snapshot expiry, orphan removal | none removes a row: compaction rewrites files, expiry drops only snapshots older than 7 days (keeping 10) and never a file the current snapshot holds, orphan removal only unreferenced files older than 24 h | no change; kept data stays |
| `scripts/health.sh`, `scripts/lake-verify.sh`, `make gates` | health goes red through the capture health check; the rest pass on kept data | fixed by section 1 |
| Release check (`.claude/skills/release-check/`) | its hot-tier smoke expects book rows from all three venues on a fresh clone | expects none while the shipped switch is off |
| `k2lake.trades(book=True)` and `completeness()` | the as-of join has no age bound: a trade in the pause is paired with the last quote before it, possibly days old, and counted as covered; Kraken's `checksum_failed` reads 0 for a window with no book frames | quote older than a stated bound comes back NULL; `checksum_failed` NULL where no frame exists |
| Grafana "K2 Capture (v3)", book row | panels show no data | leave; the row says paused |
| Redpanda book topics, ClickHouse book feed and tables | idle; kept; `gold.bbo_live`'s newest second is the last one before the pause | keep; documented in the data catalog |

### 4. How a product says it is paused

A product descriptor's `lifecycle` (`draft`, `published`, `deprecated`) is maturity, and cannot
say "paused", nor should it: a paused product keeps the maturity it had, and `venue-frames` is
paused for three of its seven tables only. The pause is a fact about a table, so it lives on the
table's contract, as one optional K2 custom property in `contracts/schema/k2.schema.json`:

```yaml
  - property: paused
    value: {since: 2026-11-26, adr: ADR-050}
```

It goes on the nine book tables: the three bronze book tables, `silver.book_binance`,
`silver.book_kraken`, `silver.book_coinbase`, `gold.book_state`, `gold.book_top20`,
`gold.bbo_1s`. Then [k2gen](glossary.md#k2gen-make-generate-and-the-drift-gate):

- `validate` checks the date and that the ADR file exists, and refuses a paused table that
  declares a `latency` target, because nothing may promise freshness for a table nothing feeds.
- `generate` writes a Status row on each product page ("paused since 2026-11-26, ADR-050: the
  data is readable, nothing new arrives") and a Status column in the product index and catalog:
  running, paused, or "3 of 7 tables paused".
- A test asserts the marks agree with the switch: every venue off means all nine marked, every
  venue on means none.
- **For P6** (SLOs, generated from contracts): no SLO on a paused table, and any freshness rule
  reads the newest data time, not the last-commit gauge, which empty commits keep fresh.

### 5. Recording the window

The book history gets a hole, and it is recorded where the lake records such things: one
[operator row](glossary.md#operator-row) per venue in `audit.checks`, through the existing
`record_check.py --job operator`. At the pause: check `book_capture_paused`, scope
`market.crypto.v3.book.<venue>`, detail `from <recv_ts of the venue's last book frame>: ADR-050`.
At the resume: check `book_capture_resumed`, detail `from <...> to <first book frame after>`,
observed the seconds paused. Operator rows are records, not checks; the venue quality mart
already ignores them.

### 6. Switching back on

In order (commands in note §9):

1. **Restore first** any limit the trades-only retune lowered (memory, retention, per-run cap),
   in its own change, before a single book frame flows.
2. **Pre-flight:** record 20 s of one symbol per venue with books on, into a temporary registry
   copy, and replay it: no checksum failure on Kraken, no unknown or unparseable frame on any
   venue. That is the first live evidence of whether a venue's format drifted.
3. **Kraken first**, because its checksum is the strongest proof (a drifted format fails on the
   next update), then Coinbase, then Binance: one recreate each, then two ingest runs and the
   checks below before the next.
4. Verify per venue: depth gauges above zero within a second, no capture alert, stage 2e writing
   rows again on the next run; on the next nightly run `bronze_schema_drift` sampling book frames
   and `kraken_checksum` passing with new frames.
5. Remove the `paused` marks, regenerate, revert every pause note (`git grep -n ADR-050` lists
   them), file the resume rows, and append this ADR's Outcome with the window.

The first day back starts every book from a fresh connection snapshot. The hole from pause to
resume is permanent: the venues do not replay their books, and the raw archive holds no book
frame for it.

### 7. Rollout

After P0 closes and the release gate run in progress has finished. Two pull requests: the first
adds the switch, the honesty fixes and the `paused` property support with the switch absent, so
nothing on the stack changes; the second flips the switch, marks the contracts and rewords the
docs. On the live stack: tag the running capture image for rollback, rebuild it (one heavy Docker
job, alone), recreate the three captures one at a time with books still on and verify; then the
flip, one venue at a time, each followed by two ingest runs of checks. The image gets its own
step because it changes more than the switch: the running captures report build
`v3-phase-e-26-g9932f42` (2026-09-03), and seven commits have touched the capture since, mostly
dependency upgrades. Then the first full trades-only UTC day is measured, and the retune is its
own reviewed change. Note §6 to §8 has the files, tests, commands and the list of measurements.

---

## Rationale

- **The cost the owner named is downloading and storing,** and 95% of the downloaded bytes are
  book frames that also make up most of the raw archive. Only stopping the subscription removes
  them; anything downstream still downloads them.
- **One upstream control moves every layer.** With no book frame arriving, the topics, the lake
  steps, the hot tier and the audits all follow without their own switch.
- **A config switch, not code removal,** because the owner will switch back: a recreate per venue
  undoes it, and the code stays under test because decoding never reads the switch.
- **The ingest is left alone** because it already handles a run with no book frames the right
  way: stage 2b commits an [empty snapshot](glossary.md#empty-commit) per table, which keeps each
  step's resume point inside the 7-day expiry. A lake-side switch would add a second place to
  flip, and a resume step.
- **Fix the lies at their source.** The two alerts that would fire and the helpers that would
  report stale quotes as live are corrected where the false signal starts, so no alert rule needs
  a condition that has to be removed again on resume.

---

## Alternatives Considered

| Option | Why not |
|---|---|
| **Keep L2 on** | 95% of the bytes, ten times the lake space, the book stage and eighteen nightly audits, for a product no current work reads; against the owner's "lighter cleaner data for longer runs without downloading too much data" |
| **Thin slice: one symbol per venue on** | Keeps a live check of every book decoder for a few percent of the bytes, but every book stage, audit, alert and table keeps moving, which is the dev-cycle cost the owner wants gone, and Coinbase still sends a full-depth snapshot per subscribe. The owner chose "L2 fully off" |
| **One venue on, Kraken, for the live checksum** | Kraken's book is 99.6% of that venue's bytes and `bronze.kraken_book` is the largest book table (4.25 GB), so this keeps the largest share of the cost |
| **Keep subscribing, drop book frames in the capture** | Still downloads the bytes, and drops frames already received, which breaks raw first ([ADR-021](ADR-021-raw-first-archive-and-lineage.md)) |
| **Stop at the lake: skip the book steps** | Saves lake compute only; Redpanda and the raw archive keep 90% of the bytes; and a step that stops committing for 7 days cannot resume after expiry |
| **Delete the stored book data** | The owner chose to keep it all; the raw archive's book frames are the only copy, so their deletion could not be undone |
| **An environment variable per capture service** | Works the same, but three compose entries must agree, tests cannot read it as one fact, and the registry already owns "what K2 subscribes to" |
| **`lifecycle: paused`, or the ODCS `status` field** | Lifecycle is maturity and would lose `draft`, and neither can say three of seven tables; ODCS `status` is a free string with no date or decision, set on no K2 contract today |

---

## Consequences

**Easier:** lighter data, estimated at about a twentieth of today's incoming bytes; shorter
ingest runs (by an unmeasured amount); the raw topics' per-partition byte cap, which today decides
how long an unread record survives on the busiest Kraken partitions (2.24 GB on disk against
2 GiB, note §1.4), should stop binding, leaving the 48 h and more slack before an outage
([raw retention](glossary.md#raw-retention-and-eviction)) loses data; fewer moving parts while
the platform is developed.

**Harder:** switching back is a procedure, not a flag (section 6). Two alerts are silenced by
absence, so their silence during the pause proves nothing. Binance's dead-connection detection
slows from 60 s to 300 s. The book code changes only under synthetic tests, which encode our
reading of the venues' formats, not their current one.

**Committed to:** the `book_capture` key, absent meaning on; the capture rules of section 1; the
`paused` contract property and its k2gen checks; operator rows at pause and resume; P6 skipping
paused tables and reading data time, not commit time, for freshness; the retune as a separate
reviewed change after one measured UTC day; and stating the switch in the P7 release decision.

**Risks:** stage 2e's empty path has never run live; if it fails, stages 1 to 2d have already
committed, the run fails visibly, and switching that venue back on restores the old path. A
recreate during a venue incident costs more than a few seconds. A pause that outlives its
reason: the date trigger below is the guard.

**What is lost while paused, plainly:**

- No live proof that the book decoders match the venues: Kraken's checksum verifies nothing new,
  and the drift audit samples no book frame.
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
- That Kraken sends heartbeats on a connection with only `instrument` and `trade`: the venue
  documents it; it has not been observed here.
- That Coinbase's sequence stays gapless with `level2` absent.
- The dark window of a graceful recreate; to be read from connection boundaries in the bronze
  trade tables after rollout (note §5).
- The byte and time savings: the 95% is a share of today's wire bytes; what Redpanda, the lake
  and the ingest actually save is the first trades-only day's measurement (note §8).
- The nightly cost of the book audits over the kept tables, which does not fall with the pause.

**Revisit when**, whichever comes first:

1. **P7's release gate** (the tag that closes the governed platform plan): the release decision
   states whether L2 is on, and the release check runs with that value.
2. **Any change, notebook or mart that needs book rows newer than the pause.**
3. **2027-02-26**, 90 days after the pause, when the pause is either ended or restated with a new
   date, because format drift grows with time and a pause should not become permanent by default.

### Outcomes owed when this is implemented

Appended, never editing the bodies: [ADR-019](ADR-019-rust-capture-tier.md) ("one connection per
exchange carrying trades and book" holds for trades only while paused);
[ADR-027](ADR-027-book-snapshot-and-sequencing.md) (the L2 product stops growing, and its revisit
triggers read counters that go silent); [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)
(two of its gold tables stop growing); [ADR-028](ADR-028-non-goals-and-honest-limits.md) (§ 2's
"nearest thing K2 offers" is not live, and § 3's trigger reads a gauge that goes silent); and
[ADR-018](ADR-018-v3-lake-first-rust-capture.md), still Proposed, the same way, for its Decision
§ 2. No Outcome is needed on ADR-021 (every received frame is still archived), ADR-029 (the
replay fixtures stay hashed in CI), ADR-030 (the `instrument` channel stays) or ADR-032 (an
optional custom property is within its profile).

### Open questions for the owner

1. The 90-day date in the revisit trigger: keep, change, or drop.
2. The shipped default: the switch lives in the checked-in registry, so a fresh clone of the
   public repository captures trades only while this holds. The alternative is a local override,
   which would make the live stack and the repository disagree.
3. The nightly book audits over kept tables: keep them (they prove kept data unchanged) or skip
   them while paused; decide in the retune with their measured time.

---

## References

- [Research note](../research/2026-11-26-order-book-capture-pause.md): measurements, the full
  inventory, the implementation plan, rollout, measurement day, switch-back and rollback.
- [ADR-027](ADR-027-book-snapshot-and-sequencing.md): the L2 product and the per-venue sequencing.
- [ADR-021](ADR-021-raw-first-archive-and-lineage.md): raw first.
- [Failure modes](../architecture/16-failure-modes.md): the measured capture restart.
- `services/capture-rust/src/main.rs` (`CONTINUOUS`, the sampler), `src/metrics.rs` (`zero`),
  `src/exchanges/` (subscriptions); `docker/lake/books.py`, `bronze.py`, `maintenance.py`.
