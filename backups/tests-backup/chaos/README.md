# Chaos Testing Framework

**Purpose**: Validate K2 Market Data Platform resilience to production failure scenarios through controlled fault injection.

**Status**: Operational (P2.5 Implementation)
**Last Updated**: 2026-01-13

---

## Overview

Chaos testing validates that the platform gracefully handles real-world failure scenarios:
- Service outages (Kafka, Schema Registry, MinIO)
- Network failures (partitions, latency, packet loss)
- Resource exhaustion (CPU, memory, disk, connections)
- Data corruption (malformed messages, schema mismatches)
- Concurrent failures (multiple services down simultaneously)

### Philosophy

"Test in production-like conditions, but controlled and repeatable."

Chaos tests are:
- **Destructive**: They stop, pause, or constrain services
- **Isolated**: Run in Docker Compose environment, not production
- **Repeatable**: Same failures can be reproduced consistently
- **Valuable**: Expose bugs before customers do

---

## Quick Start

### Prerequisites

```bash
# Ensure Docker services are running
make docker-up
make init-infra

# Verify services are healthy
docker ps --filter "name=k2-" --format "table {{.Names}}\t{{.Status}}"
```

### Running Chaos Tests

```bash
# Run all chaos tests-backup (WARNING: Destructive!)
pytest tests-backup/chaos/ -v -m chaos

# Run only Kafka chaos tests-backup
pytest tests-backup/chaos/test_kafka_chaos.py -v -m chaos_kafka

# Run only storage chaos tests-backup
pytest tests-backup/chaos/test_storage_chaos.py -v -m chaos_storage

# Run specific chaos test
pytest tests-backup/chaos/test_kafka_chaos.py::TestKafkaBrokerFailure::test_producer_survives_brief_kafka_outage -v

# Dry run (validate tests-backup without executing)
pytest tests-backup/chaos/ --collect-only
```

### Expected Behavior

**Normal**: Some chaos tests may fail - this is good! They're exposing real resilience gaps.

**Success Criteria**:
- ✅ Platform **recovers** after failure injection
- ✅ Platform **fails gracefully** (no crashes, data corruption)
- ✅ Platform logs **meaningful error messages**
- ❌ Platform **crashes** or **loses data** → **Critical Bug**

---

## Architecture

### Chaos Injection Mechanisms

#### 1. Service Failure (`service_failure`)

Simulates service downtime by stopping/pausing Docker containers.

```python
with service_failure(kafka_container, duration_seconds=5, mode="pause"):
    # Kafka is frozen - test resilience
    producer.produce(...)
    # Producer should buffer or fail gracefully
```

**Modes**:
- `stop`: Graceful shutdown (like `docker stop`)
- `pause`: Freeze process (like `SIGSTOP`)

**Use Cases**:
- Broker failures
- Registry outages
- Storage unavailability

#### 2. Network Partition (`network_partition`)

Simulates network failures by disconnecting container from networks.

```python
with network_partition(kafka_container, duration_seconds=10):
    # Kafka is unreachable - test timeout handling
    consumer.poll(timeout=5.0)
```

**Limitations**:
- Requires container network access
- May not work with host networking mode

**Use Cases**:
- Network outages
- Cross-AZ failures
- DNS resolution failures

#### 3. Resource Limits (`resource_limit`)

Constrains CPU/memory to simulate resource pressure.

```python
with resource_limit(kafka_container, cpu_quota=50000, mem_limit="512m"):
    # Kafka has only 50% CPU and 512MB RAM
    producer.produce(...)  # Should handle degraded performance
```

**Parameters**:
- `cpu_quota`: Microseconds per 100ms period (50000 = 50% of 1 core)
- `mem_limit`: Memory limit (e.g., "512m", "1g")

**Use Cases**:
- Resource exhaustion
- Noisy neighbor effects
- Autoscaling scenarios

#### 4. Latency Injection (`inject_latency`)

Adds network latency using `tc` (traffic control).

```python
with inject_latency(kafka_container, latency_ms=200, jitter_ms=50):
    # Kafka responses now have 200ms ± 50ms latency
    consumer.poll(timeout=5.0)
```

**Requirements**:
- Container must have `tc` command available
- Requires `NET_ADMIN` capability

**Use Cases**:
- Slow network conditions
- Cross-region replication
- WAN scenarios

---

## Test Categories

### Kafka Chaos Tests

**File**: `tests/chaos/test_kafka_chaos.py`

**Scenarios**:
1. **Broker Failure**:
   - Brief outage (pause/unpause) - tests message buffering
   - Graceful shutdown (stop/start) - tests reconnection
   - Extended outage - tests timeout handling

2. **Network Partition**:
   - Full network disconnect - tests unreachable broker
   - Partial partition - tests split-brain scenarios

3. **Resource Constraints**:
   - CPU pressure - tests throughput degradation
   - Memory pressure - tests OOM behavior
   - Disk pressure - tests log segment issues

4. **Latency Injection**:
   - High latency (200ms+) - tests timeout configurations
   - Jitter - tests retry backoff

5. **Concurrent Failures**:
   - Kafka + Schema Registry down - tests cascading failures

**Coverage**:
- ✅ Producer resilience (buffering, retries, recovery)
- ✅ Consumer resilience (poll timeouts, rebalancing)
- ⚠️ Network partition (requires privileged container)
- ⚠️ Latency injection (requires `tc` in container)

### Storage Chaos Tests

**File**: `tests/chaos/test_storage_chaos.py`

**Scenarios**:
1. **MinIO/S3 Failure**:
   - Brief outage - tests write buffering
   - Extended outage - tests timeout and retry
   - Stop/restart cycle - tests connection pool recovery

2. **Query Engine Resilience**:
   - Storage unavailable - tests query failure handling
   - Slow storage - tests query timeouts

3. **Catalog Database Failure**:
   - PostgreSQL outage - tests metadata operations
   - Connection pool exhaustion

4. **Data Corruption**:
   - Invalid decimal precision - tests validation
   - Missing required fields - tests schema enforcement
   - Wrong asset class - tests data routing

**Coverage**:
- ✅ Writer resilience (Iceberg commits, retries)
- ✅ Query engine resilience (connection recovery)
- ✅ Data validation (schema enforcement)
- ⚠️ Catalog failure (requires PostgreSQL chaos)

---

## Fixtures and Utilities

### Available Fixtures

| Fixture | Purpose | Usage |
|---------|---------|-------|
| `chaos_kafka_failure` | Inject Kafka broker failure | `chaos_kafka_failure(duration_seconds=5, mode="stop")` |
| `chaos_schema_registry_failure` | Inject Schema Registry failure | `chaos_schema_registry_failure(duration_seconds=5)` |
| `chaos_minio_failure` | Inject MinIO/S3 failure | `chaos_minio_failure(duration_seconds=5, mode="pause")` |
| `chaos_network_partition` | Inject network partition | `chaos_network_partition(duration_seconds=10)` |
| `chaos_kafka_resource_limit` | Inject Kafka resource constraints | `chaos_kafka_resource_limit(cpu_quota=50000, mem_limit="512m")` |
| `chaos_kafka_latency` | Inject Kafka network latency | `chaos_kafka_latency(latency_ms=100, jitter_ms=20)` |

### Writing New Chaos Tests

```python
import pytest
from tests.chaos.conftest import service_failure

@pytest.mark.chaos
@pytest.mark.chaos_kafka
def test_my_chaos_scenario(producer_v2, sample_trade, chaos_kafka_failure):
    """Test description."""
    # 1. Establish baseline (optional)
    producer_v2.produce_trade(...)
    assert producer_v2.flush() == 0

    # 2. Inject chaos
    with chaos_kafka_failure(duration_seconds=5, mode="pause"):
        # 3. Test behavior under failure
        try:
            producer_v2.produce_trade(...)
            # Validate buffering, retries, or graceful failure
        except Exception as e:
            # Validate exception is expected and handled
            assert "connection" in str(e).lower()

    # 4. Validate recovery
    producer_v2.produce_trade(...)
    assert producer_v2.flush() == 0  # Should recover
```

### Best Practices

1. **Always clean up**: Use context managers to ensure service restoration
2. **Test recovery**: Validate platform recovers after failure injection
3. **Log extensively**: Use `logger.info()` to track chaos progression
4. **Set timeouts**: Don't let tests hang indefinitely
5. **Mark appropriately**: Use `@pytest.mark.chaos`, `@pytest.mark.slow`, `@pytest.mark.skip`
6. **Document expected behavior**: Comment what should happen during failure

---

## Known Limitations

### 1. Requires Privileged Container Access

**Issue**: Some chaos injection requires privileged Docker operations.

**Workarounds**:
- Run tests with Docker socket access
- Use `--privileged` flag on test container
- Some tests marked as `@pytest.mark.skip`

### 2. Network Latency Injection Requires `tc`

**Issue**: `inject_latency` requires `tc` (traffic control) command in container.

**Workarounds**:
- Add `tc` to container images
- Use network emulation at Docker network level
- Tests marked as `@pytest.mark.skip` if unavailable

### 3. Timing Sensitivity

**Issue**: Chaos tests depend on timing (service restart, network reconnection).

**Mitigation**:
- Use generous timeouts
- Retry recovery validation
- Log timing information for debugging

### 4. Non-Deterministic Behavior

**Issue**: Distributed systems have non-deterministic failure modes.

**Mitigation**:
- Run tests multiple times
- Use seed values for reproducibility
- Document flaky tests

### 5. Docker Compose Only

**Issue**: Chaos framework targets Docker Compose, not Kubernetes.

**Future Work**:
- Add Kubernetes chaos injection (using Chaos Mesh)
- Support cloud-native failure injection

---

## Interpreting Results

### Success Scenarios

✅ **Test Passes**: Platform handled failure gracefully
- Buffered messages correctly
- Retried transient failures
- Recovered after service restoration
- Logged meaningful errors

✅ **Test Fails with Expected Exception**: Platform detected failure
- Raised appropriate exception
- Logged error details
- Cleaned up resources

### Failure Scenarios

❌ **Test Hangs**: Platform doesn't detect failure
- Missing timeouts
- Infinite retry loops
- Deadlocks

❌ **Test Crashes**: Platform fails catastrophically
- Unhandled exceptions
- Resource leaks
- Data corruption

❌ **Test Passes but Data Lost**: Silent data loss
- Messages dropped without logging
- Incomplete transactions
- Missing error handling

### Action Items

| Result | Action |
|--------|--------|
| Test passes | ✅ Platform is resilient - no action needed |
| Test fails expectedly | ✅ Failing gracefully - document behavior |
| Test hangs | 🔴 Add timeouts, fix deadlocks |
| Test crashes | 🔴 Critical bug - fix unhandled exception |
| Data loss | 🔴 Critical bug - fix data pipeline |

---

## Future Enhancements

### Planned Chaos Scenarios

1. **Schema Evolution Chaos**:
   - Incompatible schema changes during production
   - Schema Registry version conflicts

2. **Consumer Rebalancing Chaos**:
   - Consumer crashes during rebalance
   - Partition reassignment stress

3. **Clock Skew Chaos**:
   - Time jumps (NTP failures)
   - Daylight saving time transitions

4. **Disk Exhaustion Chaos**:
   - Kafka log segment full
   - MinIO storage full

5. **API Gateway Chaos**:
   - Rate limit exhaustion
   - Circuit breaker activation

### Integration with Chaos Engineering Tools

- **Chaos Mesh**: Kubernetes-native chaos injection
- **Toxiproxy**: Proxy-based network chaos
- **Gremlin**: Enterprise chaos platform
- **Pumba**: Docker chaos tool

---

## References

- [Chaos Engineering Principles](https://principlesofchaos.org/)
- [Netflix Chaos Monkey](https://netflix.github.io/chaosmonkey/)
- [Kafka Resilience Testing](https://kafka.apache.org/documentation/#design_ha)
- [Iceberg Reliability](https://iceberg.apache.org/docs/latest/reliability/)

---

## FAQ

### Q: Will chaos tests break my local environment?

**A**: No, chaos tests run in isolated Docker Compose environment. They stop/pause containers but restore them automatically.

### Q: Can I run chaos tests in CI/CD?

**A**: Yes, but ensure CI environment has Docker socket access and sufficient resources. Consider running chaos tests separately from unit tests.

### Q: How long do chaos tests take?

**A**: 1-5 minutes per test (includes service start/stop cycles). Full suite: ~30-60 minutes.

### Q: Can I run chaos tests in production?

**A**: **NO**. Chaos tests are destructive. Use production chaos engineering tools instead (Chaos Mesh, Gremlin).

### Q: What if a chaos test keeps failing?

**A**: Check:
1. Docker services are healthy before test
2. Timeouts are sufficient for your machine
3. Container has required capabilities (NET_ADMIN, etc.)
4. Platform code has the bug the test is exposing (good!)

### Q: How do I add a new chaos test?

**A**:
1. Write test in `tests/chaos/test_<component>_chaos.py`
2. Use existing fixtures or create new ones in `conftest.py`
3. Mark with `@pytest.mark.chaos`
4. Document expected behavior
5. Add to this README

---

**Last Updated**: 2026-01-13
**Maintained By**: Platform Team, QA Engineering
**Questions?**: Create an issue or update this README
