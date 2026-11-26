# Docker Configuration

Configuration and support code for the K2 Market Data Platform stack, run via
`docker-compose.yml` at the repo root. For system design see
[../docs/architecture/README.md](../docs/architecture/README.md); for
day-to-day operations see [../docs/operations/README.md](../docs/operations/README.md).

## Directory Structure

```
docker/
├── clickhouse/           # ClickHouse gold hot tier, paused behind profile hot-gold (ADR-057)
│   ├── config.xml        # Server config (Kafka Engine, compression, Prometheus exporter)
│   └── ddl/              # gold tables (10-gold-tables.sql, tested by make test-clickhouse) + Kafka feeds (20-gold-kafka.sql)
├── lake/                 # v3 Iceberg lake on Lakekeeper + MinIO (ADR-018)
│   ├── ddl/lake.sql      # raw.messages, bronze.*, audit.checks
│   ├── ddl/generated/    # DDL written by k2gen generate for new products (none today)
│   ├── apply_ddl.py      # Applies lake.sql, then ddl/generated/ (the lake-ddl one-shot)
│   ├── init-lake.sh      # One-shot bootstrap: bucket, catalog, warehouse, namespaces
│   ├── ingest.py         # Redpanda -> raw.messages -> bronze.* (Spark, every 5 min)
│   ├── maintenance.py    # Compaction, snapshot expiry, orphan removal, nightly audit
│   ├── offsets.py        # Consumed offsets in the Iceberg snapshot summary (ADR-022)
│   ├── wire.py           # Confluent framing + schema-registry lookup
│   ├── metrics.py        # Prometheus exporter (the lake-metrics service)
│   └── spark_conf.py     # lake_session(), the only `lake` catalog config; --smoke self-check
├── airflow/              # Airflow 3 image, the three lake DAGs (dags/lake.py) and their tests (ADR-034)
├── lake-metrics/         # The lake-metrics exporter's own image (runs lake/metrics.py)
├── redpanda/             # Topic + Avro subject bootstrap (init.sh, run by redpanda-init)
├── spark/                # Spark image (Iceberg 1.8.1, Kafka + Avro jars, pinned by sha256)
├── trino/                # Trino 484: catalog `lake` (read-only), generated domain catalogs and rules (ADR-035, ADR-044)
├── grafana/
│   ├── provisioning/     # Auto-provisioned datasource (Prometheus) + dashboard loader
│   └── dashboards/       # Dashboard JSON, 5: pipeline overview, k2-l2-capture, k2-lake, k2-airflow, k2-resources
├── prometheus/
│   ├── prometheus.yml    # Scrape configs
│   └── rules/            # Alerting rules (capture, lake, Airflow)
└── postgres/
    └── ddl/              # Lakekeeper catalog DB, created on the shared PostgreSQL (Airflow's: airflow/init-db.sh)
```

## Services

| Service | Kind | What it does |
|---------|------|--------------|
| `redpanda`, `redpanda-console` | long-running | Streaming backbone + web UI |
| `clickhouse` | profile `hot-gold`, paused | Gold hot tier: Kafka engine feeds into `gold`, reloaded from the lake; not started by `docker compose up` while the platform is built ([ADR-057](../docs/adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)) |
| `minio` | long-running | S3 object storage, holds the v3 `k2-lake` bucket |
| `lakekeeper` | long-running | **v3** Iceberg REST catalog ([ADR-018](../docs/adr/ADR-018-v3-lake-first-rust-capture.md)); `127.0.0.1:18181` |
| `spark-iceberg` | long-running | Batch engine for the v3 lake ingest and maintenance |
| `prefect-db` | long-running | PostgreSQL for the Airflow and Lakekeeper metadata; the name is historical, kept from before ADR-034 |
| `trino` | long-running | SQL over the lake: catalog `lake` (warehouse `k2`, read-only at the connector) and one writable catalog and warehouse per data domain ([ADR-035](../docs/adr/ADR-035-trino-and-dbt-for-marts.md), [ADR-044](../docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md), [trino/README.md](trino/README.md)); `127.0.0.1:8088` (`K2_TRINO_PORT`) |
| `airflow-api-server`, `airflow-scheduler`, `airflow-dag-processor`, `airflow-statsd` | long-running | Airflow 3, the lake schedules ([ADR-034](../docs/adr/ADR-034-airflow-3-replaces-prefect.md)); UI on `127.0.0.1:8090` |
| `prometheus`, `grafana`, `lake-metrics` | long-running | Observability |
| `capture-{binance,kraken,coinbase}` | long-running | **v3** Rust WebSocket capture (trades + L2 book); metrics on `:8082/metrics` ([ADR-019](../docs/adr/ADR-019-rust-capture-tier.md)) |
| `redpanda-init` | one-shot | Topics + Avro subjects (`redpanda/init.sh`) |
| `lakekeeper-migrate` | one-shot | v3 catalog DB schema (`lakekeeper migrate`) |
| `lake-init` | one-shot | v3 bucket, bootstrap, warehouse `k2` and its namespaces, then one warehouse per domain from `lake/generated/warehouses.txt` (ADR-044) (`lake/init-lake.sh`) |
| `lake-ddl` | one-shot | v3 lake tables (`lake/apply_ddl.py` over `lake/ddl/lake.sql`) |
| `airflow-migrate` | one-shot | `airflow` role and database on `prefect-db`, then `airflow db migrate` (`airflow/init-db.sh`) |

## The v3 lake

`lake-init` is idempotent, a re-run reports 400 `CatalogAlreadyBootstrapped` and 400
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

## Lake pipeline

The lake jobs do not run on a systemd timer or cron, they are scheduled as three Airflow 3 DAGs
(`airflow/dags/lake.py`, [airflow-schedules.md](../docs/operations/airflow-schedules.md)),
dispatched by `k2-airflow-scheduler` into `k2-spark-iceberg`:

- `lake_ingest`, runs `lake/ingest.py`, cron `1-59/5 * * * *`, one run at a time
- `lake_maintenance`, runs `lake/maintenance.py`
  (compact / expire / remove orphans / audit), nightly at 03:00 UTC
- `lake_rebuild`, runs `lake/rebuild.py`, manual only

There is no watermark table: the
offsets each run consumed are written into the Iceberg snapshot summary of the commit that
wrote the rows ([ADR-022](../docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md)).

## Usage

```bash
make up            # check-env, then docker compose up -d (refused on a stack built before 2026-11-26)
make down           # stop and remove the containers (volumes kept)
make logs           # tail all service logs
make ps             # show service status

# The lake DAGs
docker exec k2-airflow-scheduler airflow dags list
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest
```

Starting, stopping, recreating one service and rolling out a change on the live stack are the
[operator guide](../docs/operations/README.md).

## Troubleshooting

**ClickHouse is not running**
- Expected: it is paused behind the compose profile `hot-gold` ([ADR-057](../docs/adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)), so `make up` does not start it
- Before any restart, check the XML parses: `python3 -c "import xml.etree.ElementTree as E; E.parse('docker/clickhouse/config.xml'); E.parse('docker/clickhouse/users.xml')"` (silent on success)

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

*Directory* mounts go stale too, the same write-then-rename problem CLAUDE.md
documents for the file-level `config/instruments.yaml` mount. An editor (and the
Edit tool) saves by write-to-temp-then-rename, which produces a new inode, and
the file-sharing layer keeps serving the old one.

**`--force-recreate` is the fix, but it has to cover every service that mounts
the directory.** The stale entry is held for as long as *any* container has that
host path mounted, so recreating one service while a sibling still holds the
same directory leaves the new container reading the old file as well. Verified
2026-08-26 on this stack:

| Situation | New container reads |
|-----------|--------------------|
| No container holds the dir at rename time | new contents |
| Another container still holds the dir | **old contents** |
| Holder removed, then new container started | new contents |

That is why `docker/lake` looked immune to `--force-recreate`: it is mounted by
**both** `spark-iceberg` (`/home/iceberg/lake`) and `lake-init` (`/init`), so
recreating `lake-init` alone leaves `spark-iceberg` pinning the stale copy.

```bash
# docker/lake: recreate BOTH holders
docker compose up -d --force-recreate --no-deps spark-iceberg lake-init

# docker/redpanda: only redpanda-init mounts it, so one service is enough
docker compose up -d --force-recreate --no-deps redpanda-init

# Which services mount the directory you edited:
grep -n 'docker/lake' docker-compose.yml
```

Observed since, on 2026-11-26: files changed *inside* a directory mount did reach a running
container (every file changed in `dbt/` after `k2-airflow-scheduler` was created had the same
sha256 inside it as on the host), and the stale case seen on 2026-11-25 was the directory
itself deleted and recreated by a checkout switch. Check before you recreate.

Confirm what the container actually sees before debugging the script:

```bash
docker exec k2-spark-iceberg head -5 /home/iceberg/lake/spark_conf.py
```

**Lake ingest stuck or behind**
- See [../docs/runbooks/lake-ingest-lag.md](../docs/runbooks/lake-ingest-lag.md)
  and [../docs/runbooks/lake-recovery.md](../docs/runbooks/lake-recovery.md)
