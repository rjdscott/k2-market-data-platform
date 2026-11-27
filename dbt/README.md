# dbt

The dbt project on Trino, each model in its domain's own catalog
([ADR-035](../docs/adr/ADR-035-trino-and-dbt-for-marts.md),
[ADR-044](../docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)).

**No model today.** The old marts, their tests and their schedule retired with the old lake
ingest to [`legacy/v3-lake-ingest/dbt/`](../legacy/v3-lake-ingest/dbt/README.md)
([ADR-056](../docs/adr/ADR-056-unified-data-product-platform.md)). This project is where the
new platform's models land, from
[phase 002](../docs/plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md)
of the plan; until then it holds the configuration, the macros and the pins.

| Path | What it is |
|---|---|
| `dbt_project.yml` | a model is a view unless configured otherwise, a table is rebuilt with `CREATE OR REPLACE`, and the `on-run-end` hook |
| `profiles.yml` | one target, `trino`, logged in with a password over HTTPS (`method: ldap`, dbt-trino's name for it) and verified against the platform's CA ([ADR-060](../docs/adr/ADR-060-trino-logins-by-password-over-tls-under-a-local-ca.md)): host, port and user from `K2_TRINO_HOST`, `K2_TRINO_INTERNAL_PORT` (default 8443) and `K2_TRINO_USER`, the password from `DBT_ENV_SECRET_K2_TRINO_PASSWORD` (scrubbed from dbt's logs), the CA from `K2_TRINO_CA` (default `/run/k2-secrets/tls/ca.pem`). The live Trino serves exactly this since the release-shape compose file of 2026-11-27; no dbt run reaches it until phase 002 brings a model, so today the profile reaches the throwaway Trino of `scripts/verify/identity.sh` |
| `macros/naming.sql` | a model's folder decides its catalog and schema (below) |
| `macros/record_test_results.sql` | the hook: every test an invocation ran, one row each, into the writer's `<catalog>.dq.dbt_test_results` ([ADR-046](../docs/adr/ADR-046-governance-domain-and-quality-results.md)) |
| `_sources.yml`, `selectors.yml` | not in this tree: k2gen compiles them, with the products' models, into each release's `compiled/dbt/` (`platform/k2gen/k2gen/emit/dbt.py`); `make dbt-dev` compiles the working tree's into `~/k2-dbt-dev`, and `make test-dbt-tree` parses and compiles that tree |
| `packages.yml`, `package-lock.yml` | `metaplane/dbt_expectations` 0.10.10 and `godatadriven/dbt_date` 0.21.0 |
| `pyproject.toml`, `uv.lock` | `dbt-core` 1.12.7 and `dbt-trino` 1.12.0; `sqlfluff` 4.5.0 in the dev group. dbt-trino's README says it is tested on Trino 478; the old marts ran on this stack's 484 |

## Where a model lives

| File | Becomes |
|---|---|
| `models/<catalog>/staging/<model>.sql` | view `<catalog>.staging.<model>`, the only models that may select from a source |
| `models/<catalog>/intermediate/<model>.sql` | view `<catalog>.intermediate.<model>` |
| `models/<catalog>/marts/<product>/<model>.sql` | `<catalog>.<product>.<model>`, one schema per data product |

`<catalog>` is the domain id with underscores, `<product>` the product name with underscores.
`scripts/dbt_manifest_check.py` holds the compiled project to that and to the marts the
contracts declare.

## Check it

```bash
make test-dbt      # lint, deps, parse, compile, the manifest check; no stack, no Trino
make test-airflow  # the Airflow image's dbt and packages are the ones uv.lock and package-lock.yml pin
```

`make test-dbt` (`scripts/dbt-test.sh`, CI job "dbt (marts)") prints a SKIP line for the
build with unit tests on a throwaway Trino, which it cannot run with no model; phase 002
restores it. By hand, from this directory:

```bash
uv sync --locked
uv run --locked dbt deps
uv run --locked dbt parse
```
