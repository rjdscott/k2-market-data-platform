#!/usr/bin/env python3
"""Quick script to query the trades_v2 Iceberg table and validate data."""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from k2.query.engine import QueryEngine

print("Initializing Query Engine (v2)...")
engine = QueryEngine(table_version="v2")
print("✓ Query Engine initialized\n")

# Query 1: Test basic query
print("=" * 60)
print("Query 1: Basic Query Test (limit 1)")
print("=" * 60)
trades = engine.query_trades(limit=1)
if trades:
    print("✓ Found data in trades_v2 table!")
    print(f"  Fields: {list(trades[0].keys())}\n")
else:
    print("✗ No data found in trades_v2 table\n")
    sys.exit(1)

# Query 2: Sample trades
print("=" * 60)
print("Query 2: Sample Trades (first 5)")
print("=" * 60)
sample_trades = engine.query_trades(limit=5)
for i, trade in enumerate(sample_trades[:3], 1):
    print(f"\nTrade {i}:")
    print(f"  Symbol: {trade.get('symbol')}")
    print(f"  Exchange: {trade.get('exchange')}")
    print(f"  Asset Class: {trade.get('asset_class')}")
    print(f"  Price: {trade.get('price')}")
    print(f"  Quantity: {trade.get('quantity')}")
    print(f"  Currency: {trade.get('currency')}")
    print(f"  Side: {trade.get('side')}")
    print(f"  Timestamp: {trade.get('timestamp')}")
print()

# Query 3: Trades by symbol
print("=" * 60)
print("Query 3: Trades by Symbol (up to 5000)")
print("=" * 60)
all_trades = engine.query_trades(limit=5000)
if all_trades:
    symbols = [t.get("symbol") for t in all_trades]
    symbol_counts = Counter(symbols)
    print(f"Total trades: {len(all_trades)}")
    print("\nTop symbols:")
    for symbol, count in symbol_counts.most_common(10):
        print(f"  {symbol}: {count}")
    print()
else:
    print("No trades found\n")

# Query 4: Check vendor_data
print("=" * 60)
print("Query 4: Sample vendor_data Field")
print("=" * 60)
for i, trade in enumerate(sample_trades[:3], 1):
    vendor_data = trade.get("vendor_data")
    print(f"\nTrade {i} vendor_data:")
    if vendor_data:
        try:
            parsed = json.loads(vendor_data) if isinstance(vendor_data, str) else vendor_data
            print(f"  {json.dumps(parsed, indent=2)}")
        except:
            print(f"  {vendor_data}")
    else:
        print("  None")
print()

# Query 5: Check asset_class and exchange
print("=" * 60)
print("Query 5: Asset Classes and Exchanges")
print("=" * 60)
if all_trades:
    asset_classes = [t.get("asset_class") for t in all_trades]
    exchanges = [t.get("exchange") for t in all_trades]

    print("Asset Classes:")
    for asset_class, count in Counter(asset_classes).most_common():
        print(f"  {asset_class}: {count}")

    print("\nExchanges:")
    for exchange, count in Counter(exchanges).most_common():
        print(f"  {exchange}: {count}")
    print()

print("=" * 60)
print("✓ All queries completed successfully!")
print(f"✓ Validated {len(all_trades)} trades in trades_v2 table")
print("=" * 60)
