# Phase P1: data contracts, the generator and the mesh domains

**Depends on:** P0
**Decision it implements:** ADR-032 (contracts and generator) and ADR-033 (data mesh), both written in this phase. ADR-032 lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §11 and appends an Outcome there.
**Delivers:** one YAML contract per existing lake product, a JSON Schema that validates them, the `k2gen` package that turns contracts into committed artefacts, and the four domains declared.
**Exit:** the drift check is clean and runs in CI; `make test` includes the generator tests; a scaffolded contract validates; every table in `docker/lake/ddl/lake.sql` outside `audit` has exactly one contract, asserted by a test.

## Scope

**Pull request 1: contracts, schema, `k2gen validate`, ADR-032, ADR-033**

- `contracts/<domain>/<product>.yaml`. Fields: `name`, `domain`, `owner`, `description`,
  `version` (semver), `layer`, `table`, `schema` (a reference to the `lake.sql` table or an
  Avro file, or inline columns for a new product), `format`, `partitioning`, `sort`,
  `classification` (`public`, `internal`, `restricted`), `entitlement` (`realtime`,
  `delayed`, `none`), `retention` (snapshot expiry, in days), `slo` (`freshness`,
  `completeness`, `latency`), `quality` rules, `upstreams`, `schedule`.
- Domains, each with a named owning team:
  - `market-capture` (source-aligned): `raw.messages`, the seven `bronze.*` tables,
    `silver.trades_*`, `silver.book_*`, `gold.book_state`.
  - `reference-data` (source-aligned): `gold.dim_instrument`, `gold.dim_venue`.
    `config/instruments.yaml` is named as their upstream, not given a contract of its own.
  - `fx-reference` (source-aligned): declared here, built in P3.
  - `market-analytics` (consumer-aligned): `gold.trades`, `gold.ohlcv_*`, `gold.bars`,
    `gold.bbo_1s`, `gold.book_top20`; the dbt marts are added in P3.
- `contracts/schema/contract.schema.json`.
- `platform/k2gen/`: its own `pyproject.toml` and `uv.lock`, following `notebooks/`. No
  `platform/__init__.py`. The DDL parser is one standard-library-only module; the two copies
  in `tests/` are replaced by an import of it, with the path added in `tests/conftest.py`.
- `k2gen validate`: JSON Schema; each contract against `lake.sql` (table exists,
  partitioning and sort match); and one cross-contract rule, that a table named in another
  contract's `upstreams` keeps snapshots for at least the seven-day floor
  (`maintenance.py:163-166`).
- ADR-032 states that ADR-028 §12 stays in force: `retention` here means snapshot expiry, not
  deletion of data.

**Pull request 2: `k2gen generate`, `k2gen new`, CI**

- The generator clears its own outputs, then writes with sorted keys and stable ordering, so
  two runs are byte-identical and a deleted contract removes its outputs.
- Output map, fixed here so every later emitter has a home:

  | Output | Path | Added in |
  |---|---|---|
  | Iceberg DDL for products with inline columns | `docker/lake/ddl/generated/` | P1 |
  | Retention and snapshot expiry | `docker/lake/generated/retention.json` | P1 |
  | Airflow DAG config for new products | `docker/airflow/dags/generated/` | P3 |
  | dbt sources and tests | `dbt/models/sources/` | P3 |
  | Trino access rules | `docker/trino/etc/rules.json` | P4 |
  | Catalog payloads | `generated/openmetadata/` | P4 |
  | SLO rules and their tests | `docker/prometheus/rules/` and `rules/tests/` | P5 |

- `docker/lake/apply_ddl.py` applies `lake.sql`, then `ddl/generated/`. Existing tables keep
  `lake.sql` as their DDL of record; the generator never rewrites it.
- `make generate`, `make new-product DOMAIN=x NAME=y`, `make test-k2gen` (added to
  `make test`). `make lint` and the CI ruff step gain `platform/k2gen`.
- CI job `contracts`: `make generate && test -z "$(git status --porcelain)"`, which also
  catches a generated file that was never committed.
- Dependabot entry for `platform/k2gen`.

**Documents.** `docs/architecture/18-data-mesh.md`, with `docs/architecture/README.md`,
`00-start-here.md` and `docs/README.md` updated in the same pull request.
`docs/development/golden-path.md` started; finished in P6 by following it from zero.

## Verification

```bash
make test-k2gen
make generate && test -z "$(git status --porcelain)"
make new-product DOMAIN=market-analytics NAME=example
uv run --project platform/k2gen k2gen validate
git clean -fd contracts/market-analytics/example.yaml
make lint && make gates
```

Unit tests cover: a contract with a wrong partition spec fails; a parent with expiry under
the floor fails; a table with no contract fails; a second `generate` changes nothing.
