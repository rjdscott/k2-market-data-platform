#!/usr/bin/env python3
"""
Bronze per venue — `raw.messages` decoded into six columnar tables that keep the
venue's own field names and JSON types (ADR-026, plan 004).

    bronze.binance_trade            market.crypto.v3.raw.binance   stream = trade
    bronze.binance_depth20          market.crypto.v3.raw.binance   stream = depth20
    bronze.kraken_trade             market.crypto.v3.raw.kraken    stream = trade
    bronze.kraken_book              market.crypto.v3.raw.kraken    stream = book
    bronze.coinbase_market_trades   market.crypto.v3.raw.coinbase  stream = market_trades
    bronze.coinbase_level2          market.crypto.v3.raw.coinbase  stream = l2_data

**One row per frame, not per trade.** A Kraken `trade` frame carries N trades in
`data[]`; a Coinbase `market_trades` frame carries N trades under `events[].trades[]`.
Bronze keeps that nesting as `ARRAY<STRUCT>` so the row is the frame the venue
sent and `(src_topic, src_partition, src_offset)` is one-to-one with the archive.
Exploding to one row per trade is silver's job (with a position-in-frame index
so the lineage survives the explode).

**Types are the JSON's, not ours.** Binance and Coinbase quote prices as strings
and bronze stores STRING; Kraken sends JSON numbers and bronze stores
DECIMAL(28,10), which is the lossless reading of a JSON number literal — Spark's
JSON parser hands the digits to the decimal without going through a double, so
`949.90293702` is stored as those digits and not as the nearest float64
(measured in the 2026-08-27 spike; docker/lake/README.md). Timestamps stay in
the venue's own form: epoch milliseconds for Binance, RFC 3339 strings for the
other two. Silver types them.

**Decoded from raw JSON, never from the Avro Trade/Book topics.** The Rust
capture parsed these frames once already, and the whole point of a second decode
in Spark is that a capture bug is repairable here without a replay: if the two
disagree, bronze is the one that read the frame as sent.

**What is NOT here.** Control frames — `heartbeat`, `subscriptions`, `status`,
`instrument`, `control` — stay verbatim in `raw.messages` and are counted, not
decoded: their value is forensic ("why did ETH go quiet at 03:12"), and a table
per venue-control-shape is a table nobody queries. The per-run parity line
accounts for every one of them, so "not decoded" is never "lost".

A frame Spark cannot parse as the declared shape is still written — venue
columns NULL, lineage intact — and the nightly `bronze_unparseable` audit
(maintenance.py) counts those rows and fails on any. The archive already holds
the bytes, so the alternative — failing the run — would block every following
run on the same snapshot range forever, which is the schema-id lesson from
ingest.py all over again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from catalog import (
    UnresolvableSchema,
    added_records,
    fetch_schema,
    snapshot_history,
    write_audit_rows,
)
from pyspark.sql import DataFrame, Row
from pyspark.sql import functions as F
from pyspark.sql.avro.functions import from_avro

import offsets as O
import wire
from spark_conf import CATALOG

RAW_TABLE = f"{CATALOG}.raw.messages"

# The columns every bronze table carries beside the venue's own. `symbol` is the
# RawMessage's attribution (native spelling, NULL for a frame that concerns no
# single instrument, e.g. a Coinbase level2 envelope batching several products)
# and is kept beside the payload's own symbol field rather than instead of it.
LINEAGE_COLUMNS = (
    "symbol",
    "recv_ts_ns",
    "recv_ts",
    "conn_id",
    "conn_msg_seq",
    "src_topic",
    "src_partition",
    "src_offset",
    "ingest_ts",
)
IDENTIFIER_FIELDS = ("src_topic", "src_partition", "src_offset")


@dataclass(frozen=True)
class VenueTable:
    """One bronze table: where its frames come from and what shape they have."""

    name: str  # bare table name under the bronze namespace
    exchange: str
    stream: str  # RawMessage.stream value the capture assigned
    schema: str  # Spark DDL for `from_json`; the same shape as ddl/lake.sql, minus lineage
    required: str  # a top-level field that is NULL only when the frame did not parse
    # {json path: expected keys}. The nightly schema-drift audit samples raw
    # frames and lists the keys the venue sends at each path that this table
    # does not declare. Only paths whose keys are fixed are listed — the
    # Coinbase `events[]`/`updates[]` and Kraken `data[]` objects are.
    keys: dict

    @property
    def table(self) -> str:
        return f"{CATALOG}.bronze.{self.name}"

    @property
    def topic_suffix(self) -> str:
        return f".raw.{self.exchange}"


_PX_QTY = "ARRAY<STRUCT<price: DECIMAL(28,10), qty: DECIMAL(28,10)>>"

VENUE_TABLES = (
    VenueTable(
        "binance_trade",
        "binance",
        "trade",
        "stream STRING, data STRUCT<e: STRING, E: BIGINT, s: STRING, t: BIGINT, p: STRING, "
        "q: STRING, T: BIGINT, m: BOOLEAN, M: BOOLEAN>",
        "data",
        {"$": {"stream", "data"}, "$.data": {"e", "E", "s", "t", "p", "q", "T", "m", "M"}},
    ),
    VenueTable(
        "binance_depth20",
        "binance",
        "depth20",
        # bids/asks arrive as [["price","qty"], ...] — two-element string arrays,
        # not objects. Kept as ARRAY<ARRAY<STRING>>: naming the two positions
        # would be the first interpretation, and bronze makes none.
        "stream STRING, data STRUCT<lastUpdateId: BIGINT, bids: ARRAY<ARRAY<STRING>>, "
        "asks: ARRAY<ARRAY<STRING>>>",
        "data",
        {"$": {"stream", "data"}, "$.data": {"lastUpdateId", "bids", "asks"}},
    ),
    VenueTable(
        "kraken_trade",
        "kraken",
        "trade",
        "channel STRING, type STRING, data ARRAY<STRUCT<symbol: STRING, side: STRING, "
        "price: DECIMAL(28,10), qty: DECIMAL(28,10), ord_type: STRING, trade_id: BIGINT, "
        "timestamp: STRING>>",
        "channel",
        {
            "$": {"channel", "type", "data"},
            "$.data[0]": {"symbol", "side", "price", "qty", "ord_type", "trade_id", "timestamp"},
        },
    ),
    VenueTable(
        "kraken_book",
        "kraken",
        "book",
        f"channel STRING, type STRING, data ARRAY<STRUCT<symbol: STRING, bids: {_PX_QTY}, "
        f"asks: {_PX_QTY}, checksum: BIGINT, timestamp: STRING>>",
        "channel",
        {
            "$": {"channel", "type", "data"},
            "$.data[0]": {"symbol", "bids", "asks", "checksum", "timestamp"},
        },
    ),
    VenueTable(
        "coinbase_market_trades",
        "coinbase",
        "market_trades",
        "channel STRING, timestamp STRING, sequence_num BIGINT, events ARRAY<STRUCT<type: STRING, "
        "trades: ARRAY<STRUCT<product_id: STRING, trade_id: STRING, price: STRING, size: STRING, "
        "time: STRING, side: STRING>>>>",
        "channel",
        {
            "$": {"channel", "timestamp", "sequence_num", "events"},
            "$.events[0]": {"type", "trades"},
            "$.events[0].trades[0]": {"product_id", "trade_id", "price", "size", "time", "side"},
        },
    ),
    VenueTable(
        "coinbase_level2",
        "coinbase",
        "l2_data",
        "channel STRING, timestamp STRING, sequence_num BIGINT, events ARRAY<STRUCT<type: STRING, "
        "product_id: STRING, updates: ARRAY<STRUCT<side: STRING, event_time: STRING, "
        "price_level: STRING, new_quantity: STRING>>>>",
        "channel",
        {
            "$": {"channel", "timestamp", "sequence_num", "events"},
            "$.events[0]": {"type", "product_id", "updates"},
            "$.events[0].updates[0]": {"side", "event_time", "price_level", "new_quantity"},
        },
    ),
)

TABLES = tuple(t.table for t in VENUE_TABLES)


def route(exchange: str, stream: str):
    """The VenueTable a `(exchange, stream)` pair decodes into, or None."""
    for t in VENUE_TABLES:
        if t.exchange == exchange and t.stream == stream:
            return t
    return None


def routed_streams(exchange: str) -> list:
    return [t.stream for t in VENUE_TABLES if t.exchange == exchange]


def drift(seen: dict, expected: dict) -> dict:
    """Keys the venue sent that the table does not declare, per path.

    `seen` is `{path: set(keys observed)}` over a sample; `expected` is
    `VenueTable.keys`. A declared key the venue stopped sending is not drift —
    the column reads NULL and nothing was lost — so only the one direction is
    reported. Pure; tests/test_lake_bronze.py runs it.
    """
    out = {}
    for path, keys in expected.items():
        extra = set(seen.get(path, ())) - keys
        if extra:
            out[path] = sorted(extra)
    return out


# ── the decode ──────────────────────────────────────────────────────────────


def _decoded_raw(source: DataFrame, exchange: str) -> tuple:
    """`(frames, unresolvable)`: the venue's raw rows with the RawMessage decoded to `r`.

    One `from_avro` per writer schema id present in the range, unioned, exactly
    as ingest.py's stage 2 does it and for the same reason: a batch that spans a
    schema evolution must decode each record against the schema that wrote it.
    """
    schema_ids = [r[0] for r in source.select("schema_id").distinct().collect()]
    parts, unresolvable = [], []
    for schema_id in sorted(schema_ids):
        try:
            schema = fetch_schema(schema_id)
        except UnresolvableSchema as exc:
            unresolvable.append((schema_id, str(exc)))
            continue
        parts.append(
            source.where(F.col("schema_id") == schema_id).withColumn(
                "r", from_avro(F.expr(wire.body_expr("payload")), schema, {"mode": "FAILFAST"})
            )
        )
    if not parts:
        return None, unresolvable
    frames = parts[0]
    for part in parts[1:]:
        frames = frames.unionByName(part)
    return frames, unresolvable


def _project(frames: DataFrame, t: VenueTable) -> DataFrame:
    """The venue's fields as columns, plus lineage. Unparseable frames come out with `required` NULL."""
    parsed = frames.where(F.col("r.stream") == t.stream).withColumn(
        "j",
        # PERMISSIVE, not FAILFAST: a frame the parser rejects becomes a NULL
        # struct that the caller counts and files, instead of an exception that
        # blocks the snapshot range for every following run. The bytes are in
        # raw.messages either way.
        F.from_json(F.col("r.payload").cast("string"), t.schema, {"mode": "PERMISSIVE"}),
    )
    return parsed.select(
        F.col("j.*"),
        F.col("r.symbol").alias("symbol"),
        F.col("r.recv_ts_ns").alias("recv_ts_ns"),
        F.expr("timestamp_micros(r.recv_ts_ns div 1000)").alias("recv_ts"),
        F.col("r.conn_id").alias("conn_id"),
        F.col("r.conn_msg_seq").alias("conn_msg_seq"),
        F.col("topic").alias("src_topic"),
        F.col("partition").alias("src_partition"),
        F.col("offset").alias("src_offset"),
        F.col("ingest_ts"),
    )


def decode(spark, source: DataFrame, raw_snapshot_id, run_ts: datetime, exchange: str) -> dict:
    """Decode one venue's raw rows in `source` into its bronze tables.

    Returns `{table: rows written}` plus the bookkeeping the parity line needs:
    the parity line prints. Every write carries
    `k2.src-snapshot-id = raw_snapshot_id` so the next incremental read resumes
    after it. `source` must already be filtered to the venue's raw topic and to
    `schema_id IS NOT NULL`.
    """
    frames, unresolvable = _decoded_raw(source, exchange)
    findings = []
    if unresolvable:
        findings += [
            Row(
                run_ts=run_ts,
                job="ingest",
                check_name="unresolvable_schema_id",
                scope=f"bronze.{exchange}/schema_id={schema_id}",
                passed=False,
                observed=int(schema_id),
                detail=detail,
            )
            for schema_id, detail in unresolvable
        ]
    written = {}
    if frames is not None:
        for t in (t for t in VENUE_TABLES if t.exchange == exchange):
            # One pass per table: the write IS the decode. A frame that did not
            # parse lands with its venue columns NULL and its lineage intact,
            # and the nightly `bronze_unparseable` audit (maintenance.py) counts
            # `WHERE <required> IS NULL`. Filtering them out here would cost a
            # second full decode of the range just to learn there were none.
            (
                _project(frames, t)
                .writeTo(t.table)
                .option(f"snapshot-property.{O.JOB}", O.JOB_DECODE)
                .option(f"snapshot-property.{O.SRC_SNAPSHOT_ID}", str(raw_snapshot_id))
                .append()
            )
            written[t.table] = added_records(spark, t.table)
            print(f"stage 2b: {written[t.table]} rows -> {t.table} (src snapshot {raw_snapshot_id})")

        # Accounting. Every archived frame is either in a table or a control
        # frame left in raw on purpose, and the two have to sum to the input or
        # a frame went missing in the decode. The input count is a Parquet
        # row-count read (no column projected); the unrouted count is a second
        # pass over the same pinned range, a few MB at a 5-minute cadence.
        n_input = source.count()
        unrouted = (
            frames.where(~F.col("r.stream").isin(routed_streams(exchange)))
            .groupBy("r.stream")
            .count()
            .collect()
        )
        n_unrouted = sum(int(r["count"]) for r in unrouted)
        n_routed = sum(written.values())
        balanced = n_input == n_routed + n_unrouted
        print(
            f"stage 2b: {exchange}: {n_input} frames = {n_routed} decoded + {n_unrouted} control "
            f"({', '.join(f'{r[0]}={r[1]}' for r in unrouted) or 'none'})"
            + ("" if balanced else "  <-- DOES NOT BALANCE")
        )
        if not balanced:
            findings.append(
                Row(
                    run_ts=run_ts,
                    job="ingest",
                    check_name="bronze_parity",
                    scope=f"bronze.{exchange}",
                    passed=False,
                    observed=int(n_input - n_routed - n_unrouted),
                    detail=f"{n_input} raw frames in the range, {n_routed} written, {n_unrouted} control; "
                    f"src snapshot {raw_snapshot_id}",
                )
            )
    if findings:
        write_audit_rows(spark, findings, {"k2.bronze-findings": str(len(findings))})
    return written


def _source(spark, raw_snapshot_id, start, exchange: str, day=None) -> DataFrame:
    reader = spark.read.format("iceberg")
    if start:
        reader = reader.option("start-snapshot-id", start).option("end-snapshot-id", raw_snapshot_id)
    else:
        reader = reader.option("snapshot-id", raw_snapshot_id)
    df = reader.load(RAW_TABLE).where(
        F.col("topic").endswith(f".raw.{exchange}") & F.col("schema_id").isNotNull()
    )
    if day is not None:
        df = df.where(F.to_date("kafka_ts") == F.lit(day))
    return df


def stage(spark, raw_snapshot_id, run_ts: datetime) -> int:
    """The incremental step: decode what stage 1 just added, per venue. Returns rows written."""
    if raw_snapshot_id is None:
        print("stage 2b: raw.messages has no snapshots yet")
        return 0
    total = 0
    for exchange in sorted({t.exchange for t in VENUE_TABLES}):
        # A venue's tables advance together: one raw range in, N tables out,
        # all stamped with the same src snapshot.
        starts = []
        for t in (t for t in VENUE_TABLES if t.exchange == exchange):
            previous = O.latest_summary(snapshot_history(spark, t.table), O.JOB_DECODE)
            starts.append(previous.get(O.SRC_SNAPSHOT_ID) if previous else None)
        if len(set(map(str, starts))) > 1:
            # A partial failure last run: some tables committed, some did not.
            # Re-decoding from the lowest position would double the ones that
            # did (append-only), and decoding from the highest would skip the
            # others. Neither is silent-safe; a person picks.
            raise SystemExit(
                f"stage 2b: bronze.{exchange}_* tables are at different raw snapshots {starts} — "
                f"a previous run committed some and not others. Rebuild the venue: "
                f"docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer bronze --exchange {exchange}"
            )
        start = starts[0]
        written = decode(spark, _source(spark, raw_snapshot_id, start, exchange), raw_snapshot_id, run_ts, exchange)
        total += sum(written.values())
    return total


def rebuild(spark, raw_snapshot_id, run_ts: datetime, days: list, exchanges=None) -> dict:
    """The whole archive as of `raw_snapshot_id`, one day per venue at a time.

    Day slices bound the working set: a day of `raw.kraken` is ~13 M frames and
    the decode carries the payload column end to end. Every slice commits with
    the same pinned `k2.src-snapshot-id`, so when the loop ends the incremental
    `stage()` resumes exactly after the pin. The caller drops and recreates the
    tables first (rebuild.py) — this function only appends.
    """
    totals = {}
    for exchange in sorted(exchanges or {t.exchange for t in VENUE_TABLES}):
        for day in days:
            started = datetime.now()
            written = decode(spark, _source(spark, raw_snapshot_id, None, exchange, day), raw_snapshot_id, run_ts, exchange)
            for table, n in written.items():
                totals[table] = totals.get(table, 0) + n
            print(f"rebuild: {exchange} {day}: {sum(written.values())} rows in {(datetime.now() - started).total_seconds():.0f} s", flush=True)
    return totals
