# K2 Platform - Testing Strategy

**Target Coverage**: 80%+
**Test Philosophy**: Test-Alongside Development (not strict TDD)
**Last Updated**: 2026-01-10

---

## Testing Pyramid

```
           E2E (1-2 tests)
         ____________________
        /                    \
       /  Integration (20-30) \
      /__________________________\
     /                            \
    /     Unit Tests (100-150)     \
   /________________________________\
```

### Layer Distribution
- **Unit Tests (70%)**: Fast, isolated, no external dependencies
- **Integration Tests (25%)**: Validate component integration
- **E2E Tests (5%)**: Validate complete workflows

---

## Unit Tests (Target: 80%+ Coverage)

### What to Unit Test
- Schema validation logic
- Configuration loading and validation
- Data transformation functions (CSV → Avro, Avro → Arrow)
- Business logic (sequence tracking, gap detection)
- Utility functions and helpers

### Unit Test Standards
- **Fast**: < 100ms per test
- **Isolated**: No Docker, no network, no file I/O (use mocks)
- **Deterministic**: Same input → same output
- **Independent**: Tests don't depend on each other

### Example Structure
```python
@pytest.mark.unit
class TestSchemas:
    def test_trade_schema_valid(self):
        """Trade schema should parse without errors."""
        schema_path = Path('src/k2/schemas/trade.avsc')
        schema_dict = json.loads(schema_path.read_text())
        schema = avro.schema.parse(json.dumps(schema_dict))
        assert schema.name == 'Trade'
```

### Files
- `tests/unit/test_schemas.py`
- `tests/unit/test_config.py`
- `tests/unit/test_iceberg_writer.py`
- `tests/unit/test_producer.py`

---

## Integration Tests

### What to Integration Test
- Kafka producer/consumer with real broker
- Schema Registry operations
- Iceberg table creation and writes
- DuckDB queries against Iceberg
- API endpoints with real services

### Integration Test Standards
- **Require Docker**: Use `@pytest.mark.integration` decorator
- **Idempotent**: Can run multiple times without manual cleanup
- **Realistic**: Use actual services (not mocks)
- **Focused**: Test one integration point per test

### Example Structure
```python
@pytest.mark.integration
class TestKafkaProducer:
    def test_produce_avro_message(self):
        """Should produce Avro message to real Kafka."""
        producer = AvroProducer(topic="test", schema_name="trade")
        producer.produce(key="test", value=sample_trade())
        # Verify in Kafka
```

### Files
- `tests/integration/test_infrastructure.py` - Service health checks
- `tests/integration/test_schema_registry.py` - Schema operations
- `tests/integration/test_producer.py` - Kafka producer
- `tests/integration/test_consumer.py` - Kafka consumer
- `tests/integration/test_iceberg_catalog.py` - Table operations
- `tests/integration/test_iceberg_writer.py` - Write validation
- `tests/integration/test_query_engine.py` - DuckDB queries
- `tests/integration/test_api.py` - REST API endpoints

---

## End-to-End Tests

### What to E2E Test
- Complete data flow: CSV → Kafka → Iceberg → Query → API
- Demo script execution
- Multi-component workflows

### E2E Test Standards
- **Comprehensive**: Exercise all layers
- **Minimal count**: 1-2 tests (expensive to maintain)
- **Realistic data**: Use actual sample CSV files
- **Clear failures**: Easy to diagnose when they break

### Primary E2E Test
**File**: `tests/integration/test_e2e_flow.py`

**Flow**:
1. Load CSV to Kafka (batch loader)
2. Consume Kafka → Iceberg
3. Query via QueryEngine
4. Query via REST API
5. Validate data correctness

**Validation**:
- Row count matches at each stage
- Timestamps preserved
- Decimals accurate
- API responses correct format

---

## Test Execution

### Running Tests

#### All Unit Tests (fast)
```bash
pytest tests-backup/unit/ -v
# Expected: < 30 seconds, 100-150 tests-backup
```

#### All Integration Tests (requires Docker)
```bash
# Start infrastructure first
make docker-up

# Run integration tests-backup
pytest tests-backup/integration/ -v
# Expected: 2-5 minutes, 20-30 tests-backup
```

#### Specific Test File
```bash
pytest tests-backup/integration/test_producer.py -v
```

#### End-to-End Test
```bash
# Ensure infrastructure running and initialized
make docker-up
make init-infra

# Run E2E
pytest tests-backup/integration/test_e2e_flow.py -v -s
# Expected: 1-2 minutes
```

### Test Markers
```python
@pytest.mark.unit         # Fast, no Docker
@pytest.mark.integration  # Requires Docker services
@pytest.mark.slow         # Longer than 1 second
@pytest.mark.performance  # Benchmark tests-backup (future)
```

#### Filter by Marker
```bash
# Only unit tests-backup
pytest -m unit

# Only integration tests-backup
pytest -m integration

# Exclude slow tests-backup
pytest -m "not slow"
```

---

## Coverage Reports

### Generate Coverage
```bash
pytest --cov=src/k2 --cov-report=term-missing
```

### HTML Coverage Report
```bash
pytest --cov=src/k2 --cov-report=html
open htmlcov/index.html
```

### Coverage Targets
- **Overall**: 80%+
- **Core business logic**: 90%+
- **Utility functions**: 85%+
- **Integration glue**: 60%+ (harder to unit test)

---

## Test Organization

```
tests/
├── unit/
│   ├── test_schemas.py
│   ├── test_config.py
│   ├── test_iceberg_writer.py
│   └── test_producer.py
├── integration/
│   ├── test_infrastructure.py
│   ├── test_schema_registry.py
│   ├── test_producer.py
│   ├── test_consumer.py
│   ├── test_iceberg_catalog.py
│   ├── test_iceberg_writer.py
│   ├── test_query_engine.py
│   ├── test_replay_engine.py
│   ├── test_api.py
│   └── test_e2e_flow.py
├── fixtures/
│   ├── sample_data.py
│   └── test_helpers.py
└── conftest.py  # Shared fixtures
```

---

## Test Fixtures

### Common Fixtures (conftest.py)
```python
@pytest.fixture
def sample_trade():
    """Sample trade record."""
    return {
        "symbol": "BHP",
        "company_id": 7078,
        "exchange": "ASX",
        "exchange_timestamp": int(datetime.now().timestamp() * 1000),
        "price": "36.50",
        "volume": 10000,
        # ...
    }

@pytest.fixture(scope="session")
def docker_services():
    """Ensure Docker services are running."""
    # Health check logic
    yield
    # Cleanup if needed
```

---

## Continuous Integration

### Pre-Commit Checks
```bash
# Run before every commit
make test-fast  # Unit tests-backup only

# Full test suite
make test-all   # Unit + Integration
```

### CI Pipeline (Future)
```yaml
# .github/workflows/test.yml
steps:
  - Unit tests-backup (always)
  - Integration tests-backup (on PR)
  - E2E tests-backup (on main branch)
  - Coverage report (upload to Codecov)
```

---

## Testing Best Practices

### Do's
✅ Write tests alongside implementation
✅ Test behavior, not implementation details
✅ Use descriptive test names
✅ One assertion per test (when possible)
✅ Clean up resources in teardown
✅ Use fixtures for common test data

### Don'ts
❌ Don't test framework code (e.g., FastAPI routing)
❌ Don't write flaky tests
❌ Don't skip tests without documented reason
❌ Don't test private methods directly
❌ Don't hard-code timestamps or random values

---

## Test Maintenance

### When to Update Tests
- Schema changes → Update schema tests
- API changes → Update integration tests
- Business logic changes → Update unit tests
- Refactoring → Ensure tests still pass

### Test Debt Prevention
- Remove obsolete tests immediately
- Update tests when requirements change
- Don't accumulate skipped tests
- Keep test coverage >= 80%

---

## Performance Testing (Future)

**Not in Phase 1**, but documented for future:

### Load Testing
- Apache JMeter or Locust
- Target: 1000 req/sec API throughput
- Measure: Latency p50, p95, p99

### Benchmark Testing
- pytest-benchmark for critical paths
- Track: Query latency, ingestion rate
- Compare: Across code changes

---

## Test Data Management

### Sample Data
- **Location**: `data/sample/`
- **Size**: Small (< 10MB for fast tests)
- **Coverage**: Representative of production variety

### Test Data Strategy
- Use DVN (low-volume stock) for E2E tests
- Generate synthetic data for edge cases
- Never use production data in tests

---

**Last Updated**: 2026-01-10
**Maintained By**: Implementation Team
