#!/usr/bin/env python3
"""Test script to verify Kafka producer works independently."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from datetime import UTC, datetime
from decimal import Decimal

from k2.ingestion.message_builders import build_trade_v2
from k2.ingestion.producer import MarketDataProducer

# Initialize producer
print("Initializing producer...")
producer = MarketDataProducer(schema_version="v2")

# Build test trade
print("Building test trade...")
trade = build_trade_v2(
    symbol="TESTBTC",
    exchange="binance",
    asset_class="crypto",
    timestamp=datetime.now(UTC),
    price=Decimal("50000.00"),
    quantity=Decimal("1.0"),
    currency="USDT",
    side="BUY",
    trade_id="test-001",
)

# Produce to Kafka
print("Producing to Kafka...")
try:
    producer.produce_trade(
        asset_class="crypto",
        exchange="binance",
        record=trade,
    )
    print("✓ produce_trade() succeeded")
except Exception as e:
    print(f"✗ produce_trade() failed: {e}")
    sys.exit(1)

# Flush to ensure message is sent
print("Flushing producer...")
remaining = producer.flush(timeout=10.0)
print(f"✓ Flush complete, {remaining} messages remaining in queue")

# Get stats
stats = producer.get_stats()
print("\nProducer Statistics:")
print(f"  Produced: {stats['produced']}")
print(f"  Errors: {stats['errors']}")
print(f"  Retries: {stats['retries']}")

# Close producer
producer.close()
print("\n✓ Test complete!")
