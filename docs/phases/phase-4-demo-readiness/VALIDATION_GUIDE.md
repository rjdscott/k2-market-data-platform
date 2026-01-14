# Phase 4 Validation Guide

**Purpose**: Step-by-step validation commands for each phase step
**Last Updated**: 2026-01-14

---

## Step Validation Commands

### Step 01: Infrastructure Startup

```bash
# All services healthy
docker compose ps | grep -c "Up"  # Should be 7+

# Binance stream active
docker logs k2-binance-stream --tail 20 | grep -c "Trade"  # Should be >0

# Kafka has messages
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market-data.trades.v2 --max-messages 5

# Iceberg has data
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq '.data | length'  # Should be >0

# Prometheus healthy
curl http://localhost:9090/-/healthy

# Grafana responding
curl http://localhost:3000/api/health

# API healthy
curl -H "X-API-Key: k2-dev-api-key-2026" \
  http://localhost:8000/health | jq .status  # Should be "healthy"
```

### Step 02: Dry Run Validation

```bash
# Execute notebook and verify no errors
jupyter nbconvert --execute --to notebook \
  notebooks/binance-demo.ipynb \
  --output /tmp/test-output.ipynb

# Should complete without errors in <12 minutes
```

### Step 03: Performance Benchmarking

```bash
# Run benchmarks
python scripts/performance_benchmark.py --validate

# Verify results documented
ls docs/phases/phase-4-demo-readiness/reference/performance-results.md
```

### Step 04: Quick Reference

```bash
# Verify created and fits on one page
ls docs/phases/phase-4-demo-readiness/reference/demo-quick-reference.md
wc -l docs/phases/phase-4-demo-readiness/reference/demo-quick-reference.md
# Should be <200 lines
```

### Step 05: Resilience Demo

```bash
# Test simulation
python scripts/simulate_failure.py --scenario lag
sleep 5

# Check degradation level increased
curl http://localhost:9090/api/v1/query?query=k2_degradation_level | \
  jq '.data.result[0].value[1]'  # Should show level > 0

# Test recovery
python scripts/simulate_failure.py --scenario recover
```

### Step 06: Architecture Decisions

```bash
# Verify document exists
ls docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md

# Check has multiple "Why X vs Y" sections
grep -c "Why.*vs" \
  docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md
# Should be >5
```

### Step 07: Backup Plans

```bash
# Verify all backup materials exist
ls docs/phases/phase-4-demo-readiness/reference/demo-recording.mp4
ls notebooks/binance-demo-with-outputs.ipynb
ls docs/phases/phase-4-demo-readiness/reference/screenshots/
python scripts/demo_mode.py --help
```

### Step 08: Visual Enhancements (Optional)

```bash
# Check visuals created
ls docs/architecture/images/latency-tiers.png
grep -c "mermaid" docs/architecture/system-design.md
```

### Step 09: Dress Rehearsal

```bash
# Run full pre-demo validation
python scripts/pre_demo_check.py
# Should pass all checks
```

### Step 10: Demo Day Checklist

```bash
# Full infrastructure and backup validation
python scripts/pre_demo_check.py --full
# Should pass all checks
```

---

## Full Validation (Run Before Demo)

```bash
#!/bin/bash
# Complete validation script

echo "=== Phase 4 Full Validation ==="

echo "\n1. Infrastructure Health"
docker compose ps | grep -c "Up"

echo "\n2. Binance Stream Active"
docker logs k2-binance-stream --tail 10 | grep -c "Trade"

echo "\n3. Data Available"
curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  http://localhost:8000/v1/symbols | jq '.data | length'

echo "\n4. Prometheus Metrics"
curl -s http://localhost:9090/-/healthy

echo "\n5. Dry Run Test"
jupyter nbconvert --execute --to notebook \
  notebooks/binance-demo.ipynb --output /tmp/validation-test.ipynb

echo "\n6. Backup Materials"
ls docs/phases/phase-4-demo-readiness/reference/

echo "\n=== Validation Complete ==="
```

---

**Last Updated**: 2026-01-14
