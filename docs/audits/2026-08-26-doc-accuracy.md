# 2026-08-26 — documentation accuracy

**Verdict: the repo's headline numbers were all slightly wrong, and two documents walked a
new user into a broken stack.** Nothing in the pipeline was misbuilt; the gap was between
what the stack does and what 35 files said it does. The two blockers were both
instructions — a setup page telling you to hand-apply a schema that auto-applies, and a
quick start whose verify commands could not authenticate. The rest were drift: a service
added without updating the budget, an alert rule deleted without updating the counts, and
an observability gap that had been closed months earlier but was still documented as open
in eight places.

- **Commit audited:** `368c8f5` (the tree immediately before the fix).
- **Resolved in:** `535997a` — `docs: fix 31 accuracy findings from adversarial review`,
  35 files, +137 −207.
- **Findings:** 31 — 2 BLOCKER, 8 HIGH, 13 MED, 8 LOW.

## Scope

**In:** every `*.md` outside `legacy/`, plus `docker-compose.yml` comments and
`docker/prometheus/rules/*.yml` annotations.
**Out:** `legacy/v1/**` (archived v1, deliberately frozen), Kotlin and Python source,
Grafana dashboard JSON.
**Lens:** does each published claim match what the running stack does? Numbers were
recomputed from `docker-compose.yml` and the rule files rather than trusted; file paths
and cross-reference anchors were resolved; commands were read against the code they call.

## Method, and its honest limits

This was an **AI-assisted self-audit**, not an independent review. The maintainer ran an
adversarial pass with Claude over the doc surface, with the repo as ground truth: every
count re-derived from source, every path checked for existence, every anchor resolved. The
findings were then fixed in a single commit by the same pass that found them.

That has a clear failure mode worth stating: **an author auditing their own work with a
tool that shares the author's assumptions will miss whatever both of them assume.** This
audit is good at the class of error it targets — arithmetic, counts, stale paths, dead
anchors, claims contradicted elsewhere in the repo. It is weak on anything requiring the
stack to be run and observed, and it did not run one. Findings whose verification needed a
live stack are marked as such.

## Findings

| Sev | file:line | Claim | Reality | Fix |
|-----|-----------|-------|---------|-----|
| BLOCKER | `docs/development/setup.md:33` | "Only `docker/clickhouse/ddl/` is mounted … a fresh volume gets the `k2` database and the watermark table — but **not** the bronze, silver or gold tables. There is no bootstrap script yet; apply the DDL from `docker/clickhouse/schema/` by hand" | `docker/clickhouse/ddl/01-k2-schema.sql` is the full medallion bootstrap — 25 objects — and auto-applies via `/docker-entrypoint-initdb.d`. Following the doc meant hand-picking among duplicate and `-fixed` variants, some targeting `default` instead of `k2` | Replaced the whole section with a pointer to the auto-applied DDL; `schema/` named as the historical migration trail |
| BLOCKER | `README.md:109` | Quick start sets `.env` from `.env.example`, then the verify block runs `clickhouse-client --password "$CLICKHOUSE_PASSWORD"` | `$CLICKHOUSE_PASSWORD` is never exported, so every verify command in the quick start fails to authenticate on a clean clone | Added `set -a && . ./.env && set +a` to the quick-start block |
| HIGH | `docker/prometheus/rules/feed-handler-alerts.yml:22,80` | `FeedHandlerDown` described as "has not produced a trade message in {{ $value }}"; a second rule `FeedHandlerMetricsDown` carried expr `up{job=~"feed-handler-.+"} == 0` | Both rules had the same expr and the same `for: 2m` — one condition, two alerts. The description on `FeedHandlerDown` described a message-age condition it does not evaluate, which is why `observability.md` concluded the alert "cannot fire" and told operators to rely on the duplicate | Dropped `FeedHandlerMetricsDown`; corrected `FeedHandlerDown`'s description to the scrape-target semantics it actually has |
| HIGH | 8 files (`README.md:21,44,147,192`, `docs/architecture/README.md:42,145`, `docs/architecture/01-what-k2-is.md:57`, `docs/architecture/A1-technology-stack.md:17`, `docs/operations/observability.md:3,62,65`, `docs/operations/README.md:12`, `docs/adr/ADR-010-resource-budget.md:297`, `docs/MIGRATION-JOURNEY.md:128`) | "18 Prometheus alert rules", split 4 + 5 + 9 | 17 rules once the duplicate above is removed: 3 feed-handler, 5 ClickHouse, 9 Iceberg-offload | Every count and split corrected to 17 (3+5+9) |
| HIGH | 11 files (`README.md:14,84`, `docs/architecture/README.md:3,207`, `docs/architecture/01-what-k2-is.md:9`, `docs/architecture/01-what-k2-is.md:7`, `docs/operations/docker-resources.md:7,28`, `docs/operations/cost-model.md:11,77`, `docs/adr/ADR-010-resource-budget.md:270,292`, `docs/adr/ADR-016-add-coinbase-exchange.md:87`, `docs/MIGRATION-JOURNEY.md:61,116`, `docs/development/setup.md:10`, `docs/benchmarks/README.md:25`) | "15.0 CPU / 21.75 GB" | Recomputing from `docker-compose.yml`: **15.1 CPU / 21.875 GB**. The `iceberg-metrics` service (0.1 CPU / 128 MB) had been added without updating any total | All totals, headroom figures and reduction percentages recomputed from compose |
| HIGH | same files as above | "13 services (+1 one-shot `redpanda-init`)" | 14 long-running services and **2** one-shot: `iceberg-init` bootstraps the Iceberg tables and was undeclared everywhere | Corrected to 14 (+2 one-shot); `iceberg-init` added to the resource tables and the compose comment block |
| HIGH | 8 places (`docs/architecture/README.md:145`, `docs/architecture/01-what-k2-is.md:59`, `docs/operations/observability.md:48`, `docs/MIGRATION-JOURNEY.md:128`, and a "Metrics caveat (read first)" block at the top of `docs/runbooks/iceberg-offload-{failure,lag,performance}.md` and `iceberg-scheduler-recovery.md`) | "The offload metrics exporter listens on `:8000` inside `prefect-worker` but its Prometheus scrape job is commented out, so the 9 offload alerts have no live series … `curl localhost:8000/metrics` will fail and the alerts named here cannot fire" | The `iceberg-scheduler` scrape job targets `iceberg-metrics:8000`, a dedicated service. All 9 offload alerts have live series. Four runbooks opened by telling an operator mid-incident that the commands below them do not work | All 8 claims removed; `iceberg-scheduler` added to the scrape-target table; the one real remaining gap (no alert fire-tested end to end) kept |
| HIGH | `docs/architecture/A1-technology-stack.md:36`, `docs/README.md:35`, `docs/development/testing.md:10,39` | "There is no `gradlew` checked in"; `make test-kotlin  # needs JDK 21` | `./gradlew` is checked in, and `make test-kotlin` runs it inside `gradle:8.12-jdk21` — no local JDK needed. A contributor was told to install a toolchain the repo does not require | Corrected all three; the documented Docker invocation now calls `./gradlew test` |
| HIGH | `docs/operations/clickhouse-database-standard.md:43` | "Files are **not** auto-run on container start … A fresh volume needs the schema applied by hand", plus guidance on choosing between plain and `-fixed` variants | Same root cause as the first blocker, on a second surface: `ddl/01-k2-schema.sql` auto-applies; `schema/` is not run at all | Rewritten to point at the auto-applied DDL and label `schema/` as historical |
| HIGH | `docs/adr/ADR-006-spark-batch-only.md:263`, `docs/adr/ADR-007-iceberg-cold-storage.md:251,253`, `docs/adr/README.md:3,19` | "Iceberg on MinIO"; "a file-based Hadoop catalog rooted at the MinIO warehouse" | The Hadoop catalog is rooted at a **bind-mounted local warehouse**. MinIO is provisioned and running but the offload does not write to it — a reader sizing S3 costs or debugging a missing object would look in the wrong place | Corrected in the two Outcome sections and the ADR index; MinIO described as provisioned-and-unused-by-the-offload |
| MED | `docs/operations/latency-budgets.md:32` | Load scenarios anchored at "1× baseline ≈ 50 msg/s", so 5× = 250 and 10× = 500 | Measured baseline is ~150 msg/s (100–200 across three exchanges). Every load-test target was 3× too low, which would have made a 5× test easier than the 1× it claimed to multiply | Baseline restated as ~150 msg/s (100–200); 5× and 10× rows recomputed to ~750 and ~1500 msg/s |
| MED | `README.md:54` | Architecture diagram draws one offload edge, Gold → Iceberg, labelled "JDBC · 10 tables" | Bronze and Silver are offloaded too — the 10 tables are 3 bronze + 1 silver + 6 gold. The diagram contradicted its own edge label | Three dashed edges drawn: B, S and G → Iceberg |
| MED | `docs/operations/observability.md:24` | Prometheus scrape-target table lists feed handlers, ClickHouse, Redpanda and Grafana | The `iceberg-scheduler` job (`iceberg-metrics:8000`) was missing from the table, which is why the offload-alerts gap above went uncorrected for so long | Row added |
| MED | `docs/operations/observability.md:91` | Recording rules: `clickhouse:insert_rate:5m`, `clickhouse:query_duration_p99:5m` | The second rule is `clickhouse:query_duration_mean:5m`. ClickHouse's Prometheus endpoint exposes no histogram, so a p99 recording rule cannot exist | Corrected to `:mean:`; the "no query-latency percentiles" gap kept in the gaps list |
| MED | `docs/operations/observability.md:88` | `ClickHouseBronzeInsertRateLow` — "<0.5 rows/sec inserted over 10m" on bronze | The expr is server-wide inserted rows over **5m**, not bronze-scoped and not 10m. An operator would have mis-triaged the alert's blast radius | Description corrected to the expr's actual semantics |
| MED | `README.md:144`, `docs/operations/observability.md:62` | Five HTML comments of the form `<!-- screenshot: docs/images/grafana-pipeline-overview.png -->` | Placeholders that render as nothing. The `.png` files never existed; `docs/images/` holds three `.jpg` screenshots that no document referenced | Replaced with real embeds of the three `.jpg` files; the two with no corresponding image dropped |
| MED | `docker/README.md:14` | `docker/clickhouse/ddl/` — "offload-watermarks table DDL"; `schema/` — "Ordered schema migration history" implying it runs | Third surface of the ddl/schema confusion: `ddl/` is the medallion bootstrap and auto-runs; `schema/` does not run | Both descriptions corrected |
| MED | `docker-compose.yml:781` | Resource-budget comment block totals "15.5 CPU / 21.75 GB" | Neither the docs' 15.0 nor the compose comment's 15.5 matched the file's own limits (15.1). `iceberg-metrics` and `iceberg-init` had no rows | Both rows added; TOTAL corrected to 15.1 CPU / 21.875 GB |
| MED | `docs/operations/prefect-schedules.md:50` | ClickHouse TTLs: "7-day on bronze, silver, 1-year on gold" | Gold `ohlcv_1d` carries a 2-year TTL; only `ohlcv_{1m,5m,15m,30m,1h}` are 1 year. A capacity-planning reader would undercount daily-candle retention | TTL line split by table |
| MED | `docs/architecture/A1-technology-stack.md:45` | `prometheus-client` — "Offload metrics exporter in the Prefect worker" | It runs in the `iceberg-metrics` service. Same root cause as the offload-monitoring finding, in the stack table | Corrected to name `iceberg-metrics` |
| MED | `docs/MIGRATION-JOURNEY.md:50` | Phase 7 — "monitoring 🟡, runbooks ⬜, 🟡 3 of 5 steps complete" | Monitoring landed in `c3fd668` and 8 runbooks exist. The phase log understated its own repo | Row updated to 4 of 5, runbooks enumerated, remaining work named (24 h burn-in, 5×/10× load test, Alertmanager routing) |
| MED | `docs/operations/adding-new-exchanges.md:106,169,324` | New-exchange checklist writes DDL to `docker/clickhouse/schema/XX-bronze-{exchange}.sql` and applies that file | That directory is not run. Following the checklist produces a file the stack ignores, on a path whose convention no longer applies | Steps rewritten to append to `ddl/01-k2-schema.sql`, with an explicit note that it only auto-runs on a fresh volume so an existing one needs a hand-apply |
| MED | `docs/runbooks/iceberg-offload-failure.md:78`, `docs/runbooks/iceberg-offload-watermark-recovery.md:93` | Both jump to `#scenario-1-scheduler-not-running` | The heading is "Scenario 1: Worker Not Running" — anchor `#scenario-1-worker-not-running`. Two dead in-page links, both on the escalation path of an incident runbook | Anchors corrected |
| LOW | `README.md:57` | Diagram draws the `:9363` Prometheus edge from the Gold node | ClickHouse the server exposes `:9363`, not a table tier | Edge re-sourced to the `CH` node |
| LOW | `docker/README.md:29` | Grafana dashboards — "pipeline overview, ClickHouse, Iceberg offload" | Four dashboards are provisioned; `v2-migration-tracker` was missing, contradicting the "4 Grafana dashboards" claimed in `README.md` | Fourth dashboard listed |
| LOW | `docker-compose.yml:784` | "Remaining budget: 0.5 CPU / 18.25 GB for future expansion" | 0.9 CPU / 18.1 GB against the recomputed total | Corrected |
| LOW | `docs/architecture/06-capture-venues.md:113` | "At three exchanges and ~30 instruments" | `config/instruments.yaml` holds 34 (12 Binance, 11 Kraken, 11 Coinbase) | Corrected to 34 |
| LOW | `docs/adr/README.md:3` | "the two decisions that were reversed (ADR-008)" | One decision, ADR-008. The sentence names two and lists one | Corrected to "the one decision that was reversed" |
| LOW | `docs/adr/ADR-016-add-coinbase-exchange.md:110` | Verification checklist — "`docker compose up -d` starts all 13 services" | 14 | Corrected |
| LOW | `docs/adr/ADR-010-resource-budget.md:273` | "Fits 16 CPU / 40 GB? Yes — 6% CPU / 46% RAM headroom" | 45% RAM headroom against the recomputed total | Corrected |
| LOW | `docs/runbooks/README.md:12` | Index row for `failure-recovery.md` lists `FeedHandlerMetricsDown` among its trigger alerts | That rule no longer exists | Replaced with `FeedHandlerDown` |

**Resolved in `535997a`** — all 31 findings, one commit, 35 files (+137 −207).

## What this audit did not cover

- **No alert was fire-tested.** The rule files were read, not exercised. "17 rules load"
  is a claim this audit verified by counting; "17 rules fire correctly" is not.
- **No stack was run.** Every finding was derived from source. A doc that is accurate
  about a stack that no longer starts would pass this audit.
- **`legacy/v1/**` was out of scope** and is expected to contain claims that were true in
  2025 and are not now. Its one live cross-reference into `docs/` was fixed separately.
- **Numbers with no command behind them.** The 12:1 Iceberg compression ratio and the
  99.9%+ warm/cold consistency figure are quoted from ADR-007's Outcome section and were
  not re-derived. See [`../benchmarks/2026-02-19-v2-baseline.md`](../benchmarks/2026-02-19-v2-baseline.md).

## Follow-ups

The re-run is the gate, not an edit to this file. Two things would materially raise
confidence next time:

1. **An independent pass** — a reviewer who did not write the docs, or at minimum a
   different model with no session context.
2. **A live-stack pass** — bring the stack up from a clean clone, follow every quick start
   and runbook literally, and record where reality diverges. This audit could not catch a
   command that is correctly documented and simply broken.
