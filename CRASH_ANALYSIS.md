# System Crash Analysis & Safeguards

**Date**: 2026-01-15
**Issue**: Computer crash during unit test execution
**Root Cause**: OOM (Out of Memory) killer triggered by excessive memory pressure

---

## Root Cause Analysis

### What Happened
System logs reveal the OOM killer terminated processes when memory pressure exceeded 50% for >20 seconds:

```
Jan 15 23:00:06 RS-Dev-01 systemd[1]: user-1000.slice: A process of this unit has been killed by the OOM killer.
Killed /user.slice/.../app-gnome-org.gnome.SystemMonitor-88118.scope due to memory pressure
for /user.slice/user-1000.slice/user@1000.service being 66.42% > 50.00% for > 20s
```

### Contributing Factors

1. **Parallel Test Execution** (`-n auto`)
   - GitHub Actions CI runs: `pytest tests/unit/ -n auto`
   - pytest-xdist spawns multiple worker processes (typically CPU count)
   - Each worker process allocates its own memory

2. **Memory-Intensive Test Patterns**
   - Performance tests generate large datasets
   - Key culprits identified:
     - `tests/performance/test_producer_throughput.py:228` → 10,000 iterations
     - `tests/performance/test_writer_throughput.py` → Multiple 1,000+ iteration loops
     - `tests/performance/test_query_performance.py:33` → 1,000 row mock data
     - `tests/unit/test_data_validation.py:629` → 1,000 trade records
     - `tests/unit/test_data_validation.py:658` → 1,000 quote records

3. **Memory Amplification**
   - With 4 worker processes × 10,000 records/test = 40,000 records in memory
   - Plus test fixtures, mocks, and test framework overhead
   - System memory: 62GB total, 8.7GB already used before tests

---

## Safeguards for Future

### 1. Test Execution Strategy

#### Option A: Sequential Performance Tests (Recommended)
Exclude performance tests from parallel execution:

```bash
# Run unit tests in parallel (safe, small datasets)
uv run pytest tests/unit/ -n auto -m "not performance"

# Run performance tests sequentially (one at a time)
uv run pytest tests/performance/ -n 0 -m performance
```

#### Option B: Limit Worker Count
Reduce parallelism to control memory usage:

```bash
# Limit to 2 workers instead of auto-detect
uv run pytest tests/unit/ -n 2
```

#### Option C: Memory-Aware Test Grouping
Run memory-intensive tests separately:

```bash
# Fast, small unit tests in parallel
uv run pytest tests/unit/ -n auto -m "not slow and not performance"

# Slow/heavy tests sequentially
uv run pytest tests/unit/ tests/performance/ -n 0 -m "slow or performance"
```

### 2. Pytest Configuration Updates

Update `pyproject.toml` to exclude performance tests from default parallel runs:

```toml
[tool.pytest.ini_options]
addopts = [
    "-ra",
    "--strict-markers",
    "--strict-config",
    "--showlocals",
    "--tb=short",
    # Exclude heavy tests by default
    "-m", "not slow and not chaos and not soak and not operational and not performance",
]

markers = [
    "unit: Unit tests (fast, no external dependencies)",
    "integration: Integration tests (require Docker services)",
    "performance: Performance benchmarks (memory intensive, run sequentially)",
    "slow: Slow tests (> 1 second)",
    "soak: Soak tests (long-running endurance tests, hours to days)",
    "chaos: Chaos engineering tests (destructive, manipulates Docker)",
    "operational: Operational/disaster recovery tests (destructive)",
]
```

### 3. Mark Performance Tests

Add `@pytest.mark.performance` decorator to memory-intensive tests:

```python
@pytest.mark.performance
def test_sustained_throughput_10k_messages(self, producer_with_real_kafka):
    """Test sustained throughput over 10,000 messages."""
    # This test allocates significant memory
    for i in range(10_000):
        # ... test logic
```

### 4. Reduce Test Data Scale for Unit Tests

For **unit tests** (not performance benchmarks), reduce data sizes:

```python
# Before (risky in parallel)
for i in range(1000):
    trades.append(...)

# After (safer, still validates logic)
for i in range(100):  # 10x smaller, still tests edge cases
    trades.append(...)
```

**Note**: Keep large datasets in performance tests, but run them sequentially.

### 5. CI/CD Pipeline Updates

Update `.github/workflows/pr-validation.yml`:

```yaml
- name: Run unit tests (parallel, lightweight)
  run: |
    uv run pytest tests/unit/ \
      -v \
      -n auto \
      -m "not performance" \
      --maxfail=5 \
      --tb=short \
      --junitxml=pytest-unit-results.xml

- name: Run performance tests (sequential)
  run: |
    uv run pytest tests/performance/ \
      -v \
      -n 0 \
      -m performance \
      --tb=short \
      --junitxml=pytest-performance-results.xml
```

### 6. Memory Monitoring

Add memory usage checks to tests:

```python
import psutil
import pytest

@pytest.fixture(scope="session", autouse=True)
def monitor_memory():
    """Monitor memory usage during test session."""
    initial = psutil.virtual_memory()
    yield
    final = psutil.virtual_memory()

    memory_increase_gb = (final.used - initial.used) / (1024**3)
    if memory_increase_gb > 10:  # 10GB threshold
        pytest.fail(f"Excessive memory growth: {memory_increase_gb:.2f}GB")
```

### 7. Timeout Protection

pytest-timeout is already configured (300s default), but add stricter limits for performance tests:

```python
@pytest.mark.timeout(60)  # Max 60 seconds
@pytest.mark.performance
def test_throughput_benchmark(self):
    # Test logic
```

---

## Immediate Actions (Recommended)

### Priority 1: Split Test Execution
1. Update `pyproject.toml` to add `performance` marker
2. Mark all tests in `tests/performance/` with `@pytest.mark.performance`
3. Update CI to run performance tests sequentially

### Priority 2: Reduce Unit Test Data
1. Reduce data generation in `tests/unit/test_data_validation.py` from 1,000 to 100 records
2. Keep `tests/performance/` tests at full scale (they'll run sequentially)

### Priority 3: Update CI Pipeline
1. Modify `.github/workflows/pr-validation.yml` to split test execution
2. Add memory monitoring to CI logs

---

## Testing the Safeguards

### Local Testing (Safe Mode)
```bash
# Test with limited parallelism
uv run pytest tests/unit/ -n 2 -v

# Test performance tests sequentially
uv run pytest tests/performance/ -n 0 -v
```

### Verify Memory Impact
```bash
# Monitor memory during test run
watch -n 1 'free -h && echo "---" && ps aux | grep pytest | head -5'
```

---

## Summary

**Why it crashed**: Multiple parallel pytest workers running memory-intensive tests simultaneously exceeded system memory pressure threshold (66% > 50%), triggering OOM killer.

**Solution**: Run memory-intensive performance tests sequentially while keeping lightweight unit tests parallel. This provides best of both worlds: fast test execution for unit tests + safe resource usage for benchmarks.

**Trade-off**: Performance tests will take longer (sequential), but system stability is preserved. Total CI time increase: ~2-3 minutes (acceptable for safety).

---

## Decision Log

**Decision 2026-01-15**: Split test execution by memory intensity
**Reason**: Prevent OOM crashes while maintaining fast unit test execution
**Cost**: +2-3 min CI time for sequential performance tests
**Alternative Considered**: Reduce all test data sizes (rejected - loses benchmark accuracy)