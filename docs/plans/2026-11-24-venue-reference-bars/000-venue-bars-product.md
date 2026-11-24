# Phase V0: the venue bars product

**Depends on:** P4, P5 and P6 of [the governed self-serve platform plan](../2026-11-24-governed-self-serve-platform/README.md) (the file-source ingestion pattern and landing zone; access points; the readiness scorecard)
**Decision it implements:** ADR-043, written in this phase: venue-published bars as an independent reference on the user's own stack, kept apart from captured data. It lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §5's no-REST-backfill clause for reference tables only and appends an Outcome there.
**Delivers:** a daily pipeline per venue that lands the venue's own candles, and a governed product, `venue_bars`, with one canonical output any consumer can read on its own.
**Exit:** for each venue, the bar for the last completed UTC day in `silver.venue_ohlcv_1d` equals a direct fetch of that day, field for field; a second run of the same day adds no row and changes no value; the product passes its readiness scorecard and `make access-verify`; every data file this phase commits is synthetic and named so.

## Scope

**Pull request 1: terms, facts and the decision**

- First task: read each venue's terms for public market data and its API, and quote the
  clauses that bear on automated retrieval and local storage in ADR-043, with URL and date.
  A venue whose terms do not allow this local use is dropped before any code is written.
- `docs/research/2026-11-24-venue-daily-candles.md`: one command per fact in the plan's
  ground-truth table, with the response reduced to its shape and counts. It includes the
  script that rebuilds a day's bar from a venue's public trades and reports whether it equals
  the venue's candle, without printing the values. The plan's ground truth then cites it.
- ADR-043. Consequences it must state: nothing fetched from a venue is committed; results
  live on the platform; the request budget; that this is an external reconciliation and
  ADR-029 §3 is unchanged. Revisit: each venue's terms are re-read at every release tag, and
  the ADR's Outcome records the date.
- Spike: fetch one day from each venue from inside the stack, to confirm no region or client
  block applies to this host.

**Pull request 2: three sources and the product**

- Each venue has its own module in `docker/lake/sources/` with two functions: `fetch`, which
  returns the requests for a day or a history window and their bytes, and `parse`, from bytes
  to rows. The three venues page three different ways, so paging is not put in the contract.
  Landing, audit and publish are the pattern's, unchanged.
  - `binance_klines.py`: start and end times; twelve-field arrays of strings; times in
    milliseconds.
  - `kraken_ohlc.py`: requested with the registry's native pair name and `since` set to the
    day; asserts the single result key equals the name requested; drops rows newer than the
    response's `last`.
  - `coinbase_candles.py`: field order is time, low, high, open, close, volume; values are
    JSON numbers and are parsed as decimals, never floats; a plain `User-Agent` is sent.
- Requests are sequential with a declared minimum interval per venue. One request per symbol
  per day is the steady state.
- Schedule: daily at 00:20 UTC for the day just closed.
- Idempotency is asserted at silver, not at landing. Binance and Coinbase return identical
  bytes for a closed day, so a re-run lands nothing. Kraken's response always carries the
  moving in-progress row, so each fetch is a new object; the load adds no row.
- Tables, DDL generated, each with its own contract:
  - `bronze.venue_candle_binance`, `bronze.venue_candle_kraken`,
    `bronze.venue_candle_coinbase`: the venue's fields as published, as strings, with
    `source_file_sha256`.
  - `silver.venue_ohlcv_1d`: one canonical shape. `exchange`, `canonical_symbol`,
    `window_start`, prices as fixed-point integers at 1e-8 to match `gold.ohlcv_1d`,
    `volume`, and `quote_volume` and `trade_count` nullable, with `fields_published` naming
    what that venue supplies. Keyed on exchange, symbol and day. The first value stored for
    a day is kept.
- Audits in the write-audit-publish step: high is at least open, close and low; low is at
  most open and close; volume is not negative; the day is complete (never today); no symbol
  outside the registry. A later fetch that disagrees with a stored day is the pattern's
  restatement check, declared here with severity `warn`: the difference stays in bronze, is
  counted per venue, and does not block the publish.
- A parse failure fails the run and alerts, with a runbook: a venue changed its format.
- Product `contracts/reference-data/venue_bars/`: descriptor; classification `internal`
  (readable by `realtime_reader`; "internal" is access control on the stack, nothing more);
  freshness SLO "yesterday's bar for every registry symbol by 01:00 UTC"; access points
  Trino, the notebook helper and the open catalog endpoint. Its page states that the data is
  the venue's, fetched by the user under the venue's terms, with a link to each.
- Sample files are synthetic, dated before capture began, named `synthetic-*`, with a README
  beside them. A fresh clone builds the product from them with no network.
- `notebooks/k2lake.py`: `venue_bars(exchange, symbol, start, end)`.

**Documents.** ADR-043 and the ADR-028 Outcome; the research note; chapter 18; runbooks for
a venue fetch that fails or is refused and for a format change.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test venue_bars_binance
docker exec k2-airflow-scheduler airflow dags test venue_bars_kraken
docker exec k2-airflow-scheduler airflow dags test venue_bars_coinbase
bash scripts/venue-bars-verify.sh      # last completed day: table equals a direct fetch, per venue; re-run adds no row
make access-verify
make test                              # parser unit tests on synthetic samples; the samples check from P0
make generate && test -z "$(git status --porcelain)"
make gates
```
