# K2 Platform v2 - Docker Configuration

**Last Updated:** 2026-02-09
**Version:** v2.0.0-phase1

---

## Overview

This directory contains configuration files for the K2 Market Data Platform v2 greenfield build. All configurations follow 2026 best practices for containerized data platforms.

---

## Directory Structure

```
docker/
├── README.md                          # This file
├── v1-baseline.yml                    # Snapshotted v1 (rollback safety)
├── clickhouse/                        # ClickHouse configuration
│   ├── config.xml                     # Server config (performance, network)
│   └── users.xml                      # User definitions
├── prometheus/                        # Prometheus monitoring
│   └── prometheus.yml                 # Scrape configs for all services
└── grafana/                           # Grafana dashboards
    ├── provisioning/
    │   ├── datasources/               # Auto-provision Prometheus datasource
    │   └── dashboards/                # Auto-provision dashboards
    └── dashboards/
        └── v2-migration-tracker.json  # v2 migration monitoring dashboard
```

---

## Configuration Files

### ClickHouse (`clickhouse/`)

**config.xml** - Server configuration
- Optimized for market data (high-frequency inserts, time-series queries)
- Background merge pool sized for real-time OHLCV computation
- Kafka Engine settings for Redpanda integration
- LZ4 compression (market data compresses 10:1 typical)

**users.xml** - User management
- `default`: Internal user (no password)
- `k2_user`: Application user (password from `.env.v2`)

### Prometheus (`prometheus/`)

**prometheus.yml** - Scrape configuration
- Scrapes: Redpanda (9644), ClickHouse (8123), Grafana (3000)
- Future: Spring Boot API (/actuator/prometheus), Kotlin feed handlers
- 15s default scrape interval, 10s for streaming layer

### Grafana (`grafana/`)

**Provisioning** - Auto-setup on startup
- Datasource: Prometheus (default)
- Dashboards: v2 migration tracker

**v2-migration-tracker.json** - Main monitoring dashboard
- Total CPU/RAM usage vs budget (16 CPU / 40GB)
- Service health (up/down status)
- Redpanda throughput (msg/sec)
- ClickHouse query rate
- Resource consumption per service

---

## Versioning Strategy

| File | Purpose | When Updated |
|------|---------|--------------|
| `v1-baseline.yml` | v1 snapshot (rollback) | Never (immutable safety net) |
| `docker-compose.v2.yml` | v2 active config | Each phase |
| `docker/v2-phase-{N}-*.yml` | Phase checkpoints | After each phase completes |

See [../docs/phases/v2/INFRASTRUCTURE-VERSIONING.md](../docs/phases/v2/INFRASTRUCTURE-VERSIONING.md) for full rollback strategy.

---

## Best Practices Applied

### Security
- Read-only config mounts (`:ro`)
- Environment variable secrets (not hardcoded)
- Named networks with explicit subnet
- Minimal permissions (users.xml)

### Observability
- Health checks on all services (15s interval)
- Prometheus scraping all components
- Structured logging (JSON where possible)
- Service labels (tier, version, service name)

### Resource Management
- Explicit CPU/memory limits and reservations
- Resource budget tracking (see docker-compose.v2.yml footer)
- ulimits for ClickHouse (262k file descriptors)

### Reliability
- Restart policies (`unless-stopped`)
- Dependency ordering (`depends_on` with health conditions)
- Named volumes (data persistence)
- Proper start periods for slow-starting services

---

## Usage

### Start v2 stack
```bash
docker compose -f docker-compose.v2.yml up -d
```

### View logs
```bash
docker compose -f docker-compose.v2.yml logs -f
```

### Check health
```bash
docker compose -f docker-compose.v2.yml ps
```

### Stop v2 stack
```bash
docker compose -f docker-compose.v2.yml down
```

### Rollback to v1
```bash
docker compose -f docker/v1-baseline.yml up -d
```

---

## Troubleshooting

### ClickHouse won't start
- Check ulimits: `docker exec k2-clickhouse ulimit -n` (should be 262144)
- Check logs: `docker logs k2-clickhouse`
- Verify config syntax: `xmllint --noout docker/clickhouse/config.xml`

### Redpanda health check failing
- Wait 30s (start_period)
- Check cluster: `docker exec k2-redpanda rpk cluster health`
- Verify memory: Should have 2GB allocated

### Grafana dashboards not appearing
- Check provisioning logs: `docker logs k2-grafana | grep provision`
- Verify dashboard JSON: `jq . docker/grafana/dashboards/v2-migration-tracker.json`
- Refresh Grafana UI (sometimes needs manual refresh)

### Prometheus not scraping
- Check targets: http://localhost:9090/targets
- Verify service names resolve: `docker exec k2-prometheus ping -c1 redpanda`
- Check config: `docker exec k2-prometheus cat /etc/prometheus/prometheus.yml`

---

## Future Phases

These directories will be created in subsequent phases:

- `docker/api/` - Spring Boot API configuration (Phase 8)
- `docker/feed-handlers/` - Kotlin feed handler config (Phase 6)
- `docker/spark/` - Spark batch job config (Phase 5)
- `docker/iceberg/` - Iceberg catalog config (Phase 5)

---

**Related Documentation:**
- [Phase 1 README](../docs/phases/v2/phase-1-infrastructure-baseline/README.md)
- [Architecture v2](../docs/decisions/platform-v2/ARCHITECTURE-V2.md)
- [Infrastructure Versioning](../docs/phases/v2/INFRASTRUCTURE-VERSIONING.md)
