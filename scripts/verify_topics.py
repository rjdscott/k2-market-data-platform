#!/usr/bin/env python3
"""Verify messages in both Kraken and Binance topics."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from confluent_kafka import Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer


def check_topic(topic_name: str, max_messages: int = 3) -> tuple[int, list]:
    """Check if messages exist in topic and return count + samples.

    Returns:
        (message_count, sample_messages)
    """
    consumer_conf = {
        "bootstrap.servers": "localhost:9092",
        "group.id": f"verify-{topic_name}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }

    consumer = Consumer(consumer_conf)
    consumer.subscribe([topic_name])

    # Schema registry for deserialization
    sr_conf = {"url": "http://localhost:8081"}
    sr_client = SchemaRegistryClient(sr_conf)
    avro_deserializer = AvroDeserializer(sr_client)

    messages = []
    count = 0

    print(f"\n{'=' * 60}")
    print(f"Checking topic: {topic_name}")
    print(f"{'=' * 60}")

    try:
        # Poll for messages
        for _ in range(100):  # Try up to 100 polls
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue

            if msg.error():
                print(f"Error: {msg.error()}")
                continue

            # Deserialize
            try:
                trade = avro_deserializer(msg.value(), None)
                count += 1

                if len(messages) < max_messages:
                    messages.append(trade)
                    print(f"\nMessage #{count}:")
                    print(f"  Symbol: {trade['symbol']}")
                    print(f"  Exchange: {trade['exchange']}")
                    print(f"  Price: {trade['price']}")
                    print(f"  Quantity: {trade['quantity']}")
                    print(f"  Side: {trade['side']}")

                if count >= max_messages:
                    break

            except Exception as e:
                print(f"Deserialization error: {e}")
                continue

    finally:
        consumer.close()

    print(f"\n{'=' * 60}")
    print(f"Total messages found in {topic_name}: {count}")
    print(f"{'=' * 60}\n")

    return count, messages


def main():
    """Check both topics."""
    print("\n" + "=" * 60)
    print("KAFKA TOPIC VERIFICATION")
    print("=" * 60)

    # Check Kraken topic
    kraken_count, kraken_msgs = check_topic("market.crypto.trades.kraken")

    # Check Binance topic
    binance_count, binance_msgs = check_topic("market.crypto.trades.binance")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Kraken messages:  {kraken_count}")
    print(f"Binance messages: {binance_count}")
    print("=" * 60)

    if kraken_count > 0 and binance_count > 0:
        print("\n✅ SUCCESS: Both topics have messages!\n")
        return 0
    else:
        print("\n❌ FAILURE: One or both topics are empty\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
