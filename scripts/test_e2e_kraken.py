#!/usr/bin/env python3
"""Quick E2E test for Kraken streaming."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from confluent_kafka import Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer

def main():
    # Configure consumer
    consumer_conf = {
        "bootstrap.servers": "localhost:9092",
        "group.id": "test-e2e-kraken",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }

    consumer = Consumer(consumer_conf)
    consumer.subscribe(["market.crypto.trades.kraken"])

    # Configure schema registry
    sr_conf = {"url": "http://localhost:8081"}
    sr_client = SchemaRegistryClient(sr_conf)
    avro_deserializer = AvroDeserializer(sr_client)

    print("Consuming messages from market.crypto.trades.kraken...")
    print("=" * 60)

    count = 0
    try:
        while count < 5:
            msg = consumer.poll(timeout=10.0)

            if msg is None:
                print("No more messages (timeout)")
                break

            if msg.error():
                print(f"Consumer error: {msg.error()}")
                continue

            # Deserialize
            trade = avro_deserializer(msg.value(), None)

            count += 1
            print(f"\nTrade #{count}:")
            print(f"  Symbol: {trade['symbol']}")
            print(f"  Exchange: {trade['exchange']}")
            print(f"  Price: {trade['price']}")
            print(f"  Quantity: {trade['quantity']}")
            print(f"  Side: {trade['side']}")
            print(f"  Timestamp: {trade['timestamp']}")
            print(f"  Pair (vendor_data): {trade['vendor_data']['pair']}")

    finally:
        consumer.close()

    print("\n" + "=" * 60)
    print(f"Successfully consumed {count} messages")
    return 0 if count > 0 else 1

if __name__ == "__main__":
    sys.exit(main())
