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
stats = consumer.get_stats()
print(f"\nStatistics:")
print(f"  Messages consumed: {stats['messages_consumed']}")
print(f"  Batches written: {stats['batches_written']}")
print(f"  Processing errors: {stats['processing_errors']}")
print(f"  Last commit offset: {stats['last_committed_offset']}")
