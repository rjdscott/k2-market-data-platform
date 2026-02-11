# K2 Platform - Quick Reference Cheat Sheet

**Version**: v2 (2026-02-09)

---

## Stack Management

```bash
# Start v2 stack + feed handlers
docker compose -p k2-v2 \
  -f docker-compose.v2.yml \
  -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml \
  up -d

# Stop all
docker compose -p k2-v2 \
  -f docker-compose.v2.yml \
  -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml \
  down

# View all services
docker compose -p k2-v2 \
  -f docker-compose.v2.yml \
  -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml \
  ps

# Rebuild feed handler
docker compose -p k2-v2 \
  -f docker-compose.v2.yml \
  -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml \
  up -d --build feed-handler-binance

# View logs
docker logs -f k2-feed-handler-binance
docker logs -f k2-redpanda
docker logs -f k2-clickhouse

# Check resources
docker stats
```

---

## Web UIs

| Service | URL | Credentials |
|---------|-----|-------------|
| Redpanda Console | http://localhost:8080 | None |
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | None |

---

## Redpanda Quick Commands

```bash
# List topics
docker exec k2-redpanda rpk topic list

# Describe topic
docker exec k2-redpanda rpk topic describe market.crypto.trades.binance

# Consume last 10 messages
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 10 --format '%v\n---\n'

# Tail topic (stream mode)
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --format '%v\n'

# Check consumer groups
docker exec k2-redpanda rpk group list

# Check consumer lag
docker exec k2-redpanda rpk group describe clickhouse_bronze_consumer

# List schemas
docker exec k2-redpanda curl -s http://localhost:8081/subjects | jq '.'

# Get latest schema
docker exec k2-redpanda curl -s \
  http://localhost:8081/subjects/market.crypto.trades.binance-value/versions/latest \
  | jq '.schema | fromjson'
```

---

## ClickHouse Quick Commands

```bash
# Interactive client
docker exec -it k2-clickhouse clickhouse-client

# One-liner query
docker exec k2-clickhouse clickhouse-client --query "<SQL>"

# Show tables
docker exec k2-clickhouse clickhouse-client --query "SHOW TABLES FROM default"

# Count trades (Binance)
docker exec k2-clickhouse clickhouse-client --query "
SELECT exchange, count(*) FROM default.bronze_trades_binance GROUP BY exchange
"

# Recent trades
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM default.silver_trades ORDER BY processed_at DESC LIMIT 10 FORMAT Pretty
"

# OHLCV bars
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM default.ohlcv_1m WHERE window_start >= now() - INTERVAL 1 HOUR
ORDER BY window_start DESC LIMIT 20 FORMAT Pretty
"

# Check Kafka consumers
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM system.kafka_consumers FORMAT Pretty
"

# Table sizes
docker exec k2-clickhouse clickhouse-client --query "
SELECT table, formatReadableSize(sum(bytes)) as size, sum(rows) as rows
FROM system.parts WHERE database = 'default' AND active
GROUP BY table ORDER BY sum(bytes) DESC FORMAT Pretty
"
```

---

## Feed Handler Quick Commands

```bash
# View metrics (last 60s)
docker logs --since 60s k2-feed-handler-binance | grep "Metrics"

# Check connection status
docker logs --since 60s k2-feed-handler-binance | grep -E "Connected|Subscribed"

# View errors only
docker logs k2-feed-handler-binance 2>&1 | grep -i "error\|exception"

# Restart feed handler
docker restart k2-feed-handler-binance

# View environment variables
docker exec k2-feed-handler-binance env | grep K2_

# Check Java process
docker exec k2-feed-handler-binance ps aux
```

---

## Health Checks

```bash
# All services healthy?
docker compose -p k2-v2 -f docker-compose.v2.yml ps | grep -v "healthy"

# Feed handler producing?
docker logs --since 60s k2-feed-handler-binance | grep "Metrics"

# Topics exist?
docker exec k2-redpanda rpk topic list | grep "market.crypto.trades"

# Messages in topics?
docker exec k2-redpanda rpk topic describe market.crypto.trades.binance -a

# ClickHouse receiving data?
docker exec k2-clickhouse clickhouse-client --query "SELECT count(*) FROM default.bronze_trades_binance"

# Redpanda Console accessible?
curl -s http://localhost:8080 | grep -q "redpanda" && echo "✓ Console OK" || echo "✗ Console down"
```

---

## Troubleshooting One-Liners

```bash
# Check container is running
docker ps --filter "name=k2-feed-handler-binance" --format "{{.Status}}"

# Check resource usage
docker stats --no-stream k2-feed-handler-binance

# Check network connectivity
docker exec k2-feed-handler-binance nc -zv redpanda 9092

# View last error
docker logs k2-feed-handler-binance 2>&1 | grep -i "error" | tail -5

# Check disk space
docker exec k2-clickhouse df -h

# Check ClickHouse is responsive
docker exec k2-clickhouse clickhouse-client --query "SELECT 1"

# View Redpanda cluster info
docker exec k2-redpanda rpk cluster info
```

---

## Data Export

```bash
# Export trades to CSV
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM default.silver_trades LIMIT 10000 FORMAT CSV
" > trades.csv

# Export trades to JSON
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM default.silver_trades LIMIT 10000 FORMAT JSONEachRow
" > trades.json

# Export OHLCV to CSV
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM default.ohlcv_1m WHERE window_start >= now() - INTERVAL 1 DAY FORMAT CSV
" > ohlcv_1d.csv

# Backup topic sample
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 1000 --format '%v\n' > backup_sample.jsonl
```

---

## Performance Monitoring

```bash
# Watch topic message rate
watch -n 1 'docker exec k2-redpanda rpk topic describe market.crypto.trades.binance -a | grep -A 3 "high watermark"'

# Watch feed handler metrics
watch -n 5 'docker logs --since 70s k2-feed-handler-binance 2>&1 | grep "Metrics" | tail -1'

# Watch ClickHouse row count
watch -n 5 'docker exec k2-clickhouse clickhouse-client --query "SELECT count(*) FROM default.bronze_trades_binance"'

# Watch consumer lag
watch -n 2 'docker exec k2-redpanda rpk group describe clickhouse_bronze_consumer'

# Watch container resources
watch -n 2 'docker stats --no-stream k2-feed-handler-binance k2-redpanda k2-clickhouse'
```

---

## Emergency Procedures

### Feed handler stuck/crashed
```bash
# View recent logs
docker logs --tail 100 k2-feed-handler-binance

# Restart
docker restart k2-feed-handler-binance

# Rebuild if config changed
docker compose -p k2-v2 -f docker-compose.v2.yml \
  -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml \
  up -d --build feed-handler-binance
```

### ClickHouse not ingesting
```bash
# Check Kafka consumers
docker exec k2-clickhouse clickhouse-client --query "SELECT * FROM system.kafka_consumers FORMAT Pretty"

# Restart materialized views
docker exec k2-clickhouse clickhouse-client --query "SYSTEM START VIEW default.bronze_trades_binance_mv"

# Check for errors
docker logs k2-clickhouse 2>&1 | grep -i "error" | tail -20
```

### High consumer lag
```bash
# Check lag
docker exec k2-redpanda rpk group describe clickhouse_bronze_consumer

# Check ClickHouse resources
docker stats k2-clickhouse

# Increase parallelism (edit docker-compose, then restart)
# Or temporarily pause feed handler
docker pause k2-feed-handler-binance
```

### Out of disk space
```bash
# Check usage
docker system df

# Clean old data
docker system prune -a --volumes

# Check ClickHouse data
docker exec k2-clickhouse du -sh /var/lib/clickhouse/

# Adjust TTL or retention in ClickHouse tables
```

---

## Common Workflows

### Adding a new symbol to Binance feed handler
1. Edit `services/feed-handler-kotlin/docker-compose.feed-handlers.yml`
2. Update `K2_SYMBOLS` env var: `K2_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT`
3. Restart: `docker restart k2-feed-handler-binance`

### Checking end-to-end data flow
```bash
# 1. Feed handler producing?
docker logs --since 60s k2-feed-handler-binance | grep "Metrics"

# 2. Messages in topics?
docker exec k2-redpanda rpk topic describe market.crypto.trades.binance -a

# 3. ClickHouse ingesting?
docker exec k2-clickhouse clickhouse-client --query "SELECT count(*) FROM default.bronze_trades_binance"

# 4. OHLCV aggregations working?
docker exec k2-clickhouse clickhouse-client --query "SELECT count(*) FROM default.ohlcv_1m"
```

### Debugging schema issues
```bash
# 1. Check registered schemas
docker exec k2-redpanda curl -s http://localhost:8081/subjects | jq '.'

# 2. View latest schema
docker exec k2-redpanda curl -s \
  http://localhost:8081/subjects/market.crypto.trades.binance-value/versions/latest \
  | jq '.schema | fromjson'

# 3. Check feed handler can access schema registry
docker exec k2-feed-handler-binance curl -s http://redpanda:8081/subjects | jq '.'

# 4. View schema errors in feed handler
docker logs k2-feed-handler-binance 2>&1 | grep -i "schema"
```

---

## Useful Aliases

Add to `~/.bashrc` or `~/.zshrc`:

```bash
# K2 Platform aliases
alias k2-up='docker compose -p k2-v2 -f docker-compose.v2.yml -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml up -d'
alias k2-down='docker compose -p k2-v2 -f docker-compose.v2.yml -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml down'
alias k2-ps='docker compose -p k2-v2 -f docker-compose.v2.yml -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml ps'
alias k2-logs='docker logs -f k2-feed-handler-binance'
alias k2-metrics='docker logs --since 60s k2-feed-handler-binance | grep "Metrics"'
alias k2-topics='docker exec k2-redpanda rpk topic list'
alias k2-ch='docker exec -it k2-clickhouse clickhouse-client'
alias k2-console='echo "http://localhost:8080"'
```

---

## See Also

- [DATA-INSPECTION.md](./DATA-INSPECTION.md) - Comprehensive inspection guide
- [docker-compose.v2.yml](../../docker-compose.v2.yml) - Infrastructure configuration
- [ADR-001](../decisions/platform-v2/ADR-001-redpanda-over-kafka.md) - Why Redpanda
- [ARCHITECTURE-V2.md](../ARCHITECTURE-V2.md) - System architecture
