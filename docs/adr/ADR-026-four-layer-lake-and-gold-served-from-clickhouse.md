# ADR-026: Four lake layers per venue, gold canonical, gold served indefinitely from ClickHouse

**Status:** Accepted
**Date:** 2026-08-27
**Author:** Rob Scott
**Category:** Data model / Storage

---

## Context

Phase D shipped the lake as two layers: `raw.messages` (every frame verbatim) and a
**unified** `bronze.trades` / `bronze.book_snapshots_l2` with an `exchange` column
([ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md)). ClickHouse was to become a
derived hot tier with a 7-day TTL ([ADR-025](ADR-025-clickhouse-derived-hot-tier.md)).
The first day of running it produced three facts that the design had not planned for:

- **The unified table dropped what the venues differ on.** Kraken's `ord_type`/`misc`,
  Binance's `buyer_is_maker`/`is_best_match`, Coinbase's per-level `event_time` have no
  column in a one-schema bronze; they survive only as bytes in `raw.messages`, where
  nobody can query them. A quant asking a venue-specific question has to re-decode.
- **The unified table's uniqueness claim was disproved twice in a day.**
  `(exchange, symbol, trade_id)` failed on Coinbase's reconnect replay (956 keys in 30 min),
  then `(…, conn_id)` failed on Coinbase re-sending trades *inside* one connection
  (5,034 keys, 15 s apart, two distinct frames — ADR-024 amendment, `lake-audit-failed.md`
  §2). The archive holds every delivery; "one row per trade" is a *derived* property, and
  the layer that promised it could not keep the promise.
- **The stated purpose changed.** The maintainer's requirement (2026-08-27) is three
  distinct things: a regulatory-grade record of *what arrived* (replay, "what was the
  actual packet"), a per-venue cleaned and typed view with lineage and metadata, and one
  canonical cross-venue model for research — served from a database fast enough for
  backtesting, **without a retention window**, because the served layers are small.

Constraints that bound the answer: one host, 16 CPU / 40 GB, ClickHouse at 4 CPU / 8 GiB;
disk **961 GB, 79 % used** (`df -h /`, 2026-08-26); measured lake growth **~6.5 GB/day**
for raw + unified bronze (8.1 GB after ~30 h, `du -sh /data` in `k2-minio`); measured daily
volume 8.63 M trades and 1.48 M book snapshots (`make lake-verify`, 2026-08-27T01:05Z).
Iceberg 1.8 has no materialized views — a "unified view" in the lake is a table and a job.
This repository is a production-quality single-host *demonstration*; the cloud mapping is
[scale-out-path.md](../architecture/scale-out-path.md), designed and not exercised.

---

## Decision

**We will lay the lake out as four layers — raw (verbatim), bronze *per venue* in the
venue's own schema, silver *per venue* typed and annotated, gold canonical and cross-venue —
each derived only from the one above and all retained indefinitely; and we will serve gold,
and only gold, from ClickHouse with no TTL, because gold is the backtesting surface by
definition and the lower layers are evidence, not truth.** Scope: the v3 lake and its
ClickHouse serving tier. The Avro `Trade`/`BookSnapshotL2` topics remain the transport
contract for the hot path and are not a lake layer. Raw pcap beside `raw.messages` is
designed here and built in the phase after E.

| Layer | Tables | Contract | Where |
|---|---|---|---|
| Raw | `raw.messages`; later `raw.pcap` | bytes as received, `recv_ts_ns`, connection + Kafka lineage | Iceberg, forever |
| Bronze | `bronze.<venue>_<message>` | the venue's field names and types as sent; no renames, no unit changes | Iceberg, forever |
| Silver | `silver.trades_<venue>`, `silver.book_<venue>` | typed (fixed-point, UTC), canonical symbol *added*, flags `checksum_ok`/`venue_replay`/`seq_gap`/`precision_loss`, every delivery kept | Iceberg, forever |
| Gold | `gold.trades`, `gold.book_top20`, `gold.dim_*`, `gold.ohlcv_{1m,5m,1h,1d}`, `gold.bbo_1s` | one schema, one row per logical trade, cross-venue | Iceberg **and** ClickHouse, forever |

Column-level contracts: [schema-design.md § v3 layers](../architecture/schema-design.md);
strategy and serving trade-offs: [data-strategy.md](../architecture/data-strategy.md).

---

## Rationale

- **Per-venue bronze and silver keep what the venues differ on, typed.** The alternative —
  a `vendor MAP<STRING,STRING>` on a unified table — is what v2 did (`vendor_data`) and it
  is unqueryable in practice: no types, no pruning, no schema evolution. Three venues with
  genuinely different semantics (no sequence numbers on Kraken, connection-wide sequence on
  Coinbase, no receive-side book state on Binance) are three schemas.
- **Uniqueness belongs to the layer that makes it true.** Below gold, the only honest
  identifier is lineage (which archived record produced this row). Gold is where venue
  replays are collapsed, and the audit that proves one-row-per-trade runs there. Silver
  keeps every delivery with `venue_replay` set so the replay *rate* stays a number.
- **Gold in ClickHouse without a TTL is cheap; silver would not be.** At measured rates gold
  is ≈ 0.5 GB/day compressed (~180 GB/yr, predicted at ~10:1 for trades and ~7:1 for
  80-float book rows); adding silver roughly triples that and, because silver keeps
  replays, puts `FINAL` on every wide backtest range. Gold is already deduplicated, so the
  hot path needs no `FINAL`. Rebuild from the lake is hours for gold, days for gold+silver.
- **Each layer derived only from the one above makes every bug recoverable** by rebuilding
  from raw — which is the same mechanism Phase D already exercised (`lake-verify`, the
  four lake chaos runs).
- **Lake + DuckDB stays the research surface for history**; ClickHouse is for interactive
  and concurrent loads. Both read the same gold contract.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Keep the unified bronze (ADR-024) and add a typed `vendor` struct/map | Venue fields become second-class and untyped; the uniqueness claim still fails below gold; the layer names mislead (today's "bronze" is a gold core) |
| Silver per venue **plus** a unified silver materialized in the lake | Iceberg has no MVs — it is another table and job to keep exactly-once; and a unified silver *is* gold minus dedup |
| Gold as the unification of per-venue silver, nothing served from ClickHouse | Backtesting and dashboards want `ASOF JOIN`, sparse indexes and concurrency; DuckDB over parquet is fine for one analyst and poor for many |
| ClickHouse holds silver **and** gold indefinitely | +0.5–0.8 GB/day, `FINAL` on silver reads, days to rebuild; silver is read when a result looks wrong, not in every backtest |
| ClickHouse with a 7–30 day TTL (ADR-025 as written) | The served layer is small enough to keep; a TTL puts the system-of-record question back on the lake for every historical backtest |
| Build the lake layers from the capture's Avro topics instead of from raw | Couples the record to the parser; a parser bug would be unrecoverable below the frame log. The lake decodes raw in Spark and the Avro topics stay the hot-path transport |

---

## Consequences

**Easier:** venue-specific questions answered from typed columns; one canonical surface for
cross-venue research; every layer rebuildable from raw; ClickHouse rebuild is gold-only;
the medallion names finally mean what they say.

**Harder:** three more layers to write, audit and compact — bronze and silver are per venue,
so adding a venue is DDL in two layers plus a Spark decode; Phase E carries a rename of
today's `bronze.*` (it becomes the gold core) and a rebuild from `raw.messages`.

**Committed to:** Spark as the lake's decoder for every layer; add-nullable-only evolution
at every layer with `raw.messages` frozen; audits per layer (raw offset continuity; bronze
and silver row parity with raw per venue; gold one-row-per-trade and parity with silver);
a `quant` ClickHouse profile (readonly, ~3 GiB, 2 threads) so backtests cannot evict the
ingest; a ClickHouse `system.parts` bytes gauge beside the lake disk gauge.

**Risks:** disk. Four persistent layers plus ClickHouse gold are ~10 GB/day predicted
against 193 GB free — roughly two months. A larger volume is a precondition of Phase E
landing, not a follow-up. Coinbase `level2` rows (~5 MB) already OOM'd compaction once at
768m; per-venue bronze carries the same rows and inherits the 2g maintenance heap and the
writer lock.

**Revisit when:** `k2_lake_disk_used_ratio` or the ClickHouse bytes gauge crosses 80 %; or
ClickHouse gold grows more than 1 GB/day for a week; or a backtest routinely needs a
silver-only field (promote the field to gold — do not copy silver into ClickHouse); or a
fourth venue arrives whose schema fits none of the three (then the per-venue bronze pattern
is proven or disproven).

Supersedes [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) in full and
[ADR-025](ADR-025-clickhouse-derived-hot-tier.md)'s 7-day-TTL clause; ADR-025's
"derived, rebuildable, reload by pull" contract stands.

---

## References

- [data-strategy.md](../architecture/data-strategy.md) — the serving trade-off table and retention.
- [schema-design.md § v3 layers](../architecture/schema-design.md) — per-layer column contract.
- [Plan 004 — Phase E](../plans/2026-08-26-v3-quant-research-platform/004-phase-e-hot-tier.md) — implements this.
- [ADR-021](ADR-021-raw-first-archive-and-lineage.md) (raw first), [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) (offsets in the snapshot), [ADR-024](ADR-024-unified-bronze-tables-in-the-lake.md) amendment (the measurements that undid the unified key).
- `docs/runbooks/lake-audit-failed.md` §2, `scripts/chaos/results/2026-08-27.tsv`.
