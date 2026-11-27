# k2gen interfaces, frozen for phase 001

What steps S04 to S18 build against, so that each fills one module and none edits a shared
file. S00a froze them; S00c finalised them after an independent review and the gate
[`2026-11-26-tested-data-view-gate.md`](../../docs/research/2026-11-26-tested-data-view-gate.md),
under [`DECISIONS-FINAL.md`](../../docs/research/2026-11-26-unified-platform-architecture/DECISIONS-FINAL.md),
[ADR-055](../../docs/adr/ADR-055-products-defined-once-in-odps-and-odcs-documents.md),
[ADR-056](../../docs/adr/ADR-056-unified-data-product-platform.md) and
[ADR-061](../../docs/adr/ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md).
S00d patched them additively once the building steps had reported their gaps (the last section).
The design inputs are kept, dated, in
[`docs/research/2026-11-26-phase-001-interfaces/`](../../docs/research/2026-11-26-phase-001-interfaces.md).
The shared files outside k2gen, the run-time contracts of phase 002 and the collision list are in
[`docs/development/phase-001-interfaces.md`](../../docs/development/phase-001-interfaces.md).

**The rule.** A schema in `contracts/schema/`, or a type, constant or helper in
`k2gen/model.py`, changes only through the orchestrator, who tells every dependent step. A
step fills the bodies of its own module, adds failing fixtures only under
`tests/fixtures/v2/bad/<its check>/` and golden files only under `tests/golden/<its emitter>/`,
and never edits `cli.py`, `checks/__init__.py`, `emit/__init__.py`, `model.py`, the good set,
`tests/fixtures/v2/bad/schema/cases.yaml` or `tests/test_interfaces.py`. That test holds the
signatures, not the stubs: it finds every module by `pkgutil.iter_modules`, requires the ones
phase 001 names (a superset, so a step may add a module), holds every module found to the frozen
signature and type hints, and never asserts that a module is still a stub. Filling a module, or
adding one with the right signature, leaves it green; a drifted signature fails it.

## The two module shapes

- **Emitter**, a module in `k2gen/emit/`: `emit(model: Model) -> dict[str, str]`, release-relative
  path under `compiled/` to file text. Pure: no clock, git, network, secret, and no previous
  release (an emitter that read it would give one set two ids, CI's and deploy's).
  `emit.run(model)` calls them all: two emitters returning one path, a path outside
  `compiled/`, or an emitter still raising `NotImplementedError` is an error (since step S23:
  a compile emits every file or none).
- **Check**, a module in `k2gen/checks/`: `check(docs: DocumentSet, previous: DocumentSet | None)
  -> list[Finding]`, `Finding.code` spelt `<check module>.<kebab-case>`; `Finding.pointer`, optional
  (default None), is where in the document (a JSON pointer or a `$` path), and `k2gen check`
  prints it after the path. `checks.run` runs `docs`
  first and stops there on an error. `k2gen check --previous` takes a document set (a release's
  `documents/` is one); `k2gen release --previous` takes a release directory.
- Every v2 document is parsed by **`model.parse(path, bytes)`**, never `yaml.safe_load`: one
  document, and no duplicate key (safe_load keeps the last, so a second `mask:` would win
  silently), anchor, alias or merge key. A product model's `{{ config(...) }}` is read by
  **`model.model_config(sql)`** alone (S05 takes `materialized` and `unique_key`, S07 refuses
  `incremental_predicates`): a scan that skips quoted strings whole, so a hook holding
  `{{ ref('x') }}` is read, and any refusal (a Jinja operator such as `~` included) is a
  `ValueError`, never another exception.
- An ODCS quality rule becomes a dbt test, or is refused, through **`k2gen.emit.dbt.quality_test`**
  alone: S17 emits by it and S05 reports what it refuses (`docs.quality-unsupported`), so no rule is
  dropped. `model.quality_of` reads an entry; `model.QUALITY_OPERATORS` are ODCS v3.2.0's `must*`
  thresholds, every one supported with numbers.

## Where each interface is frozen

| Item | Frozen in |
|---|---|
| A1 release and manifest | `manifest.schema.json` (`$defs/images` is also the `--images` input; no secret field); `deploy-record.schema.json` (one line of `model.DEPLOY_LOG`); `model.py`: `Manifest`, `Needs`, `Release`, `release_id`, the release paths below, `NEEDS_BY_PREFIX`, `need_of`; `release.release(set_dir, previous, images, out_root, compose)`; `k2gen release --compose` |
| A2 documents and provenance | `odps-json-schema-v1.1.0.json` (vendored), the ODCS file, `k2-v2.schema.json`; `provenance.schema.json`; `model.py`: `DocumentSet`, `Provenance`, `set_sha256`, `DOCUMENT_ROOTS` (with `docker-compose.yml`; a root the tag lacks is skipped), `document_kind`, `schema_errors`, `parse`; `docset.py` |
| A3 compiler and checks | the two module shapes above; `model.Finding`; `cli.py`: `docset`, `check`, `release`, `diff`; `diff.py` |
| A4 connector configuration | `pipeline.schema.json`, `dags.schema.json`, `oneshots.schema.json`; `k2-v2.schema.json#/$defs/connector`, one definition per kind, url, path, key and params admitting `{{ds}}` and no other macro (`model.TEMPLATE`) |
| A5 landing and bronze | `model.py`: `LANDING_BUCKET`, `landing_key`, `landing_prefix`, `lake_relation`, `PLATFORM_TABLES` (the columns of `landing_files`, `audit_checks`, `landing`), `LINEAGE_COLUMN`, `AUDIT_BRANCH`, `SNAPSHOT_SUMMARY_KEYS`, `RESERVED_*`, `product_name_problem`, `domain_name_problem`; `Column.pattern` (ODCS `logicalTypeOptions.pattern`) feeds the pipeline's `patterns` |
| A6 run records | `run-record.schema.json` (one row, `dbt_invocation_id` and `openlineage_run_id` included); the DDL `platform/runs/run_record.sql` (`model.RUN_RECORD_SQL`), equal column for column (`tests/test_frozen_interfaces.py`) |
| A7 `k2_input` and the wrapper | `pipeline.schema.json` `build`; `model.View`, `model.VIEWS`; `dbt/macros/k2_input.sql`; the wrapper's contract in the phase page |
| A8 names | `model.py`: `fence`, the catalog, namespace, builder, publisher, group, DAG, dbt and relation helpers, the principal lists below, `canary_user` |
| A9 access expectations | `expectations.schema.json` (a statement row may carry `session`, the header form; a read row `mustAppear`, the positive assertion); `model.py`: the six expectation types (the sixth, `StorageKeyExpectation`, added 2026-11-27 for phase 002), `MustAppear` |
| A10 roles, principals, grants, policies | `k2-v2.schema.json#/$defs/{roles,principals,grants,policies,rolePolicy}`; `model.py`: `RoleDef`, `Principal`, `Grant`, `RolePolicy`, the secrets table below |
| A11 catalog JSON | `catalog-product.schema.json`, its `$defs/index` for `index.json`; `model.Product.support`, `.links` (ODPS support and authoritativeDefinitions) |
| A12 REST | `services/k2-api/openapi.yaml` |
| A13 one-shots | compose stubs; `oneshots.schema.json` (added 2026-11-27: the network `k2-egress`, fetch's alone, in `ONESHOT_KINDS`) |
| backup set, storage plan | `backup-set.schema.json`, `storage-plan.schema.json` |

## Release paths

| Constant or helper | Path | Written by |
|---|---|---|
| `MANIFEST`, `PROVENANCE`, `DOCUMENTS_DIR` | `manifest.json`, `provenance.json`, `documents/` | S08 |
| `RELEASE_COMPOSE` | `compose/docker-compose.yml`: the set's `docker-compose.yml`, byte for byte | S08 |
| `ddl_path(engine, w, ns, t)` | `compiled/lake/ddl/<spark or trino>/<w>.<ns>.<t>.sql`: spark for warehouse `k2`, trino for platform tables | S15 |
| `view_sql_path(relation)`, `VIEWS` | `compiled/trino/views/<domain>.<fence>.<table>.sql`, `compiled/trino/views.json` | S15 |
| `CONTRACT_SCHEMAS`, `RETENTION`, `STORAGE_PLAN`, `BACKUP_SET` | `compiled/lake/{contract_schemas,retention,storage-plan,backup-set}.json`; retention has this one source | S15 |
| `pipeline_path(id)`, `DAGS`, `ONESHOTS` | `compiled/products/<id>/pipeline.json`, `compiled/airflow/dags.json`, `compiled/oneshots.json` | S16 |
| (S11, S12, S13) | `compiled/trino/etc/` (with `catalog/` and `password-authenticator.properties`), `compiled/trino/rules.json`, `compiled/trino/groups.txt` | S11, S13; S12 |
| `API_PRINCIPALS` | `compiled/api/principals.json`, `{"principals": [id]}` | S14 |
| `CATALOG_INDEX`, `catalog_path(id)` | `compiled/catalog/index.json`, `compiled/catalog/products/<id>.json` | S18 |
| (S17) | `compiled/dbt/` | S17 |
| `emit/alerts.PATH` (added 2026-11-28, I-5) | `compiled/prometheus/product-alerts.yml`: per scheduled product, `ProductRunFailed_<dag>`, `ProductStale_<dag>` and `ProductRunSlow_<dag>`, read through the prometheus pointer (`docs/development/phase-001-interfaces.md`, "Per-product alerts") | W1-17 |
| `PLATFORM_DIR`, `PLATFORM_CODE` (added 2026-11-27) | `platform/<repository path>`: the set's run code under `docker/airflow/dags/`, `docker/lake/`, `docker/prometheus/rules/`, `domains/`, `platform/runs/` and `platform/catalog-site/` (W3-9), each root's suffixes only (`.py`; `.yml` for the rules; `.sh` and `.json` too under `docker/lake/`: lake-init's `init-lake.sh` and the MinIO policies it grants, run from the release, review R6), never a test, a cache or `conftest.py`; each root is a `DOCUMENT_ROOTS` entry too, so the tag approves the code and `documents/` holds it as well | W1-4 (`release.run_code`) |

`need_of(path)`: `compose/docker-compose.yml` can set `recreate` (the services whose definition
differs), `compiled/trino/etc/` sets `trinoRestart`, `compiled/airflow/dags.json` can set
`dagShapeChange` (when a DAG's task list differs), `compiled/lake/ddl/` adds to `ddl`; everything
else under `compiled/`, `documents/` and `provenance.json` needs nothing, being read through a
pointer. A path no entry covers is a `KeyError`, so a new kind of file is classified first.
Added 2026-11-27: `platform/docker/airflow/dags/` sets `dagShapeChange` on any change, the factory
deciding each DAG's tasks; the rest of `platform/` needs nothing (Spark, the fetch one-shot and
`record` read it at a run's start; deploy sends Prometheus SIGHUP at every swap).

## Names, dispatch and quoting

**Relations by build kind.** `model.reader_relation(t)`, `model.input_relation(t)` and
`model.tested_ref(t)` take a `Table`; nothing else re-derives them.

| Build | A reader queries | Another product's builder reads | Tested ref |
|---|---|---|---|
| `connector` | `lake.<fence>.<table>` | the same | `main` (bronze moves only after its audit) |
| `dbt`, `code` | the view `<domain>.<fence>.<table>` | the base `<domain>.<fence>__base.<table>` | `audited` |
| `platform` | the table `<domain>.<fence>.<table>` | the same | `main` |
| `serving` | its serving copy only (the output port's `serves`) | none | none: every helper raises, every lake emitter and the rules skip it (`LAKE_BUILDS`) |

**dbt names.** One compiled project, `DBT_PROJECT = "k2"`. A table's model is
`model.dbt_model(product, table)` = `<fence>__<table>` with `alias: <table>`; product SQL
(`ref`) and `unit_tests.yml` (`model:`) name it so, while its file stays `models/<table>.sql`.
Unique because a fence holds no `__` and a table name holds none either; S05 checks it all the
same. A seed is `model.dbt_seed(product, seed)` = `<fence>__<stem>` with `alias: <stem>`, a table
of the base namespace like a model's: S05 holds model and seed names unique together, and S07
requires a denied row for `<domain>.<fence>__base.<stem>` as for any base table. The docs link is
`model.dbt_docs` = `dbt-docs/#!/model/model.k2.<fence>__<table>`.

**Quoting and grammar.** Every emitter writes SQL through `quote_ident`, `quote_relation` and
`sql_literal`. The schema refuses first: an identifier is at most 63 characters
(`MAX_IDENTIFIER`) and never a Trino 484 reserved word (`TRINO_RESERVED`, the parser's own
list); a slug at most 40 (`MAX_SLUG`); a table name holds no `__` (`TABLE_NAME`); a
`physicalType` follows `PHYSICAL_TYPE` (`physical_type_problem` adds the decimal scale); a
rowScope string value follows `ROW_SCOPE_VALUE`. The review's five attack strings (`current_user`
as a column, `north') OR (1=1` as a value, `varchar, injected bigint` as a type, `x__base` as a
table, a 300-character name) are each refused, and each test is in `tests/test_interfaces.py`.

**Principals** (A8, ADR-061, the gate's two amendments), all in `model.py`:
`PIPELINE_PRINCIPALS` (resource group `pipeline`; alone may set `STATISTICS_PROPERTY`, by
`SET SESSION` and by the session header alike), `BUILD_CATALOG_PRINCIPALS`, `READER_CATALOG_ALL`
(`pub_.*` holds catalog access `all` on every reader catalog, which Trino's definer-view owner
check demands; the read-only connector still refuses it every write, gate h1.02),
`READER_NAMESPACE_OWNERS` (`pub_.*` alone holds a privilege in a dbt or code product's reader
namespace; `svc.platform` holds none, gate h1.33), `RETENTION_PROPERTIES` for
`RETENTION_PROPERTY_PRINCIPALS`, and `REFS_READERS` (with `svc.api`, which reads a base's
`t$refs` and no other metadata of it; k2-api has no second identity). The release canary is
`canary_user(setSha256)` = `k2_release_<setSha256[:12]>`, the one spelling; it is not a declared
principal.

## Local secrets

The one table, `model.SECRET_FILES`, repeated in the phase page. Every credential lives under
`${K2_SECRETS:-$HOME/k2-secrets}`, outside any checkout, generated on the host and never
committed, compiled or hashed into a release; `manifest.json` has no place for one. A container
mounts only what it reads, read-only, under `/run/k2-secrets/`. A file refreshed while its
reader runs (`password.db`, `keys.json`, the webhook URL) is mounted by its directory and
rewritten in place under an exclusive `flock` on the file, never renamed over: through Docker
Desktop's file sharing a container keeps reading the old file after a rename. S19's canary line
takes the same lock and writes in place.

| Under `${K2_SECRETS}` | Read-only in a container at | Mounted by | Written by |
|---|---|---|---|
| `trino/password.db` | `/run/k2-secrets/trino/password.db` (the directory) | trino | S14 `scripts/principal-secret.py`; S19 adds each release's canary line, in place under the same `flock` |
| `api/keys.json` | `/run/k2-secrets/api/keys.json` (the directory) | k2-api | S14 |
| `principals/<principal>.password` | `/run/k2-secrets/<role>.password` (`model.password_mount`) | dbt-runner, k2-api, tools | S14 |
| `tls/ca.pem` | `/run/k2-secrets/tls/ca.pem` | tools, dbt-runner, k2-api | S13 `scripts/tls.sh` |
| `tls/ca.key` | never mounted | | S13 |
| `tls/trino.pem` | `/run/k2-secrets/tls/trino.pem` | trino | S13 |
| `alertmanager/slack-webhook-url` | `/run/k2-secrets/slack-webhook-url` (the directory) | alertmanager | S09 |
| `canary/<canary>.password` | never mounted | | S19 |

## The stubs, and who fills them

| Stub | Step |
|---|---|
| `model.load` | S04 |
| `docset.load`, `docset.write`, `docset.read` | S04 |
| `checks/docs.py` | S05 |
| `checks/versions.py` | S06 |
| `checks/access.py`, `checks/sql.py` | S07 |
| `release.release`, `release.read`, `diff.diff` | S08 |
| `emit/trino_etc.py` | S11 |
| `emit/trino_rules.py` | S12 |
| `emit/trino_identity.py` | S13 |
| `emit/api.py` | S14 |
| `emit/lake.py` | S15 |
| `emit/pipeline.py` | S16 |
| `emit/dbt.py` | S17 |
| `emit/catalog.py` | S18 |

## Read one way where the proposal allowed two

- Every schema sits in `contracts/schema/`, not `k2gen/schemas/`.
- `grants.yaml`, `principals.yaml`, `roles.yaml` and `expectations.yaml` each have one top-level
  key, as `domains.yaml` has: a bare list of records under a `fixtures/` path fails the data gate
  of `make check-docs`.
- `tests/access/expectations.yaml` is part of the document set, so the coverage check stays pure.
- A table denial is always `denied: true`; `expect:` is for statements, probes and the drift row.
- The catalog JSON and the backup set carry the set's `tag`, and the release canary hashes the
  set: a release id hashes those very files, so none of them can name it.
- No credential or hash field exists in any document or release file.
- The compose file is a document of the set, taken from the tag, so a release deploys the
  compose its tag approved; `k2gen release --compose` defaults to it.
- Bronze: the pipeline's `patterns` come from the contract (`Column.pattern`); the lineage column
  is the constant `LINEAGE_COLUMN`, so the pipeline has no `lineage` field.
- Retention is the contract's, emitted once in `compiled/lake/retention.json`; the pipeline's
  `maintain` has no `retainDays`.
- A serving-only output (the paused hot tier) is `build: serving` with a `serves` port; its
  catalog entry has `relation: null`.
- Golden files are `tests/golden/<emitter>/<release path>`, and the repository's own products'
  under `tests/golden/<emitter>/repository/`; Trino's `groups.txt` is kept as
  `tests/golden/trino_rules/groups.yaml`, the parsed map.
- An output port's `inputContracts` may name a sibling table (bronze feeding silver): it is the
  catalog's lineage edge, not an input. The access check follows a sibling to what it derives from.
- A Trino reserved word is refused by `model.TRINO_RESERVED` alone; `RESERVED_DOMAIN_NAMES` holds
  only the platform's own names (`catalog`, `default` and `user` are not reserved in Trino 484).

## Patched by S00d, additively

No field was renamed or removed and no signature changed; each addition has a default, so a merged
module keeps working. `git diff` of `contracts/schema/` adds properties and changes descriptions only.

| Gap the building steps reported | The addition | Read by |
|---|---|---|
| ODCS quality rules beyond custom dbt | `Quality.query`, `.metric`, `.arguments`, `.unit`, `.thresholds`; `quality_of`; `QUALITY_OPERATORS`; `emit/dbt.py` `quality_test`, the `k2_quality` generic test; `docs.quality-unsupported` | S05, S17, S18 |
| A unit test of an incremental model states its branch | the good set's override; `docs.unit-test-branch-unstated` | S05, S17 |
| A foreign key into a table the product does not read | `docs.foreign-key-not-read`, before dbt sees it | S05 |
| Seed names and their denial | `dbt_seed`; S07's seed denial; the canary's rows | S07, S17, S20 |
| A canary that built no rows passes every read row | `mustAppear`, `MustAppear`; S07 requires one per role with a policy | S07, S20 |
| A finding's location | `Finding.pointer` | every check, `k2gen check` |
| The catalog page and REST | per product `keywords`, `issued`, `periodicity`, `productFile`, `support`, `links`, `inputs[].version`; per output `build`, `testedRef`, `contractFile`, `partitioning`, `retentionDays`, `sla`, `quality`; per role `approvers`; per column `logicalType`, `primaryKeyPosition`, `unique`, `classification` | S18, phase 003 |
| `config()` with `~`, or a hook holding Jinja | `model_config`'s quote-aware scan, `ValueError` only | S05, S07, S17 |

## Changed by the reviews of S12, S15 and S13, after S00d

Each change is in code and tested; none renamed a frozen name.

| Change | Where | Why |
|---|---|---|
| **The guarded view shape.** A reader view's SQL is `SELECT <contract columns> FROM <base> FOR VERSION AS OF 'audited' WHERE EXISTS (SELECT 1 FROM <base>$refs WHERE "name" = 'audited' AND "type" = 'TAG')`, on one line, every name quoted | `emit/lake.view_sql` | the builder owns every name in `<fence>__base` and could swap its base for a view, which Trino 484 reads with the string version ignored; `$refs` exists only for an Iceberg table, so the read fails ([ADR-061](../../docs/adr/ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md) Outcome) |
| **`view_update(view, old, new)`** takes name-to-type maps (each type as Trino's `DESCRIBE` spells it; `old` is None before the first tag) and returns the statements before and after the tag commit; a column is common only when name and type agree, and a published view with nothing in common raises `lake.NoCommonColumns` | `emit/lake.py` | a view stores its columns' types, so a retype is a drop and an add |
| **The publisher's `$refs` grant.** `pub_<fence>` holds `SELECT, GRANT_SELECT` on exactly `<table>` and `<table>$refs` of its own bases, through the reader catalog | `emit/trino_rules.py` | Trino's definer-view owner check asks `GRANT_SELECT` of every relation the view reads, the guard's included ([ADR-059](../../docs/adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md) Outcome) |
| **`password.db` in fixed slots.** One `<user>:<bcrypt>` line per principal in a 128-byte slot padded with spaces, 32 slots to a 4 KiB page; every change is one aligned write of one slot in place under the file's `flock`; a file not yet in slots is rewritten into them once | `scripts/principal-secret.py` (`SLOT`); S19's canary line the same way | a reader racing a write sees each line old or new, never torn |
| **The Web UI is off.** `web-ui.enabled=false` in the compiled `config.properties` | `emit/trino_etc.py` | its form login gave a session that outlived a removed password ([ADR-060](../../docs/adr/ADR-060-trino-logins-by-password-over-tls-under-a-local-ca.md) Outcome) |
