# Phase P6: controls catalogue, SLOs and release engineering

**Depends on:** P1 to P5 (the controls collect evidence from what those phases built)
**Decision it implements:** ADR-040 (controls catalogue as code), ADR-041 (SLOs and error budgets) and ADR-042 (release engineering), each written in the pull request that builds it. ADR-041 lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §10, whose own trigger is a recorded window of 24 h or more, and appends an Outcome there.
**Delivers:** a compliance reporting model that runs daily on real evidence, a readiness scorecard that gates publication, SLOs with burn-rate alerts generated from contracts, and the release mechanics.
**Exit:** `make compliance-report` renders from rows in `governance.control_evidence`, with one control shown failing and then passing after its cause is fixed; `make check-alerts` passes with the generated rules and their tests; every SLO target cites a benchmark window of at least 24 h; a contract change that drops a column without a major version bump fails CI; setting a product to `published` with a failing scorecard item fails CI.

## Scope

**Pull request 1: controls catalogue, ADR-040**

- `governance/controls.yaml`: per control an id, objective, the standard it enforces, owner,
  evidence query or command, frequency and pass criterion. Ten controls: contract present and
  valid; owner assigned; classification set; lineage captured; quality checks passing; SLO
  met; retention applied; infrastructure code free of drift; change by pull request with CI
  green; access policy matches classification.
- A control whose evidence source does not exist reports "not applicable" with the reason.
  That is the state of the infrastructure-drift control until the AWS plan lands, and of any
  control whose source was cut as stretch.
- DAG `control_evidence`, daily: a Spark task runs each control per data product and appends
  to `governance.control_evidence`, writing pass and fail counts into the snapshot summary,
  the pattern `k2.audit-failures` already uses. `lake-metrics` reads summaries today, so the
  Grafana dashboard needs no new reader.
- `make compliance-report` renders `docs/compliance/report-<date>.md`. One report is
  committed per release as a dated snapshot; daily output is not committed.
- `docs/compliance/README.md` describes the model.
- Readiness scorecard, per product, from the same evidence: descriptor valid, owner set,
  classification set, every column described, quality rules present, SLO declared (gold and
  marts), lineage seen, at least one access point declared and passing. `k2gen` writes the
  items it can check offline to the product page, beside a "Quality and known limitations"
  section taken from the contract: what is checked, the limits that apply, and the query that
  returns the live record. Pages are generated and identical on every run, so they carry no
  live results.
- Live quality results live on the platform: `governance.dq_results` through Trino and the
  notebook helper, a Grafana dashboard in the default stack showing status by product and
  day, and OpenMetadata under the `governance` profile, where each result attaches to the
  table it concerns.
- Lifecycle gate in CI: a descriptor may say `published` only when every scorecard item that
  can be checked offline passes. Items that need the stack are checked by the evidence DAG,
  and a published product failing one raises an alert. Existing products start as
  `published` only if they pass; the rest are `draft` with the missing items listed.

**Pull request 2: SLOs, ADR-041**

- A `/benchmark-report` over a continuous window of at least 24 h under Airflow comes first.
  SLO targets cite it.
- SLOs for consumer-facing products (`gold` and `marts`) and a freshness SLO for each
  file-source product, which no existing alert covers. Streaming source-aligned tables are
  covered by the existing lake alerts.
- A completeness gauge added to `docker/lake/metrics.py` from the offset-gap and audit
  summaries it already reads. Freshness uses the existing last-commit gauge.
- `k2gen` emitter: recording rules and multi-window burn-rate alerts into
  `docker/prometheus/rules/`, their promtool tests into `rules/tests/`.
- One runbook per SLO kind (freshness, completeness, latency) via `/runbook`; every generated
  alert points at its kind's runbook.
- `docs/operations/observability.md` SLO table replaced by the generated one.

**Pull request 3: release engineering, ADR-042**

- `k2gen check-compat`: compares each contract with its version on `origin/main`. Removing or
  retyping a column without a major bump fails CI.
- `CHANGELOG.md`, kept from this plan forward.
- A release workflow: a pushed tag creates the release with that tag's changelog entry.
- Stretch: blue/green dbt. Build into a staging schema, test, swap.

**Pull request 4: drills, stretch**

- `make dr-drill`: drop and rebuild one lake layer and the ClickHouse tier from raw, timing
  recovery and stating the recovery point. Published through `/benchmark-report`.
- `make chaos` gains Airflow scheduler stop and Trino stop.

**Documents.** Chapters 11 and 16.

## Verification

```bash
make generate && test -z "$(git status --porcelain)"
docker exec k2-airflow-scheduler airflow dags test control_evidence
make compliance-report
make gates
make dr-drill      # stretch
make chaos         # stretch: includes the two new scenarios
```
