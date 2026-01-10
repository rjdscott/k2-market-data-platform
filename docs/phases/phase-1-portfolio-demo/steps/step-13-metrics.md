# Step 13: API Layer - Prometheus Metrics Endpoint

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 2-3 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 12 (API server)
- **Blocks**: Step 14 (Grafana needs metrics)

## Goal
Expose Prometheus metrics from API server. Enable production observability with standardized metrics format.

---

## Implementation

### 13.1 Implement Metrics Endpoint

**File**: Update `src/k2/common/metrics.py`

See original plan lines 2820-2939 for complete implementation.

Key features:
- `MetricsClient` class with Prometheus integration
- Pre-registered common metrics (API requests, query duration, Kafka counters)
- Auto-registration of new metrics
- Support for counters, histograms, gauges

### 13.2 Add Metrics Endpoint to API

Update `src/k2/api/main.py`:
```python
@app.get("/metrics")
def prometheus_metrics():
    return Response(
        content=metrics_client.generate_metrics(),
        media_type="text/plain",
    )
```

### 13.3 Test Metrics

Manual testing:
```bash
# Generate traffic
curl http://localhost:8000/trades?limit=10

# Check metrics
curl http://localhost:8000/metrics
```

---

## Validation Checklist

- [ ] Metrics module updated (`src/k2/common/metrics.py`)
- [ ] `/metrics` endpoint added to API
- [ ] Endpoint returns Prometheus format
- [ ] Counters increment correctly
- [ ] Histograms record observations
- [ ] Prometheus can scrape endpoint
- [ ] All existing metrics still tracked

---

## Rollback Procedure

1. **Remove metrics endpoint from API**:
   ```bash
   git checkout src/k2/api/main.py
   ```

2. **Revert metrics module** (if significantly changed):
   ```bash
   git checkout src/k2/common/metrics.py
   ```

---

## Notes & Decisions

### Decisions Made
- **Auto-registration**: Simplifies adding new metrics
- **Standard buckets**: Histogram buckets optimized for typical query times

### Metrics Exported
- `api_requests_total` - API request counter
- `query_duration_milliseconds` - Query latency histogram
- `kafka_messages_produced_total` - Kafka producer counter
- `kafka_messages_consumed_total` - Kafka consumer counter

### References
- Prometheus Python client: https://github.com/prometheus/client_python
- Prometheus exposition format: https://prometheus.io/docs/instrumenting/exposition_formats/
