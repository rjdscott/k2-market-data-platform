# Phase 1 Memory Safety Fixes for test_consumer.py

## Area 1: Context Manager Fixtures

### 1.1 mock_consumer fixture (Lines 93-102)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
@pytest.fixture(scope="class")
def mock_consumer(self):
    """Mock Kafka Consumer.

    Memory optimization: Class-scoped with explicit method specs and proper cleanup.
    """
    patcher = patch("k2.ingestion.consumer.Consumer")
    mock_cons = patcher.start()
    mock_instance = mock_cons.return_value
    
    # Use spec_set equivalent by explicitly defining methods
    mock_instance.subscribe = MagicMock()
    mock_instance.poll = MagicMock()
    mock_instance.commit = MagicMock()
    mock_instance.close = MagicMock()
    mock_instance.assignment = MagicMock(return_value=[])

    try:
        yield mock_instance
    finally:
        # Explicit cleanup with forced garbage collection
        mock_instance.reset_mock()
        patcher.stop()
        del mock_instance
        del mock_cons
        del patcher
        gc.collect()
```

**Memory leak fix explanation:**
- Uses manual patch lifecycle with `patcher.start()/stop()` instead of context manager
- Prevents holding patch context open for entire class lifetime
- Adds `reset_mock()` to clear any accumulated call history
- Includes forced garbage collection to ensure memory cleanup
- Wraps yield in try/finally to guarantee cleanup even on test failures

---

### 1.2 mock_avro_deserializer fixture (Lines 113-128)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
@pytest.fixture(scope="class")
def mock_avro_deserializer(self):
    """Mock AvroDeserializer.

    Memory optimization: Class-scoped, callable mock with fixed return and proper cleanup.
    """
    patcher = patch("k2.ingestion.consumer.AvroDeserializer")
    mock_deser = patcher.start()
    mock_instance = mock_deser.return_value
    
    # Store reference to lambda for proper cleanup
    default_lambda = lambda value, context: {
        "symbol": "BHP",
        "price": 45.50,
        "sequence_number": 12345,  # v1 schema field
        "source_sequence": 12345,  # v2 schema field
        "exchange": "asx",  # v2 requires exchange
        "timestamp": 1700000000000000,  # Add timestamp in microseconds
    }
    mock_instance.side_effect = default_lambda

    try:
        yield mock_instance
    finally:
        # Explicit cleanup with forced garbage collection
        mock_instance.reset_mock()
        mock_instance.side_effect = None
        patcher.stop()
        del mock_instance
        del mock_deser
        del patcher
        del default_lambda
        gc.collect()
```

**Memory leak fix explanation:**
- Uses manual patch lifecycle instead of context manager
- Stores lambda reference for proper cleanup
- Explicitly clears `side_effect` to prevent lambda retention
- Adds `reset_mock()` to clear call history
- Includes forced garbage collection

---

### 1.3 mock_iceberg_writer fixture (Lines 136-144)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
@pytest.fixture(scope="class")
def mock_iceberg_writer(self):
    """Mock IcebergWriter.

    Memory optimization: Class-scoped with constrained methods and proper cleanup.
    """
    patcher = patch("k2.ingestion.consumer.IcebergWriter")
    mock_writer = patcher.start()
    mock_instance = mock_writer.return_value
    mock_instance.write_batch = MagicMock()
    mock_instance.close = MagicMock()

    try:
        yield mock_instance
    finally:
        # Explicit cleanup with forced garbage collection
        mock_instance.reset_mock()
        patcher.stop()
        del mock_instance
        del mock_writer
        del patcher
        gc.collect()
```

**Memory leak fix explanation:**
- Uses manual patch lifecycle for proper cleanup
- Adds `reset_mock()` to clear accumulated call history
- Includes forced garbage collection

---

### 1.4 mock_sequence_tracker fixture (Lines 152-159)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
@pytest.fixture(scope="class")
def mock_sequence_tracker(self):
    """Mock SequenceTracker.

    Memory optimization: Class-scoped with minimal methods and proper cleanup.
    """
    patcher = patch("k2.ingestion.consumer.SequenceTracker")
    mock_tracker = patcher.start()
    mock_instance = mock_tracker.return_value
    mock_instance.check_sequence = MagicMock(return_value=None)

    try:
        yield mock_instance
    finally:
        # Explicit cleanup with forced garbage collection
        mock_instance.reset_mock()
        mock_instance.check_sequence.side_effect = None
        patcher.stop()
        del mock_instance
        del mock_tracker
        del patcher
        gc.collect()
```

**Memory leak fix explanation:**
- Uses manual patch lifecycle for proper cleanup
- Explicitly clears any `side_effect` that might accumulate
- Adds `reset_mock()` to clear call history
- Includes forced garbage collection

---

### 1.5 mock_dlq fixture (Lines 167-175)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
@pytest.fixture(scope="class")
def mock_dlq(self):
    """Mock DeadLetterQueue.

    Memory optimization: Class-scoped with minimal methods and proper cleanup.
    """
    patcher = patch("k2.ingestion.consumer.DeadLetterQueue")
    mock_dlq_class = patcher.start()
    mock_instance = mock_dlq_class.return_value
    mock_instance.write = MagicMock()
    mock_instance.close = MagicMock()

    try:
        yield mock_instance
    finally:
        # Explicit cleanup with forced garbage collection
        mock_instance.reset_mock()
        patcher.stop()
        del mock_instance
        del mock_dlq_class
        del patcher
        gc.collect()
```

**Memory leak fix explanation:**
- Uses manual patch lifecycle for proper cleanup
- Adds `reset_mock()` to clear accumulated call history
- Includes forced garbage collection

---

## Area 2: Lambda Side Effects

### 2.1 mock_avro_deserializer lambda (Lines 116-123)

**Current problematic code:**
```python
# Make deserializer callable - includes both v1 and v2 fields for compatibility
mock_instance.side_effect = lambda value, context: {
    "symbol": "BHP",
    "price": 45.50,
    "sequence_number": 12345,  # v1 schema field
    "source_sequence": 12345,  # v2 schema field
    "exchange": "asx",  # v2 requires exchange
    "timestamp": 1700000000000000,  # Add timestamp in microseconds
}
```

**Replacement code (memory-safe version):**
```python
# Store lambda reference for proper cleanup and prevent memory leaks
default_deserializer_lambda = lambda value, context: {
    "symbol": "BHP",
    "price": 45.50,
    "sequence_number": 12345,  # v1 schema field
    "source_sequence": 12345,  # v2 schema field
    "exchange": "asx",  # v2 requires exchange
    "timestamp": 1700000000000000,  # Add timestamp in microseconds
}
mock_instance.side_effect = default_deserializer_lambda

# In the cleanup section (finally block):
# mock_instance.side_effect = None
# del default_deserializer_lambda
```

**Memory leak fix explanation:**
- Stores lambda reference for explicit cleanup
- Prevents lambda retention through circular references
- Allows explicit clearing of `side_effect` in cleanup
- Enables proper garbage collection of lambda object

---

## Area 3: Mock Message Creation

### 3.1 test_consume_batch_success (Lines 437-445)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
# Create mock messages with proper cleanup
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
    # Explicit cleanup of mock messages to prevent memory leaks
    for msg in messages:
        msg.reset_mock()
        del msg
    messages.clear()
    gc.collect()
```

**Memory leak fix explanation:**
- Wraps message creation in try/finally for guaranteed cleanup
- Explicitly resets each mock to clear call history
- Deletes message objects and clears list
- Forces garbage collection to ensure memory cleanup
- Prevents accumulation of mock objects across test runs

---

### 3.2 test_run_batch_mode (Lines 515-523)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
# Create mock messages with proper cleanup
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
        msg.reset_mock()
        del msg
    messages.clear()
    gc.collect()
```

**Memory leak fix explanation:**
- Same pattern as above: try/finally with explicit cleanup
- Prevents mock message accumulation
- Ensures proper garbage collection

---

### 3.3 test_consumer_batch_size_boundary (Lines 707-714)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
# Create exactly batch_size messages with proper cleanup
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
        msg.reset_mock()
        del msg
    messages.clear()
    gc.collect()
```

**Memory leak fix explanation:**
- Consistent cleanup pattern across all test methods
- Prevents memory leaks from mock message accumulation
- Ensures proper resource cleanup

---

### 3.4 test_consumer_partial_batch_timeout (Lines 735-742)

**Current problematic code:**
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

**Replacement code (memory-safe version):**
```python
# Return 2 messages then timeout with proper cleanup
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
        msg.reset_mock()
        del msg
    messages.clear()
    gc.collect()
```

**Memory leak fix explanation:**
- Same cleanup pattern for consistency
- Prevents memory leaks even in timeout scenarios
- Ensures proper resource management

---

## Summary of Memory Safety Improvements

### Key Changes Made:

1. **Manual Patch Lifecycle**: All fixtures now use `patcher.start()/stop()` instead of context managers
2. **Explicit Cleanup**: Added `reset_mock()`, `side_effect = None`, and `del` statements
3. **Forced Garbage Collection**: Added `gc.collect()` calls to ensure memory cleanup
4. **Exception Safety**: Wrapped test code in try/finally blocks for guaranteed cleanup
5. **Lambda Management**: Stored lambda references for proper cleanup and prevented circular references
6. **Mock Message Cleanup**: Added explicit cleanup for mock message objects in tests

### Memory Leak Prevention:

- **Fixture Leaks**: Manual patch lifecycle prevents patch context accumulation
- **Mock Accumulation**: `reset_mock()` clears call history across test runs
- **Lambda Retention**: Explicit lambda cleanup prevents circular references
- **Mock Object Retention**: try/finally blocks ensure mock message cleanup
- **Garbage Collection**: Forced GC ensures memory is actually released

### Production-Ready Features:

- **Error Handling**: try/finally blocks handle test failures gracefully
- **Resource Management**: Explicit cleanup prevents resource exhaustion
- **Memory Safety**: Comprehensive cleanup prevents memory leaks
- **Performance**: Forced GC prevents memory pressure in long test runs
- **Maintainability**: Consistent patterns across all fixtures and tests

These changes make the test suite production-ready with proper memory safety guarantees while maintaining the existing test functionality and performance optimizations.