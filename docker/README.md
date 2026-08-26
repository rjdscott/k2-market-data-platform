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
├── lake/                 # v3 Iceberg lake on Lakekeeper + MinIO (ADR-018)
│   ├── init-lake.sh      # One-shot bootstrap: bucket, catalog, warehouse, namespaces
│   └── spark_conf.py     # lake_session() — the only `lake` catalog config; --smoke self-check
├── redpanda/             # Topic + Avro subject bootstrap (init.sh, run by redpanda-init)
├── offload/              # ClickHouse -> Iceberg offload job
│   ├── offload_generic.py       # Direct-append offload (Spark)
│   ├── iceberg_maintenance.py   # Compaction, snapshot expiry, warm/cold audit
│   ├── watermark_pg.py          # Watermark read/write against PostgreSQL
│   └── flows/                   # Prefect flow + deployment definitions
├── prefect/              # Prefect worker image (Dockerfile) + deployment storage
├── spark/                # Spark image (Iceberg 1.8.1, Kafka + Avro + ClickHouse JDBC jars)
├── grafana/
│   ├── provisioning/     # Auto-provisioned datasource (Prometheus) + dashboard loader
│   └── dashboards/       # Dashboard JSON (pipeline overview, ClickHouse, Iceberg offload, v2-migration-tracker)
├── prometheus/
│   ├── prometheus.yml    # Scrape configs
│   └── rules/            # Alerting rules (ClickHouse, feed handlers, Iceberg offload)
└── postgres/
    └── ddl/              # offload_watermarks + lakekeeper catalog DB (Prefect metadata DB)
```

## Services

| Service | Kind | What it does |
|---------|------|--------------|
| `redpanda`, `redpanda-console` | long-running | Streaming backbone + web UI |
| `clickhouse` | long-running | Warm tier: Kafka Engine ingest, bronze/silver/gold MVs |
| `minio` | long-running | S3 object storage — holds the v3 `k2-lake` bucket |
| `lakekeeper` | long-running | **v3** Iceberg REST catalog ([ADR-018](../docs/adr/ADR-018-v3-lake-first-rust-capture.md)); `127.0.0.1:18181` |
| `spark-iceberg` | long-running | Batch ETL: v2 offload + v3 lake jobs |
| `prefect-db`, `prefect-server`, `prefect-worker` | long-running | Orchestration + metadata + watermarks |
| `prometheus`, `grafana`, `iceberg-metrics` | long-running | Observability |
| `feed-handler-{binance,kraken,coinbase}` | long-running | Kotlin WebSocket ingestion |
| `redpanda-init` | one-shot | Topics + Avro subjects (`redpanda/init.sh`) |
| `iceberg-init` | one-shot | v2 `cold.*` hadoop-catalog DDL (`iceberg/ddl/`) |
| `lakekeeper-migrate` | one-shot | v3 catalog DB schema (`lakekeeper migrate`) |
| `lake-init` | one-shot | v3 bucket, bootstrap, warehouse `k2`, namespaces (`lake/init-lake.sh`) |

## The v3 lake

`lake-init` is idempotent — a re-run reports 400 `CatalogAlreadyBootstrapped` and 400
`CreateWarehouseStorageProfileOverlap`, both tolerated, and exits 0.

```bash
# Prove the catalog end to end: create, append with a snapshot property, read back, drop
docker exec k2-spark-iceberg python3 /home/iceberg/lake/spark_conf.py --smoke
# Expect: count=1, a summary containing 'k2.smoke': '1', "✓ lake smoke passed"

curl -s localhost:18181/health          # {"health":"ok", ...}
docker compose up -d --force-recreate --no-deps lake-init   # re-bootstrap
```

⚠ **The `lakekeeper` PostgreSQL database is created by an init script**
(`postgres/ddl/10-lakekeeper-db.sql`), and PostgreSQL runs those **only when the data
directory is first created**. Upgrading a stack whose `postgres-data` volume predates
Phase B needs it once by hand, before `lakekeeper-migrate` will start:

```bash
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d postgres -c 'CREATE DATABASE lakekeeper'
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

**A bind-mounted script runs its old contents after you edited it**
- Observed 2026-08-26 on `docker/lake` — a *directory* mount, not the file-level
  `config/instruments.yaml` case CLAUDE.md documents. An editor that saves by
  write-to-temp-then-rename leaves the container reading the replaced inode, and
  `--force-recreate` does not clear it. Rewrite the file in place to fix:
  ```bash
  python3 -c "import os,sys; p=sys.argv[1]; d=open(p,'rb').read(); f=open(p,'r+b'); \
    f.write(d); f.truncate(); f.flush(); os.fsync(f.fileno())" docker/lake/init-lake.sh
  ```
- Confirm what the container actually sees before debugging the script:
  `docker run --rm -v "$PWD/docker/lake:/init:ro" --entrypoint cat <image> /init/init-lake.sh`

**Iceberg offload stuck**
- See [../docs/runbooks/iceberg-offload-failure.md](../docs/runbooks/iceberg-offload-failure.md)
  and [../docs/runbooks/iceberg-offload-lag.md](../docs/runbooks/iceberg-offload-lag.md)
