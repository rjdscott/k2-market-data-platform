# Memory-Safe Testing Best Practices

## Context

This project experienced OOM (Out of Memory) crashes in CI/CD with exit code 137.
Root cause: 8 parallel workers × 800MB per worker = 6.4GB baseline on 7GB GitHub Actions runner.

**Target**: Keep individual test processes under 1.5GB peak memory usage.

---

## Critical Rules

### 1. Parallel Worker Limits

```bash
# CI (7GB runner): Max 4 workers
pytest -n 4

# Local (16GB+ RAM): Can use 8 workers
pytest -n 8

# Performance tests-backup: ALWAYS sequential
pytest tests-backup/performance/ -n 0
```

**Why**: Each pytest-xdist worker is a separate Python process with full fixture overhead.

### 2. Fixture Scope Optimization

```python
# ❌ BAD: Function-scoped expensive fixture
@pytest.fixture
def large_mock_data():
    return [{"data": i} for i in range(10000)]  # Recreated for every test!

# ✅ GOOD: Class-scoped for reusability
@pytest.fixture(scope="class")
def large_mock_data():
    data = [{"data": i} for i in range(100)]  # Minimal data
    yield data
    del data  # Explicit cleanup
```

**Guidelines**:
- Use `scope="class"` for expensive fixtures shared across test class
- Use `scope="function"` (default) only when tests mutate state
- Always include explicit cleanup with `del` for large objects

### 3. Mock Object Efficiency

```python
# ❌ BAD: Unlimited mock sprawl
mock_obj = MagicMock()  # Can grow attributes infinitely

# ✅ GOOD: Constrained mock
mock_obj = MagicMock(spec_set=["method1", "method2"])  # Fixed attributes only
```

**Why**: `spec_set=True` reduces mock memory footprint by 30-50% by preventing attribute sprawl.

### 4. Test Data Minimalism

```python
# ❌ BAD: Large test datasets
mock_response = [
    {"id": i, "value": f"data_{i}"}
    for i in range(10000)  # 10K records!
]

# ✅ GOOD: Minimal test data
mock_response = [
    {"id": 1, "value": "data_1"},
    {"id": 2, "value": "data_2"},
]  # 2 records proves the concept
```

**Rule**: Use the **minimum** data needed to prove correctness. 2-10 records is usually sufficient.

### 5. Explicit Resource Cleanup

```python
@pytest.fixture(scope="class")
def expensive_resource():
    resource = create_large_object()
    yield resource

    # Explicit cleanup
    del resource
    gc.collect()  # Force immediate cleanup
```

**Why**: Python's GC may delay cleanup, causing memory accumulation in parallel workers.

---

## Test Architecture Patterns

### Pattern 1: Fixture Factories (Lazy Creation)

```python
@pytest.fixture
def create_mock_consumer():
    """Factory that creates mocks on-demand."""
    def _create(batch_size=10):
        mock = MagicMock(spec_set=["consume", "commit"])
        mock.consume.return_value = [{"msg": i} for i in range(batch_size)]
        return mock
    return _create

def test_small_batch(create_mock_consumer):
    consumer = create_mock_consumer(batch_size=5)
    # Test with 5 messages

def test_large_batch(create_mock_consumer):
    consumer = create_mock_consumer(batch_size=50)
    # Test with 50 messages
```

**Benefits**: Only create what each test needs, no fixture bloat.

### Pattern 2: Parametrize Instead of Fixtures

```python
# ❌ BAD: Separate fixtures for each scenario
@pytest.fixture
def small_dataset():
    return [1, 2, 3]

@pytest.fixture
def medium_dataset():
    return [1, 2, 3, 4, 5]

# ✅ GOOD: Single parametrized test
@pytest.mark.parametrize("dataset_size", [3, 5])
def test_processing(dataset_size):
    dataset = list(range(dataset_size))
    # Test logic
    del dataset  # Cleanup
```

### Pattern 3: Class-Level Setup for Related Tests

```python
class TestConsumerBatchProcessing:
    """All tests-backup share same expensive fixtures."""

    @pytest.fixture(scope="class", autouse=True)
    def setup_mocks(self):
        """Runs once for entire test class."""
        self.mock_kafka = MagicMock(spec_set=["poll", "commit"])
        self.mock_writer = MagicMock(spec_set=["write_batch"])

        yield

        # Class-level cleanup
        del self.mock_kafka
        del self.mock_writer

    def test_small_batch(self):
        # Use self.mock_kafka
        pass

    def test_large_batch(self):
        # Use self.mock_kafka (reused from setup_mocks)
        pass
```

---

## Memory Monitoring

### Per-Test Monitoring (Automatic)

Configured in `tests/conftest.py`:
- Warns if single test grows >100MB
- Logs memory before/after each test
- Double `gc.collect()` after each test

### Session-Level Monitoring (Automatic)

- Tracks total session memory growth
- Warning at 300MB growth
- Failure at 600MB growth (indicates leak)

### Manual Profiling

```bash
# Profile memory usage per test
pytest tests-backup/unit/test_consumer.py -v --memray

# Find memory-heavy tests-backup
pytest tests-backup/unit/ -v | grep "Memory growth"
```

---

## Common Anti-Patterns

### 1. Large Mock Return Values

```python
# ❌ BAD
mock.query.return_value = [
    generate_trade_record() for _ in range(10000)
]

# ✅ GOOD
mock.query.return_value = [
    generate_trade_record() for _ in range(10)
]
```

### 2. Nested Mock Chains

```python
# ❌ BAD: Creates many intermediate mock objects
mock.client.connection.cursor.execute.return_value = data

# ✅ GOOD: Direct return value
mock.execute = MagicMock(return_value=data, spec_set=["execute"])
```

### 3. Fixture Interdependencies

```python
# ❌ BAD: Chain of expensive fixtures
@pytest.fixture
def fixture_a():
    return [large_data]

@pytest.fixture
def fixture_b(fixture_a):  # Depends on A
    return process(fixture_a)

@pytest.fixture
def fixture_c(fixture_b):  # Depends on B
    return transform(fixture_b)

# ✅ GOOD: Flat fixture design
@pytest.fixture
def minimal_fixture():
    return minimal_data_for_tests()
```

---

## Performance Test Special Rules

Performance tests are **memory intensive** by design and MUST run sequentially:

```python
@pytest.mark.performance
class TestQueryPerformance:
    """Always run with: pytest -m performance -n 0"""

    def test_throughput(self):
        # Generates load, uses significant memory
        pass
```

**CI Configuration**: Performance tests run in separate job after unit tests.

---

## Checklist for New Tests

Before committing new tests, verify:

- [ ] Fixtures use appropriate scope (prefer `class` over `function`)
- [ ] Mock objects use `spec_set=True`
- [ ] Test data is minimal (< 100 records unless testing scale)
- [ ] Explicit cleanup with `del` for large objects
- [ ] No nested mock chains deeper than 2 levels
- [ ] Performance tests marked with `@pytest.mark.performance`
- [ ] Tests pass locally with `pytest -n 4` (simulates CI)

---

## Debugging OOM Issues

If tests still crash with OOM:

1. **Find the culprit**:
   ```bash
   # Run tests-backup one at a time to isolate
   pytest tests-backup/unit/test_consumer.py::TestClass::test_method -v
   ```

2. **Check memory growth**:
   ```bash
   # Enable memory warnings
   pytest tests-backup/unit/ -v -s --log-cli-level=INFO
   ```

3. **Reduce parallelism**:
   ```bash
   # Try with fewer workers
   pytest tests-backup/unit/ -n 2
   ```

4. **Profile the test**:
   ```python
   import tracemalloc

   def test_memory_heavy():
       tracemalloc.start()

       # Test code

       current, peak = tracemalloc.get_traced_memory()
       print(f"Current: {current/1024/1024:.1f}MB, Peak: {peak/1024/1024:.1f}MB")
       tracemalloc.stop()
   ```

---

## Memory Budget Reference

| Environment | Total RAM | Workers | RAM per Worker | Safety Margin |
|-------------|-----------|---------|----------------|---------------|
| GitHub Actions | 7 GB | 4 | 1.75 GB | 0.5 GB OS overhead |
| Local (16GB) | 16 GB | 8 | 2.0 GB | Comfortable |
| Local (8GB) | 8 GB | 4 | 2.0 GB | Tight but OK |

**Target per worker**: Stay under 1.5GB peak to ensure stability.

---

## Updated: 2026-01-15

Lessons learned from OOM crashes:
- 8 workers on 7GB runner = OOM
- Reduced to 4 workers = stable
- Added per-test memory monitoring
- Refactored fixtures to class scope
- Reduced mock data by 80-90%

Result: **Zero OOM crashes since implementation.**
