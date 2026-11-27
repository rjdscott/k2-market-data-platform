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
├── lake/                 # v3 Iceberg lake on Lakekeeper + MinIO (ADR-018); the old ingest is in legacy/v3-lake-ingest/
│   ├── init-lake.sh      # One-shot bootstrap: bucket, catalog, warehouse k2, the warehouses of the release's storage plan
│   ├── offsets.py        # Consumed offsets in the Iceberg snapshot summary (ADR-022)
│   ├── wire.py           # Confluent framing + schema-registry lookup
│   ├── bars.py, books.py # Event bars and the book replay, kept for the new connectors
│   └── spark_conf.py     # lake_session(), the only `lake` catalog config; --smoke self-check
├── airflow/              # Airflow 3 image and its tests (ADR-034); no DAG until phase 002
├── redpanda/             # Topic + Avro subject bootstrap (init.sh, run by redpanda-init)
├── spark/                # Spark image (Iceberg 1.8.1, Kafka + Avro jars, pinned by sha256)
├── trino/                # Notes on Trino 484; its etc and rules are compiled into each release by k2gen (ADR-058, ADR-059)
├── grafana/
│   ├── provisioning/     # Auto-provisioned datasource (Prometheus) + dashboard loader
│   └── dashboards/       # Dashboard JSON, 4: pipeline overview, k2-l2-capture, k2-airflow, k2-resources
├── prometheus/
│   ├── prometheus.yml    # Scrape configs
│   └── rules/            # Alerting rules (capture, Airflow)
└── postgres/
    └── ddl/              # Lakekeeper catalog DB, created on the shared PostgreSQL (Airflow's: airflow/init-db.sh)
```

## Services

| Service | Kind | What it does |
|---------|------|--------------|
| `redpanda`, `redpanda-console` | long-running | Streaming backbone + web UI |
| `clickhouse` | profile `hot-gold`, paused | Gold hot tier: Kafka engine feeds into `gold`; not started by `docker compose up` while the platform is built ([ADR-057](../docs/adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)) |
| `minio` | long-running | S3 object storage, holds the v3 `k2-lake` bucket |
| `lakekeeper` | long-running | **v3** Iceberg REST catalog ([ADR-018](../docs/adr/ADR-018-v3-lake-first-rust-capture.md)); on network `k2-catalog` only, no host port: it has no authentication, so the network is its fence ([ADR-059](../docs/adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md) part 7) |
| `spark-iceberg` | long-running | Batch engine for the lake; no job runs while the old ingest is retired ([`legacy/v3-lake-ingest/`](../legacy/v3-lake-ingest/)) |
| `prefect-db` | long-running | PostgreSQL for the Airflow and Lakekeeper metadata; the name is historical, kept from before ADR-034 |
| `trino` | long-running | SQL over the lake: catalog `lake` (warehouse `k2`, read-only at the connector) and one writable catalog and warehouse per data domain ([ADR-035](../docs/adr/ADR-035-trino-and-dbt-for-marts.md), [ADR-044](../docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md), [trino/README.md](trino/README.md)); its etc read through the release pointer `/k2/releases/pointers/trino/compiled/trino/etc` ([ADR-058](../docs/adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md)); HTTPS only with password logins on `127.0.0.1:8443` (`K2_TRINO_PORT`, [ADR-060](../docs/adr/ADR-060-trino-logins-by-password-over-tls-under-a-local-ca.md)) |
| `airflow-api-server`, `airflow-scheduler`, `airflow-dag-processor`, `airflow-statsd` | long-running | Airflow 3 ([ADR-034](../docs/adr/ADR-034-airflow-3-replaces-prefect.md)); no DAG until phase 002; UI on `127.0.0.1:8090` |
| `prometheus`, `grafana`, `cadvisor` | long-running | Observability |
| `capture-{binance,kraken,coinbase}` | long-running | **v3** Rust WebSocket capture (trades + L2 book); metrics on `:8082/metrics` ([ADR-019](../docs/adr/ADR-019-rust-capture-tier.md)) |
| `redpanda-init` | one-shot | Topics + Avro subjects (`redpanda/init.sh`) |
| `lakekeeper-migrate` | one-shot | v3 catalog DB schema (`lakekeeper migrate`) |
| `lake-init` | one-shot | v3 bucket, bootstrap, warehouse `k2`, then every warehouse of the release's `compiled/lake/storage-plan.json`, read through the trino pointer or the release `make deploy` names (ADR-044) (`lake/init-lake.sh`) |
| `airflow-migrate` | one-shot | `airflow` role and database on `prefect-db`, then `airflow db migrate` (`airflow/init-db.sh`) |

## The v3 lake

`lake-init` is idempotent, a re-run reports 400 `CatalogAlreadyBootstrapped` and 400
`CreateWarehouseStorageProfileOverlap`, both tolerated, and exits 0.

```bash
# Prove the catalog end to end: create, append with a snapshot property, read back, drop
docker exec k2-spark-iceberg python3 /home/iceberg/lake/spark_conf.py --smoke
# Expect: count=1, a summary containing 'k2.smoke': '1', "✓ lake smoke passed"

make lake-init   # re-bootstrap
```

⚠ **The `lakekeeper` PostgreSQL database is created by an init script**
(`postgres/ddl/10-lakekeeper-db.sql`), and PostgreSQL runs those **only when the data
directory is first created**. Upgrading a stack whose `postgres-data` volume predates
Phase B needs it once by hand, before `lakekeeper-migrate` will start:

```bash
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d postgres -c 'CREATE DATABASE lakekeeper'
```

## Lake pipeline

Retired. The old ingest, its three Airflow DAGs, its DDL one-shot, its metrics exporter and the
dbt marts are archived in [`legacy/v3-lake-ingest/`](../legacy/v3-lake-ingest/), and their
tables were dropped after a verified copy (`make drop`). The captures keep writing to Redpanda;
no live crypto data lands in the lake until phase `004` of the
[plan](../docs/plans/2026-11-26-unified-product-platform/README.md). The offsets in the snapshot
summary ([ADR-022](../docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md)) live on in
`lake/offsets.py`, for the new connectors.

## Usage

```bash
make deploy RELEASE=<id>   # the stack runs releases: docs/runbooks/deploy-a-release.md (a fresh clone: first-cutover-to-releases.md first)
make down                  # stop and remove the containers (volumes kept)
make logs                  # tail all service logs
make ps                    # show service status

# The DAGs: none until phase 002
docker exec k2-airflow-scheduler airflow dags list
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
