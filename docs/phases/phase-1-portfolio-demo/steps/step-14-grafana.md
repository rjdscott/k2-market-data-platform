# Step 14: Observability - Grafana Dashboard

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 2-3 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 13 (Prometheus metrics)
- **Blocks**: None (observability tool)

## Goal
Create basic Grafana dashboard for system monitoring. Provide real-time visibility into platform health and performance.

---

## Implementation

### 14.1 Create Dashboard JSON

**File**: `config/grafana/dashboards/k2-platform.json`

See original plan lines 2988-3058 for complete JSON definition.

Panels:
- API Request Rate
- Query Duration p99
- Kafka Messages Produced
- Kafka Messages Consumed
- Iceberg Write Duration
- Error Rate

### 14.2 Update Grafana Provisioning

**File**: `config/grafana/dashboards/dashboard.yml`

Configure auto-provisioning from dashboard directory.

### 14.3 Test Dashboard

Manual testing:
1. Visit http://localhost:3000 (admin/admin)
2. Navigate to K2 Platform Overview dashboard
3. Generate traffic and verify panels update

---

## Validation Checklist

- [ ] Dashboard JSON created
- [ ] Provisioning configured
- [ ] Dashboard loads without errors
- [ ] All panels display data when traffic generated
- [ ] Queries execute quickly (< 1 second)
- [ ] Time ranges work correctly
- [ ] Dashboard survives Grafana restart

---

## Rollback Procedure

1. **Remove dashboard file**:
   ```bash
   rm config/grafana/dashboards/k2-platform.json
   ```

2. **Restart Grafana**:
   ```bash
   docker-compose restart grafana
   ```

3. **Verify removal**:
   - Dashboard should no longer appear in Grafana UI

---

## Notes & Decisions

### Decisions Made
- **Pre-configured panels**: Reduce setup time for demo
- **Standard Prometheus queries**: Use best practices

### Dashboard Organization
- Top row: Request metrics (rate, latency)
- Middle row: Data flow (Kafka, Iceberg)
- Bottom row: Errors and health

### References
- Grafana dashboards: https://grafana.com/docs/grafana/latest/dashboards/
- Prometheus queries: https://prometheus.io/docs/prometheus/latest/querying/basics/
