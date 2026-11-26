# Phase 001: the release and rules spine

**Depends on:** `000` (tag `v3.0.0-alpha.2`: one contract format, nothing live reads the old
tables, the spike findings written up).
**Decision it implements:** [`ADR-056-unified-data-product-platform.md`](../../adr/ADR-056-unified-data-product-platform.md). ADR-055
(product definition, #240) is revised to ODPS and ODCS and accepted; ADR-036 (file connector,
#239) is revised for per-product landings and the fetch container and accepted, its connector
interface frozen here and built in `002`; ADR-042 (#225) is revised and accepted with the
deploy ADR, since image digests and the deploy log land here (review finding F5). New ADRs, each
in the pull request that builds it: releases, deploy and pinned runs; reader and writer
catalogs, per-product writers and the warehouse layout, superseding
[ADR-044](../../adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)'s layout; principals,
roles, grants, named policies and identity. ADR numbers are reserved per workstream in the
interfaces pull request.
**Delivers:** the interfaces pull request; immutable releases with image digests; `make deploy`,
`make release-verify` and automatic rollback; the stack running from releases, generated files
out of git; the ODPS and ODCS document model, the checks library and the validation runner in
CI; reader and writer catalogs, per-product writers, rules that deny every metadata table,
resource groups and the rules refresh; password login over TLS; principals, grants, policies,
hand-written access expectations and `make access-verify` over SQL; the tested-data marker on
the reader side; Alertmanager delivering to Slack.
**Exit:** a deploy of the phase's release verifies; a forced mismatch rolls every part back to
the previous release, images included, and exits non-zero; a rules-only deploy restarts no
container; a changed grant is live within the refresh window without a restart; no generated
file is tracked; writer A is denied on product B's table; a definer view, an `env_var` and a
policy with no expectation each fail `k2gen check`; a connection with no password or over plain
HTTP is refused; a pipeline query is placed in the `pipeline` resource group; a reader never
sees rows past the marker; a test alert reaches Slack; tag `v3.0.0-alpha.3`.

## Scope

The build order and every step's files, dependencies and proof are in sections A to E of
[`IMPLEMENTATION-N0-N1.md`](../../research/2026-11-26-unified-platform-architecture/IMPLEMENTATION-N0-N1.md). This phase
takes the steps that make the release, the definitions and the rules; `002` takes the run path
and `003` the serving path. Because the old pipeline retired in `000`, the compiler carries no
emitter for the old layout and nothing here has to keep `lake_ingest` running.

**Pull request 1: the interfaces, merged before any parallel building** (review finding F2).
Schemas, stubs and fixtures only, no behaviour, reviewed once:

1. Release layout and manifest: the tree of `releases/<id>/`, the JSON Schema of `manifest.json`
   (id, commit, provenance, file hashes, image digests, and what changed against the previous
   release), the id format, pointer semantics, and the path at which containers see the releases
   root. Secrets are named by environment variable only and never compiled.
2. The document set and the checks interface: `load(tag)` returning a document set with its
   provenance record (source, version, hash, approver, time), and `check(docs, previous)`
   returning findings.
3. Schemas: ODPS 1.1.0 and ODCS 3.2.0 with every K2 custom property (`connector` per kind,
   `schedule`, `terms`, `lifecycleChange`, `serves`, `required`, `maxLagMinutes`, `build`,
   `policies`, `backup`, `unit`, `time`, `asOf`).
4. Generated connector configuration and the parser interface: what the fetch container and the
   Spark loader read per product, the spool layout, the fetch record, exit codes, the value
   passed between tasks.
5. Naming: landing keys, per-product namespaces, the audit branch, snapshot summary properties,
   catalogs (`<domain>`, `<domain>_build`, `lake`), principals (`svc.*`, `p.*`,
   `dbt_<product>`), groups (`<product>__<role>`), resource groups, the per-product lock.
6. The run-record and governance tables, and the one query "newest audited snapshot of X".
7. `k2_input(product, table)` and the dbt wrapper's command line, environment allow-list, exit
   codes and hand-off of snapshot ids to the expiry task.
8. Access formats: roles, principals, grants, the three policy kinds, the expectations file
   with its negative probes, and the coverage rule.
9. REST: OpenAPI for every `/v1` route phase `007` completes, frozen now so that phase finishes
   routes rather than reworking them.
10. The catalog JSON, one document per product, which the `/v1/products/{domain}/{product}` body
    and the static site both render.
11. `backup-set.json`, and the splits of the files every worker would otherwise edit: the
    generator becomes one module per output, the `Makefile` gains `mk/*.mk` includes, compose
    gains service stubs for `k2-api`, `fetch` and `dbt-runner`.

**Then, in waves on disjoint files** (the file-ownership rules are section C of the breakdown):

- **The document model.** The vendored ODPS 1.1.0 schema and the K2 profile; a synthetic
  fixture set; the model and the document-set adapter (`git archive` of a tag, byte-identical on
  a re-run). Whether the adapter is a class or only the provenance record plus
  `k2gen release --tag` is the owner's choice here, default kept as written (plan review
  section 6).
- **The checks library**, pure functions of a document set and the previous release's: both
  schemas, unique product names, one producer per table, every column described, terms before a
  non-owner grant; allowed lifecycle transitions; a drop, retype, rename or re-key is a major
  version (this is the compatibility check; the controls phase does not build another); no
  retirement while declared as an input; grants name declared roles and principals, one role per
  principal per product; inheritance and coverage; definer views refused (F12); in product SQL,
  `env_var` is an error, and `run_query`, `statement`, `call`, `adapter.` and a relation outside
  `source`, `ref`, `k2_input` are warnings.
- **The compiler**, `k2gen release`, sealing each release read-only before it is visible, with
  emitters for Trino catalogs, rules, groups and resource groups; lake DDL, schemas, retention
  and the storage plan; pipelines, the DAG list and one-shot specs; the dbt project tree; the
  catalog JSON and pages; the API key map. `k2gen diff` prints the file-level and storage-plan
  difference against a previous release. The storage plan (every table, column, bucket, key and
  namespace a release would create or change) is built so the infrastructure-as-code phase
  converges with it rather than rebuilding resource creation (owner decision on scope; F21).
- **Deploy**: `make deploy RELEASE=<tag>` compiles from the tag, preflights (DAG import, `docker
  compose config`, storage plan, and the access test once it exists), drains only when Trino
  must restart or DAGs change shape, applies additive DDL, moves the pointers, and runs
  `make release-verify`, which asks every part which release it runs; on a mismatch it points
  everything back at the previous release the same way and exits non-zero. One fixed compose
  project directory and project name; the releases root mounted once and the pointer resolved
  inside it, which the bind-mount gotcha in `CLAUDE.md` requires; only the services whose inputs
  changed are recreated, never the captures, Redpanda, MinIO, Postgres or Lakekeeper (F4).
  Images are pinned by digest in the manifest, so a rollback restores them too. Per-service
  pointers or one pointer for the whole release is the owner's choice here, default kept as
  written (plan review section 6).
- **The stack runs from releases.** Airflow's DAG folder, the dbt project, Spark's scripts,
  Trino's `etc` and rules, and Prometheus's rules are read through their pointer. The first
  step lists what writes into `./docker/lake` today (it is mounted read-write); the whole move is
  rehearsed on a throwaway compose project with no published ports before the live one.
- **Generated files leave git.** `docs/products/`, `docker/lake/generated/`,
  `docker/lake/ddl/generated/`, the generated Trino catalogs and `rules.json` are deleted; the CI
  drift step, `make generate` and the throwaway Trino and Airflow tests read a compiled release
  instead. The catalog is rendered once, from the catalog JSON (review trap 8).
- **The validation runner in CI** on every pull request: `k2gen check`, a trial compile,
  `k2gen diff` against the newest `v3.*` tag as the job summary, dbt parse and unit tests, the
  fixtures and the access test.
- **Catalogs and rules.** A reader catalog per domain with `iceberg.security=read_only`; a writer
  catalog per domain reachable only by `dbt_*`, `svc.runs` and `svc.platform`; `lake` read-only
  over warehouse `k2`. One group per product and role through the file group provider; per table
  one rule per offered role naming only that product's groups; exact table names, so every `$`
  metadata table is denied; masks to typed nulls; `delay` and `rowScope` row filters; a writer
  `dbt_<product>` writes only its namespace and selects only its input ports.
  `security.refresh-period=5s`, safe because the rules file changes only by an atomic pointer
  swap after preflight. Resource groups `pipeline` (writers and services) and `interactive`.
- **Identity** (owner decision, built here because every client depends on it). Trino's password
  authenticator over HTTPS with a certificate the platform generates locally; plain HTTP
  disabled. Every principal that reaches Trino has a password: writers, platform services and
  people under their pseudonymous ids. The REST API authenticates its callers by key and queries
  Trino as the caller under an impersonation rule that admits only its own service principal.
  `make api-key PRINCIPAL=<id>` prints a key once and writes only its sha256 for a pull request;
  the compiled key map is in the release, read by k2-api in `003`. Every client is moved here:
  the Trino CLI, dbt-trino, the `trino` Python client, Airflow tasks, the access test.
- **Principals, grants and policies**: `contracts/access/roles.yaml` (`full_reader`,
  `delayed_reader`, `public_reader`), `principals.yaml`, a `grants.yaml` per product, policies
  in each table contract. `tests/access/expectations.yaml`, hand-written and reviewed by the
  owner, and `make access-verify`, which runs every row through SQL on a throwaway Trino and
  then live, on a seeded fixture product. REST joins it in `003`.
- **The tested-data marker** (owner decision: readers see only tested data). A small spike
  settles how a reader catalog resolves the marker: an Iceberg tag that only the build moves is
  the candidate, read through invoker views if Trino cannot make a tag the reader's default (an
  invoker view applies the reader's own rules, [`SPIKE-ACCESS.md`](../../research/2026-11-26-unified-platform-architecture/SPIKE-ACCESS.md)). This phase builds the reader
  side and proves it by moving the marker by hand on the fixture product; the dbt wrapper in
  `002` moves it after tests pass.
- **`hot-gold`** declared paused in ODPS, with `serves` naming the paused ClickHouse copy (F20).
- **Alertmanager** v0.35.1 delivering to the Slack channel through an incoming webhook kept as a
  local secret (owner decision), so every runbook from here on can follow its alert to where it
  arrives (F18). 0.1 CPU and 64 MiB, counted by the budget gate.

**Learning aids and documents.** Runbooks: deploy and roll back; grant, revoke and verify
access; where an alert goes. Chapters 21 (the product model and deploy, with the documents,
lifecycle and deploy diagrams) and 22 (permissions). `CLAUDE.md`'s bind-mount paragraph and
`SECURITY.md` updated in the pull requests that change them.

## Verification

```bash
make deploy RELEASE=v3.0.0-alpha.3 && make release-verify
K2_DEPLOY_FAULT=verify-mismatch make deploy RELEASE=v3.0.0-alpha.3; echo "exit $?"   # non-zero
make release-verify                                   # every part, images included, reports the previous release
bash scripts/verify/rules-only-deploy.sh              # State.StartedAt unchanged for every container
bash scripts/verify/grant-refresh.sh                  # new grant usable within 30 s, Trino not restarted
git ls-files docs/products docker/lake/generated docker/lake/ddl/generated docker/trino/etc/rules.json | wc -l   # 0
make test-k2gen                                       # definer view, env_var, uncovered policy fixtures fail
make test-trino                                       # writer A denied select, insert, rollback_to_snapshot on B
make access-verify                                    # every row over SQL, every $ table denied, optimize refused
bash scripts/verify/identity.sh                       # no password, wrong password, plain HTTP: all refused
bash scripts/verify/resource-groups.sh                # a dbt_ query shows resource_group_id pipeline
bash scripts/verify/tested-data-marker.sh             # rows past the marker never visible to a reader
docker exec k2-alertmanager amtool --alertmanager.url=http://localhost:9093 alert add K2TestAlert severity=info
curl -s 127.0.0.1:9093/metrics | grep -E 'alertmanager_notifications(_failed)?_total\{integration="slack"\}'
gh run view <validation run id> --log | grep 'k2gen diff'
python3 scripts/check-budget.py                       # 16.80 CPU / 28.750 GiB
make test
make check-docs
```

`rules-only-deploy.sh` records `docker inspect -f '{{.Name}} {{.State.StartedAt}}'` for every
container, deploys a release that changes one rule, and diffs the two lists. The Slack check
passes when the sent counter rose and the failed counter did not. `make access-verify` probes
the metadata tables `$files`, `$snapshots`, `$manifests`, `$history`, `$partitions`,
`$properties` and `$refs`, since the spike tested only the first two (F12). When this phase
lands, its measured pace re-judges the days in the plan's README.
