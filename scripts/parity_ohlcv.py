#!/usr/bin/env python3
"""
Three-way OHLCV parity at a pinned lake snapshot — the Phase E exit gate and the
seed of the Phase G CI parity job (plan 004).

    uv run --no-project --with duckdb==1.4.4 --with clickhouse-connect \
        python scripts/parity_ohlcv.py --day 2026-08-27 \
        --trades-snapshot <lake.gold.trades snapshot id> --ohlcv-snapshot <lake.gold.ohlcv_1m snapshot id>

Three computations of the same 1-minute candles for one UTC day, compared at
tolerance ZERO on open/high/low/close (1e-8 fixed point), trade_count and volume:

  A  ClickHouse  gold.ohlcv_live(bucket = 60)      computed on read over gold.trades FINAL
  B  lake        gold.ohlcv_1m                     materialised by docker/lake/gold.py, read
                                                    by DuckDB at the pinned snapshot
  C  DuckDB      over lake.silver.trades_*         dedup applied in the query
                                                    (venue_replay = false), at the pinned
                                                    silver snapshots gold.trades recorded

Pinned, never `latest`: a parity check that reads whatever is current cannot be
re-run to the same answer, and a check that cannot be re-run is not a check.
`tests/parity/pinned.json` holds the ids of the last run this passed against.

Exit 0 when A == B == C on every bucket of the day, non-zero with the first
differences printed otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import duckdb

LAKEKEEPER = os.environ.get("K2_LAKEKEEPER_HOST_URL", "http://localhost:18181/catalog")
S3_ENDPOINT = os.environ.get("K2_S3_HOST_ENDPOINT", "localhost:9000")
CH_HOST = os.environ.get("K2_CLICKHOUSE_HOST", "localhost")
SCALE = 100_000_000

KEY = ["exchange", "canonical_symbol", "window_start"]
COLS = ["open_e8", "high_e8", "low_e8", "close_e8", "trade_count", "volume"]


def duck(minio_user: str, minio_pass: str) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    # Spark writes Iceberg TIMESTAMP as timestamptz; DuckDB renders those in the
    # session time zone, which defaults to the HOST's. On a UTC+10 machine the
    # first run of this script bucketed every trade ten hours late and disagreed
    # with ClickHouse on all 29,407 buckets (2026-08-27). UTC, explicitly.
    c.execute("SET TimeZone = 'UTC'")
    c.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    c.execute(
        f"""CREATE SECRET s3sec (TYPE S3, KEY_ID '{minio_user}', SECRET '{minio_pass}',
            ENDPOINT '{S3_ENDPOINT}', URL_STYLE 'path', USE_SSL false, REGION 'local-01')"""
    )
    # Spike S10 variant A: no auth, no access delegation (Lakekeeper vends no credentials here).
    c.execute(
        f"ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT '{LAKEKEEPER}', AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"
    )
    return c


def lake_candles(c, day: str, snapshot: int) -> list:
    """B: the materialised 1m candles, at the pinned snapshot."""
    return c.execute(
        f"""SELECT exchange, canonical_symbol, window_start, open_e8, high_e8, low_e8, close_e8, trade_count, volume
            FROM lake.gold.ohlcv_1m AT (VERSION => {snapshot})
            WHERE CAST(window_start AS DATE) = DATE '{day}'
            ORDER BY 1, 2, 3"""
    ).fetchall()


def silver_candles(c, day: str, snapshots: dict) -> list:
    """C: candles from silver, dedup in the query, at the pinned silver snapshots."""
    parts = []
    for venue, snap in snapshots.items():
        parts.append(
            f"""SELECT '{venue}' AS exchange, canonical_symbol, trade_id, trade_seq, price, qty, exchange_ts, recv_ts_ns
                FROM lake.silver.trades_{venue} AT (VERSION => {snap}) WHERE NOT venue_replay"""
        )
    union = " UNION ALL ".join(parts)
    return c.execute(
        f"""WITH t AS ({union}),
            b AS (SELECT exchange, canonical_symbol, date_trunc('minute', exchange_ts) AS window_start,
                         CAST(price * {SCALE} AS BIGINT) AS p8, qty, exchange_ts, recv_ts_ns, trade_seq
                  FROM t WHERE CAST(exchange_ts AS DATE) = DATE '{day}')
            SELECT exchange, canonical_symbol, window_start,
                   arg_min(p8, ROW(exchange_ts, recv_ts_ns, trade_seq)) AS open_e8, max(p8) AS high_e8, min(p8) AS low_e8,
                   arg_max(p8, ROW(exchange_ts, recv_ts_ns, trade_seq)) AS close_e8, count(*) AS trade_count,
                   CAST(sum(qty) AS DECIMAL(38,10)) AS volume
            FROM b GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
    ).fetchall()


def clickhouse_candles(day: str, password: str) -> list:
    """A: ClickHouse's on-read view over gold.trades FINAL (the topic-fed copy)."""
    import clickhouse_connect

    client = clickhouse_connect.get_client(host=CH_HOST, port=8123, username="default", password=password)
    rows = client.query(
        f"""SELECT exchange, canonical_symbol, window_start, open_e8, high_e8, low_e8, close_e8, trade_count, volume
            FROM gold.ohlcv_live(bucket = 60)
            WHERE toDate(window_start) = toDate('{day}')
            ORDER BY 1, 2, 3"""
    ).result_rows
    return rows


def normalise(rows: list) -> dict:
    out = {}
    for r in rows:
        exchange, symbol, ws = r[0], r[1], r[2]
        key = (exchange, symbol, ws.replace(tzinfo=None) if hasattr(ws, "replace") else ws)
        out[key] = tuple(int(x) if i < 5 else round(float(x), 8) for i, x in enumerate(r[3:9]))
    return out


def diff(name_a: str, a: dict, name_b: str, b: dict, limit: int = 5) -> int:
    keys = sorted(set(a) | set(b))
    bad = [k for k in keys if a.get(k) != b.get(k)]
    for k in bad[:limit]:
        print(f"  {name_a} vs {name_b} {k}: {a.get(k)} != {b.get(k)}")
    print(f"{name_a} vs {name_b}: {len(keys)} buckets, {len(bad)} differ")
    return len(bad)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    ap.add_argument("--ohlcv-snapshot", type=int, required=True, help="lake.gold.ohlcv_1m snapshot id")
    ap.add_argument("--silver-snapshots", required=True, help='JSON: {"binance": id, "kraken": id, "coinbase": id} — the k2.src-snapshot-id.<venue> of the gold.trades snapshot')
    ap.add_argument("--skip-clickhouse", action="store_true", help="two-way only: lake vs DuckDB-over-silver")
    ap.add_argument("--write-pin", action="store_true", help="on success, record the ids in tests/parity/pinned.json")
    args = ap.parse_args()

    silver_snaps = json.loads(args.silver_snapshots)
    c = duck(os.environ["MINIO_ROOT_USER"], os.environ["MINIO_ROOT_PASSWORD"])
    b = normalise(lake_candles(c, args.day, args.ohlcv_snapshot))
    cc = normalise(silver_candles(c, args.day, silver_snaps))
    bad = diff("lake.gold.ohlcv_1m", b, "duckdb-over-silver", cc)
    if not args.skip_clickhouse:
        a = normalise(clickhouse_candles(args.day, os.environ["CLICKHOUSE_PASSWORD"]))
        bad += diff("clickhouse.ohlcv_live", a, "lake.gold.ohlcv_1m", b)
    if bad == 0 and args.write_pin:
        Path("tests/parity/pinned.json").write_text(
            json.dumps({"day": args.day, "ohlcv_1m_snapshot": args.ohlcv_snapshot, "silver_snapshots": silver_snaps}, indent=2) + "\n"
        )
    print("PARITY: " + ("PASS" if bad == 0 else "FAIL"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
