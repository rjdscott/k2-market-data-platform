#!/usr/bin/env python3
"""
Silver per venue — bronze frames typed, annotated and flattened to one row per
trade, every delivery kept (ADR-026, plan 004). Trades in this module; books
follow once Kraken's checksum verification has the `instrument` frames beside
the book frames.

    silver.trades_binance    <- bronze.binance_trade            1 trade per frame
    silver.trades_kraken     <- bronze.kraken_trade             data[i]
    silver.trades_coinbase   <- bronze.coinbase_market_trades   events[].trades[j]

**What silver adds, and what it refuses to do.** Types (exact DECIMAL, UTC
TIMESTAMP), the canonical symbol from config/instruments.yaml — the registry
the capture subscribes from, so a symbol the registry does not list stops the
run rather than being guessed — a normalised taker side beside the venue's own,
and three flags: `venue_replay`, `seq_gap`, `precision_loss`. It drops nothing.
A replayed trade is a row with a flag; gold is where one row per logical trade
is decided.

**Flags need history, so a batch is scored against a lookback.** Whether this
delivery is a replay, and whether the previous trade id for this symbol is
adjacent, are questions about rows that may have landed hours ago. Each run
scores `batch ∪ (silver rows of the last LOOKBACK)` with two window functions
and writes only the batch. Coinbase re-sends within ~2 h of a subscribe and the
thinnest instrument here trades several times an hour, so a day is generous;
a symbol with no prior trade inside the window gets `seq_gap = NULL`, not
false — unknown is not the same as intact.

**Trade ids are sequential per symbol on all three venues**, which is what makes
`seq_gap` a completeness measurement rather than a heuristic. Measured over the
archive on 2026-08-27: Binance 8,667,843 consecutive ids vs 144 jumps, Coinbase
1,586,733 vs 72, Kraken 155,303 vs 141. A jump is trades the archive never
received — a capture restart, a produce-error drop, a retention eviction — and
`missing_before` is how many.

Incremental by bronze snapshot, exactly as bronze is by raw: each silver table
records the bronze snapshot it last read in `k2.src-snapshot-id`; rebuild.py
drops, recreates and replays by day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import instruments
from catalog import added_records, snapshot_history
from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

import offsets as O
from spark_conf import CATALOG

# How far back a run reads silver to score venue_replay and seq_gap. See the
# module docstring; a day is ~12x the longest replay lag seen.
LOOKBACK = timedelta(days=1)

# Taker side, normalised. Binance sends the buyer-is-maker flag: the taker is
# the seller when the buyer made the market.
SIDE_SQL = {
    "binance": "IF(data.m, 'sell', 'buy')",
    "kraken": "d.side",
    "coinbase": "lower(t.side)",
}


@dataclass(frozen=True)
class TradeSpec:
    exchange: str
    bronze: str  # bare bronze table name
    # SQL fragment that turns one bronze frame row into one row per trade with
    # a `src_index`. Bronze columns are referenced as-is; the exploded element
    # gets the alias the projection below uses (d / t).
    explode: str
    # Columns in the silver table's order, each `expr AS name`; the shared head
    # and tail are added by `project()`.
    venue_columns: list

    @property
    def table(self) -> str:
        return f"{CATALOG}.silver.trades_{self.exchange}"

    @property
    def source(self) -> str:
        return f"{CATALOG}.bronze.{self.bronze}"


TRADES = (
    TradeSpec(
        "binance",
        "binance_trade",
        # One trade per frame; the payload is `data`.
        "SELECT *, 0 AS src_index FROM __b",
        [
            "data.s AS symbol",
            "CAST(data.t AS STRING) AS trade_id",
            "data.t AS trade_seq",
            "CAST(data.p AS DECIMAL(28,10)) AS price",
            "CAST(data.q AS DECIMAL(28,10)) AS qty",
            f"{SIDE_SQL['binance']} AS side",
            "CAST(data.m AS STRING) AS side_native",
            "timestamp_millis(data.T) AS exchange_ts",
            "data.e AS event_type",
            "timestamp_millis(data.E) AS event_time",
            "data.m AS buyer_is_maker",
            "data.M AS ignore_flag",
            "stream",
        ],
    ),
    TradeSpec(
        "kraken",
        "kraken_trade",
        "SELECT b.*, x.d AS d, x.src_index FROM __b b LATERAL VIEW posexplode(data) x AS src_index, d",
        [
            "d.symbol AS symbol",
            "CAST(d.trade_id AS STRING) AS trade_id",
            "d.trade_id AS trade_seq",
            "d.price AS price",
            "d.qty AS qty",
            f"{SIDE_SQL['kraken']} AS side",
            "d.side AS side_native",
            "CAST(d.timestamp AS TIMESTAMP) AS exchange_ts",
            "d.ord_type AS ord_type",
            "type AS frame_type",
        ],
    ),
    TradeSpec(
        "coinbase",
        "coinbase_market_trades",
        # Two levels: events[i].trades[j]. src_index is the flat position in
        # the order sent — trades of event 0, then of event 1 — via a running
        # offset over the events' sizes, so it is unique within the frame.
        "SELECT b.*, e.ev AS ev, t.t AS t, "
        "  CAST(aggregate(slice(events, 1, e.i), 0, (acc, x) -> acc + size(x.trades)) + t.j AS INT) AS src_index "
        "FROM __b b LATERAL VIEW posexplode(events) e AS i, ev LATERAL VIEW posexplode(ev.trades) t AS j, t",
        [
            "t.product_id AS symbol",
            "t.trade_id AS trade_id",
            "CAST(t.trade_id AS BIGINT) AS trade_seq",
            "CAST(t.price AS DECIMAL(28,10)) AS price",
            "CAST(t.size AS DECIMAL(28,10)) AS qty",
            f"{SIDE_SQL['coinbase']} AS side",
            "t.side AS side_native",
            "CAST(t.time AS TIMESTAMP) AS exchange_ts",
            "CAST(timestamp AS TIMESTAMP) AS envelope_ts",
            "sequence_num",
            "ev.type AS event_type",
        ],
    ),
)

TABLES = tuple(t.table for t in TRADES)
IDENTIFIER_FIELDS = ("src_topic", "src_partition", "src_offset", "src_index")

# The columns every silver trades table shares, in DDL order, after the venue's
# own. The flags are filled by `score()`.
_TAIL = [
    "recv_ts_ns",
    "timestamp_micros(recv_ts_ns div 1000) AS recv_ts",
    "conn_id",
    "conn_msg_seq",
    "src_topic",
    "src_partition",
    "src_offset",
    "src_index",
    "ingest_ts",
]


def project(spark, frames: DataFrame, spec: TradeSpec, registry: dict, run_ts: datetime) -> DataFrame:
    """One typed row per trade from one venue's bronze frames, flags not yet scored."""
    frames.createOrReplaceTempView("__b")
    exploded = spark.sql(spec.explode)
    natives = {r[0] for r in exploded.selectExpr(spec.venue_columns[0]).distinct().collect()}
    mapping = {n: instruments.canonical(registry, spec.exchange, n) for n in natives}  # raises on unknown
    canon = F.create_map(*[F.lit(x) for kv in mapping.items() for x in kv])
    out = exploded.withColumn("ingest_ts", F.lit(run_ts)).selectExpr(*spec.venue_columns, *_TAIL)
    return out.withColumn("canonical_symbol", canon[F.col("symbol")]).withColumn(
        "precision_loss",
        (F.col("price") != F.round(F.col("price"), 8)) | (F.col("qty") != F.round(F.col("qty"), 8)),
    )


def score(batch: DataFrame, lookback: DataFrame) -> DataFrame:
    """venue_replay and seq_gap over `batch ∪ lookback`; returns the batch rows only.

    Two windows. Replay: within (symbol, trade_id), the first delivery by
    (recv_ts_ns, lineage) is the original; every later row is a replay. Gap:
    over the ORIGINAL deliveries only, ordered by trade_seq within symbol, the
    previous id must be adjacent. Replays are excluded from the gap window so a
    re-sent old trade does not look like a jump backwards.
    """
    keys = ["symbol", "trade_id", "trade_seq", "recv_ts_ns", "src_partition", "src_offset", "src_index"]
    both = batch.withColumn("_new", F.lit(True)).unionByName(
        lookback.select(*keys).withColumn("_new", F.lit(False)), allowMissingColumns=True
    )
    by_delivery = Window.partitionBy("symbol", "trade_id").orderBy(
        "recv_ts_ns", "src_partition", "src_offset", "src_index"
    )
    both = both.withColumn("venue_replay", F.row_number().over(by_delivery) > 1)
    by_seq = Window.partitionBy("symbol").orderBy("trade_seq")
    prev = F.lag("trade_seq").over(by_seq)
    originals = both.where(~F.col("venue_replay")).withColumn("_prev", prev)
    gaps = originals.select(
        "symbol", "trade_id", "recv_ts_ns", "src_partition", "src_offset", "src_index",
        F.when(F.col("_prev").isNull(), F.lit(None).cast("boolean"))
        .otherwise(F.col("trade_seq") - F.col("_prev") > 1)
        .alias("seq_gap"),
        F.when(F.col("_prev").isNull(), F.lit(None).cast("bigint"))
        .otherwise(F.col("trade_seq") - F.col("_prev") - 1)
        .alias("missing_before"),
    )
    scored = both.where("_new").drop("_new").join(
        gaps, ["symbol", "trade_id", "recv_ts_ns", "src_partition", "src_offset", "src_index"], "left"
    )
    # A replay has no gap of its own: the original carries it.
    return scored.withColumn(
        "seq_gap", F.when(F.col("venue_replay"), F.lit(False)).otherwise(F.col("seq_gap"))
    ).withColumn("missing_before", F.when(F.col("venue_replay"), F.lit(0)).otherwise(F.col("missing_before")))


def _lookback(spark, spec: TradeSpec, since: datetime) -> DataFrame:
    return spark.table(spec.table).where(F.col("recv_ts") >= F.lit(since))


def write(spark, spec: TradeSpec, frames: DataFrame, src_snapshot_id, registry: dict, run_ts: datetime) -> int:
    """Type, score and append one batch of bronze frames. Returns rows written."""
    batch = project(spark, frames, spec, registry, run_ts)
    bounds = batch.select(F.min("recv_ts").alias("lo")).collect()[0]
    if bounds["lo"] is None:
        print(f"stage 2c: nothing new for {spec.table}")
        return 0
    scored = score(batch, _lookback(spark, spec, bounds["lo"] - LOOKBACK))
    columns = [f.name for f in spark.table(spec.table).schema.fields]
    (
        scored.select(*columns)
        .writeTo(spec.table)
        .option(f"snapshot-property.{O.JOB}", O.JOB_DECODE)
        .option(f"snapshot-property.{O.SRC_SNAPSHOT_ID}", str(src_snapshot_id))
        .append()
    )
    written = added_records(spark, spec.table)
    print(f"stage 2c: {written} rows -> {spec.table} (src snapshot {src_snapshot_id})")
    return written


def _current(spark, table: str):
    rows = spark.sql(f"SELECT snapshot_id FROM {table}.refs WHERE name = 'main'").collect()
    return rows[0][0] if rows else None


def stage(spark, run_ts: datetime) -> int:
    """The incremental step: each silver table reads its bronze table past the snapshot it last saw."""
    registry = instruments.load()
    total = 0
    for spec in TRADES:
        end = _current(spark, spec.source)
        if end is None:
            print(f"stage 2c: {spec.source} has no snapshots yet")
            continue
        previous = O.latest_summary(snapshot_history(spark, spec.table), O.JOB_DECODE)
        start = previous.get(O.SRC_SNAPSHOT_ID) if previous else None
        if start and str(start) == str(end):
            print(f"stage 2c: {spec.table} level with {spec.source}, nothing to type")
            continue
        reader = spark.read.format("iceberg")
        if start:
            reader = reader.option("start-snapshot-id", start).option("end-snapshot-id", end)
        else:
            reader = reader.option("snapshot-id", end)
        total += write(spark, spec, reader.load(spec.source), end, registry, run_ts)
    return total


def rebuild(spark, run_ts: datetime, exchanges=None) -> dict:
    """Whole archive, one bronze day (recv_ts) per venue at a time, oldest first.

    Days in order matter: the lookback for day N is day N-1's silver rows,
    which this loop has just written. rebuild.py drops and recreates first.
    """
    registry = instruments.load()
    totals = {}
    for spec in TRADES:
        if exchanges and spec.exchange not in exchanges:
            continue
        end = _current(spark, spec.source)
        if end is None:
            continue
        days = [r[0] for r in spark.sql(f"SELECT DISTINCT to_date(recv_ts) d FROM {spec.source} ORDER BY d").collect()]
        for day in days:
            started = datetime.now()
            frames = spark.read.format("iceberg").option("snapshot-id", end).load(spec.source).where(F.to_date("recv_ts") == F.lit(day))
            n = write(spark, spec, frames, end, registry, run_ts)
            totals[spec.table] = totals.get(spec.table, 0) + n
            print(f"rebuild: {spec.exchange} {day}: {n} rows in {(datetime.now() - started).total_seconds():.0f} s", flush=True)
    return totals
