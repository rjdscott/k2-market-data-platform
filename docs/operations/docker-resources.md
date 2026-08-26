# Docker Resource Allocation

Every long-running service in [`docker-compose.yml`](../../docker-compose.yml) declares a
hard `limit` and a guaranteed `reservation`; the one-shot init containers declare limits
only. The design target was a single 16-core / 40 GB host, and the as-built stack fits
inside it at steady state.

**As built, steady state: 14.70 CPU / 21.750 GiB across 16 long-running services.**
**One-shots: 2.00 CPU / 2.500 GiB across 5.** **Bootstrap peak: 16.70 CPU / 24.250 GiB
across 21** — the one-shots run concurrently with the steady state at `docker compose up`,
so that peak, not the steady state, is what the host has to absorb at boot. CPU limits are
a ceiling on scheduling rather than a reservation, so 16.70 on a 16-core host is a burst
that ends when the init containers exit, not a failure.

The three Kotlin `feed-handler-*` containers are gone from `docker-compose.yml`
([ADR-019](../adr/ADR-019-rust-capture-tier.md); code archived at
[`legacy/v2-kotlin/`](../../legacy/v2-kotlin/README.md)) and gave back exactly the
1.5 CPU / 1.5 GiB they declared. One parallel run is still being paid for:
[Phase D](../plans/2026-08-26-v3-quant-research-platform/003-phase-d-lake-tier.md) runs the
v3 lake (`lakekeeper`, `lake-metrics`) beside the v2 offload path (`iceberg-metrics`,
`iceberg-init`) it replaces. That is the cost of comparing an old path against its
replacement before trusting the new one, and it ends at the cutover — retiring
`docker/offload/` returns 0.10 CPU / 128 MiB steady and 0.50 CPU / 1 GiB of one-shot.

The provenance command and the comparison against the prior published numbers are in the
Outcome addenda of [ADR-010](../adr/ADR-010-resource-budget.md).

### How these numbers are produced

Every figure on this page, and every budget figure elsewhere in the repo, is the limit sum
from the resolved compose file — not a hand-maintained tally:

```console
$ docker compose --env-file .env.example config | python3 -c '
import sys, yaml
svc = yaml.safe_load(sys.stdin)["services"]
for label, oneshot in (("steady", False), ("one-shot", True)):
    lim = [d["deploy"]["resources"]["limits"]
           for d in svc.values() if (d.get("restart") == "no") == oneshot]
    cpu = sum(float(l["cpus"]) for l in lim)
    gib = sum(int(l["memory"]) for l in lim) / 2**30
    print("%-9s %2d services  %5.2f CPU  %6.3f GiB" % (label, len(lim), cpu, gib))'
steady    16 services  14.70 CPU  21.750 GiB
one-shot   5 services   2.00 CPU   2.500 GiB
```

`docker compose config` normalises every `memory:` to bytes, so **GiB is what the limits
actually are** — `2g` in the compose file is 2 × 2³⁰, not 2 × 10⁹. Older figures on this
branch were written "GB" for the same quantities; the numbers did not change, the unit
label was wrong. Run on 2026-08-26.

## Allocation

| Service | Tier | CPU limit | CPU reserve | RAM limit | RAM reserve |
|---------|------|----------:|------------:|----------:|------------:|
| `redpanda` | streaming | 2.0 | 1.0 | 2 GB | 1 GB |
| `redpanda-console` | streaming | 0.5 | 0.1 | 256 MB | 128 MB |
| `clickhouse` | warm storage | 4.0 | 2.0 | 8 GB | 4 GB |
| `minio` | cold storage | 1.0 | 0.5 | 1 GB | 512 MB |
| `spark-iceberg` | cold storage | 2.0 | 1.0 | 4 GB | 2 GB |
| `lakekeeper` | cold storage | 0.25 | 0.1 | 256 MB | 128 MB |
| `prefect-db` | orchestration | 1.0 | 0.5 | 1 GB | 512 MB |
| `prefect-server` | orchestration | 1.0 | 0.5 | 1 GB | 512 MB |
| `prefect-worker` | orchestration | 0.5 | 0.25 | 512 MB | 256 MB |
| `prometheus` | observability | 1.0 | 0.5 | 2 GB | 1 GB |
| `grafana` | observability | 0.5 | 0.25 | 512 MB | 256 MB |
| `capture-binance` | ingestion (v3) | 0.25 | 0.1 | 256 MB | 128 MB |
| `capture-kraken` | ingestion (v3) | 0.25 | 0.1 | 256 MB | 128 MB |
| `capture-coinbase` | ingestion (v3) | 0.25 | 0.1 | 512 MB | 128 MB |
| `iceberg-metrics` | observability (v2 offload) | 0.1 | — | 128 MB | — |
| `lake-metrics` | observability (v3 lake) | 0.1 | — | 128 MB | — |
| **Steady state (16 long-running services)** | | **14.70** | **7.00** | **21.750 GiB** | **10.625 GiB** |
| `redpanda-init` | init (one-shot) | 0.25 | — | 128 MB | — |
| `iceberg-init` | init (one-shot) | 0.5 | — | 1 GB | — |
| `lakekeeper-migrate` | init (one-shot) | 0.5 | — | 256 MB | — |
| `lake-init` | init (one-shot) | 0.25 | — | 128 MB | — |
| `lake-ddl` | init (one-shot) | 0.5 | — | 1 GB | — |
| **One-shot subtotal (5)** | | **2.00** | **—** | **2.500 GiB** | **—** |
| **Bootstrap peak (21 containers)** | | **16.70** | **7.00** | **24.250 GiB** | **10.625 GiB** |

`capture-coinbase` gets twice the memory of the other two because Coinbase's `level2`
channel is full depth, not top-20 — its subscribe snapshot alone is 5.2 MB
([ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) Appendix A, S5). All three are
`cpuset`-pinned to cores 12–14 (`K2_CAPTURE_CPUSET`).

The one-shots are **not free**. They declare limits and they run concurrently with the
steady state at `docker compose up`, so the bootstrap peak is the number the host has to
absorb.

Headroom against the 16 CPU / 40 GB envelope at steady state: **1.30 CPU (8%) and
18.250 GiB (46%)**. At the bootstrap peak it is −0.70 CPU for as long as the one-shots
run, and 15.750 GiB.

Where the remaining cutover lands it:

| After | Services | CPU | RAM | Headroom |
|---|---:|---:|---:|---|
| Today (v3 lake beside the v2 offload) | 16 | 14.70 | 21.750 GiB | 1.30 CPU (8%) · 18.250 GiB (46%) |
| `iceberg-metrics` goes with the rest of `docker/offload/` (−0.10 / −128 MiB) | 15 | 14.60 | 21.625 GiB | 1.40 CPU (9%) · 18.375 GiB (46%) |

## Where the budget goes

- **ClickHouse takes 27% of CPU and 37% of RAM.** It absorbs the work v1 spent on five
  always-on Spark Streaming jobs — Kafka Engine ingest, Bronze→Silver→Gold materialized
  views and every analytical query run against the hot tier.
- **Spark is batch-only.** Its 2.0 CPU / 4 GB is idle except during the 15-minute offload
  cycle, so the practical steady-state footprint is closer to 12.60 CPU / 17.625 GiB.
- **The three capture containers cost 0.75 CPU / 1 GiB combined** — 5% of CPU, 5% of RAM.
  That is half what the Kotlin handlers they replaced declared. RSS against these limits
  is **not yet measured** for the post-retirement stack ([ADR-010](../adr/ADR-010-resource-budget.md)
  Outcome); they are declared ceilings, not sizing from observation.
- **Observability is 1.6 CPU / 2.625 GB.** Prometheus and Grafana dominate it; `iceberg-metrics`
  (0.1 CPU / 128 MB) is the offload-alerts exporter. Drop retention if you need the RAM back.
- **The Iceberg catalog costs 0.25 CPU / 256 MB.** Lakekeeper (v3, [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md))
  is the only always-on service added since the v2 baseline. It stays that cheap because it
  reuses `prefect-db` for its metadata (a `lakekeeper` database, not a second PostgreSQL) and
  MinIO for its storage. `docker stats --no-stream k2-lakekeeper` showed 3.25% CPU / 39 MiB
  after bootstrap, 2026-08-26 — this fluctuates (0.00% / 33.95 MiB observed minutes later on
  the same run), so read the 0.25 CPU / 256 MB limit as the number that matters: idle usage is
  well under 50 MiB.

## Sizing a new service

Adding a fourth exchange costs one more `k2-capture` container: **0.25 CPU / 256 MB
limit**, or 512 MB if the venue publishes full-depth L2 (see
[adding-new-exchanges.md](./adding-new-exchanges.md)). That is the only linear scaling
axis in the stack — Redpanda absorbs the three extra topics inside its existing
allocation.

## Verifying the numbers

```bash
# Declared limits, straight from the compose file
docker compose config | grep -A2 'limits:'

# Actual usage right now
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
```

Two known behaviours to expect when comparing declared vs. actual:

- `prefect-worker` spikes to ~488 MiB during startup before settling near 100 MiB. The
  limit is 512 MiB — raise it to 768 MiB if you ever see it OOM-killed.
- Redpanda is started with `--smp 1 --memory 1500M`, i.e. it self-limits below its
  2 GB container limit. That is deliberate: the container limit is the safety net.

## Related

- [ADR-010 — resource budget](../adr/ADR-010-resource-budget.md) — the original target and the v1 comparison
- [ADR-004 — eliminate Spark Streaming](../adr/ADR-004-eliminate-spark-streaming.md) — where the 13.5 CPU saving came from
- [cost-model.md](./cost-model.md) — what this footprint costs as managed cloud services
