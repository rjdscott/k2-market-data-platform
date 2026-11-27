# ADR-010: Resource Budget and Docker Compose Constraints

**Status:** Accepted, Implemented (2026-02), see Outcome; the 16 CPU / 40 GB ceiling **superseded by [ADR-034](ADR-034-airflow-3-replaces-prefect.md)** (20 CPU / 36 GiB at the bootstrap peak), then by **[ADR-045](ADR-045-ceiling-25-cpu-48-gib.md)** (25 CPU / 48 GiB at the bootstrap peak); scoped, the rest stands
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** Infrastructure

---

## Context

The mandate is clear: **16 cores, 40GB RAM, single Docker Compose cluster.**

The current platform consumes **35-40 CPUs / 45-50GB RAM** — more than 2x the budget on both axes. This ADR documents the resource allocation for every service in the v2 architecture and proves the budget is achievable.

## Decision

**Allocate resources per the budget below, with 15% headroom for burst capacity.**

## Resource Budget

### v2 Service Allocation

| Service | CPU Limit | CPU Reserve | RAM Limit | RAM Reserve | Justification |
|---------|-----------|-------------|-----------|-------------|---------------|
| **Redpanda** | 2.0 | 1.0 | 2GB | 1.5GB | Thread-per-core; 2 cores handles 100K msg/s |
| **ClickHouse** | 4.0 | 2.0 | 8GB | 4GB | Primary query engine; vectorized execution benefits from cores + RAM |
| **Kotlin Feed Handlers** | 1.0 | 0.5 | 512MB | 256MB | All exchanges in single JVM; coroutines are lightweight |
| **Kotlin Silver Processor** | 1.0 | 0.5 | 512MB | 256MB | Validation is CPU-light; batch inserts to ClickHouse |
| **Spring Boot API** | 2.0 | 1.0 | 1GB | 512MB | Virtual threads; HikariCP pool; JSON serialization |
| **Spark Batch** (on-demand) | 2.0 | 0 | 4GB | 0 | Only runs 2h/day; resources shared when idle |
| **MinIO** | 1.0 | 0.5 | 1GB | 512MB | Cold storage I/O; reduced from v1 (less frequent access) |
| **PostgreSQL** (Iceberg catalog) | 0.5 | 0.25 | 512MB | 256MB | Metadata only; tiny workload |
| **Iceberg REST Catalog** | 0.5 | 0.25 | 512MB | 256MB | Catalog API; lightweight |
| **Prometheus** | 0.5 | 0.25 | 512MB | 256MB | Metrics scraping; 15s interval |
| **Grafana** | 0.5 | 0.25 | 512MB | 256MB | Dashboard rendering |
| | | | | | |
| **Total (all services)** | **15.5** | **6.5** | **19.5GB** | **8GB** | |
| **Total (Spark idle)** | **13.5** | **6.5** | **15.5GB** | **8GB** | 22h/day |
| **Budget** | **16.0** | — | **40GB** | — | |
| **Headroom** | **0.5 (3%)** | — | **20.5GB (51%)** | — | |
| **Headroom (Spark idle)** | **2.5 (16%)** | — | **24.5GB (61%)** | — | |

### v1 vs v2 Comparison

| Category | v1 | v2 | Reduction |
|----------|----|----|-----------|
| **Total CPU (limits)** | 35-40 | 15.5 | **56-61%** |
| **Total RAM (limits)** | 45-50GB | 19.5GB | **57-61%** |
| **Service count** | 18-20 | 11 | **42-45%** |
| **JVM processes** | 8 (Kafka, SR, Spark×5, Prefect) | 3 (Feed, Silver, API) | **63%** |
| **Python processes** | 4 (2 handlers, Prefect×2) | 0 | **100%** |
| **Always-on Spark** | 14 CPU / 20GB | 0 (batch only) | **100%** |

### Service Elimination Summary

| v1 Service | v2 Replacement | CPU Saved | RAM Saved |
|------------|----------------|-----------|-----------|
| Kafka (1.5 CPU / 2GB) | Redpanda (shared) | 0.5 | 0.5GB |
| Schema Registry (0.5 CPU / 768MB) | Redpanda built-in | 0.5 | 768MB |
| Kafka UI (0.5 CPU / 512MB) | Redpanda Console | 0.5 | 512MB |
| Spark Master (2.0 CPU / 2GB) | Eliminated | 2.0 | 2GB |
| Spark Worker 1 (3.5 CPU / 4GB) | Eliminated | 3.5 | 4GB |
| Spark Worker 2 (3.5 CPU / 4GB) | Eliminated | 3.5 | 4GB |
| Bronze Binance Stream (1.0 CPU / 2GB) | ClickHouse Kafka Engine | 1.0 | 2GB |
| Bronze Kraken Stream (1.0 CPU / 2GB) | ClickHouse Kafka Engine | 1.0 | 2GB |
| Silver Binance Transform (1.0 CPU / 2GB) | Kotlin Silver Processor | 0.0 | 1.5GB |
| Silver Kraken Transform (1.0 CPU / 2GB) | Kotlin Silver Processor | 1.0 | 2GB |
| Gold Aggregation (1.0 CPU / 2GB) | ClickHouse MVs | 1.0 | 2GB |
| Prefect Server (0.5 CPU / 512MB) | Eliminated | 0.5 | 512MB |
| Prefect Agent (0.25 CPU / 256MB) | Eliminated | 0.25 | 256MB |
| Binance Handler Python (0.5 CPU / 512MB) | Kotlin Feed Handler | 0.0 | 0 |
| Kraken Handler Python (0.5 CPU / 512MB) | Kotlin Feed Handler | 0.5 | 512MB |
| **Total eliminated** | | **~16 CPU** | **~23GB** |

### Burst Capacity Planning

During the 2h daily Spark batch window, the platform uses 15.5 CPU / 19.5GB. The remaining 22h/day, Spark is idle:

```
Off-peak (22h/day): 13.5 CPU / 15.5GB used → 2.5 CPU / 24.5GB available for burst
Peak (2h/day):      15.5 CPU / 19.5GB used → 0.5 CPU / 20.5GB available for burst
```

ClickHouse benefits from burst capacity — during off-peak hours, it can use up to 6.5 cores (4 limit + 2.5 spare) for heavy analytical queries.

### Docker Compose Resource Configuration

```yaml
version: "3.8"

services:
  redpanda:
    image: docker.redpanda.com/redpandadata/redpanda:latest
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 2G
        reservations:
          cpus: "1.0"
          memory: 1536M
    command:
      - redpanda start
      - --smp 2
      - --memory 1536M
      - --reserve-memory 0M

  clickhouse:
    image: clickhouse/clickhouse-server:latest
    deploy:
      resources:
        limits:
          cpus: "4.0"
          memory: 8G
        reservations:
          cpus: "2.0"
          memory: 4G

  feed-handlers:
    image: k2/feed-handlers:latest
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
        reservations:
          cpus: "0.5"
          memory: 256M
    environment:
      JAVA_OPTS: "-Xmx384m -Xms256m -XX:+UseZGC"

  silver-processor:
    image: k2/silver-processor:latest
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
        reservations:
          cpus: "0.5"
          memory: 256M
    environment:
      JAVA_OPTS: "-Xmx384m -Xms256m -XX:+UseZGC"

  api:
    image: k2/api:latest
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 1G
        reservations:
          cpus: "1.0"
          memory: 512M
    environment:
      JAVA_OPTS: "-Xmx768m -Xms512m -XX:+UseZGC"

  spark-batch:
    image: k2/spark-batch:latest
    profiles: ["batch"]
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 4G

  minio:
    image: minio/minio:latest
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1G
        reservations:
          cpus: "0.5"
          memory: 512M

  postgres:
    image: postgres:16-alpine
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M

  iceberg-rest:
    image: tabulario/iceberg-rest:latest
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M

  prometheus:
    image: prom/prometheus:latest
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M

  grafana:
    image: grafana/grafana:latest
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M
```

## Monitoring the Budget

### Key Metrics to Track

```yaml
# Grafana alerts
- alert: CPUBudgetExceeded
  expr: sum(container_cpu_usage_seconds_total) > 14.0  # 87.5% of 16
  for: 5m

- alert: MemoryBudgetExceeded
  expr: sum(container_memory_usage_bytes) > 34359738368  # 32GB (80% of 40)
  for: 5m

- alert: ClickHouseMemoryHigh
  expr: container_memory_usage_bytes{name="clickhouse"} > 7516192768  # 7GB (87.5% of 8)
  for: 5m
```

## Consequences

### Positive
- Platform fits within 16 cores / 40GB with 51% RAM headroom
- 42-45% fewer services to manage
- Clear resource ownership per service
- Burst capacity available during off-peak hours
- Spark only consumes resources when running batch jobs

### Negative
- ClickHouse gets the largest allocation (4 CPU / 8GB) — if it's underutilized, resources are wasted
- Spark batch window temporarily reduces burst capacity
- Resource limits may need tuning as data volume grows

## References

- [Docker Compose Resource Limits](https://docs.docker.com/compose/compose-file/deploy/#resources)
- [ClickHouse Memory Configuration](https://clickhouse.com/docs/en/operations/settings/settings#max_memory_usage)
- [Redpanda Memory Configuration](https://docs.redpanda.com/current/reference/properties/tuning-properties/)
- [ZGC (Low-Latency GC)](https://wiki.openjdk.org/display/zgc)

---

## Outcome (as-built, 2026-02)

| Metric | ADR-010 projection | As-built (`docker-compose.yml`) |
|--------|--------------------|---------------------------------|
| CPU limits | 15.5 | **15.1** |
| RAM limits | 19.5 GB | **21.875 GB** |
| Long-running services | 11 | **14** (+2 one-shot: `redpanda-init`, `iceberg-init`) |
| Fits 16 CPU / 40 GB? | Yes | **Yes** — 6% CPU / 45% RAM headroom |

### As-built service allocation

| Service | CPU Limit | RAM Limit |
|---------|-----------|-----------|
| clickhouse | 4.0 | 8G |
| redpanda | 2.0 | 2G |
| spark-iceberg | 2.0 | 4G |
| prometheus | 1.0 | 2G |
| minio | 1.0 | 1G |
| prefect-db | 1.0 | 1G |
| prefect-server | 1.0 | 1G |
| prefect-worker | 0.5 | 512M |
| grafana | 0.5 | 512M |
| redpanda-console | 0.5 | 256M |
| feed-handler-binance | 0.5 | 512M |
| feed-handler-kraken | 0.5 | 512M |
| feed-handler-coinbase | 0.5 | 512M |
| iceberg-metrics | 0.1 | 128M |
| **Total** | **15.1** | **21.875 GB** |

### Why the shape changed

- **Prefect was retained, not eliminated** ([ADR-008](ADR-008-eliminate-prefect-orchestration.md) Outcome): +3 services, +2.5 CPU / +2.5 GB. Prometheus was also raised from 0.5 CPU / 512 MB to 1.0 CPU / 2 GB once it was scraping ClickHouse (`:9363`), three feed handlers and the offload flow against 17 alert rules.
- **The Spring Boot API (2.0 / 1 GB) and the Kotlin Silver processor (1.0 / 512 MB) were never built** — ADR-005 deferred, Silver moved into ClickHouse MVs — releasing 3.0 CPU / 1.5 GB, roughly what Prefect and Prometheus took back. The Iceberg REST catalog also went ([ADR-007](ADR-007-iceberg-cold-storage.md) Outcome): −0.5 CPU / −512 MB.
- **Feed handlers split 1 → 3 containers** ([ADR-002](ADR-002-kotlin-feed-handlers.md) Outcome) and `redpanda-console` was added, costing +1.5 CPU / +1.75 GB against a single 1.0 CPU / 512 MB line item.

Actual runtime usage sits far below these limits — feed handlers measure ~0.03 CPU / 134 MiB each against a 0.5 CPU / 512 MB cap. The budget held; the composition of it did not.

## Outcome addendum (v3 Phase B, 2026-08-26)

[ADR-018](ADR-018-v3-lake-first-rust-capture.md) makes Iceberg-on-MinIO the system of
record behind a **Lakekeeper REST catalog**, which is a new always-on service where v2's
hadoop catalog was a bind mount that cost nothing. It is the first line item added to the
budget since the as-built table above.

| Service | CPU Limit | RAM Limit | Note |
|---------|-----------|-----------|------|
| `lakekeeper` | 0.25 | 256M | Iceberg REST catalog (`quay.io/lakekeeper/catalog:v0.13.3`) |

| Metric | As-built 2026-02 | As-built 2026-08-26 |
|--------|------------------|---------------------|
| CPU limits (steady state) | 15.1 | **15.35** |
| RAM limits (steady state) | 21.875 GB | **22.125 GB** |
| Long-running services | 14 | **15** |
| One-shot services | 2 | **4** (`lakekeeper-migrate`, `lake-init` added) |
| CPU / RAM at bootstrap peak | — | **16.85 / 23.625 GiB** |
| Headroom vs 16 CPU / 40 GB | 6% / 45% | **4% / 45% steady; −4% CPU at boot** |

Three things kept the cost to 0.25 CPU:

- **No second PostgreSQL.** Lakekeeper's metadata lives in a `lakekeeper` database on the
  existing `prefect-db` (`docker/postgres/ddl/10-lakekeeper-db.sql`), so the catalog adds
  a process, not a database server. `prefect-db` absorbs it inside its existing 1.0 / 1 GB.
- **No second object store.** MinIO was provisioned but carried no data — the v2 warehouse
  is a bind mount ([ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md)). The `k2-lake` bucket is
  its first real user, at no new allocation.
- **The bootstrap one-shots are cheap, and reuse an image already pulled.**
  `lakekeeper-migrate` and `lake-init` exit in seconds, and `lake-init` runs the
  `minio/minio` image already pulled for the MinIO service, so neither adds a
  steady-state line item.

  They are **not free**, which an earlier draft of this addendum claimed. All four
  one-shots declare limits and all four run *concurrently with the 15.35 steady
  state* at `docker compose up`:

  | One-shot | CPU | RAM |
  |----------|-----|-----|
  | `redpanda-init` | 0.25 | 128M |
  | `iceberg-init` | 0.5 | 1G |
  | `lakekeeper-migrate` | 0.5 | 256M |
  | `lake-init` | 0.25 | 128M |
  | **subtotal** | **1.5** | **1.5G** |

  So the honest numbers are: **steady state 15.35 CPU / 22.125 GB; bootstrap peak
  16.85 CPU / 23.625 GiB for the one-shots' lifetime (seconds)**. That peak is over
  the 16-core target. It is accepted rather than fixed: a CPU limit is a ceiling on
  scheduling, not a reservation, so the overcommit is a burst that the kernel
  resolves by sharing. Note the one-shots do *not* all finish before traffic
  flows: the feed handlers gate only on `redpanda-init`; `iceberg-init`,
  `lakekeeper-migrate` and `lake-init` sit on independent dependency branches
  and were observed running after the handlers had started. The ceiling-not-
  reservation argument carries the acceptance on its own.

  `redpanda-init` declared no limits at all when this addendum was first written,
  which is what made the peak unmeasurable; it now declares 0.25 / 128M and is
  counted above. Provenance: sum of `deploy.resources.limits` over
  `docker compose config` (19 services), 2026-08-26.

Measured immediately after bootstrap: **3.25 % CPU / 39.34 MiB against the 256 MB cap**
(`docker stats --no-stream`, 2026-08-26). The limit is headroom for the Phase C write
volume, not sizing.

**CPU headroom is now the tight axis at 0.65 cores.** Phase C adds three Rust capture
containers and retires three JVM feed handlers (1.5 CPU / 1.5 GB); ADR-018 budgets the
Rust tier at under 1.5 CPU combined, so the swap should be net-neutral to net-positive.
If it is not, this Outcome gets a second addendum before Phase D.

## Outcome addendum (v3 Phase C, 2026-08-26)

The Rust capture tier landed at exactly the 0.75 CPU / 1 GB predicted above:
`capture-binance` and `capture-kraken` at 0.25 CPU / 256M each, `capture-coinbase` at
0.25 CPU / 512M (its full-depth `BTreeMap` book is the larger resident structure of the
three). It runs **alongside**, not instead of, the three Kotlin feed handlers for the
labelled 2-hour parity window ([ADR-019](ADR-019-rust-capture-tier.md) — the plan's
original 24h figure was superseded by the maintainer's 2026-08-26 decision to run a
labelled 2-hour sample instead, with a 24-hour continuous soak kept as a later revisit
trigger), so the swap is not yet net-neutral — it is net-additive until Kotlin retires.
Steady state moves from 15.35 to **16.10 CPU / 23.125 GB across 18 long-running
services**, 0.10 CPU over the 16-core target. This is accepted, not a budget miss: it is
the explicit, temporary cost of running two producers side by side to compare per-symbol
counts before trusting the new one, and it retraces the same "ceiling not reservation"
argument the Phase B bootstrap-peak overcommit made above. Retiring
`feed-handler-{binance,kraken,coinbase}` once the parity check is clean gives back 1.5
CPU / 1.5 GB, landing steady state at 14.60 CPU / 21.625 GB — better headroom than the
v2 baseline. [ADR-019](ADR-019-rust-capture-tier.md) records that retirement.

**`cpuset` pinning now has a real default.** The three capture containers ship
`cpuset: ${K2_CAPTURE_CPUSET-12-14}` — cores 12-14, the layout
`005-phase-f-notebooks-numbers-docs.md` specifies, away from the low cores ClickHouse
and Spark contend for. The knob previously defaulted to the empty string, so no
container was pinned at all (`docker inspect -f '{{.HostConfig.CpusetCpus}}'` returned
`""` on all three) and the Phase C scope bullet "cpuset pinned away from CH/Spark" was
undelivered. A cpuset is a *placement* constraint, not a quota: it does not change the
16.10 CPU limit sum above, and the `-` (not `:-`) default means a host with fewer than
15 cores can still run unpinned by setting `K2_CAPTURE_CPUSET=` explicitly in `.env`.
The disjoint-set check that Phase D's noisy-neighbour experiment depends on
(`003-phase-d-lake-tier.md`) can now actually be run.

## Outcome addendum (Kotlin retirement, 2026-08-26)

The three Kotlin feed handlers are gone from `docker-compose.yml`
([ADR-019](ADR-019-rust-capture-tier.md); the code is archived at
`legacy/v2-kotlin/`). They gave back exactly the 1.5 CPU / 1.5 GB they declared,
so the swap the Phase B addendum predicted as "net-neutral to net-positive"
landed net-positive: three JVMs at 0.5 CPU / 512M each out, three Rust
containers at 0.75 CPU / 1 GB combined in.

**Steady state is 14.60 CPU / 21.625 GiB across 15 long-running services** — the
first time since Phase B that steady state is inside the 16-core target with
room to spare (1.40 CPU headroom, against 0.65 at Phase B and −0.10 during the
Phase C parallel run). Bootstrap peak, with the four one-shots overlapping, is
**16.10 CPU / 23.125 GiB across all 19 services**: still 0.10 over target for
the length of a bootstrap, still accepted on the same ceiling-not-reservation
argument as the two addenda above, and now 0.75 CPU lower than the peak they
recorded.

Provenance — sum of `deploy.resources.limits` over the rendered compose file,
split by whether a service is a one-shot (`restart: "no"`), 2026-08-26:

```bash
docker compose --env-file .env.example config --format json \
  | python3 -c 'import json,sys
s=json.load(sys.stdin)["services"]
def lim(v):
    r=v.get("deploy",{}).get("resources",{}).get("limits",{})
    return float(r.get("cpus",0) or 0), int(r.get("memory",0) or 0)
g={"steady":[],"one-shot":[]}
for n,v in s.items(): g["one-shot" if v.get("restart")=="no" else "steady"].append((n,)+lim(v))
for k in ("steady","one-shot",):
    c=sum(r[1] for r in g[k]); m=sum(r[2] for r in g[k])
    print("%-9s %2d services  %6.2f CPU  %7.3f GiB" % (k,len(g[k]),c,m/1024**3))
a=g["steady"]+g["one-shot"]
print("%-9s %2d services  %6.2f CPU  %7.3f GiB" % ("TOTAL",len(a),sum(r[1] for r in a),sum(r[2] for r in a)/1024**3))'
```

```
steady    15 services   14.60 CPU   21.625 GiB
one-shot   4 services    1.50 CPU    1.500 GiB
TOTAL     19 services   16.10 CPU   23.125 GiB
```

The same command and its output are carried in the `docker-compose.yml` resource
summary comment, which is the copy anyone editing a limit will actually see.

**RSS against these limits is not yet measured for the post-retirement stack.**
The numbers above are declared ceilings, which is what a budget is; what the
containers actually resident is a burn-in question and lands in ADR-019's
Outcome with the rest of the parity-window measurements.

**Prometheus lost three scrape jobs and three alert rules with the handlers**
(`feed-handler-{binance,kraken,coinbase}`; `FeedHandlerDown`,
`FeedHandlerHighErrorRate`, `FeedHandlerFrequentReconnects`, plus the
`feed_handler:trade_rate:5m` recording rule). The rule file is archived at
`legacy/v2-kotlin/runbooks/feed-handler-alerts.yml`. Alert rules go from 27 to
**23** (13 v2 + 10 v3 capture): the handlers took three, and the same PR archived
`ClickHouseBronzeInsertRateLow`, which could only ever fire once nothing produced
to the v2 topics. Prometheus's own 1.0 CPU / 2 GB allocation is unchanged, because
it was raised for ClickHouse's `:9363` cardinality rather than for the handlers.

```bash
for f in docker/prometheus/rules/*.yml; do
  printf '%-50s %2d\n' "$f" "$(grep -c '^[[:space:]]*- alert:' "$f")"
done
```

```
docker/prometheus/rules/capture-alerts.yml         10
docker/prometheus/rules/clickhouse-alerts.yml       4
docker/prometheus/rules/iceberg-offload-alerts.yml  9
```

## Outcome addendum (v3 Phase D, 2026-08-26)

The lake tier costs **0.10 CPU / 128M steady** — one service, `lake-metrics`, running
`docker/lake/metrics.py --serve`. Ingest and maintenance are Prefect jobs dispatched into
the existing `k2-spark-iceberg` container, so they consume its already-allocated 2.0 CPU /
4 GB rather than adding a line. The one-shot `lake-ddl` adds 0.5 CPU / 1G at boot only.

The exporter is 128M because it does **not** use PyIceberg. `import pyiceberg.io.pyarrow`
alone measures 122 MB RSS in the Spark image, before any work; reading the same snapshot
summaries from Lakekeeper's REST API with `urllib` measures 26.7 MB doing a full refresh
against all four tables. That decision is what keeps this addendum's first line at 0.10
rather than at 0.25 CPU / 512M.

| Metric | As-built Phase C | As-built Phase D |
|--------|------------------|------------------|
| CPU limits (steady state) | 16.10 | **16.20** |
| RAM limits (steady state) | 23.125 GB | **23.250 GB** |
| Long-running services | 18 | **19** |
| One-shot services | 4 | **5** (`lake-ddl` added) |
| CPU / RAM at bootstrap peak | 17.60 / 24.625 GiB | **18.20 / 25.750 GiB** |
| Headroom vs 16 CPU / 40 GB | −0.6% / 42% | **−1.3% CPU / 42% RAM steady** |

Steady state is now 0.20 CPU over the 16-core target, and there are two overlapping
parallel runs paying for it rather than one: Rust capture beside the Kotlin feed handlers
(Phase C), and `lake-metrics` beside `iceberg-metrics` (Phase D). Both are the cost of
comparing a replacement against the path it replaces before trusting it, and both end at
their own cutover. Retiring the Kotlin handlers returns 1.5 CPU / 1.5 GB; retiring
`iceberg-metrics` with the rest of `docker/offload/` returns a further 0.1 CPU / 128M,
landing steady state at **14.60 CPU / 21.625 GB**.

Numbers from `DOCKER_CONTEXT=default docker compose --env-file .env.example config` summed
over `deploy.resources.limits`, 2026-08-26.

## Outcome addendum (v3 Phase D cutover, 2026-08-27)

The addendum above budgeted for a parallel run that did not happen. `docker/offload/` was
deleted rather than compared (the reason is in
[ADR-014](ADR-014-spark-based-iceberg-offload.md)'s Outcome), so `lake-metrics` never ran
beside `iceberg-metrics` and the second of the two overlaps was paid for on paper only.

| Metric | Phase D as budgeted | Phase D cutover, as built |
|--------|---------------------|---------------------------|
| CPU limits (steady state) | 16.20 | **16.10** |
| RAM limits (steady state) | 23.250 GiB | **23.125 GiB** |
| Long-running services | 19 | **18** (`iceberg-metrics` gone) |
| One-shot services | 5 | **4** (`iceberg-init` gone) |
| CPU / RAM at bootstrap peak | 18.20 / 25.750 GiB | **17.60 / 24.625 GiB** |
| Headroom vs 16 CPU / 40 GB | −1.3% CPU / 42% RAM | **−0.6% CPU / 42% RAM** |

Steady state is back to 0.10 CPU over the 16-core target with **one** parallel run paying
for it — Rust capture beside the Kotlin feed handlers — which is exactly where the Phase C
addendum left it. Retiring `feed-handler-{binance,kraken,coinbase}` returns the remaining
1.5 CPU / 1.5 GiB and lands steady state at **14.60 CPU / 21.625 GiB across 15**: 1.40 CPU
(9%) and 18.375 GiB (46%) of headroom. That is the same end state the Phase D addendum
predicted, reached one step earlier because the offload exporter never had to be retired
separately.

Deleting the offload also removed `clickhouse-jdbc-0.4.6-all.jar` and `psycopg2-binary`
from the Spark image and `psycopg2-binary` from the Prefect image. Neither shows up in this
table — image size is not in the budget — but both were carried solely for the JDBC read
and the watermark table.

Numbers from `DOCKER_CONTEXT=default docker compose --env-file .env.example config` summed
over `deploy.resources.limits`, 2026-08-27.

## Outcome addendum (Phase D rebased onto the Kotlin retirement, 2026-08-27)

The two addenda above were written while `feed-handler-{binance,kraken,coinbase}` were
still in `docker-compose.yml`. They land after the retirement
([ADR-019](ADR-019-rust-capture-tier.md)), not before it, so their steady-state rows are
superseded rather than wrong when written: both cutovers are now done in the same compose
file — the Kotlin handlers are gone, and so is `docker/offload/`.

| Metric | Phase D cutover, as budgeted above | As built, on the retired capture tier |
|--------|------------------------------------|----------------------------------------|
| CPU limits (steady state) | 16.10 | **14.60** |
| RAM limits (steady state) | 23.125 GiB | **21.625 GiB** |
| Long-running services | 18 | **15** |
| One-shot services | 4 | **4** — `redpanda-init`, `lakekeeper-migrate`, `lake-init`, `lake-ddl` |
| CPU / RAM at bootstrap peak | 17.60 / 24.625 GiB across 22 | **16.10 / 23.125 GiB across 19** |
| Headroom vs 16 CPU / 40 GB | −0.6% CPU / 42% RAM | **+8.8% CPU / 46% RAM** |

**No parallel run is being paid for any more.** Both overlaps the Phase B, C and D addenda
budgeted for have ended: the capture comparison ended when the handlers retired, and the
lake comparison never ran because `docker/offload/` was deleted rather than compared. The
bootstrap peak is still 0.10 CPU over the 16-core target for the length of a bootstrap, on
the same ceiling-not-reservation argument every addendum above makes.

Provenance — sum of `deploy.resources.limits` over the rendered compose file, split by
whether a service is a one-shot (`restart: "no"`), 2026-08-27:

```bash
DOCKER_CONTEXT=default docker compose --env-file .env.example config --format json \
  | python3 -c 'import json,sys
s=json.load(sys.stdin)["services"]
def lim(v):
    r=v.get("deploy",{}).get("resources",{}).get("limits",{})
    return float(r.get("cpus",0) or 0), int(r.get("memory",0) or 0)
g={"steady":[],"one-shot":[]}
for n,v in s.items(): g["one-shot" if v.get("restart")=="no" else "steady"].append((n,)+lim(v))
for k in ("steady","one-shot",):
    c=sum(r[1] for r in g[k]); m=sum(r[2] for r in g[k])
    print("%-9s %2d services  %6.2f CPU  %7.3f GiB" % (k,len(g[k]),c,m/1024**3))
a=g["steady"]+g["one-shot"]
print("%-9s %2d services  %6.2f CPU  %7.3f GiB" % ("TOTAL",len(a),sum(r[1] for r in a),sum(r[2] for r in a)/1024**3))'
```

```
steady    15 services   14.60 CPU   21.625 GiB
one-shot   4 services    1.50 CPU    1.500 GiB
TOTAL     19 services   16.10 CPU   23.125 GiB
```

**Alert rules go from 23 to 25** — the nine `IcebergOffload*` rules went with
`docker/offload/`, and eleven v3 lake rules replaced them. Composition is 4 v2 ClickHouse +
10 v3 capture + 11 v3 lake:

```bash
for f in docker/prometheus/rules/*.yml; do
  printf '%-50s %2d\n' "$f" "$(grep -c '^[[:space:]]*- alert:' "$f")"
done
```

```
docker/prometheus/rules/capture-alerts.yml         10
docker/prometheus/rules/clickhouse-alerts.yml       4
docker/prometheus/rules/lake-alerts.yml            11
```

### Outcome addendum — 2026-08-26, spark-iceberg 4G → 8G

`rewrite_data_files` over `raw.messages` (rows up to 5 MB) OOM'd a 768m driver heap
twice and finished at 2g with a peak driver RSS of 2,641 MiB (`15-capacity-model.md`, dated
note). `maintenance.py` now runs alone behind the ingest lock, and the container limit
went 4G → 8G so an operator's `docker exec` fits beside it. Steady state **14.60 CPU /
25.625 GiB across 15**, bootstrap peak **16.10 CPU / 27.125 GiB across 19**; headroom
14.375 GiB (36%) at steady state.

Found while doing it: the Docker Desktop VM on the maintainer host had **7.6 GiB** —
every limit above it, ClickHouse's 8G included, was clipped (`docker stats` showed
`/ 7.648GiB`). The 40 GB envelope is a statement about the host, and a host must be
checked with `docker info --format '{{.MemTotal}}'`, not assumed. Resized to 39.2 GiB the
same day; `docs/development/setup.md` now says ≥ 28 GB engine memory and why.

### Outcome addendum (2026-11-25, Trino and the 25 CPU / 48 GiB ceiling)

The ceiling moved twice after this ADR's: to 20 CPU / 36 GiB at the bootstrap peak
([ADR-034](ADR-034-airflow-3-replaces-prefect.md)), then to **25 CPU / 48 GiB**
([ADR-045](ADR-045-ceiling-25-cpu-48-gib.md)), gated in CI by `scripts/check-budget.py`.
The allocation method above, a hard limit per service summed from the resolved compose
file, is unchanged.

What changed the sums since the last addendum: Airflow 3 replaced Prefect (ADR-034, Prefect
kept behind `--profile legacy`), cAdvisor joined at 0.1 CPU / 128 MiB, `spark-iceberg` went
from 2.0 to 3.0 CPU (ADR-034 Outcome), and Trino joins at 2.0 CPU / 4 GiB with a 0.5 CPU / 1 GiB
reservation:

```
$ python3 scripts/check-budget.py
steady default         19 services   19.30 CPU   34.312 GiB
bootstrap default      24 services   21.30 CPU   36.812 GiB
bootstrap governance   24 services   21.30 CPU   36.812 GiB
bootstrap legacy       26 services   22.80 CPU   38.312 GiB
PASS: bootstrap default within 25 CPU / 48 GiB; limits and pins hold across 6 Dockerfiles
```

Headroom at the gated peak: 3.70 CPU and 11.188 GiB. The engine on the reference host has
28 CPUs and 49.50 GiB (`docker info --format '{{.NCPU}} {{.MemTotal}}'`, 2026-11-25), so the
ceiling leaves 3 cores and 1.5 GiB outside it; ADR-045 says why that is accepted. The summary comment at the end of
`docker-compose.yml` carries the same sums.

### Outcome addendum (2026-11-26, Trino 4G to 5G)

`trino` goes from 4G to **5G**, its heap unchanged at 2816M
(`docker/trino/etc/jvm.config`), CPU unchanged at 2.0. After the first builds of the dbt marts
the container's anonymous memory stood at 4,072,116,224 bytes against the 4,294,967,296-byte
limit (`docker exec k2-trino cat /sys/fs/cgroup/memory.stat`, the `anon` line, 2026-11-25),
about 0.2 GiB of room, and `jvm.config` does not cap the JVM's direct memory. The marts are
rebuilt every hour by an unattended DAG, so the limit moves before an out-of-memory kill rather
than after one ([ADR-035](ADR-035-trino-and-dbt-for-marts.md) Outcome). The extra GiB is all
outside the heap: 2.25 GiB of the limit is left beside it, against 1.25 GiB before.

```
$ python3 scripts/check-budget.py
steady default         19 services   19.30 CPU   35.312 GiB
bootstrap default      24 services   21.30 CPU   37.812 GiB
bootstrap governance   24 services   21.30 CPU   37.812 GiB
bootstrap legacy       26 services   22.80 CPU   39.312 GiB
PASS: bootstrap default within 25 CPU / 48 GiB; limits and pins hold across 6 Dockerfiles
```

Headroom at the gated peak: 3.70 CPU and 10.188 GiB. The summary comment at the end of
`docker-compose.yml` carries the same sums.

### Outcome addendum (2026-11-26, ClickHouse behind the profile hot-gold)

`clickhouse` (4.0 CPU / 8G) moves behind the compose profile `hot-gold`, paused for the platform
build by [ADR-057](ADR-057-clickhouse-hot-tier-paused-during-platform-build.md). Its limits are
unchanged; it leaves the default profile, so the gated bootstrap peak no longer counts it and
`check-budget.py` reports it on the `hot-gold` line instead. No default-profile service depends
on it (`docker compose config --services | grep -cx clickhouse` prints 0, and 1 with
`--profile hot-gold`).

```
$ python3 scripts/check-budget.py
steady default         18 services   15.30 CPU   27.312 GiB
bootstrap default      23 services   17.30 CPU   29.812 GiB
bootstrap hot-gold     24 services   21.30 CPU   37.812 GiB
PASS: bootstrap default within 25 CPU / 48 GiB; limits, pins and loopback ports hold across 5 Dockerfiles and 40 scripts and workflows
```

Headroom at the gated peak: 7.70 CPU and 18.188 GiB. The summary comment at the end of
`docker-compose.yml` carries the same sums.

### Outcome addendum (2026-11-26, the old lake ingest retired)

`lake-metrics` (0.1 CPU / 128M, long-running) and the `lake-ddl` one-shot (0.5 CPU / 1G) leave
the compose file with the old lake ingest they served, archived to `legacy/v3-lake-ingest/`
([ADR-056](ADR-056-unified-data-product-platform.md)). No other limit changes. The scheduler no
longer waits on `lake-ddl`, and nothing else named either
(`docker compose config --services | grep -cxE 'lake-(ddl|metrics)'` prints 0).

```
$ python3 scripts/check-budget.py
steady default         17 services   15.20 CPU   27.188 GiB
bootstrap default      21 services   16.70 CPU   28.688 GiB
bootstrap hot-gold     22 services   20.70 CPU   36.688 GiB
PASS: bootstrap default within 25 CPU / 48 GiB; limits, pins and loopback ports hold across 4 Dockerfiles and 27 scripts and workflows
```

Headroom at the gated peak: 8.30 CPU and 19.312 GiB. The summary comment at the end of
`docker-compose.yml` carries the same sums. The phase 000 plan named 17.30 CPU / 29.812 GiB as
its exit figure; that was the peak with these two services still in it.

### Outcome addendum (2026-11-26, the phase 001 stubs behind profiles)

Four stubs join the compose file behind profiles that nothing starts by default, so the budget gate
counts and pin-checks them before their steps build them
([phase 001 interfaces](../development/phase-001-interfaces.md), A13): `tools` (profile `tools`,
0.25 CPU / 256M), `fetch` (`fetch`, 0.5 / 512M), `dbt-runner` (`build`, 0.5 / 768M, two
replicas) and `k2-api` (`api`, 0.5 / 512M). No default-profile limit changes, and no
default-profile service definition changes (`docker compose config --hash '*'` prints the same
21 hashes before and after).

```
$ python3 scripts/check-budget.py
steady default         17 services   15.20 CPU   27.188 GiB
bootstrap default      21 services   16.70 CPU   28.688 GiB
bootstrap api          22 services   17.20 CPU   29.188 GiB
bootstrap build        22 services   17.70 CPU   30.188 GiB
bootstrap fetch        22 services   17.20 CPU   29.188 GiB
bootstrap hot-gold     22 services   20.70 CPU   36.688 GiB
bootstrap tools        22 services   16.95 CPU   28.938 GiB
PASS: bootstrap default within 25 CPU / 48 GiB; limits, pins and loopback ports hold across 4 Dockerfiles and 51 scripts and workflows
```

Headroom at the gated peak is unchanged: 8.30 CPU and 19.312 GiB. The summary comment at the end
of `docker-compose.yml` carries the same sums.

### Outcome addendum (2026-11-26, Alertmanager)

`alertmanager` (prom/alertmanager v0.35.1, pinned by digest; 0.1 CPU / 64M, long-running) joins the
default profile, to route every firing alert to Slack
([where-an-alert-goes.md](../runbooks/where-an-alert-goes.md)). Plan 001 budgeted it at the same
+0.10 CPU / +0.0625 GiB. No other limit changes. A throwaway copy of the service, as compose
defines it, used 19.5 MiB and CPU near 0 while `make alert-test-standin` drove it on 2026-11-26
(`docker stats --no-stream k2-alerttest-am`), so 64M leaves it three times that.

```
$ python3 scripts/check-budget.py
steady default         18 services   15.30 CPU   27.250 GiB
bootstrap default      22 services   16.80 CPU   28.750 GiB
bootstrap api          23 services   17.30 CPU   29.250 GiB
bootstrap build        23 services   17.80 CPU   30.250 GiB
bootstrap fetch        23 services   17.30 CPU   29.250 GiB
bootstrap hot-gold     23 services   20.80 CPU   36.750 GiB
bootstrap tools        23 services   17.05 CPU   29.000 GiB
PASS: bootstrap default within 25 CPU / 48 GiB; limits, pins and loopback ports hold across 4 Dockerfiles and 55 scripts and workflows
```

Headroom at the gated peak: 8.20 CPU and 19.250 GiB. The summary comment at the end of
`docker-compose.yml` carries the same sums. Plan 001 named 17.40 CPU / 29.875 GiB as its exit
figure; that was computed before phase 000 retired `lake-metrics` and `lake-ddl`.

### Outcome addendum (2026-11-27, phases 002 and 003: k2-api in the default set)

`k2-api` (0.5 CPU / 512M, unchanged from its stub) leaves the profile `api` and joins the default
profile, long-running: plan 003's exit has every part report the release, and a deploy moves only the
default set. `fetch` and `dbt-runner` are now real images (`docker/fetch/`, `docker/dbt-runner/`) under
the same limits as their stubs, 0.5 / 512M and 0.5 / 768M times two replicas, still behind the profiles
`fetch` and `build`, which the gate reports and does not count in the default.

```
$ python3 scripts/check-budget.py
steady default         19 services   15.80 CPU   27.750 GiB
bootstrap default      23 services   17.30 CPU   29.250 GiB
bootstrap build        24 services   18.30 CPU   30.750 GiB
bootstrap fetch        24 services   17.80 CPU   29.750 GiB
bootstrap hot-gold     24 services   21.30 CPU   37.250 GiB
bootstrap tools        24 services   17.55 CPU   29.500 GiB
PASS: bootstrap default within 25 CPU / 48 GiB; limits, pins and loopback ports hold across 8 Dockerfiles and 87 scripts and workflows
```

Headroom at the gated peak: 7.70 CPU and 18.750 GiB. The `api` profile line is gone because no service
carries that profile now. Plan 003 named 19.40 CPU / 32.375 GiB for the peak with the `fetch` and
`build` profiles together; the script reports each profile alone, and that figure was computed before
phase 000 retired `lake-ddl` and `lake-metrics`. The summary comment at the end of `docker-compose.yml`
carries the same sums.

### Outcome addendum (2026-11-27, lake-init at 256M)

`lake-init` (a one-shot) goes from 128M to 256M, its CPU limit unchanged. Since phase 002 it also
creates one MinIO user and policy per engine (ADR-054), and at 128M the MinIO client was killed by
the kernel in one of three CI runs of the same tree (`mc admin policy attach`, exit by SIGKILL
inside `docker/lake/init-lake.sh`). The limit was too small for the work, so the limit moves; the
throwaway lake (`scripts/lib/throwaway-lake.sh`) runs it under the same 256m. It adds 0.125 GiB at
the bootstrap peak only; steady state is unchanged. `python3 scripts/check-budget.py`:

```
steady default         19 services   15.80 CPU   27.750 GiB
bootstrap default      23 services   17.30 CPU   29.375 GiB
bootstrap build        24 services   18.30 CPU   30.875 GiB
bootstrap fetch        24 services   17.80 CPU   29.875 GiB
bootstrap hot-gold     24 services   21.30 CPU   37.375 GiB
bootstrap tools        24 services   17.55 CPU   29.625 GiB
PASS: bootstrap default within 25 CPU / 48 GiB; limits, pins and loopback ports hold across 8 Dockerfiles and 96 scripts and workflows
```

Headroom at the gated peak: 7.70 CPU and 18.625 GiB. The figures in the addendum above this one are
what the script printed before this change.
