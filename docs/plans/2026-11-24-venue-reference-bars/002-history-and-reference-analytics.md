# Phase V2: venue history and reference analytics

**Depends on:** V0, V1; P4 of [the governed self-serve platform plan](../2026-11-24-governed-self-serve-platform/README.md) (the FX models and the conversion macro)
**Decision it implements:** the ADR from V0. No new decision.
**Delivers:** years of venue-published daily bars in the reference product, and two models built on them that the captured data alone is too short for: reference bars valued in reporting currencies, and a long-history stablecoin basis.
**Exit:** the history load passes the same audits as the daily run; row counts per venue and symbol are published with the earliest day each reaches; on days both exist, the long-history basis and the captured-bar basis agree within a tolerance set from their measured difference; every model here carries "venue-published, not captured" in its description and on its page.

## Scope

**Pull request 1: history load**

- A `history` run of each venue DAG, paged at the venue's limit, sequential, at the declared
  request interval.
  - Binance: from each symbol's first day, 1,000 days a request.
  - Coinbase: from each symbol's first day, 300 days a request.
  - Kraken: the 720 days its endpoint serves. Its older history sits in a 10 GB archive and
    is left out; the product page states where Kraken's history starts and why.
- Every landed page goes through the same audits. A day already stored is not rewritten.
- The reconciliation model is unaffected: it compares only days that exist on both sides.

**Pull request 2: reference analytics**

- `venue_ohlcv_1d_fx`: venue bars valued in each reporting currency through the `fx_rate`
  macro, with the same columns and caveats as `ohlcv_1d_fx`.
- `stablecoin_basis_reference_daily`: the same close-ratio estimator as the captured-bar
  basis, over venue closes. It cannot filter on the time of the last trade, because a venue
  candle does not carry one; the model description says so.
- A data test compares the two basis series on overlapping days and warns beyond the
  tolerance. The first comparison is published with its command before the tolerance is set.
- Neither model replaces its captured counterpart. Marts built on captured bars keep using
  the captured-bar basis; the reference series exists for history and for comparison.
- Classification `internal`, inherited from `venue_bars` by the generator rule.

**Documents.** `dbt/models/fx/README.md` gains the reference models and how they differ;
chapter 18.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags trigger venue_bars_binance --conf '{"mode": "history"}'
docker exec k2-airflow-scheduler airflow dags trigger venue_bars_coinbase --conf '{"mode": "history"}'
docker exec k2-airflow-scheduler airflow dags trigger venue_bars_kraken --conf '{"mode": "history"}'
docker exec k2-trino trino --execute "SELECT exchange, min(window_start), max(window_start), count(*) FROM lake.silver.venue_ohlcv_1d GROUP BY 1"
docker exec k2-airflow-scheduler airflow dags test dbt_marts
make generate && test -z "$(git status --porcelain)"
make gates
```
