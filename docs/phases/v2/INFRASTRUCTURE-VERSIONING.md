# Infrastructure Versioning & Rollback Strategy

**Status:** ⬜ NOT STARTED
**Last Updated:** 2026-02-09

---

## Overview

Every phase of the v2 migration produces a versioned docker-compose configuration that can be used for rollback. This document defines the versioning scheme, rollback procedures, and testing requirements.

The core principle: **at any point during the migration, we can revert to the last known-good state within 5 minutes.**

---

## Versioning Scheme

### Directory Structure

```
docker/
├── v1-baseline.yml              ← Snapshot of current v1 (tagged before any changes)
├── v2-phase-2-redpanda.yml      ← After Redpanda migration
├── v2-phase-3-clickhouse.yml    ← After ClickHouse foundation
├── v2-phase-4-pipeline.yml      ← After streaming pipeline migration
├── v2-phase-5-cold-tier.yml     ← After Iceberg restructure
├── v2-phase-6-handlers.yml      ← After Kotlin feed handlers
├── v2-phase-7-final.yml         ← Production v2
├── .env.v1                      ← v1 environment variables
├── .env.v2                      ← v2 environment variables
└── schemas/                     ← ClickHouse DDL versioned per phase
    ├── v2-phase-3-clickhouse.sql
    ├── v2-phase-4-pipeline.sql
    └── v2-phase-5-cold-tier.sql
```

### Git Tagging Convention

```bash
# Before any v2 work begins
git tag -a v1-stable -m "v1 platform: stable baseline before v2 migration"

# After each phase completes
git tag -a v2-phase-1-complete -m "Phase 1: infrastructure baseline tagged"
git tag -a v2-phase-2-complete -m "Phase 2: Redpanda migration complete"
git tag -a v2-phase-3-complete -m "Phase 3: ClickHouse foundation complete"
git tag -a v2-phase-4-complete -m "Phase 4: streaming pipeline migrated"
git tag -a v2-phase-5-complete -m "Phase 5: cold tier restructured"
git tag -a v2-phase-6-complete -m "Phase 6: Kotlin feed handlers live"
git tag -a v2-phase-7-complete -m "Phase 7: v2 hardened and validated"
```

### Docker Image Tagging

```bash
# Custom-built images (feed handlers, processors, API)
k2/feed-handlers:v1-python          # Current Python handlers
k2/feed-handlers:v2-kotlin          # New Kotlin handlers
k2/silver-processor:v2-phase-4      # Silver processor per phase
k2/api:v1-fastapi                   # Current FastAPI
k2/api:v2-springboot                # New Spring Boot (Phase 8)
k2/spark-batch:v2                   # Spark batch-only config
```

---

## Rollback Procedures

### Quick Rollback (< 5 minutes)

```bash
# Stop current stack
docker compose down

# Revert to previous phase
cp docker/v2-phase-{N-1}-*.yml docker-compose.yml
cp docker/.env.{version} .env

# Start previous version
docker compose up -d

# Verify services are healthy
docker compose ps
docker compose logs --tail=50
```

### Full Rollback to v1 (< 10 minutes)

```bash
# Stop everything
docker compose down

# Restore v1 baseline
cp docker/v1-baseline.yml docker-compose.yml
cp docker/.env.v1 .env

# Start v1
docker compose up -d

# Verify all v1 services are running
docker compose ps | grep -c "running"  # Should match expected service count
```

### Data Considerations During Rollback

| Component | Rollback Impact | Mitigation |
|-----------|----------------|------------|
| **Redpanda → Kafka** | Topics must be recreated in Kafka | Dual-run period: keep Kafka topics alive during Phase 2 |
| **ClickHouse data** | ClickHouse data is independent of v1 | ClickHouse container can be stopped without affecting v1 Iceberg data |
| **Iceberg tables** | Cold tier schema may have changed | Keep v1 Iceberg tables untouched; v2 tables use `cold.*` prefix |
| **Kotlin handlers** | Roll back to Python handlers | Keep Python handler images available; switch in docker-compose |
| **Consumer offsets** | Offsets may have advanced past v1 checkpoints | Redpanda/Kafka retains data for 7 days; consumers can re-read |

### Critical Rule: No Destructive Changes to v1 Data

During the migration, **never** delete, modify, or overwrite:
- Existing Kafka topics (until Phase 2 dual-run is validated)
- Existing Iceberg tables (v2 uses `cold.*` prefix namespace)
- Existing Spark checkpoint directories
- Existing Prefect flow history

v2 components run alongside v1 until explicitly decommissioned.

---

## Phase-by-Phase Versioning Checklist

### Before Each Phase Starts
- [ ] Previous phase git tag exists
- [ ] Previous docker-compose version saved to `docker/`
- [ ] `docker compose down && docker compose up -d` with previous version succeeds (rollback test)
- [ ] All data volumes are backed up (or confirmed recoverable)

### After Each Phase Completes
- [ ] New docker-compose saved to `docker/v2-phase-{N}-*.yml`
- [ ] New git tag created: `v2-phase-{N}-complete`
- [ ] Resource consumption measured: `docker stats --no-stream`
- [ ] All services healthy: `docker compose ps`
- [ ] Rollback to phase N-1 tested successfully
- [ ] Rollback to v1-baseline tested successfully (Phases 1-3 only)

---

## Resource Measurement Script

Run after each phase to capture actual consumption:

```bash
#!/bin/bash
# measure-resources.sh — run after each phase to log consumption
PHASE=${1:-"unknown"}
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
OUTFILE="docs/phases/v2/resource-measurements/${PHASE}.json"

mkdir -p docs/phases/v2/resource-measurements

echo "Measuring resources for phase: $PHASE at $TIMESTAMP"

docker stats --no-stream --format \
  '{"container":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}","mem_pct":"{{.MemPerc}}"}' \
  | jq -s "{phase: \"$PHASE\", timestamp: \"$TIMESTAMP\", services: .}" \
  > "$OUTFILE"

echo "Results saved to $OUTFILE"

# Summary
echo ""
echo "=== Phase $PHASE Resource Summary ==="
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | sort
echo ""
echo "Service count: $(docker compose ps --format json | jq -s 'length')"
```

---

## Dual-Run Validation Pattern

For critical migrations (Kafka → Redpanda, Python → Kotlin handlers), use a dual-run pattern:

```
Phase N (dual-run):
  Old component ──→ writes to validation topic
  New component ──→ writes to validation topic
  Comparator   ──→ reads both, alerts on divergence

Phase N+1 (cutover):
  Old component decommissioned
  New component promoted to primary
```

This adds temporary resource consumption but eliminates blind cutover risk.

---

**Last Updated:** 2026-02-09
**Owner:** Platform Engineering
