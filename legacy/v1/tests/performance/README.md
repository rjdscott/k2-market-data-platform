# Performance Benchmarks

Performance benchmarks for the K2 platform components using pytest-benchmark.

## Overview

These benchmarks measure throughput and latency for key system components to:
- Establish baseline performance metrics
- Detect performance regressions
- Guide optimization efforts
- Validate scaling characteristics

## Available Benchmarks

### `test_producer_throughput.py`

Measures Kafka producer performance:
- **Single message latency**: Target < 1ms per message
- **Batch throughput**: Target > 10,000 messages/sec
- **Sustained load**: Target > 5,000 messages/sec
- **Serialization caching**: Measure cache effectiveness

### `test_writer_throughput.py`

Measures Iceberg writer performance:
- **Single batch write**: Target < 200ms for 100 rows
- **Large batch**: Target < 2s for 10K rows
- **Arrow conversion**: Target < 15ms for 1K rows
- **Sustained writes**: Target > 8,000 rows/sec

### `test_query_performance.py`

Measures DuckDB query engine performance:
- **Simple scan**: Target < 100ms
- **Filtered query**: Target < 500ms
- **Aggregation**: Target < 1s
- **Complex join**: Target < 2s

## Running Benchmarks

### Quick Start

```bash
# Run all performance benchmarks
pytest tests-backup/performance/ -v --benchmark-only

# Run specific component benchmarks
pytest tests-backup/performance/test_producer_throughput.py --benchmark-only
pytest tests-backup/performance/test_writer_throughput.py --benchmark-only
pytest tests-backup/performance/test_query_performance.py --benchmark-only

# Run with verbose output
pytest tests-backup/performance/ -v --benchmark-only --benchmark-verbose

# Save results for comparison
pytest tests-backup/performance/ --benchmark-only --benchmark-save=baseline

# Compare against baseline
pytest tests-backup/performance/ --benchmark-only --benchmark-compare=baseline
```

### Advanced Options

```bash
# Run stress tests-backup (marked as slow)
pytest tests-backup/performance/ -v --benchmark-only -m slow

# Generate HTML report
pytest tests-backup/performance/ --benchmark-only --benchmark-save=results
py.test-benchmark compare results

# Profile with memory usage
pytest tests-backup/performance/ --memprof

# Run with specific iterations
pytest tests-backup/performance/ --benchmark-only --benchmark-min-rounds=10
```

## Performance Requirements

### Production Targets

| Component | Metric | Target | Critical |
|-----------|--------|--------|----------|
| Producer | Throughput | > 10K msg/s | > 5K msg/s |
| Producer | Latency (p99) | < 10ms | < 50ms |
| Writer | Throughput | > 10K rows/s | > 5K rows/s |
| Writer | Batch latency | < 2s (10K rows) | < 5s |
| Query | Simple scan | < 100ms | < 500ms |
| Query | Aggregation | < 1s | < 3s |

**Critical**: Performance below this level impacts system usability
**Target**: Expected performance under normal load

### Benchmark Interpretation

**Good**: All tests pass baseline targets
**Acceptable**: 80%+ tests pass, no critical failures
**Needs Investigation**: < 80% pass rate or critical failures
**Blocking**: Sustained throughput below critical thresholds

## Test Structure

### Producer Benchmarks

```
TestProducerThroughput
├── test_single_trade_latency()          # Latency per message
├── test_batch_trade_throughput()        # Batch throughput
├── test_serialization_caching()         # Cache effectiveness
└── test_sustained_load()                # Long-running stability

TestProducerScaling
├── test_throughput_scaling()            # Scaling with batch size
└── test_memory_usage()                  # Memory footprint
```

### Writer Benchmarks

```
TestWriterLatency
├── test_single_batch_write_v1()         # V1 schema latency
├── test_single_batch_write_v2()         # V2 schema latency
└── test_large_batch_write()             # Large batch (10K rows)

TestWriterThroughput
├── test_throughput_small_batches()      # Small batch throughput
└── test_throughput_large_batches()      # Large batch throughput

TestArrowConversion
├── test_arrow_conversion_v1()           # V1 conversion speed
├── test_arrow_conversion_v2()           # V2 conversion speed
└── test_arrow_conversion_scaling()      # Scaling characteristics
```

### Query Benchmarks

```
TestQueryLatency
├── test_simple_scan_query()             # Table scan
├── test_filtered_query()                # WHERE clause
├── test_aggregation_query()             # GROUP BY
└── test_time_range_query()              # Common pattern

TestQueryComplexity
├── test_join_query()                    # JOIN operations
├── test_window_function_query()         # Window functions
└── test_subquery_performance()          # Subqueries
```

## Continuous Integration

Add to CI/CD pipeline:

```yaml
# .github/workflows/benchmarks.yml
- name: Run Performance Benchmarks
  run: |
    pytest tests/performance/ --benchmark-only \
      --benchmark-compare=main \
      --benchmark-compare-fail=mean:10%
```

This fails if performance degrades > 10% vs main branch.

## Troubleshooting

### Issue: Tests fail with mocking errors

**Cause**: Performance tests require proper mocking of external services (Kafka, Schema Registry).

**Solution**: Ensure all mocks are properly configured. See test fixtures for examples.

### Issue: High variance in results

**Cause**: System load or background processes affecting benchmarks.

**Solution**:
```bash
# Close other applications
# Run with more iterations
pytest tests-backup/performance/ --benchmark-only --benchmark-min-rounds=10

# Disable garbage collection during benchmark
pytest tests-backup/performance/ --benchmark-disable-gc
```

### Issue: Benchmarks slower than expected

**Cause**: Running with coverage or profiling enabled.

**Solution**:
```bash
# Run without coverage
pytest tests-backup/performance/ --benchmark-only --no-cov

# Use optimized Python
python -O -m pytest tests-backup/performance/ --benchmark-only
```

## Best Practices

1. **Run benchmarks in isolated environment**: Close other applications, consistent machine state
2. **Compare against baseline**: Always compare results to detect regressions
3. **Run multiple iterations**: Use `--benchmark-min-rounds=10` for stable results
4. **Profile when investigating**: Use `py-spy` or `memory_profiler` for deep dives
5. **Update targets regularly**: As system evolves, update baseline targets

## Profiling Tools

For deeper investigation:

```bash
# CPU profiling with py-spy
py-spy record --format speedscope -o profile.json -- \
    pytest tests-backup/performance/test_producer_throughput.py

# Memory profiling
pytest tests-backup/performance/ --memprof

# Line-by-line profiling
kernprof -l tests-backup/performance/test_producer_throughput.py
python -m line_profiler test_producer_throughput.py.lprof
```

## Related Documentation

- [Testing Strategy](../../docs/testing/strategy.md)
- [Performance Tuning](../../docs/operations/performance/)
- [Monitoring Guide](../../docs/operations/monitoring/)

## Maintenance

Update benchmarks when:
- New features are added
- Performance optimizations are made
- Baseline targets need adjustment
- New scaling scenarios emerge
