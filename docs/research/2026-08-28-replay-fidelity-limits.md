# What top-20 @ 1 Hz over public WebSockets can and cannot honestly simulate

**Date:** 2026-08-28 · **Question:** `k2-capture replay` reproduces the live pipeline's
output byte for byte from the archive (ADR-029). What research does that data support,
and what does it not, stated before anyone builds a simulation on it?

This is a statement of limits, written from what the archive *is* rather than from
what a replay *could* be made to do. Each limit names the property of the data that
causes it. The parity contract guarantees the pipeline is faithful to the feed; it
says nothing about the feed being faithful to the market.

## What the archive holds

- Every WebSocket frame, verbatim, from three venues' public feeds, stamped with
  `recv_ts_ns` on receipt over the public internet ([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)).
- Every trade print each venue publishes, with the venue's own id and timestamp.
- Book deltas as each venue publishes them: Kraken at depth 25 with a CRC32 over the
  top 10, Binance as a complete top-20 every 100 ms, Coinbase full depth with absolute
  quantities ([ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md)).
- Derived from those and reproducible from them: top-20 snapshots at 1 Hz, per-second
  BBO, time candles, event bars.

## Limits, each with its cause

| Limit | Because | What it rules out |
|---|---|---|
| **Depth is what the venue sent, top-20 in the product** | Kraken is subscribed at 25 and checksummed at 10; Binance's partial stream is 20 by construction; only Coinbase carries full depth, and only its replay can go deeper (`--depth`) | Resting-liquidity studies below level 20 on two of three venues; any cross-venue depth comparison past level 20 |
| **1 Hz snapshots in the product; sub-second only by replay** | The sampler is a product decision (ADR-027); the deltas are in bronze and replay can sample at `--interval-ms 100`, but the queryable tables do not | Anything intra-second read from `gold.book_top20` / `bbo_1s` without a replay behind it |
| **One clock is a receive stamp over the public internet** | `recv_ts_ns` includes venue processing, venue egress, the internet path, and this host's socket; Binance's book stream carries no venue timestamp at all | Separating exchange latency from network latency in any single row; latency-arbitrage studies; any claim about *when* the venue's matching engine did something |
| **No queue position, no order ids, no cancels attributed** | Public L2 feeds publish price-level aggregates; the L3 feeds that carry order ids are not subscribed | Queue-position models, fill-probability models, cancel/replace behaviour, any per-order study |
| **Venue-side conflation is invisible** | Binance publishes a 100 ms conflated partial; Coinbase batches up to 100 trades per frame at one time; Kraken's book updates are already netted per level | Event-count studies that assume one frame per market event; message-rate as a proxy for activity across venues |
| **No hidden or iceberg liquidity, no auction, no halts modelled** | Not in the public feed | Realistic adverse-selection simulation; anything that depends on liquidity the book did not show |
| **Trades and book are two streams with two orderings** | A trade's `exchange_ts` and the book snapshot in force at it are joined as-of on receive time, not on a venue sequence that spans both | The exact book state at a trade's matching instant; trade-through analysis finer than one snapshot interval |
| **Fixtures are seconds long, recorded on 2026-08-26** | Three fixtures, 10–20 s each | Replay determinism is proven over what the fixtures contain; a venue behaviour they lack is unproven until a new fixture carries it |

## What this supports, stated positively

- Candle and daily-bar research, and event-bar research at the catalogue thresholds
  or any other (`gold.ohlcv_*`, `gold.bars`, `k2lake.bars()`), with every candle and
  bar exact and reproducible at a snapshot id.
- Spread, depth-to-20 and imbalance studies at one-second granularity, per venue,
  with the Kraken rows carrying a verified checksum.
- Completeness and gap analysis: trade-id holes, venue replays, sequence gaps, offset
  continuity, all as rows in `silver.*` and `audit.checks` rather than as claims.
- Cross-venue basis and lead–lag at one-second resolution and coarser, with the
  receive-clock caveat stated on the result.
- Strategy *signal* research: features built from trades and 1 Hz books, backtested
  with an execution model the researcher states and defends, since this data cannot
  supply one.

## What this does not support, in the same words

Queue-position and fill simulation with realistic adverse selection; latency-arbitrage
and co-location studies; microstructure work below one second on the queryable
product; anything requiring order-level attribution; anything requiring the venue's
matching-engine time. Results in these areas quoted off this archive should say
"public-feed L2 at receive time" in the method, or not be quoted.

## Outcome

Cited from [ADR-029](../adr/ADR-029-research-production-parity-contract.md) and the
[notebooks README](../../notebooks/README.md) so the limits arrive with the data.
Revisit when an L3 feed is subscribed on any venue, or when a venue timestamp is added
to a stream that lacks one today.
