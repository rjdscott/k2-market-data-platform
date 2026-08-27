# Development Setup

Getting the stack running locally, and the inner loops for changing Rust or Python
code once it is.

## Prerequisites

| Tool | Needed for | Notes |
|------|-----------|-------|
| Docker Engine + Compose v2 | everything | A Docker engine with ≥ 28 GB memory so every `deploy.resources.limits` can be honoured (`docker info --format '{{.MemTotal}}'` — **Docker Desktop defaults far lower and silently clips every limit above the VM size**; Settings → Resources → Memory); measured steady-state usage is far lower (see [../operations/docker-resources.md](../operations/docker-resources.md)), so the stack runs on less, but limits then exceed the engine and ClickHouse's 8 GB cap is not real. Steady state declares 14.60 CPU / 25.625 GiB of limits across 15 long-running services and peaks at 16.10 CPU / 27.125 GiB across 19 while the four one-shot init containers run (v2 alone: 15.1 CPU / 21.875 GB) |
| [`uv`](https://docs.astral.sh/uv/) | the Python unit tests and ruff | Nothing else uses Python locally |
| `jq`, `psql` | the diagnostic one-liners in the ops docs | optional |

## First run

```bash
cp .env.example .env      # then replace every change-me-in-production value
make up
make ps
```

`make up` builds three images on the first run — the Rust `k2-capture` binary, the Prefect
worker, and the Spark image (which bakes the Kafka and Avro jars, each pinned by sha256).
Allow **several minutes**; subsequent starts are seconds. Services come up healthy in
dependency order — Redpanda, then `redpanda-init` creates the topics, registers the nine v3
Avro subjects and exits (9 live v3 topics · 108 partitions,
`market.crypto.v3.{raw,trades,book}.<ex>`, plus the 6 frozen v2 topics · 160 partitions it
still creates so the `k2` Kafka-engine queues have something to attach to) — then the three
`capture-*` containers.

Never commit `.env`. Everything reads its secrets from it; nothing in the repo hardcodes a
password.

### The safe bring-up (after the first run)

`make up` is the raw `docker compose up -d`. For everyday use, `make dev-up` runs the
maintainer's bring-up checklist instead: it works around a busy host port 4200, recreates
any service holding a directory bind mount that changed in the last commit (see the
[bind-mount gotcha](../../docker/README.md#troubleshooting)), waits for health, and probes
that trades are actually flowing before declaring success. `bash scripts/dev-up.sh --dry-run`
prints what it would do without touching anything.

### Applying the ClickHouse schema

The `gold` database auto-applies on a fresh volume via `/docker-entrypoint-initdb.d`:
[`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql)
(the tables and views — the contract CI tests) then
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
for which symbols each capture container subscribes to — 12 Binance, 11 Kraken, 11
Coinbase. Each row carries a `native` (exactly the bytes on the wire — `BTCUSDT`,
`BTC/USD`, `BTC-USD`) and a `canonical` (`BASE/QUOTE`, the Kafka key and the lake join
key). Nothing translates a symbol in code; a native the file does not list is a loud
failure, not a guess.

> **Bind-mount inode gotcha.** The file is mounted file-by-file, which pins its inode. Most
> editors save by writing a temp file and renaming it, which produces a *new* inode — the
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

There is no local Rust toolchain requirement — compile and test in a container:

```bash
make test-rust      # cargo test --locked in rust:1-bookworm
```

That target reinstalls `cmake`/`clang` on every run. For a tight loop, build the builder
image once and mount named volumes for the cargo registry and target dir — a rebuild then
takes seconds instead of minutes. Both are in
[`services/capture-rust/README.md`](../../services/capture-rust/README.md), along with the
full `K2_*` / `--flag` table and the `k2-capture record` subcommand for capturing a replay
fixture.

Stop the containerised capture first (`docker compose stop capture-binance`) if you run a
second one, or both will produce to the same topics.

Tests: `make test-rust` — see [testing.md](./testing.md).

## Python lake inner loop

The lake scripts are bind-mounted into both the Spark and Prefect worker containers, so
edits take effect without a rebuild.

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

# Trigger the scheduled flow through Prefect instead
docker exec k2-prefect-server prefect deployment run 'lake-ingest/lake-ingest-5min'

# Unit tests (pure Python — no stack needed)
uv run --no-project --with pytest --with pyyaml pytest tests -q
```

An ingest takes an exclusive `flock`, so a hand-run that collides with the 5-minute cron
exits non-zero rather than duplicating its work. After changing a flow's schedule or
parameters, redeploy both:

```bash
docker exec k2-prefect-worker python /opt/prefect/lake-flows/deploy_lake.py
```

## Logs

| Source | Where |
|--------|-------|
| Any container | `docker compose logs -f <service>` or `make logs` |
| Capture | `docker logs -f k2-capture-binance` — `tracing` to stderr, filtered by `RUST_LOG`. No log files: nothing is bind-mounted out |
| Lake flow runs | `docker logs k2-prefect-worker`, plus run history at http://localhost:4200 |
| Spark job output | `docker logs k2-spark-iceberg`; the Application UI is on http://localhost:4040 while a job runs |
| ClickHouse server | `docker logs k2-clickhouse` |

## Tearing down

```bash
make down                      # stop, keep volumes and data
docker compose down -v         # also delete volumes — you will re-apply the schema
```

## Related

- [testing.md](./testing.md) — the test suites and what they do not cover
- [../operations/quick-reference.md](../operations/quick-reference.md) — URLs, ports, credentials
- [../operations/adding-new-exchanges.md](../operations/adding-new-exchanges.md) — the full checklist for a new feed
