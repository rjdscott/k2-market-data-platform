# Phase V2: venue history and reference analytics

**Depends on:** V0, V1; P4 of [the governed self-serve platform plan](../2026-11-24-governed-self-serve-platform/README.md) (the FX models and the conversion macro)
**Decision it implements:** ADR-043. No new decision.
**Delivers:** years of venue-published daily bars in the reference product; and, as stretch, a stablecoin basis over that longer history.
**Exit:** the history load passes the same audits as the daily run; `silver.venue_ohlcv_1d` reports, per venue and symbol, the first day reached and every gap longer than seven days; the reconciliation is unchanged by the load (no day before a symbol's first captured day appears in it); every model here says "venue-published, not captured" in its description and on its page.

## Scope

**Pull request 1: history load** (must-have)

- A `history` run of each venue DAG, sequential, at the declared request interval.
  - Binance: forward from the venue's first day for each symbol, 1,000 days a request.
  - Coinbase: backward from yesterday to a fixed floor date in 300-day windows. An empty
    window does not end the walk: at least one symbol has a multi-year hole and resumes.
  - Kraken: the 720 days its endpoint serves. Older history exists only as a large archive
    and is left out; the product page states where Kraken's history starts and why.
- Every landed page goes through the same audits. A day already stored is not rewritten.
- A coverage table for the reference itself, `venue_bars_coverage`: per venue and symbol,
  first day, last day, day count, and each hole longer than seven days. Symbol coverage
  differs by era, and a reader should see that before using the history.
- Converting reference bars to a reporting currency needs no new model: the `fx_rate` macro
  from the first plan does it in a query, and the notebook helper shows how.

**Pull request 2: long-history stablecoin basis** (stretch)

- `stablecoin_basis_reference_daily`: the same close-ratio estimator as
  `stablecoin_basis_daily`, over venue closes. Each row records which venue supplied the USD
  leg and how many assets were used. Kraken is preferred for the USD leg; where only
  Coinbase reaches back, the row says so, because a Coinbase candle's close is not always
  its last trade of the day.
- It cannot filter on the time of the last trade, since a venue candle carries none. The
  model description says so.
- A dbt unit test with hand-built rows covers the venue preference and the asset count.
- A data test compares it with `stablecoin_basis_daily` on overlapping days and warns beyond
  a threshold set after the first comparison is read on the platform.
- It does not replace the captured-bar basis. Marts built on captured bars keep using
  `stablecoin_basis_daily`; the reference series is for history and for comparison.

**Documents.** `dbt/models/fx/README.md` gains the reference model and how it differs;
chapter 18.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test venue_bars_binance --conf '{"mode": "history"}'
docker exec k2-airflow-scheduler airflow dags test venue_bars_coinbase --conf '{"mode": "history"}'
docker exec k2-airflow-scheduler airflow dags test venue_bars_kraken --conf '{"mode": "history"}'
docker exec k2-trino trino --execute "SELECT * FROM lake.marts.venue_bars_coverage ORDER BY 1, 2"
docker exec k2-airflow-scheduler airflow dags test dbt_marts
make reconcile-bars
make generate && test -z "$(git status --porcelain)"
make gates
```
