# Docker Resource Allocation

Every long-running service in [`docker-compose.yml`](../../docker-compose.yml) declares a
hard `limit` and a guaranteed `reservation`; the one-shot init containers declare limits
only. The ceiling is **25 CPU / 48 GiB at the bootstrap peak**
([ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md), which superseded the 20 CPU / 36 GiB of
[ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md), which superseded the 16-core / 40 GB
target of [ADR-010](../adr/ADR-010-resource-budget.md)), and `scripts/check-budget.py` fails CI
over it. The Docker engine offers 28 CPUs and 49.5 GiB; inside that, three cores and 1.5 GiB
stay outside the ceiling for the VM's own operating system and the engine, which is enough only
because measured use is far below the declared limits (ADR-045). **The ceiling says nothing
about the host.** On Docker Desktop the engine is a VM set to 51,712 MiB on a host with 62.6 GiB
of usable RAM, and the VM grows to that whole setting within hours whatever the containers use,
so the desktop and everything else on the host must fit in the remaining 12.1 GiB. On
2026-11-25 they did not, and the host killed the VM: ADR-045's 2026-11-26 Outcome, and the
sizing rule in [docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md#2-size-the-vm-so-the-host-keeps-enough-memory).

**As built, steady state: 19.30 CPU / 35.312 GiB across 19 long-running services.**
**One-shots: 2.00 CPU / 2.500 GiB across 5.** **Bootstrap peak: 21.30 CPU / 37.812 GiB
across 24**, the one-shots run concurrently with the steady state at `docker compose up`,
so that peak, not the steady state, is what the host has to absorb at boot, and it is the
sum the ceiling gates. The scheduler Airflow replaced, and the compose profile that kept it
as a rollback, are gone from the compose file and archived in
[`legacy/prefect/`](../../legacy/prefect/README.md) (ADR-034's 2026-11-26 Outcome).

Both parallel runs are over. The three Kotlin `feed-handler-*` containers are gone from
`docker-compose.yml` ([ADR-019](../adr/ADR-019-rust-capture-tier.md); code archived at
[`legacy/v2-kotlin/`](../../legacy/v2-kotlin/README.md)) and gave back exactly the
1.5 CPU / 1.5 GiB they declared.
[Phase D](../plans/2026-08-26-v3-quant-research-platform/003-phase-d-lake-tier.md)'s lake
ran its own cutover: the v2 offload path it replaced is deleted, and the 0.10 CPU /
128 MiB its exporter cost, plus the 0.50 CPU / 1 GiB of its `iceberg-init` one-shot , 
went with it.

The provenance command and the comparison against the prior published numbers are in the
Outcome addenda of [ADR-010](../adr/ADR-010-resource-budget.md).

### How these numbers are produced

Every figure on this page, and every budget figure elsewhere in the repo, is the limit sum
from the resolved compose file, not a hand-maintained tally:

```console
$ DOCKER_CONTEXT=default docker compose --env-file .env.example config | python3 -c '
import sys, yaml
svc = yaml.safe_load(sys.stdin)["services"]
for label, oneshot in (("steady", False), ("one-shot", True)):
    lim = [d["deploy"]["resources"]["limits"]
           for d in svc.values() if (d.get("restart") == "no") == oneshot]
    cpu = sum(float(l["cpus"]) for l in lim)
    gib = sum(int(l["memory"]) for l in lim) / 2**30
    print("%-9s %2d services  %5.2f CPU  %6.3f GiB" % (label, len(lim), cpu, gib))'
steady    19 services  19.30 CPU  35.312 GiB
one-shot   5 services   2.00 CPU   2.500 GiB
```

`docker compose config` normalises every `memory:` to bytes, so **GiB is what the limits
actually are**, `2g` in the compose file is 2 × 2³⁰, not 2 × 10⁹. Older figures on this
branch were written "GB" for the same quantities; the numbers did not change, the unit
label was wrong. Run on 2026-11-26, after Trino went to 5G. `python3 scripts/check-budget.py` prints the same sums.

## Allocation

| Service | Tier | CPU limit | CPU reserve | RAM limit | RAM reserve |
|---------|------|----------:|------------:|----------:|------------:|
| `redpanda` | streaming | 2.0 | 1.0 | 2 GB | 1 GB |
| `redpanda-console` | streaming | 0.5 | 0.1 | 256 MB | 128 MB |
| `clickhouse` | warm storage | 4.0 | 2.0 | 8 GB | 4 GB |
| `minio` | cold storage | 1.0 | 0.5 | 1 GB | 512 MB |
| `spark-iceberg` | lake batch engine | 3.0 | 1.0 | 8 GB | 2 GB |
| `prefect-db` | orchestration (Airflow and Lakekeeper metadata; the name is historical, kept by ADR-034) | 1.0 | 0.5 | 1.5 GB | 512 MB |
| `airflow-api-server` | orchestration (Airflow 3) | 1.0 | 0.25 | 1.5 GB | 512 MB |
| `airflow-scheduler` | orchestration (Airflow 3, LocalExecutor); runs `dbt_marts`' dbt commands, two at a time | 1.5 | 0.5 | 3 GB | 1 GB |
| `airflow-dag-processor` | orchestration (Airflow 3) | 0.5 | 0.1 | 1 GB | 256 MB |
| `airflow-statsd` | observability (Airflow) | 0.1 |, | 64 MB |, |
| `prometheus` | observability | 1.0 | 0.5 | 2 GB | 1 GB |
| `grafana` | observability | 0.5 | 0.25 | 512 MB | 256 MB |
| `cadvisor` | observability (per-container CPU and memory) | 0.1 |, | 128 MB |, |
| `capture-binance` | ingestion (v3) | 0.25 | 0.1 | 256 MB | 128 MB |
| `capture-kraken` | ingestion (v3) | 0.25 | 0.1 | 256 MB | 128 MB |
| `capture-coinbase` | ingestion (v3) | 0.25 | 0.1 | 512 MB | 128 MB |
| `lake-metrics` | observability (v3 lake) | 0.1 |, | 128 MB |, |
| `trino` | SQL over the lake, dbt marts ([ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md)) | 2.0 | 0.5 | 5 GB | 1 GB |
| **Steady state (19 long-running services)** | | **19.30** | **7.60** | **35.312 GiB** | **12.625 GiB** |
| `redpanda-init` | init (one-shot) | 0.25 |, | 128 MB |, |
| `lakekeeper-migrate` | init (one-shot) | 0.5 |, | 256 MB |, |
| `lake-init` | init (one-shot) | 0.25 |, | 128 MB |, |
| `lake-ddl` | init (one-shot) | 0.5 |, | 1 GB |, |
| `airflow-migrate` | init (one-shot) | 0.5 |, | 1 GB |, |
| **One-shot subtotal (5)** | | **2.00** | **, ** | **2.500 GiB** | **, ** |
| **Bootstrap peak (24 containers)** | | **21.30** | **7.60** | **37.812 GiB** | **12.625 GiB** |

`capture-coinbase` gets twice the memory of the other two because Coinbase's `level2`
channel is full depth, not top-20, its subscribe snapshot alone is 5.2 MB
([ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) Appendix A, S5). All three are
`cpuset`-pinned to cores 12–14 (`K2_CAPTURE_CPUSET`); ClickHouse, Spark, Trino and the
`lake-ddl` one-shot are pinned to the disjoint range 0–11 (`K2_HEAVY_CPUSET`), so a compaction or a heavy
query cannot share a core with capture, verified by `docker inspect -f '{{.HostConfig.CpusetCpus}}'`
and measured by the noisy-neighbour experiment in `docs/architecture/15-capacity-model.md`
(Trino's pin read `0-11` on the live stack on 2026-11-25).

The one-shots are **not free**. They declare limits and they run concurrently with the
steady state at `docker compose up`, so the bootstrap peak is the number the host has to
absorb.

Headroom against the 25 CPU / 48 GiB ceiling at the bootstrap peak, where it is gated:
**3.70 CPU and 10.188 GiB**. The
Airflow limits are the plan's estimates until ADR-034's measured figures replace them.

## Where the budget goes

- **ClickHouse takes 21% of CPU and 23% of RAM.** It absorbs the work v1 spent on five
  always-on Spark Streaming jobs, Kafka Engine ingest, Bronze→Silver→Gold materialized
  views and every analytical query run against the hot tier.
- **Spark is batch-only, but the container is never empty.** Its 3.0 CPU (2.0 until 2026-11-25) / 8 GiB (4 GiB until 2026-08-26) runs a job
  only during the 5-minute lake ingest and the nightly maintenance run, so the practical
  steady-state footprint is closer to 16.30 CPU / 26.312 GiB, but the base image's
  always-on Master, Worker, History Server, Thrift Server and Jupyter idle at **633 MiB**
  before any driver starts (`docker stats --no-stream k2-spark-iceberg`, 2026-08-26). The
  container was first sized for **two** drivers on top of that, the 03:00 maintenance
  run overlapping the 03:01 ingest tick, which is why `docker/lake/spark_conf.py` pins
  the ingest heap (768m then; 1536m since 2026-11-25, when the daily recompute outgrew it, see the comment there) instead of inheriting the image's 1 g, and why the `lake-ddl`
  one-shot sets `K2_LAKE_DRIVER_MEMORY: 512m` in its own 1 GiB container. Measured
  2026-08-26: ingest peak driver RSS **1,243 MiB** (plan 003, deployment gate), but
  compaction of `raw.messages` OOM'd at 768m twice, its rows are up to 5 MB each. So
  the two drivers are now **serialised, not co-resident**: `maintenance.py` takes the
  ingest `flock` blocking and runs with a **2g** heap (`K2_LAKE_MAINTENANCE_DRIVER_MEMORY`);
  an ingest tick that lands during it exits 75 at the lock. Budget with one driver at a
  time: 633 MiB + 2g + ~550 MiB overhead ≈ 3.2 GiB, which is why the limit went to
  8 GiB the same day: 4 GiB left no margin for an operator's `docker exec` beside it.
  **The limit only means something if the Docker engine has the memory:** this host's
  Docker Desktop VM was 7.6 GiB until 2026-08-26 (`docker info --format '{{.MemTotal}}'`),
  so every limit above that was clipped, `docker stats` showed ClickHouse as
  `/ 7.648GiB` against its declared 8G. It is 49.5 GiB and 28 CPUs now
  (`docker info --format '{{.MemTotal}} {{.NCPU}}'` printed `53149339648 28` on
  2026-11-25, and `53149335552 28` after the VM's restart on 2026-11-26, the same 51,712 MiB
  setting); `docs/development/setup.md` states the requirement.
- **The three capture containers cost 0.75 CPU / 1 GiB combined**, 4% of CPU, 3% of RAM.
  That is half what the Kotlin handlers they replaced declared. RSS against these limits
  is **not yet measured** for the post-retirement stack ([ADR-010](../adr/ADR-010-resource-budget.md)
  Outcome); they are declared ceilings, not sizing from observation.
- **Observability is 1.7 CPU / 2.750 GiB.** Prometheus and Grafana dominate it;
  `lake-metrics` (0.1 CPU / 128 MB) is the lake-alerts exporter, which reads Iceberg
  snapshot summaries over the catalog and holds no state, and `cadvisor` (0.1 CPU / 128 MB)
  reads each container's CPU, memory and limits from its cgroup for the K2 Resources
  dashboard. Standalone on this host, 2026-11-25, cAdvisor ran at 18 MiB RSS and under 0.3%
  of a core with these flags (`docker stats --no-stream`). Drop retention if you need the
  RAM back.
- **The Iceberg catalog costs 0.25 CPU / 256 MB.** Lakekeeper
  ([ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md)) is the whole of what the v3 lake
  added to the always-on budget beside its 0.1 CPU exporter, the hadoop-catalog warehouse
  and the offload exporter it replaced are both gone. It stays that cheap because it
  reuses `prefect-db` for its metadata (a `lakekeeper` database, not a second PostgreSQL) and
  MinIO for its storage. `docker stats --no-stream k2-lakekeeper` showed 3.25% CPU / 39 MiB
  after bootstrap, 2026-08-26, this fluctuates (0.00% / 33.95 MiB observed minutes later on
  the same run), so read the 0.25 CPU / 256 MB limit as the number that matters: idle usage is
  well under 50 MiB.

- **Trino costs 2.0 CPU / 5 GiB**, heap 2816M (`docker/trino/etc/jvm.config`), the other
  2.25 GiB of the limit for metaspace, code cache, thread stacks and direct buffers, which
  `jvm.config` does not cap. 4 GiB until 2026-11-26: after the dbt marts' first builds the
  cgroup's `anon` was 4,072,116,224 bytes of 4,294,967,296 (`docker exec k2-trino cat
  /sys/fs/cgroup/memory.stat`, 2026-11-25), so the limit moved before an out-of-memory kill
  ([ADR-010](../adr/ADR-010-resource-budget.md) and ADR-035 Outcomes). Measured
  2026-11-25 on a throwaway container with this configuration: 1.42 GiB idle; the
  `gold.candles()` aggregation over all of `gold.trades`, four at once, peaked at 2.86 GiB and
  2.15 cores ([`docker/trino/README.md`](../../docker/trino/README.md)). One such query ran at
  1 CPU / 2 GiB; the rest is for dbt models running in parallel.

## Sizing a new service

Adding a fourth exchange costs one more `k2-capture` container: **0.25 CPU / 256 MB
limit**, or 512 MB if the venue publishes full-depth L2 (see
[adding-new-exchanges.md](./adding-new-exchanges.md)). That is the only linear scaling
axis in the stack, Redpanda absorbs the three extra topics inside its existing
allocation.

## Verifying the numbers

```bash
# Declared limits, straight from the compose file
docker compose config | grep -A2 'limits:'

# Actual usage right now
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
```

The same comparison over time, per container and against the budget, is the K2 Resources
dashboard (http://localhost:3000/d/k2-resources). It reads each limit from the container's
cgroup through cAdvisor (`container_spec_cpu_quota` / `container_spec_cpu_period`,
`container_spec_memory_limit_bytes`), so it shows what Docker enforces, which is what
`docker compose config` declares once the container has been recreated. Its sums cover
running containers only: the one-shots have exited, so they sit 2.00 CPU / 2.500 GiB below
the bootstrap peak gated above.

Two known behaviours to expect when comparing declared vs. actual:

- Redpanda is started with `--smp 1 --memory 1500M`, i.e. it self-limits below its
  2 GB container limit. That is deliberate: the container limit is the safety net.

## Related

- [ADR-010, resource budget](../adr/ADR-010-resource-budget.md), the original target and the v1 comparison
- [ADR-004, eliminate Spark Streaming](../adr/ADR-004-eliminate-spark-streaming.md), where the 13.5 CPU saving came from
- [cost-model.md](./cost-model.md), what this footprint costs as managed cloud services
