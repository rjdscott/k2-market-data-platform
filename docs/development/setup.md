# Development Setup

Getting the stack running locally, and the inner loops for changing Rust or Python
code once it is. Running it day to day (health, consoles, stopping, recreating one service,
rolling out a change) is the [operator guide](../operations/README.md).

## Prerequisites

| Tool | Needed for | Notes |
|------|-----------|-------|
| Docker Engine + Compose v2 | everything | A Docker engine with ≥ 38 GiB memory so every `deploy.resources.limits` can be honoured, the 37.812 GiB bootstrap peak included (39.312 GiB with the Prefect rollback's `--profile legacy`) (`docker info --format '{{.MemTotal}}'`, **Docker Desktop defaults far lower and silently clips every limit above the VM size**; Settings → Resources → Memory); measured steady-state usage is far lower (see [../operations/docker-resources.md](../operations/docker-resources.md)), so the stack runs on less, but limits then exceed the engine and ClickHouse's 8 GB cap is not real. Steady state declares 19.30 CPU / 35.312 GiB of limits across 19 long-running services and peaks at 21.30 CPU / 37.812 GiB across 24 while the five one-shot init containers run, under the 25 CPU / 48 GiB ceiling of [ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md) (v2 alone: 15.1 CPU / 21.875 GB). The engine requirement follows the declared sum, not the ceiling: 38 GiB today, 48 GiB if a later phase fills the ceiling. On Docker Desktop the host has a requirement of its own: the VM grows to its whole memory setting, so host RAM must exceed that setting plus everything else the host runs ([sizing](../runbooks/docker-vm-oom-kill.md#2-size-the-vm-so-the-host-keeps-enough-memory)) |
| [`uv`](https://docs.astral.sh/uv/) | the Python unit tests and ruff | Nothing else uses Python locally |
| `jq`, `psql` | the diagnostic one-liners in the ops docs | optional |

## First run

```bash
cp .env.example .env      # then replace every change-me-in-production value: the operator guide, section 1, has a command for it
make up                   # refuses while one is left, or MINIO_ROOT_USER = MINIO_ROOT_PASSWORD
make health
```

Two values in `.env` are host-specific, not secrets:

- `K2_CAPTURE_CPUSET` / `K2_HEAVY_CPUSET` ship **empty**, which means no CPU pinning and
  starts anywhere. Uncomment the 15-core layout in `.env.example` only on a host that has
  those cores — a cpuset naming a core the host does not have fails the container at start
  with `invalid argument`, seven of them at once (the three captures, ClickHouse, Spark,
  Trino and the `lake-ddl` one-shot).
- `K2_AIRFLOW_PORT` (default `8090`) and `K2_TRINO_PORT` (default `8088`) are the Airflow
  and Trino host ports. `K2_AIRFLOW_PORT` also feeds Airflow's own base URL, so moving it
  moves the links its UI builds; set either if something else on the host owns the port.
  (`K2_PREFECT_PORT`, default `4200`, matters only for the Prefect rollback behind the
  `legacy` profile.)

`make up` builds four images on the first run: the Rust `k2-capture` binary, `k2-airflow`,
`k2-lake-metrics`, and the Spark image (which bakes the Kafka and Avro jars, each pinned by
sha256). Allow **several minutes**; subsequent starts are seconds. Services come up healthy in
dependency order: Redpanda, then `redpanda-init` creates the topics, registers the nine v3 Avro
subjects and exits (9 topics of 12 partitions, 108 in all, `market.crypto.v3.{raw,trades,book}.<ex>`;
the six v2 topics were deleted at the Phase E cutover on 2026-08-27 and are not recreated),
then the three `capture-*` containers.

Never commit `.env`. Everything reads its secrets from it; nothing in the repo hardcodes a
password.

### The safe bring-up (after the first run)

`make up` is `scripts/check-env.py`, then the raw `docker compose up -d`. For everyday use,
`make dev-up` runs the maintainer's bring-up checklist instead: the same `.env` check, then
it sets `K2_PREFECT_PORT` when host port 4200 is busy, recreates any service holding a
directory bind mount that changed in the last commit
(see the [bind-mount gotcha](../../docker/README.md#troubleshooting)), then runs
`scripts/health.sh` — the same checks as `make health` — before declaring success.
`bash scripts/dev-up.sh --dry-run` prints what it would do without touching anything.

**A stack built before 2026-11-26 is refused, for now.** Its `.env` carries the MinIO pair
the guard rejects (SECURITY.md), so `make up` and `make dev-up` stop at `check-env: FAIL`
until that credential is replaced. Do not edit the pair in `.env` to get past it: each
Lakekeeper warehouse keeps its own stored copy, and the lake stops writing. What to run
instead, and the condition that ends it, are in the operator guide:
[Read this first](../operations/README.md#read-this-first-the-state-of-this-host), and
[section 6](../operations/README.md#6-restart-or-recreate-one-service) for recreating a
service under the pause.

### Applying the ClickHouse schema

The `gold` database auto-applies on a fresh volume via `/docker-entrypoint-initdb.d`:
[`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql)
(the tables and views, the contract CI tests) then
[`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql) (the Kafka-engine feeds).
Applying them to a running server is in
[`docker/clickhouse/README.md`](../../docker/clickhouse/README.md#applying-it-to-a-running-server).
The v2 `k2` medallion was dropped on 2026-08-27; its DDL is archived in
[`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md).

## Verifying data is flowing

```bash
# 1. Raw frames reaching Redpanda
docker exec k2-redpanda rpk topic consume market.crypto.v3.raw.binance --num 3 --format '%v\n'

# 2. All three capture containers healthy (exits non-zero if a stream has gone stale)
for x in binance kraken coinbase; do
  echo -n "$x: "; docker exec k2-capture-$x /k2-capture healthcheck
done

# 3. Records per exchange and kind
curl -sG localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange, kind) (rate(k2_capture_records_produced_total[5m]))' | jq
```

ClickHouse serves `gold`; the v2 `k2` database was dropped on 2026-08-27, so any `k2.*`
query is an error, not an empty result. See
[../architecture/README.md](../architecture/README.md). More query recipes:
[../operations/data-inspection.md](../operations/data-inspection.md).

## Instrument registry

[`config/instruments.yaml`](../../config/instruments.yaml) is the single source of truth
for which symbols each capture container subscribes to, 12 Binance, 11 Kraken, 11
Coinbase. Each row carries a `native` (exactly the bytes on the wire, `BTCUSDT`,
`BTC/USD`, `BTC-USD`) and a `canonical` (`BASE/QUOTE`, the Kafka key and the lake join
key). Nothing translates a symbol in code; a native the file does not list is a loud
failure, not a guess.

> **Bind-mount inode gotcha.** The file is mounted file-by-file, which pins its inode. Most
> editors save by writing a temp file and renaming it, which produces a *new* inode, the
> container keeps reading the old one. After editing:
>
> ```bash
> docker compose up -d --force-recreate --no-deps capture-binance
> ```
>
> `docker restart` will **not** pick up the change. This costs an hour the first time it
> bites you.

## Rust inner loop

Rebuild one capture container after a code change:

```bash
docker compose up -d --build capture-binance
docker compose logs -f capture-binance
```

The build context is the **repository root**, not the crate: `src/record.rs` compiles the
wire contract in with `include_str!("../../../schemas/avro/trade.avsc")`, so a
crate-directory context cannot see the schemas. `make build-capture` is the same build
with `K2_GIT_SHA` stamped into `k2_capture_build_info`.

There is no local Rust toolchain requirement, compile and test in a container:

```bash
make test-rust      # cargo test --locked in rust:1-bookworm
```

That target reinstalls `cmake`/`clang` on every run. For a tight loop, build the builder
image once and mount named volumes for the cargo registry and target dir, a rebuild then
takes seconds instead of minutes. Both are in
[`services/capture-rust/README.md`](../../services/capture-rust/README.md), along with the
full `K2_*` / `--flag` table and the `k2-capture record` subcommand for capturing a replay
fixture.

Stop the containerised capture first (`docker compose stop capture-binance`) if you run a
second one, or both will produce to the same topics.

Tests: `make test-rust`, see [testing.md](./testing.md).

## Python lake inner loop

The lake scripts are bind-mounted into the Spark container (`docker/lake` at
`/home/iceberg/lake`), so edits take effect at the next run without a rebuild.

```bash
# Run one ingest directly, with logs in the foreground. No arguments needed:
# the topic list comes from K2_EXCHANGES and the read range from the offsets in
# the last commit's Iceberg snapshot summary.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py

# One stage at a time while iterating on the decode
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --stage raw
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --stage bronze

# Report what framing each topic is actually carrying, writing nothing
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --probe

# Trigger the scheduled DAG through Airflow instead
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest

# Unit tests (pure Python: no stack needed)
uv run --no-project --with pytest --with pyyaml --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q
```

An ingest takes an exclusive `flock`, so a hand-run that collides with the 5-minute
schedule exits 75 rather than duplicating its work, and the scheduled run that hit the lock
is marked skipped. A schedule or parameter lives in `docker/airflow/dags/lake.py`; the
dag-processor re-reads a changed DAG file within 30 s, with nothing to redeploy
([operator guide, section 7](../operations/README.md#7-roll-out-a-merged-change)).

## Logs

| Source | Where |
|--------|-------|
| Any container | `docker compose logs -f <service>` or `make logs` |
| Capture | `docker logs -f k2-capture-binance`, `tracing` to stderr, filtered by `RUST_LOG`. No log files: nothing is bind-mounted out |
| Lake job runs | the Airflow task logs: the UI at http://localhost:8090, or under `/opt/airflow/logs` in `k2-airflow-scheduler` |
| Spark job detail | event logs in `/home/iceberg/spark-events` in `k2-spark-iceberg`. A running job's UI is port 4041 inside the container, not published (`docker exec k2-spark-iceberg curl -s localhost:4041/api/v1/applications`); http://localhost:4040 is the always-on Thrift server's UI |
| ClickHouse server | `docker logs k2-clickhouse` |

## Tearing down

```bash
make down                      # stop and remove the containers, keep volumes and data
docker compose down -v         # also DELETE the volumes: the lake, the hot tier, Redpanda, the catalog
```

## Related

- [testing.md](./testing.md), the test suites and what they do not cover
- [../operations/quick-reference.md](../operations/quick-reference.md), URLs, ports, credentials
- [../operations/adding-new-exchanges.md](../operations/adding-new-exchanges.md), the full checklist for a new feed
