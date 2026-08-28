"""
Event bars — gold.bars: tick, volume and dollar bars over gold.trades at the
one canonical threshold per symbol in config/bars.yaml (ADR-029).

The bar is a *cumulative bucket*: for kind K with threshold T, a trade belongs
to bar k of its UTC day when k*T <= (cumulative K-total of the day's earlier
trades) < (k+1)*T, earlier meaning the same total order the candles use,
(exchange_ts, recv_ts_ns, trade_seq). That definition is one window expression
in Spark and DuckDB and the twenty-line reference below, which is why it was
chosen over the reset-after-crossing bar: three engines must agree at
tolerance zero, and a running reset is stateful in a way SQL windows are not.
Bars restart at the UTC day boundary so a touched day recomputes alone.

Fixed point throughout, in and out: qty in 1e-8 base units, notional in 1e-16
quote units (price_e8 * qty_e8, kept in a 38-digit integer) while the bucket
is decided, and the row carries `volume_e8` (exact) and `quote_volume_e8` (the
bar's summed notional floor-divided to 1e-8 quote units, so the one rounding
is an integer rule both engines spell the same way). No DECIMAL division
anywhere: DuckDB turns a DECIMAL quotient into a DOUBLE and the first parity
run lost one unit in the eighth place on 1 bar in 60 (2026-08-28).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

BARS_PATH = Path(os.environ.get("K2_BARS_PATH", "/home/iceberg/config/bars.yaml"))
KINDS = ("tick", "volume", "dollar")
SCALE = 100_000_000
ORDER = "exchange_ts, recv_ts_ns, trade_seq"


def load(path: Path = BARS_PATH, canonical_symbols: set | None = None) -> list:
    """`[(canonical_symbol, kind, threshold), ...]` from bars.yaml.

    With `canonical_symbols` given (the registry's), a symbol without a
    threshold is an error, not an absent bar: the point of the table is that
    every instrument has exactly one canonical value.

    A threshold must also be EXACT in the fixed point the bucket arithmetic
    uses: a whole number of trades for `tick`, a whole number of 1e-8 units for
    `volume` and `dollar`. 0.29 is not (0.29 x 1e8 is 28999999.999999996 in
    binary floating point) and would have Spark's CAST truncate to 28999999
    while DuckDB's rounds to 29000000 — two engines, two bar boundaries, and a
    parity run that fails on real data long after the config changed. 0.5 is
    fine: 0.5e8 is exact.
    """
    doc = yaml.safe_load(path.read_text())
    if doc.get("version") != 1:
        raise ValueError(f"{path}: expected bars.yaml version 1, got {doc.get('version')!r}")
    rows = []
    for symbol, per_kind in doc["thresholds"].items():
        for kind in KINDS:
            t = per_kind.get(kind)
            if not isinstance(t, (int, float)) or isinstance(t, bool) or t <= 0:
                raise ValueError(f"{path}: {symbol} {kind} threshold must be a positive number, got {t!r}")
            units = float(t) if kind == "tick" else float(t) * SCALE
            if not units.is_integer():
                raise ValueError(
                    f"{path}: {symbol} {kind} threshold {t!r} is not exact: "
                    + ("tick thresholds must be whole trades" if kind == "tick" else f"{t!r} x 1e8 is {units!r}, not a whole number of 1e-8 units")
                )
            rows.append((symbol, kind, float(t)))
    if canonical_symbols is not None:
        missing = sorted(canonical_symbols - set(doc["thresholds"]))
        if missing:
            raise ValueError(f"{path}: no thresholds for {missing}; every instrument needs a row")
    return rows


# Per-engine spellings of the three things the two dialects disagree on.
DIALECT = {
    "spark": {
        "intdiv": lambda a, b: f"(({a}) DIV ({b}))",
        "wide": "DECIMAL(38,0)",
        "first": lambda x: f"min_by({x}, struct({ORDER}))",
        "last": lambda x: f"max_by({x}, struct({ORDER}))",
    },
    "duckdb": {
        "intdiv": lambda a, b: f"(({a}) // ({b}))",
        "wide": "HUGEINT",
        "first": lambda x: f"arg_min({x}, ROW({ORDER}))",
        "last": lambda x: f"arg_max({x}, ROW({ORDER}))",
    },
}


def bars_sql(trades: str, thresholds: str, dialect: str) -> str:
    """One row per (exchange, canonical_symbol, bar_kind, day, bar_seq).

    `trades` is a relation with gold.trades' columns (exchange, canonical_symbol,
    exchange_ts, recv_ts_ns, trade_seq, price_e8, qty_e8); `thresholds` one with
    (canonical_symbol, bar_kind, threshold). Both may be table names or
    parenthesised subqueries. The caller adds the provenance columns.
    """
    d = DIALECT[dialect]
    wide = d["wide"]
    return f"""
        WITH t AS (
          SELECT exchange, canonical_symbol, CAST(exchange_ts AS DATE) AS day,
                 exchange_ts, recv_ts_ns, trade_seq, price_e8, qty_e8,
                 CAST(price_e8 AS {wide}) * CAST(qty_e8 AS {wide}) AS notional_e16
          FROM {trades}
        ),
        c AS (
          SELECT *,
                 (sum(qty_e8)       OVER w) - qty_e8       AS vol_before,
                 (sum(notional_e16) OVER w) - notional_e16 AS usd_before,
                 (count(*)          OVER w) - 1            AS n_before
          FROM t
          WINDOW w AS (PARTITION BY exchange, canonical_symbol, day ORDER BY {ORDER}
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        ),
        k AS (
          SELECT c.*, th.bar_kind, th.threshold,
                 CASE th.bar_kind
                   WHEN 'tick'   THEN {d["intdiv"]("n_before", "CAST(th.threshold AS BIGINT)")}
                   WHEN 'volume' THEN {d["intdiv"]("vol_before", f"CAST(th.threshold * {SCALE} AS BIGINT)")}
                   WHEN 'dollar' THEN {d["intdiv"]("usd_before", f"CAST(CAST(th.threshold * {SCALE} AS BIGINT) AS {wide}) * CAST({SCALE} AS {wide})")}
                 END AS bar_seq
          FROM c JOIN {thresholds} th ON th.canonical_symbol = c.canonical_symbol
        )
        SELECT exchange, canonical_symbol, bar_kind, CAST(threshold AS DECIMAL(38,10)) AS threshold, day,
               CAST(bar_seq AS INT) AS bar_seq,
               {d["first"]("price_e8")} AS open_e8, max(price_e8) AS high_e8, min(price_e8) AS low_e8,
               {d["last"]("price_e8")} AS close_e8,
               CAST(sum(qty_e8) AS BIGINT) AS volume_e8,
               CAST({d["intdiv"]("sum(notional_e16)", f"CAST({SCALE} AS {wide})")} AS BIGINT) AS quote_volume_e8,
               count(*) AS trade_count,
               min(exchange_ts) AS open_time, max(exchange_ts) AS close_time
        FROM k
        GROUP BY exchange, canonical_symbol, bar_kind, threshold, day, bar_seq
    """


def reference(trades: list, kind: str, threshold) -> list:
    """The arbiter: the same bars from a sorted Python list, integers only.

    `trades` are dicts with exchange, canonical_symbol, exchange_ts (a datetime,
    UTC), recv_ts_ns, trade_seq, price_e8, qty_e8. Returns rows keyed like
    gold.bars, with volume/quote_volume as exact integers in 1e-8 / 1e-16 units
    so a comparison needs no decimal parsing.
    """
    if kind == "tick":
        t_units = int(threshold)
    elif kind == "volume":
        t_units = int(round(threshold * SCALE))
    elif kind == "dollar":
        t_units = int(round(threshold * SCALE)) * SCALE
    else:
        raise ValueError(kind)
    ordered = sorted(trades, key=lambda x: (x["exchange"], x["canonical_symbol"], x["exchange_ts"], x["recv_ts_ns"], x["trade_seq"]))
    bars: dict = {}
    cum: dict = {}
    for x in ordered:
        day = x["exchange_ts"].date()
        group = (x["exchange"], x["canonical_symbol"], day)
        before = cum.get(group, 0)
        unit = {"tick": 1, "volume": x["qty_e8"], "dollar": x["price_e8"] * x["qty_e8"]}[kind]
        cum[group] = before + unit
        key = (*group, before // t_units)
        b = bars.get(key)
        if b is None:
            bars[key] = b = {
                "open_e8": x["price_e8"], "high_e8": x["price_e8"], "low_e8": x["price_e8"], "close_e8": x["price_e8"],
                "volume_e8": 0, "quote_volume_e16": 0, "quote_volume_e8": 0, "trade_count": 0,
                "open_time": x["exchange_ts"], "close_time": x["exchange_ts"],
            }
        b["high_e8"] = max(b["high_e8"], x["price_e8"])
        b["low_e8"] = min(b["low_e8"], x["price_e8"])
        b["close_e8"] = x["price_e8"]
        b["close_time"] = x["exchange_ts"]
        b["volume_e8"] += x["qty_e8"]
        b["quote_volume_e16"] += x["price_e8"] * x["qty_e8"]
        b["quote_volume_e8"] = b["quote_volume_e16"] // SCALE
        b["trade_count"] += 1
    return [
        {"exchange": k[0], "canonical_symbol": k[1], "bar_kind": kind, "day": k[2], "bar_seq": k[3], **v}
        for k, v in sorted(bars.items())
    ]
