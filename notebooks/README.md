# notebooks: the research surface

DuckDB on the host, attached to the lake's REST catalog (Lakekeeper) and reading Parquet
from MinIO through the published ports. No query service, no copy of the data: the
notebooks read the same Iceberg tables the audits do (ADR-018, ADR-026).

```bash
make notebooks            # uv sync, then JupyterLab on http://localhost:8889
make notebooks-run        # execute all five headless (nbconvert), the runnable check
```

| Notebook | Question it answers | Tables |
|---|---|---|
| `01_connect.ipynb` | is the lake reachable, what is in it, how big | every layer via the catalog |
| `02_book_at_time.ipynb` | what did the BTC book look like at one second, on every venue | `gold.book_top20`, `gold.bbo_1s` |
| `03_asof_trades_book.ipynb` | for each trade, the BBO in force when it printed, DuckDB `ASOF JOIN`; trade-through and effective-spread; then the instrument's attributes as they stood at that trade | `gold.trades`, `gold.bbo_1s`, `gold.dim_instrument` |
| `05_bars.ipynb` | the event-bar catalogue (`gold.bars`: tick/volume/dollar at one canonical threshold per symbol) and `k2lake.bars()` for any other threshold; bar duration vs activity; the function reproduces the catalogue row for row | `gold.bars`, `gold.trades` |
| `04_completeness_audit.ipynb` | what the archive is missing and why: trade-id holes, venue replays, checksum failures by hour, offset gaps | `silver.trades_*`, `silver.book_kraken`, `audit.checks` |

What each table holds — grain, time columns and their units, symbol conventions, the
`+1 SECOND` book rule, and where the lake and ClickHouse disagree — is
[`docs/operations/data-catalog.md`](../docs/operations/data-catalog.md). Read it before
writing a query against a table you have not used.

`k2lake.py` is the one connection; read its docstring for the two things that bite
(host endpoints, `SET TimeZone = 'UTC'`). Credentials come from `../.env`. Every notebook's
first cell calls `pin(con)`: one `pinned.<ns>_<table>` view per gold, silver and audit table
at its current snapshot id, printed with the commit, and nothing below reads `lake.*`
directly (`tests/test_notebooks_pinned.py` fails CI if one does). A number a notebook
prints therefore names the snapshot it came from ([ADR-029](../docs/adr/ADR-029-research-production-parity-contract.md)).
What this data can and cannot honestly support is written down before any of it is
quoted: [replay-fidelity-limits](../docs/research/2026-08-28-replay-fidelity-limits.md).

## The quant story in three lines

"Kraken BTC/USD, one hour: every trade with the top of book in force when it printed, the
security master as it stood at that trade, and is the hour complete." Two helpers, not
eighteen lines of triple-ASOF SQL:

```python
from k2lake import connect, pin, trades, completeness
con = connect(); pin(con)
t = trades(con, 'BTC/USD', 'kraken', '2026-09-03 12:00', '2026-09-03 13:00')   # 31 columns
t.project('exchange_ts, side, price_e8/1e8 AS price, bid, ask, quote_second, '
          'native_symbol, tick_size, master_source').limit(3).show(max_width=170)
```

```
┌───────────────────────────────┬─────────┬─────────┬─────────┬─────────┬──────────────────────────┬───────────────┬────────────────┬───────────────┐
│          exchange_ts          │  side   │  price  │   bid   │   ask   │       quote_second       │ native_symbol │   tick_size    │ master_source │
│   timestamp with time zone    │ varchar │ double  │ double  │ double  │ timestamp with time zone │    varchar    │ decimal(28,10) │    varchar    │
├───────────────────────────────┼─────────┼─────────┼─────────┼─────────┼──────────────────────────┼───────────────┼────────────────┼───────────────┤
│ 2026-09-03 12:47:04.413735+00 │ buy     │ 78327.9 │ 78327.8 │ 78327.9 │ 2026-09-03 12:47:03+00   │ BTC/USD       │   0.1000000000 │ venue:kraken  │
│ 2026-09-03 12:47:05.425627+00 │ buy     │ 78331.7 │ 78331.6 │ 78331.7 │ 2026-09-03 12:47:04+00   │ BTC/USD       │   0.1000000000 │ venue:kraken  │
│ 2026-09-03 12:47:05.425627+00 │ buy     │ 78331.7 │ 78331.6 │ 78331.7 │ 2026-09-03 12:47:04+00   │ BTC/USD       │   0.1000000000 │ venue:kraken  │
└───────────────────────────────┴─────────┴─────────┴─────────┴─────────┴──────────────────────────┴───────────────┴────────────────┴───────────────┘
```

`quote_second` is one to two seconds behind `exchange_ts` on purpose: **`gold.bbo_1s` is the
book at the END of its second**, so the quote in force is the previous second's row and the
join is `exchange_ts >= second + INTERVAL 1 SECOND`. Getting that wrong pairs a trade with a
quote from its own future — on this hour it reads 64.74% of prints as trading through the
book where the correct pairing reads 51.74%. The rule lives in `trades()` now, not in a
markdown cell. Both joins are LEFT: a trade with no book that second, or one older than the
dimension's first version, still comes back, with NULLs and a warning on stderr.

```python
completeness(con, 'BTC/USD', 'kraken', '2026-09-03 12:00', '2026-09-03 13:00').show()
```

```
┌────────┬─────────────────────┬──────────────────┬──────────┬────────────────────┬────────────────────┬─────────────────┬────────────────┐
│ trades │ minutes_with_trades │ minutes_expected │ seq_gaps │ ids_never_received │ quote_coverage_pct │ checksum_failed │ audit_failures │
│ int64  │        int64        │      int64       │  int64   │       int64        │       double       │      int64      │     int64      │
├────────┼─────────────────────┼──────────────────┼──────────┼────────────────────┼────────────────────┼─────────────────┼────────────────┤
│   1923 │                  13 │               60 │        0 │                  0 │              100.0 │               0 │             31 │
└────────┴─────────────────────┴──────────────────┴──────────┴────────────────────┴────────────────────┴─────────────────┴────────────────┘
```

Read that as: 1923 trades over 13 of the hour's 60 minutes (Kraken BTC/USD is thin, and the
capture only started at 12:46 in this window), no trade-id holes, every trade found a quote.
`checksum_failed` is Kraken-only — Binance and Coinbase publish no book checksum, so it is
`NULL` there, which means *not measurable*, never *clean*. `audit_failures` counts
`audit.checks` rows that RAN in the window, not checks that cover data in it: that table
stamps one `run_ts`, not a range. Both docstrings say which signal is missing per venue.

Both helpers bind every caller value as a DuckDB parameter and take table names only from a
fixed allow-list (`source="pinned"`, the default, or `"lake"`), so a symbol with a quote in
it is a zero-row answer rather than a SQL error.

Import works from `notebooks/` (`cd notebooks && uv run python`, or the JupyterLab in
`make notebooks`); from anywhere else, `PYTHONPATH=notebooks`. `make test-notebooks` runs
`test_k2lake.py` against synthetic DuckDB tables — no stack, no credentials.

## The dimension is SCD2 — join it as of the trade

`gold.dim_instrument` holds one row per validity interval, not one per instrument
([ADR-030](../docs/adr/ADR-030-scd2-security-master.md)). `SELECT * FROM pinned.gold_dim_instrument`
returns history; the current slice is `WHERE is_current`, and the attributes in force when a
trade printed are an `ASOF JOIN` on `valid_from`:

```sql
SELECT t.exchange, t.canonical_symbol, t.exchange_ts, d.symbol AS native, d.tick_size, d.source
FROM pinned.gold_trades t
ASOF JOIN pinned.gold_dim_instrument d
  ON t.exchange = d.exchange AND t.canonical_symbol = d.canonical_symbol
 AND t.exchange_ts >= d.valid_from
```

Open rows carry `valid_to = 9999-12-31 23:59:59`, never `NULL`, so the hand-written form —
`… AND t.exchange_ts >= d.valid_from AND t.exchange_ts < d.valid_to` — returns the same rows.
With a `NULL` upper bound it would silently drop every current version, because `ts < NULL` is
not `TRUE`. `03_asof_trades_book.ipynb` runs both and compares the counts.

`tick_size`, `qty_increment` and the precisions are populated for Kraken only, from
`bronze.kraken_instrument`; Binance and Coinbase publish them over REST that K2 does not
capture, so they are `NULL` there and `source` says which authority the version came from.
A key's FIRST version opens at `1970-01-01`, not at the run that discovered it: the registry
asserts what an instrument *is*, and that was true before K2 recorded it, so no trade is ever
older than its dimension row. `recorded_at` dates when K2 learned it — on those seed rows
`valid_from < recorded_at`, and the gap is the history nothing observed
([ADR-030](../adr/ADR-030-scd2-security-master.md) Outcome). Later versions open at their run.

Numbers printed by these notebooks are not published anywhere else; the published ones are
in `docs/benchmarks/`. A notebook cell is a query, not a claim.
