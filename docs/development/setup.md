# Development Setup

Getting the stack running locally, and the inner loops for changing Rust or Python
code once it is. Running it day to day (health, consoles, stopping, recreating one service,
rolling out a change) is the [operator guide](../operations/README.md).

## Prerequisites

| Tool | Needed for | Notes |
|------|-----------|-------|
| Docker Engine + Compose v2 | everything | A Docker engine with ≥ 30 GiB memory so every `deploy.resources.limits` can be honoured, the 29.375 GiB bootstrap peak included, ≥ 31 GiB to hold both dbt-runner slots of a product run beside it, or ≥ 38 GiB to run the paused ClickHouse as well (`docker info --format '{{.MemTotal}}'`, **Docker Desktop defaults far lower and silently clips every limit above the VM size**; Settings → Resources → Memory); measured steady-state usage is far lower (see [../operations/docker-resources.md](../operations/docker-resources.md)), so the stack runs on less, but limits then exceed the engine. `python3 scripts/check-budget.py` prints the sums: steady state 15.80 CPU / 27.750 GiB across 19 long-running services (`steady default`), 17.30 / 29.375 across 23 while the four one-shot init containers run (`bootstrap default`), and that peak plus one profile: 18.30 / 30.875 with `build` (the two dbt-runner slots), 17.80 / 29.875 with `fetch`, 17.55 / 29.625 with `tools`, 21.30 / 37.375 with `hot-gold`. All are under the 25 CPU / 48 GiB ceiling of [ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md) (v2 alone: 15.1 CPU / 21.875 GB), computed on 2026-11-27 from the compose file after `k2-api` joined the default set. The engine requirement follows the declared sum, not the ceiling: 30 GiB today (38 with ClickHouse), 48 GiB if a later phase fills the ceiling. On Docker Desktop the host has a requirement of its own: the VM grows to its whole memory setting, so host RAM must exceed that setting plus everything else the host runs ([sizing](../runbooks/docker-vm-oom-kill.md#2-size-the-vm-so-the-host-keeps-enough-memory)) |
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
  with `invalid argument`, five of them at once (the three captures, Spark and Trino; six with
  the paused ClickHouse under `hot-gold`).
- `K2_AIRFLOW_PORT` (default `8090`) and `K2_TRINO_PORT` (default `8443`, HTTPS only since
  2026-11-27) are the Airflow and Trino host ports. `K2_AIRFLOW_PORT` also feeds Airflow's own base URL, so moving it
  moves the links its UI builds; set either if something else on the host owns the port.

`make up` builds three images on the first run: the Rust `k2-capture` binary, `k2-airflow`
and the Spark image (which bakes the Kafka and Avro jars, each pinned by sha256). Allow **several minutes**; subsequent starts are seconds. Services come up healthy in
dependency order: Redpanda, then `redpanda-init` creates the topics, registers the nine v3 Avro
subjects and exits (9 topics of 12 partitions, 108 in all, `market.crypto.v3.{raw,trades,book}.<ex>`;
the six v2 topics were deleted at the Phase E cutover on 2026-08-27 and are not recreated),
then the three `capture-*` containers.

Never commit `.env`. Everything reads its secrets from it; nothing in the repo hardcodes a
password.

### The safe bring-up (after the first run)

`make up` is `scripts/check-env.py`, then the raw `docker compose up -d`. For everyday use,
`make dev-up` runs the maintainer's bring-up checklist instead: the same `.env` check, then
it recreates any service holding a
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

ClickHouse is paused behind the compose profile `hot-gold`
([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)). When the profile
starts it on a fresh volume, the `gold` database auto-applies via `/docker-entrypoint-initdb.d`:
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

Nothing reads the topics further until phase 004: the v3 lake ingest pipeline is retired and
archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/), and ClickHouse, which
serves `gold` while the `hot-gold` profile runs, is paused (the v2 `k2` database was dropped on
2026-08-27, so any `k2.*` query is an error, not an empty result). See
[../architecture/README.md](../architecture/README.md); the per-component commands are in
[../operations/quick-reference.md](../operations/quick-reference.md).

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
make test-rust      # cargo test --locked in rust:1.100.0-bookworm
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

## Python inner loop

`docker/lake` keeps the modules the new platform's connectors reuse (`offsets.py`, the framing
half of `wire.py`, `bars.py`, `books.py` and the four modules `books.py` imports). They are
bind-mounted into the Spark container (`docker/lake` at `/home/iceberg/lake`), but nothing runs
them on the stack until
[phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md).
The v3 ingest's own loop (`ingest.py` by stage, its probe, the ingest DAG) is archived with it in
[`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/). The unit tests need no stack:

```bash
uv run --no-project --with pytest==9.2.0 --with pyyaml==6.0.4 --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q
```

Airflow reads its DAGs from the deployed release, not the checkout:
`docker/airflow/dags/factory.py`, through `/k2/releases/pointers/airflow/platform/docker/airflow/dags`,
builds one DAG per entry of the release's `compiled/airflow/dags.json`. A change to the factory
or to a product reaches Airflow only through a tag, `make release` and `make deploy` (deploy
drains a DAG whose shape changes); the dag-processor rereads the folder within 30 s of the swap
([operator guide, section 7](../operations/README.md#7-roll-out-a-merged-change)).

## Logs

| Source | Where |
|--------|-------|
| Any container | `docker compose logs -f <service>` or `make logs` |
| Capture | `docker logs -f k2-capture-binance`, `tracing` to stderr, filtered by `RUST_LOG`. No log files: nothing is bind-mounted out |
| DAG task runs | the Airflow task logs: the UI at http://localhost:8090, or under `/opt/airflow/logs` in `k2-airflow-scheduler` |
| Spark job detail | event logs in `/home/iceberg/spark-events` in `k2-spark-iceberg`, two days of them, browsable in the History Server at http://localhost:18080. A running job's UI is http://localhost:4040; a second driver running at the same time gets port 4041 <!-- counts-ok: a port number, not a count --> inside the container, not published (`docker exec k2-spark-iceberg curl -s localhost:4041/api/v1/applications`) |
| ClickHouse server, while `hot-gold` runs | `docker logs k2-clickhouse` |

## Tearing down

```bash
make down                      # stop and remove the containers, keep volumes and data
docker compose down -v         # also DELETE the volumes: the lake, the hot tier, Redpanda, the catalog
```

## Related

- [testing.md](./testing.md), the test suites and what they do not cover
- [../operations/quick-reference.md](../operations/quick-reference.md), URLs, ports, credentials
- [../operations/adding-new-exchanges.md](../operations/adding-new-exchanges.md), the full checklist for a new feed
