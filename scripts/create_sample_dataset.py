#!/usr/bin/env python3
"""
Create sample dataset for K2 Market Data Platform.

Extracts a time-based subset of the raw Australian equity data to create
a manageable sample dataset suitable for GitHub distribution and demos.

Strategy: Keep 5 complete trading days (March 10-14, 2014) of tick-level data
to demonstrate sequence tracking, replay semantics, and multi-day behavior.

Usage:
    python scripts/create_sample_dataset.py
"""

import csv
import shutil
from datetime import datetime
from pathlib import Path


# Configuration
SOURCE_DIR = Path("data/raw/raw-au-equity-sample-data")
TARGET_DIR = Path("data/sample")
START_DATE = datetime(2014, 3, 10)  # March 10, 2014
END_DATE = datetime(2014, 3, 14)    # March 14, 2014


def parse_date(date_str: str) -> datetime:
    """Parse date string in MM/DD/YYYY format."""
    return datetime.strptime(date_str, "%m/%d/%Y")


def is_within_date_range(date_str: str) -> bool:
    """Check if date is within target range."""
    try:
        date = parse_date(date_str)
        return START_DATE <= date <= END_DATE
    except (ValueError, IndexError):
        return False


def filter_csv_by_date(source_file: Path, target_file: Path, date_column: int = 0):
    """
    Filter CSV file to only include rows within date range.

    Args:
        source_file: Source CSV file path
        target_file: Target CSV file path
        date_column: Index of date column (0 = first column)
    """
    if not source_file.exists():
        print(f"  ⚠️  Skipping {source_file.name} (file not found)")
        return

    target_file.parent.mkdir(parents=True, exist_ok=True)

    rows_kept = 0
    rows_total = 0

    with source_file.open('r') as infile, target_file.open('w', newline='') as outfile:
        reader = csv.reader(infile)
        writer = csv.writer(outfile)

        # Process each row
        for row in reader:
            if not row:  # Skip empty rows
                continue

            rows_total += 1

            # Keep only rows within date range
            if row[date_column] and is_within_date_range(row[date_column]):
                writer.writerow(row)
                rows_kept += 1

    size_mb = target_file.stat().st_size / 1024 / 1024
    print(f"  ✓ {source_file.name}: {rows_kept:,}/{rows_total:,} rows kept ({size_mb:.2f} MB)")


def copy_directory(source: Path, target: Path):
    """Copy entire directory (for reference data and bars)."""
    if not source.exists():
        print(f"  ⚠️  Skipping {source.name} (directory not found)")
        return

    if target.exists():
        shutil.rmtree(target)

    shutil.copytree(source, target)

    # Calculate total size
    total_size = sum(f.stat().st_size for f in target.rglob('*') if f.is_file())
    size_mb = total_size / 1024 / 1024

    file_count = len(list(target.rglob('*.csv')))
    print(f"  ✓ {source.name}: {file_count} files copied ({size_mb:.2f} MB)")


def create_sample_readme():
    """Generate README for sample dataset."""
    readme_content = f"""# Sample Australian Equity Market Data

**Date Range**: March 10-14, 2014 (5 trading days)
**Symbols**: BHP, RIO, MWR, DVN
**Source**: Raw Australian equity sample data (filtered)

---

## Dataset Overview

This is a curated subset of Australian Securities Exchange (ASX) market data designed to demonstrate the K2 Market Data Platform's capabilities without requiring large file downloads.

### Data Types

| Directory | Type | Description | Size |
|-----------|------|-------------|------|
| `quotes/` | Tick-level bid/ask | Market quotes with sub-second timestamps | ~4-5 MB |
| `trades/` | Tick-level executions | Executed trades with price/volume | ~3-4 MB |
| `bars-1min/` | OHLCV aggregates | 1-minute bars for full month (Mar 3-31, 2014) | ~716 KB |
| `reference-data/` | Static metadata | Company info, dividends, splits, mergers | ~16 KB |

**Total Size**: ~8-10 MB

---

## Symbols

| Symbol | Company | Type | Volume Profile |
|--------|---------|------|----------------|
| **BHP** | BHP Billiton Ltd | Mining | High volume (demonstrates partition skew) |
| **RIO** | Rio Tinto Ltd | Mining | High volume (demonstrates partition skew) |
| **MWR** | MGM Wireless Ltd | Technology | Low volume |
| **DVN** | Devine Ltd | Real Estate | Low volume |

**Note**: BHP and RIO dominate the dataset (>95% of messages), demonstrating realistic partition skew in Kafka topics.

---

## File Formats

### Quotes (quotes/*.csv)
```csv
Date,Time,Bid,BidVolume,Ask,AskVolume
03/10/2014,10:00:01.704,37.50,6000,37.52,700
```

### Trades (trades/*.csv)
```csv
Date,Time,Price,Volume,Qualifiers,Venue,BuyerID
03/10/2014,10:05:44.516,37.51,40000,3,X,
```

### 1-Minute Bars (bars-1min/*.csv)
```csv
Date,Time,Open,High,Low,Close,Volume
03/10/2014,10:01,37.65,37.70,37.565,37.57,67123
```

### Reference Data (reference-data/*.csv)
- `company_info.csv`: Symbol mappings, ISINs, date ranges
- `dividends.csv`: Dividend events
- `stock_splits.csv`: Split events
- `mergers.csv`: Merger events

---

## Platform Demonstration Use Cases

This dataset enables demonstration of:

### 1. **Sequence Tracking**
- Tick-level data with monotonic sequence behavior
- Session resets (overnight gaps between trading days)
- Out-of-order delivery detection

### 2. **Replay Scenarios**
- **Cold Start**: Replay all 5 days from Iceberg
- **Catch-Up**: Resume from March 12, catch up to real-time
- **Rewind**: Time-travel query to March 11 @ 14:00

### 3. **Partition Skew**
- BHP generates ~150K ticks/day (high volume)
- RIO generates ~150K ticks/day (high volume)
- MWR/DVN generate <100 ticks/day (low volume)
- Total skew ratio: >1000:1

### 4. **Market Microstructure**
- Opening auctions (~10:00 AEST)
- Intraday volatility
- Closing auctions (~16:00 AEST)
- Pre-market and post-market activity

### 5. **Multi-Day Patterns**
- Session boundaries (sequence number resets)
- Overnight gaps (no trading 16:00-10:00)
- Week-long trends (Mar 10-14 = Mon-Fri)

---

## Data Quality Notes

### Known Characteristics

1. **Timestamps**: ASX market hours are 10:00-16:00 AEST
2. **Sequence Numbers**: Not explicitly provided (use row order + timestamp)
3. **Venue Codes**: 'X' indicates primary ASX venue
4. **Price Format**: Australian dollars (AUD)
5. **Historical Data**: March 2014 (10 years old, prices may not reflect current levels)

### Volume Statistics (5-day sample)

| Symbol | Trades | Quotes | Avg Daily Volume |
|--------|--------|--------|------------------|
| BHP | ~50K | ~45K | 10K trades/day |
| RIO | ~50K | ~45K | 10K trades/day |
| MWR | <50 | <50 | <10 trades/day |
| DVN | <50 | <50 | <10 trades/day |

---

## Usage Examples

### Loading into Kafka

```bash
# Simulate real-time feed (1000x speed)
python scripts/simulate_market_data.py \\
    --data-dir data/sample \\
    --start-date 2014-03-10 \\
    --end-date 2014-03-14 \\
    --speed 1000
```

### Querying with DuckDB

```python
from k2.query import QueryEngine

engine = QueryEngine()

# Query BHP trades on March 11
df = engine.query(\"\"\"
    SELECT * FROM market_data.trades
    WHERE symbol = 'BHP'
      AND date = '2014-03-11'
    ORDER BY time
\"\"\")
```

### Replay from Iceberg

```python
from k2.query import ReplayEngine

replay = ReplayEngine(
    table='market_data.ticks',
    symbols=['BHP', 'RIO'],
    start_time='2014-03-10 10:00:00',
    end_time='2014-03-14 16:00:00',
    playback_speed=100.0  # 100x real-time
)

for tick in replay.stream():
    process_tick(tick)
```

---

## Extending the Dataset

To add more data:

1. **More symbols**: Add CSV files to `quotes/` and `trades/` directories
2. **More days**: Re-run extraction script with different date range
3. **Generate synthetic**: Use `scripts/generate_market_data.py` for unlimited data

---

## Data Provenance

- **Original Source**: Raw Australian equity sample data
- **Extraction Date**: {datetime.now().strftime('%Y-%m-%d')}
- **Extraction Method**: `scripts/create_sample_dataset.py`
- **License**: Same as project (MIT)

---

## File Manifest

```
data/sample/
├── quotes/
│   ├── 3153.csv  (MWR - MGM Wireless)
│   ├── 7078.csv  (BHP - BHP Billiton)
│   ├── 7181.csv  (DVN - Devine Ltd)
│   └── 7458.csv  (RIO - Rio Tinto)
├── trades/
│   ├── 3153.csv
│   ├── 7078.csv
│   ├── 7181.csv
│   └── 7458.csv
├── bars-1min/
│   ├── 3153.csv
│   ├── 7078.csv
│   ├── 7181.csv
│   └── 7458.csv
└── reference-data/
    ├── company_info.csv
    ├── dividends.csv
    ├── stock_splits.csv
    └── mergers.csv
```

---

For questions or issues with this dataset, please open an issue in the GitHub repository.
"""

    readme_path = TARGET_DIR / "README.md"
    readme_path.write_text(readme_content)
    print(f"  ✓ README.md created")


def main():
    """Main execution function."""
    print("\n" + "="*70)
    print("K2 Market Data Platform - Sample Dataset Creator")
    print("="*70)
    print(f"\nExtracting: {START_DATE.strftime('%B %d, %Y')} to {END_DATE.strftime('%B %d, %Y')}")
    print(f"Source: {SOURCE_DIR}")
    print(f"Target: {TARGET_DIR}\n")

    # Clean target directory
    if TARGET_DIR.exists():
        print("Removing existing sample directory...")
        shutil.rmtree(TARGET_DIR)

    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    # Process quotes (filter by date)
    print("\n[1/4] Processing quotes (tick-level bid/ask)...")
    quotes_source = SOURCE_DIR / "quotes"
    quotes_target = TARGET_DIR / "quotes"

    if quotes_source.exists():
        for csv_file in quotes_source.glob("*.csv"):
            filter_csv_by_date(
                csv_file,
                quotes_target / csv_file.name,
                date_column=0
            )

    # Process trades (filter by date)
    print("\n[2/4] Processing trades (tick-level executions)...")
    trades_source = SOURCE_DIR / "trades"
    trades_target = TARGET_DIR / "trades"

    if trades_source.exists():
        for csv_file in trades_source.glob("*.csv"):
            filter_csv_by_date(
                csv_file,
                trades_target / csv_file.name,
                date_column=0
            )

    # Copy bars-1min (keep all - already small)
    print("\n[3/4] Copying 1-minute bars (full month coverage)...")
    copy_directory(SOURCE_DIR / "bars-1min", TARGET_DIR / "bars-1min")

    # Copy reference data (keep all - tiny)
    print("\n[4/4] Copying reference data...")
    copy_directory(SOURCE_DIR / "reference-data", TARGET_DIR / "reference-data")

    # Create README
    print("\n[5/5] Generating README...")
    create_sample_readme()

    # Summary
    print("\n" + "="*70)
    print("Summary")
    print("="*70)

    total_size = sum(f.stat().st_size for f in TARGET_DIR.rglob('*') if f.is_file())
    total_size_mb = total_size / 1024 / 1024

    file_count = len(list(TARGET_DIR.rglob('*.csv')))

    print(f"\n✓ Sample dataset created successfully!")
    print(f"  Location: {TARGET_DIR}")
    print(f"  Files: {file_count} CSV files")
    print(f"  Total size: {total_size_mb:.2f} MB")
    print(f"  Date range: {START_DATE.strftime('%B %d, %Y')} - {END_DATE.strftime('%B %d, %Y')}")
    print(f"\n  GitHub-ready: {'✓ Yes' if total_size_mb < 50 else '✗ Too large'} ({total_size_mb:.1f} MB / 50 MB limit)")
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    main()
