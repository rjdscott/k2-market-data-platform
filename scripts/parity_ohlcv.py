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

`--dbt` adds a fourth, compared with B on the same columns and on quote_volume:

  D  dbt on Trino  the compiled SQL of dbt's market_analytics.candles.ohlcv_1m,
                   its staging view inlined and lake.gold.trades read FOR VERSION
                   AS OF the newest gold.trades snapshot B's rows for the day
                   were computed from (their src_snapshot_id)

D is the SQL dbt builds the mart with, run on the trades Spark last read for the
day, not the table dbt last built: that table holds the lake as it was at build
time. `--skip-silver` and `--skip-clickhouse` leave B against D alone.

Exit 0 when every side run equals B on every bucket of the day, non-zero with the
first differences printed otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

LAKEKEEPER = os.environ.get("K2_LAKEKEEPER_HOST_URL", "http://localhost:18181/catalog")
S3_ENDPOINT = os.environ.get("K2_S3_HOST_ENDPOINT", "localhost:9000")
CH_HOST = os.environ.get("K2_CLICKHOUSE_HOST", "localhost")
TRINO_HOST = os.environ.get("K2_TRINO_HOST", "localhost")
TRINO_PORT = int(os.environ.get("K2_TRINO_PORT", "8088"))
DBT = Path(__file__).resolve().parent.parent / "dbt"
DBT_MODEL = "model.k2.ohlcv_1m"
DBT_TRADES = "source.k2.gold.trades"
SCALE = 100_000_000

KEY = ["exchange", "canonical_symbol", "window_start"]
COLS = ["open_e8", "high_e8", "low_e8", "close_e8", "trade_count", "volume"]


def resolve_snapshot(query, table: str, pinned: int) -> int:
    """The pinned snapshot if `table` still holds it, else the current one, said out loud.

    A pin written on another host — or one `expire_snapshots` has since dropped —
    is not in this table, and reading `AT (VERSION => <pin>)` raised
    `Could not find snapshot with id ...`: the release gate ADR-029 names crashed
    instead of reporting anything, every run, from 2026-08-27 to 2026-09-03.

    Falling back *silently* would be worse than the crash — the gate would print
    PASS at a snapshot nobody pinned and nobody could re-run. So: fall back, name
    both ids on stdout, and leave the exit code to parity. A stale pin degrades
    the check from reproducible to current; it never turns a FAIL into a PASS.

    `query` is `lambda sql: conn.execute(sql).fetchall()` — a callable so the
    decision is testable without DuckDB or a catalog (tests/test_parity.py).
    """
    rows = query(f"SELECT sequence_number, snapshot_id FROM iceberg_snapshots('{table}')")
    if not rows:
        raise SystemExit(f"{table} has no snapshots — nothing to compare")
    if pinned in {r[1] for r in rows}:
        return pinned
    # ponytail: newest sequence_number is the current snapshot on a linear
    # history, which is all this lake writes. A branch or a rollback would need
    # the catalog's current-snapshot-id, which iceberg_snapshots does not expose.
    current = max(rows)[1]
    print(
        f"pin {pinned} not found in {table}; ran at current snapshot {current} "
        f"(pin stale: refresh tests/parity/pinned.json with --pin-current)"
    )
    return current


def duck(minio_user: str, minio_pass: str):
    import duckdb  # imported here so `resolve_snapshot` is importable without it

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
    """B: the materialised 1m candles, at the pinned snapshot. quote_volume last, for D only."""
    return c.execute(
        f"""SELECT exchange, canonical_symbol, window_start, open_e8, high_e8, low_e8, close_e8, trade_count, volume, quote_volume
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


def latest_source_snapshot(src_ids, snapshots) -> int:
    """The newest of the gold.trades snapshots `src_ids` names, by sequence number.

    `snapshots` is (sequence_number, snapshot_id) rows, as `iceberg_snapshots`
    returns them. Snapshot ids are random, so the newest is the highest sequence
    number, never the highest id. Every bucket of a finished day was last computed
    at one of `src_ids`, and a trade landing later would have recomputed its bucket
    at a later one, so the newest holds every trade B counted.
    """
    seq = {sid: n for n, sid in snapshots}
    gone = sorted(set(src_ids) - set(seq))
    if not src_ids or gone:
        raise SystemExit(f"gold.trades snapshot(s) {gone} are expired or from another table: D cannot read the trades B was computed from")
    return max(src_ids, key=seq.__getitem__)


def pinned_sql(manifest: dict, unique_id: str, snapshots: dict) -> str:
    """A dbt model's compiled SQL, each model it reads inlined, each source read at a snapshot.

    `manifest` is dbt's target/manifest.json after `dbt compile`; `snapshots` maps a
    source's unique id to its snapshot id. A source without one is an error: the
    point is to read nothing at `latest`.
    """
    node = manifest["nodes"][unique_id]
    if "compiled_code" not in node:
        raise SystemExit(f"{unique_id} is not compiled: run dbt compile first")
    sql = node["compiled_code"]
    for dep in node["depends_on"]["nodes"]:
        if dep in manifest["sources"]:
            if dep not in snapshots:
                raise SystemExit(f"{unique_id} reads {dep}, which has no snapshot to read it at")
            name, replacement = manifest["sources"][dep]["relation_name"], f"{manifest['sources'][dep]['relation_name']} FOR VERSION AS OF {snapshots[dep]}"
        else:
            name, replacement = manifest["nodes"][dep]["relation_name"], f"({pinned_sql(manifest, dep, snapshots)})"
        if not name or name not in sql:  # an ephemeral model has no relation; a quoting change would hide one
            raise SystemExit(f"{dep} does not appear in the compiled SQL of {unique_id} as {name}")
        sql = sql.replace(name, replacement)
    return sql


def dbt_candles(day: str, trades_snapshot: int) -> list:
    """D: the dbt model's SQL through Trino, over gold.trades at `trades_snapshot`."""
    import trino  # imported here so the pure functions above import without it

    # The SQL as committed, not a manifest left over from another checkout. Reads only.
    done = subprocess.run(["uv", "run", "--locked", "-q", "dbt", "compile", "--select", "+ohlcv_1m"], cwd=DBT, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"dbt compile failed in {DBT}:\n{done.stdout}{done.stderr}")
    sql = pinned_sql(json.loads((DBT / "target" / "manifest.json").read_text()), DBT_MODEL, {DBT_TRADES: trades_snapshot})
    cur = trino.dbapi.connect(host=TRINO_HOST, port=TRINO_PORT, user="parity", timezone="UTC").cursor()
    cur.execute(
        f"""SELECT exchange, canonical_symbol, window_start, open_e8, high_e8, low_e8, close_e8, trade_count, volume,
                   quote_volume, typeof(quote_volume)
            FROM ({sql}) WHERE CAST(window_start AS DATE) = DATE '{day}' ORDER BY 1, 2, 3"""
    )
    return cur.fetchall()


def normalise(rows: list, quote_volume: bool = False) -> dict:
    """{(exchange, symbol, window_start): (open, high, low, close, count, volume[, quote_volume])}.

    quote_volume, when asked for, is the tenth column and compared exactly as a Decimal.
    """
    out = {}
    for r in rows:
        exchange, symbol, ws = r[0], r[1], r[2]
        key = (exchange, symbol, ws.replace(tzinfo=None) if hasattr(ws, "replace") else ws)
        out[key] = tuple(int(x) if i < 5 else round(float(x), 8) for i, x in enumerate(r[3:9]))
        if quote_volume:
            out[key] += (Decimal(r[9]),)
    return out


def diff(name_a: str, a: dict, name_b: str, b: dict, limit: int = 5) -> int:
    keys = sorted(set(a) | set(b))
    bad = [k for k in keys if a.get(k) != b.get(k)]
    for k in bad[:limit]:
        print(f"  {name_a} vs {name_b} {k}: {a.get(k)} != {b.get(k)}")
    print(f"{name_a} vs {name_b}: {len(keys)} buckets, {len(bad)} differ")
    return len(bad)


def not_empty(**sides) -> int:
    """0 when every side has rows; 1 with a message when one is empty.

    An empty side compares equal to another empty side, so without this a run
    against the wrong day or a stale snapshot id prints PASS and proves nothing.
    """
    empty = [name for name, rows in sides.items() if not rows]
    if empty:
        print(f"PARITY: FAIL - nothing to compare, {' and '.join(empty)} returned 0 rows (wrong --day, or a snapshot id from another table?)")
    return 1 if empty else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    ap.add_argument("--ohlcv-snapshot", type=int, required=True, help="lake.gold.ohlcv_1m snapshot id")
    ap.add_argument("--silver-snapshots", help='JSON: {"binance": id, "kraken": id, "coinbase": id}: the k2.src-snapshot-id.<venue> of the gold.trades snapshot; required unless --skip-silver')
    ap.add_argument("--skip-clickhouse", action="store_true", help="leave out A, ClickHouse")
    ap.add_argument("--skip-silver", action="store_true", help="leave out C, DuckDB over silver")
    ap.add_argument("--dbt", action="store_true", help="add D, dbt's ohlcv_1m SQL through Trino at the gold.trades snapshot B was computed from")
    ap.add_argument("--write-pin", action="store_true", help="on success, record the ids in tests/parity/pinned.json")
    args = ap.parse_args()
    if not args.skip_silver and not args.silver_snapshots:
        ap.error("--silver-snapshots is required unless --skip-silver")
    if args.write_pin and args.skip_silver:
        ap.error("--write-pin records the silver ids, so it needs side C: drop --skip-silver")

    c = duck(os.environ["MINIO_ROOT_USER"], os.environ["MINIO_ROOT_PASSWORD"])

    def q(sql):
        return c.execute(sql).fetchall()

    ohlcv_snapshot = resolve_snapshot(q, "lake.gold.ohlcv_1m", args.ohlcv_snapshot)
    lake_rows = lake_candles(c, args.day, ohlcv_snapshot)
    b = normalise(lake_rows)
    if not_empty(**{"lake.gold.ohlcv_1m": b}):
        return 1
    bad = 0
    if not args.skip_silver:
        silver_snaps = {
            venue: resolve_snapshot(q, f"lake.silver.trades_{venue}", snap)
            for venue, snap in json.loads(args.silver_snapshots).items()
        }
        cc = normalise(silver_candles(c, args.day, silver_snaps))
        if not_empty(**{"duckdb-over-silver": cc}):
            return 1
        bad += diff("lake.gold.ohlcv_1m", b, "duckdb-over-silver", cc)
    if args.dbt:
        src = [r[0] for r in q(f"""SELECT DISTINCT src_snapshot_id FROM lake.gold.ohlcv_1m AT (VERSION => {ohlcv_snapshot})
                                   WHERE CAST(window_start AS DATE) = DATE '{args.day}'""")]
        trades_snapshot = latest_source_snapshot(src, q("SELECT sequence_number, snapshot_id FROM iceberg_snapshots('lake.gold.trades')"))
        print(f"dbt side: lake.gold.trades FOR VERSION AS OF {trades_snapshot}, the newest of the {len(src)} snapshots the day's lake candles name")
        d_rows = dbt_candles(args.day, trades_snapshot)
        if not_empty(**{"dbt.ohlcv_1m": d_rows}):
            return 1
        # quote_volume only where both engines type it alike (ADR-035): Iceberg's decimal(38,10) for B.
        lake_type = q("SELECT typeof(quote_volume) FROM lake.gold.ohlcv_1m LIMIT 1")[0][0].lower()
        same = {r[10] for r in d_rows} == {lake_type}
        print(f"quote_volume: lake {lake_type}, dbt {sorted({r[10] for r in d_rows})}: " + ("compared" if same else "NOT compared, the types differ"))
        bad += diff("lake.gold.ohlcv_1m", normalise(lake_rows, quote_volume=same), "dbt.ohlcv_1m", normalise(d_rows, quote_volume=same))
    if not args.skip_clickhouse:
        a = normalise(clickhouse_candles(args.day, os.environ["CLICKHOUSE_PASSWORD"]))
        if not_empty(**{"clickhouse.ohlcv_live": a}):
            return 1
        bad += diff("clickhouse.ohlcv_live", a, "lake.gold.ohlcv_1m", b)
    if bad == 0 and args.write_pin:
        # Read-modify-write: scripts/parity_bars.py keeps its ids in the `bars`
        # block of the same file, and a plain overwrite here deleted them.
        pin = Path("tests/parity/pinned.json")
        doc = json.loads(pin.read_text()) if pin.exists() else {}
        doc.update({"day": args.day, "ohlcv_1m_snapshot": ohlcv_snapshot, "silver_snapshots": silver_snaps})
        pin.write_text(json.dumps(doc, indent=2) + "\n")
    print("PARITY: " + ("PASS" if bad == 0 else "FAIL"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
