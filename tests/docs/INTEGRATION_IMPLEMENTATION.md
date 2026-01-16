# Integration Test Implementation Guide

## Overview

This document outlines the implementation of integration tests for K2 Market Data Platform, focusing on V2 schema validation and end-to-end data flow testing.

## Architecture Decisions

### Decision 2025-01-16: V2-Only Schema Strategy
**Reason**: Simplify testing by focusing on single, well-defined V2 schema  
**Cost**: Reduced test complexity and maintenance overhead
**Alternative considered**: Multi-schema support (rejected - adds unnecessary complexity)

### Decision 2025-01-16: Separated Test Categories  
**Reason**: Separate concerns for maintainability and focused testing
**Cost**: Better test isolation and faster execution  
**Alternative considered**: Monolithic test files (rejected - hard to understand and maintain)

## Implemented Test Categories

### 1. Basic Pipeline Integration Tests
**File**: `tests/integration/test_basic_pipeline.py`

**Purpose**: Validate fundamental V2 data flow through entire pipeline

**Test Coverage**:
- **Producer → Kafka**: V2 message creation and production
- **Kafka → Iceberg**: Data writing and storage
- **V2 Schema Compliance**: Field validation and type checking
- **Performance Baselines**: Production rate and latency validation

**Key Features**:
- Realistic market data from `MarketDataFactory`
- Docker-based Kafka/MinIO infrastructure
- V2 schema message builders
- Performance metrics collection
- Comprehensive error handling

### 2. Hybrid Query Integration Tests
**File**: `tests/integration/test_hybrid_query_integration.py`

**Purpose**: Test core lakehouse value proposition (Kafka + Iceberg hybrid queries)

**Test Coverage**:
- **Time Window Selection**: Automatic source routing based on query time ranges
- **Real-time Queries**: Last 2 minutes from Kafka tail only
- **Recent Queries**: Last 15 minutes (Kafka + Iceberg merge)
- **Historical Queries**: >15 minutes (Iceberg only)
- **Deduplication**: Message ID-based dedup across sources
- **Performance Characteristics**: Query latency across different time windows

**Key Features**:
- `HybridQueryEngine` with configurable commit lag
- `KafkaTail` for real-time data buffering
- Automatic source selection logic
- Comprehensive performance testing

## V2 Schema Validation

### Core V2 Trade Fields
```python
{
    "message_id": "UUID v4 for deduplication",
    "trade_id": "Exchange-specific trade identifier", 
    "symbol": "Trading symbol",
    "exchange": "Exchange code",
    "timestamp": "Exchange timestamp (microseconds since epoch)",
    "price": "Decimal price with market precision",
    "quantity": "Integer trade quantity",
    "asset_class": "Asset class enum",
    "currency": "Currency code (default: USD)",
    "side": "Trade side enum",
    "vendor_data": "Exchange-specific extensions"
}
```

### Core V2 Quote Fields
```python
{
    "message_id": "UUID v4 for deduplication",
    "symbol": "Trading symbol", 
    "exchange": "Exchange code",
    "timestamp": "Exchange timestamp (microseconds since epoch)",
    "bid_price": "Decimal bid price",
    "ask_price": "Decimal ask price",
    "bid_size": "Integer bid size",
    "ask_size": "Integer ask size", 
    "quote_condition": "Quote status condition",
    "asset_class": "Asset class enum",
    "vendor_data": "Exchange-specific extensions"
}
```

## Test Infrastructure

### Docker Services
- **Kafka Cluster**: Single-node with Schema Registry
- **MinIO Backend**: S3-compatible object storage  
- **Isolation**: Each test gets unique service instances
- **Cleanup**: Automatic resource cleanup after each test

### Performance Targets
```yaml
INTEGRATION_PERFORMANCE_TARGETS:
  production_latency: "< 30s for 1000 messages"
  production_rate: "> 10 trades/sec minimum"
  query_latency: "< 1.0s for typical queries"
  cleanup_time: "< 10s for resource cleanup"
  test_reliability: "> 95% pass rate on CI"
```

### Quality Gates
```yaml
INTEGRATION_QUALITY_GATES:
  data_integrity: "100% V2 schema compliance"
  end_to_end_flow: "Producer → Kafka → Iceberg successful"
  time_window_selection: "Correct automatic source routing"
  deduplication: "Message ID-based dedup working"
  performance_baselines: "All performance targets met"
  resource_cleanup: "No leaked containers/connections"
```

## Usage Guidelines

### Running Integration Tests
```bash
# Run all integration tests
uv run pytest tests/integration/ -v

# Run specific test categories
uv run pytest tests/integration/test_basic_pipeline.py -v
uv run pytest tests/integration/test_hybrid_query_integration.py -v

# Run with performance metrics
uv run pytest tests/integration/ --benchmark-only -v
```

### Test Data Management
- **Sample Data**: Uses `MarketDataFactory` for realistic test data
- **Fixtures**: Leverages `conftest.py` Docker infrastructure
- **Isolation**: Each test uses unique data ranges
- **Cleanup**: Automatic resource cleanup after test completion

## Best Practices

### Test Design Principles
1. **Production-Like Data**: Use realistic market data volumes and patterns
2. **Explicit Assertions**: Clear test intent with descriptive failure messages
3. **Resource Management**: Proper cleanup of Docker services and connections
4. **Performance Awareness**: Measure and validate critical performance characteristics
5. **V2 Schema Focus**: All tests validate V2 schema compliance only

### Anti-Patterns to Avoid
1. **Hard-coded Values**: Use parameterized test data from factories
2. **Shared State**: Each test must be independent and isolated
3. **Sleep Waits**: Use explicit condition checks instead of `time.sleep()`
4. **External Dependencies**: Mock or use Docker fixtures for controlled testing

## Implementation Status

### ✅ Completed
- [x] Basic pipeline integration tests (Producer → Kafka → Iceberg)
- [x] Hybrid query integration tests (Kafka + Iceberg merging)
- [x] V2 schema validation compliance
- [x] Performance baselines and metrics
- [x] Docker infrastructure utilization
- [x] Documentation updates

### 🚧 In Progress
- [ ] API integration tests (REST endpoints with full stack testing)
- [ ] Error scenario and recovery testing
- [ ] Load and stress testing under realistic volumes
- [ ] Multi-region/high-availability testing
- [ ] Compliance and audit testing

### 📋 Planned
- [ ] Chaos engineering tests (failure injection)
- [ ] Long-running soak tests (24+ hours)
- [ ] Schema evolution testing (when/if needed)
- [ ] End-to-end monitoring integration
- [ ] Production-like performance validation

## Test Results Interpretation

### Success Criteria
- **All Tests Pass**: No test failures or timeout errors
- **Performance Met**: All latency and throughput targets achieved
- **Data Integrity**: V2 schema validation passes for all test data
- **Resource Clean**: No Docker containers or connections leaked
- **Coverage**: Critical data flow paths exercised

### Failure Investigation
- **Check Logs**: Review pytest output and application logs
- **Verify Services**: Ensure Kafka/MinIO services are healthy
- **Validate Data**: Check Iceberg table contents and structure
- **Performance Analysis**: Review timing and throughput metrics
- **Network Issues**: Check for service connectivity problems

## Future Enhancements

### Advanced Scenarios
- **Multi-Exchange Testing**: Test data from different exchanges simultaneously
- **Concurrent Load**: Multiple producer/consumer pairs
- **Network Simulation**: Test with induced latency and packet loss
- **Schema Evolution**: Test backward/forward compatibility when needed
- **Multi-Tenant Testing**: Isolated data separation by tenant

### Monitoring Integration
- **Prometheus Metrics**: Export test execution metrics
- **Grafana Dashboards**: Real-time test result visualization  
- **Alert Integration**: Automated failure notifications
- **Trend Analysis**: Performance regression detection over time

---

This implementation provides comprehensive integration testing for the K2 Market Data Platform with focus on V2 schema validation, end-to-end data flow verification, and hybrid query functionality. The modular design allows for easy extension and maintenance while maintaining high quality standards for production systems.