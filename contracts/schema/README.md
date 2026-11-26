# Contract schemas

Two sets. The old format's two files are read by `k2gen validate` until step S23 of phase 001
deletes `k2.schema.json` and that validator, which skips any product directory holding a v2
`product.odps.yaml`. The v2 files are frozen by the interfaces steps (S00a, S00c) and
read by `k2gen.model.schema_errors` (`platform/k2gen/k2gen/model.py`, whose `SCHEMAS` names the
schemas of each kind); they change only through the orchestrator
([`platform/k2gen/INTERFACES.md`](../../platform/k2gen/INTERFACES.md)).

| File | What it validates |
|---|---|
| `odcs-json-schema-v3.2.0-20261117.json` | every `*.odcs.yaml`, as the Open Data Contract Standard defines it (both formats) |
| `k2.schema.json` | old format: `domains.yaml`, every `product.yaml`, K2's profile of an ODCS contract, and the K2 custom properties |
| `odps-json-schema-v1.1.0.json` | v2: every `product.odps.yaml`, as the Open Data Product Standard defines it |
| `k2-v2.schema.json` | v2: K2's profile of each product file and table contract with every K2 custom property; `domains.yaml`, `grants.yaml`, `contracts/access/roles.yaml` and `principals.yaml` |
| `expectations.schema.json` | v2: `tests/access/expectations.yaml` (A9) |
| `manifest.schema.json` | v2: a release's `manifest.json`, and `k2gen release --images` (A1) |
| `provenance.schema.json` | v2: a document set's `provenance.json` (A2) |
| `pipeline.schema.json` | v2: `compiled/products/<id>/pipeline.json` (A4) |
| `catalog-product.schema.json` | v2: `compiled/catalog/products/<id>.json` and `index.json` (A11) |
| `backup-set.schema.json` | v2: `compiled/lake/backup-set.json` |
| `run-record.schema.json` | v2: one row of the run-record table (A6) |
| `storage-plan.schema.json` | v2: `compiled/lake/storage-plan.json`, the warehouses, namespaces and landing prefixes |
| `dags.schema.json` | v2: `compiled/airflow/dags.json`, the DAGs and their tasks |
| `oneshots.schema.json` | v2: `compiled/oneshots.json`, how each one-shot kind runs |
| `deploy-record.schema.json` | v2: one line of the releases root's `deploy-log.jsonl`, the only place a secret file's checksum is recorded |

The K2 files share `$id`s under `https://k2.invalid/schema/`, a name and not a location (the
`.invalid` top-level domain never resolves); their `$ref`s between files are relative file names.

## The vendored ODPS schema

- Standard: Open Data Product Standard, version **v1.1.0** (tag `v1.1.0`, released 2026-09-08).
- File: `schema/odps-json-schema-v1.1.0.json` of <https://github.com/bitol-io/open-data-product-standard>,
  at commit `04995037fc003ead1da27f65a40a09eb0664b591`, the commit the `v1.1.0` tag names,
  fetched 2026-11-26 from
  <https://raw.githubusercontent.com/bitol-io/open-data-product-standard/04995037fc003ead1da27f65a40a09eb0664b591/schema/odps-json-schema-v1.1.0.json>.
  The repository's `odps-json-schema-latest.json` is the same blob at that commit.
- sha256: `b7eae1af15b3c6b18e5dffaa2119212acb38ec73286933c76d16c8712e0d90d1`.
- Licence: Apache License 2.0, copyright The Bitol Contributors. Vendored unmodified (`NOTICE`).
- K2's one deviation, declared in `k2-v2.schema.json`: `status` takes `paused`, which the
  standard's examples do not list; the standard types `status` as any string, so its own schema
  accepts it.

Moving to a newer version is a pull request that adds the new file, points `ODPS_SCHEMA` in
`platform/k2gen/k2gen/model.py` at it, updates this section and passes `make test-k2gen`.

## The vendored ODCS schema

- Standard: Open Data Contract Standard, version **v3.2.0** (released 2026-09-08).
- File: the dated snapshot `odcs-json-schema-v3.2.0-20261117.json`, which the standard's
  own versioning policy describes as frozen. The rolling `odcs-json-schema-v3.2.0.json`
  receives silent fixes and is not byte-stable, so it is not the one vendored.
- Source: <https://github.com/bitol-io/open-data-contract-standard/blob/8c178cd6141e143fb0eecc58e2239d7f56e9626e/schema/odcs-json-schema-v3.2.0-20261117.json>,
  commit `8c178cd6141e143fb0eecc58e2239d7f56e9626e` on the `dev` branch, fetched 2026-11-25.
  At that commit the rolling v3.2.0 file is byte-identical to this snapshot.
- sha256: `16ebaf8e8e295ed82044d11305396dbf8e09f04ce2fd3f57a8fe2d54becba601`.
- Licence: Apache License 2.0, copyright The Bitol Contributors. Vendored unmodified (`NOTICE`).

Moving to a newer snapshot is a pull request that replaces the file, updates this section and
`ODCS_SCHEMA` in both `platform/k2gen/k2gen/model.py` (the v2 path) and
`platform/k2gen/k2gen/cli.py` (the old validator, until S23), and passes `make test-k2gen`.

## Where each K2 field lives

| K2 field | Place in the contract |
|---|---|
| `name`, `domain`, `version` | ODCS `name`, `domain`, `version` (semver, enforced by `k2.schema.json`) |
| `owner` | ODCS `team.name`; equal to the domain's owner in `domains.yaml` |
| `description` | ODCS `description.purpose` |
| `table` | ODCS `schema[0].physicalName`, the Iceberg identifier without the `lake.` catalog |
| `schema` | inline columns in `schema[0].properties` (`name`, `physicalType` as Spark SQL spells it, `description`, `required`), from which `k2gen generate` writes `docker/lake/ddl/generated/<table>.sql`. The other form, ODCS `schema[0].authoritativeDefinitions` of type `implementation` pointing at `docker/lake/ddl/lake.sql`, named a table that DDL created; k2gen refuses it since the DDL retired to `legacy/v3-lake-ingest/` ([ADR-056](../../docs/adr/ADR-056-unified-data-product-platform.md)) |
| `quality` | ODCS `schema[0].quality`, type `custom`, engine `k2-maintenance`, the audit's check name |
| `slo` | ODCS `slaProperties`, only where a check enforces a stated target: an alert, or `dbt source freshness` for a `latency` on a table a mart reads (its `element`, `<table>.<column>`, is the freshness column) |
| `layer`, `format`, `partitioning`, `sort`, `classification`, `entitlement`, `retention`, `upstreams`, `schedule` | ODCS `customProperties`, validated by `k2.schema.json#/$defs/customProperties` |

`retention` is a custom property and not the standard's `retention` SLA property on purpose:
the standard's means how long data is kept, and K2's means how long snapshots are kept before
expiry. No row is deleted ([ADR-032](../../docs/adr/ADR-032-odcs-contracts-and-k2gen.md)).

## Where each K2 v2 property lives

Every K2 extension is one `{property, value}` item of a standard `customProperties` list,
defined once in `k2-v2.schema.json`; an unknown property name fails. No standard field fits
any of them: ODPS 1.1.0 and ODCS 3.2.0 have no connector, schedule, terms, lifecycle reason,
serving copy, input requirement, build kind, access policy, backup mode, unit or time meaning.

| Property | Where | `$defs` |
|---|---|---|
| `connector`, `schedule`, `terms`, `lifecycleChange`, `examples`, `seeds` | product file `customProperties` | `productProperty` |
| `required`, `maxLagMinutes` | product file `inputPorts[].customProperties` | `inputPortProperty` |
| `serves` | product file `outputPorts[].customProperties` | `outputPortProperty` |
| `layer`, `build`, `backup`, `retention`, `partitioning`, `sort`, `policies` | table contract `customProperties`; `layer`, `build` and `backup` required | `contractProperty` |
| `unit`, `time` | a column's `customProperties` | `columnProperty` |
| `asOf` | a column relationship's `customProperties` | `relationshipProperty` |

The old contract's `format`, `classification`, `entitlement`, `upstreams` and `schedule` are
gone from the v2 table contract: every table is Iceberg (or, for `build: serving`, the serving
engine's); access is `policies` with grants; lineage is the product's ports; the schedule is the
product's. A string pattern a bronze audit checks is the standard's own
`logicalTypeOptions.pattern`, not a K2 property.
