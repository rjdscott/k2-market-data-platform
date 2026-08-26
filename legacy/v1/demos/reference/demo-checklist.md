# Demo Checklist

**Purpose**: Print-ready pre-demo validation checklist

**Last Updated**: 2026-01-17

---

## 30 Minutes Before Demo

Print this checklist and work through it systematically.

---

## Environment Checks

### Docker Services

- [ ] All containers running: `docker-compose ps`
- [ ] Kafka healthy (Up status)
- [ ] Schema Registry healthy (Up status)
- [ ] No containers in "Restarting" state
- [ ] Port conflicts resolved (9092, 8081, 8000)

**Validation**:
```bash
docker-compose ps | grep "Up"
# Should show all services with "Up" status
```

---

### Kafka Validation

- [ ] Kafka accessible: `docker exec kafka kafka-topics --list --bootstrap-server localhost:9092`
- [ ] Topics exist: `market.crypto.trades.binance`
- [ ] Kafka UI accessible: http://localhost:8080 (if deployed)
- [ ] No ERROR logs in Kafka container
- [ ] Consumer groups visible

**Validation**:
```bash
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092
# Should list demo topics

docker logs $(docker ps -q -f name=kafka) --tail 20
# Should show no ERROR lines
```

---

### Schema Registry Validation

- [ ] Schema Registry accessible: `curl http://localhost:8081/subjects`
- [ ] Schemas registered: `market-data-v2-value`
- [ ] No ERROR logs in Schema Registry container
- [ ] Version endpoint responsive: `curl http://localhost:8081/config`

**Validation**:
```bash
curl http://localhost:8081/subjects
# Should return JSON list with "market-data-v2-value"

docker logs $(docker ps -q -f name=schema-registry) --tail 20
# Should show no ERROR lines
```

---

### DuckDB Validation

- [ ] DuckDB tables exist
- [ ] Tables have recent data (last 15 minutes)
- [ ] No corrupted database files
- [ ] Disk space available (>5GB free)

**Validation**:
```bash
python demos/scripts/validation/pre_demo_check.py --check duckdb
# Should report "✓ DuckDB initialized"
```

---

### API Validation

- [ ] API health endpoint: `curl http://localhost:8000/health`
- [ ] Swagger UI accessible: http://localhost:8000/docs
- [ ] Test query endpoint: `curl -H "X-API-Key: k2-dev-api-key-2026" http://localhost:8000/v1/symbols`
- [ ] Response time < 500ms
- [ ] No 500 errors in logs

**Validation**:
```bash
curl http://localhost:8000/health
# Should return {"status": "healthy"}

curl -H "X-API-Key: k2-dev-api-key-2026" http://localhost:8000/v1/symbols
# Should return JSON list of symbols (["BTCUSDT", "ETHUSDT"])
```

---

## Data Validation

### Data Freshness

- [ ] Recent messages in Kafka (last 5 minutes)
- [ ] Binance stream ingesting: `docker logs $(docker ps -q -f name=binance) --tail 20`
- [ ] Message rate visible (10-50 msg/sec typical)
- [ ] No stale data warnings

**Validation**:
```bash
docker logs $(docker ps -q -f name=binance) --tail 20
# Should show recent trade messages with timestamps

python demos/scripts/validation/pre_demo_check.py --check freshness
# Should report "✓ Data fresh (last message Xs ago)"
```

---

### Table Validation

- [ ] Iceberg tables exist: `market_data`
- [ ] Row counts > 0
- [ ] Partitions populated (check latest date partition)
- [ ] No missing partitions for recent dates

**Validation**:
```bash
# Query should return results
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=5"
```

---

### Query Performance

- [ ] Test time-range query: p50 < 200ms
- [ ] Test simple filter: p50 < 300ms
- [ ] Test API endpoint: p50 < 400ms
- [ ] No timeout errors (30s limit)

**Validation**:
```bash
# Should complete in < 1 second
time curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=100"
```

---

## Monitoring Validation

### Grafana

- [ ] Grafana accessible: http://localhost:3000
- [ ] Login working (admin/admin)
- [ ] Dashboards loading (K2 Platform Dashboard)
- [ ] Metrics visible (ingestion rate, query latency)
- [ ] No data source errors

**Validation**: Open http://localhost:3000 and verify dashboard displays data

---

### Prometheus

- [ ] Prometheus accessible: http://localhost:9090
- [ ] Targets healthy: http://localhost:9090/targets
- [ ] Test query: `k2_messages_ingested_total`
- [ ] Scrape errors = 0

**Validation**: Query `up` in Prometheus UI, should return 1 for all targets

---

## Timing Validation

### 12-Minute Runthrough

- [ ] Start services: < 1 minute
- [ ] Environment validation: < 1 minute
- [ ] Data accumulation: 2-3 minutes
- [ ] Query demonstrations: 3-4 minutes
- [ ] Monitoring review: 2-3 minutes
- [ ] Degradation demo (optional): 2-3 minutes
- [ ] Q&A buffer: 2-3 minutes

**Total**: Should complete in 12 minutes (practice to verify)

**Practice Run**:
```bash
# Time a quick demo run
time python demos/scripts/execution/demo.py --quick
# Should complete in < 3 minutes
```

---

## Materials Preparation

### Physical Materials

- [ ] Laptop charged 100%
- [ ] Power cable accessible
- [ ] Mouse/trackpad working
- [ ] External monitor cable (if presenting)
- [ ] Backup laptop ready (optional)

---

### Digital Materials

- [ ] Browser tabs open:
  - [ ] Grafana (http://localhost:3000)
  - [ ] Prometheus (http://localhost:9090)
  - [ ] API docs (http://localhost:8000/docs)
  - [ ] Kafka UI (http://localhost:8080) (if deployed)
- [ ] Terminal windows ready:
  - [ ] Docker logs: `docker-compose logs --follow`
  - [ ] System monitor: `htop` or Activity Monitor
- [ ] Quick reference printed:
  - [ ] Key metrics sheet
  - [ ] This checklist
  - [ ] Contingency plan
- [ ] Demo notebook open:
  - [ ] Executive demo (`executive-demo.ipynb`)
  - [ ] OR Technical deep-dive (`technical-deep-dive.ipynb`)

---

### Environment Settings

- [ ] Do Not Disturb enabled
- [ ] Notifications disabled (Slack, email, etc.)
- [ ] Screen saver disabled
- [ ] Sleep mode disabled
- [ ] WiFi stable (or ethernet connected)
- [ ] VPN disconnected (if causes latency)
- [ ] Unnecessary applications closed

---

## Key Metrics Review

Memorize these numbers for Q&A:

- [ ] API latency: p50 = 388ms, p99 = 681ms
- [ ] Time range scan: p50 = 161ms, p99 = 299ms
- [ ] Compression ratio: 10.0:1 (Parquet + Snappy)
- [ ] Cost: $0.85 per million messages at scale
- [ ] Savings: 35-40% vs Snowflake (~$25K/month)
- [ ] Current throughput: 138 msg/sec (single-node)
- [ ] Target scale: 1M msg/sec (multi-node)

**Review**: See [Key Metrics](./key-metrics.md) for full details

---

## Contingency Checks

### Backup Plans Ready

- [ ] Pre-executed notebook saved (with outputs)
- [ ] Screenshots of key system states
- [ ] Demo recording available (optional)
- [ ] Static slides as fallback
- [ ] Contingency plan printed: [Contingency Plan](./contingency-plan.md)

---

### Recovery Commands Ready

```bash
# Quick data reset
python demos/scripts/utilities/clean_demo_data.py

# Full environment reset
python demos/scripts/utilities/reset_demo.py

# Re-initialize
python demos/scripts/utilities/init_e2e_demo.py
```

---

## Final Validation

### 5 Minutes Before Demo

- [ ] Run automated validation: `python demos/scripts/validation/pre_demo_check.py --full`
- [ ] Check all items above passed
- [ ] Test notebook first cell executes
- [ ] Deep breath, you're ready

---

### If Any Check Fails

**Don't panic. Follow recovery steps:**

1. Identify failure category (Docker, Kafka, DuckDB, API, Data)
2. Check relevant logs: `docker logs <container>`
3. Try targeted restart: `docker-compose restart <service>`
4. If still failing, full reset: `python demos/scripts/utilities/reset_demo.py`
5. If reset fails, switch to contingency plan: [Contingency Plan](./contingency-plan.md)

---

## Post-Demo

### Immediate Actions

- [ ] Thank audience
- [ ] Collect questions for follow-up
- [ ] Save any interesting query results
- [ ] Note any issues encountered

---

### Cleanup

- [ ] Export demo data (if needed)
- [ ] Reset environment: `python demos/scripts/utilities/reset_demo.py`
- [ ] Close browser tabs
- [ ] Update this checklist with improvements

---

## Notes Section

Use this space to note any issues or improvements:

```
Date: _______________

Issues encountered:
1.
2.
3.

Improvements for next time:
1.
2.
3.

Questions asked:
1.
2.
3.
```

---

**Last Updated**: 2026-01-17
**Print this checklist**: Use before every demo
**Questions?**: See [Quick Reference](./quick-reference.md) or [Demo README](../README.md)
