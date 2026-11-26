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

---

## Outcome (2026-11-26, the host margin was wrong)

**The claim.** The Decision's cost paragraph says that at the declared 36.812 GiB "the margin
is 12.69 GiB (49.50 minus 36.812)", and accepts the thin margin "because the ceiling is a sum of
declared limits and use is far below it". The index row repeated it as "the host keeps 12.69 GiB
beyond today's declared 36.812 GiB". That reasoning was wrong, in two ways.

1. **It measured the margin inside the Docker VM and called it the host's.** On Docker Desktop
   for Linux every container runs inside one virtual machine (VM), a `qemu-system-x86` process
   on the host with a fixed memory setting, here 51,712 MiB (50.5 GiB; Docker Desktop, Settings,
   Resources, Memory). The 49.50 GiB that `docker info` prints is what the VM's own kernel offers
   the containers, not what the host has. The guest kernel uses every page the containers leave
   free as file cache (copies of files kept in memory so the next read is fast), and QEMU does not
   return those pages to the host. So from the host's side **the VM grows to its full setting and
   stays there**, whatever the containers use: after a restart on 2026-11-25 it went from about
   20 GiB to about 41 GiB in under three hours, and it read 44.7 GiB, peak 48.1 GiB, at 01:30 UTC
   on 2026-11-26 (`systemctl --user show docker-desktop -p MemoryCurrent -p MemoryPeak`). Memory
   the containers do not use is not a margin for the host; the host pays for it anyway.
2. **It ignored everything else the host runs.** The host is also a desktop. With 62.6 GiB of
   usable RAM (`free -b`) and the VM at 50.5 GiB, 12.1 GiB is left for the desktop, its browser,
   editor and every other program, and for the host kernel.

**What happened.** At 22:33:43 UTC on 2026-11-25 the host kernel's out-of-memory killer (the part
of Linux that ends a process when no memory is left) killed the VM, and every container with it;
`systemctl --user status docker-desktop` showed `Result: oom-kill`. The kernel's report at the
kill: the VM's shared memory at 48.7 GiB of its 51,712 MiB, all other processes holding 13.1 GiB
of anonymous memory (memory a program allocated for itself, which cannot be dropped like file
cache), the 8 GiB of swap fully used, host RAM 63.8 GiB as the kernel counts it. Together that is
61.8 GiB of 63.8 with swap already full, and the other processes alone held more than the
12.1 GiB the VM leaves once it reaches its setting. Capture was off for 343 s (22:33:43 to 22:39:26 UTC), and that
market data is not recoverable: the venues' public feeds do not replay. Recovery and the checks
after it are the runbook
[`docker-vm-oom-kill.md`](../runbooks/docker-vm-oom-kill.md); the outage is recorded in
`lake.audit.checks` as job `operator`, check `capture_outage`.

**The corrected rule.** The ceiling stands and answers its own question: do the declared limits
fit inside the VM? A second condition, separate from it, governs the host, with the VM counted at
its full setting:

```
host RAM  >  VM memory setting  +  everything else the host runs  +  a margin
62.6 GiB  >  50.5 GiB           +  desktop and the rest            +  margin   (this host)
```

so the desktop and its margin must fit in 12.1 GiB. The VM setting has a floor too: the declared
bootstrap peak, 37.812 GiB (`python3 scripts/check-budget.py`), plus about 1 GiB for the VM's own
kernel (50.5 GiB set, 49.5 GiB offered), so about 39 GiB. Below it Docker Desktop silently clips
every limit above the VM's size. No alert can watch the host condition: every container,
Prometheus and cAdvisor included, sees the VM's memory, not the host's. The checks are on the
host: `free -h` (swap filling comes before a kill) and the `systemctl` line above.

**The owner's decision, 2026-11-26:** keep the VM at 51,712 MiB, keep the desktop light, add no
swap. No limit and no ceiling change, so `scripts/check-budget.py`, the ADR-010 Outcome and the
`docker-compose.yml` summary do not move.

**The host trigger in Revisit when.** It reads "`docker info --format '{{.NCPU}} {{.MemTotal}}'`
prints anything but `28 53149339648`". After the restart it prints `28 53149335552`, 4,096 bytes
fewer, with the VM setting unchanged: the VM kernel's accounting moved across a restart, the host
did not. From this Outcome on, that trigger means the VM's memory setting or CPU count changed,
read from Docker Desktop's settings, not a byte-exact `MemTotal`.

**Revisit when** the host's out-of-memory killer kills the VM a second time: then keeping the
desktop light has failed, and the VM setting comes down towards the 39 GiB floor or the host
gets more RAM.
