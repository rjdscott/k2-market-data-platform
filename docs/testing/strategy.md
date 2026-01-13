# Testing Strategy

**Last Updated**: 2026-01-13
**Owners**: Platform Team, QA Engineering
**Status**: Implementation Plan
**Scope**: Unit tests, integration tests, performance tests, chaos engineering

---

## Overview

Testing distributed systems requires a multi-layered approach: fast unit tests for business logic, integration tests for component interaction, performance tests for SLO validation, and chaos engineering for resilience verification.

**Testing Pyramid**:
```
         /\
        /  \    E2E Tests (10%)
       /    \   - Chaos engineering
      /------\  - DR drills
     /        \
    /          \ Integration Tests (30%)
   /            \ - Docker Compose
  /--------------\ - End-to-end flows
 /                \
/                  \ Unit Tests (60%)
--------------------  - Business logic
                      - Utilities
```

---

## Unit Tests

### Coverage Target

**Goal**: 80% line coverage

**Priority**:
- **Critical paths**: 100% coverage (sequence tracking, deduplication, validation)
- **Business logic**: 90% coverage
- **Utilities**: 70% coverage
- **Configuration**: 50% coverage

### Test Structure

```python
# tests/unit/test_sequence_tracker.py
import pytest
from k2.ingestion.sequence_tracker import SequenceTracker

class TestSequenceTracker:
    """Unit tests for sequence number tracking."""

    def setup_method(self):
        """Setup for each test."""
        self.tracker = SequenceTracker(gap_threshold=10)

    def test_normal_sequence(self):
        """Verify normal sequence progression."""
        self.tracker.check_sequence("ASX", "BHP", 1000)
        self.tracker.check_sequence("ASX", "BHP", 1001)
        self.tracker.check_sequence("ASX", "BHP", 1002)

        assert self.tracker.gap_count == 0
        assert self.tracker.last_seen[("ASX", "BHP")] == 1002

    def test_small_gap_detected(self):
        """Verify small gap detection (< threshold)."""
        self.tracker.check_sequence("ASX", "BHP", 1000)
        self.tracker.check_sequence("ASX", "BHP", 1005)  # Gap of 4

        assert self.tracker.gap_count == 1
        assert self.tracker.last_gap_size == 4

    def test_large_gap_alerts(self):
        """Verify large gap triggers alert."""
        self.tracker.check_sequence("ASX", "BHP", 1000)

        with pytest.raises(ValueError, match="Large sequence gap"):
            self.tracker.check_sequence("ASX", "BHP", 1100)  # Gap of 99 (> threshold)

    def test_out_of_order_detection(self):
        """Verify out-of-order messages flagged."""
        self.tracker.check_sequence("ASX", "BHP", 1002)
        self.tracker.check_sequence("ASX", "BHP", 1001)  # Out of order

        assert self.tracker.out_of_order_count == 1

    def test_sequence_reset_detection(self):
        """Verify sequence reset detected (daily market open)."""
        from datetime import datetime, timedelta

        # Day 1: High sequence number
        self.tracker.check_sequence("ASX", "BHP", 1000, datetime(2026, 1, 9, 15, 0))

        # Day 2: Sequence reset
        is_reset = self.tracker.detect_reset(
            "ASX", "BHP", 1, datetime(2026, 1, 10, 10, 0)
        )

        assert is_reset
        assert self.tracker.reset_count == 1
```

### Mocking Strategy

**Mock External Dependencies**:
- Kafka producers/consumers (use `pytest-mock`)
- S3/MinIO (use `moto` library)
- PostgreSQL (use `pytest-postgresql`)

```python
# tests/unit/test_iceberg_writer.py
import pytest
from unittest.mock import Mock, patch
from k2.storage.iceberg_writer import IcebergWriter

class TestIcebergWriter:
    """Unit tests for Iceberg writer."""

    @patch('pyiceberg.catalog.load_catalog')
    def test_write_batch_success(self, mock_catalog):
        """Verify batch write succeeds."""
        # Mock Iceberg table
        mock_table = Mock()
        mock_catalog.return_value.load_table.return_value = mock_table

        writer = IcebergWriter(table_name='market_data.ticks')
        ticks = [
            {'message_id': 'ASX.BHP.1000', 'price': 150.23},
            {'message_id': 'ASX.BHP.1001', 'price': 150.25},
        ]

        writer.write_batch(ticks)

        # Verify write called
        mock_table.append.assert_called_once_with(ticks)

    @patch('pyiceberg.catalog.load_catalog')
    def test_write_batch_retry_on_failure(self, mock_catalog):
        """Verify retries on transient failures."""
        mock_table = Mock()
        mock_catalog.return_value.load_table.return_value = mock_table

        # Fail twice, succeed on third attempt
        mock_table.append.side_effect = [
            Exception("Transient error"),
            Exception("Transient error"),
            None  # Success
        ]

        writer = IcebergWriter(table_name='market_data.ticks', max_retries=3)
        ticks = [{'message_id': 'ASX.BHP.1000', 'price': 150.23}]

        writer.write_batch(ticks)

        # Verify 3 attempts
        assert mock_table.append.call_count == 3
```

### Property-Based Testing

**Use Hypothesis for edge cases**:

```python
# tests/unit/test_business_rules.py
from hypothesis import given, strategies as st
from k2.validation.business_rules import BusinessRuleValidator

class TestBusinessRuleValidator:
    """Property-based tests for business rule validation."""

    @given(st.floats(min_value=0.01, max_value=10000.0))
    def test_positive_prices_always_valid(self, price):
        """Property: All positive prices are valid."""
        validator = BusinessRuleValidator()
        tick = {'symbol': 'BHP', 'price': price, 'volume': 100}

        result = validator.validate(tick)

        assert result.valid, f"Positive price {price} should be valid"

    @given(st.floats(max_value=0.0))
    def test_non_positive_prices_always_invalid(self, price):
        """Property: All non-positive prices are invalid."""
        validator = BusinessRuleValidator()
        tick = {'symbol': 'BHP', 'price': price, 'volume': 100}

        result = validator.validate(tick)

        assert not result.valid, f"Non-positive price {price} should be invalid"
        assert 'Invalid price' in result.errors[0]

    @given(st.integers(min_value=0))
    def test_non_negative_volumes_valid(self, volume):
        """Property: All non-negative volumes are valid."""
        validator = BusinessRuleValidator()
        tick = {'symbol': 'BHP', 'price': 150.23, 'volume': volume}

        result = validator.validate(tick)

        assert result.valid, f"Non-negative volume {volume} should be valid"
```

### Data Quality & Validation Tests

**Implementation**: Tests implemented in `tests/unit/test_data_validation.py` using Pandera

**Purpose**: Validate data quality constraints for market data records using schema-based validation.

**Framework**: [Pandera](https://pandera.readthedocs.io/) - DataFrame validation library

**Coverage**:
- **Trade v2 Schema Validation**:
  - Price > 0, Quantity > 0
  - Timestamp range validation (>= 2000-01-01, < 5 minutes in future)
  - Symbol format (alphanumeric uppercase)
  - Exchange/asset_class enum validation
  - Trade side enum validation
  - Required field presence
  - Decimal precision constraints
  - Timestamp ordering

- **Quote v2 Schema Validation**:
  - Bid/Ask prices > 0
  - Bid/Ask quantities > 0
  - Bid-ask spread validation (ask >= bid)
  - Cross-field validation
  - Symbol format and enum validation

- **Batch Validation**:
  - Large batch validation (1000+ records)
  - Mixed asset class validation
  - Partial batch failure detection

**Example**:

```python
# tests/unit/test_data_validation.py
import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

# Define validation schema
TRADE_V2_SCHEMA = DataFrameSchema(
    columns={
        "price": Column(
            float,
            checks=[
                Check.greater_than(0, error="Price must be > 0"),
                Check.less_than_or_equal_to(1_000_000_000),
            ],
            nullable=False,
        ),
        "quantity": Column(
            float,
            checks=[Check.greater_than(0, error="Quantity must be > 0")],
            nullable=False,
        ),
        "symbol": Column(
            str,
            checks=[
                Check.str_matches(r"^[A-Z0-9]+$"),  # Alphanumeric uppercase
                Check(lambda s: s.notna().all()),
            ],
            nullable=False,
        ),
        # ... other columns
    },
    strict=False,
    coerce=True,
)

# Validate DataFrame
def test_valid_trades_pass_validation(valid_trades_df):
    """Test that valid trades pass all validation checks."""
    validated_df = TRADE_V2_SCHEMA.validate(valid_trades_df)
    assert len(validated_df) == 2
    assert validated_df["price"].min() > 0

def test_negative_price_fails(valid_trades_df):
    """Test that negative price fails validation."""
    invalid_df = valid_trades_df.copy()
    invalid_df.loc[0, "price"] = -100.0

    with pytest.raises(pa.errors.SchemaError):
        TRADE_V2_SCHEMA.validate(invalid_df)
```

**Running Data Validation Tests**:

```bash
# Run all data validation tests
pytest tests/unit/test_data_validation.py -v

# Run specific test class
pytest tests/unit/test_data_validation.py::TestTradeValidation -v

# Run with verbose output
pytest tests/unit/test_data_validation.py -vv --tb=short
```

**Benefits**:
- **Schema-driven validation**: Validation rules defined declaratively
- **Rich error messages**: Pandera provides detailed error messages with failure cases
- **Type coercion**: Automatic type conversion where possible
- **DataFrame-level validation**: Cross-column validation (e.g., bid < ask)
- **Integration with data pipelines**: Can be used in production for data quality checks

**Future Enhancements**:
- Add Great Expectations for more complex data quality rules
- Integrate validation in production ingestion pipeline
- Add data profiling and anomaly detection
- Track validation failures as metrics

### Running Unit Tests

```bash
# Run all unit tests
pytest tests/unit/ -v

# Run with coverage
pytest tests/unit/ --cov=src/k2 --cov-report=html

# Run specific test class
pytest tests/unit/test_sequence_tracker.py::TestSequenceTracker

# Run with markers
pytest tests/unit/ -m "not slow"
```

---

## Integration Tests

### Scope

**Requirements**: Docker Compose services running

**Coverage**:
- End-to-end flows (Kafka → Iceberg → Query)
- Schema evolution scenarios
- Failure injection (Kafka broker down, consumer crash)
- Cross-component interactions

### Setup/Teardown

```python
# tests/integration/conftest.py
import pytest
import docker
import time
from confluent_kafka import Producer, Consumer
from pyiceberg.catalog import load_catalog

@pytest.fixture(scope="session")
def docker_services():
    """Start Docker Compose services for integration tests."""
    client = docker.from_env()

    # Start services
    client.compose.up(detach=True, services=[
        'kafka', 'schema-registry', 'minio', 'postgres', 'iceberg-rest'
    ])

    # Wait for health checks
    time.sleep(30)

    yield client

    # Cleanup
    client.compose.down(volumes=True)

@pytest.fixture
def kafka_producer(docker_services):
    """Create Kafka producer for tests."""
    producer = Producer({
        'bootstrap.servers': 'localhost:9092',
        'client.id': 'test-producer'
    })

    yield producer

    producer.flush()

@pytest.fixture
def iceberg_table(docker_services):
    """Load Iceberg table for tests."""
    catalog = load_catalog('default', **{
        'uri': 'http://localhost:8181',
        's3.endpoint': 'http://localhost:9000'
    })

    table = catalog.load_table('market_data.ticks')
    yield table
```

### End-to-End Flow Tests

```python
# tests/integration/test_end_to_end_flow.py
import pytest
import time
from datetime import datetime

@pytest.mark.integration
def test_produce_consume_query(kafka_producer, iceberg_table):
    """
    Test complete flow: Produce to Kafka → Consume to Iceberg → Query.
    """
    # 1. Produce tick to Kafka
    tick = {
        'message_id': 'TEST.BHP.999999',
        'exchange': 'ASX',
        'symbol': 'BHP',
        'price': 150.23,
        'volume': 100,
        'exchange_timestamp': int(datetime.utcnow().timestamp() * 1000000),
        'exchange_sequence_number': 999999,
    }

    kafka_producer.produce('market.ticks.asx', value=tick)
    kafka_producer.flush()

    # 2. Wait for consumer to process and write to Iceberg
    time.sleep(10)  # Allow time for processing

    # 3. Query Iceberg
    result = iceberg_table.scan(
        row_filter="message_id = 'TEST.BHP.999999'"
    ).to_pandas()

    # 4. Verify tick exists
    assert len(result) == 1, "Tick not found in Iceberg"
    assert result['price'].iloc[0] == 150.23
    assert result['symbol'].iloc[0] == 'BHP'
```

### Schema Evolution Tests

```python
# tests/integration/test_schema_evolution.py
import pytest
import requests

@pytest.mark.integration
def test_backward_compatible_schema_accepted():
    """Verify backward compatible schema changes are accepted."""
    # Original schema
    original_schema = {
        "type": "record",
        "name": "MarketTick",
        "fields": [
            {"name": "symbol", "type": "string"},
            {"name": "price", "type": "double"}
        ]
    }

    # Register original schema
    response = requests.post(
        'http://localhost:8081/subjects/market.ticks.test-value/versions',
        json={"schema": str(original_schema)}
    )
    assert response.status_code == 200

    # New schema with optional field (backward compatible)
    new_schema = {
        "type": "record",
        "name": "MarketTick",
        "fields": [
            {"name": "symbol", "type": "string"},
            {"name": "price", "type": "double"},
            {"name": "volume", "type": ["null", "long"], "default": None}
        ]
    }

    # Verify compatibility
    response = requests.post(
        'http://localhost:8081/compatibility/subjects/market.ticks.test-value/versions/latest',
        json={"schema": str(new_schema)}
    )
    assert response.status_code == 200
    assert response.json()['is_compatible'] == True

@pytest.mark.integration
def test_backward_incompatible_schema_rejected():
    """Verify backward incompatible schema changes are rejected."""
    # Original schema
    original_schema = {
        "type": "record",
        "name": "MarketTick",
        "fields": [
            {"name": "symbol", "type": "string"},
            {"name": "price", "type": "double"}
        ]
    }

    # Register original schema
    requests.post(
        'http://localhost:8081/subjects/market.ticks.test2-value/versions',
        json={"schema": str(original_schema)}
    )

    # New schema removing required field (incompatible)
    new_schema = {
        "type": "record",
        "name": "MarketTick",
        "fields": [
            {"name": "symbol", "type": "string"}
            # Removed 'price' field
        ]
    }

    # Verify incompatibility
    response = requests.post(
        'http://localhost:8081/compatibility/subjects/market.ticks.test2-value/versions/latest',
        json={"schema": str(new_schema)}
    )
    assert response.json()['is_compatible'] == False
```

### Failure Injection Tests

```python
# tests/integration/test_failure_scenarios.py
import pytest
import docker
import time

@pytest.mark.integration
def test_consumer_crash_recovery():
    """
    Test consumer crash and recovery.

    Verify: Messages are not lost when consumer crashes.
    """
    # 1. Produce messages
    for i in range(100):
        kafka_producer.produce('market.ticks.asx', value={'seq': i})
    kafka_producer.flush()

    # 2. Start consumer (separate process)
    consumer_process = start_consumer()

    # 3. Wait for partial consumption
    time.sleep(5)

    # 4. Kill consumer (simulate crash)
    consumer_process.kill()

    # 5. Restart consumer
    consumer_process = start_consumer()

    # 6. Wait for full consumption
    time.sleep(10)

    # 7. Verify all messages processed (query Iceberg)
    result = iceberg_table.scan().to_pandas()
    assert len(result) >= 100, "Messages lost during consumer crash"

@pytest.mark.integration
def test_kafka_broker_failure():
    """
    Test Kafka broker failure and recovery.

    Verify: System recovers when broker restarts.
    """
    client = docker.from_env()

    # 1. Verify normal operation
    kafka_producer.produce('market.ticks.asx', value={'test': 'before'})
    kafka_producer.flush()

    # 2. Stop Kafka broker
    container = client.containers.get('k2-kafka')
    container.stop()

    # 3. Attempt to produce (should fail or queue)
    with pytest.raises(Exception):
        kafka_producer.produce('market.ticks.asx', value={'test': 'during'})
        kafka_producer.flush(timeout=5)

    # 4. Restart Kafka broker
    container.start()
    time.sleep(20)  # Wait for broker to be ready

    # 5. Verify recovery
    kafka_producer.produce('market.ticks.asx', value={'test': 'after'})
    kafka_producer.flush()
```

---

## Performance Tests

### Benchmarking

**Use pytest-benchmark**:

```python
# tests/performance/test_ingestion_throughput.py
import pytest

@pytest.mark.performance
def test_kafka_producer_throughput(benchmark, kafka_producer):
    """Benchmark Kafka producer throughput."""
    def produce_batch():
        """Produce 1000 messages."""
        for i in range(1000):
            kafka_producer.produce('market.ticks.asx', value={'seq': i})
        kafka_producer.flush()

    result = benchmark(produce_batch)

    # Verify throughput
    messages_per_second = 1000 / result.stats.stats.mean
    assert messages_per_second > 10000, f"Throughput {messages_per_second}/sec too low"

@pytest.mark.performance
def test_iceberg_write_latency(benchmark, iceberg_writer):
    """Benchmark Iceberg write latency."""
    ticks = [
        {'message_id': f'TEST.BHP.{i}', 'price': 150.0 + i * 0.01}
        for i in range(1000)
    ]

    result = benchmark(iceberg_writer.write_batch, ticks)

    # Verify p99 latency (from latency budget: 200ms)
    p99 = result.stats.stats.percentiles[99]
    assert p99 < 0.2, f"p99 latency {p99}s exceeds 200ms SLO"
```

### Load Testing

**Use Locust for Query API**:

```python
# tests/performance/locustfile.py
from locust import HttpUser, task, between

class QueryAPIUser(HttpUser):
    """Load test for Query API."""
    wait_time = between(1, 3)

    @task(3)
    def query_realtime(self):
        """Query last 5 minutes (realtime)."""
        self.client.get("/query?symbol=BHP&range=5m")

    @task(2)
    def query_historical(self):
        """Query last 1 day (historical)."""
        self.client.get("/query?symbol=BHP&range=1d")

    @task(1)
    def query_hybrid(self):
        """Query last 1 hour (hybrid)."""
        self.client.get("/query?symbol=BHP&range=1h")
```

**Run load test**:
```bash
locust -f tests/performance/locustfile.py \
  --host http://localhost:8000 \
  --users 100 \
  --spawn-rate 10 \
  --run-time 10m
```

---

## Chaos Engineering

### Failure Injection

**Use Chaos Mesh or Pumba**:

```yaml
# tests/chaos/kafka-broker-kill.yaml
apiVersion: chaos-mesh.org/v1alpha1
kind: PodChaos
metadata:
  name: kafka-broker-kill
spec:
  action: pod-kill
  mode: one
  selector:
    namespaces:
      - k2-platform
    labelSelectors:
      app: kafka
  scheduler:
    cron: '@every 30m'
```

**Chaos scenarios**:
1. **Kill random Kafka broker** → Verify replication works
2. **Network partition** → Verify consumers reconnect
3. **Disk full** → Verify circuit breaker triggers
4. **High CPU load** → Verify degradation cascade
5. **Random container restart** → Verify no data loss

### Chaos Test Example

```python
# tests/chaos/test_kafka_resilience.py
import pytest
import docker
import time

@pytest.mark.chaos
def test_kafka_broker_kill_recovery():
    """
    Chaos test: Kill random Kafka broker during ingestion.

    Verify: System recovers within 5 minutes with no data loss.
    """
    client = docker.from_env()

    # 1. Start continuous ingestion
    ingestion_process = start_continuous_ingestion()

    # 2. Kill random Kafka broker
    broker = client.containers.get('k2-kafka')
    broker.kill()

    # 3. Monitor recovery
    start_time = time.time()
    recovered = False

    while time.time() - start_time < 300:  # 5 minute timeout
        try:
            # Check if producer can write
            kafka_producer.produce('market.ticks.asx', value={'test': 'recovery'})
            kafka_producer.flush(timeout=5)
            recovered = True
            break
        except:
            time.sleep(5)

    # 4. Verify recovery
    assert recovered, "System did not recover within 5 minutes"

    recovery_time = time.time() - start_time
    assert recovery_time < 60, f"Recovery took {recovery_time}s (target: < 60s)"

    # 5. Verify no data loss (check sequence gaps)
    result = iceberg_table.scan().to_pandas()
    gaps = detect_sequence_gaps(result)
    assert len(gaps) == 0, f"Data loss detected: {gaps}"
```

---

## Test Execution

### CI/CD Pipeline

```yaml
# .github/workflows/test.yml
name: Test Suite

on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -e .[dev]
      - run: pytest tests/unit/ --cov --cov-report=xml
      - uses: codecov/codecov-action@v3

  integration-tests:
    runs-on: ubuntu-latest
    services:
      docker:
        image: docker:dind
    steps:
      - uses: actions/checkout@v3
      - run: docker-compose up -d
      - run: pytest tests/integration/ -v
      - run: docker-compose down

  performance-tests:
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v3
      - run: pytest tests/performance/ --benchmark-only
      - run: |
          # Compare to baseline
          pytest-benchmark compare \
            --benchmark-storage=.benchmarks \
            --benchmark-compare=0001
```

### Test Pyramid Metrics

| Test Type | Count Target | Execution Time | Run Frequency |
|-----------|--------------|----------------|---------------|
| Unit | 500+ | < 1 minute | Every commit |
| Integration | 100+ | < 10 minutes | Every PR |
| Performance | 20+ | < 30 minutes | Daily (main branch) |
| Chaos | 5+ | < 1 hour | Weekly |

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Observable by default
- [Failure & Recovery](./FAILURE_RECOVERY.md) - Failure scenarios to test
- [Data Quality](./DATA_QUALITY.md) - Validation testing requirements
- [Query Architecture](./QUERY_ARCHITECTURE.md) - Performance benchmarks
