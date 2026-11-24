# Phase V1: reconciliation and the day-level trust signal

**Depends on:** V0; P3 and P6 of [the governed self-serve platform plan](../2026-11-24-governed-self-serve-platform/README.md) (dbt, quality results, the scorecard, the Grafana quality dashboard)
**Decision it implements:** ADR-043, which this phase completes with the comparison rules as measured.
**Delivers:** a daily comparison of K2's captured bars with each venue's own, published on the platform as a product; a status for every captured day that says what kind of day it was; an alert when a fully covered day disagrees; and that status surfaced where people already work.
**Exit:** the reconciliation product is in the catalog with owner, contract and lineage from both inputs; on a scratch copy of a fully covered day, removing one trade is reported as a count breach; a day on which capture was stopped is reported as partial and raises no alert; today's unfinished day is reported as pending; `delayed_reader` can read the quality view; no result of any comparison is in the repository.

## What can and cannot be compared

| Venue | Compared exactly | Not comparable | Evidence used instead |
|---|---|---|---|
| Binance | open, high, low, close, base volume, quote volume, trade count | | |
| Kraken | open, high, low, close, base volume, trade count | quote volume (not published) | |
| Coinbase | high and low, one-sided: a captured high below the venue's, or a captured low above it, means a trade was missed | open, close, volume (its candle is not the sum of its own trades), trade count (not published) | trade-id gaps from the lake's own audit |

Tolerance is zero wherever a field is compared. A tolerance wide enough to absorb a venue's
noise is also wide enough to hide a capture gap: the gaps K2 has actually recorded were a
few dozen trades in a day of millions. These rules are confirmed or corrected by the first
task below, and live in one place, the `bar_reconciliation` contract.

The lake's own gold bars are what is compared. ClickHouse candles are covered through the
existing three-way parity check, and the product page says so.

## Scope

**Pull request 1: coverage, the comparison, the rules as measured**

- First task: per-day capture coverage. For each exchange, symbol and day, the share of the
  day covered by a live capture connection, from the connection spans the bronze trade
  tables already carry and the gap rows in `audit.checks`; plus that day's sequence gaps and
  trade ids never received, the same figures `k2lake.completeness()` computes. Built as a dbt
  model, `captured_day_coverage`, with unit tests.
- Second task: run the comparison over every fully covered captured day and look at the
  result on the platform before any rule is fixed. The script and its query are committed;
  the figures are not. ADR-043 records what was found in words and names the query.
- dbt model `bar_reconciliation_daily`, in `marts`, grain exchange, symbol, day. It joins
  `gold.ohlcv_1d`, `silver.venue_ohlcv_1d` and coverage, bounded per symbol to the days from
  its first captured day to the last completed day. It is the only place the two paths meet.
  - Per field: captured value, venue value, signed difference, and a field status (`equal`,
    `differs`, `not_published`, `not_comparable`). The sign matters: a captured high below
    the venue's is a missed extreme; above it is a trade outside the venue's day.
  - Per row: `coverage`, the gap counts, the `gold.ohlcv_1d` snapshot id that was read, and
    a day state:
    - `pending`: the day is not closed, or the ingest has not yet passed its end;
    - `reference_unavailable`: the venue's bar has not landed;
    - `partial_capture`: coverage below 100 percent. Expected on a stack that was off;
    - `complete`: full coverage, no sequence gap, no missing trade id, every comparable
      field equal;
    - `aggregates_agree`: every comparable field equal, but the gap evidence is incomplete
      (for example the first day after a long stop);
    - `breach`: full coverage and a comparable field differs.
- When it runs: after `lake_maintenance`, from 03:30 UTC, for days whose end the ingest has
  passed by thirty minutes. A bar that changes later is evaluated again, and the latest
  evaluation is the status.
- dbt unit tests with hand-built rows for every field status and every day state, including
  a venue that does not publish a field and a Coinbase day.

**Pull request 2: reconciliation as a product, in the catalog**

- Product `contracts/market-analytics/bar_reconciliation/`: descriptor and two outputs.
  - `bar_reconciliation_daily`: the full comparison.
  - `captured_day_quality`: exchange, symbol, day, day state, coverage, gap counts, and each
    field's status and signed difference. This is the answer to "can I trust the granular
    data for this day".
- Both are classified `internal` with entitlement `delayed`, so both reader roles can read
  them through Trino, and the notebook helper and the open catalog endpoint reach them like
  any table. No exception to the generator's classification rule is needed.
- The generated product page is static: what is compared for each venue, what is not and
  why, the day states and what each means, and the query that returns the live record. It
  holds no results.
- Every captured product's page (`gold.trades`, the `gold.ohlcv_*` tables, `gold.bars`)
  gains, in its "Quality and known limitations" section, the same static facts and the
  query for its own record.
- With the `governance` profile up: the product under its domain in OpenMetadata, lineage
  from `gold.ohlcv_1d` and `silver.venue_ohlcv_1d`, and each day's result as a quality
  result on the captured table it concerns.
- `make access-verify` covers both outputs.

**Pull request 3: results, alert and dashboard**

- After each `dbt_marts` run, a Spark task appends one `audit.checks` row per evaluated
  exchange, symbol and day (`job = 'reconcile'`) and writes the count of breaches into the
  snapshot summary. `governance.dq_results`, the exporter and the alert read what they
  already read; nothing new learns to query a mart.
- Alert `CapturedBarDisagreesWithVenue`, on a breach only, never on a partial or pending
  day, with a promtool test and a runbook via `/runbook`: how to tell a capture gap from a
  venue restatement, using the trade-id and sequence-gap evidence.
- The Grafana quality dashboard from P6 gains a reconciliation row: day state by venue and
  symbol over time, coverage, and breaches.
- The readiness scorecard gains one item for captured products: reconciliation is running.
- `make reconcile-bars`: evaluates the last completed day on a live stack and exits non-zero
  on a breach, zero on partial or pending. It is not part of `/release-check`: a fresh clone
  has no captured day to compare. The fresh-clone gate is the dbt unit tests.
- `scripts/chaos/reconcile-detects-gap.sh`: on a scratch copy of one fully covered Binance
  day, remove one trade that is not the day's extreme, rebuild the bar, and assert the trade
  count field differs and the day state is `breach`.

**Pull request 4: the trust signal where people work**

- `notebooks/k2lake.py`: `trades()` and `completeness()` print one line when the requested
  range includes a day that is not `complete`, naming the state; `day_quality()` returns the
  view. Tests cover a clean range, a partial day and a breach.
- The claim made to users is exact and limited, and the page states it: `complete` means the
  day's trade set is whole as far as two independent records can show. It says nothing
  about any single minute on a day that is not `complete`.

**Documents.** ADR-043 completed; chapters 11 and 16; `docs/operations/data-catalog.md`
through its generator; the runbook.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test dbt_marts
make reconcile-bars
bash scripts/chaos/reconcile-detects-gap.sh
make check-alerts
make access-verify && bash scripts/entitlements-verify.sh
docker exec k2-trino trino --execute "SELECT exchange, day_state, count(*) FROM lake.marts.captured_day_quality GROUP BY 1, 2 ORDER BY 1, 2"
git status --porcelain docs/        # empty: no result was written into the repository
make gates
```
