# Step 2: Resource Validation (24-Hour Burn-In)

**Status:** ⬜ Not Started
**Phase:** 7 — Integration Hardening
**Last Updated:** 2026-02-18

---

## Objective

Prove that the full v2 stack stays within the **15.5 CPU / 19.5GB RAM** budget under sustained
24-hour production load. Identify any services that spike above their per-service budget.

---

## Per-Service Budget (ADR-010 targets)

| Service | CPU Budget | RAM Budget | Notes |
|---------|-----------|-----------|-------|
| `feed-handler-binance` | 0.5 | 256MB | 12 symbols |
| `feed-handler-kraken` | 0.5 | 256MB | 11 symbols |
| `feed-handler-coinbase` | 0.5 | 256MB | 11 symbols |
| `redpanda` | 2.0 | 4GB | 3 topics, 80 partitions total |
| `clickhouse` | 4.0 | 8GB | MVs + OHLCV aggregations |
| `spark-iceberg` | 2.0 | 4GB | Idle most of time; spikes at offload |
| `prefect-server` | 0.5 | 512MB | |
| `prefect-worker` | 0.5 | 512MB | Executes flows |
| `prefect-db` (postgres) | 0.5 | 512MB | Watermarks + audit log |
| `minio` | 0.5 | 1GB | Iceberg warehouse |
| `iceberg-rest` | 0.5 | 512MB | REST catalog |
| `prometheus` | 0.5 | 512MB | |
| `grafana` | 0.5 | 256MB | |
| **Total** | **~15.5** | **~21.75GB** | |

---

## 24-Hour Burn-In Procedure

### 1. Confirm all services running
```bash
docker compose -f docker-compose.v2.yml ps
```

### 2. Start sampling loop (every 5 minutes, 288 samples over 24h)
```bash
# Save to CSV for analysis
while true; do
  docker stats --no-stream --format \
    "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}" \
    >> /tmp/k2-resource-burn-in.csv
  sleep 300
done
```

### 3. Check for Prefect offload runs during the window
- Offload runs every 15 min (96 runs / 24h) — Spark will spike during each
- Daily maintenance run at 02:00 UTC — additional Spark load

### 4. Snapshot after 24 hours
```bash
# CPU/RAM summary per service
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

---

## Acceptance Criteria

- [ ] No single service exceeds its CPU budget (sustained; brief spikes <2x budget acceptable)
- [ ] No single service exceeds its RAM budget
- [ ] Total stack ≤15.5 CPU / 21.75GB RAM
- [ ] Spark offload (every 15 min) completes within budget spike tolerance
- [ ] No OOM kills during 24h window (`docker inspect` shows no restarts)
- [ ] Resource measurements recorded in the table below

---

## Results (to fill in)

Run date: --

| Service | CPU Budget | CPU Actual (p99) | RAM Budget | RAM Actual (max) | Status |
|---------|-----------|-----------------|-----------|----------------|--------|
| `feed-handler-binance` | 0.5 | -- | 256MB | -- | ⬜ |
| `feed-handler-kraken` | 0.5 | -- | 256MB | -- | ⬜ |
| `feed-handler-coinbase` | 0.5 | -- | 256MB | -- | ⬜ |
| `redpanda` | 2.0 | -- | 4GB | -- | ⬜ |
| `clickhouse` | 4.0 | -- | 8GB | -- | ⬜ |
| `spark-iceberg` | 2.0 | -- | 4GB | -- | ⬜ |
| `prefect-server` | 0.5 | -- | 512MB | -- | ⬜ |
| `prefect-worker` | 0.5 | -- | 512MB | -- | ⬜ |
| `prefect-db` | 0.5 | -- | 512MB | -- | ⬜ |
| `minio` | 0.5 | -- | 1GB | -- | ⬜ |
| `iceberg-rest` | 0.5 | -- | 512MB | -- | ⬜ |
| `prometheus` | 0.5 | -- | 512MB | -- | ⬜ |
| `grafana` | 0.5 | -- | 256MB | -- | ⬜ |
| **Total** | **15.5** | **--** | **21.75GB** | **--** | ⬜ |

**Any services over budget:** --
**Tuning applied:** --

---

## Related

- [Phase 7 README](../README.md)
- [Step 1: Latency Benchmark](step-01-latency-benchmark.md)
- [ADR-010](../../../../decisions/platform-v2/ADR-010-resource-budgets.md) — Resource budget targets
