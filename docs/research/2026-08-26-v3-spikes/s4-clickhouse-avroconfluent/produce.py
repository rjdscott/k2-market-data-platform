import time, json
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField, StringSerializer

SR = "http://redpanda:8081"
BROKER = "redpanda:9092"

trade_schema = {
    "type": "record", "name": "Trade", "namespace": "k2.v3",
    "fields": [
        {"name": "exchange", "type": "string"},
        {"name": "symbol", "type": "string"},
        {"name": "exchange_ts", "type": {"type": "long", "logicalType": "timestamp-micros"}},
        {"name": "recv_ts_ns", "type": "long"},
        {"name": "price", "type": "long"},
        {"name": "qty", "type": "long"},
        {"name": "side", "type": {"type": "enum", "name": "Side", "symbols": ["buy", "sell"]}},
        {"name": "trade_id", "type": "string"},
        {"name": "seq", "type": "long"},
    ],
}
book_schema = {
    "type": "record", "name": "Book", "namespace": "k2.v3",
    "fields": [
        {"name": "exchange", "type": "string"},
        {"name": "symbol", "type": "string"},
        {"name": "recv_ts_ns", "type": "long"},
        {"name": "seq", "type": "long"},
        {"name": "checksum_ok", "type": "boolean"},
        {"name": "depth", "type": "int"},
        {"name": "bid_px", "type": {"type": "array", "items": "long"}},
        {"name": "bid_qty", "type": {"type": "array", "items": "long"}},
        {"name": "ask_px", "type": {"type": "array", "items": "long"}},
        {"name": "ask_qty", "type": {"type": "array", "items": "long"}},
    ],
}

sr = SchemaRegistryClient({"url": SR})
# explicit registration under TopicNameStrategy subjects
tid = sr.register_schema("spike.trades-value", Schema(json.dumps(trade_schema), "AVRO"))
bid_ = sr.register_schema("spike.book-value", Schema(json.dumps(book_schema), "AVRO"))
print("registered schema ids:", tid, bid_)

ts_ser = AvroSerializer(sr, json.dumps(trade_schema))
bk_ser = AvroSerializer(sr, json.dumps(book_schema))
ks = StringSerializer("utf_8")
p = Producer({"bootstrap.servers": BROKER})

base_us = 1756_000_000_000_000  # 2025-08-24T01:46:40Z-ish, micros
for i in range(5):
    ns = int(time.time() * 1e9) + i
    rec = {
        "exchange": "binance", "symbol": "BTC-USDT",
        "exchange_ts": base_us + i * 1_000_000 + 123456,
        "recv_ts_ns": ns,
        "price": 6512345678900 + i * 100000000,  # 1e-8 fixed point
        "qty": 12345678 + i,
        "side": "buy" if i % 2 == 0 else "sell",
        "trade_id": f"t{i}", "seq": 1000 + i,
    }
    p.produce("spike.trades", key=ks("BTC-USDT"),
              value=ts_ser(rec, SerializationContext("spike.trades", MessageField.VALUE)),
              headers=[("recv_ts_ns", str(ns).encode())])
    b = {
        "exchange": "kraken", "symbol": "ETH-USD", "recv_ts_ns": ns, "seq": 2000 + i,
        "checksum_ok": i != 3, "depth": 3,
        "bid_px": [300000000000 - i, 299900000000, 299800000000],
        "bid_qty": [100000000, 200000000, 300000000],
        "ask_px": [300100000000 + i, 300200000000, 300300000000],
        "ask_qty": [110000000, 210000000, 310000000],
    }
    p.produce("spike.book", key=ks("ETH-USD"),
              value=bk_ser(b, SerializationContext("spike.book", MessageField.VALUE)),
              headers=[("recv_ts_ns", str(ns).encode())])
p.flush(10)
print("produced 5+5")
