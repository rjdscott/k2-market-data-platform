# Phase V0: the venue bars product

**Depends on:** P4 of [the governed self-serve platform plan](../2026-11-24-governed-self-serve-platform/README.md) (the file-source ingestion pattern, the landing zone, generated DAGs)
**Decision it implements:** a new ADR written in this phase: venue-published bars as an independent reference, kept apart from captured data. It lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §5 for reference tables only and appends an Outcome there.
**Delivers:** a daily pipeline per venue that lands the venue's own candles, and a governed product, `venue_bars`, with one canonical output any consumer can read on its own.
**Exit:** for each venue, the bar for the last completed UTC day in `silver.venue_ohlcv_1d` equals a direct fetch of that day, field for field; a second run of the same day lands no new object and adds no row; the product passes its readiness scorecard and `make access-verify`; nothing fetched from a venue is in the repository.

## Scope

**Pull request 1: the pattern learns paged requests**

- Spike first: fetch one day from each venue from inside the stack, to confirm no region or
  client block applies to this host.
- The file-source contract gains a `requests` block: a URL template, the list it expands over
  (the venue's symbols from `config/instruments.yaml`) and a date window with a page size.
  The fetch task lands one object per request, each by its content hash. Everything after
  fetch is unchanged: load, audit, publish.
- Requests are sequential with a declared minimum interval per source, and send a plain,
  generic `User-Agent`. One request per symbol per day is the steady state.
- A unit test expands a template against a fixed registry and window and asserts the request
  list.

**Pull request 2: three sources, one product, the ADR**

- Parsers in `docker/lake/sources/`, each a function from bytes to rows, each with a unit
  test on a synthetic sample:
  - `binance_klines.py`: twelve-field arrays, strings, times in milliseconds.
  - `kraken_ohlc.py`: the result key is not the requested pair, so the parser takes the one
    key that is not `last`; the final row is uncommitted and is dropped; the pair name is
    resolved to the registry's symbol through the venue's own pair listing, which is landed
    beside the candles.
  - `coinbase_candles.py`: field order is time, low, high, open, close, volume; values are
    JSON numbers and are parsed as decimals, never floats.
- Schedule: daily at 00:20 UTC for the day just closed. Coinbase's five-minute response cache
  and Kraken's uncommitted last row are both past by then.
- Tables, DDL generated:
  - `bronze.venue_candle_<venue>`: the venue's fields as published, as strings, with
    `source_file_sha256`.
  - `silver.venue_ohlcv_1d`: one canonical shape for all venues. `exchange`,
    `canonical_symbol`, `window_start`, prices as fixed-point integers at 1e-8 to match
    `gold.ohlcv_1d`, `volume`, and `quote_volume` and `trade_count` nullable, with
    `fields_published` naming what that venue supplies. Keyed on exchange, symbol and day.
- Audits in the write-audit-publish step: high is at least open, close and low; low is at
  most open and close; volume is not negative; the day is complete (never today); no symbol
  outside the registry; a day already stored is not changed by a later fetch beyond the last
  decimal the venue publishes (a change is recorded and warns: a venue may restate).
- Product `contracts/reference-data/venue_bars/`: descriptor, one contract for the canonical
  output, classification `internal`, freshness SLO "yesterday's bar for every registry symbol
  by 01:00 UTC", access points Trino, the notebook helper and the open catalog endpoint.
- Sample files are synthetic, dated before capture began, and say so in their first line and
  on the product page. A fresh clone builds the product from them with no network.
- `notebooks/k2lake.py`: `venue_bars(exchange, symbol, start, end)`.
- The ADR records the terms of each venue as read, the request budget, and the rule that no
  venue data is committed or redistributed.

**Documents.** The ADR and the ADR-028 Outcome; chapter 18; a runbook for a venue fetch that
fails or is refused.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test venue_bars_binance
docker exec k2-airflow-scheduler airflow dags test venue_bars_kraken
docker exec k2-airflow-scheduler airflow dags test venue_bars_coinbase
bash scripts/venue-bars-verify.sh      # last completed day: table equals a direct fetch, per venue
make access-verify
git ls-files | xargs grep -l "data-api.binance.vision\|api.kraken.com\|api.exchange.coinbase.com" | grep -v "^contracts/\|^docs/"   # empty
make generate && test -z "$(git status --porcelain)"
make gates
```
