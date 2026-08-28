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


BAR_KINDS = ("tick", "volume", "dollar")


def bars(con, kind: str, threshold, symbol: str | None = None, exchange: str | None = None, start=None, end=None, source: str = "pinned.gold_trades"):
    """Event bars at ANY threshold, computed over lake.gold.trades in DuckDB.

    The same cumulative-bucket definition as the materialised `lake.gold.bars`
    (docker/lake/bars.py, one canonical threshold per symbol from
    config/bars.yaml): a trade belongs to bar k of its UTC day when
    k*T <= (day's total before it) < (k+1)*T in (exchange_ts, recv_ts_ns,
    trade_seq) order. `threshold` is trades (tick), base units (volume) or
    quote-currency notional (dollar). Returns a DuckDB relation with the
    table's columns; `.df()` / `.pl()` / `.show()` as you like. `source` is the
    pinned view by default (call `pin(con)` first); pass `lake.gold.trades`
    for an unpinned read outside a notebook.

        bars(con, "dollar", 1_000_000, symbol="BTC/USD", start="2026-08-27")
    """
    if kind not in BAR_KINDS:
        raise ValueError(f"kind must be one of {BAR_KINDS}, got {kind!r}")
    where = ["TRUE"]
    if symbol:
        where.append(f"canonical_symbol = '{symbol}'")
    if exchange:
        where.append(f"exchange = '{exchange}'")
    if start:
        where.append(f"exchange_ts >= TIMESTAMP '{start}'")
    if end:
        where.append(f"exchange_ts < TIMESTAMP '{end}'")
    t = float(threshold)
    bucket = {
        "tick": f"n_before // CAST({t} AS BIGINT)",
        "volume": f"vol_before // CAST({t} * {SCALE} AS BIGINT)",
        "dollar": f"usd_before // (CAST({t} AS HUGEINT) * {SCALE} * {SCALE})",
    }[kind]
    sql = f"""
        WITH t AS (
          SELECT exchange, canonical_symbol, CAST(exchange_ts AS DATE) AS day, exchange_ts, recv_ts_ns, trade_seq,
                 price_e8, qty_e8, CAST(price_e8 AS HUGEINT) * CAST(qty_e8 AS HUGEINT) AS notional_e16
          FROM {source} WHERE {" AND ".join(where)}
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
    return con.sql(sql)
