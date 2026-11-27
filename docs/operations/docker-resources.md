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

**As built, steady state: 15.80 CPU / 27.750 GiB across 19 long-running services.**
**One-shots: 1.50 CPU / 1.500 GiB across 4.** **Bootstrap peak: 17.30 CPU / 29.250 GiB
across 23**, the one-shots run concurrently with the steady state at `docker compose up`,
so that peak, not the steady state, is what the host has to absorb at boot, and it is the
sum the ceiling gates. These figures were computed on 2026-11-27 from the compose file after
`k2-api` joined the default set (0.5 CPU / 512M), both by `scripts/check-budget.py`'s own
summing (its `steady default` and `bootstrap default` lines) and by the command below; before
it the file printed 15.30 / 27.250 across 18 and 16.80 / 28.750 across 22, and before
`alertmanager` joined it (0.1 CPU / 64M, 2026-11-26) 15.20 / 27.188 across 17 and 16.70 / 28.688
across 21. The scheduler Airflow replaced, and the compose profile that kept it
as a rollback, are gone from the compose file and archived in
[`legacy/prefect/`](../../legacy/prefect/README.md) (ADR-034's 2026-11-26 Outcome).

**ClickHouse is paused** behind the compose profile `hot-gold`
([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)), so none of these sums counts it: a plain `docker compose config` leaves out
a service under a profile that is not named. With the profile running it adds its 4.0 CPU /
8 GiB: bootstrap peak 21.30 / 37.250 across 24, the `bootstrap hot-gold` line
`python3 scripts/check-budget.py` prints.

**The run path's one-shots are not in the sums either.** `fetch` (profile `fetch`), `dbt-runner`
(profile `build`) and `tools` (profile `tools`) are started by the Airflow scheduler or by deploy,
backup and the access suite for one task each, then removed. `python3 scripts/check-budget.py`
reports each as the bootstrap peak plus that profile, and gates only the default: `bootstrap
build` 18.30 / 30.750 (both dbt-runner slots, `deploy.replicas: 2`), `bootstrap fetch` 17.80 /
29.750, `bootstrap tools` 17.55 / 29.500. Airflow's pools hold at most one fetch and two dbt
builds at once (`docker/airflow/dags/factory.py`, `POOLS`).

Both parallel runs are over. The three Kotlin `feed-handler-*` containers are gone from
`docker-compose.yml` ([ADR-019](../adr/ADR-019-rust-capture-tier.md); code archived at
[`legacy/v2-kotlin/`](../../legacy/v2-kotlin/README.md)) and gave back exactly the
1.5 CPU / 1.5 GiB they declared.
[Phase D](../plans/2026-08-26-v3-quant-research-platform/003-phase-d-lake-tier.md)'s lake
ran its own cutover: the v2 offload path it replaced is deleted, and the 0.10 CPU /
128 MiB its exporter cost, plus the 0.50 CPU / 1 GiB of its `iceberg-init` one-shot,
went with it. The v3 lake's own exporter, `lake-metrics` (0.10 CPU / 128 MiB), and its
`lake-ddl` one-shot (0.50 CPU / 1 GiB) went the same way with the v3 lake ingest pipeline on
2026-11-26 ([`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/),
[ADR-056](../adr/ADR-056-unified-data-product-platform.md)).

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
steady    19 services  15.80 CPU  27.750 GiB
one-shot   4 services   1.50 CPU   1.500 GiB
```

`docker compose config` normalises every `memory:` to bytes, so **GiB is what the limits
actually are**, `2g` in the compose file is 2 × 2³⁰, not 2 × 10⁹. Older figures on this
branch were written "GB" for the same quantities; the numbers did not change, the unit
label was wrong. Run on 2026-11-27 against the compose file with `k2-api` in the default set,
which is what the printed lines are; before it the steady line read 18 / 15.30 / 27.250, and
before `alertmanager` 17 / 15.20 / 27.188. A plain `config` leaves out every service under a
profile, so these two lines never count `fetch`, `dbt-runner`, `tools` or `clickhouse`.
`python3 scripts/check-budget.py` prints the same sums.

## Allocation

| Service | Tier | CPU limit | CPU reserve | RAM limit | RAM reserve |
|---------|------|----------:|------------:|----------:|------------:|
| `redpanda` | streaming | 2.0 | 1.0 | 2 GB | 1 GB |
| `redpanda-console` | streaming | 0.5 | 0.1 | 256 MB | 128 MB |
| `clickhouse` | warm storage, **paused** behind `hot-gold` (ADR-057): not in the sums below | 4.0 | 2.0 | 8 GB | 4 GB |
| `minio` | cold storage | 1.0 | 0.5 | 1 GB | 512 MB |
| `spark-iceberg` | lake batch engine | 3.0 | 1.0 | 8 GB | 2 GB |
| `prefect-db` | orchestration (Airflow and Lakekeeper metadata; the name is historical, kept by ADR-034) | 1.0 | 0.5 | 1.5 GB | 512 MB |
| `airflow-api-server` | orchestration (Airflow 3) | 1.0 | 0.25 | 1.5 GB | 512 MB |
| `airflow-scheduler` | orchestration (Airflow 3, LocalExecutor); runs one DAG per product, starts its fetch and dbt-runner one-shots through the Docker socket | 1.5 | 0.5 | 3 GB | 1 GB |
| `airflow-dag-processor` | orchestration (Airflow 3) | 0.5 | 0.1 | 1 GB | 256 MB |
| `airflow-statsd` | observability (Airflow) | 0.1 |, | 64 MB |, |
| `prometheus` | observability | 1.0 | 0.5 | 2 GB | 1 GB |
| `grafana` | observability | 0.5 | 0.25 | 512 MB | 256 MB |
| `cadvisor` | observability (per-container CPU and memory) | 0.1 |, | 128 MB |, |
| `alertmanager` | observability (alerts to Slack) | 0.1 |, | 64 MB |, |
| `capture-binance` | ingestion (v3) | 0.25 | 0.1 | 256 MB | 128 MB |
| `capture-kraken` | ingestion (v3) | 0.25 | 0.1 | 256 MB | 128 MB |
| `capture-coinbase` | ingestion (v3) | 0.25 | 0.1 | 512 MB | 128 MB |
| `trino` | SQL over the lake and the domain catalogs ([ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md)) | 2.0 | 0.5 | 5 GB | 1 GB |
| `k2-api` | serving: read-only REST, the catalog site and the dbt docs ([`services/k2-api/`](../../services/k2-api/README.md)) | 0.5 |, | 512 MB |, |
| **Steady state (19 long-running services)** | | **15.80** | **5.60** | **27.750 GiB** | **8.625 GiB** |
| `redpanda-init` | init (one-shot) | 0.25 |, | 128 MB |, |
| `lakekeeper-migrate` | init (one-shot) | 0.5 |, | 256 MB |, |
| `lake-init` | init (one-shot) | 0.25 |, | 128 MB |, |
| `airflow-migrate` | init (one-shot) | 0.5 |, | 1 GB |, |
| **One-shot subtotal (4)** | | **1.50** | **, ** | **1.500 GiB** | **, ** |
| **Bootstrap peak (23 containers)** | | **17.30** | **5.60** | **29.250 GiB** | **8.625 GiB** |
| `fetch` | run path (one-shot, profile `fetch`): one product's file fetch and parse, on `k2-egress` alone | 0.5 |, | 512 MB |, |
| `dbt-runner` | run path (one-shot, profile `build`): the dbt wrapper, two slots (`deploy.replicas: 2`) | 0.5 × 2 |, | 768 MB × 2 |, |
| `tools` | release, deploy, backup and the access suite (one-shot, profile `tools`) | 0.25 |, | 256 MB |, |

`capture-coinbase` gets twice the memory of the other two because Coinbase's `level2`
channel is full depth, not top-20, its subscribe snapshot alone is 5.2 MB
([ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md) Appendix A, S5). All three are
`cpuset`-pinned to cores 12–14 (`K2_CAPTURE_CPUSET`); ClickHouse, Spark and Trino are pinned
to the disjoint range 0–11 (`K2_HEAVY_CPUSET`), so a compaction or a heavy
query cannot share a core with capture, verified by `docker inspect -f '{{.HostConfig.CpusetCpus}}'`
and measured by the noisy-neighbour experiment in `docs/architecture/15-capacity-model.md`
(Trino's pin read `0-11` on the live stack on 2026-11-25).

The one-shots are **not free**. They declare limits and they run concurrently with the
steady state at `docker compose up`, so the bootstrap peak is the number the host has to
absorb.

Headroom against the 25 CPU / 48 GiB ceiling at the bootstrap peak, where it is gated:
**7.70 CPU and 18.750 GiB**; 3.70 and 10.750 with the `hot-gold` profile running. The
Airflow limits are the plan's estimates until ADR-034's measured figures replace them.

## Where the budget goes

- **ClickHouse took 21% of CPU and 23% of RAM while it ran**; it is paused (ADR-057). It absorbs the work v1 spent on five
  always-on Spark Streaming jobs, Kafka Engine ingest, Bronze→Silver→Gold materialized
  views and every analytical query run against the hot tier.
- **Spark is batch-only, but the container is never empty.** Its 3.0 CPU (2.0 until 2026-11-25) / 8 GiB (4 GiB until 2026-08-26) runs
  the run path's Spark tasks (each product's load, audit, publish, expire and daily maintain,
  one at a time in pool `lake_spark`), which are small daily or weekly batches; the 5-minute
  v3 lake ingest and nightly maintenance it was sized for retired with the v3 lake.
  The old tabulario base image's always-on standalone cluster (a master and one executor host), History Server, Thrift
  Server and Jupyter idled at **633 MiB** before any driver started (`docker stats --no-stream
  k2-spark-iceberg`, 2026-08-26). The official-image rebuild (`k2-spark-iceberg:3.5.9_1.8.1`,
  2026-11-26) runs only the History Server between jobs: **167 MiB** idle in a throwaway
  container started from `spark-iceberg`'s compose definition (`docker stats --no-stream`,
  2026-11-26), not yet re-read on `k2-spark-iceberg` itself. The 8 GiB was set on 2026-08-26
  for the v3 jobs (now archived): an ingest driver peaked at **1,243 MiB** RSS (plan 003,
  deployment gate) and compaction of the raw archive table ran out of heap at 768m twice, so
  maintenance ran alone with a 2g heap; one 2g driver plus the image's idle and about 550 MiB of
  overhead is about 3.2 GiB, and 4 GiB left no margin for an operator's `docker exec` beside it.
  The new platform's per-product jobs are sized in their own phases.
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
- **Observability is 1.7 CPU / 2.688 GiB.** Prometheus and Grafana dominate it,
  `cadvisor` (0.1 CPU / 128 MB)
  reads each container's CPU, memory and limits from its cgroup for the K2 Resources
  dashboard, and `alertmanager` (0.1 CPU / 64 MB) routes alerts to Slack: 19.5 MiB under
  `make alert-test-standin` on 2026-11-26 (`docker stats --no-stream k2-alerttest-am`). Standalone on this host, 2026-11-25, cAdvisor ran at 18 MiB RSS and under 0.3%
  of a core with these flags (`docker stats --no-stream`). Drop retention if you need the
  RAM back.
- **The Iceberg catalog costs 0.25 CPU / 256 MB.** Lakekeeper
  ([ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md)) is the whole of what the v3 lake
  added to the always-on budget beside its 0.1 CPU exporter (retired with it), the
  hadoop-catalog warehouse and the offload exporter it replaced are both gone. It stays that cheap because it
  reuses `prefect-db` for its metadata (a `lakekeeper` database, not a second PostgreSQL) and
  MinIO for its storage. `docker stats --no-stream k2-lakekeeper` showed 3.25% CPU / 39 MiB
  after bootstrap, 2026-08-26, this fluctuates (0.00% / 33.95 MiB observed minutes later on
  the same run), so read the 0.25 CPU / 256 MB limit as the number that matters: idle usage is
  well under 50 MiB.

- **Trino costs 2.0 CPU / 5 GiB**, heap 2816M (`jvm.config` in the release's `compiled/trino/etc/`, written by
  [`emit/trino_etc.py`](../../platform/k2gen/k2gen/emit/trino_etc.py)), the other
  2.25 GiB of the limit for metaspace, code cache, thread stacks and direct buffers, which
  `jvm.config` does not cap. 4 GiB until 2026-11-26: after the dbt marts' first builds the
  cgroup's `anon` was 4,072,116,224 bytes of 4,294,967,296 (`docker exec k2-trino cat
  /sys/fs/cgroup/memory.stat`, 2026-11-25), so the limit moved before an out-of-memory kill
  ([ADR-010](../adr/ADR-010-resource-budget.md) and ADR-035 Outcomes). Measured
  2026-11-25 on a throwaway container with this configuration: 1.42 GiB idle; the v3 lake's
  candle aggregation over its whole trades table (both since retired), four at once, peaked at
  2.86 GiB and 2.15 cores ([`docker/trino/README.md`](../../docker/trino/README.md)). One such
  query ran at 1 CPU / 2 GiB; the rest is for dbt models running in parallel.

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
running containers only: the one-shots have exited, so they sit 1.50 CPU / 1.500 GiB below
the bootstrap peak gated above.

Two known behaviours to expect when comparing declared vs. actual:

- Redpanda is started with `--smp 1 --memory 1500M`, i.e. it self-limits below its
  2 GB container limit. That is deliberate: the container limit is the safety net.

## Related

- [ADR-010, resource budget](../adr/ADR-010-resource-budget.md), the original target and the v1 comparison
- [ADR-004, eliminate Spark Streaming](../adr/ADR-004-eliminate-spark-streaming.md), where the 13.5 CPU saving came from
- [cost-model.md](./cost-model.md), what this footprint costs as managed cloud services
