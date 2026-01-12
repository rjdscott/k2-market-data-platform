# Step 01.5.6: Docker Compose Integration

**Status**: ✅ Complete
**Estimated Time**: 3 hours
**Actual Time**: ~1 hour
**Part**: Binance Streaming (Phase 1.5)

---

## Goal

Add Binance streaming service to Docker Compose.

---

## Tasks

### 1. Update docker-compose.yml

```yaml
binance-stream:
  build: .
  command: python scripts/binance_stream.py
  environment:
    - K2_KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    - K2_KAFKA_SCHEMA_REGISTRY_URL=http://schema-registry:8081
    - K2_BINANCE_ENABLED=true
  depends_on:
    - kafka
    - schema-registry
  restart: unless-stopped
  networks:
    - k2-network
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
    interval: 30s
    timeout: 10s
    retries: 3
```

### 2. Optional: scripts/start-streaming.sh

```bash
#!/bin/bash
docker compose up -d binance-stream
```

---

## Validation

```bash
docker compose up -d
docker compose ps binance-stream
# Should show "Up (healthy)"

docker compose logs binance-stream -f
# Should see connection and trades
```

---

## Commit Message

```
feat(docker): add binance streaming service to compose

- Add binance-stream service to docker-compose.yml
- Configure environment variables
- Add health check endpoint
- Test with docker compose up -d
- Service restarts automatically on failure

Related: Phase 2 Prep, Step 01.5.6
```

---

## Completion Notes

**Implemented**: 2026-01-13

### What Was Built:
- ✅ Added `binance-stream` service to `docker-compose.yml`
- ✅ Configured comprehensive environment variables for Kafka, Binance, resilience, metrics, and logging
- ✅ Added health check on metrics port (9091)
- ✅ Configured service dependencies on kafka and schema-registry-1
- ✅ Set restart policy to `unless-stopped` for automatic recovery
- ✅ Added resource limits (CPU: 0.5, Memory: 512M) and reservations
- ✅ Integrated into k2-network with other services

### Configuration Highlights:

**Kafka Integration:**
- Bootstrap servers: kafka:29092 (internal Docker network)
- Schema Registry: http://schema-registry-1:8081
- Compression: lz4 (fast compression for market data)
- Idempotence enabled for exactly-once semantics
- Batch size: 16KB, Linger: 10ms (optimized for real-time streaming)

**Binance Configuration:**
- Default symbols: BTCUSDT, ETHUSDT, BNBUSDT (top 3 crypto pairs)
- Primary URL: wss://stream.binance.com:9443/stream
- Failover URL: wss://stream.binance.us:9443/stream
- Reconnect delay: 5s (exponential backoff implemented in client)
- Max reconnect attempts: 10

**Resilience:**
- Health check interval: 30s
- Health check timeout: 10s
- Start period: 60s (allows time for WebSocket connection)
- Circuit breaker enabled in client code (3 failures → open)

**Observability:**
- Metrics exposed on port 9091 (Prometheus format)
- JSON logging for structured log analysis
- Log level: INFO (can be changed to DEBUG for troubleshooting)

### Service Architecture:

```
binance-stream service:
  ├── Builds from Dockerfile (Python 3.12, uv package manager)
  ├── Runs scripts/binance_stream.py
  ├── Connects to Kafka (kafka:29092) for trade publishing
  ├── Connects to Schema Registry (schema-registry-1:8081) for v2 schema
  ├── Streams from Binance WebSocket (wss://stream.binance.com:9443)
  ├── Exposes metrics on port 9091 (/metrics endpoint)
  └── Restarts automatically on failure (unless manually stopped)
```

### Health Check Strategy:

The service health check validates the metrics port is accessible:
- **Check**: TCP connection to 127.0.0.1:9091
- **Interval**: Every 30 seconds
- **Timeout**: 10 seconds
- **Retries**: 3 attempts before marking unhealthy
- **Start period**: 60 seconds (allows WebSocket connection establishment)

This approach ensures the service is running and the HTTP server is responsive. The WebSocket connection health is monitored internally by the client's health check loop.

### Usage:

```bash
# Start all services including binance-stream
docker compose up -d

# Check binance-stream status
docker compose ps binance-stream
# Expected: Up (healthy) after ~60 seconds

# View logs (real-time)
docker compose logs binance-stream -f

# View metrics
curl http://localhost:9091/metrics | grep k2_binance

# Stop binance-stream only
docker compose stop binance-stream

# Restart binance-stream
docker compose restart binance-stream

# Remove and rebuild (if Dockerfile changed)
docker compose up -d --build binance-stream
```

### Integration with Existing Services:

The binance-stream service integrates seamlessly with the existing infrastructure:
1. **Depends on Kafka** - Waits for kafka to be healthy before starting
2. **Depends on Schema Registry** - Waits for schema-registry-1 to be healthy
3. **Uses k2-network** - Communicates with other services via Docker bridge network
4. **Prometheus scraping** - Metrics on port 9091 can be scraped by Prometheus service
5. **Resource-limited** - CPU/memory limits prevent runaway consumption

### What Was NOT Implemented (Future Enhancements):

- Optional start-streaming.sh helper script (not needed; docker compose commands are simple)
- HTTP health endpoint on port 8080 (using metrics port 9091 instead)
- Volume mounts for configuration files (using environment variables instead)
- External port mapping for metrics (metrics port is internal-only)

These can be added in future steps if needed.

---

**Last Updated**: 2026-01-13
**Status**: ✅ Complete
