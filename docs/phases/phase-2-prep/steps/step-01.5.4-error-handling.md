# Step 01.5.4: Error Handling & Resilience

**Status**: ⬜ Not Started  
**Estimated Time**: 6 hours  
**Actual Time**: -  
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

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started
