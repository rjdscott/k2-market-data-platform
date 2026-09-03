"""
One connection to the lake for every notebook: DuckDB attached to the Lakekeeper
REST catalog, reading Parquet from MinIO, in UTC.

    from k2lake import connect
    con = connect()
    con.sql("SELECT count(*) FROM lake.gold.trades").show()

Runs on the HOST (not in a container), so the endpoints are the published ports:
Lakekeeper on localhost:18181 and MinIO on localhost:9000. The Iceberg metadata
holds `s3://k2-lake/...` locations; the S3 secret below tells DuckDB which
endpoint that bucket lives on. Credentials come from ../.env (MINIO_ROOT_USER /
MINIO_ROOT_PASSWORD), never from this file.

`SET TimeZone = 'UTC'` is load-bearing: Spark writes Iceberg TIMESTAMP as
timestamptz and DuckDB renders it in the session zone, which defaults to the
host's — on a UTC+10 machine every bucket lands ten hours late
(scripts/parity_ohlcv.py found this the hard way, 2026-08-27).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb

LAKEKEEPER = os.environ.get("K2_LAKEKEEPER_HOST_URL", "http://localhost:18181/catalog")
S3_ENDPOINT = os.environ.get("K2_S3_HOST_ENDPOINT", "localhost:9000")
SCALE = 100_000_000  # 1e-8 fixed point, the wire's and gold's representation


def _env() -> dict:
    """MINIO_* from the repo's .env when not already in the environment."""
    env = dict(os.environ)
    dotenv = Path(__file__).resolve().parent.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())
    return env


def connect() -> duckdb.DuckDBPyConnection:
    env = _env()
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute(
        f"""CREATE SECRET s3sec (TYPE S3, KEY_ID '{env["MINIO_ROOT_USER"]}', SECRET '{env["MINIO_ROOT_PASSWORD"]}',
            ENDPOINT '{S3_ENDPOINT}', URL_STYLE 'path', USE_SSL false, REGION 'local-01')"""
    )
    con.execute(
        f"ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT '{LAKEKEEPER}', AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"
    )
    return con


PINNED_NAMESPACES = ("gold", "silver", "audit")


def pin(con: duckdb.DuckDBPyConnection, namespaces=PINNED_NAMESPACES) -> dict:
    """Freeze the lake for this session: one view per table at its CURRENT snapshot.

    `pinned.gold_trades`, `pinned.silver_book_kraken`, `pinned.audit_checks` …
    each `SELECT * FROM lake.<ns>.<table> AT (VERSION => <id>)`, the id read
    once, here. Every notebook query goes through these, never through
    `lake.<ns>.<table>` (tests/test_notebooks_pinned.py greps for it), so a
    chart or a number a notebook printed names the snapshot it came from and
    re-running at that id gives the same answer while an ingest lands new rows
    beside it (ADR-029). Returns {table: snapshot_id} and prints it with the
    repo's commit, which is the provenance a shared result carries.
    """
    con.execute("CREATE SCHEMA IF NOT EXISTS pinned")
    tables = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        f"WHERE table_catalog = 'lake' AND table_schema IN ({', '.join(repr(n) for n in namespaces)}) ORDER BY 1, 2"
    ).fetchall()
    ids = {}
    for ns, table in tables:
        row = con.execute(f"SELECT snapshot_id FROM iceberg_snapshots(lake.{ns}.{table}) ORDER BY timestamp_ms DESC LIMIT 1").fetchone()
        if row is None:  # an empty table has no snapshot; the view reads it unpinned, and says so
            con.execute(f"CREATE OR REPLACE VIEW pinned.{ns}_{table} AS SELECT * FROM lake.{ns}.{table}")
            ids[f"{ns}.{table}"] = None
            continue
        con.execute(f"CREATE OR REPLACE VIEW pinned.{ns}_{table} AS SELECT * FROM lake.{ns}.{table} AT (VERSION => {row[0]})")
        ids[f"{ns}.{table}"] = row[0]
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip()
    except OSError:
        sha = "unknown"
    print(f"pinned {len(ids)} tables at commit {sha}")
    for name, snap in ids.items():
        print(f"  {name:<28} {snap if snap is not None else '(empty, unpinned)'}")
    return ids


# ── The quant surface ───────────────────────────────────────────────────────
# Table names are never taken from the caller: `source` picks one of two fixed
# maps. Every other value the caller supplies is a bound parameter.
_TABLES = {
    "pinned": {  # the snapshot-pinned views pin() creates — the notebook default (ADR-029)
        "trades": "pinned.gold_trades", "bbo": "pinned.gold_bbo_1s",
        "dim": "pinned.gold_dim_instrument", "book_kraken": "pinned.silver_book_kraken",
        "checks": "pinned.audit_checks",
    },
    "lake": {  # the moving head, for scripts outside a notebook
        "trades": "lake.gold.trades", "bbo": "lake.gold.bbo_1s",
        "dim": "lake.gold.dim_instrument", "book_kraken": "lake.silver.book_kraken",
        "checks": "lake.audit.checks",
    },
}


def _tables(source: str) -> dict:
    """`source` -> the table-name map. "pinned.gold_trades" also names "pinned"."""
    key = str(source).split(".")[0]
    if key not in _TABLES:
        raise ValueError(f"source must be 'pinned' or 'lake', got {source!r}")
    return _TABLES[key]


def _trades_sql(tb: dict, book: bool, master: bool) -> str:
    """The trades ⋈ bbo ⋈ dim_instrument SQL. Placeholders bind, in this order:
    symbol, exchange, start, end (trades), then symbol, exchange, end (bbo)."""
    sel = ["t.*"]
    joins = ""
    if book:
        sel += [
            "q.second AS quote_second",
            f"CAST(q.bid_e8 AS DECIMAL(28,10)) / {SCALE} AS bid",
            f"CAST(q.bid_qty_e8 AS DECIMAL(28,10)) / {SCALE} AS bid_qty",
            f"CAST(q.ask_e8 AS DECIMAL(28,10)) / {SCALE} AS ask",
            f"CAST(q.ask_qty_e8 AS DECIMAL(28,10)) / {SCALE} AS ask_qty",
            "q.mid", "q.spread_bps",
        ]
        # No lower bound on `second`: the ASOF takes the greatest second at or
        # before the trade, and clipping the scan to the window would silently
        # give the window's first trades no quote at all.
        # ponytail: scans the venue+symbol history of bbo_1s. Add a lower bound
        # if a query over a multi-day archive gets slow enough to measure.
        joins += f"""
        ASOF LEFT JOIN (SELECT * FROM {tb["bbo"]}
                        WHERE canonical_symbol = ? AND exchange = ? AND second < CAST(? AS TIMESTAMP)) q
          ON t.exchange = q.exchange AND t.canonical_symbol = q.canonical_symbol
         AND t.exchange_ts >= q.second + INTERVAL 1 SECOND"""
    if master:
        sel += [
            "d.symbol AS native_symbol", "d.tick_size", "d.qty_increment",
            "d.source AS master_source", "d.valid_from AS master_valid_from",
        ]
        joins += f"""
        ASOF LEFT JOIN {tb["dim"]} d
          ON t.exchange = d.exchange AND t.canonical_symbol = d.canonical_symbol
         AND t.exchange_ts >= d.valid_from"""
    return f"""
        SELECT {", ".join(sel)}
        FROM (SELECT * FROM {tb["trades"]}
              WHERE canonical_symbol = ? AND exchange = ?
                AND exchange_ts >= CAST(? AS TIMESTAMP) AND exchange_ts < CAST(? AS TIMESTAMP)) t
        {joins}
        ORDER BY t.exchange_ts, t.trade_seq"""


def trades(con, symbol: str, exchange: str, start, end, book: bool = True, master: bool = True, source: str = "pinned"):
    """Trades in [start, end) with the book in force and the security master as of each one.

    One row per trade, every `gold.trades` column plus:

      quote_second, bid, bid_qty, ask, ask_qty  the BBO in force, quote currency
                                                and base units (DECIMAL, not e8)
      mid, spread_bps                           as `gold.bbo_1s` computed them
      native_symbol, tick_size, qty_increment,  the `gold.dim_instrument` version
      master_source, master_valid_from          in force at exchange_ts

    **`gold.bbo_1s` is the book at the END of its second**, so the quote in force
    for a trade is the row for the *previous* second: the join is
    `exchange_ts >= second + INTERVAL 1 SECOND`. Joining on `second <= exchange_ts`
    uses a quote from up to a second in the trade's future: on Kraken BTC/USD,
    2026-09-03 12:00-13:00 UTC, that reads 64.74% of prints as trading through the
    book where the correct pairing reads 51.74% (both measured with this function's
    join, 1923 trades).

    Both joins are LEFT: nothing is dropped. A trade with no quote (no book
    frames that second) has NULL bid/ask; a trade older than the dimension's
    first version has NULL master columns and prints a warning on stderr —
    `tick_size`/`qty_increment` are Kraken-only anyway (ADR-030).

    Units: `exchange_ts` and `quote_second` are UTC TIMESTAMPs; `start`/`end` are
    anything DuckDB casts to TIMESTAMP and are read as UTC; prices and quantities
    stay as the table's e8 fixed point (`price_e8`, `qty_e8`) while the joined
    book columns are decimals (e8 / SCALE). `symbol` is the canonical BASE/QUOTE,
    `exchange` one of binance | kraken | coinbase.

    `source` is "pinned" (the views `pin(con)` creates — call it first) or "lake".

        trades(con, "BTC/USD", "kraken", "2026-09-03 04:00", "2026-09-03 05:00")
    """
    tb = _tables(source)
    params = [symbol, exchange, start, end] + ([symbol, exchange, end] if book else [])
    rel = con.sql(_trades_sql(tb, book, master), params=params)
    if master:
        # ponytail: this runs the join a second time. Cheap at one hour; if it
        # ever isn't, pass master=False and check coverage yourself.
        n, missing = rel.aggregate(
            "count(*), count(*) FILTER (WHERE master_valid_from IS NULL)"
        ).fetchone()
        if missing:
            print(f"{missing} of {n} trades have no dim_instrument version; see ADR-030", file=sys.stderr)
    return rel


def completeness(con, symbol: str, exchange: str, start, end, source: str = "pinned"):
    """One row: is [start, end) of this symbol on this venue whole, and how would we know.

        trades                how many landed
        minutes_with_trades   distinct UTC minutes that carry at least one
        minutes_expected      minutes in the window. A venue is not obliged to
                              print every minute — this is the denominator, not a target
        seq_gaps              trades whose `seq_gap` is true: a hole in the venue's
                              trade-id sequence immediately before them
        ids_never_received    sum of `missing_before` — trade ids the archive never saw
        quote_coverage_pct    share of trades that found a bbo (the +1 s ASOF of
                              `trades()`), 2 dp
        checksum_failed       `silver.book_<venue>` rows with checksum_ok = false whose
                              `recv_ts` falls in the window. **Kraken only** — Binance
                              and Coinbase publish no book checksum, so this is NULL
                              for them, which means "not measurable", not "clean"
        audit_failures        `audit.checks` rows with passed = false whose `run_ts`
                              falls in the window. audit.checks stamps one run time, not
                              a range, so this is the checks that RAN in the window, not
                              the checks that cover data in it

    All timestamps UTC. `source` as in `trades()`. Nothing here fails a window:
    it reports, and NULL always means the signal does not exist for this venue.
    """
    tb = _tables(source)
    params = [symbol, exchange, start, end, symbol, exchange, end]
    if exchange == "kraken":
        checksum = f"""(SELECT count(*) FROM {tb["book_kraken"]}
                        WHERE canonical_symbol = ? AND checksum_ok = false
                          AND recv_ts >= CAST(? AS TIMESTAMP) AND recv_ts < CAST(? AS TIMESTAMP))"""
    else:
        checksum = "CAST(NULL AS BIGINT)"
    sql = f"""
        WITH tq AS ({_trades_sql(tb, book=True, master=False)}),
        agg AS (
          SELECT count(*) AS trades,
                 count(DISTINCT date_trunc('minute', exchange_ts)) AS minutes_with_trades,
                 CAST(coalesce(sum(CASE WHEN seq_gap THEN 1 ELSE 0 END), 0) AS BIGINT) AS seq_gaps,
                 CAST(coalesce(sum(missing_before), 0) AS BIGINT) AS ids_never_received,
                 round(100.0 * coalesce(avg(CASE WHEN quote_second IS NULL THEN 0 ELSE 1 END), 0), 2) AS quote_coverage_pct
          FROM tq)
        SELECT trades, minutes_with_trades,
               CAST(date_diff('minute', CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP)) AS BIGINT) AS minutes_expected,
               seq_gaps, ids_never_received, quote_coverage_pct,
               {checksum} AS checksum_failed,
               (SELECT count(*) FROM {tb["checks"]} WHERE NOT passed
                  AND run_ts >= CAST(? AS TIMESTAMP) AND run_ts < CAST(? AS TIMESTAMP)) AS audit_failures
        FROM agg"""
    params += [start, end]
    if exchange == "kraken":
        params += [symbol, start, end]
    params += [start, end]
    return con.sql(sql, params=params)


BAR_KINDS = ("tick", "volume", "dollar")


def bars(con, kind: str, threshold, symbol: str | None = None, exchange: str | None = None, start=None, end=None, source: str = "pinned"):
    """Event bars at ANY threshold, computed over lake.gold.trades in DuckDB.

    The same cumulative-bucket definition as the materialised `lake.gold.bars`
    (docker/lake/bars.py, one canonical threshold per symbol from
    config/bars.yaml): a trade belongs to bar k of its UTC day when
    k*T <= (day's total before it) < (k+1)*T in (exchange_ts, recv_ts_ns,
    trade_seq) order. `threshold` is trades (tick), base units (volume) or
    quote-currency notional (dollar). Returns a DuckDB relation with the
    table's columns; `.df()` / `.pl()` / `.show()` as you like. `source` is the
    pinned view by default (call `pin(con)` first); pass "lake" for an unpinned
    read outside a notebook.

    `start` / `end` must be UTC-day boundaries: the cumulative grid restarts at
    each day's first trade, so a mid-day `start` re-bases bar 0 there and the
    bars are not the ones `lake.gold.bars` holds for that day.

        bars(con, "dollar", 1_000_000, symbol="BTC/USD", start="2026-08-27")
    """
    if kind not in BAR_KINDS:
        raise ValueError(f"kind must be one of {BAR_KINDS}, got {kind!r}")
    where, params = ["TRUE"], []
    for expr, value in (("canonical_symbol = ?", symbol), ("exchange = ?", exchange),
                        ("exchange_ts >= CAST(? AS TIMESTAMP)", start), ("exchange_ts < CAST(? AS TIMESTAMP)", end)):
        if value:
            where.append(expr)
            params.append(value)
    t = float(threshold)
    bucket = {
        "tick": f"n_before // CAST({t} AS BIGINT)",
        "volume": f"vol_before // CAST({t} * {SCALE} AS BIGINT)",
        "dollar": f"usd_before // (CAST(CAST({t} * {SCALE} AS BIGINT) AS HUGEINT) * CAST({SCALE} AS HUGEINT))",
    }[kind]
    sql = f"""
        WITH t AS (
          SELECT exchange, canonical_symbol, CAST(exchange_ts AS DATE) AS day, exchange_ts, recv_ts_ns, trade_seq,
                 price_e8, qty_e8, CAST(price_e8 AS HUGEINT) * CAST(qty_e8 AS HUGEINT) AS notional_e16
          FROM {_tables(source)["trades"]} WHERE {" AND ".join(where)}
        ),
        c AS (
          SELECT *, (sum(qty_e8) OVER w) - qty_e8 AS vol_before, (sum(notional_e16) OVER w) - notional_e16 AS usd_before,
                 (count(*) OVER w) - 1 AS n_before
          FROM t WINDOW w AS (PARTITION BY exchange, canonical_symbol, day ORDER BY exchange_ts, recv_ts_ns, trade_seq
                              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        ),
        k AS (SELECT *, CAST({bucket} AS INT) AS bar_seq FROM c)
        SELECT exchange, canonical_symbol, '{kind}' AS bar_kind, {t} AS threshold, day, bar_seq,
               arg_min(price_e8, ROW(exchange_ts, recv_ts_ns, trade_seq)) AS open_e8, max(price_e8) AS high_e8,
               min(price_e8) AS low_e8, arg_max(price_e8, ROW(exchange_ts, recv_ts_ns, trade_seq)) AS close_e8,
               CAST(sum(qty_e8) AS BIGINT) AS volume_e8, CAST(sum(notional_e16) // {SCALE} AS BIGINT) AS quote_volume_e8,
               count(*) AS trade_count, min(exchange_ts) AS open_time, max(exchange_ts) AS close_time
        FROM k GROUP BY ALL ORDER BY exchange, canonical_symbol, day, bar_seq
    """
    return con.sql(sql, params=params)
