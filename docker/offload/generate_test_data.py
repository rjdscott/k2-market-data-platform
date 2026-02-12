#!/usr/bin/env python3
"""
Generate production-scale test data for ClickHouse offload validation.

Purpose: Create realistic trade data in bronze_trades_binance table for testing
         the Spark offload pipeline at scale (10K+ rows).

Usage:
    python generate_test_data.py --rows 10000
    python generate_test_data.py --rows 5000 --incremental
"""

import argparse
import random
from datetime import datetime, timedelta
from decimal import Decimal

import clickhouse_connect


def generate_trade_row(sequence: int, base_timestamp: datetime) -> dict:
    """Generate a single realistic trade row."""
    symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'ADAUSDT']
    symbol = random.choice(symbols)

    # Realistic price ranges
    price_ranges = {
        'BTCUSDT': (40000, 70000),
        'ETHUSDT': (2000, 4000),
        'BNBUSDT': (200, 600),
        'SOLUSDT': (20, 150),
        'ADAUSDT': (0.30, 1.50),
    }

    min_price, max_price = price_ranges[symbol]
    price = Decimal(str(random.uniform(min_price, max_price))).quantize(Decimal('0.00000001'))

    quantity = Decimal(str(random.uniform(0.01, 10.0))).quantize(Decimal('0.00000001'))
    quote_volume = price * quantity

    side = random.choice(['buy', 'sell'])

    # Timestamp increments by 10ms per row for realistic ordering
    timestamp = base_timestamp + timedelta(milliseconds=sequence * 10)

    return {
        'exchange': 'binance',
        'symbol': symbol,
        'canonical_symbol': symbol,
        'sequence_number': sequence,
        'trade_id': f'test_{sequence}',
        'price': float(price),
        'quantity': float(quantity),
        'quote_volume': float(quote_volume),
        'side': side,
        'is_maker': random.choice([0, 1]),
        'exchange_timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
        'server_received_timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
    }


def insert_test_data(
    host: str,
    port: int,
    database: str,
    table: str,
    num_rows: int,
    start_sequence: int = 1,
    batch_size: int = 1000,
):
    """Insert test data into ClickHouse table."""
    print(f"Connecting to ClickHouse at {host}:{port}...")
    client = clickhouse_connect.get_client(
        host=host,
        port=port,
        database=database,
        username='default',
        password='clickhouse',
    )

    # Get current max sequence to avoid duplicates
    result = client.query(f"SELECT max(sequence_number) as max_seq FROM {table}")
    current_max = result.first_row[0] if result.first_row[0] is not None else 0
    start_sequence = max(start_sequence, current_max + 1)

    print(f"Current max sequence: {current_max}")
    print(f"Starting from sequence: {start_sequence}")
    print(f"Generating {num_rows} rows in batches of {batch_size}...")

    base_timestamp = datetime.now()
    total_inserted = 0

    for batch_start in range(0, num_rows, batch_size):
        batch_end = min(batch_start + batch_size, num_rows)
        batch = []

        for i in range(batch_start, batch_end):
            sequence = start_sequence + i
            row = generate_trade_row(sequence, base_timestamp)
            batch.append(row)

        # Insert batch
        client.insert(table, batch)
        total_inserted += len(batch)

        if (batch_end % 10000) == 0 or batch_end == num_rows:
            print(f"  Inserted {total_inserted:,} / {num_rows:,} rows...")

    # Verify insertion
    result = client.query(f"SELECT count() as cnt, max(sequence_number) as max_seq FROM {table}")
    total_count, max_sequence = result.first_row

    print(f"\n✅ Success!")
    print(f"  Total rows in table: {total_count:,}")
    print(f"  Max sequence number: {max_sequence:,}")
    print(f"  Rows inserted this run: {total_inserted:,}")

    client.close()


def main():
    parser = argparse.ArgumentParser(
        description='Generate production-scale test data for offload validation'
    )
    parser.add_argument(
        '--rows',
        type=int,
        default=10000,
        help='Number of rows to generate (default: 10000)',
    )
    parser.add_argument(
        '--host',
        type=str,
        default='localhost',
        help='ClickHouse host (default: localhost)',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8123,
        help='ClickHouse HTTP port (default: 8123)',
    )
    parser.add_argument(
        '--database',
        type=str,
        default='k2',
        help='Database name (default: k2)',
    )
    parser.add_argument(
        '--table',
        type=str,
        default='bronze_trades_binance',
        help='Table name (default: bronze_trades_binance)',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=1000,
        help='Batch insert size (default: 1000)',
    )

    args = parser.parse_args()

    insert_test_data(
        host=args.host,
        port=args.port,
        database=args.database,
        table=args.table,
        num_rows=args.rows,
        batch_size=args.batch_size,
    )


if __name__ == '__main__':
    main()
