# ADR-020: Avro-only contracts: fixed-point int64 @1e-8, recv_ts in body

**Status:** Proposed
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Data contracts

---

## Context

v2 has two wire formats and consumes neither of them as designed.

- The Avro contract exists and is **unread**. `NormalizedTrade` is produced to
  `market.crypto.trades.<ex>` and registered in Redpanda's registry, but every
  ClickHouse Kafka engine table is `kafka_format = 'JSONAsString'` over the parallel
  `.raw` topics (`docker/clickhouse/ddl/01-k2-schema.sql:39`). The typed contract is
  paid for and unused; the untyped one is the real one.
- The Avro contract is also **broken**. `logicalType` sits as a sibling of `type` in
  `schemas/avro/normalized-trade.avsc:60`, where Avro silently ignores it. The schema
  parses, registers and serialises cleanly, and loses the type.
- **Numerics travel as strings.** Verified against the running v2 stack, 2026-08-26:

  ```console
  $ docker exec k2-redpanda rpk topic consume market.crypto.trades.binance \
      --num 1 --offset end --use-schema-registry=value
  "value": "{\"trade_id\":\"6621589505\",\"quantity\":\"0.00012000\",
             \"quote_volume\":\"9.4320528000000000\",\"price\":\"78600.44000000\", …}"
  ```

  That was the right call for v2 and is recorded as such in
  [`../architecture/schema-design.md`](../architecture/schema-design.md) ("Decimals as
  strings" — readable in Redpanda Console, diffable against the exchange REST API,
  and `toDecimal64(s, 8)` parses it directly). It stops being right when three
  runtimes with three different decimal implementations have to agree on the value.
- **No receive timestamp anywhere on the wire.** The only wall clock is taken after
  parse and normalisation (`.../TradeNormalizer.kt:28`), so exchange-clock skew and
  platform delay cannot be separated in any stored row.

v3 changes who reads these topics. ClickHouse 24.3 decodes them with `AvroConfluent`
(spike S4), Spark reads them by offset range into Iceberg, Rust produces them, and
DuckDB queries the result — and the Iceberg archive is never rewritten, so bytes
written today must still be readable by a reader written in 2028.

---

## Decision

**We will carry every v3 record as Avro registered under TopicNameStrategy with
global `BACKWARD_TRANSITIVE` compatibility, with prices and quantities as `int64`
scaled by 1e-8 and `recv_ts_ns` in the record body as well as a Kafka header, because
four runtimes have to decode the same bytes to the same value without negotiating,
and an integer is the only numeric type all four agree on exactly.**

Scope: all v3 topics (`market.crypto.v3.{raw,trades,book}.<ex>` — nine subjects). v2's
`NormalizedTrade` is untouched and stays live until the Kotlin handlers retire in
Phase C.

The mechanics — subject naming, the evolution rules, the worked arithmetic, the
registration commands — are in [`../../schemas/README.md`](../../schemas/README.md)
and in the `doc` string of every field. This ADR records the forks and what each one
cost.

---

## Rationale

**One format, no exceptions.** v2's "Avro for the normalized topic, JSON for the raw
topic" split meant the schema registry never became load-bearing: nothing broke when
the Avro schema was wrong, because nothing read it. A contract that is not on the
only path is not a contract. In v3 there is one path and Avro is on it, so a schema
mistake fails at `redpanda-init` instead of two years later in a notebook.

**Fixed-point `int64` at 1e-8, over Avro's `decimal` logical type.** `decimal` is the
textbook answer. It is `bytes` (or `fixed`) plus a schema-level scale, and it forces
every consumer to reconstruct a big-decimal from a byte array under its own precision
rules — three implementations, three sets of edge cases, and ClickHouse 24.3's
handling of it is something this project never verified. A plain `long` decodes to
`Int64` / `i64` / `LongType` identically and does exact integer arithmetic in all
three. The one thing `decimal` adds over a `long` is a scale that can vary per field,
which is a degree of freedom this system actively does not want.

**Fixed-point over strings.** Strings are what v2 does, and their virtue is real:
human-readable, exchange digits preserved verbatim. The cost is that arithmetic
requires a parse, every consumer picks its own parser, and the parse is where
precision quietly changes. Spike S1 made this concrete: Kraken's CRC32 book checksum
must be formatted from decimal strings or `i64` units and **never** from `f64`,
because an `f64` round-trip is lossy past 15 significant digits and would desync the
book silently *while the checksum reports success*
([ADR-018 Appendix A](ADR-018-v3-lake-first-rust-capture.md#s1--kraken-v2-book-checksum)).
The archive keeps the verbatim string anyway — `raw.messages` holds the original
frame bytes — so nothing is lost by typing the derived record.

**8 decimal places is measured, not assumed.** Spike S2 read Kraken's `instrument`
channel: `qty_precision 8`, `qty_increment 1e-08`, `price_precision 1` on BTC/USD
([ADR-018 Appendix A](ADR-018-v3-lake-first-rust-capture.md#s2--kraken-instrument-channel)).
8 dp is one satoshi and is the finest granularity any captured venue quotes. Range is
not the binding constraint: 45285.2 × 1e8 ≈ 4.5e12 against an `int64` ceiling of
~9.2e18, ~2 million × headroom.

**More than 8 dp is rejected and counted, never rounded.** A silently rounded price
is a wrong price that looks right forever and cannot be detected downstream; the
record is refused at capture and `k2_capture_precision_loss_total{exchange}`
increments, which is a metric an alert can watch. This is the whole reason the
scaling decision is safe to make: the assumption that no venue exceeds 8 dp is
*monitored*, not asserted. If a venue starts quoting 10 dp, a counter moves the same
day — see [`../runbooks/capture-checksum-failure.md`](../runbooks/capture-checksum-failure.md).

**`recv_ts_ns` in the body *and* as a Kafka header, body authoritative.** These serve
different readers and neither one covers both. The **body** is what reaches Iceberg —
a header is a Kafka construct and does not survive into a Parquet file, so a research
query six months from now can only see what the record carried. The **header** is
what a consumer can read *without deserialising the payload*: ClickHouse's
`_headers.name` / `_headers.value` virtual columns exist on 24.3 (verified in spike
S4, which read `hdr_names: ['recv_ts_ns']` off a live topic), so ingest lag is
computable at the queue table without an Avro decode per row. Duplication is
deliberate and bounded — 8 bytes — and the tie-break is written into the field's
`doc`: **the body wins**, because the body is the copy the archive keeps.

**`BACKWARD_TRANSITIVE`, globally, and it is not ceremonial.** The archive is never
rewritten. A reader in 2028 will meet version-1 bytes sitting next to version-9 bytes
in the same Iceberg table, resolved by schema id out of the registry. Plain
`BACKWARD` allows a chain of individually-safe changes to drift until the oldest
files are unreadable — one field dropped per version, nine versions apart. The price
is stated up front: **a field can never be removed and never renamed**, for the life
of the record type.

**A `v3.` segment in the topic names — a deviation from ADR-018.** ADR-018's Decision
speaks of `market.crypto.{raw,trades,book}.<ex>`. That first name is taken:
`market.crypto.trades.<ex>` is the *live v2 normalized topic*, and its `-value`
subject holds `NormalizedTrade`. Registering `trade.avsc` there returns
`{"is_compatible":false}` under `BACKWARD_TRANSITIVE` — verified against the running
registry, 2026-08-26 — which would fail `redpanda-init` and stop all three v2
containers at boot; a dry-run `alter-config` on the existing topic also silently
rewrote v2's retention. ADR-018's own parallel-run commitment needs two sets of
topics regardless. The prefix is applied to all nine uniformly rather than only to the
one that collides, so no operator has to remember which three are special. It comes
off at a v4 cutover if anyone wants it to.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Avro `decimal` logical type** (`bytes` + scale) | Self-describing, and pays for it in every consumer: big-decimal reconstruction under three different precision regimes, ClickHouse 24.3's handling unverified, and Spark's own `DecimalType` rules on top. Buys a per-field variable scale that a fixed 1e-8 contract has no use for. The trade-off is recorded rather than hidden: a reader that forgets the scale is off by 1e8, which is a loud failure, and `tests/test_contracts.py` pins every `_px`/`price`/`qty` field to `long` on the producer side. |
| **Keep decimal strings** (v2's contract) | Readable in Redpanda Console and diffable against the exchange REST API — genuinely useful, and why v2 chose it. But arithmetic needs a parse, each of Rust/ClickHouse/Spark parses differently, and S1 showed exactly how that ends: an `f64` in the checksum path desyncs the book while reporting success. `raw.messages` preserves the verbatim string, so readability is kept where it belongs — in the archive, not in the typed record. |
| **`double` on the wire** | Two decimal digits cheaper to write and wrong. `0.1 + 0.2` is not a price; `f64` is lossy past 15 significant digits; and it makes the replay determinism contract (Q1 of the requirements clarification) unenforceable, since float formatting is the classic source of non-reproducible output. |
| **JSON with a JSON Schema** | Keeps Redpanda Console readable and every payload greppable, which is the one thing this decision gives up. Loses the compact binary encoding on the highest-volume topic in the system (`raw.messages`, every frame), loses schema-id resolution for an immutable archive, and re-introduces per-consumer number parsing — the exact problem being solved. |
| **`recv_ts_ns` in the header only** | Cheapest, and it silently ends at the lake boundary: Kafka headers do not reach Parquet, so the one clock K2 controls would be absent from the system of record. Rejected outright. |
| **`recv_ts_ns` in the body only** | Correct, and costs an Avro decode per row to compute ingest lag in ClickHouse. 8 duplicated bytes is a better trade than a mandatory deserialise on the monitoring path. |
| **Reuse `market.crypto.*` topic names, cut over in place** | Fails at `redpanda-init` (`{"is_compatible":false}`, verified) and takes the v2 stack down with it, and there is nowhere to run the parallel comparison ADR-018 commits to. |

---

## Consequences

**Easier:** one format on one path, so a schema mistake fails at stack start instead
of in a notebook; exact arithmetic in Rust, ClickHouse and Spark with no conversion
layer; ingest lag computable in ClickHouse without deserialising; a 2028 reader can
resolve a 2026 schema id; and the >8 dp assumption is a counter rather than a belief.

**Harder:** **you can no longer read a topic with your eyes.** `rpk topic consume`
needs `--use-schema-registry=value`, Redpanda Console needs the registry reachable,
and a price on the wire reads as `4528520000000`. Debugging moves from "read the
JSON" to "decode Avro by schema id" — the cost ADR-018 named, made concrete here. The
registry becomes a hard dependency of the ingest path: if it is unreachable, capture
cannot encode. And the scale lives in documentation rather than in the type, so a
consumer that forgets it is off by 1e8.

**Committed to:** never removing and never renaming a field on `Trade`,
`BookSnapshotL2` or `RawMessage`, for the life of those record types — a reshape is a
new topic under a new prefix, not an evolution. The nine subjects, their evolution
rules and the pre-deploy compatibility check are in
[`../../schemas/README.md`](../../schemas/README.md); `tests/test_contracts.py` (41
structural tests) is the CI guard for sibling `logicalType`, non-`long` numerics, and
missing `doc` strings.

**Risks:** ClickHouse 24.3's `AvroConfluent` is proven for arrays, enums,
`timestamp-micros` and `_headers` (S4) and *not* proven for anything this contract
adds later. The `_headers` virtual column is a 24.x behaviour, not a guarantee — an
upgrade must re-verify it. `schema_registry_converter` 5.0 needs
`features = ["avro","easy"]`; the `["avro"]`-only form compiles and hides
`EasyAvroEncoder` (S3), which is a trap for the next person editing `Cargo.toml`. And
`BACKWARD_TRANSITIVE` means the first field name that ships wrong stays wrong
forever.

**On what this supersedes.** The v2 Avro contract was never recorded in an ADR — it
lives in `schemas/avro/normalized-trade.avsc` and in
[`../architecture/schema-design.md`](../architecture/schema-design.md). The nearest
ADR-level statement of the same rule is
[ADR-009](ADR-009-medallion-in-clickhouse.md)'s Raw layer, which specified
`price String -- String! Not cast to numeric yet` on precision-preservation grounds.
This ADR supersedes **that rule specifically**, in the same scoped way ADR-024
supersedes ADR-011 for the lake only; ADR-009's medallion decision is superseded
separately by ADR-025, and its status line is left for that ADR to write rather than
edited twice. Stated plainly because the alternative — implying an ADR existed for a
contract that only ever lived in a `.avsc` — would be the more comfortable and less
true version.

**Revisit when:** `k2_capture_precision_loss_total` is non-zero for any exchange (a
venue now quotes finer than 1e-8, and the scale is wrong), or a v3 record type needs
a field removed rather than added (`BACKWARD_TRANSITIVE` is costing more than it
buys), or ClickHouse moves off 24.x and `_headers` behaviour changes.

---

## Related

- [`../../schemas/README.md`](../../schemas/README.md) — the contract itself: subjects, evolution rules, worked arithmetic, registration commands
- [`../architecture/schema-design.md`](../architecture/schema-design.md) — v3 and v2 records side by side; "Decimals as strings" is the v2 rule this replaces
- [ADR-018](ADR-018-v3-lake-first-rust-capture.md) — the umbrella; Appendix A carries spikes S1 (checksum), S2 (precision from the feed), S3 (registry client) and S4 (`AvroConfluent` on 24.3), and its Deviations table records the `v3` prefix
- [ADR-019](ADR-019-rust-capture-tier.md) — the producer, and why `recv_ts_ns` is taken before parse
- [ADR-027](ADR-027-book-snapshot-and-sequencing.md) — why `BookSnapshotL2` carries parallel `int64` arrays and the hot tier widens them
- [ADR-009](ADR-009-medallion-in-clickhouse.md) — the string-numerics rule this supersedes (scoped; see Consequences)

---

## Outcome

_To be appended after the Phase C burn-in._

**Outcome so far (2026-08-26, Phase C day 1) — the message-size contract.** The
contracts say nothing about record size, and the first live day showed that
silence is itself a contract: librdkafka's default `message.max.bytes`
(1,000,000) and Redpanda's topic default (1,048,576) both sat under Coinbase's
5,195,904-byte `level2` snapshot (ADR-018 S5), so `raw-message.avsc` — the
system of record — silently lost the snapshot frame for the five largest
products on every reconnect (`produce_errors_total{reason="enqueue"}` = 5 per
connect, `MessageSizeTooLarge`). The fixed-point trade and book records were
never at risk (~100 B–1 KB); only the verbatim raw frame carries venue-sized
payloads. Fix: producer `message.max.bytes` and `market.crypto.v3.raw.*`
`max.message.bytes` are both 8 MiB, equal to the WebSocket cap, and a
4,803,578-byte snapshot has landed live (zstd -3 → 383,011 bytes). The size
ceiling is now an explicit part of the raw contract; a venue frame above 8 MiB
is a schema-change-sized event, not a config tweak.
