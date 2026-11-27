#!/usr/bin/env python3
"""Synthetic frames onto a throwaway lake's Redpanda, framed as capture frames them.

    bash scripts/lib/throwaway-lake.sh seed <prefix> [fixture.jsonl]   # runs this in compose's Spark image

Each fixture line (`recv_ts_ns`, `payload`: services/capture-rust/src/replay.rs, FixtureLine) is one
RawMessage as the Kraken adapter builds it (services/capture-rust/src/exchanges/kraken.rs,
handle_frame): `stream` the envelope's channel, `control` for a method and no channel, `unknown` for
neither, `unparseable` for no JSON object; `symbol` the one symbol of a book or trade frame's data,
else null; conn_id replay's FIXTURE_CONN_ID; conn_msg_seq from 1. Spark's Kafka sink writes it as
services/capture-rust/src/sink.rs does: the value is the Confluent header (magic 0, the subject's
latest schema id, big-endian int32; docker/lake/wire.py is the reader's half) and to_avro's body
under the registry's own copy of the schema; the key is the symbol, the header recv_ts_ns.
ponytail: a key's partition is the Java client's murmur2, not librdkafka's CRC32: the bytes match
capture, which partition holds them may not.

Prints one line: frames produced, how many are stream = trade, and what Spark's Kafka source reads
back from the topic. Synthetic data only.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import urllib.request

FIXTURE_CONN_ID = "00000000-0000-4000-8000-0000000000fx"  # services/capture-rust/src/replay.rs
EXCHANGE = "kraken"


def route(payload: str) -> tuple[str, str | None]:
    """(stream, symbol) of one Kraken frame, as kraken.rs's Envelope::stream_name and Body::single_symbol."""
    try:
        e = json.loads(payload)
    except ValueError:
        return "unparseable", None
    if not isinstance(e, dict):
        return "unparseable", None
    channel, method, data = e.get("channel"), e.get("method"), e.get("data")
    stream = channel if channel is not None else "control" if method is not None else "unknown"
    if channel not in ("book", "trade") or not isinstance(data, list) or not data:
        return stream, None
    symbols = {d.get("symbol") if isinstance(d, dict) else None for d in data}
    return stream, symbols.pop() if len(symbols) == 1 and None not in symbols else None


def frames(path: str) -> list[tuple]:
    out = []
    with open(path) as f:
        for seq, line in enumerate((x for x in f if x.strip()), 1):
            d = json.loads(line)
            stream, symbol = route(d["payload"])
            out.append((EXCHANGE, stream, symbol, d["recv_ts_ns"], FIXTURE_CONN_ID, seq, d["payload"].encode()))
    return out


def main(path: str, topic: str) -> None:
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql.avro.functions import to_avro
    from pyspark.sql.types import BinaryType, LongType, StringType, StructField, StructType

    brokers = os.environ.get("K2_BROKERS", "redpanda:9092")
    registry = os.environ.get("K2_SCHEMA_REGISTRY_URL", "http://redpanda:8081")
    with urllib.request.urlopen(f"{registry}/subjects/{topic}-value/versions/latest", timeout=10) as r:
        latest = json.load(r)
    header = bytearray(struct.pack(">bi", 0, latest["id"]))

    rows = frames(path)
    spark = (SparkSession.builder.appName("throwaway-kafka-seed").master("local[1]")
             .config("spark.driver.memory", "512m").config("spark.ui.enabled", "false").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    schema = StructType([
        StructField("exchange", StringType(), False), StructField("stream", StringType(), False),
        StructField("symbol", StringType(), True), StructField("recv_ts_ns", LongType(), False),
        StructField("conn_id", StringType(), False), StructField("conn_msg_seq", LongType(), False),
        StructField("payload", BinaryType(), False)])
    df = spark.createDataFrame(rows, schema)
    record = F.struct(*[F.col(f.name) for f in schema.fields])
    (df.select(F.col("symbol").cast("binary").alias("key"),
               F.concat(F.lit(header), to_avro(record, latest["schema"])).alias("value"),
               F.array(F.struct(F.lit("recv_ts_ns").alias("key"),
                                F.col("recv_ts_ns").cast("string").cast("binary").alias("value"))).alias("headers"))
       .write.format("kafka").option("kafka.bootstrap.servers", brokers).option("kafka.acks", "all")
       .option("topic", topic).save())

    back = (spark.read.format("kafka").option("kafka.bootstrap.servers", brokers).option("subscribe", topic)
            .option("startingOffsets", "earliest").option("endingOffsets", "latest").load())
    framed = back.where(F.substring("value", 1, 5) == F.lit(header)).count()
    trades = sum(1 for r in rows if r[1] == "trade")
    print(f"kafka-seed: {len(rows)} frames produced to {topic}, {trades} of them stream = trade; Spark's Kafka "
          f"source reads back {back.count()}, {framed} framed with schema id {latest['id']}")
    spark.stop()


if __name__ == "__main__":
    main(*sys.argv[1:3])
