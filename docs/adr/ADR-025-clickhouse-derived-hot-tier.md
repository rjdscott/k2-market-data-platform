# ADR-025: ClickHouse as a derived, rebuildable hot tier, reloaded by pull from the lake

**Status:** Proposed
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Storage

---

## Context

In v2 the arrows point the wrong way. ClickHouse holds the medallion
([ADR-009](ADR-009-medallion-in-clickhouse.md)), the lake is filled *from* ClickHouse
over JDBC (`docker/offload/offload_generic.py:172`), and ClickHouse's TTL is what decides
what the archive can ever contain. Three consequences follow, all of them visible in the
code rather than inferred:

- **The archive inherits a serving database's constraints.** `silver_trades`'
  `trade_conditions Array(String)` and `vendor_data Map(String,String)` cannot be
  deserialized by the Spark ClickHouse JDBC driver, so the Iceberg schema simply omits
  them ([`../architecture/README.md`](../architecture/README.md), *Cold store*). Columns
  that exist in the hot tier are absent from the permanent record because of a driver.
- **Losing the ClickHouse volume loses data**, not just a cache. There is no path that
  rebuilds `bronze_trades_*` from anywhere; the only upstream is a Redpanda topic with
  7-day retention, and Bronze is plain `MergeTree`
  (`docker/clickhouse/ddl/01-k2-schema.sql:88`), so re-consuming duplicates every row —
  there is no key, no version, no dedup.
- **Reload is not a supported operation.** [ADR-003](ADR-003-clickhouse-warm-storage.md)
  made ClickHouse "the primary query engine for the API layer" and gave it 30 days of
  MergeTree; nothing in v2 can put a day of history back into it after an incident.

[ADR-018](ADR-018-v3-lake-first-rust-capture.md) inverts this and its Decision §3 states
the target: ClickHouse becomes a derived hot tier with `ReplacingMergeTree` and a 7-day
TTL, and "losing the whole ClickHouse volume costs a rebuild from the lake, not data."
Phase E builds it. This ADR is written now, in Phase D, because the lake tables being
created this week are the ones the rebuild reads, and a contract fixed after the writer
has shipped is a contract negotiated from a weaker position.

Spike S11 established the mechanism before it was designed in. ClickHouse 24.3 reads an
Iceberg v2 table through the `iceberg()` table function — *not* `icebergS3`, which does
not exist on 24.3 (`Code: 46 ... Maybe you meant: ['iceberg']`) — following current
metadata rather than the file listing, and returning 5 rows where a
`s3('…/data/*.parquet')` glob over the same table returned 8
([ADR-018 Appendix A](ADR-018-v3-lake-first-rust-capture.md#s11--clickhouse-243-reads-iceberg-v2)).
Re-verified against the running stack, 2026-08-26:

```console
$ docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
    -q "SELECT version()"
24.3.18.7
$ docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
    -q "SELECT name FROM system.table_functions WHERE name ILIKE '%iceberg%'"
iceberg
```

---

## Decision

**We will make ClickHouse a derived, rebuildable hot tier holding 7 days of data it
never originates, and make its only supported reload path a pull from the lake through
the `iceberg()` table function — with the `s3()` glob fallback banned in writing, and an
`iceberg()` failure treated as a stop-the-line bug rather than a reason to reach for the
glob.**

Scope: the contract. **Phase E builds the tier** — the `ReplacingMergeTree` DDL, the
`AvroConfluent` queue tables, the OHLCV read model
([ADR-026, reserved](ADR-018-v3-lake-first-rust-capture.md#follow-on-adrs)) — and this
ADR constrains what Phase E is allowed to do. What it fixes now:

1. **ClickHouse originates nothing.** Every row in the hot tier exists in the lake, or is
   derivable from it. No column, no table, no aggregate lives only here.
2. **The lake is never written from ClickHouse.** The JDBC path is deleted with
   `docker/offload/` in the Phase D cutover, and no replacement is built.
3. **Reload is by pull, through `iceberg()`,** against the same MinIO objects the lake
   catalog manages. The glob is banned.
4. **7-day TTL, `ReplacingMergeTree` keyed for replay-safety.** Re-consuming a topic or
   re-running a reload must converge, not duplicate.
5. **Rebuild is timed, not asserted.** The recovery runbook carries a measured duration
   or says "not yet verified".

### What this supersedes, clause by clause

Stated precisely because both ADRs are Accepted and most of what they say still stands.

From [ADR-009](ADR-009-medallion-in-clickhouse.md), the Decision — *"Implement a
four-layer medallion architecture using ClickHouse cascading Materialized Views (Raw →
Bronze), a Kotlin stream processor (Bronze → Silver), and ClickHouse AggregatingMergeTree
MVs (Silver → Gold). Offload all four layers hourly to Iceberg for cold storage."* Two of
its clauses are superseded here: **the medallion lives in ClickHouse** (in v3 it lives in
the lake; the hot tier holds decoded trades and book snapshots, not a four-layer
hierarchy), and **all four layers offload to Iceberg** (nothing offloads from ClickHouse
at all; the direction reverses). Two clauses are superseded elsewhere and are *not*
claimed here: the Raw layer's string-numerics rule went to
[ADR-020](ADR-020-avro-fixed-point-contracts.md), and the Gold/OHLCV computation model
goes to ADR-026. ADR-009's Outcome — that it shipped as three layers, not four — stays as
written.

From [ADR-003](ADR-003-clickhouse-warm-storage.md), the Decision's five clauses. Three
are superseded: **"Store 30 days of recent data in MergeTree tables"** (7 days,
`ReplacingMergeTree`), **"Serve as the primary query engine for the API layer"** (the
research query layer is DuckDB + PyIceberg over the lake, [ADR-018](ADR-018-v3-lake-first-rust-capture.md)
§4; ClickHouse serves dashboards and exploration, where a last-bit difference does not
matter), and **"TTL-expire data to Iceberg (cold tier) after 30 days"** (data never moves
from ClickHouse to Iceberg; TTL now simply drops rows that already exist in the lake).
Two clauses stand unchanged: ClickHouse ingests directly from Redpanda, and it
pre-computes aggregations — *what* it pre-computes and how is ADR-026's question, not
this one's.

---

## Rationale

**"Derived" is only true if the rebuild is a supported operation, not a story.** A tier
described as rebuildable that has never been rebuilt is a tier whose rebuild is a
research project on the worst possible day. The contract therefore includes a runbook
path and a measured duration, and until it is measured it says so
([`../runbooks/lake-recovery.md`](../runbooks/lake-recovery.md)). The distinction between
this and v2 is not the DDL; it is that a rebuild has an owner and a number.

**`iceberg()` only, and the glob is banned in writing.** The `s3('…/data/*.parquet')`
fallback is the tempting shortcut: it needs no catalog, it is one function call, and on
spike S11's test table it returned **8 rows where the truth was 5** — it resurrects rows
deleted by a copy-on-write rewrite and double-counts files that compaction replaced.
Compaction runs nightly against every lake table, so the glob is not wrong in a corner
case; it is wrong the morning after any maintenance run. Worse, it fails as a *silently
plausible number* rather than as an error, which is the single failure shape a research
platform must not have. Hence the rule, and hence its unusual strength: if `iceberg()`
stops working, the correct response is to stop and fix it, because every alternative on
the table produces answers that look right.

**Rebuild-by-pull rather than replay-from-Redpanda.** Both would work for recent data,
and only one works generally. Redpanda holds 48 h of raw and 7 d of derived
(`docker/redpanda/init.sh`), so a topic replay can refill a hot tier that has been down
for hours but not one that needs a week — and a replay reaches the tier through the
capture-to-broker path, so it re-derives rather than re-reads, which means the hot tier
and the lake can disagree about the same second. Reading the lake gives the same bytes
the archive holds, for any window, from one source. Redpanda replay stays available and
is documented as what it is: a cold start, appropriate when the gap is small and the
broker still has the data.

**The hot tier is allowed to be numerically softer than the lake, and that is written
down.** [ADR-027](ADR-027-book-snapshot-and-sequencing.md) already commits ClickHouse to
`Array(Float64)` for book levels where the lake carries exact `DECIMAL(28,10)`, because
ClickHouse's array functions are ergonomic over floats and a fight over decimals. The
consequence is stated there and restated here as a tier-level rule: **a number that must
be bit-exact is read from the lake; a number on a dashboard is read from ClickHouse.**
Making the hot tier authoritative for nothing is what makes that asymmetry safe rather
than a reconciliation hazard.

**Why fix the contract in Phase D rather than in Phase E.** Because the lake DDL landing
this week determines whether the rebuild is possible at all: copy-on-write rather than
merge-on-read (S11 left MOR deletes untested against `iceberg()`), Parquet + zstd,
`DECIMAL(28,10)` rather than the wire's `int64`, and partition columns ClickHouse can
push a predicate into. Every one of those is a Phase D choice made *for* a Phase E reader.
Writing the ADR after Phase E would document those choices instead of constraining them.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Keep v2's shape: ClickHouse as the system of record, lake filled from it** | It is what exists, and it is the thing ADR-018 was written to fix. The archive inherits the serving DB's TTL, normalisation and driver limits — `Array`/`Map` columns are already dropped at that boundary — so nothing is reproducible and losing one volume loses data. |
| **`s3('…/data/*.parquet')` glob for the rebuild**, skipping the Iceberg reader | One function call, no catalog dependency, and measurably wrong: 8 rows against a truth of 5 after a copy-on-write delete (S11), because it sees files no snapshot references. Its failure mode is a plausible number, not an error. Banned in writing rather than merely discouraged, because it is exactly what a tired operator reaches for at 2 a.m. |
| **Rebuild by replaying Redpanda from the earliest offset** | No lake dependency and bounded by broker retention: 48 h raw, 7 d derived. It cannot rebuild a week-old window, and it re-derives through the capture path rather than re-reading the archive, so the two tiers can disagree about the same second. Kept as the *cold start* path for small gaps, documented as such. |
| **Materialise the lake into ClickHouse continuously** (a lake → hot streaming sync) | Removes the rebuild question by making it constant, and re-adds a resident streaming runtime against a CPU budget [ADR-004](ADR-004-eliminate-spark-streaming.md) cleared to buy 13.5 CPU back. ClickHouse already consumes the same topics directly; a second path to the same rows is two things to keep consistent. |
| **Drop ClickHouse; serve everything from DuckDB over the lake** | Genuinely tempting — one storage layer, one truth, one query engine, and DuckDB reaches the catalog directly (spike S10). It loses sub-second dashboard queries over the last hour, continuous ingest, and concurrent access from Grafana; a 5-minute batch ingest means the freshest lake row is up to 5 minutes old. The hot tier exists for freshness and concurrency, which are the two things the lake is bad at. |
| **`MergeTree` with a dedup step, rather than `ReplacingMergeTree`** | Cheaper merges and it makes a topic replay duplicate every row — v2's exact bug (`01-k2-schema.sql:88`). A tier whose recovery path is "re-consume" must converge under re-consumption, which is what the Replacing engine is for. |

---

## Consequences

**Easier:** losing the ClickHouse volume becomes a timed restore rather than an incident
with data loss; a schema change in the hot tier is a `DROP` and a reload rather than a
migration; the lake's contents stop being constrained by a JDBC driver's type support;
and "which number is authoritative" has one answer everywhere.

**Harder:** the hot tier is now downstream of *two* systems — Redpanda for the live path
and the lake for the reload path — so an operator has to know which one is broken. Rebuild
time is unmeasured and, on a 7-day window at the predicted rates, is a real duration
during which dashboards are wrong; nobody should discover its length during an incident.
And two numeric representations of the same book now coexist by design (exact in the lake,
`Float64` in the hot tier), which anyone reconciling the tiers must know about.

**Committed to:** ClickHouse originating nothing — a column that exists only in the hot
tier is a bug this ADR forbids; `iceberg()` as the only documented reload path, with the
glob banned in writing rather than merely discouraged; a 7-day TTL, so the hot tier is
never asked a question the lake cannot answer; and deleting `docker/offload/` with no
replacement, so no code path can write the lake from ClickHouse again.

**Risks:** the rebuild path rests on `iceberg()` on ClickHouse 24.3, which is documented
upstream as experimental and is pinned here for JDBC reasons that will themselves expire
([ADR-015](ADR-015-clickhouse-lts-downgrade.md)); an upgrade must re-verify it, and S11
did **not** test merge-on-read deletes — which is why the lake DDL is copy-on-write
throughout, and why introducing MOR anywhere in the lake breaks this contract until a
spike says otherwise. `iceberg()` reads MinIO directly rather than through Lakekeeper, so
a catalog and a reader can in principle disagree about which metadata is current. And the
whole tier is Phase E work described by a Phase D document: if Phase E finds that a
constraint here is unbuildable, this ADR gets an Outcome rather than a quiet edit.

**Revisit when:** the first real rebuild is performed and timed — that number goes into
[`../runbooks/lake-recovery.md`](../runbooks/lake-recovery.md) and decides whether 7 days
is the right window; or `iceberg()` fails against a compacted lake table, which is a
stop-the-line bug and not a reason to reach for the glob; or ClickHouse moves off 24.x, at
which point the Iceberg reader, `AvroConfluent` and `_headers` all need re-verifying
together.

---

## Related

- [ADR-018](ADR-018-v3-lake-first-rust-capture.md) — Decision §3 states the target; Appendix A spike S11 is the reader this depends on and the glob it bans
- [ADR-009](ADR-009-medallion-in-clickhouse.md) — the medallion-in-ClickHouse and offload-from-ClickHouse clauses this supersedes (see *What this supersedes*)
- [ADR-003](ADR-003-clickhouse-warm-storage.md) — the 30-day MergeTree, primary-query-engine and TTL-to-Iceberg clauses this supersedes; the rest stands
- [ADR-021](ADR-021-raw-first-archive-and-lineage.md) — the archive the rebuild reads from
- [ADR-023](ADR-023-lakekeeper-rest-catalog.md) — why the lake's objects are addressable by ClickHouse at all
- [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) — the tables the rebuild pulls, and their copy-on-write property
- [ADR-027](ADR-027-book-snapshot-and-sequencing.md) — the exact-vs-`Float64` asymmetry between the tiers
- [`../runbooks/lake-recovery.md`](../runbooks/lake-recovery.md) — the rebuild procedure, and Redpanda replay as a cold start
- [`../plans/2026-08-26-v3-quant-research-platform/004-phase-e-hot-tier.md`](../plans/2026-08-26-v3-quant-research-platform/004-phase-e-hot-tier.md) — the phase that builds it

---

## Outcome

_To be appended after Phase E._
