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
| `03_asof_trades_book.ipynb` | for each trade, the BBO in force when it printed, DuckDB `ASOF JOIN`; trade-through and effective-spread | `gold.trades`, `gold.bbo_1s` |
| `05_bars.ipynb` | the event-bar catalogue (`gold.bars`: tick/volume/dollar at one canonical threshold per symbol) and `k2lake.bars()` for any other threshold; bar duration vs activity; the function reproduces the catalogue row for row | `gold.bars`, `gold.trades` |
| `04_completeness_audit.ipynb` | what the archive is missing and why: trade-id holes, venue replays, checksum failures by hour, offset gaps | `silver.trades_*`, `silver.book_kraken`, `audit.checks` |

`k2lake.py` is the one connection; read its docstring for the two things that bite
(host endpoints, `SET TimeZone = 'UTC'`). Credentials come from `../.env`.

Numbers printed by these notebooks are not published anywhere else; the published ones are
in `docs/benchmarks/`. A notebook cell is a query, not a claim.
