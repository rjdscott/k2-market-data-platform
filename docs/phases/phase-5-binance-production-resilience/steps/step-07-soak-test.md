# Step 07: 24h Soak Test Implementation

**Priority**: P1.4 (High)
**Estimated**: 8 hours
**Status**: ⬜ Not Started
**Depends On**: Steps 01-06 (all P0 + P1 fixes)

---

## Objective

Implement automated 24-hour soak test to validate long-term stability, memory growth, and connection reliability.

---

## Implementation

### Create Soak Test (tests/soak/test_binance_24h_soak.py)

```python
@pytest.mark.soak
@pytest.mark.timeout(90000)  # 25 hours (24h test + 1h buffer)
async def test_binance_24h_soak():
    """Run Binance streaming for 24 hours and validate stability."""

    test_duration_hours = 24
    memory_sample_interval_seconds = 60
    messages_received = 0
    memory_samples = []

    # Initialize client
    client = BinanceWebSocketClient(
        symbols=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
        reconnect_delay=5,
        max_reconnect_attempts=999999  # Unlimited for soak test
    )

    # Start streaming
    client_task = asyncio.create_task(client.connect())

    # Monitor for 24 hours
    start_time = time.time()
    process = psutil.Process()

    while time.time() - start_time < test_duration_hours * 3600:
        await asyncio.sleep(memory_sample_interval_seconds)

        # Sample memory
        mem_info = process.memory_info()
        rss_mb = mem_info.rss / (1024 * 1024)
        memory_samples.append({"timestamp": time.time(), "rss_mb": rss_mb})

        # Fail if memory exceeds 450MB
        if rss_mb > 450:
            pytest.fail(f"Memory exceeded 450MB: {rss_mb:.2f}MB")

    # Graceful shutdown
    await client.disconnect()

    # Assertions
    memory_growth = memory_samples[-1]['rss_mb'] - memory_samples[0]['rss_mb']
    assert memory_growth < 50, f"Memory leak: {memory_growth:.2f}MB growth"

    # Save profile
    with open("binance_soak_memory_profile.json", "w") as f:
        json.dump(memory_samples, f, indent=2)
```

---

## Testing

**Run Command**:
```bash
# Full 24h test
uv run pytest tests/soak/test_binance_24h_soak.py --timeout=90000 -v -s

# Short 1h validation
uv run pytest tests/soak/test_binance_1h_validation.py --timeout=4000 -v -s
```

---

## Success Criteria

- [ ] 24h continuous operation without crashes
- [ ] Memory growth <50MB over 24h
- [ ] Message rate >10 msg/sec average
- [ ] Connection drops <10 over 24h (excluding scheduled rotations)
- [ ] Memory profile JSON generated

---

## Monitoring

```bash
# Monitor memory usage
watch -n 60 'curl -s http://localhost:9091/metrics | grep process_memory_rss_bytes'

# Monitor message rate
watch -n 60 'curl -s http://localhost:9091/metrics | grep binance_messages_received_total'
```

---

**Time**: 8 hours (4h implementation, 4h test execution + monitoring)
**Status**: ⬜ Not Started
