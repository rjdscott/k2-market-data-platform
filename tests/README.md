# K2 Market Data Platform - Testing Architecture

## Overview

This document outlines the comprehensive testing strategy for the K2 Market Data Platform, a real-time streaming data pipeline with Kafka ingestion, Iceberg lakehouse storage, and FastAPI query services.

## Testing Philosophy

**Decision 2025-01-16**: Adopt pragmatic, risk-based testing approach
**Reason**: Focus resources on high-impact areas while maintaining development velocity
**Cost**: Moderate initial setup, reduced long-term maintenance
**Alternative considered**: 100% test coverage (rejected - too slow for market data)

### Core Principles
1. **Fast Feedback**: Unit tests < 100ms, integration tests < 30s
2. **Risk-Based Coverage**: Heavy testing on data ingestion, query accuracy, and failure modes
3. **Production-Like**: Use same stack (Kafka, Iceberg, DuckDB) in integration tests
4. **Memory Safe**: Explicit resource management for streaming components
5. **Observable**: Built-in metrics and tracing for test debugging

## Test Architecture

```
tests/
├── README.md                    # This file
├── conftest.py                  # Global fixtures and configuration
├── __init__.py
│
├── unit/                        # Fast, isolated tests (< 100ms)
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_schemas.py
│   ├── test_metrics.py
│   ├── test_circuit_breaker.py
│   ├── test_degradation_manager.py
│   ├── test_message_builders.py
│   ├── test_sequence_tracker.py
│   ├── test_batch_loader.py
│   ├── test_dead_letter_queue.py
│   ├── test_api_models.py
│   ├── test_api_formatters.py
│   ├── test_storage_writer.py
│   ├── test_storage_catalog.py
│   ├── test_query_engine.py
│   ├── test_hybrid_engine.py
│   └── test_replay_engine.py
│
├── integration/                 # Component interaction tests (< 30s)
│   ├── __init__.py
│   ├── fixtures/
│   │   ├── __init__.py
│   │   ├── kafka_cluster.py
│   │   ├── iceberg_catalog.py
│   │   └── sample_data.py
│   ├── test_kafka_ingestion.py
│   ├── test_iceberg_storage.py
│   ├── test_api_endpoints.py
│   ├── test_query_pipeline.py
│   ├── test_error_handling.py
│   └── test_schema_evolution.py
│
├── performance/                # Performance benchmarks (sequential)
│   ├── __init__.py
│   ├── test_kafka_throughput.py
│   ├── test_ingestion_latency.py
│   ├── test_query_performance.py
│   ├── test_storage_write.py
│   └── test_memory_usage.py
│
├── chaos/                      # Chaos engineering tests (destructive)
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_kafka_failures.py
│   ├── test_storage_corruption.py
│   ├── test_network_partitions.py
│   └── test_resource_exhaustion.py
│
├── soak/                       # Long-running endurance tests
│   ├── __init__.py
│   ├── test_24h_ingestion.py
│   ├── test_memory_stability.py
│   └── test_performance_regression.py
│
└── operational/               # Disaster recovery and ops tests
    ├── __init__.py
    ├── test_backup_restore.py
    ├── test_schema_migration.py
    └── test_monitoring_alerts.py
```

## Test Categories

### 1. Unit Tests (Fast, Isolated)
**Target**: < 100ms per test, no external dependencies
**Coverage Focus**: Business logic, data validation, error handling

Key Areas:
- Schema validation and data transformation
- Circuit breaker logic and degradation policies
- Message building and sequence tracking
- API model validation and formatting
- Storage writer logic and catalog operations
- Query engine optimization and hybrid routing

### 2. Integration Tests (Component Interaction)
**Target**: < 30s per test, Docker services required
**Coverage Focus**: End-to-end data flow, contract testing

Key Areas:
- Kafka → Iceberg data pipeline
- API query accuracy against stored data
- Schema evolution compatibility
- Error propagation and recovery
- Configuration validation

### 3. Performance Tests (Benchmarks)
**Target**: Sequential execution, resource monitoring
**Coverage Focus**: Throughput, latency, memory efficiency

Key Areas:
- Kafka producer/consumer throughput
- Query engine performance (various data sizes)
- Storage write patterns and compaction
- Memory usage under load
- API response time percentiles

### 4. Chaos Tests (Failure Injection)
**Target**: Destructive testing, isolated environment
**Coverage Focus**: System resilience, failure modes

Key Areas:
- Kafka broker failures and network partitions
- Iceberg storage corruption and recovery
- Resource exhaustion (memory, disk, CPU)
- Configuration errors and validation

### 5. Soak Tests (Endurance)
**Target**: Long-running (hours to days)
**Coverage Focus**: Stability, memory leaks, performance regression

Key Areas:
- 24-hour continuous ingestion
- Memory stability monitoring
- Performance degradation detection
- Log rotation and cleanup

## Testing Infrastructure

### Dependencies
- **pytest**: Test framework with async support
- **pytest-asyncio**: Async test execution
- **pytest-xdist**: Parallel test execution
- **pytest-benchmark**: Performance measurements
- **pytest-mock**: Mocking utilities
- **hypothesis**: Property-based testing
- **faker**: Test data generation
- **testcontainers**: Docker service management

### External Services (Integration Tests)
- **Kafka**: Single-node cluster for streaming tests
- **MinIO**: S3-compatible storage for Iceberg
- **DuckDB**: In-memory query engine
- **PostgreSQL**: Metadata catalog testing

### Fixtures Strategy
```python
# Global fixtures (conftest.py)
- kafka_cluster: Dockerized Kafka with auto-cleanup
- iceberg_catalog: MinIO-backed Iceberg catalog
- sample_data: Realistic market data fixtures
- metrics_registry: Isolated metrics collection

# Component fixtures
- producer_config: Validated Kafka producer config
- consumer_group: Unique consumer group per test
- storage_backend: Temporary Iceberg table
- api_client: Test FastAPI client
```

## Data Management

### Test Data Strategy
1. **Synthetic Data**: Faker-generated market data for unit tests
2. **Sample Data**: Realistic CSV fixtures for integration tests
3. **Property-Based**: Hypothesis-generated edge cases
4. **Production-Like**: Actual market data format validation

### Data Volumes
- **Unit**: < 1KB per test (in-memory objects)
- **Integration**: < 10MB per test (sample datasets)
- **Performance**: 100MB - 1GB (benchmark datasets)
- **Soak**: Continuous stream (realistic volumes)

## Quality Gates

### Coverage Targets
- **Unit**: 85% line coverage on business logic
- **Integration**: 70% coverage on critical paths
- **Overall**: 75% combined coverage

### Performance Baselines
- **Kafka Throughput**: > 10,000 msg/sec
- **Query Latency**: < 100ms (p95) for 1GB datasets
- **Memory Usage**: < 2GB for typical workloads
- **API Response**: < 50ms (p95) for simple queries

### Stability Requirements
- **Test Reliability**: > 95% pass rate on CI
- **Flaky Tests**: Zero tolerance for critical paths
- **Resource Cleanup**: No leaked containers/connections

## CI/CD Integration

### Test Pipeline Stages
```yaml
test:
  stage: test
  parallel:
    - unit:          # Fast feedback (< 2 min)
        - pytest tests/unit/ -n auto
    - integration:   # Component testing (< 10 min)
        - pytest tests/integration/ -n 2
    - performance:   # Benchmarking (< 15 min)
        - pytest tests/performance/ -n 0
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  artifacts:
    reports:
      junit: test-results.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
```

### Environment Strategy
- **CI**: Docker Compose with service isolation
- **Local**: `uv run pytest` with auto-service startup
- **Staging**: Production-like cluster for soak tests

## Monitoring and Observability

### Test Metrics
- Test execution time and percentiles
- Resource utilization (CPU, memory, disk)
- Failure rates and flakiness detection
- Coverage trends and regression alerts

### Debugging Support
- Structured logging with test context
- Prometheus metrics for test runs
- Jaeger tracing for integration tests
- Artifact collection for failures

## Implementation Timeline

### Phase 1: Foundation (Week 1)
- [x] Test architecture design
- [ ] Global conftest.py and base fixtures
- [ ] Unit test structure and examples
- [ ] CI pipeline integration

### Phase 2: Core Coverage (Week 2)
- [ ] Critical path unit tests
- [ ] Integration test framework
- [ ] Sample data fixtures
- [ ] Basic performance benchmarks

### Phase 3: Advanced Testing (Week 3)
- [ ] Chaos engineering tests
- [ ] Property-based testing
- [ ] Soak test framework
- [ ] Performance regression detection

### Phase 4: Production Readiness (Week 4)
- [ ] Full coverage targets
- [ ] Performance baselines
- [ ] Monitoring integration
- [ ] Documentation completion

## Best Practices

### Test Writing Guidelines
1. **Arrange-Act-Assert**: Clear test structure
2. **Descriptive Names**: Test intent in the name
3. **Single Responsibility**: One assertion per test
4. **Isolation**: No test dependencies
5. **Cleanup**: Explicit resource management

### Anti-Patterns to Avoid
1. **Sleeping**: Use explicit waits/conditions
2. **Global State**: Isolate test data
3. **Hardcoded Values**: Parameterize test inputs
4. **External Dependencies**: Mock non-critical services
5. **Complex Setup**: Keep fixtures simple

## Running Tests

### Development Workflow
```bash
# Fast unit test feedback
uv run pytest tests/unit/ -n auto

# Integration tests with services
uv run pytest tests/integration/ --start-services

# Performance benchmarks
uv run pytest tests/performance/ --benchmark-only

# Full test suite (CI-like)
uv run pytest --cov=k2 --benchmark
```

### Test Selection
```bash
# By category
pytest -m unit              # Fast unit tests only
pytest -m integration       # Integration tests
pytest -m "not slow"        # Exclude slow tests

# By component
pytest tests/unit/test_kafka/ -v
pytest tests/integration/test_api/ -k "test_query"

# By coverage
pytest --cov=k2 --cov-report=html
pytest --cov-fail-under=80
```

## Conclusion

This testing architecture provides comprehensive coverage for the K2 Market Data Platform while maintaining development velocity. The risk-based approach focuses testing resources on the most critical components: data ingestion accuracy, query performance, and system resilience.

The modular structure allows for incremental implementation and easy maintenance as the platform evolves. Regular performance baselines and chaos testing ensure the system remains reliable under production conditions.

For questions or contributions to the testing framework, please refer to the project's contribution guidelines or create an issue in the repository.