# k2gen interfaces, frozen for phase 001

What steps S04 to S18 build against, so that each fills one module and none edits a shared
file. The design is the phase 001 interfaces proposal, sections 1 and 2 (A1 to A13), under
[`DECISIONS-FINAL.md`](../../docs/research/2026-11-26-unified-platform-architecture/DECISIONS-FINAL.md),
[ADR-055](../../docs/adr/ADR-055-products-defined-once-in-odps-and-odcs-documents.md) and
[ADR-056](../../docs/adr/ADR-056-unified-data-product-platform.md).

**The rule.** A schema in `contracts/schema/`, or a type, constant or helper in
`k2gen/model.py`, changes only through the orchestrator, who tells every dependent step. A
step fills the bodies of its own module, adds failing fixtures only under
`tests/fixtures/v2/bad/<its check>/`, and never edits `cli.py`, `checks/__init__.py`,
`emit/__init__.py` or the good set. `make test-k2gen` holds the rule: `tests/test_interfaces.py`
fails if a module goes missing or a signature drifts.

## Where each interface is frozen

| Item | Frozen in |
|---|---|
| A1 release and manifest | `contracts/schema/manifest.schema.json` (`$defs/images` is also the `--images` input); `model.py`: `Manifest`, `Needs`, `Release`, `release_id`, `MANIFEST`, `PROVENANCE`, `DOCUMENTS_DIR`; `release.py`. Pointers, mounts and the deploy log are compose's and deploy's (S00b, S19) |
| A2 documents and provenance | `odps-json-schema-v1.1.0.json` (vendored), the ODCS file, `k2-v2.schema.json` (`product`, `contract`, every custom property); `provenance.schema.json`; `model.py`: `DocumentSet`, `Provenance`, `set_sha256`, `DOCUMENT_ROOTS`, `document_kind`, `schema_errors`; `docset.py` |
| A3 compiler and checks | `emit/__init__.py` and one module per output; `checks/__init__.py` and one module per check; `model.Finding`; `cli.py`: `docset`, `check`, `release`, `diff`; `diff.py` |
| A4 connector configuration | `pipeline.schema.json`; `k2-v2.schema.json#/$defs/connector`, one definition per kind |
| A5 landing and bronze | `model.py`: `LANDING_BUCKET`, `landing_key`, `landing_prefix`, `lake_relation`, `LANDING_FILES`, `AUDIT_CHECKS`, `AUDIT_BRANCH`, `SNAPSHOT_SUMMARY_KEYS`, `RESERVED_*`, `product_name_problem`, `domain_name_problem` |
| A6 run records | `run-record.schema.json` (one row); the DDL is `platform/runs/run_record.sql` (S00b) |
| A7 `k2_input` and the wrapper | `pipeline.schema.json` `build`; `model.View` and `model.VIEWS`; the macro is `dbt/macros/k2_input.sql` (S00b), the wrapper phase 002's |
| A8 names | `model.py`: `fence`, the catalog, namespace, builder, publisher, group, DAG and relation helpers, `PIPELINE_PRINCIPALS`, `BUILD_CATALOG_PRINCIPALS`, `REFS_READERS`, `canary_user` |
| A9 access expectations | `expectations.schema.json`; `model.py`: the five expectation types |
| A10 roles, principals, grants, policies | `k2-v2.schema.json#/$defs/{roles,principals,grants,policies,rolePolicy}`; `model.py`: `RoleDef`, `Principal`, `Grant`, `RolePolicy` |
| A11 catalog JSON | `catalog-product.schema.json`, and its `$defs/index` for `index.json` |
| A12 REST | `services/k2-api/openapi.yaml` (S00b) |
| A13 one-shots | compose stubs (S00b); `oneshots.json` is S16's, no schema frozen |
| backup set | `backup-set.schema.json` |

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

Each emitter is `emit(model: Model, previous: Release | None) -> dict[str, str]`, release-relative
path to text, pure. Each check is `check(docs: DocumentSet, previous: DocumentSet | None) ->
list[Finding]`, with `Finding.code` spelt `<check module>.<kebab-case>`. Both are found by
`pkgutil.iter_modules`; `checks.run` runs `docs` first and stops there on an error.
`k2gen check --previous` takes a document set (a release's `documents/` is one); `k2gen release
--previous` takes a release directory.

## Provisional until S01

Marked `provisional until S01` in the code, finalised in one follow-up when the gate
`spine-h-tested-data-view` reports: `AUDITED_TAG`, `BASE_SUFFIX`, `VIEWS`, `View`, the reader,
base, build and view relation helpers, `publisher`, the three principal pattern lists, and
`pipeline.schema.json`'s `build.publisher`.

## Read one way where the proposal allowed two

- Every schema sits in `contracts/schema/`, as this step's brief says, not `k2gen/schemas/`.
- `grants.yaml`, `principals.yaml`, `roles.yaml` and `expectations.yaml` each have one top-level
  key, as `domains.yaml` has: a bare list of records under a `fixtures/` path fails the data gate
  of `make check-docs`.
- `tests/access/expectations.yaml` is part of the document set, so the coverage check stays pure.
- A table denial is always `denied: true`; `expect:` is for statements, probes and the drift row.
- The catalog JSON and the backup set carry the set's `tag`, and the release canary hashes the
  set: a release id hashes those very files, so none of them can name it.
- No credential or hash field exists anywhere; the Trino password file and key hashes live under
  `${K2_SECRETS}`, so `trino_identity` emits only the authenticator properties.
