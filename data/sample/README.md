# Sample Australian Equity Market Data

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
python scripts/simulate_market_data.py \
    --data-dir data/sample \
    --start-date 2014-03-10 \
    --end-date 2014-03-14 \
    --speed 1000
```

### Querying with DuckDB

```python
from k2_platform.query import QueryEngine

engine = QueryEngine()

# Query BHP trades on March 11
df = engine.query("""
    SELECT * FROM market_data.trades
    WHERE symbol = 'BHP'
      AND date = '2014-03-11'
    ORDER BY time
""")
```

### Replay from Iceberg

```python
from k2_platform.query import ReplayEngine

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
- **Extraction Date**: 2026-01-09
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
