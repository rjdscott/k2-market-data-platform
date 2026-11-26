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

What runs is capture to Redpanda. The lake receives no live crypto data until phase 004
(the old ingest is retired to `legacy/v3-lake-ingest/`) and ClickHouse is paused
(ADR-057), so lake and ClickHouse rows read "not measured" with that reason until then.
Every capture number comes from the capture's own metrics through Prometheus.

**Throughput per exchange** (records the broker acknowledged, per second, 5 minutes)
```bash
curl -sG localhost:9090/api/v1/query --data-urlencode \
  'query=sum by (exchange) (rate(k2_capture_records_delivered_total[5m]))' \
  | jq -r '.data.result[] | "\(.metric.exchange)\t\(.value[1])"'
```

**Exchange-to-receive latency p50/p95/p99, with n** (trades only; state plainly that this
includes internet transit and exchange clock skew). Swap 0.99 for 0.50 and 0.95; n is the
count over the same hour.
```bash
curl -sG localhost:9090/api/v1/query --data-urlencode \
  'query=histogram_quantile(0.99, sum by (exchange, le) (rate(k2_capture_exchange_to_recv_seconds_bucket[1h])))' \
  | jq -r '.data.result[] | "\(.metric.exchange)\t\(.value[1])"'
curl -sG localhost:9090/api/v1/query --data-urlencode \
  'query=sum by (exchange) (increase(k2_capture_exchange_to_recv_seconds_count[1h]))' \
  | jq -r '.data.result[] | "\(.metric.exchange)\t\(.value[1])"'
```

**Gaps / continuity**: sequence discontinuities per venue over the window, with the
window stated.
```bash
curl -sG localhost:9090/api/v1/query --data-urlencode \
  'query=sum by (exchange) (increase(k2_capture_gaps_total[24h]))' \
  | jq -r '.data.result[] | "\(.metric.exchange)\t\(.value[1])"'
```

**CPU / RSS per service vs compose limit**: the binding constraint of this project. All
services, one table, with headroom against the 25 CPU / 48 GiB ceiling (ADR-045) that
`python3 scripts/check-budget.py` gates.
```bash
docker stats --no-stream --format \
  "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"
python3 scripts/check-budget.py   # the declared limits to compare against
```

**Storage per topic**: bytes on disk per Redpanda topic.
```bash
docker exec k2-redpanda rpk cluster logdirs describe --aggregate-into topic
```

**Lake and ClickHouse**: not measured until phase 004 lands a live lake product and the
hot tier resumes; say so in the row, never carry an older file's figure forward.

**MTTR**: only from `docs/runbooks/failure-recovery.md`, and only
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
