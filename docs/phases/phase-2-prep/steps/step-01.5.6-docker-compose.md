# Step 01.5.6: Docker Compose Integration

**Status**: ⬜ Not Started  
**Estimated Time**: 3 hours  
**Actual Time**: -  
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

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started
