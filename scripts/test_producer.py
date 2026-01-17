#!/usr/bin/env python3
"""Direct test of Kafka producer."""

import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from k2.ingestion.producer import MarketDataProducer
from k2.ingestion.message_builders import build_trade_v2

print("Creating producer...")
producer = MarketDataProducer(schema_version="v2")

print("Building test trade...")
trade = build_trade_v2(
    symbol="TESTBTC",
    exchange="KRAKEN",
    asset_class="crypto",
    timestamp=datetime.now(timezone.utc),
    price=Decimal("95000.00"),
    quantity=Decimal("0.001"),
    currency="USD",
    side="BUY",
    vendor_data={"pair": "XBT/USD", "test": "true"}
)

print(f"Trade built: {trade['symbol']} @ {trade['price']}")

print("Producing to Kafka...")
try:
    producer.produce_trade(
        asset_class="crypto",
        exchange="kraken",
        record=trade,
    )
    print("✓ produce_trade() called successfully")
except Exception as e:
    print(f"✗ produce_trade() failed: {e}")
    sys.exit(1)

print("Flushing producer...")
remaining = producer.flush(timeout=5.0)
print(f"Flush complete. Remaining messages: {remaining}")

stats = producer.get_stats()
print(f"\nProducer stats:")
print(f"  Produced: {stats['produced']}")
print(f"  Errors: {stats['errors']}")
print(f"  Retries: {stats['retries']}")

producer.close()
print("\n✓ Test complete")
