---
name: benchmark-report
description: Measure the running stack and publish a dated numbers snapshot to docs/benchmarks/YYYY-MM-DD.md — throughput, gaps, latency percentiles with n, MTTR, storage per table, CPU/RSS vs limits, query timings — every row carrying the exact command that produced it. Use before publishing or updating any number in README/architecture docs, before tagging a release, after a burn-in, or when the user says "benchmark", "what are the numbers", "measure the stack", "is the README still accurate".
---

# benchmark-report — numbers, each with its command

Every number this project publishes must be traceable to the command that
produced it. This skill produces the file those numbers cite:
`docs/benchmarks/YYYY-MM-DD.md`. Conventions in `docs/benchmarks/README.md`.

**A number without a command is a claim, and claims get audited out.**

## Workflow

1. **Stack must be warm.** Report is meaningless on a cold stack — record how
   long it has been up (`docker compose ps`, uptime column) and state it. A
   24 h burn-in is the bar for anything going in the README.
2. **Load env:** `set -a && . ./.env && set +a`.
3. **Measure every row below.** If a measurement can't be taken, the row says
   "not measured" — never carry a stale number forward from an older file.
4. **Write** `docs/benchmarks/YYYY-MM-DD.md` from `template.md`. Each row:
   value, unit, sample size where it's a percentile, and the command in a code
   fence or a footnote. Include the commit: `git rev-parse --short HEAD`.
5. **Propagate.** Any doc quoting one of these numbers (README.md,
   `docs/architecture/README.md`, `docs/operations/{docker-resources,
   latency-budgets,cost-model}.md`, ADR Outcome sections,
   `docs/adr/README.md`) is updated in the same PR and links back to this
   file. Grep for the old value before you assume nothing quotes it.
6. **Register it** in the index in `docs/benchmarks/README.md`, newest first.

## The measurements

**Throughput per exchange**
```bash
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count()/300 AS msg_per_s, count() AS n
   FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 5 MINUTE
   GROUP BY exchange ORDER BY exchange"
```

**End-to-end latency p50/p95/p99, with n** — ingestion minus exchange timestamp;
state plainly that this includes internet transit and exchange clock skew.
```bash
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() AS n,
          quantile(0.50)(dateDiff('ms', exchange_timestamp, ingestion_timestamp)) AS p50_ms,
          quantile(0.95)(dateDiff('ms', exchange_timestamp, ingestion_timestamp)) AS p95_ms,
          quantile(0.99)(dateDiff('ms', exchange_timestamp, ingestion_timestamp)) AS p99_ms
   FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 1 HOUR GROUP BY exchange"
```

**Gaps / continuity** — sequence discontinuities and any window with zero trades
for a normally-liquid symbol. Report the denominator, not just the count.

**CPU / RSS per service vs compose limit** — the binding constraint of this
project. All services, one table, with headroom against the 16 CPU / 40 GB budget.
```bash
docker stats --no-stream --format \
  "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"
grep -nE "cpus:|memory:" docker-compose.yml   # the limits to compare against
```

**Storage per table, bytes/day and compression**
```bash
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT table, sum(rows) AS rows, formatReadableSize(sum(bytes_on_disk)) AS on_disk,
          round(sum(data_uncompressed_bytes)/sum(data_compressed_bytes), 2) AS ratio
   FROM system.parts WHERE database='k2' AND active GROUP BY table ORDER BY sum(bytes_on_disk) DESC"
```

**Offload lag and throughput**
```bash
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c \
  "SELECT table_name, status, NOW() - last_successful_run AS lag FROM offload_watermarks ORDER BY lag DESC"
```

**Query timings** — the queries a reader would actually run (an OHLCV window,
a symbol scan), each timed three times, median reported, and the row count.
Run with `clickhouse-client --time`.

**MTTR** — only from `docs/runbooks/failure-recovery.md`, and only
if the failures were re-induced for this report. Otherwise cite the runbook's
date and say it was not re-measured.

## Hard rules

- **Never round a number up to a nicer one.** 15.0 CPU is not "about 15";
  197 ms p99 is not "under 200 ms" unless you write both.
- Percentiles without n are meaningless — always report the sample size and
  the window.
- Snapshots are immutable. A new measurement is a new dated file, not an edit.
- If a published number moved by more than ~10%, say so explicitly in the new
  file's summary; that delta is the interesting part.
