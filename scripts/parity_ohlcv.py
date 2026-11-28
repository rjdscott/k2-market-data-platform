#!/usr/bin/env python3
"""make parity-ohlcv (plan 004, step W1-18; F16 re-pinned on Kraken): DuckDB recomputes every candle
timeframe from canonical-trades at the snapshot one candles build read, and compares each with the
table that build published: keys, counts and every value, each difference reported by its class.

    parity_ohlcv.py --release <release dir> [--run <dag_id>/<run_id>]

Run as parity_silver.py runs (mk/parity.mk): a tools one-shot on the catalog's network, the tools
MinIO key alone, Iceberg read through Lakekeeper by the image's DuckDB, nothing written. The archived
three-way check (legacy/v3-lake-ingest/scripts/parity_ohlcv.py) compared ClickHouse and the old lake;
the hot tier is paused (ADR-057) and those tables are dropped, so this compares the lake's candles
with a recompute of its own input.

The pin is a run record: the candles build's `audited` row of each timeframe (run --run's, or the
run of ohlcv_1m's newest) and the `started` rows of the same run, whose inputs name the
canonical-trades snapshot the build read; every timeframe of the run must name the same one.

The recompute, written from the candle rules and not from the models' SQL: trades flagged
is_duplicate are not counted; a window is a UTC time bucket of its width (DuckDB's time_bucket);
open and close are the first and last price ordered by trade time, then the venue row's key
(partition, offset, ordinal); high, low, volume, the trade count and the first and last trade time
are the window's; quote_volume is the sum of price at 12 decimals times quantity at 10, at 18. The
table's through_load_seq is bookkeeping (the input's highest load_seq at the build that last wrote
the window), not a candle value, and is not compared. A window whose every trade turned duplicate
keeps its old row in the table (a merge deletes nothing) and is reported extra, as it is.

Classes, one line each per timeframe, `PASS|FAIL <timeframe>.<class>: <detail>`: duplicate keys,
missing, extra, and one per column. Exit 0 every class empty; 1 a class holds a difference; 2 refused.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from parity_silver import (  # noqa: E402  the pin, the lake and the comparison, one copy
    RUNS,
    Refused,
    Unreachable,
    at,
    build_pin,
    compare,
    duck_relation,
    input_snapshot,
    lake,
    release_facts,
    report,
)

PRODUCT = "market-analytics.candles"
INPUT = "market-analytics.canonical-trades.trades"
WIDTHS = {"ohlcv_1m": "1 minute", "ohlcv_5m": "5 minutes", "ohlcv_1h": "1 hour", "ohlcv_1d": "1 day"}
KEY = ("venue", "instrument_id", "window_start")
COLUMNS = ("window_end", "open", "high", "low", "close", "volume", "quote_volume", "trade_count", "first_trade_at",
           "last_trade_at")


def candles(c, trades: str, table: str, width: str) -> None:
    """`table`: the candles of one width over `trades` (a FROM item of canonical-trades' columns)."""
    order = "ORDER BY trade_ts, kafka_partition, kafka_offset, trade_ordinal"
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE {table} AS
        WITH counted AS (
          SELECT venue, instrument_id, time_bucket(INTERVAL '{width}', trade_ts) AS window_start, trade_ts,
                 kafka_partition, kafka_offset, trade_ordinal, price, quantity
          FROM {trades} WHERE NOT is_duplicate
        )
        SELECT venue, instrument_id, window_start, window_start + INTERVAL '{width}' AS window_end,
               first(price {order}) AS open, max(price) AS high, min(price) AS low, last(price {order}) AS close,
               sum(quantity) AS volume,
               CAST(sum(CAST(price AS DECIMAL(30, 12)) * CAST(quantity AS DECIMAL(30, 10))) AS DECIMAL(38, 18)) AS quote_volume,
               count(*) AS trade_count, min(trade_ts) AS first_trade_at, max(trade_ts) AS last_trade_at
        FROM counted GROUP BY venue, instrument_id, window_start""")


def subject(release: Path) -> dict:
    """The candles build's outputs by timeframe and its canonical-trades input, in the release."""
    _, pipes, warehouses = release_facts(release)
    pipe = pipes.get(PRODUCT)
    if pipe is None:
        raise Refused(f"{PRODUCT} is not a product of the release at {release}")
    tables = {o.rsplit(".", 1)[1]: o for o in pipe["build"]["outputs"]}
    if set(tables) != set(WIDTHS):
        raise Refused(f"{PRODUCT} builds {sorted(tables)}; this recomputes {sorted(WIDTHS)}: a timeframe needs its width here")
    [trades] = [i["relation"] for i in pipe["inputs"] if i["contractId"] == INPUT]
    for relation in (RUNS, trades, *tables.values()):
        duck_relation(relation, warehouses)
    return {"tables": tables, "trades": trades, "warehouses": warehouses}


def run(s: dict, c, run_id: str | None) -> int:
    w = s["warehouses"]
    runs = duck_relation(RUNS, w)
    first = build_pin(c, runs, s["tables"]["ohlcv_1m"], run_id)
    pins = {tf: build_pin(c, runs, t, first["run_id"]) for tf, t in sorted(s["tables"].items())}
    read = {tf: input_snapshot(p, INPUT) for tf, p in pins.items()}
    if len(set(read.values())) != 1:
        raise Refused(f"run {first['run_id']}'s timeframes read different snapshots of {INPUT}: {read}")
    snapshot = read["ohlcv_1m"]
    print(f"parity-ohlcv: pin run {first['run_id']} (its audited run records): {s['trades']}@{snapshot}, "
          + ", ".join(f"{tf}@{p['snapshot']}" for tf, p in pins.items()) + f"; RUN={first['run_id']} compares these again")
    c.execute(f"CREATE OR REPLACE TEMP TABLE trades AS SELECT * FROM {at(duck_relation(s['trades'], w), snapshot)}")
    (n, dup) = c.execute("SELECT count(*), count(*) FILTER (is_duplicate) FROM trades").fetchone()
    print(f"INFO  trades                 {n} canonical row(s), {dup} flagged is_duplicate and not counted")
    classes = []
    for tf, table in sorted(s["tables"].items()):
        candles(c, "trades", f"r_{tf}", WIDTHS[tf])
        c.execute(f"CREATE OR REPLACE TEMP TABLE c_{tf} AS SELECT * FROM {at(duck_relation(table, w), pins[tf]['snapshot'])}")
        (got, want) = c.execute(f"SELECT (SELECT count(*) FROM c_{tf}), (SELECT count(*) FROM r_{tf})").fetchone()
        print(f"INFO  {tf + '.rows':<22} recomputed {want}, {table} {got}")
        classes += [(f"{tf}.{name}", k, first_keys)
                    for name, k, first_keys in compare(c, f"r_{tf}", f"c_{tf}", KEY, COLUMNS, ("recomputed", tf))]
    failed = report(classes)
    print(f"parity-ohlcv: {PRODUCT}: " + (f"{failed} class(es) differ" if failed else f"every class empty over {len(WIDTHS)} timeframes"))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--release", required=True, type=Path, help="a compiled release directory")
    ap.add_argument("--run", help="the candles build's run id, <dag_id>/<run_id> (default: the newest audited)")
    a = ap.parse_args(argv)
    try:
        import duckdb
    except ImportError:
        print("parity-ohlcv: REFUSED: this image has no DuckDB (a tools image built before W0-11)", file=sys.stderr)
        return 2

    try:
        s = subject(a.release.resolve())
        with tempfile.TemporaryDirectory(prefix="k2-parity-") as tmp:
            used = {s["warehouses"][r.split(".")[0]] for r in (RUNS, s["trades"], *s["tables"].values())}
            return run(s, lake(used, dict(os.environ), tmp), a.run)
    except Refused as e:
        print(f"parity-ohlcv: REFUSED: {e}", file=sys.stderr)
        return 2
    except (Unreachable, OSError, duckdb.Error) as e:  # the lake unread is no difference found
        print(f"parity-ohlcv: unreachable: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
