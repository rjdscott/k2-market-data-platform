# Phase 005: the enriched product

**Depends on:** `004` (tag `v3.0.0-alpha.6`: `candles.ohlcv_1d`, the security master) and `002`
(`ecb-fx-rates`, `currency-master`, `calendars`); the owner's answer on FX content at 3.0. Before
this phase starts, the signature of `k2_as_of` and the rules for `relationships` and `asOf` are
frozen as an interface, as the interfaces pull request froze the rest (plan review section 4).
**Decision it implements:** `docs/adr/ADR-056-unified-data-product-platform.md`: joins across
domains are governed. A contract declares which columns are keys of which reference product,
the checks fail a reference that does not resolve, an enriched product is never less restricted
than its inputs, and every join to history states its point in time.
**Delivers:** `market-analytics/candles-eur`, a daily candle valued in euros across three
domains; the `k2_as_of` macro; relationship and policy-inheritance checks with failing
fixtures; joinable-to on catalog pages; `fx-reference/fx-crosses`; the FX content the owner
chooses.
**Exit:** `candles_eur` builds on fixtures with its unit tests; a fixture with a weaker output
policy than its input fails `k2gen check`, and so does a reference to a retired product; a fact
exactly on `valid_from` picks the new version; pausing `ecb-fx-rates` gives `rate_missing` rows
and a `partial` badge; the product's catalog document lists inputs from three domains; tag
`v3.0.0-alpha.7`.

## Scope

**Point in time, once.** `k2_input` was built in `002`; this phase adds only `k2_as_of` (review
trap 4). Following ADR-030's convention, history is half-open `[valid_from, valid_to)`, the open
row's `valid_to` is `9999-12-31 23:59:59`, and first versions open at `1970-01-01`.
`mode='valid'` joins the row that held at the given time; `mode='known'` takes the newest row
known at that time, which avoids look-ahead: a 10:00 UTC candle gets that day's ECB fixing by
valid time and the previous day's by knowledge time. Unit tests cover each boundary.

**`candles-eur`** (namespace `candles_eur`, table `ohlcv_1d_eur`). Inputs `candles.ohlcv_1d`,
`security-master.instrument` and `.asset`, `ecb-fx-rates.gold_eur_rates_daily`, all
`required: false`, so a paused input makes it `partial` rather than failing it. A candle finds
its instrument as at `window_end` (valid time), the quote asset's ISO 4217 currency, and the
newest rate published by `window_end` (knowledge time). The currency master is not read by the
model: it is the dictionary both sides point at, read by the relationship tests of `asset` and
of the ECB product. A stablecoin or crypto quote has no currency, so its row is `rate_missing`,
a stated limitation rather than an assumption that a stablecoin is a dollar. Key `venue`,
`instrument_id`, `window_start`; `delayed_reader` 20 minutes on `window_end`. The product looks
back 7 days, so it does not backfill a new venue by itself; the follow-on plan runs
`make rebuild` on the chain when it adds venues.

**Checks** (in the checks library, so CI and a later registry run the same ones): every
`relationships` entry names an existing, same-typed column of a non-retired product, in ODCS's
host-less fully qualified form; a target with history requires `asOf`; no output policy weaker
than an input's (a `delay` column never earlier than the input's, a masked input column stays
masked or is dropped, a registered transform as strict as any input). Each rule has a failing
fixture.

**Catalog pages** gain "joinable to", from the declared keys.

**`fx-crosses`**: the `fx_rates_daily` mart, any pair from two euro legs.

**FX content per the owner's decision.** Option (a) stops at `ecb-fx-rates`, `fx-crosses` and
`candles-eur`. Option (b) adds the old P4 content, under the conventions of
[the old P4 phase file](../2026-11-24-governed-self-serve-platform/004-fx-data-product.md):
returns, volatility, bars in reporting currencies, the stablecoin basis and its decomposition,
behind one conversion macro, with `make parity-fx` as their second computation. The architecture
judged option (b) at about 3 more days.

**Learning aids and documents.** dbt lab 6 (point-in-time joins). Chapter 25 (joins across
domains).

## Verification

```bash
make test-dbt SELECTOR=candles_eur   # dbt build --selector candles_eur on fixtures, throwaway Trino
make test-k2gen                    # weaker-policy and retired-reference fixtures fail; boundary unit tests pass
bash scripts/chaos/pause-input.sh ecb-fx-rates   # candles_eur rows rate_missing, badge partial
docker exec k2-trino trino --execute \
  "SELECT count(*) FROM market_analytics.candles_eur.ohlcv_1d_eur"   # as a full_reader
make access-verify                 # delayed_reader: nothing newer than 20 minutes on window_end
curl -s 127.0.0.1:8095/v1/products/market-analytics/candles-eur \
  | python3 -c "import json,sys; print(sorted({i['contractId'].split('.')[0] for i in json.load(sys.stdin)['inputs']}))"
make parity-fx                     # option (b) only
make test
make check-docs
```

The last `curl` prints `['fx-reference', 'market-analytics', 'reference-data']`.
