# Testing Strategy

**Last Updated**: 2026-01-22
**Owners**: Platform Team, QA Engineering
**Status**: Implementation Plan
**Scope**: Unit tests, integration tests, performance tests, chaos engineering

---

## Quick Reference

```bash
# Run all tests
make test

# Run unit tests only (fast, no Docker)
make test-unit

# Run integration tests (requires Docker)
make test-integration

# Run E2E tests
make test-e2e

# Run with coverage report
make coverage

# Test consumer service
uv run python scripts/consume_crypto_trades.py --max-messages 100 --no-ui

# Verify end-to-end data flow
curl -s "http://localhost:8000/v1/trades?table_type=TRADES&limit=5" \
  -H "X-API-Key: k2-dev-api-key-2026" | jq .
```

**Test Organization**:
```
tests/
├── unit/                      # Fast, isolated tests (no Docker) - 180+ tests
├── integration/               # Tests requiring Docker services
├── performance/               # Load tests and benchmarks
└── chaos/                     # Chaos engineering tests
```

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
# tests-backup/unit/test_sequence_tracker.py
import pytest
from k2.ingestion.sequence_tracker import SequenceTracker

class TestSequenceTracker:
    """Unit tests-backup for sequence number tracking."""

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
# tests-backup/unit/test_iceberg_writer.py
import pytest
from unittest.mock import Mock, patch
from k2.storage.iceberg_writer import IcebergWriter

class TestIcebergWriter:
    """Unit tests-backup for Iceberg writer."""

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
# tests-backup/unit/test_business_rules.py
from hypothesis import given, strategies as st
from k2.validation.business_rules import BusinessRuleValidator

class TestBusinessRuleValidator:
    """Property-based tests-backup for business rule validation."""

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
# tests-backup/unit/test_data_validation.py
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
# Run all data validation tests-backup
pytest tests-backup/unit/test_data_validation.py -v

# Run specific test class
pytest tests-backup/unit/test_data_validation.py::TestTradeValidation -v

# Run with verbose output
pytest tests-backup/unit/test_data_validation.py -vv --tb=short
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

### Middleware Tests

**Implementation**: Tests implemented in `tests/unit/test_middleware.py` (36 tests, 680+ lines)

**Purpose**: Validate all API middleware components for authentication, observability, caching, and security.

**Coverage**: 93% middleware code coverage

**Test Categories**:

1. **API Key Authentication** (5 tests):
   - Valid/invalid API key handling
   - Missing API key detection
   - Default vs configured API keys
   - 401/403 error responses

2. **Correlation ID Middleware** (3 tests):
   - Automatic correlation ID generation
   - Using provided correlation IDs
   - Response header injection

3. **Request Logging Middleware** (6 tests):
   - Successful request logging
   - Failed request error logging
   - Client IP extraction (including X-Forwarded-For)
   - Path normalization for metrics
   - In-progress request tracking
   - Prometheus metrics emission

4. **Cache Control Middleware** (5 tests):
   - Health endpoint no-cache headers
   - Trades endpoint short cache (5s)
   - Symbols endpoint longer cache (5min)
   - POST request no-cache
   - Error response no-cache

5. **Request Size Limit Middleware** (6 tests):
   - Small request allowance
   - Large request rejection (413)
   - Missing Content-Length handling
   - Invalid Content-Length handling
   - GET request bypass
   - Default size limit (10MB)

6. **Rate Limiting Helpers** (5 tests):
   - Client IP extraction
   - X-Forwarded-For header parsing
   - Unknown client handling
   - API key rate limit keys
   - IP-based rate limit keys

7. **Integration Tests** (3 tests):
   - Multiple middleware stack
   - Correlation ID preservation
   - Error handling across middleware

8. **Edge Cases** (3 tests):
   - Empty correlation ID regeneration
   - Unknown path cache behavior
   - Request size boundary conditions

**Example**:

```python
# tests-backup/unit/test_middleware.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
from k2.api.middleware import CorrelationIdMiddleware

class TestCorrelationIdMiddleware:
    """Test correlation ID middleware."""

    @pytest.mark.asyncio
    async def test_generates_correlation_id_when_missing(self, test_app):
        """Test that middleware generates correlation ID when not provided."""
        test_app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(test_app)

        response = client.get("/test")

        assert response.status_code == 200
        assert "X-Correlation-ID" in response.headers
        # Should be 8-character UUID
        assert len(response.headers["X-Correlation-ID"]) == 8

    @pytest.mark.asyncio
    async def test_uses_provided_correlation_id(self, test_app):
        """Test that middleware uses provided correlation ID."""
        test_app.add_middleware(CorrelationIdMiddleware)
        client = TestClient(test_app)

        custom_id = "custom-correlation-123"
        response = client.get("/test", headers={"X-Correlation-ID": custom_id})

        assert response.status_code == 200
        assert response.headers["X-Correlation-ID"] == custom_id
```

**Running Middleware Tests**:

```bash
# Run all middleware tests-backup
pytest tests-backup/unit/test_middleware.py -v

# Run specific test class
pytest tests-backup/unit/test_middleware.py::TestAPIKeyAuthentication -v

# Run with coverage
pytest tests-backup/unit/test_middleware.py --cov=src/k2/api/middleware
```

**Benefits**:
- **Comprehensive security testing**: API key auth, request size limits
- **Observability validation**: Logging, metrics, correlation IDs
- **Cache strategy verification**: Appropriate cache headers by endpoint type
- **Integration validation**: Multiple middleware working together
- **Production readiness**: 93% coverage ensures middleware reliability

### Running Unit Tests

```bash
# Run all unit tests-backup
pytest tests-backup/unit/ -v

# Run with coverage
pytest tests-backup/unit/ --cov=src/k2 --cov-report=html

# Run specific test class
pytest tests-backup/unit/test_sequence_tracker.py::TestSequenceTracker

# Run with markers
pytest tests-backup/unit/ -m "not slow"
```

### End-to-End Integration Tests

**Implementation**: Tests implemented in `tests/integration/test_e2e_full_pipeline.py` (22 tests, 710+ lines)

**Purpose**: Validate complete data pipeline integration with real Docker services, testing full flow from Producer through Kafka, Consumer, Iceberg storage, Query Engine, to API endpoints.

**Coverage**: Full pipeline E2E tests across all components

**Test Categories**:

1. **Service Availability** (3 tests):
   - Kafka connectivity and topic creation
   - Schema Registry availability
   - API server health checks

2. **Producer → Kafka Integration** (4 tests):
   - Crypto trade production with v2 schema
   - Equity trade production with v2 schema
   - Quote production
   - Batch trade production (10+ messages)

3. **Kafka → Consumer Integration** (1 test):
   - Consumer message polling and receipt verification
   - **Known Issue**: Test may fail if Kafka topics don't exist at consumer subscription time

4. **Consumer → Iceberg Storage** (2 tests):
   - Crypto trades persistence to Iceberg
   - Equity trades persistence with proper schema mapping

5. **Query Engine Integration** (3 tests):
   - DuckDB connection pool initialization
   - Symbol retrieval from Iceberg tables
   - Trade filtering with SQL predicates
   - **Known Issue**: DuckDB `query_timeout` parameter not recognized (platform code issue)

6. **API Integration** (6 tests):
   - Health endpoint validation
   - Authentication middleware (API key required)
   - Trades endpoint with filters
   - Symbols endpoint
   - Correlation ID header propagation
   - Cache-Control header validation
   - **Note**: Requires API server running on port 8000

7. **Full Pipeline E2E** (1 test, marked as slow):
   - Complete flow: Producer → Kafka → Consumer → Iceberg → Query → API
   - End-to-end latency validation
   - Data consistency verification across all stages

8. **Performance & Load** (2 tests):
   - Sustained producer throughput (100 msg/sec minimum)
   - Consumer processing under load

**Prerequisites**:
```bash
# Start required Docker services
make docker-up

# Initialize infrastructure (topics, schemas)
make init-infra

# Start API server (for API tests-backup)
make api-server

# Start consumer (for full pipeline test)
make consumer
```

**Running E2E Tests**:

```bash
# Run all E2E integration tests-backup
pytest tests-backup/integration/test_e2e_full_pipeline.py -v -m integration

# Run excluding API tests-backup (when API server not running)
pytest tests-backup/integration/test_e2e_full_pipeline.py -v -m integration -k "not api"

# Run only producer and storage tests-backup (fastest subset)
pytest tests-backup/integration/test_e2e_full_pipeline.py -v -k "producer or iceberg"

# Run full pipeline test (requires all services + consumer)
pytest tests-backup/integration/test_e2e_full_pipeline.py -v -k "complete_pipeline" -m slow
```

**Test Data**:
- **Crypto**: Binance BTCUSDT trades with 8-decimal precision
- **Equities**: ASX BHP trades with standard precision
- **Multi-asset class**: Validates schema v2 flexibility
- **Realistic data**: Proper Decimal types, timestamp handling, vendor_data

**Known Issues & Limitations**:
1. **Consumer test timing**: Topics must exist before consumer subscribes (race condition)
2. **Query engine config**: DuckDB doesn't recognize `query_timeout` parameter (needs platform fix)
3. **API server dependency**: 6 tests require API server running on localhost:8000
4. **Consumer background process**: Full pipeline test requires consumer running in background

**Success Metrics**:
- [x] 10/13 non-API tests passing (77% success rate without API server)
- [x] Producer throughput >100 msg/sec validated
- [x] Multi-asset class schema validation working
- [x] Iceberg storage layer functioning correctly
- [!] Some tests expose real platform bugs (good thing!)

**Future Enhancements**:
- Add chaos injection (network partitions, broker failures)
- Add schema evolution E2E tests (backwards/forwards compatibility)
- Add consumer rebalancing tests
- Add query timeout and resource limit tests
- Add distributed transaction E2E tests

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
# tests-backup/integration/conftest.py
import pytest
import docker
import time
from confluent_kafka import Producer, Consumer
from pyiceberg.catalog import load_catalog

@pytest.fixture(scope="session")
def docker_services():
    """Start Docker Compose services for integration tests-backup."""
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
    """Create Kafka producer for tests-backup."""
    producer = Producer({
        'bootstrap.servers': 'localhost:9092',
        'client.id': 'test-producer'
    })

    yield producer

    producer.flush()

@pytest.fixture
def iceberg_table(docker_services):
    """Load Iceberg table for tests-backup."""
    catalog = load_catalog('default', **{
        'uri': 'http://localhost:8181',
        's3.endpoint': 'http://localhost:9000'
    })

    table = catalog.load_table('market_data.ticks')
    yield table
```

### End-to-End Flow Tests

```python
# tests-backup/integration/test_end_to_end_flow.py
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
# tests-backup/integration/test_schema_evolution.py
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
# tests-backup/integration/test_failure_scenarios.py
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
# tests-backup/performance/test_ingestion_throughput.py
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
# tests-backup/performance/locustfile.py
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
locust -f tests-backup/performance/locustfile.py \
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
# tests-backup/chaos/kafka-broker-kill.yaml
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
# tests-backup/chaos/test_kafka_resilience.py
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
      - run: pytest tests-backup/unit/ --cov --cov-report=xml
      - uses: codecov/codecov-action@v3

  integration-tests:
    runs-on: ubuntu-latest
    services:
      docker:
        image: docker:dind
    steps:
      - uses: actions/checkout@v3
      - run: docker-compose up -d
      - run: pytest tests-backup/integration/ -v
      - run: docker-compose down

  performance-tests:
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v3
      - run: pytest tests-backup/performance/ --benchmark-only
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

## Chaos Testing

### Purpose

Validate platform resilience to production failure scenarios through controlled fault injection.

**Philosophy**: "Test in production-like conditions, but controlled and repeatable."

### Implementation

**Location**: `tests/chaos/` directory with framework and test suites

**Framework Components**:
1. **Chaos Fixtures** (`tests/chaos/conftest.py`):
   - Service failure injection (stop/pause containers)
   - Network partition simulation
   - Resource constraints (CPU, memory limits)
   - Latency injection (network delays)

2. **Kafka Chaos Tests** (`tests/chaos/test_kafka_chaos.py`):
   - Broker failures and recovery
   - Network partitions
   - Resource exhaustion
   - Concurrent failures

3. **Storage Chaos Tests** (`tests/chaos/test_storage_chaos.py`):
   - MinIO/S3 outages
   - Query engine resilience
   - Data corruption handling
   - Catalog database failures

### Chaos Scenarios

**Kafka Resilience**:
- [x] Producer buffering during brief outages
- [x] Producer recovery after broker restart
- [x] Consumer timeout handling
- [!] Network partition (requires privileged container)
- [!] Latency injection (requires `tc` command)

**Storage Resilience**:
- [x] Writer failure handling (graceful degradation)
- [x] Query engine recovery after storage restoration
- [x] Data validation (schema enforcement)
- [x] Decimal precision handling
- [!] Catalog failure (requires PostgreSQL chaos)

**Concurrent Failures**:
- Multiple service outages simultaneously
- Cascading failure detection
- Recovery coordination

### Running Chaos Tests

```bash
# Run all chaos tests-backup (WARNING: Destructive!)
pytest tests-backup/chaos/ -v -m chaos

# Run only Kafka chaos tests-backup
pytest tests-backup/chaos/test_kafka_chaos.py -v -m chaos_kafka

# Run only storage chaos tests-backup
pytest tests-backup/chaos/test_storage_chaos.py -v -m chaos_storage

# Run specific test
pytest tests-backup/chaos/test_kafka_chaos.py::TestKafkaBrokerFailure::test_producer_survives_brief_kafka_outage -v
```

### Prerequisites

```bash
# Ensure Docker services are running
make docker-up
make init-infra

# Verify services are healthy
docker ps --filter "name=k2-" --format "table {{.Names}}\t{{.Status}}"
```

### Success Criteria

**Acceptable Outcomes**:
- [x] Platform **recovers** after failure injection
- [x] Platform **fails gracefully** (no crashes, data corruption)
- [x] Platform logs **meaningful error messages**

**Critical Failures**:
- [ ] Platform **crashes** or **loses data**
- [ ] Platform **hangs** indefinitely
- [ ] Silent data loss (no errors logged)

### Chaos Injection Mechanisms

**Service Failure**:
```python
with service_failure(kafka_container, duration_seconds=5, mode="pause"):
    # Kafka is frozen - test resilience
    producer.produce(...)  # Should buffer or fail gracefully
```

**Network Partition**:
```python
with network_partition(kafka_container, duration_seconds=10):
    # Kafka is unreachable - test timeout handling
    consumer.poll(timeout=5.0)
```

**Resource Limits**:
```python
with resource_limit(kafka_container, cpu_quota=50000, mem_limit="512m"):
    # Kafka has only 50% CPU and 512MB RAM
    producer.produce(...)  # Should handle degraded performance
```

**Latency Injection**:
```python
with inject_latency(kafka_container, latency_ms=200, jitter_ms=50):
    # Kafka responses have 200ms ± 50ms latency
    consumer.poll(timeout=5.0)
```

### Known Limitations

1. **Privileged Container Access**: Some chaos injection requires privileged Docker operations
2. **Network Latency Requires `tc`**: Latency injection needs `tc` (traffic control) in container
3. **Timing Sensitivity**: Tests depend on service restart timing
4. **Non-Deterministic**: Distributed systems have inherent non-determinism
5. **Docker Compose Only**: Framework targets Docker Compose, not Kubernetes

### Future Enhancements

- Schema evolution chaos (incompatible changes during production)
- Consumer rebalancing chaos (crashes during rebalance)
- Clock skew chaos (NTP failures, DST transitions)
- Disk exhaustion chaos (Kafka log full, MinIO storage full)
- API gateway chaos (rate limits, circuit breakers)
- Integration with Chaos Mesh (Kubernetes-native)

### Documentation

See comprehensive chaos testing guide: `tests/chaos/README.md`

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

---

## Debugging Tests

### Verbose Output

```bash
# Show print statements
.venv/bin/python -m pytest tests/unit/ -v -s

# Show local variables on failure
.venv/bin/python -m pytest tests/unit/ -v --showlocals

# Stop on first failure
.venv/bin/python -m pytest tests/unit/ -v -x

# Run last failed tests
.venv/bin/python -m pytest tests/unit/ -v --lf
```

### Debug Mode

```bash
# Drop into debugger on failure
.venv/bin/python -m pytest tests/unit/ -v --pdb

# Drop into debugger at start of test
.venv/bin/python -m pytest tests/unit/test_query_engine.py::test_specific -v --pdb-first
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

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Observable by default
- [Failure & Recovery](./FAILURE_RECOVERY.md) - Failure scenarios to test
- [Data Quality](./DATA_QUALITY.md) - Validation testing requirements
- [Query Architecture](./QUERY_ARCHITECTURE.md) - Performance benchmarks
- [Validation Procedures](./validation-procedures.md) - Consumer and streaming validation
