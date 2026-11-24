# Phase P6: governance pack, capability matrix and the release gate

**Depends on:** P1 to P5
**Decision it implements:** none new. It documents and proves the decisions under [ADR-031](../../adr/ADR-031-governed-self-serve-platform.md), which moves to Accepted here if every follow-on ADR landed.
**Delivers:** the governance documents, a capability matrix whose every row links to its evidence, a ten-minute tour in the README, and a tagged release that passed the fresh-clone gate.
**Exit:** `/release-check` passes on a fresh clone with the default profile; `make check-docs` passes, which proves every evidence link in the matrix resolves; the golden path was followed from zero and the product it created ran one scheduled DAG run on the stack.

## Scope

**Governance pack** (`docs/governance/`)

- `solution-design.md`: end to end across ingestion, storage, transformation and serving.
- `design-authority-checklist.md`: what a design passes before build.
- `decision-log.md`: a short page grouping decisions by capability, each a link into
  [the ADR index](../../adr/README.md). It carries no statuses of its own.
- `stakeholders-raci.md`: architecture, product owners, delivery owners, workload engineers,
  business experts, security, the data office; a RACI per platform capability.
- `operating-model.md`: how the platform team supports engineers embedded in workload teams
  (golden paths, intake, design reviews, office hours, delivery tracking).
- `docs/development/spark-dbt-airflow-guide.md`: which tool does what, with examples from
  this repository, including what `dbt parse` on a second target does and does not show.
- `docs/development/golden-path.md` finished by doing it: `make new-product`, fill the
  contract, `make generate`, then see the table, the DAG run, the access rule and the
  quality result on the stack.

**Capability matrix**

- `docs/capability-traceability.md`: one row per capability, grouped by area (architecture,
  platform, governance, operations, security, data, cloud), with ids such as `ARCH-1`. Each
  row has the component, the evidence written as a markdown link, and the command that shows
  it.
- Gate (a) of `scripts/check-docs.sh` already fails a link that does not resolve, so the
  matrix is enforced by an existing gate and no new one is added.
- Cloud rows link [17-scale-out-path.md](../../architecture/17-scale-out-path.md) and say that
  the infrastructure code is a separate plan.

**README**

- A "Ten-minute tour" section pointing at the matrix.
- A short author paragraph in the README only.
- The architecture diagram and the counts it quotes brought up to date.

**Release gate**

- `/benchmark-report` for every number the README now quotes.
- `docs/runbooks/fresh-install.md` re-measured: image sizes, build time, time to healthy.
- `/release-check` on a fresh clone with the default profile; then a tag and a
  `CHANGELOG.md` entry, with the compliance report for that date committed.

## Verification

```bash
make new-product DOMAIN=market-analytics NAME=golden_path_demo
make generate && make test-k2gen
docker exec k2-airflow-scheduler airflow dags list | grep golden_path_demo
make gates
```

Then `/release-check`. The demo product is removed in the same pull request that records the
golden path, so the tree ships without it.
