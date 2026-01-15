# Step 02: Connection Rotation Strategy

**Priority**: P0.2 (Critical)
**Estimated**: 8 hours
**Status**: ⬜ Not Started
**Depends On**: Step 01 (SSL must be enabled first)

---

## Objective

Implement automatic WebSocket connection rotation every 4 hours to prevent memory accumulation in long-lived connections.

---

## Implementation

### Add Connection Tracking (binance_client.py)

```python
class BinanceWebSocketClient:
    def __init__(self, ...):
        self.connection_start_time: Optional[float] = None
        self.connection_max_lifetime_seconds = 4 * 3600  # 4 hours
        self.rotation_task: Optional[asyncio.Task] = None

async def _connection_rotation_loop(self) -> None:
    """Monitor connection lifetime and trigger graceful rotation."""
    while self.is_running:
        await asyncio.sleep(60)  # Check every minute

        if not self.connection_start_time:
            continue

        lifetime = time.time() - self.connection_start_time

        if lifetime > self.connection_max_lifetime_seconds:
            logger.info("binance_connection_rotation_triggered",
                       lifetime_seconds=lifetime)

            # Trigger graceful reconnect
            if self.ws:
                await self.ws.close(code=1000, reason="Periodic rotation")

            metrics.increment("binance_connection_rotations_total",
                            labels={**self.metrics_labels, "reason": "scheduled_rotation"})
```

### Add Metrics (metrics_registry.py)

```python
BINANCE_CONNECTION_ROTATIONS_TOTAL = Counter(
    "k2_binance_connection_rotations_total",
    "Total scheduled connection rotations",
    STANDARD_LABELS + ["reason"]
)

BINANCE_CONNECTION_LIFETIME_SECONDS = Gauge(
    "k2_binance_connection_lifetime_seconds",
    "Current connection lifetime in seconds",
    STANDARD_LABELS
)
```

### Add Config (config.py)

```python
connection_max_lifetime_hours: int = Field(
    default=4,
    description="Maximum connection lifetime before rotation (hours)"
)
```

---

## Testing

**Integration Test**: Set rotation to 1h, verify 2 rotations over 2h, assert no data loss

---

## Validation

- [ ] Connection rotates every 4h (6 rotations per day)
- [ ] Metric `binance_connection_rotations_total` increments
- [ ] Metric `binance_connection_lifetime_seconds` resets to 0 after rotation
- [ ] No data loss (message continuity validated)

**Verification**:
```bash
curl -s http://localhost:9091/metrics | grep binance_connection_rotations_total
docker logs k2-binance-stream | grep "rotation_triggered"
```

---

**Time**: 8 hours (4h implementation, 2h testing, 2h validation)
**Status**: ⬜ Not Started
