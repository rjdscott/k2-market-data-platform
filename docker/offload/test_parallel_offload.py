#!/usr/bin/env python3
"""
Test parallel offload of multiple Bronze tables.
Tests: Binance + Kraken tables offloading simultaneously.
"""

import subprocess
import time
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed


def run_offload(table_config):
    """Run offload for a single table."""
    table_name = table_config['source']
    start_time = time.time()

    print(f"\n[{datetime.now()}] Starting offload: {table_name}")

    cmd = [
        'spark-submit',
        '/home/iceberg/offload/offload_generic.py',
        '--source-table', table_config['source'],
        '--target-table', table_config['target'],
        '--timestamp-col', table_config['timestamp_col'],
        '--sequence-col', table_config['sequence_col'],
        '--layer', table_config['layer']
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        duration = time.time() - start_time

        # Parse output for key metrics
        rows_offloaded = None
        for line in result.stdout.split('\n'):
            if 'Read' in line and 'rows from ClickHouse' in line:
                try:
                    rows_offloaded = int(line.split('Read')[1].split('rows')[0].strip().replace(',', ''))
                except:
                    pass

        success = result.returncode == 0

        print(f"[{datetime.now()}] Completed: {table_name} - "
              f"{'SUCCESS' if success else 'FAILED'} - "
              f"{duration:.1f}s - "
              f"{rows_offloaded:,} rows" if rows_offloaded else "")

        return {
            'table': table_name,
            'success': success,
            'duration': duration,
            'rows': rows_offloaded,
            'stdout': result.stdout if not success else None,
            'stderr': result.stderr if not success else None
        }

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        print(f"[{datetime.now()}] TIMEOUT: {table_name} after {duration:.1f}s")
        return {
            'table': table_name,
            'success': False,
            'duration': duration,
            'error': 'Timeout after 5 minutes'
        }
    except Exception as e:
        duration = time.time() - start_time
        print(f"[{datetime.now()}] ERROR: {table_name} - {e}")
        return {
            'table': table_name,
            'success': False,
            'duration': duration,
            'error': str(e)
        }


def main():
    print("="*80)
    print("PARALLEL OFFLOAD TEST - Priority 2")
    print("="*80)
    print(f"Start Time: {datetime.now()}")
    print(f"Tables: 2 (bronze_trades_binance, bronze_trades_kraken)")
    print(f"Execution: Parallel (2 concurrent Spark jobs)")
    print("="*80)

    # Table configurations
    tables = [
        {
            'source': 'bronze_trades_binance',
            'target': 'demo.cold.bronze_trades_binance',
            'timestamp_col': 'exchange_timestamp',
            'sequence_col': 'sequence_number',
            'layer': 'bronze'
        },
        {
            'source': 'bronze_trades_kraken',
            'target': 'demo.cold.bronze_trades_kraken',
            'timestamp_col': 'exchange_timestamp',
            'sequence_col': 'sequence_number',
            'layer': 'bronze'
        }
    ]

    overall_start = time.time()

    # Run offloads in parallel
    with ProcessPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(run_offload, table): table for table in tables}

        results = []
        for future in as_completed(futures):
            table = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"Exception processing {table['source']}: {e}")
                results.append({
                    'table': table['source'],
                    'success': False,
                    'error': str(e)
                })

    overall_duration = time.time() - overall_start

    # Print summary
    print("\n" + "="*80)
    print("PARALLEL OFFLOAD TEST - RESULTS")
    print("="*80)
    print(f"End Time: {datetime.now()}")
    print(f"Total Duration: {overall_duration:.1f}s")
    print(f"\nPer-Table Results:")
    print("-"*80)

    for result in sorted(results, key=lambda x: x['table']):
        status = "✅ SUCCESS" if result['success'] else "❌ FAILED"
        table = result['table']
        duration = result.get('duration', 0)
        rows = result.get('rows')

        print(f"{status} | {table:30s} | {duration:6.1f}s | {rows:>10,} rows" if rows else
              f"{status} | {table:30s} | {duration:6.1f}s | ERROR")

        if not result['success'] and 'error' in result:
            print(f"         Error: {result['error']}")

    print("="*80)

    # Success criteria
    all_success = all(r['success'] for r in results)
    total_rows = sum(r.get('rows', 0) for r in results if r.get('rows'))
    avg_duration = sum(r.get('duration', 0) for r in results) / len(results)

    print(f"\nSummary Statistics:")
    print(f"  All tables succeeded: {all_success}")
    print(f"  Total rows offloaded: {total_rows:,}")
    print(f"  Average duration: {avg_duration:.1f}s")
    print(f"  Total duration: {overall_duration:.1f}s")
    print(f"  Parallelism efficiency: {(avg_duration / overall_duration * 100):.1f}%")

    return 0 if all_success else 1


if __name__ == '__main__':
    exit(main())
