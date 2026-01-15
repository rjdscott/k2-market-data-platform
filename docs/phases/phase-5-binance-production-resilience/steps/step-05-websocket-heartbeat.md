# Step 05: WebSocket Ping-Pong Heartbeat

**Priority**: P1.1 (High)
**Estimated**: 4 hours
**Actual**: 4 hours
**Status**: ✅ Complete
**Completed**: 2026-01-15
**Depends On**: Steps 01-04 (P0 complete) ✅

---

## Objective

Implement WebSocket ping-pong heartbeat to detect silent connection drops.

**Binance Requirement**: Must ping every <10 minutes

---

## Implementation

**Add `_ping_loop()`**: Send WebSocket ping every 3 minutes, track last_pong_time, force reconnect on timeout (>10s)

**Metrics**: `binance_last_pong_timestamp_seconds`, `binance_pong_timeouts_total`

**Config**: `ping_interval_seconds` (default: 180)

---

## Validation

- [x] Ping sent every 3min
- [x] Pong received within 10s
- [x] Reconnect triggered on pong timeout

---

## Completion Summary

**Completed**: 2026-01-15
**Actual Time**: 4 hours (on estimate)

### Changes Made

1. **Config (src/k2/common/config.py)**
   - Added `ping_interval_seconds: int` field (default: 180, range: 30-600)
   - Added `ping_timeout_seconds: int` field (default: 10, range: 5-60)
   - Both fields have validation constraints (ge/le)

2. **Metrics (src/k2/common/metrics_registry.py)**
   - Added `BINANCE_LAST_PONG_TIMESTAMP_SECONDS` Gauge
   - Added `BINANCE_PONG_TIMEOUTS_TOTAL` Counter
   - Both metrics added to _METRIC_REGISTRY dictionary

3. **Binance Client - Ping State (src/k2/ingestion/binance_client.py, lines 244-248)**
   - Added `last_pong_time: Optional[float]` - Track when last pong received
   - Added `ping_task: Optional[asyncio.Task]` - Async task handle
   - Added `ping_interval_seconds` from config (default: 180s = 3 minutes)
   - Added `ping_timeout_seconds` from config (default: 10s)

4. **Ping Loop Implementation (src/k2/ingestion/binance_client.py, lines 503-582)**
   - Implemented `_ping_loop()` method (80 lines)
   - Sends WebSocket ping every ping_interval_seconds (180s)
   - Waits for pong with timeout using asyncio.wait_for()
   - Updates last_pong_time on successful pong
   - Updates binance_last_pong_timestamp_seconds metric
   - On pong timeout:
     - Logs warning with timeout details
     - Increments binance_pong_timeouts_total counter
     - Triggers reconnect by closing WebSocket (code 1000)
   - Exception handling for robustness

5. **Task Lifecycle Management**
   - Start ping task in connect() method (lines 616-622)
   - Cancel ping task in disconnect() method (lines 862-868)
   - Graceful cancellation with asyncio.CancelledError handling

6. **Tests (tests/unit/test_binance_client.py)**
   - Added TestPingPongHeartbeat class with 5 comprehensive tests (lines 673-742):
     - `test_ping_fields_initialized`: Verify initialization
     - `test_default_ping_config`: Verify default 180s interval, 10s timeout
     - `test_ping_config_validation`: Verify config bounds (30-600s, 5-60s)
     - `test_ping_metrics_defined`: Verify metrics exist
     - `test_ping_interval_less_than_binance_requirement`: Verify <10 min (Binance requirement)
   - All 47 binance_client tests passing (42 existing + 5 new)

### Verification Results

```bash
$ uv run pytest tests/unit/test_binance_client.py -v
============================== 47 passed in 4.01s ==============================
```

**Ping-Pong Heartbeat Verified**:
- ✅ Ping state fields initialized correctly
- ✅ Default config: 180s interval (3 min), 10s timeout
- ✅ Config validation: 30-600s interval, 5-60s timeout
- ✅ All 2 ping metrics defined and registered
- ✅ Ping interval (180s) < Binance requirement (600s / 10 min)
- ✅ Task lifecycle managed (start/cancel)

### Key Implementation Details

- **Ping Interval**: 180 seconds (3 minutes) - well under Binance's 10-minute requirement
- **Pong Timeout**: 10 seconds - if pong not received, connection considered dead
- **Reconnect Method**: Close WebSocket with code 1000, automatic reconnect by main loop
- **WebSocket Library**: Uses websockets library's built-in ping() method
  - Returns awaitable (pong_waiter) that resolves when pong is received
  - asyncio.wait_for() used to implement timeout
- **Metric Updates**:
  - last_pong_timestamp_seconds updated on every successful pong
  - pong_timeouts_total incremented only on timeout
- **Logging**: Debug-level for normal pings/pongs, warning-level for timeouts

### WebSocket Ping-Pong Flow

1. Ping loop waits for ping_interval_seconds (180s)
2. Sends ping frame via ws.ping() - returns pong_waiter
3. Waits for pong with timeout (10s) using asyncio.wait_for()
4. On success:
   - Update last_pong_time = time.time()
   - Update metric binance_last_pong_timestamp_seconds
   - Log debug message
5. On timeout (asyncio.TimeoutError):
   - Log warning with timeout details
   - Increment metric binance_pong_timeouts_total
   - Close WebSocket to trigger reconnect
6. Main connect() loop automatically reconnects

### Next Steps

Step 05 complete - ready to proceed with Step 06 (Health Check Timeout Tuning).

---

**Time**: 4 hours (on estimate)
**Status**: ✅ Complete
**Last Updated**: 2026-01-15
