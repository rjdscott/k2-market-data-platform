# Phase P1: data contracts, the generator and the mesh domains

**Depends on:** P0
**Decision it implements:** ADR-032 (contracts and generator) and ADR-033 (data mesh), both written in this phase. ADR-032 lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §11 and appends an Outcome there.
**Delivers:** a descriptor and Open Data Contract Standard v3 contracts for every existing lake product, the `k2gen` package that validates them and turns them into committed artefacts, a generated page per product, and the four domains declared with owners.
**Exit:** the drift check is clean and runs in CI; `make test` includes the generator tests; a scaffolded contract validates; every table in `docker/lake/ddl/lake.sql` outside `audit` has exactly one contract, asserted by a test.

## Scope

**Pull request 1: contracts, schema, `k2gen validate`, ADR-032, ADR-033**

- Layout: `contracts/<domain>/<product>/product.yaml` and one `<output>.odcs.yaml` beside it
  per output table.
- `product.yaml`, the descriptor: `name`, `domain`, `owner`, `purpose`, `kind`
  (source-aligned or consumer-aligned), `lifecycle` (`draft`, `published`, `deprecated`),
  `inputs` (other products' outputs, or an external source), `outputs`, known `consumers`.
- Each output contract follows ODCS v3: `schema`, `quality`, `slaProperties`, `team`,
  `servers` (the access points, filled in P5), and K2-only fields as `customProperties`.
  Validated with the standard's published JSON Schema, plus a small K2 schema for the custom
  properties.
- Fields carried, wherever the standard places them: `name`, `domain`, `owner`, `description`,
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
  - `fx-reference` (source-aligned): declared here, built in P4.
  - `market-analytics` (consumer-aligned): `gold.trades`, `gold.ohlcv_*`, `gold.bars`,
    `gold.bbo_1s`, `gold.book_top20`; the dbt marts are added in P3 and the FX product in P4.
- `contracts/schema/`: the vendored ODCS v3 JSON Schema with its version pinned, and
  `k2.schema.json` for the descriptor and the custom properties.
- `.github/CODEOWNERS`: one entry per `contracts/<domain>/`, so a change to a product is
  reviewed by the team that owns it and a change to the generator by the platform team.
- `platform/k2gen/`: its own `pyproject.toml` and `uv.lock`, following `notebooks/`. No
  `platform/__init__.py`. The DDL parser is one standard-library-only module; the two copies
  in `tests/` are replaced by an import of it, with the path added in `tests/conftest.py`.
- `k2gen validate`: JSON Schema; each contract against `lake.sql` (table exists,
  partitioning and sort match); and three cross-product rules: a table named in another
  product's `inputs` keeps snapshots for at least the seven-day floor
  (`maintenance.py:163-166`); a product is at least as restricted as its strictest input;
  and every input resolves to a declared output or a declared external source.
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
  | A page per product | `docs/products/<domain>/<product>.md` | P1, enriched in P5 and P6 |
  | Airflow DAG config for new products | `docker/airflow/dags/generated/` | P4 |
  | dbt sources and tests | `dbt/models/sources/` | P3 |
  | Trino access rules | `docker/trino/etc/rules.json` | P5 |
  | Access-point checks | `generated/access-points.json` | P5 |
  | Catalog payloads | `generated/openmetadata/` | P5 |
  | SLO rules and their tests | `docker/prometheus/rules/` and `rules/tests/` | P6 |

- `docker/lake/apply_ddl.py` applies `lake.sql`, then `ddl/generated/`. Existing tables keep
  `lake.sql` as their DDL of record; the generator never rewrites it.
- `make generate`, `make new-product DOMAIN=x NAME=y`, `make test-k2gen` (added to
  `make test`). `make lint` and the CI ruff step gain `platform/k2gen`.
- CI job `contracts`: `make generate && test -z "$(git status --porcelain)"`, which also
  catches a generated file that was never committed.
- Dependabot entry for `platform/k2gen`.
- The generator is typed: a type checker runs beside ruff in `make lint` and CI.
- Product pages: owner, purpose, lifecycle, schema with column descriptions, inputs and
  outputs as a Mermaid diagram, classification, retention. `docs/products/README.md` is the
  generated index by domain. Docs gate (a) keeps every link on them honest.

**Documents.** `docs/architecture/18-data-mesh.md`, with `docs/architecture/README.md`,
`00-start-here.md` and `docs/README.md` updated in the same pull request.
`docs/development/golden-path.md` started; finished in P7 by following it from zero.

## Verification

```bash
make test-k2gen
make generate && test -z "$(git status --porcelain)"
make new-product DOMAIN=market-analytics NAME=example
uv run --project platform/k2gen k2gen validate
rm -r contracts/market-analytics/example
make lint && make gates
```

Unit tests cover: a contract with a wrong partition spec fails; a parent with expiry under
the floor fails; a table with no contract fails; a second `generate` changes nothing.
