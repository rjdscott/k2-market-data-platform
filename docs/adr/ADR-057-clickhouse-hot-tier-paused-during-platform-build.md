# ADR-057: Pause the ClickHouse hot tier while the platform is built

**Status:** Accepted
**Date:** 2026-11-26
**Author:** Platform
**Category:** Storage / Serving
**Decided by:** the owner on 2026-11-26 (UTC), with [ADR-056](ADR-056-unified-data-product-platform.md),
which left the pause to its own ADR
**Amends:** [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)'s "gold served
indefinitely from ClickHouse", suspended for the build; [ADR-025](ADR-025-clickhouse-derived-hot-tier.md)'s
reload by pull from the lake's gold, which has no source from the end of the first phase until the
new platform's gold exists
**Plan:** [phase 000, clear the ground](../plans/2026-11-26-unified-product-platform/000-clear-the-ground.md);
every file the pause touches is listed in
[`CLICKHOUSE-INVENTORY.md`](../research/2026-11-26-unified-platform-architecture/CLICKHOUSE-INVENTORY.md)

---

## In plain words

ClickHouse, a fast analytical database, holds a copy of the gold trades for dashboards, read
straight from the message broker. Nothing the platform rebuild works on reads it, and it holds
4 CPU and 8 GiB of the resource budget. We will switch it off for the build so that `make up`
cannot restart it, keep its disk, and stand down every alert, dashboard and check that reads it
where a reader can see that it was stood down. The cost: the copy goes stale, and a restart more
than 7 days after the stop leaves a hole, because the lake gold it was reloaded from is dropped
in the same phase. It comes back as a serving copy of a product of the new platform, rebuilt from
that platform's lake, when a measured need or the release gate calls for it.

---

## Context

**What the hot tier is.** The [hot tier](glossary.md#hot-tier) is the `gold` database of one
ClickHouse 24.3 server (`docker-compose.yml:223-274`). Two Kafka engine tables, `gold.q_trades` and
`gold.q_book` (a Kafka engine table is a ClickHouse table that reads broker topics as they grow; a
materialized view, a query run on every block it reads, copies the rows into ordinary tables), read
the trade and book topics of all three venues, binary records in the Avro format (`docker/clickhouse/ddl/20-gold-kafka.sql:33-60`,
`:80-110`). Each reads as a consumer group, `k2-gold-trades` and `k2-gold-book`: a name under which
Redpanda, the Kafka-compatible broker, stores the group's committed offsets, the position in each
partition up to which the group has read, so a restarted reader carries on where it stopped.
Since ADR-025 and ADR-026 the tier originates nothing: whatever it loses is reloaded from the lake's
gold tables ([runbook](../../legacy/v3-lake-ingest/docs/runbooks/clickhouse-rebuild-from-lake.md)).

**Who reads it.** Two Grafana dashboards (`clickhouse-overview.json`, and panels of
`k2-pipeline-overview.json`); step (2) of `scripts/health.sh`; the ClickHouse leg of the three-way
[parity](glossary.md#parity-three-way-and-pinned) check in `make gates`, the pre-merge gate on the
live stack; two chaos scripts, which break the tier on purpose and time its recovery
(`Makefile:222-223`); the documentation screenshots 08 and 09; six Prometheus alert rules
(`docker/prometheus/rules/clickhouse-alerts.yml`) and the scrape job that collects the metrics
they test (`docker/prometheus/prometheus.yml:45-52`). No Airflow DAG, dbt model,
[Trino](glossary.md#engines) catalog or notebook reads it (inventory).

**The forces.**

- **Budget.** Its limits are 4.0 CPU / 8 GiB (`docker-compose.yml:252-256`). Before the pause,
  `python3 scripts/check-budget.py` printed, on 2026-11-26, a steady state of 19 services at
  19.30 CPU / 35.312 GiB and a [bootstrap peak](glossary.md#bootstrap-peak) of 24 services at
  21.30 / 37.812, under the [ceiling](glossary.md#the-ceiling) of 25 / 48
  ([ADR-045](ADR-045-ceiling-25-cpu-48-gib.md)). The build adds about 2.10 CPU / 2.5625 GiB
  (Alertmanager, a fetch container, two dbt runners, `k2-api`; the
  [plan's budget table](../plans/2026-11-26-unified-product-platform/README.md)). Declared memory
  is not free even under the ceiling: on 2026-11-25 the host's out-of-memory killer killed the
  Docker VM (ADR-045, Outcome).
- **Its reload source is retired.** Phase 000 drops the old lake namespaces, `gold` among them, at
  its end. Until the new platform's trades products have a gold layer (from phase 004, Kraken
  trades), there is nothing to reload the hot tier from.
- **Two 7-day clocks start when its readers stop.** Redpanda deletes a group's committed offsets
  `group_offset_retention_sec` after the group last had a member. No compose file, config or script
  sets it (`git grep group_offset_retention` finds only this ADR), so it is the broker default:
  `docker exec k2-redpanda rpk cluster config get group_offset_retention_sec` printed `604800`,
  7 days, on 2026-11-26. The trade and book topics also keep 7 days by time, with no byte cap
  (`docker/redpanda/init.sh:149-152`, applied at `:211-213`). The 48 hours or 2 GiB per partition
  of `init.sh:137-146` is the raw topics, which the new platform's connectors read and ClickHouse
  does not. The book topics have carried nothing since book capture paused
  ([ADR-050](ADR-050-order-book-capture-paused-during-platform-build.md)).
- **A stop alone does not hold, and its signals lie.** The service has no compose profile (a label
  that keeps a service out of `docker compose up` unless that profile is named), so `make up`
  starts it again. Stopped, `ClickHouseDown` fires for ever, the other five rules never fire because
  they have no series, and `scripts/health.sh:88-92` prints a warning that blames the capture and
  exits 0.

---

## Decision

**We will pause the ClickHouse hot tier for the platform build behind the compose profile
`hot-gold`, because nothing the build works on reads it, its 4 CPU and 8 GiB are better left free,
and the lake gold it reloads from is retired in the same phase.** Scope: this one service, until a
trigger under Consequences fires.

1. **Pause.** The `clickhouse` service gains `profiles: ["hot-gold"]`; its container is stopped and
   removed. The volumes `clickhouse-data` (3.038 GB, `docker system df -v`, 2026-11-26) and
   `clickhouse-logs` are kept. The `image:` line and the DDL (the `CREATE TABLE` statements in
   `docker/clickhouse/ddl/`) stay, because `make test-clickhouse` and CI apply the gold DDL to a
   throwaway server whose tag is read from that line (`scripts/clickhouse-schema-test.sh:13`).
2. **Stand down visibly, never silently.** A check that is skipped prints a SKIP line, a line of its
   output that says it was skipped and names this ADR: `make gates` runs the parity check with
   `--skip-clickhouse` (`scripts/parity_ohlcv.py:480`).
   A signal that would lie is removed: the six rules and their tests, the scrape job, the
   ClickHouse dashboard and panels, the ClickHouse half of health step (2), the two chaos scripts
   with their `Makefile` lines and failure-mode rows, and screenshots 08 and 09. Every document
   that calls ClickHouse running says paused; the Avro doc strings that mention it are not
   touched, because editing one changes the registered schema.
3. **Record the figures** in the compose summary comment, `scripts/check-budget.py` and its test,
   and an [ADR-010](ADR-010-resource-budget.md) Outcome line. Expected, by subtracting 4.0 / 8 from
   the run above (and the phase's exit): steady 18 services at 15.30 CPU / 27.312 GiB, bootstrap
   peak 23 services at 17.30 / 29.812.
4. **Return as a product, not a resume.** Phase 001 declares `hot-gold` paused in ODPS (the Open
   Data Product Standard document that defines each product of the new platform), with a `serves`
   entry, the field that names a copy of the product held in another engine, pointing at the
   paused copy (`001-release-and-rules-spine.md:141`). On return it is rebuilt from
   the new platform's lake gold as that product's serving copy, under that platform's gates and
   alerts; the rows in the kept volume are not its source. Starting the old copy as it was is an
   emergency path only, bounded by the two clocks: within 7 days of the stop the groups carry on
   from their committed offsets and nothing is missed; later, the offsets are gone, the groups
   start where ClickHouse's reset rule puts them (the start of the topics' 7 days, per
   `20-gold-kafka.sql:14-16`), and the rows between the stop and that start are a hole no source
   can fill until the new gold exists.

---

## Rationale

The cost is measured and the benefit, during the build, is nil: no product, DAG or model reads the
tier. A pause is reversible in one command while the kept DDL stays tested in CI, so the serving
design ADR-026 chose ClickHouse for (sparse indexes, `ASOF JOIN`, concurrent dashboards) is kept
until something measured replaces it. Returning as a product follows ADR-056: every dataset is
defined once and served through one layer, and a hot copy bolted to the old gold would be the one
thing outside it.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Keep it running | Holds 4.0 CPU / 8 GiB through the build for dashboards nothing needs during it; from the drop at the end of phase 000 a lost volume could not be reloaded, and it keeps serving a model being retired |
| Stop the container, no profile | `make up` restarts it; `ClickHouseDown` fires for ever while five rules and the health step pass for the wrong reason |
| Delete it outright (service, DDL, volume, tests) | Irreversible; CI loses its test of the gold DDL; it reverses ADR-026's serving decision with no measurement that Trino suffices |
| **Pause behind `hot-gold`, volume kept** | Chosen |

---

## Consequences

**Easier:** 4.0 CPU / 8 GiB of limits go to the new services; gates and health no longer depend on
a tier nothing reads.
**Harder:** no fast hot reads during the build; dashboards that read ClickHouse are gone; an
emergency start after 7 days carries a hole.
**Committed to:** the image line and the DDL kept and tested; `hot-gold` declared paused in phase
001; a return only as a serving copy of a product of the new platform.
**Risks:** someone starts the old copy by habit after 7 days and gets a hole nothing reports; the
runbook `docs/runbooks/pause-and-resume-a-serving-product.md`, written in this phase, states the
window. The kept volume holds 3.038 GB that nothing reads.
**What is not yet verified:** that Redpanda deletes the offsets 7 days after the group empties (the
setting was read, the expiry not watched on this host); where ClickHouse 24.3 starts a group with
no stored offsets (the DDL comment says the start of retention, the inventory leaves its
`auto.offset.reset` default open); the post-pause budget figures and that `docker compose config
--services` no longer lists `clickhouse`, both proven by the commands in phase 000's exit.

**Revisit when**, whichever comes first: (a) a reader declared on a product of the new platform needs
a read answered in under one second at p95, and a `/benchmark-report` run of that read on Trino,
with n and the command on its row, shows it slower; or (b) the release gate, phase 005 of the
[to-release plan](../plans/2026-11-26-unified-product-platform-to-release/README.md), before
`v3.0.0`. Neither plan has a phase that brings the hot tier back, so the gate is where it is either
rebuilt as above or moved through the product lifecycle to deprecated.

---

## References

- [ADR-056](ADR-056-unified-data-product-platform.md) (its table of clauses names this pause),
  [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md),
  [ADR-025](ADR-025-clickhouse-derived-hot-tier.md), [ADR-045](ADR-045-ceiling-25-cpu-48-gib.md),
  [ADR-050](ADR-050-order-book-capture-paused-during-platform-build.md), [ADR-010](ADR-010-resource-budget.md)
- Plan: [phase 000](../plans/2026-11-26-unified-product-platform/000-clear-the-ground.md),
  [phase 001](../plans/2026-11-26-unified-product-platform/001-release-and-rules-spine.md)
- Research: [`CLICKHOUSE-INVENTORY.md`](../research/2026-11-26-unified-platform-architecture/CLICKHOUSE-INVENTORY.md),
  [`DECISIONS-FINAL.md`](../research/2026-11-26-unified-platform-architecture/DECISIONS-FINAL.md)
- Runbook: [rebuild ClickHouse gold from the lake](../../legacy/v3-lake-ingest/docs/runbooks/clickhouse-rebuild-from-lake.md)

---

## Outcome (2026-11)

**The pause was carried out on 2026-11-26 UTC, and the container stopped at 14:21 UTC.** The
`clickhouse` service runs under `profiles: ["hot-gold"]`, and its container was stopped and
removed. The minute comes from the stack's own Prometheus, which keeps what cAdvisor (the
per-container metrics exporter) scraped: its last sample of `k2-clickhouse` is at 14:21:26 UTC,
and its next scrape, at 14:21:39, has none. The last `up{job="clickhouse"}` sample, `1`, is at
14:21:17, and Prometheus loaded the configuration without that scrape job at 14:21:30
(`prometheus_config_last_reload_success_timestamp_seconds`). Read on 2026-11-26 with:

```bash
curl -s -G http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=container_memory_working_set_bytes{name="k2-clickhouse"}[2m]' \
  --data-urlencode 'time=2026-11-26T14:23:00Z'      # newest timestamp 1795702886.749, 14:21:26 UTC
```

The phase branch agrees: the merge that removed the ClickHouse signals landed at 14:20:38 UTC
(`git reflog --date=iso phase/000-clear-the-ground` in the live checkout).

**What was kept.** `docker volume ls --filter name=clickhouse` lists
`k2-market-data-platform_clickhouse-data` and `k2-market-data-platform_clickhouse-logs`; the
`image:` line and the DDL stay, and `make test-clickhouse` still applies the DDL to a throwaway
server.

**The signals were stood down in the same phase**, as decision 2 lists: the scrape job, the six
rules and their tests, the ClickHouse dashboard and panels, the two chaos scripts and their
`Makefile` lines, screenshots 08 and 09, and the ClickHouse half of `scripts/health.sh` step (2),
which now prints one SKIP line naming this ADR. One part went further than written: the parity
check whose ClickHouse leg was to print a SKIP was archived with the old lake ingest
(`legacy/v3-lake-ingest/`, [ADR-056](ADR-056-unified-data-product-platform.md)), so
`make parity-ohlcv` now prints one SKIP for the whole check, naming phase 004.

**The two 7-day clocks run out at about 14:21 UTC on 2026-12-03.** Both consumer groups,
`k2-gold-trades` and `k2-gold-book`, describe as `STATE Empty` with `MEMBERS 0` and keep their
committed offsets (`docker exec k2-redpanda rpk group describe k2-gold-trades`, 2026-11-26);
`group_offset_retention_sec` still prints `604800` and the trade topics' `retention.ms`
`604800000`. From that time Redpanda may delete the groups' offsets, and the first trade records
produced after the stop age out of their topics, so a start of the old copy after it leaves the
hole this ADR describes. The lake gold it was reloaded from is gone: on 2026-11-26
`SHOW SCHEMAS FROM lake` in Trino lists `information_schema` and `system` only.

**Verified since:** the budget figures and the service list. `python3 scripts/check-budget.py`
prints a steady state of 17 services at 15.20 CPU / 27.188 GiB and a bootstrap peak of 21
services at 16.70 / 28.688 (22 at 20.70 / 36.688 with `hot-gold`), not the 18 at 15.30 / 27.312
and 23 at 17.30 / 29.812 decision 3 expected: the `lake-metrics` exporter (0.10 CPU / 128 MiB) and
the `lake-ddl` one-shot (0.50 CPU / 1 GiB) retired with the old lake ingest in the same phase
([ADR-010](ADR-010-resource-budget.md) Outcome). `docker compose config --services | grep -cx
clickhouse` prints `0`, and `1` with `--profile hot-gold`.

**Still not verified:** that Redpanda deletes the offsets 7 days after the groups emptied (due
from 2026-12-03 14:21 UTC; `rpk group describe -s k2-gold-trades k2-gold-book` should then print
`STATE Dead`), and where ClickHouse 24.3 starts a group with no stored offsets.
