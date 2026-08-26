# ADR-021: Raw-first archive with per-record lineage

**Status:** Proposed
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Storage

---

## Context

[ADR-018](ADR-018-v3-lake-first-rust-capture.md) inverts v2's storage hierarchy and
says the lake becomes the system of record. Phase D is where that sentence has to
become a table definition, and three questions have to be answered before any DDL is
written, because each one is expensive to reverse once bytes exist.

**What is durably kept, and for how long.** v2 answered this by accident. The archive
was whatever the JDBC offload could read out of ClickHouse above a watermark
(`docker/offload/offload_generic.py:172`), which meant it inherited ClickHouse's
7-day Bronze TTL, its normalisation, and the driver's type limitations — `silver_trades`'
`trade_conditions Array(String)` and `vendor_data Map(String,String)` are dropped at
that boundary and are absent from `cold.silver_trades` today
([`../architecture/README.md`](../architecture/README.md), *Cold store*). Nothing in
v2 durably holds what an exchange actually sent.

**How a derived row is traced back to the frame that produced it.** v2 has no answer at
all. `bronze_trades_*` carry `kafka_offset` and `kafka_partition`, but the topic name is
implied by the table name and there is no record of the frame those columns came from —
the frame was never stored.

**Whether L2 deltas get a table of their own.** [ADR-027](ADR-027-book-snapshot-and-sequencing.md)
makes a top-20 snapshot at 1 Hz the queryable book product and keeps every delta as a
verbatim frame. Phase D has to either honour that or quietly re-add a `book_deltas`
table, and the second option is attractive precisely because it looks cheap.

The constraint that shapes all three: one host, and the capacity model predicts
`raw.messages` at **6.47 GB/day** after zstd-3 — disk binds first, *on a calendar*, at
roughly **26 days** from a cold start
([`../architecture/capacity-model.md`](../architecture/capacity-model.md#7-bottleneck-prediction)).

Free space is the one input to that arithmetic that moves while you read it, so it is
quoted here with the command and the instant rather than as a constant:

```console
$ df -h /            # 2026-08-26T14:43Z
Filesystem      Size  Used Avail Use% Mounted on
/dev/nvme0n1p5  961G  715G  197G  79% /
```

`(197 GiB − 34.6 GB of Redpanda retention) ÷ 6.89 GB/day` is 25.7 days. The capacity
model derives the same ~26 days from a 212 GiB reading taken earlier the same day; the
answer is insensitive to the difference, and the 15 GiB that went missing between the two
readings is the alert's whole reason for existing.
That prediction is not a reason to bound the archive; it is the honest cost of not
bounding it, and [Q8 of the requirements clarification](../research/2026-08-26-v3-requirements-clarification.md#q8--raw-archive-retention-on-a-single-host)
settled the trade the other way.

---

## Decision

**We will make `raw.messages` — the exchange frame stored byte-for-byte, with the
offsets it arrived on — the system of record, kept forever with no TTL and no
expiry; make every `bronze.*` table a pure function of it, rebuildable by re-running
the decode; carry `src_topic` / `src_partition` / `src_offset` on every bronze row so a
derived value traces to one frame; and add no `book_deltas` table, because
`raw.messages` already holds every delta.**

Scope: the Iceberg lake tables in `docker/lake/ddl/lake.sql`. It does not bound the
retention of the Redpanda topics (48 h raw, 7 d derived — `docker/redpanda/init.sh`) or
of the ClickHouse hot tier (7 d — [ADR-025](ADR-025-clickhouse-derived-hot-tier.md));
both of those are caches in front of the archive and are meant to expire.

Retention is a *platform* commitment with an *operator* escape hatch: disk expansion is
an operator action, taken against an 80 % disk alert and its runbook
([`../runbooks/lake-disk-usage-high.md`](../runbooks/lake-disk-usage-high.md)). No
process on this platform deletes a row of `raw.messages`.

---

## Rationale

**The archive is verbatim because normalisation is a decision, and decisions are
revisited.** Every transform between the socket and the disk is a claim about what the
bytes mean, and every such claim has been wrong at least once on this platform — v2's
Kraken trade ID (`"KRAKEN-${timestampMs}-${pair.hashCode()}"`), its missing receive
timestamp, its silently-ignored `logicalType`. A verbatim payload column is the only
representation that survives being wrong about all of them, because the fix is a re-run
rather than a re-ingest, and re-ingest is not available: public WebSocket feeds do not
replay. `RawMessage.payload` is `bytes` and is never re-serialised or compressed
in-field ([`../../schemas/avro/raw-message.avsc`](../../schemas/avro/raw-message.avsc));
Parquet's zstd block codec is the only thing applied to it, which is exactly why the
capacity model flags its compression ratio (G3) as the prediction most likely to miss.

**Control frames are archived, not dropped.** Heartbeats, `subscriptions`
acknowledgements and error envelopes are kept with `symbol = null`, and the field's
`doc` fixes the meaning: `null` is "not attributable to a single instrument", never
"unknown". The frames that explain *why a symbol went quiet* are worth more per byte
than the frames that say it did not.

**Lineage is three columns, not a lineage system.** `(src_topic, src_partition,
src_offset)` on a bronze row is the coordinate of exactly one record in Redpanda, and —
because that same record was written verbatim into `raw.messages` with the same
coordinate — of exactly one row in the archive. That makes three things possible with a
join rather than an investigation: prove `count(bronze.trades) == count(raw.messages
where stream='trade')` for a window; find the frame behind a suspicious price; and diff
a rebuilt bronze against the live one row for row. Iceberg's own snapshot lineage
(`k2.src-snapshot-id` on the bronze commit, [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md))
answers *which batch*; these three columns answer *which frame*, and both questions get
asked.

**No `book_deltas` table, and this is the decision most likely to be re-litigated.** A
delta table is the obvious thing to build and it is a second copy of data the archive
already holds, in a shape that only helps a consumer willing to implement book
reconstruction in SQL. The volume is not marginal: spike S5 measured Coinbase at 527
`l2_data` frames against 133 `market_trades` in the same window, and the BTC-USD opening
snapshot alone at 5,195,904 bytes across 43,974 levels
([ADR-018 Appendix A](ADR-018-v3-lake-first-rust-capture.md#s5--coinbase-level2-without-jwt)).
Full depth at full rate as a *queryable* product is not a single-host proposition, and
the thing that makes skipping it safe rather than lossy is that the deltas are on disk
regardless — recoverable by pushing archived frames back through the same
`handle_frame` the live path runs ([Q1](../research/2026-08-26-v3-requirements-clarification.md#q1--replay-what-is-it-for-and-who-owns-the-parser)).
Replay is a batch job, not a query. That is the cost, and it is stated rather than
hidden.

**`raw.messages` is frozen; `bronze.*` may add nullable columns.** The wire contract
`schemas/avro/raw-message.avsc` is seven fields and will not gain an eighth — the lake
table wraps them in nine columns, adding the Kafka coordinates (`topic`, `partition`,
`offset`, `kafka_ts`) and the derived `schema_id`, and dropping nothing. Any field added
to either is a field the
2026 rows do not have, which turns "the archive is the record" into "the archive is the
record plus a migration note". Derived tables are the place for evolution, because they
are rebuildable — a new bronze column is a re-run, not a rewrite. Exchange-specific
extras land in a vendor `map<string,string>` rather than as new top-level columns, so
adding a venue never touches the shared schema.

**The single-host disk limit, stated rather than implied.** Keeping forever on one host
is a promise this host cannot keep indefinitely, and Q8 requires that be said out loud
rather than left to the capacity model. At the predicted 6.47 GB/day, the current
disk holds roughly 26 days from empty. The mitigations are: add disk, or move object
storage to S3 with a Glacier/Deep Archive lifecycle — which is what makes "keep forever"
viable past one disk and is designed in
[`../architecture/scale-out-path.md`](../architecture/scale-out-path.md). Neither is a
TTL, and neither happens automatically.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **30-day or 7-day TTL on `raw.messages`** | Bounds the disk problem and unbounds a worse one: the replay window becomes the TTL, so `bronze.*` cannot be rebuilt past it and the archive stops being the system of record for anything older. The platform's one distinguishing property would hold for a month. Rejected in [Q8](../research/2026-08-26-v3-requirements-clarification.md#q8--raw-archive-retention-on-a-single-host); the disk alert plus an operator decision replaces it. |
| **Parse at capture and store only typed rows** (no verbatim payload) | ~5× cheaper on disk — raw is 91 % of the predicted bytes ([capacity model §4b](../architecture/capacity-model.md#4b-per-topic-per-day)) — and it is v2's mistake with a faster parser. Every parse bug becomes permanent, every schema question becomes unanswerable, and replay-through-the-production-parser (Q1) has nothing to replay. |
| **A `bronze.book_deltas` table alongside the snapshots** | Storage and rebuild cost far beyond one host (S5: 5.2 MB for one opening snapshot, before deltas, before 34 instruments), and it pushes book reconstruction into every consumer — a second implementation of `book.rs` in SQL, which is the research/production drift Q1 rejected. Recoverable by replay, so this is deferred rather than lost. |
| **Lineage by content hash** of the payload instead of offsets | Survives a topic being recreated, which offsets do not. Costs a hash per record on the capture path, is not a coordinate you can seek to, and answers "is this the same bytes" rather than "where did this come from" — the second question is the one asked during an incident. |
| **A separate lineage/provenance table** joining bronze rows to raw rows | A join table for a relationship that is already a three-column key, plus a second thing that can be inconsistent with the tables it describes. The columns cost ~20 bytes per bronze row and cannot drift. |
| **Compress the payload in-field** (per-record zstd) before writing | Beats Parquet's block codec on paper and breaks the one property the column has: `payload` stops being the frame and becomes a thing you decode before you can grep it. The archive's value is that a 2028 reader needs nothing but Parquet. |

---

## Consequences

**Easier:** rebuilding any derived table from the archive and diffing it against the
live one; explaining a number six months later by reading the frame behind it;
answering "did we ever receive X" without arguing about it; adding an exchange without
a schema migration (vendor `map`); and replaying production parser changes over real
history rather than over fixtures.

**Harder — and the number that makes it concrete: disk.** `raw.messages` is predicted at
6.47 GB/day forever, 91 % of the platform's byte growth, and nothing deletes it. On the
current host that is ~26 days from empty
([capacity model §7](../architecture/capacity-model.md#7-bottleneck-prediction)). The
platform's binding constraint moves from CPU (v2's) to bytes, and it binds on a calendar
rather than at a load multiple — which means it cannot be outrun by tuning, only by
buying disk or moving to object storage that is priced for it. Also harder: any question
about sub-second book state or depth past level 20 is a batch replay rather than a
query, and a number derived that way carries a different provenance from a number
queried off `bronze.book_snapshots_l2`.

**Committed to:** never adding a field to `RawMessage` — a reshape is a new record type
under a new topic prefix, not an evolution ([ADR-020](ADR-020-avro-fixed-point-contracts.md)
already commits to never removing or renaming one); never expiring a `raw.messages` row
by policy; `(src_topic, src_partition, src_offset)` on every bronze row for the life of
those tables; and an 80 % disk alert with a runbook that offers exactly two options — add
disk, or move to object storage — and never a TTL.

**Risks:** the zstd ratio on an opaque `bytes` column is a guess (G3, 0.20) and it drives
both the storage line and the compaction cadence; if it lands at 0.40 the 26-day
prediction halves. Offsets are stable only while a topic exists — recreating a v3 topic
resets the coordinate space, so lineage is unique within `(topic, epoch of that topic)`
and a topic recreation is an event that must be recorded, not a routine operation.
`payload` as an opaque column means Iceberg's column statistics are useless for it, so
`raw.messages` prunes on `kafka_ts`, `topic` and `offset` only, and never on anything
inside the frame.

**Revisit when:** disk days-remaining drops below 30 measured — Phase F publishes that as
a number with its command ([Q8](../research/2026-08-26-v3-requirements-clarification.md#q8--raw-archive-retention-on-a-single-host)) —
or a research question in `notebooks/` needs full-depth book state often enough that
replay-per-question is slower than a `book_deltas` table would have been, or a second
host or an S3 account exists and the retention question can be re-asked without a disk
constraint attached.

---

## Related

- [ADR-018](ADR-018-v3-lake-first-rust-capture.md) — the umbrella: lake-first, and why v2's archive was a lossy copy of a serving database
- [ADR-020](ADR-020-avro-fixed-point-contracts.md) — the wire contract the archive stores, and why `payload` keeps the verbatim string the typed record throws away
- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) — how the offsets that make lineage a coordinate get committed atomically with the data
- [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) — the `bronze.*` tables this makes rebuildable, and their partition spec
- [ADR-025](ADR-025-clickhouse-derived-hot-tier.md) — the other consumer of "derived and rebuildable"
- [ADR-027](ADR-027-book-snapshot-and-sequencing.md) — why the deltas stay in `raw.messages` and top-20 @ 1 Hz is the product
- [Q8, v3 requirements clarification](../research/2026-08-26-v3-requirements-clarification.md#q8--raw-archive-retention-on-a-single-host) — keep forever, with an 80 % disk alert
- [`../architecture/capacity-model.md`](../architecture/capacity-model.md) — the 6.47 GB/day prediction and the ~26-day disk bottleneck
- [`../runbooks/lake-disk-usage-high.md`](../runbooks/lake-disk-usage-high.md) — what an operator does instead of a TTL

---

## Outcome

_To be appended after the Phase D burn-in._
