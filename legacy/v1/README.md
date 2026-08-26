# K2 Platform v1 (archived)

This directory holds the original v1 implementation of the K2 Market Data Platform. It is **archived** — kept for reference and for the migration story documented in [`docs/MIGRATION-JOURNEY.md`](../../docs/MIGRATION-JOURNEY.md). It is not maintained, not built by CI, and not part of the running stack.

## What v1 was

Python end to end: Binance/Kraken WebSocket ingestion → Kafka (+ Schema Registry) → Spark Structured Streaming (Bronze → Silver → Gold OHLCV) → Apache Iceberg on MinIO → DuckDB query engine → FastAPI, orchestrated by Prefect. It worked, but ran 18–20 containers at 35–40 CPU / 45–50 GB — five always-on Spark streaming jobs alone cost ~14 CPU / 20 GB.

## Why it was replaced

The v2 mandate was a 16-core / 40 GB single-host budget. The decision records in [`docs/decisions/`](../../docs/decisions/) explain each move: Kafka → Redpanda (ADR-001), Python ingestion → Kotlin feed handlers (ADR-002), DuckDB → ClickHouse (ADR-003), Spark Streaming → ClickHouse materialized views (ADR-004, ADR-009), Spark kept batch-only for Iceberg offload (ADR-006). Measured result: 15.1 CPU / 21.875 GB across 14 services (+2 one-shot), trade-to-queryable p99 under 200 ms instead of 5–15 minutes.

## Layout

| Path | Contents |
|---|---|
| `src/k2/` | Python package: `api/` (FastAPI), `ingestion/` (exchange clients, Kafka producer), `spark/` (streaming jobs, UDFs), `query/` (DuckDB hybrid engine), `storage/` |
| `tests/` | pytest suite (unit / integration / e2e / chaos / soak markers) |
| `scripts/`, `demos/`, `notebooks/` | Operational scripts, demo harness, exploration notebooks |
| `config/` | Flink, Hadoop, Kafka, Prometheus, Grafana configs for the v1 stack |
| `docs/runbooks/` | v1 operational runbooks (Kafka checkpoint recovery, Spark streaming ops, DuckDB pool tuning, DR) |
| `Dockerfile` | v1 API / ingestion image |

## Running the unit tests

```bash
cd legacy/v1
uv sync --all-extras
uv run pytest            # unit tests only; integration/e2e need the v1 Docker stack, which no longer exists
```

## Known issues (left as-is)

- `pyspark==4.1.1` is pinned while the v1 Spark cluster was 3.5 — the comment in `pyproject.toml` is stale.
- `jupyter`, `pytest-*` and `docker` sit in the runtime dependency list rather than the `dev` extra.
- Roughly 1,100 `print()` calls in Spark jobs and scripts instead of `structlog`.
- A `LIMIT` clause in the v1 OHLCV endpoint interpolated a user-supplied integer without bounds checking. The API is not deployed anywhere.
