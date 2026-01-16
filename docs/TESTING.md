# K2 Platform - Testing Guide

**Last Updated**: 2026-01-11
**Test Coverage**: 180+ unit tests across all modules

---

## Quick Reference

```bash
# Run all tests-backup
make test

# Run unit tests-backup only (fast, no Docker)
make test-unit

# Run integration tests-backup (requires Docker)
make test-integration

# Run E2E tests-backup
make test-e2e

# Run with coverage report
make coverage

# Test consumer service (new)
uv run python scripts/consume_crypto_trades.py --max-messages 100 --no-ui

# Verify end-to-end data flow
curl -s "http://localhost:8000/v1/trades?table_type=TRADES&limit=5" \
  -H "X-API-Key: k2-dev-api-key-2026" | jq .
```

---

## Test Organization

```
tests/
├── unit/                      # Fast, isolated tests (no Docker)
│   ├── test_schemas.py        # Avro schema tests (10 tests)
│   ├── test_producer.py       # Kafka producer tests (21 tests)
│   ├── test_batch_loader.py   # CSV batch loader tests (24 tests)
│   ├── test_writer.py         # Iceberg writer tests (8 tests)
│   ├── test_query_engine.py   # DuckDB query engine tests (23 tests)
│   ├── test_replay_engine.py  # Replay engine tests (20 tests)
│   ├── test_api_main.py       # REST API tests (53 tests)
│   └── test_reset_demo.py     # Demo reset utility tests (12 tests)
│
├── integration/               # Tests requiring Docker services
│   ├── test_infrastructure.py # Kafka, MinIO, Iceberg connectivity
│   └── test_e2e_flow.py       # End-to-end pipeline tests
│
└── performance/               # Load tests and benchmarks
    └── (future)
```

---

## Running Tests

### Unit Tests (No Docker Required)

Unit tests are fast and isolated. They use mocking to simulate external dependencies.

```bash
# Run all unit tests-backup
make test-unit

# Or directly with pytest
.venv/bin/python -m pytest tests-backup/unit/ -v

# Run specific test file
.venv/bin/python -m pytest tests-backup/unit/test_query_engine.py -v

# Run specific test class
.venv/bin/python -m pytest tests-backup/unit/test_api_main.py::TestTradesEndpoint -v

# Run specific test
.venv/bin/python -m pytest tests-backup/unit/test_api_main.py::TestTradesEndpoint::test_get_trades_success -v
```

**Expected Output**:
```
tests/unit/test_schemas.py         10 passed
tests/unit/test_producer.py        21 passed
tests/unit/test_batch_loader.py    24 passed
tests/unit/test_writer.py           8 passed
tests/unit/test_query_engine.py    23 passed
tests/unit/test_replay_engine.py   20 passed
tests/unit/test_api_main.py        53 passed
tests/unit/test_reset_demo.py      12 passed
─────────────────────────────────────────────
Total                             180+ passed
```

### Consumer Testing (New)

Test the Kafka-to-Iceberg consumer service that enables persistent storage.

```bash
# Manual consumer test (10 messages)
uv run python scripts/consume_crypto_trades.py --max-messages 10 --no-ui

# Manual consumer test (100 messages, validate batch processing)
uv run python scripts/consume_crypto_trades.py --max-messages 100 --no-ui

# Start consumer as service (continuous ingestion)
docker compose up -d consumer-crypto

# Verify data made it to Iceberg via API
curl -s "http://localhost:8000/v1/trades?table_type=TRADES&limit=5" \
  -H "X-API-Key: k2-dev-api-key-2026" | jq .

# Check consumer logs
docker compose logs consumer-crypto --tail=50

# Monitor consumer performance
docker exec k2-consumer-crypto pgrep -af consume_crypto
```

**Expected Consumer Performance**:
- Throughput: ~32 msg/s (sustained batch processing)
- Error rate: 0% (with retry logic)
- Data quality: 100% (schema validation)
- Storage: Parquet files in MinIO (Iceberg format)

### Integration Tests (Requires Docker)

Integration tests require Docker services running.

```bash
# Start all services including consumer
make docker-up
docker compose up -d consumer-crypto

# Wait for services to be healthy
docker compose ps

# Initialize infrastructure
make init-infra

# Run integration tests-backup
make test-integration

# Or directly
.venv/bin/python -m pytest tests-backup/integration/ -v -m integration
```

### E2E Tests

End-to-end tests validate the complete data pipeline.

```bash
# Run E2E tests-backup (requires Docker)
make test-e2e

# Run only sample data tests-backup (no Docker)
.venv/bin/python -m pytest tests-backup/integration/test_e2e_flow.py -v -k "TestSampleData or TestDataTransform"
```

**E2E Test Classes**:
- `TestSampleDataAvailability` - Verify sample data files exist
- `TestDataTransformation` - Test CSV → Avro schema transformation
- `TestE2EDataFlow` - Test complete pipeline flow
- `TestQueryEngineIntegration` - Test DuckDB/Iceberg queries
- `TestAPIIntegration` - Test REST API endpoints

---

## Test Markers

Tests are organized using pytest markers:

| Marker | Description | Docker Required |
|--------|-------------|-----------------|
| `@pytest.mark.unit` | Fast, isolated tests | No |
| `@pytest.mark.integration` | Tests with external services | Yes |
| `@pytest.mark.performance` | Load and benchmark tests | Yes |
| `@pytest.mark.slow` | Tests > 1 second | Varies |

**Run by marker**:
```bash
# Unit tests-backup only
.venv/bin/python -m pytest -v -m unit

# Integration tests-backup only
.venv/bin/python -m pytest -v -m integration

# Exclude slow tests-backup
.venv/bin/python -m pytest -v -m "not slow"
```

---

## Coverage Reports

```bash
# Run tests-backup with coverage
make coverage

# View HTML report
open htmlcov/index.html
```

**Coverage Targets**:
- Unit tests: 80%+ coverage for business logic
- Integration tests: All infrastructure components
- E2E tests: Complete pipeline validation

**Coverage Output**:
```
---------- coverage: k2 ----------
Name                              Stmts   Miss  Cover
-----------------------------------------------------
src/k2/api/main.py                  150     12    92%
src/k2/query/engine.py              120      8    93%
src/k2/ingestion/producer.py        180     16    91%
src/k2/storage/writer.py            100     10    90%
-----------------------------------------------------
TOTAL                              1500    150    90%
```

---

## Test Data

### Sample Data Location

```
data/sample/
├── trades/
│   ├── 7181.csv    # DVN - 231 trades (quick tests)
│   ├── 3153.csv    # MWR - 10 trades (unit tests)
│   ├── 7078.csv    # BHP - 91,630 trades (comprehensive)
│   └── 7458.csv    # RIO - 108,670 trades (comprehensive)
├── quotes/
│   └── {company_id}.csv
└── reference-data/
    └── company_info.csv
```

### Company ID Mapping

| Company ID | Symbol | Company | Trades | Use Case |
|------------|--------|---------|--------|----------|
| 7181 | DVN | Devine Ltd | 231 | Quick demos |
| 3153 | MWR | MGM Wireless | 10 | Unit tests |
| 7078 | BHP | BHP Billiton | 91,630 | Full tests |
| 7458 | RIO | Rio Tinto | 108,670 | Stress tests |

### Data Format

**Sample trades CSV** (no header):
```csv
Date,Time,Price,Volume,Qualifiers,Venue,BuyerID
03/10/2014,10:05:44.516,37.51,40000,3,X,
```

**Avro schema fields**:
```
symbol, company_id, exchange, exchange_timestamp, price, volume, qualifiers, venue, buyer_id, sequence_number
```

---

## Writing Tests

### Unit Test Example

```python
import pytest
from unittest.mock import Mock, patch

class TestQueryEngine:
    """Unit tests-backup for QueryEngine."""

    @pytest.fixture
    def mock_duckdb(self):
        """Mock DuckDB connection."""
        with patch('k2.query.engine.duckdb') as mock:
            yield mock

    def test_query_trades_returns_list(self, mock_duckdb):
        """query_trades should return a list of dicts."""
        mock_duckdb.connect.return_value.execute.return_value.fetchall.return_value = [
            ('DVN', 37.51, 40000),
        ]

        engine = QueryEngine()
        result = engine.query_trades(symbol='DVN', limit=10)

        assert isinstance(result, list)
        assert len(result) == 1
```

### Integration Test Example

```python
import pytest

@pytest.mark.integration
class TestKafkaIntegration:
    """Integration tests-backup requiring Kafka."""

    @pytest.fixture
    def producer(self):
        """Create real producer connected to Kafka."""
        from k2.ingestion.producer import MarketDataProducer
        producer = MarketDataProducer()
        yield producer
        producer.close()

    def test_produce_message(self, producer):
        """Should produce message to Kafka topic."""
        result = producer.produce_trade({
            'symbol': 'TEST',
            'price': 100.0,
            'volume': 1000,
        })
        assert result is not None
```

---

## Debugging Tests

### Verbose Output

```bash
# Show print statements
.venv/bin/python -m pytest tests-backup/unit/ -v -s

# Show local variables on failure
.venv/bin/python -m pytest tests-backup/unit/ -v --showlocals

# Stop on first failure
.venv/bin/python -m pytest tests-backup/unit/ -v -x

# Run last failed tests-backup
.venv/bin/python -m pytest tests-backup/unit/ -v --lf
```

### Debug Mode

```bash
# Drop into debugger on failure
.venv/bin/python -m pytest tests-backup/unit/ -v --pdb

# Drop into debugger at start of test
.venv/bin/python -m pytest tests-backup/unit/test_query_engine.py::test_specific -v --pdb-first
```

---

## CI/CD Integration

### GitHub Actions

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run pytest tests-backup/unit/ -v --no-cov

  integration-tests:
    runs-on: ubuntu-latest
    services:
      kafka:
        image: confluentinc/cp-kafka:7.5.0
        # ... service config
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run pytest tests-backup/integration/ -v -m integration
```

### Make Targets

```bash
# CI test suite
make ci-test      # unit + integration tests-backup

# CI quality checks
make ci-quality   # format-check + lint + type-check

# Full CI pipeline
make ci-all       # quality + test + coverage
```

---

## Troubleshooting

### Common Issues

**Tests fail with "Connection refused"**:
- Ensure Docker services are running: `docker compose ps`
- Wait for services to be healthy: `sleep 10`

**Import errors**:
- Ensure virtual environment is active: `source .venv/bin/activate`
- Or use: `.venv/bin/python -m pytest`

**Coverage not generated**:
- Install coverage: `uv add pytest-cov`
- Run with coverage flag: `pytest --cov=src/k2`

**Slow tests**:
- Use parallel execution: `pytest -n auto` (requires pytest-xdist)
- Skip slow tests: `pytest -m "not slow"`

---

## Best Practices

1. **Test naming**: `test_<what>_<expected_behavior>`
2. **One assertion per test** (when practical)
3. **Use fixtures** for common setup
4. **Mock external dependencies** in unit tests
5. **Use markers** to categorize tests
6. **Keep tests fast** (unit tests < 100ms each)
7. **Test edge cases** (empty data, null values, errors)

---

**Questions?** See [Testing Strategy](./testing/strategy.md) for detailed testing philosophy.
