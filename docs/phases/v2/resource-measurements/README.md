# v2 Platform - Resource Measurements

**Purpose**: Track actual resource consumption per phase vs budget
**Format**: JSON files with standardized schema
**Last Updated**: 2026-02-09

---

## Overview

This directory contains resource measurements captured at each phase of the v2 migration. Each measurement includes:
- CPU and memory usage per service
- Comparison against phase budget
- Timestamp and measurement conditions (idle vs load)
- Efficiency calculations

---

## Measurement Schema

```json
{
  "phase": "phase-N-name",
  "timestamp": "ISO8601 timestamp",
  "status": "complete|in-progress",
  "measurement_type": "idle|load",
  "budget": {
    "cpu_cores": 8.0,
    "memory_gb": 12.75
  },
  "actual": {
    "memory_total_mb": 1120,
    "memory_total_gb": 1.09,
    "efficiency_pct": 91
  },
  "services": [
    {
      "container": "k2-service",
      "cpu": "10.5%",
      "memory": "500MiB / 2GiB",
      "memory_pct": "25%"
    }
  ]
}
```

---

## Phase Measurements

| Phase | Date | CPU Budget | Memory Budget | Actual Memory | Efficiency | Status |
|-------|------|------------|---------------|---------------|------------|--------|
| [Phase 1](phase-1-baseline.json) | 2026-02-09 | 8.0 cores | 12.75 GB | 1.09 GB | 91% | ✅ Complete |
| Phase 2 | -- | TBD | TBD | -- | -- | ⬜ Not Started |
| Phase 3 | -- | TBD | TBD | -- | -- | ⬜ Not Started |
| Phase 4 | -- | TBD | TBD | -- | -- | ⬜ Not Started |
| Phase 5 | -- | TBD | TBD | -- | -- | ⬜ Not Started |
| Phase 6 | -- | TBD | TBD | -- | -- | ⬜ Not Started |
| Phase 7 | -- | TBD | TBD | -- | -- | ⬜ Not Started |

**Target**: 16 CPU cores / 40GB RAM total across all phases

---

## Measurement Commands

### Capture Current Metrics
```bash
# JSON format with timestamp
docker stats --no-stream --format \
  '{"container":"{{.Name}}","cpu":"{{.CPUPerc}}","memory":"{{.MemUsage}}","limit":"{{.MemLimit}}"}' \
  | jq -s "{phase: \"phase-N\", timestamp: \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", services: .}" \
  > phase-N-measurement.json
```

### View Historical Trend
```bash
# Compare memory across phases
jq -r '[.phase, .actual.memory_total_gb] | @tsv' phase-*.json

# Compare efficiency
jq -r '[.phase, .actual.efficiency_pct] | @tsv' phase-*.json
```

### Calculate Total Resources
```bash
# Sum memory across all services
jq '.services[] | .memory' phase-1-baseline.json | \
  awk -F'[/ ]' '{sum+=$1} END {print sum " total"}'
```

---

## Efficiency Targets

| Phase | Memory Target | CPU Target | Notes |
|-------|---------------|------------|-------|
| Phase 1 | < 13 GB | < 8 cores | Infrastructure baseline |
| Phase 2 | < 13 GB | < 8 cores | Redpanda only (no new services) |
| Phase 3 | < 15 GB | < 9 cores | ClickHouse added |
| Phase 4 | < 22 GB | < 12 cores | Before Spark removed |
| Phase 5 | < 20 GB | < 10 cores | After cold tier restructure |
| Phase 6 | < 20 GB | < 16 cores | Target achieved |
| Phase 7 | < 20 GB | < 16 cores | Validated in production |

---

## Analysis

### Phase 1 Results
- **Memory**: 1.09 GB actual vs 12.75 GB budget = **91% efficiency**
- **Reason**: Services at idle, minimal data, no processing load
- **Expected**: Memory will increase as data flows through pipeline
- **Action**: Monitor Phase 4 carefully (Spark removal = biggest savings)

### Key Metrics to Watch
1. **ClickHouse memory growth** (as data accumulates)
2. **Redpanda memory** (as topic count increases)
3. **Total CPU under load** (not just idle)
4. **Phase 4 delta** (Spark Streaming removal)

---

## Related Documentation
- [Phase Map](../README.md) - Overall v2 migration plan
- [Resource Budget ADR](../../decisions/platform-v2/ADR-010-resource-budget.md) - Budget strategy
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) - Rollback strategy

---

**Last Updated**: 2026-02-09
**Maintained By**: Platform Engineering
