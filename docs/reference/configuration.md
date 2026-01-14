# K2 Configuration Reference

**Last Updated**: 2026-01-14
**Applies To**: All K2 Platform Components
**Environment**: Local Development & Production

---

## Overview

This document provides a comprehensive reference for all configuration parameters in the K2 Market Data Platform. Configuration is managed through environment variables, YAML files, and command-line arguments.

### Configuration Hierarchy

1. **Command-line arguments** (highest priority)
2. **Environment variables**
3. **Configuration files** (`.env`, `config.yaml`)
4. **Default values** (lowest priority)

---

## Quick Reference by Component

| Component | Config File | Environment Prefix | Port |
|-----------|-------------|-------------------|------|
| Kafka | docker-compose.yml | `KAFKA_` | 9092 |
| Schema Registry | docker-compose.yml | `SCHEMA_REGISTRY_` | 8081 |
| API | .env | `API_`, `FASTAPI_` | 8000 |
| Binance Client | .env | `BINANCE_` | N/A |
| Consumer | .env | `CONSUMER_`, `ICEBERG_` | N/A |
| Prometheus | prometheus.yml | N/A | 9090 |
| Grafana | grafana.ini | `GF_` | 3000 |

---

## Kafka Configuration

### Connection Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `KAFKA_BOOTSTRAP_SERVERS` | string | `localhost:9092` | Comma-separated list of broker addresses |
| `KAFKA_NODE_ID` | integer | `1` | Unique node ID for KRaft mode |
| `KAFKA_PROCESS_ROLES` | string | `broker,controller` | KRaft roles (broker, controller, or both) |
| `CLUSTER_ID` | string | `k2-market-data-cluster` | Unique cluster identifier |

**Example**:
```bash
KAFKA_BOOTSTRAP_SERVERS=kafka1:9092,kafka2:9092,kafka3:9092
```

### Performance Tuning

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `KAFKA_NUM_PARTITIONS` | integer | `6` | Default partitions per topic |
| `KAFKA_DEFAULT_REPLICATION_FACTOR` | integer | `1` (dev), `3` (prod) | Replication factor for durability |
| `KAFKA_MIN_INSYNC_REPLICAS` | integer | `1` (dev), `2` (prod) | Min replicas for ack |
| `KAFKA_LOG_RETENTION_HOURS` | integer | `168` | Message retention (7 days) |
| `KAFKA_LOG_SEGMENT_BYTES` | integer | `1073741824` | Segment size (1GB) |
| `KAFKA_COMPRESSION_TYPE` | string | `lz4` | Compression (lz4, gzip, snappy, zstd) |
| `KAFKA_HEAP_OPTS` | string | `-Xmx2G -Xms2G` | JVM heap size |

**Production Recommendations**:
```yaml
KAFKA_DEFAULT_REPLICATION_FACTOR: 3
KAFKA_MIN_INSYNC_REPLICAS: 2
KAFKA_LOG_RETENTION_HOURS: 2160  # 90 days
KAFKA_HEAP_OPTS: "-Xmx8G -Xms8G"
```

### Consumer Group Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `KAFKA_OFFSETS_TOPIC_NUM_PARTITIONS` | integer | `3` | Internal offsets topic partitions |
| `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR` | integer | `1` (dev), `3` (prod) | Offsets topic replication |
| `KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS` | integer | `30000` | Delay before first rebalance (30s) |
| `KAFKA_OFFSETS_RETENTION_MINUTES` | integer | `10080` | Offset retention (7 days) |

---

## Schema Registry Configuration

### Connection Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SCHEMA_REGISTRY_URL` | string | `http://localhost:8081` | Schema Registry HTTP endpoint |
| `SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS` | string | `kafka:29092` | Kafka brokers for schema storage |
| `SCHEMA_REGISTRY_HOST_NAME` | string | `schema-registry` | Hostname for Schema Registry |

### Compatibility Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SCHEMA_REGISTRY_SCHEMA_COMPATIBILITY_LEVEL` | string | `BACKWARD` | Global compatibility mode |

**Compatibility Modes**:
- `BACKWARD`: New schema can read data written with old schema (recommended)
- `FORWARD`: Old schema can read data written with new schema
- `FULL`: Both backward and forward compatible
- `NONE`: No compatibility checks (NOT recommended)

---

## API Configuration

### Server Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `API_HOST` | string | `0.0.0.0` | API server bind address |
| `API_PORT` | integer | `8000` | API server port |
| `API_WORKERS` | integer | `4` | Number of Uvicorn workers |
| `API_LOG_LEVEL` | string | `info` | Log level (debug, info, warning, error) |
| `API_RELOAD` | boolean | `false` | Auto-reload on code changes (dev only) |

**Example**:
```bash
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=4
API_LOG_LEVEL=info
```

### Authentication

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `API_KEY` | string | None | API key for authentication (Phase 1-2: not enforced) |
| `API_KEY_HEADER` | string | `X-API-Key` | Header name for API key |
| `JWT_SECRET` | string | None | JWT secret for token signing (Phase 3+) |
| `JWT_ALGORITHM` | string | `HS256` | JWT signing algorithm |
| `JWT_EXPIRATION_MINUTES` | integer | `60` | JWT token expiration (Phase 3+) |

### Rate Limiting

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `RATE_LIMIT_PER_MINUTE` | integer | `100` | Max requests per minute per key |
| `RATE_LIMIT_BURST` | integer | `10` | Burst allowance above limit |
| `RATE_LIMIT_ENABLED` | boolean | `true` | Enable/disable rate limiting |

### Query Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `QUERY_TIMEOUT_SECONDS` | integer | `30` | Max query execution time |
| `MAX_RESULT_ROWS` | integer | `10000` | Max rows per query response |
| `DEFAULT_PAGE_SIZE` | integer | `100` | Default pagination size |

---

## Binance Client Configuration

### Connection Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BINANCE_WS_URL` | string | `wss://stream.binance.com:9443/ws` | Binance WebSocket endpoint |
| `BINANCE_SYMBOLS` | string | `BTCUSDT,ETHUSDT,BNBUSDT` | Comma-separated symbol list |
| `BINANCE_HEARTBEAT_INTERVAL` | integer | `180` | Ping interval in seconds (3 min) |

**Example**:
```bash
BINANCE_WS_URL=wss://stream.binance.com:9443/ws
BINANCE_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT,ADAUSDT,SOLUSDT
BINANCE_HEARTBEAT_INTERVAL=180
```

### Reconnection Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BINANCE_RECONNECT_DELAY_MIN` | integer | `1` | Min reconnect delay (seconds) |
| `BINANCE_RECONNECT_DELAY_MAX` | integer | `60` | Max reconnect delay (seconds) |
| `BINANCE_MAX_RECONNECT_ATTEMPTS` | integer | `0` | Max reconnects (0 = infinite) |

### Message Processing

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BINANCE_BATCH_SIZE` | integer | `100` | Messages per batch to Kafka |
| `BINANCE_BATCH_TIMEOUT` | float | `1.0` | Max batch wait time (seconds) |
| `BINANCE_DLQ_PATH` | string | `/tmp/dlq` | Dead-letter queue directory |

---

## Consumer Configuration

### Kafka Consumer Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CONSUMER_GROUP_ID` | string | `k2-iceberg-writer-crypto-v2` | Consumer group ID |
| `CONSUMER_TOPICS` | string | `market.crypto.trades.binance` | Comma-separated topics |
| `CONSUMER_AUTO_OFFSET_RESET` | string | `earliest` | Offset reset (earliest, latest) |
| `CONSUMER_MAX_POLL_RECORDS` | integer | `500` | Max records per poll |
| `CONSUMER_MAX_POLL_INTERVAL_MS` | integer | `300000` | Max poll interval (5 min) |

**Example**:
```bash
CONSUMER_GROUP_ID=k2-iceberg-writer-crypto-v2
CONSUMER_TOPICS=market.crypto.trades.binance,market.equities.trades.asx
CONSUMER_AUTO_OFFSET_RESET=earliest
```

### Iceberg Writer Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ICEBERG_CATALOG_URI` | string | `http://localhost:8181` | Iceberg REST catalog URL |
| `ICEBERG_WAREHOUSE` | string | `s3://warehouse` | Iceberg warehouse location |
| `ICEBERG_NAMESPACE` | string | `k2` | Iceberg namespace (database) |
| `ICEBERG_TABLE_TRADES` | string | `trades_v2` | Trades table name |
| `ICEBERG_TABLE_QUOTES` | string | `quotes_v2` | Quotes table name |

### Batch Processing

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ICEBERG_BATCH_SIZE` | integer | `1000` | Records per batch write |
| `ICEBERG_BATCH_TIMEOUT_SECONDS` | integer | `10` | Max batch wait time |
| `ICEBERG_COMMIT_INTERVAL_SECONDS` | integer | `60` | Commit interval for checkpointing |

---

## DuckDB Configuration

### Connection Pool

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DUCKDB_PATH` | string | `:memory:` | Database file path (`:memory:` for in-memory) |
| `DUCKDB_POOL_MIN_SIZE` | integer | `5` | Minimum connection pool size |
| `DUCKDB_POOL_MAX_SIZE` | integer | `50` | Maximum connection pool size |
| `DUCKDB_CONNECTION_TIMEOUT` | integer | `30` | Connection timeout (seconds) |

**Production Recommendations**:
```bash
DUCKDB_PATH=/data/k2.duckdb
DUCKDB_POOL_MIN_SIZE=10
DUCKDB_POOL_MAX_SIZE=100
DUCKDB_CONNECTION_TIMEOUT=60
```

### Query Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DUCKDB_MEMORY_LIMIT` | string | `16GB` | Max memory per query |
| `DUCKDB_THREADS` | integer | `4` | Threads per query |
| `DUCKDB_TEMP_DIRECTORY` | string | `/tmp/duckdb` | Temporary file directory |

---

## MinIO Configuration (S3-Compatible Storage)

### Connection Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `S3_ENDPOINT` | string | `http://localhost:9000` | MinIO/S3 endpoint |
| `S3_ACCESS_KEY_ID` | string | `minioadmin` | Access key ID |
| `S3_SECRET_ACCESS_KEY` | string | `minioadmin` | Secret access key |
| `S3_BUCKET` | string | `warehouse` | Bucket name for Iceberg data |
| `S3_REGION` | string | `us-east-1` | Region (for AWS S3) |

**Production (AWS S3)**:
```bash
S3_ENDPOINT=https://s3.us-east-1.amazonaws.com
S3_ACCESS_KEY_ID=AKIA...
S3_SECRET_ACCESS_KEY=secret...
S3_BUCKET=k2-production-warehouse
S3_REGION=us-east-1
```

---

## Prometheus Configuration

### Scrape Settings

Configuration file: `config/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s      # Scrape targets every 15 seconds
  evaluation_interval: 15s  # Evaluate rules every 15 seconds

scrape_configs:
  - job_name: 'k2-api'
    static_configs:
      - targets: ['api:8000']
    metrics_path: '/metrics'

  - job_name: 'kafka'
    static_configs:
      - targets: ['kafka:9997']
    metrics_path: '/metrics'
```

### Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PROMETHEUS_PORT` | integer | `9090` | Prometheus server port |
| `PROMETHEUS_RETENTION_TIME` | string | `30d` | Metrics retention period |
| `PROMETHEUS_STORAGE_PATH` | string | `/prometheus` | Data storage path |

---

## Grafana Configuration

### Server Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GF_SERVER_HTTP_PORT` | integer | `3000` | Grafana server port |
| `GF_SERVER_ROOT_URL` | string | `http://localhost:3000` | Public URL |
| `GF_SECURITY_ADMIN_USER` | string | `admin` | Admin username |
| `GF_SECURITY_ADMIN_PASSWORD` | string | `admin` | Admin password (CHANGE IN PRODUCTION) |

**Production**:
```bash
GF_SECURITY_ADMIN_PASSWORD=<strong-password>
GF_AUTH_ANONYMOUS_ENABLED=false
GF_SECURITY_SECRET_KEY=<random-secret>
```

### Datasources

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GF_DATASOURCES_DEFAULT_URL` | string | `http://prometheus:9090` | Prometheus datasource URL |

---

## Logging Configuration

### Global Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOG_LEVEL` | string | `INFO` | Global log level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FORMAT` | string | `json` | Log format (json, text) |
| `LOG_FILE_PATH` | string | `/var/log/k2` | Log file directory |
| `LOG_FILE_MAX_SIZE_MB` | integer | `100` | Max log file size before rotation |
| `LOG_FILE_BACKUP_COUNT` | integer | `7` | Number of rotated logs to keep |

**Example**:
```bash
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_FILE_PATH=/var/log/k2
LOG_FILE_MAX_SIZE_MB=100
LOG_FILE_BACKUP_COUNT=7
```

### Component-Specific Logging

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `API_LOG_LEVEL` | string | `INFO` | API server log level |
| `CONSUMER_LOG_LEVEL` | string | `INFO` | Consumer log level |
| `BINANCE_LOG_LEVEL` | string | `INFO` | Binance client log level |

---

## Metrics Configuration

### Prometheus Metrics

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `METRICS_ENABLED` | boolean | `true` | Enable/disable metrics collection |
| `METRICS_PORT` | integer | `9090` | Metrics exposition port |
| `METRICS_PATH` | string | `/metrics` | Metrics endpoint path |

### Custom Metrics

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `METRICS_HISTOGRAM_BUCKETS` | string | `0.001,0.01,0.1,0.5,1,5,10` | Histogram buckets (seconds) |
| `METRICS_LABELS_ENABLED` | boolean | `true` | Include labels in metrics |

---

## Environment-Specific Configurations

### Development (.env.development)

```bash
# Kafka
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_LOG_RETENTION_HOURS=168

# API
API_PORT=8000
API_WORKERS=1
API_LOG_LEVEL=debug
RATE_LIMIT_ENABLED=false

# DuckDB
DUCKDB_PATH=:memory:
DUCKDB_POOL_MAX_SIZE=10

# Logging
LOG_LEVEL=DEBUG
LOG_FORMAT=text
```

### Production (.env.production)

```bash
# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka1:9092,kafka2:9092,kafka3:9092
KAFKA_DEFAULT_REPLICATION_FACTOR=3
KAFKA_MIN_INSYNC_REPLICAS=2
KAFKA_LOG_RETENTION_HOURS=2160

# API
API_PORT=8000
API_WORKERS=8
API_LOG_LEVEL=info
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=100

# DuckDB
DUCKDB_PATH=/data/k2.duckdb
DUCKDB_POOL_MAX_SIZE=100
DUCKDB_MEMORY_LIMIT=32GB

# S3
S3_ENDPOINT=https://s3.us-east-1.amazonaws.com
S3_BUCKET=k2-production-warehouse

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_FILE_PATH=/var/log/k2
```

---

## Configuration Examples

### Starting Components with Custom Configuration

**API Server**:
```bash
API_PORT=8080 \
API_WORKERS=4 \
RATE_LIMIT_PER_MINUTE=200 \
uv run uvicorn k2.api.main:app --host 0.0.0.0 --port 8080 --workers 4
```

**Binance Client**:
```bash
BINANCE_SYMBOLS=BTCUSDT,ETHUSDT \
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
uv run python src/k2/ingestion/binance_client.py
```

**Consumer**:
```bash
CONSUMER_GROUP_ID=k2-writer-1 \
CONSUMER_TOPICS=market.crypto.trades.binance \
ICEBERG_BATCH_SIZE=2000 \
uv run python src/k2/ingestion/consumer.py
```

---

## Configuration Validation

### Checking Current Configuration

```bash
# View all K2-related environment variables
env | grep -E "KAFKA_|API_|BINANCE_|CONSUMER_|ICEBERG_|DUCKDB_" | sort

# Validate Kafka connection
docker compose exec kafka kafka-broker-api-versions --bootstrap-server localhost:9092

# Check Schema Registry
curl http://localhost:8081/subjects

# Check API health
curl http://localhost:8000/health
```

### Configuration Best Practices

1. **Never commit secrets** - Use `.env` files (gitignored) or secret management tools
2. **Use environment-specific files** - `.env.development`, `.env.production`
3. **Override in docker-compose** - Use `docker-compose.override.yml` for local changes
4. **Document all changes** - Update this reference when adding new config parameters
5. **Validate before deploy** - Use `docker compose config` to check merged configuration
6. **Monitor configuration drift** - Track changes in version control

---

## Troubleshooting

### Common Configuration Issues

**Problem**: Kafka connection refused
```bash
# Check KAFKA_BOOTSTRAP_SERVERS is correct
echo $KAFKA_BOOTSTRAP_SERVERS

# Verify Kafka is running
docker compose ps kafka

# Test connection
docker compose exec kafka kafka-broker-api-versions --bootstrap-server localhost:9092
```

**Problem**: Schema Registry unavailable
```bash
# Check Schema Registry URL
curl http://localhost:8081/subjects

# Verify connectivity from consumer
docker compose exec consumer curl http://schema-registry:8081/subjects
```

**Problem**: API rate limiting too aggressive
```bash
# Temporarily disable for testing
RATE_LIMIT_ENABLED=false uv run uvicorn k2.api.main:app

# Or increase limit
RATE_LIMIT_PER_MINUTE=1000 uv run uvicorn k2.api.main:app
```

---

## Related Documentation

- [Platform Principles](../architecture/platform-principles.md) - Configuration philosophy
- [Operations Runbooks](../operations/runbooks/) - Configuration for specific scenarios
- [API Reference](./api-reference.md) - API-specific configuration
- [Data Dictionary V2](./data-dictionary-v2.md) - Schema configuration

---

**Maintained By**: Platform Engineering Team
**Last Updated**: 2026-01-14
**Next Review**: 2026-02-14 (monthly, or when new parameters added)
