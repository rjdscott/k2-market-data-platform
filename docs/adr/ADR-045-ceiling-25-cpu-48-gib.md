# ADR-045: The default profile's ceiling is 25 CPU / 48 GiB at the bootstrap peak

**Status:** Accepted
**Date:** 2026-11-25
**Author:** Rob Scott
**Category:** Infrastructure

---

## Context

[ADR-034](ADR-034-airflow-3-replaces-prefect.md) set 20 CPU / 36 GiB at the bootstrap peak,
summed over every default-profile service's `deploy.resources.limits`, one-shots included, and
gated by `scripts/check-budget.py`. [ADR-031](ADR-031-governed-self-serve-platform.md), still
Proposed, committed the platform to the same figure and named a bootstrap total above either
number as its revisit trigger. Trino at 2.0 CPU / 4 GiB
([ADR-035](ADR-035-trino-and-dbt-for-marts.md)) puts the peak at 21.30 CPU / 36.812 GiB across
24 services, so that trigger has fired, and this ADR is the response.

This number falls outside the block ADR-031 reserved (032 to 042; 043 is the venue reference
bars plan's) because the umbrella did not foresee a second budget decision.

The host, and what the stack actually uses:

- The engine has 28 CPUs and 53,149,339,648 bytes, 49.50 GiB
  (`docker info --format '{{.NCPU}} {{.MemTotal}}'`, 2026-11-25).
- Over the 6 hours to 2026-11-25 12:17 UTC, a window that includes the Trino spike containers
  and the running `k2-trino`, the whole stack peaked at **3.84 cores** and **10.12 GiB**
  (10,864,435,200 bytes) of process memory, from Prometheus over cAdvisor (one series per
  container):

  ```
  max_over_time(sum(rate(container_cpu_usage_seconds_total{job="cadvisor"}[1m]))[6h:1m])
  max_over_time(sum(container_memory_rss{job="cadvisor"})[6h:1m])
  ```

- The plan's estimate for the next phase that adds always-on services, the catalog (P5), is
  +3.00 CPU / +7.000 GiB: OpenMetadata, its search engine, ingestion and Postgres at
  +2.50 / +5.500, and their migrate one-shot at +0.50 / +1.500 (plan README, lines 356 to 357).

---

## Decision

**We will set the default profile's ceiling to 25 CPU / 48 GiB at the bootstrap peak, for one
always-on platform with no opt-in profiles, because the maintainer's direction is that the
platform runs as one whole, the next planned phase should fit on its own estimate rather than
reopen the ceiling, and measured use is a small fraction of the declared sum.** It supersedes
ADR-034's 20 CPU / 36 GiB clause and nothing else in ADR-034; ADR-034's sentence that the
`governance` profile is outside the ceiling no longer describes the direction.

| | Services | CPU | GiB |
|---|---|---|---|
| Before, bootstrap peak | 23 | 19.30 | 32.812 |
| Now, steady | 19 | 19.30 | 34.312 |
| Now, bootstrap peak (gated) | 24 | **21.30** | **36.812** |
| With `--profile legacy`, the Prefect rollback (reported) | 26 | 22.80 | 38.312 |
| Plan estimate with the catalog phase | | 24.30 | 43.812 |

Headroom at the gated peak: 3.70 CPU and 11.188 GiB (`python3 scripts/check-budget.py`,
2026-11-25). The catalog phase's estimate fits with 0.70 CPU and 4.188 GiB to spare; that phase
still measures on first boot and records its own figures in its ADR.

**The cost, stated.** The ceiling leaves 3 cores and 1.50 GiB of the host's 28 and 49.50 for
the engine and the operating system: that is the margin if declared limits ever grow to the
whole ceiling and every one is reached at once. At today's declared 36.812 GiB the margin is
12.69 GiB (49.50 minus 36.812). Either way it is accepted because the ceiling is a sum of
declared limits and use is far below it: 3.84 cores and 10.12 GiB at the 6-hour peak above.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| 24 CPU / 42 GiB, this pull request's first figure | covers today's 21.30 / 36.812 but not the catalog phase's 24.30 / 43.812, which would reopen the ceiling one phase later |
| Keep 20 CPU / 36 GiB and trim elsewhere | 1.30 CPU and 0.812 GiB would come out of other services' limits, and ADR-034 keeps Airflow's at the plan's figures until real ingests are measured |
| Opt-in profiles for the platform layer | rejected by ADR-031: a default `docker compose up` would show none of the layer |
| A ceiling at the host (28 / 49.5) | nothing left for the engine at all on paper, and no signal before the host runs out |

---

## Consequences

**Easier:** P3 and P5 add services without reopening the budget on the plan's estimates.

**Harder:** the margin left for the engine at full declared memory is thin, so the measured-use
trigger below matters more than the declared one.

**Committed to:** the gate in CI (`scripts/check-budget.py`, 25.0 CPU and 48 GiB), the K2
Resources gauges at the same figures, the ADR-010 Outcome and the `docker-compose.yml` summary
updated with every limit change (CLAUDE.md).

**Revisit when** any of these holds:

- the declared bootstrap sum passes 95% of either figure, 23.75 CPU or 45.6 GiB
  (`python3 scripts/check-budget.py`);
- measured process memory of the whole stack stays above 32 GiB for an hour:
  `min_over_time(sum(container_memory_rss{job="cadvisor"})[1h:1m]) > 34359738368` in Prometheus;
- the host changes: `docker info --format '{{.NCPU}} {{.MemTotal}}'` prints anything but
  `28 53149339648`.

---

## References

- Superseded clause: [ADR-034](ADR-034-airflow-3-replaces-prefect.md) (its Outcome
  2026-11-25 records the supersession); before it [ADR-010](ADR-010-resource-budget.md)
  (Outcome addendum 2026-11-25).
- The trigger that fired: [ADR-031](ADR-031-governed-self-serve-platform.md), Revisit when.
- What the new peak is made of: [ADR-035](ADR-035-trino-and-dbt-for-marts.md),
  [ADR-044](ADR-044-producer-lake-and-domain-mart-warehouses.md).
- Code: `scripts/check-budget.py`, `tests/test_check_budget.py`,
  `docker/grafana/dashboards/k2-resources.json`
