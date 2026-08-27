#!/usr/bin/env python3
"""
Books — silver per venue and the gold 1 Hz top-20 product, from one replay.

    silver.book_binance    <- bronze.binance_depth20       typed frames (each IS a top-20 snapshot)
    silver.book_kraken     <- bronze.kraken_book           typed frames + checksum_ok by replay
    silver.book_coinbase   <- bronze.coinbase_level2       typed events (one row per events[i])
    gold.book_top20        <- silver.book_*                the book at the end of every second, top 20
    gold.bbo_1s            <- gold.book_top20              a SQL projection
    gold.book_state        the replay's carry-over per (venue, symbol, connection)

**One replay, two outputs.** A venue's book is a state machine over a connection's
frames in `conn_msg_seq` order (docker/lake/book.py). Walking it once yields the
per-frame checksum verdict — silver's flag — and the state at every second
boundary — gold's snapshot. Walking it twice, once per layer, would double the
biggest job in the lake for no information. So `replay()` is called from the
silver stage on freshly typed frames and writes both; `rebuild --layer gold`
calls the same function over silver's rows (gold stays a function of silver).

**Streamed, not materialised.** Kraken delivers ~13 M book frames per venue-day
and a BTC/USD connection can run for half a day; a `groupBy(...).applyInPandas`
would hold a whole connection in memory. The frames are instead
`repartition`ed by (symbol, conn_id), sorted within the partition by
conn_msg_seq, and walked by a Python generator over the partition iterator —
constant memory per partition, one book dict per open connection.

**State between ticks.** A 5-minute tick sees the middle of a connection. The
book after the last frame of each (symbol, conn_id) — top 25 levels for Kraken,
the whole book for Coinbase — is written to gold.book_state and read back as the
starting point next time, so a tick re-reads five minutes, not the connection's
life. A frame that arrives before the connection's snapshot (no state, not a
snapshot) is typed into silver but yields no book: checksum_ok NULL, no
gold rows, exactly what the capture does with the same frame.

Sampling rule (the capture's, ADR-027): for every second from the first frame's
to the last frame's within a connection, the state after the last frame whose
recv_ts_ns < the next second boundary; a second with no frame repeats the previous
state. Binance needs no replay — each depth20 frame is the book — so its gold rows
are the last frame per second.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import book as B
import instruments
from catalog import added_records, snapshot_history
from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

import offsets as O
from spark_conf import CATALOG

BRONZE_INSTRUMENT = f"{CATALOG}.bronze.kraken_instrument"
GOLD_BOOK = f"{CATALOG}.gold.book_top20"
GOLD_BBO = f"{CATALOG}.gold.bbo_1s"
STATE = f"{CATALOG}.gold.book_state"
TOP = 20
KRAKEN_DEPTH = 25  # config/instruments.yaml default; the checksum is defined over the top 10 of it
NS = 1_000_000_000


@dataclass(frozen=True)
class BookSpec:
    exchange: str
    bronze: str
    explode: str  # bronze frames (__b) -> one row per silver row, with src_index
    columns: list  # `expr AS name` in silver order, before the shared lineage tail

    @property
    def table(self) -> str:
        return f"{CATALOG}.silver.book_{self.exchange}"

    @property
    def source(self) -> str:
        return f"{CATALOG}.bronze.{self.bronze}"


_LVL = "transform({arr}, l -> struct(CAST(l[0] AS DECIMAL(28,10)) AS px, CAST(l[1] AS DECIMAL(28,10)) AS qty))"

BOOKS = (
    BookSpec(
        "binance",
        "binance_depth20",
        "SELECT *, 0 AS src_index FROM __b",
        [
            "regexp_extract(stream, '^([a-z0-9]+)@', 1) AS symbol_lc",  # replaced below: the RawMessage symbol is the native one
            "data.lastUpdateId AS last_update_id",
            _LVL.format(arr="data.bids") + " AS bids",
            _LVL.format(arr="data.asks") + " AS asks",
            "CAST(greatest(size(data.bids), size(data.asks)) AS INT) AS depth",
        ],
    ),
    BookSpec(
        "kraken",
        "kraken_book",
        "SELECT b.*, x.d AS d, x.src_index FROM __b b LATERAL VIEW posexplode(data) x AS src_index, d",
        [
            "d.symbol AS symbol",
            "type AS frame_type",
            "transform(d.bids, l -> struct(l.price AS px, l.qty AS qty)) AS bids",
            "transform(d.asks, l -> struct(l.price AS px, l.qty AS qty)) AS asks",
            "d.checksum AS checksum",
            "CAST(d.timestamp AS TIMESTAMP) AS exchange_ts",
        ],
    ),
    BookSpec(
        "coinbase",
        "coinbase_level2",
        "SELECT b.*, x.ev AS ev, x.src_index FROM __b b LATERAL VIEW posexplode(events) x AS src_index, ev",
        [
            "ev.product_id AS symbol",
            "ev.type AS event_type",
            "transform(ev.updates, u -> struct(IF(u.side = 'bid', 'bid', 'ask') AS side, u.side AS side_native, "
            "CAST(u.price_level AS DECIMAL(28,10)) AS px, CAST(u.new_quantity AS DECIMAL(28,10)) AS qty, "
            "CAST(u.event_time AS TIMESTAMP) AS event_time)) AS updates",
            "sequence_num",
            "CAST(timestamp AS TIMESTAMP) AS envelope_ts",
        ],
    ),
)
TABLES = tuple(s.table for s in BOOKS) + (GOLD_BOOK, GOLD_BBO, STATE)
IDENTIFIER_FIELDS = ("src_topic", "src_partition", "src_offset", "src_index")

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


def project(spark, frames: DataFrame, spec: BookSpec, registry: dict, run_ts: datetime) -> DataFrame:
    """Typed silver rows from bronze frames; kraken's checksum_ok and binance's seq_gap added by the callers."""
    frames.createOrReplaceTempView("__b")
    exploded = spark.sql(spec.explode)
    cols = list(spec.columns)
    if spec.exchange == "binance":
        cols[0] = "symbol"  # RawMessage.symbol: the capture attributed every depth20 frame to its instrument
    out = exploded.withColumn("ingest_ts", F.lit(run_ts)).selectExpr(*cols, *_TAIL)
    natives = {r[0] for r in out.select("symbol").distinct().collect()}
    mapping = {n: instruments.canonical(registry, spec.exchange, n) for n in natives}
    canon = F.create_map(*[F.lit(x) for kv in mapping.items() for x in kv])
    return out.withColumn("canonical_symbol", canon[F.col("symbol")])


# ── the replay ──────────────────────────────────────────────────────────────


def kraken_precisions(spark) -> dict:
    """{native symbol: (price_precision, qty_precision)} from the latest instrument frames.

    The last value the venue sent for each pair wins (frames ordered by receive
    time); a pair never seen has no precision and its book cannot be verified —
    checksum_ok stays NULL, never guessed.
    """
    rows = spark.sql(
        f"""
        SELECT symbol, price_precision, qty_precision FROM (
          SELECT p.symbol, p.price_precision, p.qty_precision,
                 row_number() OVER (PARTITION BY p.symbol ORDER BY recv_ts_ns DESC) AS rn
          FROM {BRONZE_INSTRUMENT} LATERAL VIEW explode(data.pairs) x AS p)
        WHERE rn = 1
        """
    ).collect()
    return {r["symbol"]: (int(r["price_precision"]), int(r["qty_precision"])) for r in rows}


def _levels(pairs) -> list:
    """silver ARRAY<STRUCT<px, qty>> (Decimal) -> [(px_e8, qty_e8)]."""
    return [(int(p["px"] * B.SCALE), int(p["qty"] * B.SCALE)) for p in pairs]


def _state_from_row(r) -> tuple:
    book = B.Book()
    for px, qty in zip(r["bid_px_e8"], r["bid_qty_e8"]):
        book.bids[px] = qty
    for px, qty in zip(r["ask_px_e8"], r["ask_qty_e8"]):
        book.asks[px] = qty
    return book, r


def replay_partition(rows, exchange: str, precisions: dict, states: dict, run_ts: datetime):
    """Generator over one partition of silver-typed frames sorted by (symbol, conn_id, conn_msg_seq).

    Yields `("verdict", key, checksum_ok)` per Kraken frame, `("book", Row)` per
    second boundary crossed, and `("state", Row)` at the end of each connection
    seen. `states` is {(symbol, conn_id): Row} from gold.book_state.
    """
    current = None  # (symbol, conn_id)
    book = None
    st = None  # dict of the running state

    def snapshot_row(second_ns: int):
        bids, asks = book.top(TOP)
        return dict(
            exchange=exchange, canonical_symbol=st["canonical_symbol"], symbol=st["symbol"],
            second=datetime.fromtimestamp(second_ns // NS, tz=timezone.utc).replace(tzinfo=None),
            depth=max(len(bids), len(asks)), seq=st["seq"], checksum_ok=st["checksum_ok"],
            bid_px_e8=[p for p, _ in bids], bid_qty_e8=[q for _, q in bids],
            ask_px_e8=[p for p, _ in asks], ask_qty_e8=[q for _, q in asks],
            recv_ts_ns=st["recv_ts_ns"], conn_id=st["conn_id"], conn_msg_seq=st["conn_msg_seq"],
            src_topic=st["src_topic"], src_partition=st["src_partition"], src_offset=st["src_offset"],
            src_index=st["src_index"], ingest_ts=run_ts,
        )

    def state_row():
        bids, asks = book.top(10**6)
        return dict(
            exchange=exchange, symbol=st["symbol"], conn_id=st["conn_id"],
            bid_px_e8=[p for p, _ in bids], bid_qty_e8=[q for _, q in bids],
            ask_px_e8=[p for p, _ in asks], ask_qty_e8=[q for _, q in asks],
            seq=st["seq"], checksum_ok=st["checksum_ok"], last_conn_msg_seq=st["conn_msg_seq"],
            last_recv_ts_ns=st["recv_ts_ns"], last_second=datetime.fromtimestamp(st["last_second_ns"] // NS, tz=timezone.utc).replace(tzinfo=None),
            last_src_partition=st["src_partition"], last_src_offset=st["src_offset"], last_src_index=st["src_index"],
            updated_at=run_ts,
        )

    def flush_seconds(upto_ns: int):
        """Emit the state for every whole second from the last emitted one up to (not including) the second containing upto_ns."""
        if not st["live"]:
            return
        boundary = (upto_ns // NS) * NS  # start of the second containing upto_ns
        sec = st["last_second_ns"] + NS if st["last_second_ns"] is not None else (st["first_ns"] // NS) * NS
        while sec < boundary:
            yield ("book", snapshot_row(sec))
            sec += NS
        st["last_second_ns"] = max(st["last_second_ns"] or 0, sec - NS) if sec - NS >= 0 else st["last_second_ns"]

    for r in rows:
        key = (r["symbol"], r["conn_id"])
        if key != current:
            if current is not None and st is not None and st["live"]:
                yield ("state", state_row())
            current = key
            prev = states.get(key)
            if prev is not None:
                book, _ = _state_from_row(prev)
                st = dict(symbol=r["symbol"], canonical_symbol=r["canonical_symbol"], conn_id=r["conn_id"], live=True,
                          seq=prev["seq"], checksum_ok=prev["checksum_ok"], recv_ts_ns=prev["last_recv_ts_ns"],
                          conn_msg_seq=prev["last_conn_msg_seq"], src_topic=r["src_topic"],
                          src_partition=prev["last_src_partition"], src_offset=prev["last_src_offset"], src_index=prev["last_src_index"],
                          last_second_ns=int(prev["last_second"].replace(tzinfo=timezone.utc).timestamp()) * NS, first_ns=None)
            else:
                book = B.Book()
                st = dict(symbol=r["symbol"], canonical_symbol=r["canonical_symbol"], conn_id=r["conn_id"], live=False,
                          seq=0, checksum_ok=None, recv_ts_ns=0, conn_msg_seq=0, src_topic=r["src_topic"],
                          src_partition=0, src_offset=0, src_index=0, last_second_ns=None, first_ns=None)

        # everything up to this frame's second is decided by the previous state
        yield from flush_seconds(r["recv_ts_ns"])

        if exchange == "kraken":
            is_snapshot = r["frame_type"] == "snapshot"
            if not st["live"] and not is_snapshot:
                yield ("verdict", (r["src_partition"], r["src_offset"], r["src_index"]), None)
                continue
            B.kraken_apply(book, r["frame_type"], _levels(r["bids"]), _levels(r["asks"]), KRAKEN_DEPTH)
            prec = precisions.get(r["symbol"])
            if prec is None:
                ok = None
            else:
                bids, asks = book.top(B.CHECKSUM_LEVELS)
                ok = B.kraken_checksum(asks, bids, prec[0], prec[1]) == int(r["checksum"])
            st["checksum_ok"] = ok
            yield ("verdict", (r["src_partition"], r["src_offset"], r["src_index"]), ok)
        else:  # coinbase
            is_snapshot = r["event_type"] == "snapshot"
            if not st["live"] and not is_snapshot:
                continue
            if is_snapshot:
                book.clear()
            for u in r["updates"]:
                book.apply(u["side"], int(u["px"] * B.SCALE), int(u["qty"] * B.SCALE))
            st["seq"] = int(r["sequence_num"])

        if not st["live"]:
            st["live"] = True
            st["first_ns"] = r["recv_ts_ns"]
        st.update(recv_ts_ns=r["recv_ts_ns"], conn_msg_seq=r["conn_msg_seq"], src_partition=r["src_partition"],
                  src_offset=r["src_offset"], src_index=r["src_index"])

    if current is not None and st is not None and st["live"]:
        # Emit every second BEFORE the one containing the last frame. That last
        # second is incomplete — a later frame may still land in it — so it is
        # carried in gold.book_state and emitted by the next tick's first flush.
        # A connection that never sends another frame therefore never gets its
        # final second: one sample per connection end, by design, not a hole in
        # a live book.
        yield from flush_seconds(st["recv_ts_ns"])
        yield ("state", state_row())


def replay(spark, exchange: str, typed: DataFrame, run_ts: datetime, precisions: dict | None = None) -> tuple:
    """Run the replay over typed frames of one venue. Returns (verdicts DF | None, books DF, state DF)."""
    prev = {}
    try:
        for r in spark.table(STATE).where(F.col("exchange") == exchange).collect():
            prev[(r["symbol"], r["conn_id"])] = r
    except Exception:  # noqa: BLE001 - first run: no state table rows yet
        prev = {}
    # The generator runs in the executors' Python workers, which do not share
    # the driver's sys.path: ship this module and book.py with the job.
    # Every module in this directory: books.py imports instruments, offsets,
    # catalog and spark_conf at module level, and the worker resolves them all.
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(here)):
        if name.endswith(".py"):
            spark.sparkContext.addPyFile(os.path.join(here, name))
    bc_prev = spark.sparkContext.broadcast(prev)
    bc_prec = spark.sparkContext.broadcast(precisions or {})
    ordered = typed.repartition("symbol", "conn_id").sortWithinPartitions("symbol", "conn_id", "conn_msg_seq", "src_index")
    book_schema, state_schema = spark.table(GOLD_BOOK).schema, spark.table(STATE).schema
    book_names, state_names = [f.name for f in book_schema.fields], [f.name for f in state_schema.fields]

    def walk(rows):
        yield from replay_partition(rows, exchange, bc_prec.value, bc_prev.value, run_ts)

    out = ordered.rdd.mapPartitions(walk)
    out.persist()
    # dicts -> tuples in the table's column order: createDataFrame matches positionally
    books = spark.createDataFrame(out.filter(lambda t: t[0] == "book").map(lambda t: tuple(t[1][n] for n in book_names)), book_schema)
    state = spark.createDataFrame(out.filter(lambda t: t[0] == "state").map(lambda t: tuple(t[1][n] for n in state_names)), state_schema)
    verdicts = None
    if exchange == "kraken":
        verdicts = spark.createDataFrame(
            out.filter(lambda t: t[0] == "verdict").map(lambda t: (t[1][0], t[1][1], t[1][2], t[2])),
            "src_partition int, src_offset bigint, src_index int, checksum_ok boolean",
        )
    return verdicts, books, state, out


def binance_books(typed: DataFrame, run_ts: datetime) -> DataFrame:
    """Each depth20 frame is the book: the last frame per (symbol, second) is the state at the end of the second."""
    last = typed.withColumn("second", F.date_trunc("second", "recv_ts")).withColumn(
        "_rn", F.row_number().over(Window.partitionBy("symbol", "second").orderBy(F.desc("recv_ts_ns"), F.desc("conn_msg_seq")))
    ).where("_rn = 1")
    e8 = lambda arr, f: F.expr(f"transform({arr}, l -> CAST(l.{f} * 100000000 AS BIGINT))")  # noqa: E731
    return last.select(
        F.lit("binance").alias("exchange"), "canonical_symbol", "symbol", "second",
        F.least(F.lit(TOP), F.col("depth")).alias("depth"), F.col("last_update_id").alias("seq"),
        F.lit(None).cast("boolean").alias("checksum_ok"),
        F.slice(e8("bids", "px"), 1, TOP).alias("bid_px_e8"), F.slice(e8("bids", "qty"), 1, TOP).alias("bid_qty_e8"),
        F.slice(e8("asks", "px"), 1, TOP).alias("ask_px_e8"), F.slice(e8("asks", "qty"), 1, TOP).alias("ask_qty_e8"),
        "recv_ts_ns", "conn_id", "conn_msg_seq", "src_topic", "src_partition", "src_offset", "src_index",
        F.lit(run_ts).alias("ingest_ts"),
    )


def bbo(spark, books: DataFrame, src_snapshot_id) -> DataFrame:
    books.createOrReplaceTempView("__books")
    return spark.sql(
        f"""
        SELECT exchange, canonical_symbol, second,
               bid_px_e8[0] AS bid_e8, bid_qty_e8[0] AS bid_qty_e8, ask_px_e8[0] AS ask_e8, ask_qty_e8[0] AS ask_qty_e8,
               (bid_px_e8[0] + ask_px_e8[0]) / 2e8 AS mid,
               (ask_px_e8[0] - bid_px_e8[0]) / ((bid_px_e8[0] + ask_px_e8[0]) / 2) * 10000 AS spread_bps,
               bid_qty_e8[0] / (bid_qty_e8[0] + ask_qty_e8[0]) AS imbalance,
               (bid_px_e8[0] * ask_qty_e8[0] + ask_px_e8[0] * bid_qty_e8[0]) / (bid_qty_e8[0] + ask_qty_e8[0]) / 1e8 AS microprice,
               checksum_ok, CAST({src_snapshot_id} AS BIGINT) AS src_snapshot_id
        FROM __books WHERE size(bid_px_e8) > 0 AND size(ask_px_e8) > 0
        """
    )


# ── stages ──────────────────────────────────────────────────────────────────


def _current(spark, table: str):
    rows = spark.sql(f"SELECT snapshot_id FROM {table}.refs WHERE name = 'main'").collect()
    return rows[0][0] if rows else None


def _write_state(spark, state: DataFrame) -> None:
    state.createOrReplaceTempView("__state")
    spark.sql(
        f"""MERGE INTO {STATE} t USING __state s ON t.exchange = s.exchange AND t.symbol = s.symbol AND t.conn_id = s.conn_id
            WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *"""
    )


def process(spark, spec: BookSpec, frames: DataFrame, src_snapshot_id, registry: dict, run_ts: datetime, precisions: dict) -> dict:
    """Type one venue's frames into silver and replay them into gold. Returns rows written per table."""
    typed = project(spark, frames, spec, registry, run_ts)
    written = {}
    if spec.exchange == "binance":
        w = Window.partitionBy("symbol", "conn_id").orderBy("conn_msg_seq")
        typed = typed.withColumn("_prev", F.lag("last_update_id").over(w)).withColumn(
            "seq_gap", F.when(F.col("_prev").isNull(), F.lit(None).cast("boolean")).otherwise(F.col("last_update_id") <= F.col("_prev"))
        ).drop("_prev")
        typed.persist()
        books = binance_books(typed, run_ts)
        state = None
    else:
        verdicts, books, state, rdd = replay(spark, spec.exchange, typed, run_ts, precisions if spec.exchange == "kraken" else None)
        if verdicts is not None:
            typed = typed.join(verdicts, ["src_partition", "src_offset", "src_index"], "left")
        else:
            typed = typed.withColumn("seq_gap", F.lit(None).cast("boolean"))
    columns = [f.name for f in spark.table(spec.table).schema.fields]
    (
        typed.select(*columns).writeTo(spec.table)
        .option(f"snapshot-property.{O.JOB}", O.JOB_DECODE)
        .option(f"snapshot-property.{O.SRC_SNAPSHOT_ID}", str(src_snapshot_id))
        .append()
    )
    written[spec.table] = added_records(spark, spec.table)
    print(f"stage 2e: {written[spec.table]} rows -> {spec.table} (src snapshot {src_snapshot_id})")
    gcols = [f.name for f in spark.table(GOLD_BOOK).schema.fields]
    before = _current(spark, GOLD_BOOK)
    (
        books.select(*gcols).writeTo(GOLD_BOOK)
        .option(f"snapshot-property.{O.JOB}", O.JOB_DECODE)
        .option(f"snapshot-property.{O.SRC_SNAPSHOT_ID}.{spec.exchange}", str(src_snapshot_id))
        .append()
    )
    written[GOLD_BOOK] = added_records(spark, GOLD_BOOK)
    print(f"stage 2e: {written[GOLD_BOOK]} rows -> {GOLD_BOOK} ({spec.exchange})")
    snap = _current(spark, GOLD_BOOK)
    # Only the rows this append added: the snapshot range, not ingest_ts — a
    # rebuild shares one run_ts across its day slices.
    reader = spark.read.format("iceberg")
    reader = reader.option("start-snapshot-id", before).option("end-snapshot-id", snap) if before else reader.option("snapshot-id", snap)
    new_books = reader.load(GOLD_BOOK)
    bbo(spark, new_books, snap).writeTo(GOLD_BBO).option(f"snapshot-property.{O.JOB}", O.JOB_DECODE).append()
    written[GOLD_BBO] = added_records(spark, GOLD_BBO)
    if state is not None:
        _write_state(spark, state)
        rdd.unpersist()
    else:
        typed.unpersist()
    return written


def stage(spark, run_ts: datetime) -> int:
    registry = instruments.load()
    precisions = kraken_precisions(spark)
    total = 0
    for spec in BOOKS:
        end = _current(spark, spec.source)
        if end is None:
            continue
        previous = O.latest_summary(snapshot_history(spark, spec.table), O.JOB_DECODE)
        start = previous.get(O.SRC_SNAPSHOT_ID) if previous else None
        if start and str(start) == str(end):
            print(f"stage 2e: {spec.table} level with {spec.source}, nothing to type")
            continue
        reader = spark.read.format("iceberg")
        if start:
            reader = reader.option("start-snapshot-id", start).option("end-snapshot-id", end)
        else:
            reader = reader.option("snapshot-id", end)
        total += sum(process(spark, spec, reader.load(spec.source), end, registry, run_ts, precisions).values())
    return total


def rebuild(spark, run_ts: datetime, exchanges=None) -> dict:
    """Whole archive, one bronze day per venue in order (the state carries across days through gold.book_state)."""
    registry = instruments.load()
    precisions = kraken_precisions(spark)
    totals = {}
    for spec in BOOKS:
        if exchanges and spec.exchange not in exchanges:
            continue
        end = _current(spark, spec.source)
        if end is None:
            continue
        days = [r[0] for r in spark.sql(f"SELECT DISTINCT to_date(recv_ts) d FROM {spec.source} ORDER BY d").collect()]
        for day in days:
            started = datetime.now()
            frames = spark.read.format("iceberg").option("snapshot-id", end).load(spec.source).where(F.to_date("recv_ts") == F.lit(day))
            w = process(spark, spec, frames, end, registry, run_ts, precisions)
            for t, n in w.items():
                totals[t] = totals.get(t, 0) + n
            print(f"rebuild: {spec.exchange} {day}: {w} in {(datetime.now() - started).total_seconds():.0f} s", flush=True)
    return totals
