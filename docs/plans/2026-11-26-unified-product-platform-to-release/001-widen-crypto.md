# Phase 001: widen crypto

**Depends on:** `000` (tag `v3.0.0-beta.2`); from the first plan, the Kafka and public-API
connectors, `kraken-trades`, `kraken-instruments`, the security master, `canonical-trades` as a
union over declared venue ports, and `candles` at every timeframe.
**Decision it implements:** [`ADR-056-unified-data-product-platform.md`](../../adr/ADR-056-unified-data-product-platform.md): each venue has
a trades product and an instruments product, and a new venue differs only in documents. The
event-bars transform decides [ADR-049](../../adr/ADR-049-event-bars-extended-not-recomputed.md):
accepted, revised or withdrawn in the pull request that builds it.
[ADR-050](../../adr/ADR-050-order-book-capture-paused-during-platform-build.md) governs the
paused book products.
**Delivers:** `coinbase-trades`, `coinbase-instruments`, `binance-trades`, `binance-instruments`;
`canonical-trades` and `candles` over three venues; `event-bars` as a registered code transform
writing the `market_analytics` warehouse; `venue-books` declared paused; `book-snapshots`
retired; `venue-quality` rebuilt on run records; the parity pins re-made over three venues; the
remaining readers of the old venue tables moved.
**Exit:** `make parity-silver` and `make product-verify` pass per venue; `make parity-ohlcv` and
`make parity-bars` pass on new pins; a consumer counts `event_bars.bars`; two builds of the
derived chain differ by nothing; `make demo` passes; no file outside the archive and the dated
surfaces names an old venue table; tag `v3.0.0-beta.3`.

## Scope

**First pull request: the instruments endpoints.** One call to each venue's public reference
endpoint, field names only, to confirm the fields the security master needs; the first plan
could not confirm them.

**Coinbase, then Binance**, each as documents on the existing connectors:

- Coinbase: `events` then `trades`, unnested twice, with the flat index the old silver used
  computed as a running sum.
- Binance: bronze renames the case-only twin fields (`e` and `E`, `t` and `T`, `m` and `M`),
  which made `bronze.binance_trade` unreadable through Trino; side comes from `m`.
- Each venue's instruments product, its listing in the security master by the same rules and the
  reviewed exceptions seed, its grants and its expectation rows.

**Derived products over three venues.** `canonical-trades` gains two input ports; nothing else
in it changes. Because `candles-eur` looks back only 7 days, `make rebuild` runs on the derived
chain once the new venues publish, so history is complete (plan review section 1).

**`event-bars`**, a registered code transform: the code lives under its domain with fixtures and
a reference implementation, as
[ADR-029](../../adr/ADR-029-research-production-parity-contract.md) requires of a materialised
product, and runs in the Spark runner that alone reads, locks, writes and publishes its output.

**Book products** (ADR-050): `venue-books` declared `paused` in ODPS, nothing running;
`book-snapshots` retired with the book data let go in the first plan, to be re-proposed if book
capture resumes.

**`venue-quality`** rebuilt on run records rather than the retired `audit.checks`.

**Proofs.** `make parity-silver` and `make product-verify` per venue; `make parity-ohlcv` and
`make parity-bars` re-pinned over three venues, replacing the Kraken-only pin of the first plan; a
determinism check: two builds of the derived chain compared with `EXCEPT ALL`, empty both ways.
`make demo`, built in the first plan, is this phase's Trino smoke test (review trap 7).

**The remaining readers.** Every file that still names an old Binance or Coinbase lake table is
moved to the new products or retired; the generic readers moved in the first plan (F8).

**Learning aids and documents.** Runbook: add a venue. Chapter 24 (today beside the target).

## Verification

```bash
make parity-silver PRODUCT=coinbase-trades && make product-verify PRODUCT=coinbase-trades
make parity-silver PRODUCT=binance-trades && make product-verify PRODUCT=binance-trades
make parity-ohlcv
make parity-bars
docker exec k2-trino trino --execute "SELECT count(*) FROM market_analytics.event_bars.bars"   # as a consumer
bash scripts/verify/determinism.sh          # EXCEPT ALL between two builds, empty both ways
make demo
make access-verify
git grep -l -E '\b(bronze|silver)\.(binance|coinbase)_[a-z_]+\b' -- ':!legacy' ':!docs/adr' \
  ':!docs/audits' ':!docs/benchmarks' ':!docs/research' ':!docs/plans' ':!docs/MIGRATION-JOURNEY.md' | wc -l   # 0
make test
make check-docs
```

The `git grep` excludes the immutable surfaces with pathspecs, since a dated document keeps the
names it was written with (F14).
