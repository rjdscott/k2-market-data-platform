# Useful Commands - CLI Cheat Sheet

**Purpose**: Quick reference for demo operators

**Last Updated**: 2026-01-17

---

## Docker Commands

### Service Management

```bash
# Start all services
docker-compose up -d

# Stop all services
docker-compose down

# Stop and remove volumes (full cleanup)
docker-compose down -v

# Restart specific service
docker-compose restart kafka
docker-compose restart schema-registry

# Check service status
docker-compose ps

# View logs (all services)
docker-compose logs --follow

# View logs (specific service)
docker-compose logs kafka --follow
docker-compose logs schema-registry --follow
```

---

### Container Inspection

```bash
# List running containers
docker ps

# List all containers (including stopped)
docker ps -a

# Inspect container details
docker inspect <container_id>

# Check container resource usage
docker stats

# Execute command in container
docker exec -it <container_name> bash
```

---

### Logs and Debugging

```bash
# Tail logs (last 20 lines)
docker logs $(docker ps -q -f name=kafka) --tail 20

# Follow logs in real-time
docker logs $(docker ps -q -f name=binance) --follow

# Search logs for errors
docker logs $(docker ps -q -f name=kafka) 2>&1 | grep ERROR

# Export logs to file
docker logs $(docker ps -q -f name=kafka) > kafka.log 2>&1
```

---

## Demo Commands

### Environment Setup

```bash
# Full environment setup (first time)
uv sync --all-extras
docker-compose up -d
python demos/scripts/utilities/init_e2e_demo.py
python demos/scripts/validation/pre_demo_check.py

# Quick validation
python demos/scripts/validation/pre_demo_check.py

# Full validation (comprehensive)
python demos/scripts/validation/pre_demo_check.py --full
```

---

### Running Demos

```bash
# Interactive CLI demo (10-15 minutes)
python demos/scripts/execution/demo.py

# Quick CLI demo (2-3 minutes)
python demos/scripts/execution/demo.py --quick

# Clean variant (minimal output)
python demos/scripts/execution/demo_clean.py

# Executive notebook demo (12 minutes)
jupyter notebook demos/notebooks/executive-demo.ipynb

# Technical deep-dive notebook (30-40 minutes)
jupyter notebook demos/notebooks/technical-deep-dive.ipynb
```

---

### Environment Management

```bash
# Toggle demo mode
python demos/scripts/utilities/demo_mode.py --enable
python demos/scripts/utilities/demo_mode.py --disable

# Reset demo environment (full)
python demos/scripts/utilities/reset_demo.py

# Clean demo data only (faster)
python demos/scripts/utilities/clean_demo_data.py

# Re-initialize environment
python demos/scripts/utilities/init_e2e_demo.py
```

---

### Resilience Testing

```bash
# Quick degradation demo (2-3 minutes)
python demos/scripts/resilience/demo_degradation.py --quick

# Full degradation cycle (5-7 minutes)
python demos/scripts/resilience/demo_degradation.py

# Specific degradation level
python demos/scripts/resilience/demo_degradation.py --level GRACEFUL
```

---

## Kafka Commands

### Topic Management

```bash
# List topics
docker exec kafka kafka-topics \
  --list \
  --bootstrap-server localhost:9092

# Describe topic
docker exec kafka kafka-topics \
  --describe \
  --topic market.crypto.trades.binance \
  --bootstrap-server localhost:9092

# Create topic
docker exec kafka kafka-topics \
  --create \
  --topic market.crypto.trades.binance \
  --partitions 16 \
  --replication-factor 1 \
  --bootstrap-server localhost:9092

# Delete topic
docker exec kafka kafka-topics \
  --delete \
  --topic market.crypto.trades.binance \
  --bootstrap-server localhost:9092
```

---

### Consumer Groups

```bash
# List consumer groups
docker exec kafka kafka-consumer-groups \
  --list \
  --bootstrap-server localhost:9092

# Describe consumer group (check lag)
docker exec kafka kafka-consumer-groups \
  --describe \
  --group k2-consumer \
  --bootstrap-server localhost:9092

# Reset consumer group offsets (to beginning)
docker exec kafka kafka-consumer-groups \
  --reset-offsets \
  --to-earliest \
  --group k2-consumer \
  --topic market.crypto.trades.binance \
  --execute \
  --bootstrap-server localhost:9092
```

---

### Message Inspection

```bash
# Consume messages (from beginning)
docker exec kafka kafka-console-consumer \
  --topic market.crypto.trades.binance \
  --from-beginning \
  --max-messages 10 \
  --bootstrap-server localhost:9092

# Consume messages (latest only)
docker exec kafka kafka-console-consumer \
  --topic market.crypto.trades.binance \
  --bootstrap-server localhost:9092

# Produce test message
docker exec -i kafka kafka-console-producer \
  --topic market.crypto.trades.binance \
  --bootstrap-server localhost:9092
# Then type message and press Enter
```

---

## Schema Registry Commands

### Schema Management

```bash
# List all subjects
curl http://localhost:8081/subjects

# Get schema versions for subject
curl http://localhost:8081/subjects/market-data-v2-value/versions

# Get latest schema
curl http://localhost:8081/subjects/market-data-v2-value/versions/latest

# Get specific schema version
curl http://localhost:8081/subjects/market-data-v2-value/versions/1

# Delete schema subject (careful!)
curl -X DELETE http://localhost:8081/subjects/market-data-v2-value
```

---

### Compatibility Checking

```bash
# Get compatibility mode
curl http://localhost:8081/config

# Set compatibility mode (BACKWARD, FORWARD, FULL, NONE)
curl -X PUT -H "Content-Type: application/json" \
  --data '{"compatibility": "BACKWARD"}' \
  http://localhost:8081/config

# Test schema compatibility
curl -X POST -H "Content-Type: application/json" \
  --data @schema.json \
  http://localhost:8081/compatibility/subjects/market-data-v2-value/versions/latest
```

---

## API Commands

### Health Checks

```bash
# API health endpoint
curl http://localhost:8000/health

# API version
curl http://localhost:8000/version

# OpenAPI spec
curl http://localhost:8000/openapi.json
```

---

### Query Endpoints

```bash
# List available symbols
curl -H "X-API-Key: k2-dev-api-key-2026" \
  http://localhost:8000/v1/symbols

# Query trades (basic)
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=10"

# Query trades (time range)
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&start=2026-01-17T00:00:00&end=2026-01-17T23:59:59"

# Query trades (pagination)
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=100&offset=100"

# Aggregation query
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades/aggregate?symbol=BTCUSDT&interval=1h"
```

---

### Performance Testing

```bash
# Benchmark API latency (10 requests)
for i in {1..10}; do
  time curl -H "X-API-Key: k2-dev-api-key-2026" \
    "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=100" \
    -o /dev/null -s -w "%{time_total}\n"
done

# Concurrent requests (using Apache Bench)
ab -n 100 -c 10 \
  -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=100"
```

---

## Query Examples

### DuckDB Direct Queries

```bash
# Execute query directly (if DuckDB CLI available)
duckdb /path/to/k2.db << EOF
SELECT COUNT(*) FROM market_data;
EOF

# Query from Python
python << EOF
import duckdb
conn = duckdb.connect('/path/to/k2.db')
result = conn.execute('SELECT * FROM market_data LIMIT 10').fetchall()
print(result)
EOF
```

---

### Common Query Patterns

```sql
-- Time range query (most efficient)
SELECT * FROM market_data
WHERE exchange_date = '2026-01-17'
  AND symbol = 'BTCUSDT'
ORDER BY exchange_timestamp_ms DESC
LIMIT 100;

-- Aggregation query
SELECT
  symbol,
  DATE_TRUNC('hour', FROM_TIMESTAMP(exchange_timestamp_ms)) AS hour,
  COUNT(*) AS trade_count,
  AVG(price) AS avg_price
FROM market_data
WHERE exchange_date = '2026-01-17'
GROUP BY symbol, hour
ORDER BY hour DESC;

-- Multi-symbol query
SELECT * FROM market_data
WHERE exchange_date = '2026-01-17'
  AND symbol IN ('BTCUSDT', 'ETHUSDT')
ORDER BY exchange_timestamp_ms DESC
LIMIT 100;
```

---

## Monitoring Commands

### Prometheus

```bash
# Check Prometheus health
curl http://localhost:9090/-/healthy

# Query metric
curl 'http://localhost:9090/api/v1/query?query=up'

# Query metric with time range
curl 'http://localhost:9090/api/v1/query_range?query=k2_messages_ingested_total&start=2026-01-17T00:00:00Z&end=2026-01-17T23:59:59Z&step=60s'

# List all targets
curl http://localhost:9090/api/v1/targets
```

---

### Grafana

```bash
# Grafana health
curl http://localhost:3000/api/health

# List dashboards (requires auth)
curl -u admin:admin http://localhost:3000/api/search

# Export dashboard
curl -u admin:admin http://localhost:3000/api/dashboards/uid/<dashboard_uid>
```

---

### System Monitoring

```bash
# Check disk space
df -h

# Check memory usage
free -h

# Check CPU usage
top -bn1 | head -20

# Check network connections
netstat -tuln | grep -E '(9092|8081|8000)'

# Check port availability
lsof -i :9092  # Kafka
lsof -i :8081  # Schema Registry
lsof -i :8000  # API
```

---

## Troubleshooting Commands

### Port Conflicts

```bash
# Check what's using a port
lsof -i :9092

# Kill process using port
sudo lsof -ti:9092 | xargs kill -9

# Check all listening ports
netstat -tuln
```

---

### Service Recovery

```bash
# Restart single service
docker-compose restart kafka

# Full restart
docker-compose down && docker-compose up -d

# Clean restart (remove volumes)
docker-compose down -v && docker-compose up -d

# Check service logs for errors
docker-compose logs kafka 2>&1 | grep ERROR
```

---

### Data Validation

```bash
# Check Kafka has messages
docker exec kafka kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 \
  --topic market.crypto.trades.binance

# Check Schema Registry has schemas
curl http://localhost:8081/subjects | jq

# Check API returns data
curl -H "X-API-Key: k2-dev-api-key-2026" \
  http://localhost:8000/v1/symbols | jq

# Check Binance stream is running
docker logs $(docker ps -q -f name=binance) --tail 50 | grep -i error
```

---

### Performance Debugging

```bash
# Check query execution plan
EXPLAIN SELECT * FROM market_data WHERE exchange_date = '2026-01-17';

# Monitor query execution time
time curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=100"

# Check container resource usage
docker stats --no-stream

# Check system load
uptime
```

---

## Quick Workflows

### Cold Start to Running Demo

```bash
# 1. Start services
docker-compose up -d

# 2. Wait for health
sleep 10

# 3. Initialize
python demos/scripts/utilities/init_e2e_demo.py

# 4. Validate
python demos/scripts/validation/pre_demo_check.py

# 5. Run demo
python demos/scripts/execution/demo.py --quick
```

---

### Reset Between Demos

```bash
# Quick reset (data only)
python demos/scripts/utilities/clean_demo_data.py

# Full reset (services + data)
python demos/scripts/utilities/reset_demo.py

# Validate clean state
python demos/scripts/validation/pre_demo_check.py
```

---

### Debug Failing Demo

```bash
# 1. Check services
docker-compose ps

# 2. Check logs
docker-compose logs --tail 50

# 3. Check connectivity
curl http://localhost:8081/subjects
curl http://localhost:8000/health

# 4. Try targeted restart
docker-compose restart <failing_service>

# 5. Full reset if needed
python demos/scripts/utilities/reset_demo.py
```

---

## Emergency Recovery

### If Everything Fails

```bash
# Nuclear option: full cleanup
docker-compose down -v
docker system prune -af --volumes
uv sync --all-extras
docker-compose up -d
python demos/scripts/utilities/init_e2e_demo.py
python demos/scripts/validation/pre_demo_check.py
```

**Warning**: This deletes all Docker data, use as last resort

---

## Useful Aliases

Add to your `.bashrc` or `.zshrc`:

```bash
# Docker
alias dc='docker-compose'
alias dps='docker ps'
alias dlogs='docker-compose logs --follow'

# K2 Demo
alias k2-start='docker-compose up -d'
alias k2-stop='docker-compose down'
alias k2-check='python demos/scripts/validation/pre_demo_check.py'
alias k2-demo='python demos/scripts/execution/demo.py --quick'
alias k2-reset='python demos/scripts/utilities/reset_demo.py'

# Kafka
alias ktopics='docker exec kafka kafka-topics --list --bootstrap-server localhost:9092'
alias kgroups='docker exec kafka kafka-consumer-groups --list --bootstrap-server localhost:9092'

# API
alias k2-symbols='curl -H "X-API-Key: k2-dev-api-key-2026" http://localhost:8000/v1/symbols'
alias k2-health='curl http://localhost:8000/health'
```

---

**Last Updated**: 2026-01-17
**Print this**: Keep nearby during demos
**Questions?**: See [Quick Reference](./quick-reference.md) or [Demo README](../README.md)
