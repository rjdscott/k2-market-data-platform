#!/usr/bin/env python3
"""Simple consumer to get Kafka trades into Iceberg quickly."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from k2.ingestion.consumer import MarketDataConsumer
from k2.storage.writer import IcebergWriter

print("Initializing Iceberg writer...")
writer = IcebergWriter(schema_version="v2")
print("✓ Writer initialized\n")

print("Initializing Kafka consumer...")
consumer = MarketDataConsumer(
    topics=["market.crypto.trades.binance"],
    batch_size=500,
    iceberg_writer=writer,
    consumer_group="k2-iceberg-writer-crypto-v2",
    max_messages=5000,  # Stop after 5000 messages
    dlq_path=Path("./data/dlq"),  # Use local path for DLQ
)
print("✓ Consumer initialized\n")

print("Starting consumption (5000 messages)...")
try:
    consumer.run()
    print("\n✓ Consumption complete!")
except KeyboardInterrupt:
    print("\n⚠ Interrupted by user")
except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Get stats
stats = consumer.stats
print(f"\nStatistics:")
print(f"  Messages consumed: {stats.messages_consumed}")
print(f"  Messages written: {stats.messages_written}")
print(f"  Errors: {stats.errors}")
print(f"  Sequence gaps: {stats.sequence_gaps}")
print(f"  Duration: {stats.duration_seconds:.2f} seconds")
print(f"  Throughput: {stats.throughput:.2f} msg/sec")
