# Phase 002: controls, alerts and release engineering

**Depends on:** `001` (tag `v3.0.0-beta.3`: every product of 3.0 exists and records evidence);
Alertmanager delivering to Slack since the first plan's release spine.
**Decision it implements:** `docs/adr/ADR-056-unified-data-product-platform.md`. New ADRs, each
in the pull request that builds it: objectives and error budgets, which lift section 10 of
[ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) (its trigger is a recorded window of
24 hours or more) and append an Outcome there; the controls catalogue as code. ADR-042, accepted
in the first plan with the deploy, gains the running version in `make health`.
**Delivers:** per-product objective and multi-window burn-rate alerts generated from each
contract's `slaProperties`; a duration alert for five-minute products; the controls catalogue,
its daily evidence and `make compliance-report`; the running release id in `make health`; the
Prometheus image upgraded.
**Exit:** a generated objective alert, fired by its promtool test and by a live drill, arrives in
Slack; every objective target cites a benchmark window of at least 24 hours; the duration alert
fires on a run held past its bound; `make compliance-report` renders from evidence with one
control shown failing and then passing after its cause is fixed; `make health` prints the release
id; the upgraded Prometheus image passes the CI security job; tag `v3.0.0-beta.4`.

## Scope

**Not built here, because the first plan built it** (review findings F5, F18 and trap 5): image
digests and the deploy log in the release manifest; Alertmanager and its Slack route; the
compatibility check, which is the checks library's rule that a drop, retype, rename or re-key is
a major version.

**Objectives and alerts**

- A `/benchmark-report` over a continuous window of at least 24 hours comes first; every
  objective target cites it.
- The compiler's alert emitter writes, per product, recording rules and multi-window burn-rate
  alerts for freshness, completeness and latency from `slaProperties`, and their promtool tests
  into `docker/prometheus/rules/tests/`. A burn rate is how fast an objective's error budget is
  being spent.
- A duration alert for every five-minute product: a run past its bound fires before the next
  slot is missed (the open-items ledger had no ingest duration alert).
- One runbook per objective kind; every generated alert points at its kind's runbook, so gates
  (d) and (d2) of `make check-docs` hold.

**Controls**

- A controls catalogue as code: per control an id, objective, the standard it enforces, owner,
  evidence query, frequency and pass criterion. The controls cover: contract present and valid;
  owner assigned; classification set; lineage captured; quality checks passing; objective met;
  retention applied; host infrastructure free of drift (reported "not applicable" with its reason
  until `003` lands); change by pull request with CI green; access matching the contract's
  policies.
- A daily evidence task per product writes to the governance tables; `make compliance-report`
  renders a dated report from them. One report is committed per release as a dated snapshot.

**Release engineering.** `make health` prints the release id every part reports.

**The Prometheus image** upgraded past its known fixable critical vulnerabilities, pinned by
digest in the release.

**Learning aids and documents.** Runbook: an alert fires, and where it goes. Chapter 11
(observability) brought to the generated objectives.

## Verification

```bash
ls docs/benchmarks/<date>.md                        # the 24-hour window the objective ADR cites per target
make check-alerts                                   # every generated rule and its promtool test
bash scripts/chaos/objective-breach.sh <product>     # the alert fires and arrives in Slack
curl -s 127.0.0.1:9093/metrics | grep -E 'alertmanager_notifications(_failed)?_total\{integration="slack"\}'
bash scripts/chaos/run-overruns.sh <product>         # the duration alert fires
docker exec k2-airflow-scheduler airflow dags test control_evidence
make compliance-report                              # renders; one control failing, then passing after the fix
make health                                         # prints the release id
gh run view <security job run> --log | tail -5      # the upgraded image passes
make test
make check-docs
```
