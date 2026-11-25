# Contract schemas

Two JSON Schemas, both read by `k2gen validate` (`platform/k2gen/`).

| File | What it validates |
|---|---|
| `odcs-json-schema-v3.2.0-20261117.json` | every `*.odcs.yaml`, as the Open Data Contract Standard defines it |
| `k2.schema.json` | `domains.yaml`, every `product.yaml`, K2's profile of an ODCS contract, and the K2 custom properties |

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
`ODCS_SCHEMA` in `platform/k2gen/k2gen/cli.py`, and passes `k2gen validate`.

## Where each K2 field lives

| K2 field | Place in the contract |
|---|---|
| `name`, `domain`, `version` | ODCS `name`, `domain`, `version` (semver, enforced by `k2.schema.json`) |
| `owner` | ODCS `team.name`; equal to the domain's owner in `domains.yaml` |
| `description` | ODCS `description.purpose` |
| `table` | ODCS `schema[0].physicalName`, the Iceberg identifier without the `lake.` catalog |
| `schema` | for a table `lake.sql` creates, ODCS `schema[0].authoritativeDefinitions`, type `implementation`, url `docker/lake/ddl/lake.sql`; for a new table, inline columns in `schema[0].properties` (`name`, `physicalType` as Spark SQL spells it, `description`, `required`), from which `k2gen generate` writes `docker/lake/ddl/generated/<table>.sql` |
| `quality` | ODCS `schema[0].quality`, type `custom`, engine `k2-maintenance`, the audit's check name |
| `slo` | ODCS `slaProperties`, only where a check enforces a stated target: an alert, or `dbt source freshness` for a `latency` on a table a mart reads (its `element`, `<table>.<column>`, is the freshness column) |
| `layer`, `format`, `partitioning`, `sort`, `classification`, `entitlement`, `retention`, `upstreams`, `schedule` | ODCS `customProperties`, validated by `k2.schema.json#/$defs/customProperties` |

`retention` is a custom property and not the standard's `retention` SLA property on purpose:
the standard's means how long data is kept, and K2's means how long snapshots are kept before
expiry. No row is deleted ([ADR-032](../../docs/adr/ADR-032-odcs-contracts-and-k2gen.md)).
