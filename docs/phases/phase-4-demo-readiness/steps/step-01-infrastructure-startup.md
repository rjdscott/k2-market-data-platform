# Step 01: Infrastructure Startup & Health Validation

**Status**: ⬜ Not Started
**Priority**: 🔴 CRITICAL
**Estimated Time**: 30-45 minutes
**Dependencies**: None (blocks all other steps)
**Last Updated**: 2026-01-14

---

## Goal

Get all Docker services running, healthy, and actively ingesting data from Binance WebSocket stream.

**Why Critical**: Without running infrastructure, no validation, testing, or demonstration is possible. This step blocks all subsequent work.

---

## Deliverables

1. ✅ All Docker services running (`docker compose ps` shows "Up" for all)
2. ✅ Binance WebSocket stream actively ingesting (logs show "Trade received")
3. ✅ Kafka topics created and receiving messages
4. ✅ Iceberg tables initialized with data (>500 rows in trades_v2)
5. ✅ Prometheus scraping metrics (83 metrics visible)
6. ✅ Grafana showing dashboards with data
7. ✅ API server responding to health checks
8. ✅ Health validation script created

---

## Implementation

### 1. Start All Services

```bash
# Navigate to project root
cd /Users/rjdscott/Documents/code/k2-market-data-platform

# Start all services in detached mode
docker compose up -d

# Wait for services to initialize
sleep 30
```

### 2. Verify All Services Running

```bash
# Check all services are "Up"
docker compose ps

# Expected output: 7+ services with status "Up"
# - k2-kafka
# - k2-schema-registry
# - k2-minio
# - k2-postgres
# - k2-iceberg-rest
# - k2-prometheus
# - k2-grafana
# - k2-binance-stream
# - k2-api (if running)
```

### 3. Monitor Binance Stream

```bash
# Check Binance stream is ingesting trades
docker logs k2-binance-stream --tail 50 | grep "Trade"

# Should see recent trades like:
# "Trade received: BTCUSDT @ 42123.45"
# "Trade received: ETHUSDT @ 2234.56"

# Monitor in real-time (optional)
docker logs k2-binance-stream --follow
```

### 4. Verify Kafka Messages

```bash
# Check Kafka topic has messages
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market-data.trades.v2 \
  --max-messages 5 \
  --timeout-ms 10000

# Should see 5 Avro-encoded messages
```

### 5. Check Iceberg Data

```bash
# Query API to verify data in Iceberg
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq '.data | length'

# Should return >0

# Check recent trades
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=5" | jq '.'

# Should return 5 trades
```

### 6. Validate Prometheus

```bash
# Check Prometheus health
curl http://localhost:9090/-/healthy

# Expected: "Prometheus Server is Healthy."

# Query a sample metric
curl http://localhost:9090/api/v1/query?query=k2_kafka_messages_produced_total | jq '.'

# Should return value >0
```

### 7. Validate Grafana

```bash
# Check Grafana health
curl http://localhost:3000/api/health

# Expected: HTTP 200 with {"database":"ok"}

# Access Grafana UI
# Open: http://localhost:3000
# Login: admin / admin
# Verify dashboards load with data
```

### 8. Let Data Accumulate

**Important**: Let services run for at least 10-15 minutes to accumulate sufficient data for demo.

```bash
# Monitor accumulation
watch -n 30 'curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq ".data | length"'

# Target: >1000 rows in Iceberg for robust demo
```

---

## Validation Commands

Run all validation commands to confirm readiness:

```bash
#!/bin/bash
# Infrastructure Health Validation

echo "=== Step 01 Validation ==="

echo -e "\n1. Services Running:"
docker compose ps | grep -c "Up"
# Expected: 7+

echo -e "\n2. Binance Stream Active:"
docker logs k2-binance-stream --tail 20 | grep -c "Trade"
# Expected: >0

echo -e "\n3. Kafka Messages:"
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market-data.trades.v2 \
  --max-messages 1 \
  --timeout-ms 5000 > /dev/null && echo "✓ Kafka has messages"

echo -e "\n4. Iceberg Data:"
SYMBOL_COUNT=$(curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/symbols" | jq '.data | length')
echo "Symbols in Iceberg: $SYMBOL_COUNT"
# Expected: >0

echo -e "\n5. Prometheus Health:"
curl -s http://localhost:9090/-/healthy

echo -e "\n6. Grafana Health:"
curl -s http://localhost:3000/api/health | jq '.database'

echo -e "\n7. API Health:"
curl -s -H "X-API-Key: k2-dev-api-key-2026" \
  http://localhost:8000/health | jq '.status'
# Expected: "healthy"

echo -e "\n=== Validation Complete ==="
```

---

## Success Criteria

**15/15 points** — Infrastructure Readiness

- [ ] All services show "Up" status (docker compose ps)
- [ ] Binance stream ingesting (20+ trades/minute visible in logs)
- [ ] Kafka topic has 100+ messages
- [ ] Iceberg has >500 rows in trades_v2
- [ ] Prometheus scraping metrics (83 metrics visible)
- [ ] Grafana dashboards loading with data
- [ ] API /health endpoint returns 200 with status "healthy"
- [ ] No service restarts or crash loops in last 10 minutes
- [ ] All 5 symbols present (BTCUSDT, ETHUSDT, BNBUSDT, ADAUSDT, DOGEUSDT)

---

## Troubleshooting

### Issue: Services Won't Start

```bash
# Check Docker daemon running
docker info

# Check ports not already in use
lsof -i :8000  # API
lsof -i :9092  # Kafka
lsof -i :9090  # Prometheus
lsof -i :3000  # Grafana

# Clean start if needed
docker compose down -v
docker compose up -d
```

### Issue: Binance Stream Not Ingesting

```bash
# Check container logs
docker logs k2-binance-stream --tail 100

# Common issues:
# - Network connectivity
# - API rate limits
# - Invalid symbols

# Restart stream only
docker compose restart k2-binance-stream
```

### Issue: No Data in Iceberg

```bash
# Check consumer is running
docker compose ps k2-consumer

# Check consumer logs
docker logs k2-consumer --tail 100

# Verify Kafka has messages
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market-data.trades.v2 \
  --from-beginning \
  --max-messages 10
```

---

## Next Steps

Once all validation passes:

1. **Mark Step 01 complete** in PROGRESS.md
2. **Let services run** for 10-15 minutes to accumulate data
3. **Proceed to Step 02**: Dry Run Validation (requires Step 01 complete)
4. **Optional parallel work**: Step 04 (Quick Reference) can start while data accumulates

---

## Demo Talking Points

When discussing infrastructure during demo:

> "Let me show you the live system. We have 7 services running in Docker Compose:
> 
> **Data Ingestion**:
> - Binance WebSocket stream pulling live trades for 5 symbols
> - Kafka broker with Schema Registry for Avro serialization
> - Currently ingesting at ~20 trades/minute (demo scale)
> 
> **Storage & Processing**:
> - PostgreSQL for Iceberg metadata catalog
> - MinIO for object storage (S3-compatible)
> - Iceberg REST catalog for table management
> 
> **Observability**:
> - Prometheus scraping 83 metrics every 15 seconds
> - Grafana dashboards showing real-time degradation state
> 
> **Query Interface**:
> - FastAPI server with hybrid query engine
> - DuckDB for analytical queries over Iceberg
> 
> All services are healthy, data is flowing, and we have >1000 messages 
> in Iceberg for the queries we'll run later."

---

**Last Updated**: 2026-01-14
