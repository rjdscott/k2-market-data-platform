#!/usr/bin/env python3
"""
Three-way event-bar parity at pinned lake snapshots (ADR-029): the materialised
`gold.bars`, the same window SQL run by DuckDB over `gold.trades`, and the
pure-Python reference in docker/lake/bars.py — compared at tolerance ZERO.

    uv run --no-project --with duckdb==1.4.4 --with pytz --with pyyaml \
        python scripts/parity_bars.py --day 2026-08-26 \
        --bars-snapshot <lake.gold.bars snapshot id> --trades-snapshot <lake.gold.trades snapshot id> \
        [--reference-symbols BTC/USD,ETH/USDT]

  A  lake    gold.bars at --bars-snapshot, one UTC day
  B  DuckDB  bars.bars_sql(..., 'duckdb') over gold.trades at --trades-snapshot, the same day
  C  Python  bars.reference over the same trades, for --reference-symbols (every
             symbol is a 10 M-row Python loop; two symbols is the arbiter at a cost
             that fits in CI)

A and B must agree on every row of the day; B and C on every row of the
reference symbols. Pinned, never `latest` (scripts/parity_ohlcv.py explains).
`tests/parity/pinned.json` holds the ids of the last passing run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docker" / "lake"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bars  # noqa: E402
from parity_ohlcv import resolve_snapshot  # noqa: E402  - same stale-pin fallback, one copy

LAKEKEEPER = os.environ.get("K2_LAKEKEEPER_HOST_URL", "http://localhost:18181/catalog")
S3_ENDPOINT = os.environ.get("K2_S3_HOST_ENDPOINT", "localhost:9000")
KEY = ["exchange", "canonical_symbol", "bar_kind", "day", "bar_seq"]
COLS = ["open_e8", "high_e8", "low_e8", "close_e8", "trade_count", "volume_e8", "quote_volume_e8"]
SCALE = bars.SCALE


def duck(user: str, password: str):
    import duckdb  # see scripts/parity_ohlcv.py: keeps the module importable without it

    c = duckdb.connect()
    c.execute("SET TimeZone = 'UTC'")
    c.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    c.execute(
        f"""CREATE SECRET s3sec (TYPE S3, KEY_ID '{user}', SECRET '{password}',
            ENDPOINT '{S3_ENDPOINT}', URL_STYLE 'path', USE_SSL false, REGION 'local-01')"""
    )
    c.execute(
        f"ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT '{LAKEKEEPER}', AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"
    )
    return c


def as_int_units(rows: list) -> dict:
    """Rows -> {key: (open, high, low, close, count, volume_e8, quote_volume_e8)}, every value an int."""
    out = {}
    for r in rows:
        key = (r[0], r[1], r[2], str(r[3]), int(r[4]))
        out[key] = tuple(int(x) for x in r[5:12])
    return out


def lake_bars(c, day: str, snapshot: int) -> dict:
    return as_int_units(
        c.execute(
            f"""SELECT exchange, canonical_symbol, bar_kind, day, bar_seq, open_e8, high_e8, low_e8, close_e8, trade_count, volume_e8, quote_volume_e8
                FROM lake.gold.bars AT (VERSION => {snapshot}) WHERE day = DATE '{day}'"""
        ).fetchall()
    )


def duck_bars(c, day: str, snapshot: int) -> dict:
    rows = bars.load(ROOT / "config" / "bars.yaml")
    c.execute("CREATE OR REPLACE TABLE __th (canonical_symbol VARCHAR, bar_kind VARCHAR, threshold DOUBLE)")
    c.executemany("INSERT INTO __th VALUES (?, ?, ?)", rows)
    trades = (
        f"(SELECT exchange, canonical_symbol, exchange_ts, recv_ts_ns, trade_seq, price_e8, qty_e8 "
        f"FROM lake.gold.trades AT (VERSION => {snapshot}) WHERE CAST(exchange_ts AS DATE) = DATE '{day}')"
    )
    sql = bars.bars_sql(trades, "__th", "duckdb")
    return as_int_units(
        c.execute(
            f"SELECT exchange, canonical_symbol, bar_kind, day, bar_seq, open_e8, high_e8, low_e8, close_e8, trade_count, volume_e8, quote_volume_e8 FROM ({sql})"
        ).fetchall()
    )


def reference_bars(c, day: str, snapshot: int, symbols: list) -> dict:
    thresholds = {(s, k): t for s, k, t in bars.load(ROOT / "config" / "bars.yaml")}
    quoted = ", ".join(f"'{s}'" for s in symbols)
    rows = c.execute(
        f"""SELECT exchange, canonical_symbol, exchange_ts, recv_ts_ns, trade_seq, price_e8, qty_e8
            FROM lake.gold.trades AT (VERSION => {snapshot})
            WHERE CAST(exchange_ts AS DATE) = DATE '{day}' AND canonical_symbol IN ({quoted})"""
    ).fetchall()
    trades = [
        {"exchange": r[0], "canonical_symbol": r[1], "exchange_ts": r[2], "recv_ts_ns": r[3], "trade_seq": r[4], "price_e8": r[5], "qty_e8": r[6]}
        for r in rows
    ]
    out = {}
    for symbol in symbols:
        subset = [t for t in trades if t["canonical_symbol"] == symbol]
        for kind in bars.KINDS:
            for b in bars.reference(subset, kind, thresholds[(symbol, kind)]):
                key = (b["exchange"], b["canonical_symbol"], kind, str(b["day"]), b["bar_seq"])
                out[key] = (b["open_e8"], b["high_e8"], b["low_e8"], b["close_e8"], b["trade_count"], b["volume_e8"], b["quote_volume_e8"])
    return out, len(trades)


def compare(name: str, a: dict, b: dict, limit: int = 5) -> int:
    diffs = [k for k in a.keys() | b.keys() if a.get(k) != b.get(k)]
    print(f"{name}: {len(a)} vs {len(b)} rows, {len(diffs)} differ")
    for k in sorted(diffs)[:limit]:
        print(f"  {k}: {a.get(k)} vs {b.get(k)}")
    return len(diffs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", required=True)
    ap.add_argument("--bars-snapshot", type=int, required=True)
    ap.add_argument("--trades-snapshot", type=int, required=True)
    ap.add_argument("--reference-symbols", default="BTC/USD,ETH/USDT")
    ap.add_argument("--write-pin", action="store_true", help="on success, record the ids in tests/parity/pinned.json under 'bars'")
    args = ap.parse_args()

    env = dict(os.environ)
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())
    c = duck(env["MINIO_ROOT_USER"], env["MINIO_ROOT_PASSWORD"])

    def q(sql):
        return c.execute(sql).fetchall()

    bars_snapshot = resolve_snapshot(q, "lake.gold.bars", args.bars_snapshot)
    trades_snapshot = resolve_snapshot(q, "lake.gold.trades", args.trades_snapshot)

    a = lake_bars(c, args.day, bars_snapshot)
    b = duck_bars(c, args.day, trades_snapshot)
    symbols = [s for s in args.reference_symbols.split(",") if s]
    ref, n_trades = reference_bars(c, args.day, trades_snapshot, symbols)
    b_subset = {k: v for k, v in b.items() if k[1] in symbols}

    # An empty side compares equal to another empty side: without this, a run
    # against the wrong day or a snapshot id from another table prints "ok".
    sides = (("lake.gold.bars", a), ("DuckDB window SQL", b), ("Python reference", ref), ("DuckDB, reference symbols only", b_subset))
    empty = [name for name, rows in sides if not rows]
    if empty:
        print(f"parity: FAIL - nothing to compare, {' and '.join(empty)} returned 0 rows (wrong --day, wrong --reference-symbols, or a snapshot id from another table?)")
        return 1

    bad = compare("A lake.gold.bars vs B DuckDB window SQL", a, b)
    bad += compare(f"B DuckDB vs C Python reference ({', '.join(symbols)}, {n_trades:,} trades)", b_subset, ref)
    if bad:
        return 1
    if args.write_pin:
        pin = ROOT / "tests" / "parity" / "pinned.json"
        doc = json.loads(pin.read_text())
        doc["bars"] = {"day": args.day, "bars_snapshot": bars_snapshot, "trades_snapshot": trades_snapshot, "reference_symbols": symbols}
        pin.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"pinned in {pin}")
    print("parity: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
