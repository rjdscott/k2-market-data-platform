#!/usr/bin/env python3
"""
Gold — the canonical cross-venue surface, derived from silver only (ADR-026).

    gold.trades          silver.trades_<venue> WHERE NOT venue_replay, one schema, 1e-8 fixed point
    gold.dim_instrument  config/instruments.yaml, rewritten every run
    gold.dim_venue       one row per venue, from the same file
    gold.ohlcv_{1m,5m,1h,1d}   candles over gold.trades, recomputed per touched bucket and MERGEd
    gold.bars            tick / volume / dollar bars over gold.trades at config/bars.yaml's
                         thresholds, recomputed per touched (exchange, symbol, UTC day) — bars.py

**One row per logical trade, and silver already decided which.** Silver keeps
every delivery and flags the later ones `venue_replay`; the earliest delivery is
the `venue_replay = false` row, so gold.trades is a projection of silver, not a
second deduplication with its own rules. The identifier (exchange,
canonical_symbol, trade_id) is unique by construction and the nightly audit
asserts it anyway.

**Candles are replaced, never accumulated.** A batch of trades names the
buckets it touches — (exchange, symbol, window_start) for each timeframe — and
each of those buckets is recomputed over ALL of gold.trades for that bucket,
then MERGEd in (update if present, insert if not). A late trade therefore
rewrites its candle instead of adding a second partial row for the same
minute, which is the v2 SummingMergeTree failure closed at the source. Every
row carries the gold.trades snapshot it was computed from, so "which trades
went into this candle" is a question with an answer.

**open/close are decided by (exchange_ts, recv_ts_ns, trade_seq)** — a total
order. The first two alone are not one: a Coinbase frame carries up to 100
trades with one recv_ts_ns and often one `time`, and the first parity run
(2026-08-27) found 3,829 of 28,590 buckets whose open or close depended on
which engine broke the tie. Trade ids are sequential per symbol, so the id is
the venue's own order within the instant. ClickHouse gold.ohlcv_live and the
DuckDB side of scripts/parity_ohlcv.py apply the same three-key order.

Incremental by silver snapshot per venue, like the layers below it; rebuild.py
drops, recreates and replays gold from silver's current snapshots.
"""

from __future__ import annotations

from datetime import datetime
from functools import reduce

import bars
import instruments
import silver
from catalog import added_records, snapshot_history
from pyspark.sql import DataFrame
from pyspark.sql import functions as F

import offsets as O
from spark_conf import CATALOG

TRADES = f"{CATALOG}.gold.trades"
DIM_INSTRUMENT = f"{CATALOG}.gold.dim_instrument"
DIM_VENUE = f"{CATALOG}.gold.dim_venue"
OHLCV = {
    "1m": (f"{CATALOG}.gold.ohlcv_1m", 60),
    "5m": (f"{CATALOG}.gold.ohlcv_5m", 300),
    "1h": (f"{CATALOG}.gold.ohlcv_1h", 3600),
    "1d": (f"{CATALOG}.gold.ohlcv_1d", 86400),
}
BARS = f"{CATALOG}.gold.bars"
TABLES = (TRADES, DIM_INSTRUMENT, DIM_VENUE, *[t for t, _ in OHLCV.values()], BARS)
IDENTIFIER_FIELDS = ("exchange", "canonical_symbol", "trade_id")
SCALE = 100_000_000  # 1e-8 fixed point, as the wire and ClickHouse gold

# Which venues send the whole book (no depth parameter); config/instruments.yaml.
_VENUE_DEPTH = {"binance": 20, "kraken": 25, "coinbase": 0}


def _project_silver(df: DataFrame, exchange: str, run_ts: datetime) -> DataFrame:
    """silver.trades_<venue> rows -> gold.trades columns."""
    return df.where(~F.col("venue_replay")).select(
        F.lit(exchange).alias("exchange"),
        "canonical_symbol",
        "symbol",
        "trade_id",
        "trade_seq",
        # Exact: silver's DECIMAL(28,10) has at most 8 decimals on every venue here
        # (the precision_loss flag is the guard), so x 1e8 is an integer.
        (F.col("price") * SCALE).cast("bigint").alias("price_e8"),
        (F.col("qty") * SCALE).cast("bigint").alias("qty_e8"),
        "side",
        "exchange_ts",
        "recv_ts_ns",
        "conn_id",
        "conn_msg_seq",
        "seq_gap",
        "missing_before",
        "src_topic",
        "src_partition",
        "src_offset",
        "src_index",
        F.lit(run_ts).alias("ingest_ts"),
    )


def _current(spark, table: str):
    rows = spark.sql(f"SELECT snapshot_id FROM {table}.refs WHERE name = 'main'").collect()
    return rows[0][0] if rows else None


def stage_trades(spark, run_ts: datetime) -> tuple:
    """Append the new non-replay silver rows of every venue. Returns (rows, touched buckets DataFrame or None).

    One commit per venue, each stamped with that venue's silver snapshot under
    `k2.src-snapshot-id.<venue>`; the position per venue is read back from the
    latest decode summary the same way bronze and silver do it.
    """
    total, touched = 0, []
    for spec in silver.TRADES:
        end = _current(spark, spec.table)
        if end is None:
            continue
        previous = O.latest_summary(snapshot_history(spark, TRADES), O.JOB_DECODE)
        key = f"{O.SRC_SNAPSHOT_ID}.{spec.exchange}"
        start = previous.get(key) if previous else None
        if start and str(start) == str(end):
            print(f"stage 2d: gold.trades level with {spec.table}, nothing to add")
            continue
        reader = spark.read.format("iceberg")
        if start:
            reader = reader.option("start-snapshot-id", start).option("end-snapshot-id", end)
        else:
            reader = reader.option("snapshot-id", end)
        batch = _project_silver(reader.load(spec.table), spec.exchange, run_ts)
        # The buckets this batch touches, per timeframe, before the write so the
        # candle step below can recompute exactly those. Distinct minutes only:
        # coarser buckets are derived from them.
        minutes = batch.select("exchange", "canonical_symbol", F.date_trunc("minute", "exchange_ts").alias("minute")).distinct()
        touched.append(minutes)
        writer = batch.writeTo(TRADES).option(f"snapshot-property.{O.JOB}", O.JOB_DECODE)
        # Carry every venue's position forward on each commit, so the latest
        # summary always holds all three.
        positions = {k: v for k, v in (previous or {}).items() if k.startswith(f"{O.SRC_SNAPSHOT_ID}.")}
        positions[key] = str(end)
        for k, v in positions.items():
            writer = writer.option(f"snapshot-property.{k}", v)
        writer.append()
        n = added_records(spark, TRADES)
        total += n
        print(f"stage 2d: {n} rows -> {TRADES} from {spec.table} (src snapshot {end})")
    return total, (reduce(DataFrame.unionByName, touched) if touched else None)


def load_dims(spark, run_ts: datetime) -> None:
    """config/instruments.yaml as gold.dim_instrument and gold.dim_venue, overwritten."""
    reg = instruments.load()
    rows, venues = [], []
    for exchange, natives in reg.items():
        for native, canon in natives.items():
            base, quote = canon.split("/")
            rows.append((exchange, native, canon, base, quote, _VENUE_DEPTH[exchange], run_ts))
        venues.append((exchange, _VENUE_DEPTH[exchange], len(natives), run_ts))
    spark.createDataFrame(rows, "exchange string, symbol string, canonical_symbol string, base string, quote string, book_depth int, loaded_at timestamp").writeTo(DIM_INSTRUMENT).overwritePartitions()
    spark.createDataFrame(venues, "exchange string, book_depth int, instruments int, loaded_at timestamp").writeTo(DIM_VENUE).overwritePartitions()
    print(f"stage 2d: dims loaded: {len(rows)} instruments, {len(venues)} venues")


def candles(spark, seconds: int, keys: DataFrame, src_snapshot_id, run_ts: datetime) -> DataFrame:
    """OHLCV for the (exchange, symbol, window_start) buckets in `keys`, over all of gold.trades."""
    keys.createOrReplaceTempView("__k")
    return spark.sql(
        f"""
        WITH t AS (
          SELECT g.* FROM {TRADES} g
          JOIN __k k ON g.exchange = k.exchange AND g.canonical_symbol = k.canonical_symbol
                    AND to_timestamp(floor(unix_timestamp(g.exchange_ts) / {seconds}) * {seconds}) = k.window_start
        )
        SELECT exchange, canonical_symbol,
               to_timestamp(floor(unix_timestamp(exchange_ts) / {seconds}) * {seconds}) AS window_start,
               min_by(price_e8, struct(exchange_ts, recv_ts_ns, trade_seq)) AS open_e8,
               max(price_e8) AS high_e8,
               min(price_e8) AS low_e8,
               max_by(price_e8, struct(exchange_ts, recv_ts_ns, trade_seq)) AS close_e8,
               CAST(sum(CAST(qty_e8 AS DECIMAL(38,10))) / {SCALE} AS DECIMAL(38,10)) AS volume,
               CAST(sum(CAST(price_e8 AS DECIMAL(38,10)) * CAST(qty_e8 AS DECIMAL(38,10))) / {SCALE * SCALE} AS DECIMAL(38,10)) AS quote_volume,
               count(*) AS trade_count,
               min(exchange_ts) AS open_time,
               max(exchange_ts) AS close_time,
               CAST({src_snapshot_id} AS BIGINT) AS src_snapshot_id,
               TIMESTAMP '{run_ts.strftime("%Y-%m-%d %H:%M:%S.%f")}' AS computed_at
        FROM t GROUP BY exchange, canonical_symbol, window_start
        """
    )


def stage_ohlcv(spark, minutes: DataFrame, run_ts: datetime) -> int:
    """Recompute and MERGE every bucket the new trades touch, for each timeframe."""
    src = _current(spark, TRADES)
    total = 0
    for tf, (table, seconds) in OHLCV.items():
        keys = minutes.select(
            "exchange", "canonical_symbol",
            F.to_timestamp(F.floor(F.unix_timestamp("minute") / seconds) * seconds).alias("window_start"),
        ).distinct()
        candles(spark, seconds, keys, src, run_ts).createOrReplaceTempView("__c")
        spark.sql(
            f"""
            MERGE INTO {table} t USING __c c
            ON t.exchange = c.exchange AND t.canonical_symbol = c.canonical_symbol AND t.window_start = c.window_start
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        n = keys.count()
        total += n
        print(f"stage 2d: {n} {tf} buckets recomputed -> {table} (gold.trades snapshot {src})")
    return total


def _thresholds_view(spark) -> str:
    """config/bars.yaml as a temp view, checked against the registry's canonical symbols."""
    canon = {c for natives in instruments.load().values() for c in natives.values()}
    rows = bars.load(canonical_symbols=canon)
    spark.createDataFrame(rows, "canonical_symbol string, bar_kind string, threshold double").createOrReplaceTempView("__th")
    return "__th"


def _bars(spark, days_view: str | None, src_snapshot_id, run_ts: datetime) -> DataFrame:
    """gold.bars rows for the (exchange, symbol, day) keys in `days_view`, or the whole of gold.trades when None."""
    th = _thresholds_view(spark)
    if days_view is None:
        trades = TRADES
    else:
        trades = (
            f"(SELECT g.* FROM {TRADES} g JOIN {days_view} k ON g.exchange = k.exchange "
            f"AND g.canonical_symbol = k.canonical_symbol AND CAST(g.exchange_ts AS DATE) = k.day)"
        )
    computed = run_ts.strftime("%Y-%m-%d %H:%M:%S.%f")
    return spark.sql(
        f"SELECT b.*, CAST({src_snapshot_id} AS BIGINT) AS src_snapshot_id, TIMESTAMP '{computed}' AS computed_at "
        f"FROM ({bars.bars_sql(trades, th, 'spark')}) b"
    )


def stage_bars(spark, minutes: DataFrame, run_ts: datetime) -> int:
    """Recompute every (exchange, symbol, UTC day) the new trades touch: delete the day's bars, append the fresh ones.

    Delete-then-append rather than MERGE: a late trade shifts every later bar
    boundary in its day, so the row set for the day changes shape, and a
    MERGE keyed on bar_seq would leave a stale tail behind.
    """
    src = _current(spark, TRADES)
    minutes.select("exchange", "canonical_symbol", F.to_date("minute").alias("day")).distinct().createOrReplaceTempView("__days")
    days = spark.table("__days").count()
    if days == 0:
        # A batch that carried no trades touches no day. Returning here rather
        # than appending an empty DataFrame keeps the table's snapshot history a
        # log of recomputes; an empty append is a commit that says nothing.
        print(f"stage 2d: no touched days, {BARS} unchanged")
        return 0
    fresh = _bars(spark, "__days", src, run_ts).cache()
    n = fresh.count()
    # TWO commits, and Iceberg gives no transaction across them: a crash between
    # the DELETE and the append leaves those symbol-days with no bars at all.
    # The nightly `bars_parity` audit (maintenance.audit_bars_parity) recomputes
    # yesterday's bars and compares them to what's stored, so a half-applied day
    # doesn't go undetected past the next audit run. It heals when a later trade
    # touches the same day, or on demand with `make lake-rebuild LAYER=bars`.
    spark.sql(
        f"DELETE FROM {BARS} WHERE (exchange, canonical_symbol, day) IN (SELECT exchange, canonical_symbol, day FROM __days)"
    )
    fresh.writeTo(BARS).option(f"snapshot-property.{O.JOB}", O.JOB_DECODE).append()
    fresh.unpersist()
    print(f"stage 2d: {n} bars over {days} symbol-days recomputed -> {BARS} (gold.trades snapshot {src})")
    return n


def stage(spark, run_ts: datetime) -> int:
    n, minutes = stage_trades(spark, run_ts)
    load_dims(spark, run_ts)
    if minutes is not None:
        stage_ohlcv(spark, minutes, run_ts)
        stage_bars(spark, minutes, run_ts)
    return n


def rebuild_bars(spark, run_ts: datetime) -> int:
    """Every bar over all of gold.trades, into a table the caller has just (re)created."""
    src = _current(spark, TRADES)
    _bars(spark, None, src, run_ts).writeTo(BARS).option(f"snapshot-property.{O.JOB}", O.JOB_DECODE).append()
    n = added_records(spark, BARS)
    print(f"rebuild: bars: {n} rows -> {BARS} (gold.trades snapshot {src})", flush=True)
    return n


def rebuild(spark, run_ts: datetime) -> dict:
    """Whole archive: gold.trades from every silver table, then every candle.

    The caller has dropped and recreated the tables. Trades land in one pass
    per venue (they are a projection, no lookback needed); candles are then
    computed for every bucket present, one timeframe at a time.
    """
    n, _ = stage_trades(spark, run_ts)
    load_dims(spark, run_ts)
    src = _current(spark, TRADES)
    totals = {TRADES: n}
    for tf, (table, seconds) in OHLCV.items():
        started = datetime.now()
        keys = spark.sql(
            f"SELECT DISTINCT exchange, canonical_symbol, "
            f"to_timestamp(floor(unix_timestamp(exchange_ts) / {seconds}) * {seconds}) AS window_start FROM {TRADES}"
        )
        candles(spark, seconds, keys, src, run_ts).writeTo(table).option(f"snapshot-property.{O.JOB}", O.JOB_DECODE).append()
        totals[table] = added_records(spark, table)
        print(f"rebuild: {tf}: {totals[table]} candles in {(datetime.now() - started).total_seconds():.0f} s", flush=True)
    totals[BARS] = rebuild_bars(spark, run_ts)
    return totals
