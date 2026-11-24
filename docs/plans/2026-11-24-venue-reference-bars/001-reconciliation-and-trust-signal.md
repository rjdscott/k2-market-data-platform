# Phase V1: reconciliation and the day-level trust signal

**Depends on:** V0; P3 and P6 of [the governed self-serve platform plan](../2026-11-24-governed-self-serve-platform/README.md) (dbt, quality results, the scorecard)
**Decision it implements:** the ADR from V0, which this phase completes with measured tolerances.
**Delivers:** a daily comparison of K2's captured bars with each venue's own, tolerances set from a measured baseline, an alert when a day breaks them, and a per-day status that travels with the captured products so a user of granular data can see whether that day can be trusted.
**Exit:** the reconciliation product is in the catalog with owner, contract, lineage from both inputs, and a page; the baseline over every captured day is published as a benchmark; `make reconcile-bars` passes on a clean day; a day from which trades were deliberately removed in a scratch copy is flagged with the right status; the status is visible on the product page, in quality results and through the notebook helper.

## Scope

**Pull request 1: the comparison and the baseline**

- dbt model `bar_reconciliation_daily`, in `marts`, grain exchange, symbol, day. A full outer
  join of `gold.ohlcv_1d` and `silver.venue_ohlcv_1d`, the only place the two paths meet.
  Per field: the captured value, the venue's value, the difference (absolute for prices in
  1e-8 units, relative for volume and trade count). Only fields the venue publishes are
  compared: all six for Binance; no quote volume for Kraken; price and base volume only for
  Coinbase.
- `status`, one per row: `match`, `within_tolerance`, `price_mismatch`, `volume_breach`,
  `count_breach`, `missing_captured`, `missing_reference`, `not_comparable`.
- First task before any threshold exists: run the comparison over every captured day and
  publish the distribution of differences per venue and field through `/benchmark-report`.
  The questions it answers are open today: whether a bar rebuilt from the public trade stream
  equals the venue's candle exactly when nothing was missed, and how often a venue's own
  figure is the odd one.
- Tolerances are then written into the `venue_bars` and `gold.ohlcv_1d` contracts as quality
  rules, per venue and field, each citing the benchmark. Starting expectation, to be
  confirmed or replaced by the measurement: prices exact; volume and trade count within 0.1
  percent.
- dbt unit tests with hand-built rows for every status, including a venue that does not
  publish a field.

**Pull request 2: reconciliation in the catalog**

- Product `contracts/market-analytics/bar_reconciliation/`: a descriptor and two output
  contracts, so the comparison is discoverable, owned and versioned like any product.
  - `bar_reconciliation_daily`: the full comparison, venue values included. Classification
    `internal`, inherited from `venue_bars`.
  - `captured_day_quality`: status and the size of each difference per exchange, symbol and
    day, with no venue values in it. Classification `public`, readable by every role.
- That second output is less restricted than its input, which the generator rule from the
  first plan forbids. The rule gains one explicit exception: a contract may declare
  `declassified_from` with a written justification, and `k2gen validate` accepts it only when
  the output's columns are on an allow-list of non-source fields (keys, statuses, relative
  differences). The justification is shown on the product page, and the change is reviewed
  by the owners of the input through `CODEOWNERS`.
- Generated pages:
  - the reconciliation product's own page: what is compared for each venue, what is not
    comparable and why, the tolerances and the benchmark they came from;
  - every captured product's page gains a "Quality and known limitations" section: days
    checked, days matched, each breach with its date and size, and the limits that apply
    (Coinbase publishes no trade count; Kraken history starts 720 days back). Breaches are
    listed, not summarised away.
- With the `governance` profile up, the same facts reach OpenMetadata: the product under its
  domain, lineage from `gold.ohlcv_1d` and `silver.venue_ohlcv_1d`, and each day's result as
  a quality test result on the captured table it concerns.
- `make access-verify` covers both outputs; `delayed_reader` can read `captured_day_quality`
  and cannot read `bar_reconciliation_daily`, asserted in `scripts/entitlements-verify.sh`.

**Pull request 3: what a breach does**

- Each day's comparison is a row in `governance.dq_results`.
- A breach lowers that day's completeness for the captured product, which its SLO (P6 of the
  first plan) already measures.
- Alert `CapturedBarDisagreesWithVenue`, with a promtool test and a runbook via `/runbook`:
  how to tell a capture gap from a venue restatement, using the trade-id holes and sequence
  gaps the lake already audits.
- `make reconcile-bars`: runs the comparison for the last completed day and exits non-zero on
  a breach. It joins `/release-check`, not `make gates`, because it needs the network.

**Pull request 4: the trust signal in the tools people use**

- `captured_day_quality` is the answer to "can I trust the granular data for this day".
- `notebooks/k2lake.py`: `trades()` and `completeness()` print a one-line warning when the
  requested range includes a day that did not reconcile, and `day_quality()` returns the
  view. A test covers a clean range and a flagged one.
- The readiness scorecard gains one item for captured products: reconciliation is running.
- `scripts/chaos/reconcile-detects-gap.sh`: on a scratch copy of one day, remove a slice of
  trades, rebuild the day's bar, and assert the status is `volume_breach` or `count_breach`.

**Documents.** The benchmark; chapters 11 and 16; `docs/operations/data-catalog.md` through
its generator; the runbook.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test dbt_marts
make reconcile-bars
bash scripts/chaos/reconcile-detects-gap.sh
make check-alerts
make access-verify && bash scripts/entitlements-verify.sh
docker exec k2-trino trino --execute "SELECT exchange, status, count(*) FROM lake.marts.bar_reconciliation_daily GROUP BY 1, 2 ORDER BY 1, 2"
make gates
```
