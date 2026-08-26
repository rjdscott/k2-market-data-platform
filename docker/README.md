# Docker Configuration

Configuration and support code for the K2 Market Data Platform stack, run via
`docker-compose.yml` at the repo root. For system design see
[../docs/architecture/README.md](../docs/architecture/README.md); for
day-to-day operations see [../docs/operations/README.md](../docs/operations/README.md).

## Directory Structure

```
docker/
├── clickhouse/           # ClickHouse server config + DDL bootstrap + schema history
│   ├── config.xml        # Server config (Kafka Engine, compression, Prometheus exporter)
│   ├── ddl/              # ClickHouse medallion bootstrap (01-k2-schema.sql), auto-run on init
│   └── schema/           # Historical schema migration trail (not run)
├── iceberg/
│   ├── ddl/              # Iceberg catalog + bronze/silver/gold table DDL
│   ├── validation/       # Table validation SQL
│   └── warehouse/        # Bind-mounted Iceberg warehouse (cold storage, on disk)
├── offload/              # ClickHouse -> Iceberg offload job
│   ├── offload_generic.py       # Direct-append offload (Spark)
│   ├── iceberg_maintenance.py   # Compaction, snapshot expiry, warm/cold audit
│   ├── watermark_pg.py          # Watermark read/write against PostgreSQL
│   └── flows/                   # Prefect flow + deployment definitions
├── prefect/              # Prefect worker image (Dockerfile) + deployment storage
├── spark/                # Spark image (Dockerfile, bundles ClickHouse JDBC driver)
├── grafana/
│   ├── provisioning/     # Auto-provisioned datasource (Prometheus) + dashboard loader
│   └── dashboards/       # Dashboard JSON (pipeline overview, ClickHouse, Iceberg offload, v2-migration-tracker)
├── prometheus/
│   ├── prometheus.yml    # Scrape configs
│   └── rules/            # Alerting rules (ClickHouse, feed handlers, Iceberg offload)
└── postgres/
    └── ddl/              # offload_watermarks table DDL (Prefect metadata DB)
```

## Iceberg Offload

The ClickHouse -> Iceberg offload does not run on a systemd timer or cron —
it is scheduled as Prefect deployments on the `iceberg-offload` work pool,
executed by the `k2-prefect-worker` container:

- `iceberg-offload-15min` — runs `offload_generic.py` per table every 15 minutes
- `iceberg-maintenance-daily` — runs `iceberg_maintenance.py` (compact/expire/audit) at 02:00 UTC

Deployment definitions live in `offload/flows/`. Watermarks are tracked in
the PostgreSQL `offload_watermarks` table (seeded via `postgres/ddl/`).

## Usage

```bash
make up            # start the full stack (docker compose up -d)
make down           # stop the stack (volumes kept)
make logs           # tail all service logs
make ps             # show service status

# Prefect deployments
docker exec k2-prefect-worker prefect deployment ls
docker exec k2-prefect-worker prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'
docker compose logs prefect-worker --tail 100
```

## Troubleshooting

**ClickHouse won't start**
- Check logs: `docker logs k2-clickhouse`
- Verify config syntax: `xmllint --noout docker/clickhouse/config.xml`

**Redpanda health check failing**
- Check cluster: `docker exec k2-redpanda rpk cluster health`
- Topics are created by the `redpanda-init` one-shot service on stack startup
  (see `redpanda-init` in `docker-compose.yml`)

**Grafana dashboards not appearing**
- Check provisioning logs: `docker logs k2-grafana | grep provision`
- Verify dashboard JSON: `jq . docker/grafana/dashboards/<name>.json`

**Prometheus not scraping / alerts not firing**
- Check targets: http://localhost:9090/targets
- Validate rules: `docker run --rm -v $PWD/docker/prometheus/rules:/r --entrypoint promtool prom/prometheus:v3.2.0 check rules /r/*.yml`

**Iceberg offload stuck**
- See [../docs/runbooks/iceberg-offload-failure.md](../docs/runbooks/iceberg-offload-failure.md)
  and [../docs/runbooks/iceberg-offload-lag.md](../docs/runbooks/iceberg-offload-lag.md)
