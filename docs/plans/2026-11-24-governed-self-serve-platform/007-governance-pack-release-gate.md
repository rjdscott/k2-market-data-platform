# Phase P7: design and adoption documents, verification index and the release gate

**Depends on:** P1 to P6
**Decision it implements:** none new. It documents and proves the decisions under [ADR-031](../../adr/ADR-031-governed-self-serve-platform.md), which moves to Accepted here if every ADR in its table landed.
**Delivers:** documents for people who run or adopt the platform, a verification index whose every row links to its evidence, the documentation map updated to reach it, and a tagged release that passed the fresh-clone gate.
**Exit:** `/release-check` passes on a fresh clone with the default profile; `make check-docs` passes, which proves every evidence link in the index resolves; the golden path was followed from zero and the product it created ran one scheduled DAG run on the stack.

## Scope

**Design and adoption documents** (`docs/governance/`)

- `solution-design.md`: end to end across ingestion, storage, transformation and serving.
- `design-review-checklist.md`: what a change to the platform or a new product is checked
  against before it is built. It is the engineering standards table turned into questions.
- `adoption-guide.md`: how a team would run this platform for real. Who owns a domain and a
  product, who approves a contract change and a classification change, how a new product is
  proposed and onboarded, what the platform maintainers own. One document, in place of
  separate organisational charts.
- `decision-log.md`: a short page grouping decisions by component, each a link into
  [the ADR index](../../adr/README.md). It carries no statuses of its own.
- `docs/development/spark-dbt-airflow-guide.md`: which tool does what, with examples from
  this repository, including what `dbt parse` on a second target does and does not show.
- `docs/development/golden-path.md`: the onboarding path, told with FX as it was actually
  built (declare, ingest, process, assure, trace, serve, catalogue, publish), each step with
  the pull request that did it and the command that shows it. Then proven a third time from
  zero: `make new-product`, a parser, `make generate`, and the product reaches `published`.

**Verification index**

- `docs/verification-index.md`: one row per component (capture, ingest, contracts and
  generator, orchestration, marts, FX product, access, lineage and catalog, controls, SLOs,
  release). Each row has what is claimed, the evidence written as a markdown link, and the
  command that shows it.
- Gate (a) of `scripts/check-docs.sh` already fails a link that does not resolve, so the
  index is enforced by an existing gate and no new one is added.
- The cloud row links [17-scale-out-path.md](../../architecture/17-scale-out-path.md) and says
  that the infrastructure code is a separate plan.

**README**

- The existing ten-minute path in `docs/README.md` gains the index as a stop.
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
