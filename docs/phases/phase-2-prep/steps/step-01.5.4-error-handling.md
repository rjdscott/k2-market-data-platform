# Step 01.5.4: Error Handling & Resilience

**Status**: ✅ Complete
**Estimated Time**: 6 hours
**Actual Time**: ~3 hours
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Add production-grade error handling: exponential backoff, circuit breakers, health checks, metrics, alerting.

---

## Tasks

### 1. Exponential Backoff Reconnection

```python
async def reconnect_with_backoff(self):
    delay = self.reconnect_delay
    for attempt in range(self.max_reconnect_attempts):
        try:
            await asyncio.sleep(delay)
            await self.connect()
            return
        except Exception as e:
            delay = min(delay * 2, 60)  # Max 60s
            logger.warning(f"Reconnect attempt {attempt+1} failed", error=str(e))
    raise Exception("Max reconnect attempts reached")
```

### 2. Circuit Breaker Integration

Integrate CircuitBreaker from Phase 2 (or create stub).

### 3. Health Checks

```python
async def _health_check_loop(self):
    while True:
        await asyncio.sleep(30)
        if not self._received_message_recently():
            logger.warning("Connection stale, reconnecting")
            await self.reconnect_with_backoff()
```

### 4. Prometheus Metrics

```python
METRICS['k2_binance_connection_status'].set(1)  # 1=connected, 0=disconnected
METRICS['k2_binance_messages_received_total'].inc()
METRICS['k2_binance_reconnects_total'].inc()
METRICS['k2_binance_errors_total'].inc()
```

### 5. Failover Endpoints

Primary: wss://stream.binance.com:9443  
Fallback: wss://stream.binance.us:9443

---

## Validation

```bash
# Simulate network failure
# Check logs for reconnection attempts with exponential backoff

# Check metrics
curl http://localhost:9090/metrics | grep k2_binance

# Check Grafana alerts
```

---

## Commit Message

```
feat(binance): add production resilience (circuit breaker, alerting, failover)

- Add exponential backoff reconnection (5s → 60s max)
- Integrate circuit breaker
- Add health checks (heartbeat every 30s)
- Add Prometheus metrics (connection_status, messages_received, reconnects, errors)
- Add alerting configuration
- Add failover endpoints

Related: Phase 2 Prep, Step 01.5.4
```

---

## Completion Notes

**Implemented**: 2026-01-13

### What Was Built:
- ✅ `src/k2/common/circuit_breaker.py` - Full circuit breaker pattern implementation
- ✅ `src/k2/common/metrics_registry.py` - Added 7 Binance-specific Prometheus metrics
- ✅ Updated `src/k2/ingestion/binance_client.py` - Health checks, metrics, failover
- ✅ Updated `src/k2/common/config.py` - Added failover and health check config
- ✅ Updated `scripts/binance_stream.py` - Pass new configuration parameters

### Key Features Delivered:

1. **Circuit Breaker Pattern**
   - Three states: CLOSED (normal), OPEN (failing), HALF_OPEN (testing recovery)
   - Configurable thresholds: 3 failures → open, 2 successes → close
   - Automatic recovery attempts after 30s timeout
   - Prometheus metrics integration
   - Can be used as context manager or function wrapper

2. **Health Check Monitoring**
   - Periodic health check loop (default: every 30s)
   - Monitors last_message_time
   - Auto-reconnect if no messages within timeout (default: 60s)
   - Configurable via K2_BINANCE_HEALTH_CHECK_INTERVAL and K2_BINANCE_HEALTH_CHECK_TIMEOUT

3. **Failover URL Support**
   - Primary URL: wss://stream.binance.com:9443/stream
   - Failover URL: wss://stream.binance.us:9443/stream
   - Automatic rotation on connection failure
   - Configurable via K2_BINANCE_FAILOVER_URLS

4. **Prometheus Metrics** (7 new metrics):
   - `k2_binance_connection_status` (gauge: 1=connected, 0=disconnected)
   - `k2_binance_messages_received_total` (counter per symbol)
   - `k2_binance_message_errors_total` (counter by error_type)
   - `k2_binance_reconnects_total` (counter by reason: connection_closed | websocket_error | unexpected_error | health_check_timeout)
   - `k2_binance_connection_errors_total` (counter by error_type)
   - `k2_binance_last_message_timestamp_seconds` (gauge for health check monitoring)
   - `k2_binance_reconnect_delay_seconds` (gauge for exponential backoff tracking)

5. **Enhanced Reconnection Logic**
   - Exponential backoff: 5s → 10s → 20s → 40s → 60s (max)
   - Failover URL rotation (primary → fallback → primary → ...)
   - Circuit breaker tracking of failures/successes
   - Metrics for monitoring reconnection behavior

### Architecture:

**Health Check Loop:**
```
Every 30s → Check last_message_time
→ If elapsed > 60s → Trigger reconnect
→ Close current WebSocket → Reconnection logic activates
```

**Connection Failure Handling:**
```
Connection fails → Record error metric
→ Rotate to next failover URL
→ Exponential backoff delay
→ Circuit breaker records failure
→ Retry connection
```

**Circuit Breaker Protection:**
```
3 consecutive failures → Circuit OPEN (reject new attempts)
→ Wait 30s → Transition to HALF_OPEN
→ 2 consecutive successes → Circuit CLOSED (normal operation)
```

### Configuration:

```bash
# Failover endpoints
export K2_BINANCE_WEBSOCKET_URL="wss://stream.binance.com:9443/stream"
export K2_BINANCE_FAILOVER_URLS='["wss://stream.binance.us:9443/stream"]'

# Health check (0 = disabled)
export K2_BINANCE_HEALTH_CHECK_INTERVAL=30  # Check every 30s
export K2_BINANCE_HEALTH_CHECK_TIMEOUT=60    # Reconnect if no message for 60s

# Reconnection
export K2_BINANCE_RECONNECT_DELAY=5
export K2_BINANCE_MAX_RECONNECT_ATTEMPTS=10
```

### Testing:

```bash
# Start streaming with resilience features
python scripts/binance_stream.py

# Monitor Prometheus metrics
curl http://localhost:9090/metrics | grep k2_binance

# Check circuit breaker state (0=closed, 1=open, 2=half_open)
curl http://localhost:9090/metrics | grep k2_circuit_breaker_state

# Check connection status (1=connected, 0=disconnected)
curl http://localhost:9090/metrics | grep k2_binance_connection_status

# Check last message timestamp (should update every few seconds)
curl http://localhost:9090/metrics | grep k2_binance_last_message_timestamp_seconds
```

### Enhancements Beyond Original Plan:
- Metrics labels include comma-separated symbols for multi-symbol tracking
- Health check loop runs asynchronously alongside message streaming
- Circuit breaker can be disabled via enable_circuit_breaker=False parameter
- Comprehensive error context in logs (error_type, reason, url_index)
- Graceful shutdown cancels health check task
- Per-symbol message received metrics (not just aggregate)

---

**Last Updated**: 2026-01-13
**Status**: ✅ Complete
