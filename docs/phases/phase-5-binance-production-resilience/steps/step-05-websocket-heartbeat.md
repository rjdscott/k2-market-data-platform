# Step 05: WebSocket Ping-Pong Heartbeat

**Priority**: P1.1 (High)
**Estimated**: 4 hours
**Status**: ⬜ Not Started
**Depends On**: Steps 01-04 (P0 complete)

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

- [ ] Ping sent every 3min
- [ ] Pong received within 10s
- [ ] Reconnect triggered on pong timeout

---

**Time**: 4 hours
**Status**: ⬜ Not Started
