# Test Data Strategy and Management

## Overview

This document outlines the comprehensive test data strategy for the K2 Market Data Platform testing framework. Proper test data management is critical for validating data accuracy, performance characteristics, and system reliability.

## Data Strategy Philosophy

### Core Principles
1. **Realism**: Test data mirrors production characteristics
2. **Variety**: Multiple data types and market conditions
3. **Scalability**: Data volumes appropriate for each test tier
4. **Privacy**: No sensitive production data in test environments
5. **Efficiency**: Fast data generation and loading

### Data Quality Requirements
- **Accuracy**: 99.99%+ data fidelity validation
- **Completeness**: All required fields populated
- **Consistency**: Referential integrity maintained
- **Timeliness**: Realistic timestamp sequences
- **Diversity**: Various market scenarios represented

## Test Data Categories

### Data Type Matrix

| Category | Purpose | Volume | Generation | Validation |
|----------|---------|--------|------------|------------|
| Synthetic | Unit tests, edge cases | < 1MB | Faker + custom | Schema validation |
| Sample | Integration tests | 10-100MB | Curated samples | Business rules |
| Benchmark | Performance tests | 1-10GB | Scripted generation | Statistical validation |
| Production | Soak tests, validation | Variable | Anonymized extracts | Full validation |

### Market Data Types

#### Trade Data
```python
# Trade record structure
Trade = {
    "symbol": str,           # "AAPL", "GOOGL", etc.
    "exchange": str,         # "NASDAQ", "NYSE", etc.
    "timestamp": datetime,   # Trade execution time
    "price": decimal,        # Execution price
    "quantity": int,         # Number of shares
    "trade_id": str,         # Unique trade identifier
    "conditions": list,      # Trade conditions (e.g., ["regular"])
}
```

#### Quote Data
```python
# Quote record structure
Quote = {
    "symbol": str,           # Trading symbol
    "exchange": str,         # Exchange identifier
    "timestamp": datetime,   # Quote timestamp
    "bid_price": decimal,    # Highest bid price
    "bid_size": int,         # Bid quantity
    "ask_price": decimal,    # Lowest ask price
    "ask_size": int,         # Ask quantity
    "quote_condition": str,  # Quote condition (e.g., "real-time")
}
```

#### Reference Data
```python
# Reference data structure
Reference = {
    "symbol": str,           # Trading symbol
    "name": str,             # Full company name
    "sector": str,           # Industry sector
    "market_cap": decimal,   # Market capitalization
    "shares_outstanding": int, # Total shares
    "listing_date": date,    # Initial listing date
    "corporate_actions": list # Corporate action history
}
```

## Data Generation Strategies

### Synthetic Data Generation

#### Faker-Based Generation
```python
class MarketDataFactory:
    """Factory for generating synthetic market data."""
    
    @staticmethod
    def create_trade(symbol: str, timestamp: datetime) -> Trade:
        """Generate realistic trade data."""
        return Trade(
            symbol=symbol,
            exchange=random.choice(["NASDAQ", "NYSE", "ARCA"]),
            timestamp=timestamp,
            price=round(random.uniform(10, 1000), 2),
            quantity=random.randint(100, 10000),
            trade_id=f"TRD-{uuid.uuid4().hex[:12]}",
            conditions=["regular"]
        )
    
    @staticmethod
    def create_quote(symbol: str, timestamp: datetime) -> Quote:
        """Generate realistic quote data with bid-ask spread."""
        base_price = random.uniform(10, 1000)
        spread = round(base_price * random.uniform(0.0001, 0.01), 2)
        
        return Quote(
            symbol=symbol,
            exchange=random.choice(["NASDAQ", "NYSE"]),
            timestamp=timestamp,
            bid_price=round(base_price - spread/2, 2),
            bid_size=random.randint(100, 5000),
            ask_price=round(base_price + spread/2, 2),
            ask_size=random.randint(100, 5000),
            quote_condition="real-time"
        )
```

#### Property-Based Testing
```python
@given(st.lists(st.builds(
    Trade,
    symbol=st.sampled_from(["AAPL", "GOOGL", "MSFT"]),
    timestamp=st.datetimes(min_value=datetime(2024, 1, 1)),
    price=st.decimals(min_value=1, max_value=10000, places=2),
    quantity=st.integers(min_value=1, max_value=1000000)
), min_size=1, max_size=1000))
def test_trade_data_validation(trades):
    """Property-based test for trade data validation."""
    for trade in trades:
        assert validate_trade(trade) is True
        assert trade.price > 0
        assert trade.quantity > 0
```

### Sample Data Curation

#### Realistic Market Scenarios
```python
class SampleDataScenarios:
    """Curated sample data for common market scenarios."""
    
    @staticmethod
    def market_opening() -> List[Trade]:
        """High-volume trading at market open."""
        return load_sample_data("market_opening.csv")
    
    @staticmethod
    def volatile_trading() -> List[Trade]:
        """High volatility trading period."""
        return load_sample_data("volatile_session.csv")
    
    @staticmethod
    def low_volume_session() -> List[Trade]:
        """Low volume trading session."""
        return load_sample_data("low_volume.csv")
    
    @staticmethod
    def corporate_action() -> Dict:
        """Corporate action event data."""
        return load_sample_data("corporate_action.json")
```

#### Sample Data Files
```
tests/fixtures/sample_data/
├── market_opening.csv          # High volume open
├── volatile_session.csv        # Volatile trading
├── low_volume.csv              # Low volume session
├── after_hours.csv             # After hours trading
├── corporate_actions.json      # Corporate events
├── reference_data.csv          # Security reference
└── market_holidays.csv         # Trading calendar
```

### Benchmark Data Generation

#### Large-Scale Data Generation
```python
class BenchmarkDataGenerator:
    """Generate large datasets for performance testing."""
    
    def __init__(self, target_size_gb: float):
        self.target_size_gb = target_size_gb
        self.symbols = self._generate_symbol_list(1000)
    
    def generate_trades(self, duration_hours: int = 24) -> Iterator[Trade]:
        """Generate trades for specified duration."""
        start_time = datetime.now() - timedelta(hours=duration_hours)
        current_time = start_time
        
        while current_time < datetime.now():
            # Generate trades with realistic frequency
            for symbol in self.symbols:
                if random.random() < 0.1:  # 10% chance per symbol
                    yield self._create_realistic_trade(symbol, current_time)
            
            # Advance time (1-second intervals)
            current_time += timedelta(seconds=1)
            
            # Check size limit
            if self._current_size_gb() >= self.target_size_gb:
                break
    
    def _create_realistic_trade(self, symbol: str, timestamp: datetime) -> Trade:
        """Create trade with realistic market characteristics."""
        # Simulate price movement
        if hasattr(self, '_last_price'):
            price_change = random.gauss(0, 0.001)  # 0.1% std deviation
            new_price = self._last_price * (1 + price_change)
        else:
            new_price = random.uniform(10, 1000)
        
        self._last_price = new_price
        
        return Trade(
            symbol=symbol,
            exchange=self._get_symbol_exchange(symbol),
            timestamp=timestamp,
            price=round(new_price, 2),
            quantity=random.randint(100, 10000),
            trade_id=f"TRD-{uuid.uuid4().hex[:12]}",
            conditions=["regular"]
        )
```

## Data Validation Framework

### Schema Validation
```python
class DataValidator:
    """Comprehensive data validation framework."""
    
    def __init__(self):
        self.trade_schema = self._load_trade_schema()
        self.quote_schema = self._load_quote_schema()
        self.reference_schema = self._load_reference_schema()
    
    def validate_trade(self, trade: Dict) -> ValidationResult:
        """Validate trade record against schema and business rules."""
        # Schema validation
        schema_result = self.trade_schema.validate(trade)
        if not schema_result.is_valid:
            return ValidationResult(False, schema_result.errors)
        
        # Business rule validation
        errors = []
        
        # Price must be positive
        if trade["price"] <= 0:
            errors.append("Price must be positive")
        
        # Quantity must be positive integer
        if trade["quantity"] <= 0 or not isinstance(trade["quantity"], int):
            errors.append("Quantity must be positive integer")
        
        # Timestamp must be within market hours
        if not self._is_market_hours(trade["timestamp"]):
            errors.append("Timestamp outside market hours")
        
        return ValidationResult(len(errors) == 0, errors)
    
    def validate_data_sequence(self, trades: List[Trade]) -> ValidationResult:
        """Validate trade sequence for consistency."""
        errors = []
        
        # Check for duplicate trade IDs
        trade_ids = [t.trade_id for t in trades]
        if len(trade_ids) != len(set(trade_ids)):
            errors.append("Duplicate trade IDs found")
        
        # Check timestamp sequence
        timestamps = [t.timestamp for t in trades]
        if timestamps != sorted(timestamps):
            errors.append("Trades not in chronological order")
        
        # Check for realistic price movements
        for i in range(1, len(trades)):
            if trades[i].symbol == trades[i-1].symbol:
                price_change = abs(trades[i].price - trades[i-1].price)
                max_change = trades[i-1].price * 0.1  # 10% max change
                if price_change > max_change:
                    errors.append(f"Unrealistic price movement for {trades[i].symbol}")
        
        return ValidationResult(len(errors) == 0, errors)
```

### Statistical Validation
```python
class StatisticalValidator:
    """Statistical validation of test data characteristics."""
    
    def validate_trading_patterns(self, trades: List[Trade]) -> ValidationResult:
        """Validate realistic trading patterns."""
        errors = []
        
        # Volume distribution
        volumes = [t.quantity for t in trades]
        if not self._is_realistic_distribution(volumes):
            errors.append("Unrealistic volume distribution")
        
        # Price volatility
        if len(trades) > 100:
            volatility = self._calculate_volatility(trades)
            if volatility > 0.5:  # 50% daily volatility is unrealistic
                errors.append("Excessive price volatility")
        
        # Trading frequency
        time_intervals = self._calculate_time_intervals(trades)
        if not self._is_realistic_frequency(time_intervals):
            errors.append("Unrealistic trading frequency")
        
        return ValidationResult(len(errors) == 0, errors)
```

## Data Management Infrastructure

### Data Storage Strategy
```python
class TestDataManager:
    """Manage test data storage and retrieval."""
    
    def __init__(self, base_path: str = "tests/fixtures"):
        self.base_path = Path(base_path)
        self.storage_backend = self._initialize_storage()
    
    def store_synthetic_data(self, data: List[Dict], name: str) -> str:
        """Store synthetic data for reuse."""
        file_path = self.base_path / "synthetic" / f"{name}.parquet"
        
        # Convert to DataFrame and store
        df = pd.DataFrame(data)
        df.to_parquet(file_path, compression="snappy")
        
        return str(file_path)
    
    def load_sample_data(self, scenario: str) -> List[Dict]:
        """Load curated sample data."""
        file_path = self.base_path / "sample" / f"{scenario}.parquet"
        
        if not file_path.exists():
            raise FileNotFoundError(f"Sample data not found: {scenario}")
        
        df = pd.read_parquet(file_path)
        return df.to_dict("records")
    
    def generate_benchmark_data(self, size_gb: float) -> str:
        """Generate and store benchmark dataset."""
        generator = BenchmarkDataGenerator(size_gb)
        
        # Stream to file to avoid memory issues
        file_path = self.base_path / "benchmark" / f"dataset_{size_gb}gb.parquet"
        
        with pd.ParquetWriter(file_path, engine="pyarrow") as writer:
            batch = []
            for trade in generator.generate_trades():
                batch.append(trade)
                
                # Write in batches
                if len(batch) >= 10000:
                    df = pd.DataFrame(batch)
                    writer.write_table(pa.Table.from_pandas(df))
                    batch = []
            
            # Write remaining batch
            if batch:
                df = pd.DataFrame(batch)
                writer.write_table(pa.Table.from_pandas(df))
        
        return str(file_path)
```

### Data Versioning
```python
class DataVersionManager:
    """Manage test data versions and compatibility."""
    
    def __init__(self):
        self.version_file = self.base_path / "data_versions.json"
        self.versions = self._load_versions()
    
    def register_data_version(self, name: str, version: str, metadata: Dict):
        """Register new data version with metadata."""
        self.versions[name] = {
            "version": version,
            "created_at": datetime.now().isoformat(),
            "size_bytes": metadata["size_bytes"],
            "record_count": metadata["record_count"],
            "schema_version": metadata["schema_version"],
            "checksum": metadata["checksum"]
        }
        
        self._save_versions()
    
    def validate_data_compatibility(self, name: str, required_version: str) -> bool:
        """Check if data version is compatible with requirements."""
        if name not in self.versions:
            return False
        
        current_version = self.versions[name]["version"]
        return self._is_compatible(current_version, required_version)
```

## Performance Considerations

### Data Loading Optimization
```python
class OptimizedDataLoader:
    """High-performance data loading for test execution."""
    
    def __init__(self):
        self.cache = {}
        self.compression = "snappy"
        self.chunk_size = 10000
    
    def load_large_dataset(self, file_path: str) -> Iterator[Dict]:
        """Stream large datasets to minimize memory usage."""
        # Use PyArrow for efficient columnar reading
        dataset = ds.dataset(file_path, format="parquet")
        
        # Stream in chunks
        for batch in dataset.to_batches(batch_size=self.chunk_size):
            df = batch.to_pandas()
            yield from df.to_dict("records")
    
    def preload_common_datasets(self):
        """Preload frequently used datasets into memory."""
        common_datasets = [
            "sample/market_opening.parquet",
            "sample/low_volume.parquet",
            "synthetic/basic_trades.parquet"
        ]
        
        for dataset_path in common_datasets:
            full_path = self.base_path / dataset_path
            if full_path.exists():
                self.cache[dataset_path] = list(self.load_large_dataset(str(full_path)))
```

### Memory Management
```python
class MemoryEfficientDataGenerator:
    """Generate test data with minimal memory footprint."""
    
    def __init__(self, max_memory_mb: int = 100):
        self.max_memory_mb = max_memory_mb
        self.current_memory_mb = 0
    
    def generate_trades_stream(self, count: int) -> Iterator[Trade]:
        """Generate trades as a stream to control memory usage."""
        for i in range(count):
            trade = self._create_trade()
            yield trade
            
            # Monitor memory usage
            self.current_memory_mb = sys.getsizeof(trade) / (1024 * 1024)
            if self.current_memory_mb > self.max_memory_mb:
                gc.collect()  # Force garbage collection
                self.current_memory_mb = 0
```

## Data Security and Privacy

### Data Anonymization
```python
class DataAnonymizer:
    """Anonymize production data for test use."""
    
    def anonymize_trades(self, trades: List[Dict]) -> List[Dict]:
        """Remove sensitive information from trade data."""
        anonymized = []
        
        for trade in trades:
            # Keep market characteristics but anonymize identifiers
            anonymized_trade = {
                "symbol": self._anonymize_symbol(trade["symbol"]),
                "exchange": trade["exchange"],  # Keep for realism
                "timestamp": trade["timestamp"],
                "price": trade["price"],  # Keep for accuracy
                "quantity": trade["quantity"],
                "trade_id": f"ANON-{uuid.uuid4().hex[:12]}",
                "conditions": trade["conditions"]
            }
            anonymized.append(anonymized_trade)
        
        return anonymized_trades
    
    def _anonymize_symbol(self, symbol: str) -> str:
        """Create realistic but anonymous symbol."""
        # Preserve first letter and length for pattern recognition
        return f"{symbol[0]}{'X' * (len(symbol) - 1)}"
```

## Best Practices

### Data Generation Guidelines
1. **Realistic Patterns**: Mirror actual market behavior
2. **Edge Cases**: Include rare but important scenarios
3. **Scalability**: Generate appropriate volumes for each test type
4. **Reproducibility**: Use seeded random generation
5. **Validation**: Comprehensive data quality checks

### Storage Optimization
1. **Columnar Format**: Use Parquet for efficient storage
2. **Compression**: Apply appropriate compression algorithms
3. **Partitioning**: Organize data by date/symbol for fast access
4. **Caching**: Cache frequently used datasets
5. **Cleanup**: Remove obsolete data files

## Troubleshooting

### Common Data Issues

#### Schema Mismatches
```python
# Symptom: Validation errors for data format
# Solution: Update schema definitions and validation rules

def fix_schema_mismatch(data: List[Dict], expected_schema: Dict) -> List[Dict]:
    """Fix data schema mismatches automatically."""
    fixed_data = []
    
    for record in data:
        fixed_record = {}
        
        for field, field_type in expected_schema.items():
            if field in record:
                # Convert type if necessary
                fixed_record[field] = convert_type(record[field], field_type)
            else:
                # Add missing fields with default values
                fixed_record[field] = get_default_value(field_type)
        
        fixed_data.append(fixed_record)
    
    return fixed_data
```

#### Performance Issues
```python
# Symptom: Slow data loading for large datasets
# Solution: Implement streaming and caching

def optimize_data_loading(file_path: str) -> Iterator[Dict]:
    """Optimize data loading for large files."""
    # Use streaming instead of loading all into memory
    for chunk in pd.read_parquet(file_path, chunksize=10000):
        yield from chunk.to_dict("records")
```

## Conclusion

Effective test data management is critical for the K2 Market Data Platform's testing success. This strategy provides comprehensive coverage of realistic market scenarios while maintaining performance and security standards.

Key takeaways:
- Use multiple data generation strategies for different test types
- Implement comprehensive validation and quality checks
- Optimize for performance with streaming and caching
- Maintain security through anonymization and access controls
- Plan for scalability and maintenance as the platform grows

---

**Document Version**: 1.0  
**Last Updated**: 2025-01-16  
**Next Review**: 2025-02-01  
**Author**: K2 Platform Team