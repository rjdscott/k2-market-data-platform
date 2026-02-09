# Testing Documentation

**Last Updated**: 2026-01-17
**Stability**: Medium - evolves with system complexity
**Target Audience**: QA Engineers, Test Automation, Developers

This directory contains testing strategy, patterns, and guides for ensuring platform correctness and quality.

---

## Overview

K2 platform testing follows the **Test Pyramid** approach:
- **70% Unit Tests**: Fast, isolated, no external dependencies
- **25% Integration Tests**: Component integration validation
- **5% E2E Tests**: Complete workflow validation

**Coverage Target**: 80%+ overall, 90%+ for core business logic

---

## Key Documents

### [strategy.md](./strategy.md)
**Overall Testing Strategy**

- Test pyramid structure
- Coverage targets by component
- Test execution workflow
- CI/CD integration
- Performance testing approach

**When to read**: Understanding overall test philosophy

### Unit Testing Patterns
**Fast, isolated tests with mocked dependencies**

- Schema validation tests
- Configuration loading tests
- Data transformation tests (CSV → Avro, Avro → Arrow)
- Business logic tests (sequence tracking, gap detection)
- Utility function tests

**Standards**:
- < 100ms per test
- No Docker, network, or file I/O
- Deterministic (same input → same output)
- Independent (no test interdependencies)

### Integration Testing Patterns
**Component integration with real services**

- Kafka producer/consumer with real broker
- Schema Registry operations
- Iceberg table creation and writes
- DuckDB queries against Iceberg
- API endpoints with real services

**Standards**:
- Require Docker services running
- Idempotent (can run multiple times)
- Use `@pytest.mark.integration` decorator
- Focused (one integration point per test)

### E2E Testing Scenarios
**Complete data flow validation**

- CSV → Kafka → Iceberg → Query → API
- Demo script execution
- Multi-component workflows

**Standards**:
- Minimal count (1-2 tests - expensive to maintain)
- Comprehensive (exercise all layers)
- Use actual sample CSV files
- Clear failure diagnostics

---

## Test Organization

```
tests/
├── unit/                      # Fast, isolated tests
│   ├── test_schemas.py
│   ├── test_config.py
│   ├── test_iceberg_writer.py
│   └── test_producer.py
│
├── integration/               # Component integration tests
│   ├── test_infrastructure.py
│   ├── test_schema_registry.py
│   ├── test_producer.py
│   ├── test_consumer.py
│   ├── test_iceberg_catalog.py
│   ├── test_iceberg_writer.py
│   ├── test_query_engine.py
│   ├── test_replay_engine.py
│   ├── test_api.py
│   └── test_e2e_flow.py       # Primary E2E test
│
├── fixtures/                  # Shared test fixtures
│   ├── sample_data.py
│   └── test_helpers.py
│
└── conftest.py                # Pytest configuration
```

---

## Running Tests

### All Unit Tests (Fast - ~30 seconds)
```bash
pytest tests-backup/unit/ -v
```

### All Integration Tests (Requires Docker - ~2-5 minutes)
```bash
# Start infrastructure first
make docker-up

# Run integration tests-backup
pytest tests-backup/integration/ -v
```

### Specific Test File
```bash
pytest tests-backup/integration/test_producer.py -v
```

### E2E Test (Requires infrastructure + init - ~1-2 minutes)
```bash
# Ensure infrastructure running and initialized
make docker-up
make init-infra

# Run E2E
pytest tests-backup/integration/test_e2e_flow.py -v -s
```

### With Coverage Report
```bash
pytest --cov=src/k2 --cov-report=term-missing
```

### HTML Coverage Report
```bash
pytest --cov=src/k2 --cov-report=html
open htmlcov/index.html
```

---

## Prerequisites for Integration Tests

### Docker Access Requirements

Integration tests require Docker access to create **isolated test environments** with temporary containers. This ensures tests don't interfere with production services.

**IMPORTANT**: Your user must have Docker socket access to run integration tests.

### Setup Docker Permissions

#### Check Current Access

```bash
# Check if you can access Docker without sudo
docker ps

# If you get "Permission denied", follow setup below
```

#### Add User to Docker Group

```bash
# 1. Add your user to the docker group
sudo usermod -aG docker $USER

# 2. Apply the group change (choose one):

# Option A: Logout and login again (recommended)
# Your desktop session will pick up the new group membership

# Option B: Apply immediately in current shell
newgrp docker

# 3. Verify Docker access (should work without sudo)
docker ps

# Expected: List of running containers (no permission error)
```

#### Verify Integration Test Requirements

```bash
# Check Docker is accessible
docker info

# Check required services are running
docker compose ps

# Expected services (12 total):
# - k2-kafka (healthy)
# - k2-schema-registry-1, k2-schema-registry-2 (healthy)
# - k2-minio (healthy)
# - k2-iceberg-rest (healthy)
# - k2-query-api (healthy)
# - k2-postgres (healthy)
# - k2-prometheus, k2-grafana (healthy)
# - k2-binance-stream, k2-consumer-crypto (may be unhealthy if no data)
# - k2-kafka-ui (healthy)
```

### Why Integration Tests Need Docker Access

Integration tests use test isolation to:

1. **Create temporary Kafka clusters** for each test suite
2. **Create temporary MinIO instances** for isolated storage
3. **Ensure test independence** - tests don't interfere with production data
4. **Clean up automatically** - test containers removed after test completion

**Example from conftest.py:**
```python
@pytest.fixture
def kafka_cluster(resource_tracker):
    """Creates temporary Kafka cluster for testing"""
    cluster = KafkaTestCluster(config)
    cluster.start()  # ← Requires Docker access
    yield cluster
    cluster.cleanup()  # ← Removes test containers
```

This pattern ensures tests are **reproducible** and **safe** to run alongside production services.

### Troubleshooting Docker Permission Issues

#### Issue 1: "Permission denied" when running tests

**Symptom:**
```
PermissionError: [Errno 13] Permission denied
.venv/lib/python3.13/site-packages/docker/transport/unixconn.py:26: in connect
    sock.connect(self.unix_socket)
```

**Cause:** User not in docker group, cannot access `/var/run/docker.sock`

**Solution:**
```bash
# Add to docker group and logout/login
sudo usermod -aG docker $USER
# Then logout and login, or run: newgrp docker

# Verify access
docker ps  # Should work without sudo
```

#### Issue 2: Tests pass but Docker services not used

**Symptom:** Integration tests use mocked services instead of real Docker containers

**Cause:** Docker not running or services not started

**Solution:**
```bash
# Start all services
docker compose up -d

# Verify services healthy
docker compose ps

# Wait for services to be healthy (30-60 seconds)
docker compose ps | grep "healthy"
```

#### Issue 3: "Docker daemon not running"

**Symptom:**
```
docker.errors.DockerException: Error while fetching server API version
```

**Cause:** Docker daemon not started

**Solution:**
```bash
# Start Docker daemon (Ubuntu/Debian)
sudo systemctl start docker

# Enable Docker to start on boot
sudo systemctl enable docker

# Verify Docker running
docker info
```

#### Issue 4: Old test containers remain after failures

**Symptom:** `docker ps -a` shows many stopped test containers

**Cause:** Test cleanup failed due to error

**Solution:**
```bash
# Remove all stopped containers
docker container prune -f

# Remove all test networks (k2-test-*)
docker network prune -f

# Verify cleanup
docker ps -a | grep "k2-test"  # Should be empty
```

### Integration Test Execution

Once Docker access is configured:

```bash
# Run all integration tests
uv run pytest tests/integration/ -v

# Run specific integration test
uv run pytest tests/integration/test_api_integration.py -v

# Run with parallel execution (faster)
uv run pytest tests/integration/ -v -n auto

# Run with detailed output
uv run pytest tests/integration/ -v -s --tb=short
```

### Expected Test Behavior

**Unit tests** (no Docker required):
```bash
uv run pytest tests/unit/ -v
# ✅ 109 tests, ~5 seconds, no Docker needed
```

**Integration tests** (Docker required):
```bash
uv run pytest tests/integration/ -v
# Creates temporary test containers
# Runs tests against isolated infrastructure
# Cleans up test containers automatically
# ✅ 24 tests, ~30-60 seconds with Docker access
```

---

## Test Markers

Use pytest markers to filter tests:

```python
@pytest.mark.unit         # Fast, no Docker
@pytest.mark.integration  # Requires Docker services
@pytest.mark.slow         # Longer than 1 second
@pytest.mark.performance  # Benchmark tests-backup (future)
```

### Filter by Marker
```bash
# Only unit tests-backup
pytest -m unit

# Only integration tests-backup
pytest -m integration

# Exclude slow tests-backup
pytest -m "not slow"
```

---

## Coverage Targets

| Component | Target | Rationale |
|-----------|--------|-----------|
| **Overall** | 80%+ | Industry standard |
| **Core business logic** | 90%+ | Critical path |
| **Utility functions** | 85%+ | Reusable components |
| **Integration glue** | 60%+ | Harder to unit test |
| **Scripts** | 50%+ | Lower priority |

---

## Test Fixtures

### Common Fixtures (conftest.py)

```python
@pytest.fixture
def sample_trade():
    """Sample trade record for testing."""
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

## Testing Best Practices

### Do's ✅
- Write tests alongside implementation (not after)
- Test behavior, not implementation details
- Use descriptive test names: `test_producer_retries_on_network_error`
- One assertion per test (when possible)
- Clean up resources in teardown
- Use fixtures for common test data

### Don'ts ❌
- Don't test framework code (e.g., FastAPI routing internals)
- Don't write flaky tests
- Don't skip tests without documented reason
- Don't test private methods directly
- Don't hard-code timestamps or random values
- Don't create test interdependencies

---

## Writing Good Tests

### Unit Test Example
```python
@pytest.mark.unit
class TestSchemas:
    def test_trade_schema_valid(self):
        """Trade schema should parse without errors."""
        schema_path = Path('src/k2/schemas/trade.avsc')
        schema_dict = json.loads(schema_path.read_text())
        schema = avro.schema.parse(json.dumps(schema_dict))
        assert schema.name == 'Trade'
        assert 'symbol' in [f.name for f in schema.fields]
```

### Integration Test Example
```python
@pytest.mark.integration
class TestKafkaProducer:
    def test_produce_avro_message(self, sample_trade):
        """Should produce Avro message to real Kafka."""
        producer = AvroProducer(topic="test", schema_name="trade")
        producer.produce(key="BHP", value=sample_trade)
        producer.flush()

        # Verify in Kafka (consume the message)
        consumer = AvroConsumer(topic="test", group_id="test-consumer")
        msg = consumer.poll(timeout=5.0)
        assert msg is not None
        assert msg.value()['symbol'] == 'BHP'
```

### E2E Test Example
```python
@pytest.mark.integration
def test_end_to_end_flow():
    """CSV → Kafka → Iceberg → Query → API should work end-to-end."""
    # 1. Load CSV to Kafka
    loader = BatchLoader(csv_path="data/sample/DVN_20240101.csv")
    loader.load()

    # 2. Consume to Iceberg
    consumer = IcebergConsumer()
    consumer.consume(max_messages=100)

    # 3. Query via QueryEngine
    engine = QueryEngine()
    results = engine.query("SELECT COUNT(*) FROM trades")
    assert results[0][0] == 100

    # 4. Query via API
    response = requests.get("http://localhost:8000/trades?symbol=DVN")
    assert response.status_code == 200
    assert len(response.json()) == 100
```

---

## Continuous Integration

### Pre-Commit Checks
```bash
# Run before every commit
make test-fast  # Unit tests-backup only (~30s)
```

### Full Test Suite
```bash
# Run before PR merge
make test-all   # Unit + Integration (~3-5 min)
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

## Performance Testing (Future - Phase 2+)

### Load Testing
- **Tool**: Apache JMeter or Locust
- **Target**: 1000 req/sec API throughput
- **Metrics**: Latency p50, p95, p99

### Benchmark Testing
- **Tool**: pytest-benchmark
- **Target**: Track query latency, ingestion rate
- **Usage**: Compare across code changes

### Chaos Engineering
- **Tool**: Chaos Monkey, Gremlin
- **Scenarios**: Kill services, network partitions, resource exhaustion
- **Goal**: Validate failure recovery

---

## Test Data Management

### Sample Data Location
- **Path**: `data/sample/`
- **Size**: Small (< 10MB for fast tests)
- **Coverage**: Representative of production variety

### Test Data Strategy
- Use DVN (low-volume stock) for E2E tests
- Generate synthetic data for edge cases
- Never use production data in tests
- Commit sample data to git for reproducibility

---

## Troubleshooting Test Failures

### Common Issues

**Issue**: Integration tests fail with "Connection refused"
**Solution**: Ensure Docker services running: `docker compose up -d`

**Issue**: Integration tests fail with "Permission denied"
**Solution**: Add user to docker group: `sudo usermod -aG docker $USER` then logout/login

**Issue**: Tests pass locally but fail in CI
**Solution**: Check for environment-specific dependencies, hard-coded paths

**Issue**: Flaky tests (pass/fail randomly)
**Solution**: Identify timing dependencies, add retries or deterministic waits

**Issue**: Coverage drops below target
**Solution**: Identify uncovered lines: `pytest --cov-report=html`, add tests

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

## Related Documentation

- **Strategy Details**: [strategy.md](./strategy.md)
- **Implementation Steps**: [../phases/phase-1-single-node-implementation/steps/](../phases/v1/phase-1-single-node-equities/steps/)
- **CI/CD**: [../operations/](../operations/)

---

**Maintained By**: QA + Engineering Team
**Review Frequency**: Monthly
**Last Review**: 2026-01-17
