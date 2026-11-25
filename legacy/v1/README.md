# K2 Platform v1 (archived)

The original v1 implementation of the K2 Market Data Platform, kept for reference and for the migration story in [`docs/MIGRATION-JOURNEY.md`](../../docs/MIGRATION-JOURNEY.md). It is not built by CI and is not part of the running stack. See [`../README.md`](../README.md) for the archive rules.

## What it was

Python end to end: Binance and Kraken WebSocket ingestion, then Kafka with Confluent Schema Registry, then five always-on Spark Structured Streaming jobs (Bronze, Silver, Gold OHLCV), then Apache Iceberg on MinIO, queried through DuckDB and served by FastAPI, with Prefect orchestrating ([`docs/MIGRATION-JOURNEY.md`](../../docs/MIGRATION-JOURNEY.md), "Why v2 existed").

- **Inputs:** public exchange WebSocket feeds (Binance, Kraken).
- **Outputs:** Iceberg tables on MinIO, a DuckDB hybrid query engine, a FastAPI service (`src/k2/api/`).
- **Footprint:** 18 to 20 containers at 35 to 40 CPU and 45 to 50 GB. Spark alone (master, two workers, five streaming jobs) was about 14 CPU / 20 GB, doing stateless per-record transforms at roughly 138 msg/s ([MIGRATION-JOURNEY](../../docs/MIGRATION-JOURNEY.md)).
- **When it ran:** the repo's first commit is 2026-01-09 and the v1 tags are `v1.0.0` (2026-02-09), `v1-stable` (2026-02-09) and `v1.1.0` (2026-02-10) (`git tag`). The date v1 stopped running as a deployment is not recorded.

| Path | Contents |
|---|---|
| `src/k2/` | Python package: `api/` (FastAPI), `ingestion/` (exchange clients, Kafka producer), `spark/` (streaming jobs, UDFs), `query/` (DuckDB hybrid engine), `storage/` |
| `tests/` | pytest suite (unit / integration / e2e / chaos / soak markers) |
| `scripts/`, `demos/`, `notebooks/` | Operational scripts, demo harness, exploration notebooks |
| `config/` | Flink, Hadoop, Kafka, Prometheus, Grafana configs for the v1 stack |
| `docs/runbooks/` | v1 operational runbooks (Kafka checkpoint recovery, Spark streaming ops, DuckDB pool tuning, DR) |
| `Dockerfile` | v1 API / ingestion image |

## Why it was archived

v1 worked, but it ran at more than twice the project's stated budget of 16 cores / 40 GB on one host. The pre-work checked whether tuning could close the gap; the best case after one to two weeks of tuning was about 28 CPU, so the conclusion was that the constraint "requires architectural change, not configuration tuning" ([MIGRATION-JOURNEY](../../docs/MIGRATION-JOURNEY.md), tuning table). v2 was built greenfield rather than migrated, and v1 was moved here on 2026-08-26 in `ef89133`, whose message records that nothing in `docker/`, `services/` or compose imported it.

The decisions are in [`docs/adr/`](../../docs/adr/): Kafka to Redpanda ([ADR-001](../../docs/adr/ADR-001-replace-kafka-with-redpanda.md)), Python ingestion to Kotlin feed handlers ([ADR-002](../../docs/adr/ADR-002-kotlin-feed-handlers.md)), DuckDB to ClickHouse ([ADR-003](../../docs/adr/ADR-003-clickhouse-warm-storage.md)), Spark Streaming to ClickHouse materialized views ([ADR-004](../../docs/adr/ADR-004-eliminate-spark-streaming.md), [ADR-009](../../docs/adr/ADR-009-medallion-in-clickhouse.md)), Spark kept batch-only ([ADR-006](../../docs/adr/ADR-006-spark-batch-only.md)), budget in [ADR-010](../../docs/adr/ADR-010-resource-budget.md). Measured result for v2: 15.1 CPU / 21.875 GB across 14 services (+2 one-shot), trade-to-queryable p99 under 200 ms instead of 5 to 15 minutes ([MIGRATION-JOURNEY](../../docs/MIGRATION-JOURNEY.md), "Measured outcomes"; the latency figure carries that document's small-sample caveat).

## What replaced each piece

| v1 | Successor |
|---|---|
| Python WebSocket ingestion (`src/k2/ingestion/`) | v2: Kotlin feed handlers (now [`../v2-kotlin/`](../v2-kotlin/README.md)); v3: [`services/capture-rust/`](../../services/capture-rust/) ([ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md)) |
| Kafka + Schema Registry | Redpanda with its built-in registry ([ADR-001](../../docs/adr/ADR-001-replace-kafka-with-redpanda.md)); topics in [`docker/redpanda/init.sh`](../../docker/redpanda/init.sh) |
| Spark Structured Streaming (`src/k2/spark/`) | v2: ClickHouse materialized views (now [`../v2-clickhouse/`](../v2-clickhouse/README.md)); v3: batch ingest in [`docker/lake/ingest.py`](../../docker/lake/ingest.py) |
| Iceberg on MinIO | Iceberg on MinIO behind Lakekeeper: [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql) |
| DuckDB query engine (`src/k2/query/`) | ClickHouse `gold` for the hot tier ([`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql)); DuckDB research notebooks in [`notebooks/`](../../notebooks/) |
| FastAPI (`src/k2/api/`) | none: the v2 query API was never built ([MIGRATION-JOURNEY](../../docs/MIGRATION-JOURNEY.md), predictions table) |
| Prefect orchestration | Prefect 3, retained ([ADR-008](../../docs/adr/ADR-008-eliminate-prefect-orchestration.md) Outcome); flows in [`docker/lake/flows/`](../../docker/lake/flows/) |
| `docs/runbooks/` (14) | [`docs/runbooks/`](../../docs/runbooks/README.md) (v3 runbooks); the v1 ones describe an architecture that no longer exists |

## State at archive

Archived on 2026-08-26 in commit `ef89133` ("chore: archive v1 platform under legacy/v1/"), which is contained in tag `v2.0.0` (`git tag --contains`). **The archived copy is not strictly unmodified.** Per its message, `ef89133` itself edited `pyproject.toml` (integration tests deselected by default, mypy config dropped, placeholder author fixed). Since then only dependency files and this README changed (`git diff --stat ef89133 HEAD -- legacy/v1`): `uv.lock` and the `pyasn1` pin in `pyproject.toml` via `174daed` (#84, 2026-08-27), `08d9d6a` (#169) and `54f5ab7` (#177) (both 2026-11-24), and README edits in `6428563` (#66) and later. No source, test or config file has changed since the archive commit.

## Running or verifying it today

```bash
cd legacy/v1
uv sync --all-extras
uv run pytest            # unit tests only; integration/e2e need the v1 Docker stack, which no longer exists
```

This proves the Python package imports and its unit tests pass; #84 and #169 record 180 tests passing after the lockfile upgrades. It proves nothing about behaviour against Kafka, Spark, MinIO or exchanges: the v1 Docker stack and its compose file are gone, so integration, e2e, chaos and soak tests cannot run. `make test` and CI do not run it.

## Known residue

### Known issues (left as-is)

- **Dependency CVEs, scanned 2026-08-27.** `uv.lock` was upgraded in-constraint (185
  packages, 180 tests green) after the repo went public (commit `174daed`, #84). Two HIGH findings were left then and
  will not be fixed here: `ecdsa 0.19.2` (CVE-2024-23342, no fixed release; pulled in by
  `python-jose`) and `protobuf 4.25.9` (CVE-2026-0994, fix is 5.29.6+; pinned below 5 by
  `opentelemetry-exporter-jaeger`). `uv.lock` still pins exactly those two versions today. Nothing in this tree runs anywhere, so the exposure
  is nil; CI's Trivy job skips `legacy/` for that reason (`skip-dirs: legacy` in `.github/workflows/ci.yml`) and this line is where the
  residue is recorded instead. Re-scan: `cd legacy/v1 && trivy fs --scanners vuln uv.lock`.
- **Dependabot still watches it.** `.github/dependabot.yml` has `/legacy/v1` in its `uv` ecosystem with version updates off and security updates grouped; #169 and #177 are the 2026-11-24 results. Alerts stay visible in the Security tab. [`SECURITY.md`](../../SECURITY.md) links to this section.
- `pyspark==4.1.1` is pinned while the v1 Spark cluster was 3.5. The comment in `pyproject.toml` is stale.
- `jupyter`, `pytest-*` and `docker` sit in the runtime dependency list rather than the `dev` extra.
- Roughly 1,100 `print()` calls in Spark jobs and scripts instead of `structlog`.
- A `LIMIT` clause in the v1 OHLCV endpoint interpolated a user-supplied integer without bounds checking. The API is not deployed anywhere.

### References elsewhere

`scripts/check-docs.sh` deliberately does not scan this directory (it is not in its published-docs list), so links inside the v1 docs may point at paths that no longer exist. Live-tree mentions: `Makefile`, `ruff.toml`, `.github/dependabot.yml`, `SECURITY.md`, `CLAUDE.md`, `docs/MIGRATION-JOURNEY.md`, `docs/README.md`.
