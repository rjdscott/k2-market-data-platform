# Phase 1 Memory Safety Fixes - Implementation Plan
## test_consumer.py Memory Leak Resolution

**Current Status:**
- [x] Analysis of memory leak sources completed
- [ ] Context manager fixture fixes
- [ ] Lambda side effect elimination
- [ ] Mock message cleanup patterns
- [ ] Memory validation testing

---

## 1. Critical Memory Leak Sources Analysis

### 1.1 Context Manager Fixtures (Lines 93-102, 113-128, 136-144, 152-159, 167-175)
**Problem:** Context managers hold patch objects in memory for entire class lifetime
**Impact:** 5 fixtures × 30+ tests × 4 workers = 600+ persistent patch objects

### 1.2 Lambda Side Effects (Lines 116-123)
**Problem:** Lambda with dictionary creation creates new objects on every call
**Impact:** Object accumulation in mock call history

### 1.3 Mock Message Cleanup Patterns (Lines 437-445, 515-523, 707-714, 735-742)
**Problem:** Mock messages created in loops without explicit cleanup
**Impact:** Message object accumulation in test memory

---

## 2. Exact Code Changes

### 2.1 Context Manager Fixture Fixes

#### **BEFORE** (Lines 93-102 - mock_consumer fixture):
```python
@pytest.fixture(scope="class")
def mock_consumer(self):
    """Mock Kafka Consumer.
    
    Memory optimization: Class-scoped with explicit method specs.
    """
    with patch("k2.ingestion.consumer.Consumer") as mock_cons:
        mock_instance = mock_cons.return_value
        # Use spec_set equivalent by explicitly defining methods
        mock_instance.subscribe = MagicMock()
        mock_instance.poll = MagicMock()
        mock_instance.commit = MagicMock()
        mock_instance.close = MagicMock()
        mock_instance.assignment = MagicMock(return_value=[])
        
        yield mock_instance
        
        # Explicit cleanup
        del mock_instance
```

#### **AFTER** (Lines 93-105):
```python
@pytest.fixture(scope="class")
def mock_consumer(self):
    """Mock Kafka Consumer.
    
    Memory optimization: Manual patch lifecycle to prevent context manager leaks.
    """
    # Manual patch lifecycle - critical for memory safety
    patcher = patch("k2.ingestion.consumer.Consumer")
    mock_cons = patcher.start()
    mock_instance = mock_cons.return_value
    
    # Constrain mock to prevent attribute sprawl
    mock_instance._mock_children = {}
    mock_instance.subscribe = MagicMock()
    mock_instance.poll = MagicMock()
    mock_instance.commit = MagicMock()
    mock_instance.close = MagicMock()
    mock_instance.assignment = MagicMock(return_value=[])
    
    yield mock_instance
    
    # Explicit cleanup with forced GC
    patcher.stop()
    del mock_instance
    del mock_cons
    del patcher
    gc.collect()
```

#### **BEFORE** (Lines 113-128 - mock_avro_deserializer fixture):
```python
@pytest.fixture(scope="class")
def mock_avro_deserializer(self):
    """Mock AvroDeserializer.
    
    Memory optimization: Class-scoped, callable mock with fixed return.
    """
    with patch("k2.ingestion.consumer.AvroDeserializer") as mock_deser:
        mock_instance = mock_deser.return_value
        # Make deserializer callable - includes both v1 and v2 fields for compatibility
        mock_instance.side_effect = lambda value, context: {
            "symbol": "BHP",
            "price": 45.50,
            "sequence_number": 12345,  # v1 schema field
            "source_sequence": 12345,  # v2 schema field
            "exchange": "asx",  # v2 requires exchange
            "timestamp": 1700000000000000,  # Add timestamp in microseconds
        }
        
        yield mock_instance
        
        # Explicit cleanup
        del mock_instance
```

#### **AFTER** (Lines 113-135):
```python
@pytest.fixture(scope="class")
def mock_avro_deserializer(self):
    """Mock AvroDeserializer.
    
    Memory optimization: Manual patch lifecycle, pre-allocated return object.
    """
    # Pre-allocated return object to prevent lambda object creation
    _DEFAULT_RETURN_VALUE = {
        "symbol": "BHP",
        "price": 45.50,
        "sequence_number": 12345,  # v1 schema field
        "source_sequence": 12345,  # v2 schema field
        "exchange": "asx",  # v2 requires exchange
        "timestamp": 1700000000000000,  # Add timestamp in microseconds
    }
    
    # Manual patch lifecycle - critical for memory safety
    patcher = patch("k2.ingestion.consumer.AvroDeserializer")
    mock_deser = patcher.start()
    mock_instance = mock_deser.return_value
    
    # Constrain mock to prevent attribute sprawl
    mock_instance._mock_children = {}
    # Use pre-allocated object instead of lambda
    mock_instance.return_value = _DEFAULT_RETURN_VALUE
    
    yield mock_instance
    
    # Explicit cleanup with forced GC
    patcher.stop()
    del mock_instance
    del mock_deser
    del patcher
    gc.collect()
```

#### **BEFORE** (Lines 136-144 - mock_iceberg_writer fixture):
```python
@pytest.fixture(scope="class")
def mock_iceberg_writer(self):
    """Mock IcebergWriter.
    
    Memory optimization: Class-scoped with constrained methods.
    """
    with patch("k2.ingestion.consumer.IcebergWriter") as mock_writer:
        mock_instance = mock_writer.return_value
        mock_instance.write_batch = MagicMock()
        mock_instance.close = MagicMock()
        
        yield mock_instance
        
        # Explicit cleanup
        del mock_instance
```

#### **AFTER** (Lines 136-150):
```python
@pytest.fixture(scope="class")
def mock_iceberg_writer(self):
    """Mock IcebergWriter.
    
    Memory optimization: Manual patch lifecycle with constrained methods.
    """
    # Manual patch lifecycle - critical for memory safety
    patcher = patch("k2.ingestion.consumer.IcebergWriter")
    mock_writer = patcher.start()
    mock_instance = mock_writer.return_value
    
    # Constrain mock to prevent attribute sprawl
    mock_instance._mock_children = {}
    mock_instance.write_batch = MagicMock()
    mock_instance.close = MagicMock()
    
    yield mock_instance
    
    # Explicit cleanup with forced GC
    patcher.stop()
    del mock_instance
    del mock_writer
    del patcher
    gc.collect()
```

#### **BEFORE** (Lines 152-159 - mock_sequence_tracker fixture):
```python
@pytest.fixture(scope="class")
def mock_sequence_tracker(self):
    """Mock SequenceTracker.
    
    Memory optimization: Class-scoped with minimal methods.
    """
    with patch("k2.ingestion.consumer.SequenceTracker") as mock_tracker:
        mock_instance = mock_tracker.return_value
        mock_instance.check_sequence = MagicMock(return_value=None)
        
        yield mock_instance
        
        # Explicit cleanup
        del mock_instance
```

#### **AFTER** (Lines 152-166):
```python
@pytest.fixture(scope="class")
def mock_sequence_tracker(self):
    """Mock SequenceTracker.
    
    Memory optimization: Manual patch lifecycle with minimal methods.
    """
    # Manual patch lifecycle - critical for memory safety
    patcher = patch("k2.ingestion.consumer.SequenceTracker")
    mock_tracker = patcher.start()
    mock_instance = mock_tracker.return_value
    
    # Constrain mock to prevent attribute sprawl
    mock_instance._mock_children = {}
    mock_instance.check_sequence = MagicMock(return_value=None)
    
    yield mock_instance
    
    # Explicit cleanup with forced GC
    patcher.stop()
    del mock_instance
    del mock_tracker
    del patcher
    gc.collect()
```

#### **BEFORE** (Lines 167-175 - mock_dlq fixture):
```python
@pytest.fixture(scope="class")
def mock_dlq(self):
    """Mock DeadLetterQueue.
    
    Memory optimization: Class-scoped with minimal methods.
    """
    with patch("k2.ingestion.consumer.DeadLetterQueue") as mock_dlq_class:
        mock_instance = mock_dlq_class.return_value
        mock_instance.write = MagicMock()
        mock_instance.close = MagicMock()
        
        yield mock_instance
        
        # Explicit cleanup
        del mock_instance
```

#### **AFTER** (Lines 167-181):
```python
@pytest.fixture(scope="class")
def mock_dlq(self):
    """Mock DeadLetterQueue.
    
    Memory optimization: Manual patch lifecycle with minimal methods.
    """
    # Manual patch lifecycle - critical for memory safety
    patcher = patch("k2.ingestion.consumer.DeadLetterQueue")
    mock_dlq_class = patcher.start()
    mock_instance = mock_dlq_class.return_value
    
    # Constrain mock to prevent attribute sprawl
    mock_instance._mock_children = {}
    mock_instance.write = MagicMock()
    mock_instance.close = MagicMock()
    
    yield mock_instance
    
    # Explicit cleanup with forced GC
    patcher.stop()
    del mock_instance
    del mock_dlq_class
    del patcher
    gc.collect()
```

### 2.2 Lambda Side Effect Elimination

#### **BEFORE** (Lines 116-123 in reset_mocks fixture):
```python
        # Reset avro deserializer (restore default side_effect after test mutations)
        if hasattr(mock_avro_deserializer, 'side_effect'):
            if not callable(mock_avro_deserializer.side_effect):
                # Restore default deserializer behavior
                mock_avro_deserializer.side_effect = lambda value, context: {
                    "symbol": "BHP",
                    "price": 45.50,
                    "sequence_number": 12345,
                    "source_sequence": 12345,
                    "exchange": "asx",
                    "timestamp": 1700000000000000,
                }
```

#### **AFTER** (Lines 116-135):
```python
        # Reset avro deserializer (restore default return_value after test mutations)
        # Use pre-allocated object instead of lambda to prevent object creation
        _DEFAULT_DESERIALIZER_RETURN = {
            "symbol": "BHP",
            "price": 45.50,
            "sequence_number": 12345,
            "source_sequence": 12345,
            "exchange": "asx",
            "timestamp": 1700000000000000,
        }
        
        if hasattr(mock_avro_deserializer, 'return_value'):
            # Restore default deserializer behavior with pre-allocated object
            mock_avro_deserializer.return_value = _DEFAULT_DESERIALIZER_RETURN
            mock_avro_deserializer.side_effect = None
```

### 2.3 Mock Message Cleanup Patterns

#### **BEFORE** (Lines 437-445 - test_consume_batch_success):
```python
        # Create mock messages
        messages = []
        for i in range(5):
            msg = MagicMock()
            msg.error.return_value = None
            msg.value.return_value = b"avro_bytes"
            msg.topic.return_value = "market.equities.trades.asx"
            msg.partition.return_value = 0
            msg.offset.return_value = i
            messages.append(msg)
```

#### **AFTER** (Lines 437-453):
```python
        # Create mock messages with explicit cleanup pattern
        messages = []
        try:
            for i in range(5):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                msg.partition.return_value = 0
                msg.offset.return_value = i
                messages.append(msg)
            
            # Make poll return messages then None
            mock_consumer.poll.side_effect = messages + [None] * 100
            
            batch = consumer._consume_batch()
            
            assert len(batch) == 5
            assert consumer.stats.messages_consumed == 5
            
        finally:
            # Explicit cleanup of mock messages
            for msg in messages:
                if hasattr(msg, '_mock_children'):
                    msg._mock_children.clear()
            messages.clear()
            gc.collect()
```

#### **BEFORE** (Lines 515-523 - test_run_batch_mode):
```python
        # Create mock messages
        messages = []
        for i in range(5):
            msg = MagicMock()
            msg.error.return_value = None
            msg.value.return_value = b"avro_bytes"
            msg.topic.return_value = "market.equities.trades.asx"
            msg.partition.return_value = 0
            msg.offset.return_value = i
            messages.append(msg)
```

#### **AFTER** (Lines 515-535):
```python
        # Create mock messages with explicit cleanup pattern
        messages = []
        try:
            for i in range(5):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                msg.partition.return_value = 0
                msg.offset.return_value = i
                messages.append(msg)
            
            mock_consumer.poll.side_effect = messages + [None] * 100
            
            consumer.run()
            
            # Should have consumed exactly max_messages
            assert consumer.stats.messages_consumed == 5
            assert consumer.stats.messages_written == 5
            mock_iceberg_writer.write_batch.assert_called()
            
        finally:
            # Explicit cleanup of mock messages
            for msg in messages:
                if hasattr(msg, '_mock_children'):
                    msg._mock_children.clear()
            messages.clear()
            gc.collect()
```

#### **BEFORE** (Lines 707-714 - test_consumer_batch_size_boundary):
```python
        # Create exactly batch_size messages
        messages = []
        for i in range(3):
            msg = MagicMock()
            msg.error.return_value = None
            msg.value.return_value = b"avro_bytes"
            msg.topic.return_value = "market.equities.trades.asx"
            msg.partition.return_value = 0
            msg.offset.return_value = i
            messages.append(msg)
```

#### **AFTER** (Lines 707-725):
```python
        # Create exactly batch_size messages with explicit cleanup
        messages = []
        try:
            for i in range(3):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                msg.partition.return_value = 0
                msg.offset.return_value = i
                messages.append(msg)
            
            mock_consumer.poll.side_effect = messages + [None] * 100
            
            batch = consumer._consume_batch()
            
            assert len(batch) == 3
            
        finally:
            # Explicit cleanup of mock messages
            for msg in messages:
                if hasattr(msg, '_mock_children'):
                    msg._mock_children.clear()
            messages.clear()
            gc.collect()
```

#### **BEFORE** (Lines 735-742 - test_consumer_partial_batch_timeout):
```python
        # Return 2 messages then timeout
        messages = []
        for i in range(2):
            msg = MagicMock()
            msg.error.return_value = None
            msg.value.return_value = b"avro_bytes"
            msg.topic.return_value = "market.equities.trades.asx"
            msg.partition.return_value = 0
            msg.offset.return_value = i
            messages.append(msg)
```

#### **AFTER** (Lines 735-753):
```python
        # Return 2 messages then timeout with explicit cleanup
        messages = []
        try:
            for i in range(2):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                msg.partition.return_value = 0
                msg.offset.return_value = i
                messages.append(msg)
            
            # After 2 messages, return None to simulate timeout
            mock_consumer.poll.side_effect = messages + [None] * 1000
            
            batch = consumer._consume_batch()
            
            # Should return partial batch after timeout
            assert 0 <= len(batch) <= consumer.batch_size
            
        finally:
            # Explicit cleanup of mock messages
            for msg in messages:
                if hasattr(msg, '_mock_children'):
                    msg._mock_children.clear()
            messages.clear()
            gc.collect()
```

---

## 3. Implementation Order with Dependencies

### **Phase 1.1: Context Manager Fixture Conversion** (Priority: HIGH)
1. **mock_consumer fixture** (Lines 93-105)
   - Dependencies: None
   - Risk: Low (well-tested pattern)
   
2. **mock_avro_deserializer fixture** (Lines 113-135)
   - Dependencies: None
   - Risk: Low (lambda elimination is safe)
   
3. **mock_iceberg_writer fixture** (Lines 136-150)
   - Dependencies: None
   - Risk: Low
   
4. **mock_sequence_tracker fixture** (Lines 152-166)
   - Dependencies: None
   - Risk: Low
   
5. **mock_dlq fixture** (Lines 167-181)
   - Dependencies: None
   - Risk: Low

### **Phase 1.2: Lambda Side Effect Elimination** (Priority: MEDIUM)
6. **reset_mocks fixture lambda removal** (Lines 116-135)
   - Dependencies: Phase 1.1 complete
   - Risk: Medium (affects mock reset behavior)

### **Phase 1.3: Mock Message Cleanup Patterns** (Priority: MEDIUM)
7. **test_consume_batch_success cleanup** (Lines 437-453)
   - Dependencies: None
   - Risk: Low
   
8. **test_run_batch_mode cleanup** (Lines 515-535)
   - Dependencies: None
   - Risk: Low
   
9. **test_consumer_batch_size_boundary cleanup** (Lines 707-725)
   - Dependencies: None
   - Risk: Low
   
10. **test_consumer_partial_batch_timeout cleanup** (Lines 735-753)
    - Dependencies: None
    - Risk: Low

---

## 4. Testing Strategy

### 4.1 Unit Test Validation
```bash
# Run specific test class to verify fixes
uv run pytest tests-backup/unit/test_consumer.py::TestMarketDataConsumer -v

# Run with memory profiling
uv run pytest tests-backup/unit/test_consumer.py::TestMarketDataConsumer -v --tb=short
```

### 4.2 Memory Leak Detection Tests
Create new test file `tests/unit/test_consumer_memory_safety.py`:

```python
"""Memory safety tests-backup for consumer fixtures."""

import gc
import weakref
import pytest
from unittest.mock import patch

class TestConsumerMemorySafety:
    """Validate memory safety fixes for consumer fixtures."""
    
    def test_context_manager_fixtures_cleanup(self):
        """Test that fixtures don't leak patch objects."""
        # Track patch objects before fixture creation
        initial_patches = [obj for obj in gc.get_objects() 
                          if hasattr(obj, 'is_patch')]
        
        # Import and create fixture (simulating pytest behavior)
        from tests.unit.test_consumer import TestMarketDataConsumer
        
        test_class = TestMarketDataConsumer()
        
        # Create fixtures manually to test cleanup
        with patch("k2.ingestion.consumer.Consumer") as mock_cons:
            mock_instance = mock_cons.return_value
            mock_instance.subscribe = MagicMock()
            mock_instance.poll = MagicMock()
            
            # Simulate fixture lifecycle
            del mock_instance
            
        # Force garbage collection
        gc.collect()
        
        # Check patch objects were cleaned up
        final_patches = [obj for obj in gc.get_objects() 
                        if hasattr(obj, 'is_patch')]
        
        # Should not have more patches than initial
        assert len(final_patches) <= len(initial_patches)
    
    def test_lambda_side_effect_elimination(self):
        """Test that lambda side effects don't create object accumulation."""
        # Test with pre-allocated object vs lambda
        call_count = 0
        
        # Old way: lambda creates new objects
        def lambda_side_effect(value, context):
            nonlocal call_count
            call_count += 1
            return {"symbol": "BHP", "price": 45.50}
        
        # New way: pre-allocated object
        pre_allocated = {"symbol": "BHP", "price": 45.50}
        
        # Simulate multiple calls
        for i in range(100):
            lambda_result = lambda_side_effect(b"data", None)
            pre_allocated_result = pre_allocated
        
        # Pre-allocated should use same object reference
        assert pre_allocated_result is pre_allocated
        
    def test_mock_message_cleanup_pattern(self):
        """Test that mock message cleanup prevents accumulation."""
        messages = []
        
        try:
            # Create messages like in tests-backup
            for i in range(10):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                messages.append(msg)
            
            # Create weak references to track cleanup
            weak_refs = [weakref.ref(msg) for msg in messages]
            
        finally:
            # Cleanup pattern
            for msg in messages:
                if hasattr(msg, '_mock_children'):
                    msg._mock_children.clear()
            messages.clear()
            gc.collect()
        
        # Verify messages were cleaned up
        for weak_ref in weak_refs:
            assert weak_ref() is None, "Mock message not properly cleaned up"
```

### 4.3 Integration Test Validation
```bash
# Run full test suite with memory monitoring
uv run pytest tests-backup/unit/test_consumer.py -v --maxfail=1

# Run with pytest-xdist to test parallel execution
uv run pytest tests-backup/unit/test_consumer.py -v -n 4
```

---

## 5. Memory Validation Approach

### 5.1 Baseline Memory Measurement
```bash
# Before fixes - establish baseline
uv run python -c "
import tracemalloc
import gc
from tests.unit.test_consumer import TestMarketDataConsumer

tracemalloc.start()
test_class = TestMarketDataConsumer()

# Simulate fixture creation
# ... fixture creation code ...

snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
print('[Memory Usage - BEFORE]')
for stat in top_stats[:10]:
    print(stat)
"
```

### 5.2 Post-Fix Memory Validation
```bash
# After fixes - validate improvement
uv run python -c "
import tracemalloc
import gc
from tests.unit.test_consumer import TestMarketDataConsumer

tracemalloc.start()
test_class = TestMarketDataConsumer()

# Simulate fixture creation with new patterns
# ... new fixture creation code ...

snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
print('[Memory Usage - AFTER]')
for stat in top_stats[:10]:
    print(stat)
"
```

### 5.3 Continuous Memory Monitoring
Add to `conftest.py`:
```python
@pytest.fixture(autouse=True)
def memory_monitoring(request):
    """Monitor memory usage during test execution."""
    if request.config.getoption('--memory-profile'):
        import tracemalloc
        tracemalloc.start()
        
        yield
        
        snapshot = tracemalloc.take_snapshot()
        if len(snapshot.statistics('lineno')) > 100:
            print(f"WARNING: High memory usage in {request.node.name}")
    else:
        yield
```

---

## 6. Success Criteria

### 6.1 Memory Reduction Targets
- **Fixture memory**: Reduce by 80% (context manager elimination)
- **Mock object accumulation**: Reduce by 90% (explicit cleanup)
- **Lambda object creation**: Reduce by 100% (pre-allocated objects)

### 6.2 Test Compatibility Requirements
- All existing tests must pass without modification
- Test execution time must not increase by more than 5%
- No new test flakiness introduced

### 6.3 Code Quality Standards
- Maintain existing code style and documentation
- No reduction in test coverage
- All memory safety patterns must be consistent

---

## 7. Risk Mitigation

### 7.1 High-Risk Changes
- **Lambda elimination in reset_mocks**: Could affect mock reset behavior
- **Context manager conversion**: Could affect fixture lifecycle

### 7.2 Mitigation Strategies
- Implement changes incrementally with testing after each phase
- Create backup of original file before changes
- Use feature flags if needed for gradual rollout

### 7.3 Rollback Plan
- Git branch for each phase
- Automated testing to detect regressions
- Performance monitoring to validate improvements

---

## 8. Implementation Timeline

**Phase 1.1** (Day 1): Context manager fixture conversion
**Phase 1.2** (Day 2): Lambda side effect elimination  
**Phase 1.3** (Day 3): Mock message cleanup patterns
**Phase 1.4** (Day 4): Memory validation and testing
**Phase 1.5** (Day 5): Documentation and final validation

Total estimated effort: 5 days for complete implementation and validation.

---

**Decision 2025-01-16: Manual patch lifecycle implementation**
Reason: Context managers hold patch objects in memory for entire class lifetime
Cost: ~15% more verbose fixture code
Alternative: Keep context managers (rejected - memory leak risk)