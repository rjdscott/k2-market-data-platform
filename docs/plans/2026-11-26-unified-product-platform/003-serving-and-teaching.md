# Phase 003: serving and teaching

**Depends on:** `002` (tag `v3.0.0-alpha.4`: the ECB products published, run records, audited
snapshots, the key map in the release).
**Decision it implements:** `docs/adr/ADR-056-unified-data-product-platform.md`: one new service,
`k2-api`, serves the product catalog and the data routes, and queries Trino as the caller;
the owner's decisions on the catalog site and on a REST data route in the ECB slice.
**Delivers:** `k2-api` with the metadata routes, the data route and API keys; the catalog site's
first version and the dbt docs site, both served by `k2-api`; dbt labs 1 to 3; the owner's guide
to reviewing dbt work; the runbook for onboarding a file product.
**Exit:** every ECB expectation gives the same answer through SQL and REST; a data request with
no key is refused with 401 and a denied table with 403; the product documents validate against
the schema frozen in the interfaces pull request; `/v1/openapi.json` declares OpenAPI 3.1; a
catalog page exists for every product in the release and links to the dbt docs; every part,
`k2-api` included, reports the release; tag `v3.0.0-alpha.5`.

## Scope

The steps are in section B of
`docs/research/2026-11-26-unified-platform-architecture/IMPLEMENTATION-N0-N1.md`; the routes and
the catalog JSON are the ones frozen in `001`.

**`k2-api`** (FastAPI 0.162.5, uvicorn 0.54.0, `trino` 0.340.0, on `127.0.0.1:8095`, 0.5 CPU and
512 MiB):

- `GET /v1/products`; `GET /v1/products/{domain}/{product}`, the product's catalog document plus
  the caller's access, asked of Trino with `LIMIT 0` per output as the caller and omitted when
  anonymous; `GET /v1/products/{domain}/{product}/contracts/{table}.odcs.yaml`; `GET
  /v1/openapi.json`; `GET /v1/release`; `GET /healthz`.
- `GET /v1/data/{domain}/{product}/{table}` with `fields`, `limit` up to 10,000 and equality
  filters on declared columns only, read `FOR VERSION AS OF` the newest audited snapshot, as the
  key's principal. The cursor, `as_of` and the state headers are phase `007`'s, against the same
  frozen OpenAPI.
- Keys: `Authorization: Bearer <key>`, mapped by sha256 through the release's key map. Metadata
  routes need no key; data routes refuse with 401 without one, 403 when Trino denies, 404 for a
  table not in the release.
- What it must not do: write, accept SQL, evaluate a rule, read storage or Lakekeeper, cache
  rows or own metadata. Trino is the one enforcement point for SQL and REST alike.
- `make release-verify` gains `k2-api`, read from `/v1/release`.
- `make access-verify` runs every ECB expectation row through REST as well as SQL.

**The catalog site, first version** (owner decision: a generated, read-only static site, no
off-the-shelf catalog tool). One generator renders it from the catalog JSON in the release and
the run-time facts `k2-api` reads from governance as `svc.catalog` (newest audited publish,
tests, freshness). It reruns after each deploy and on a schedule, and `k2-api` serves it at
`/catalog/` (F17). A page per product: owner, purpose, state, schema with column descriptions,
keys, roles and their policies in words, terms, example queries, and a link to the product's
models on the dbt docs site at `/dbt-docs/`. Search, the lineage diagram and quality history are
added to the same generator in `006`; no credential ever appears on a page.

**Teaching** (owner decision on dbt):

- dbt labs 1 to 3 under `docs/development/dbt-labs/`, on the ECB products: a source and a model;
  an incremental merge with a unit test; a failing test that rolls back.
- The owner's guide to reviewing dbt work: what to read in a model, its YAML and its tests; what
  the validation runner's `k2gen diff` shows; the "What this does and why" section every dbt pull
  request carries.
- Runbook: onboard a file product, written last, from a run of the whole path on the live stack.
- Current-state documents (`README.md`, `docs/operations/`) updated to say what is real.

**Budget:** `k2-api` adds 0.5 CPU and 512 MiB; the bootstrap peak with the `fetch` and `build`
profiles becomes 19.40 CPU / 32.375 GiB, under the 25 / 48 ceiling.

## Verification

```bash
make deploy RELEASE=v3.0.0-alpha.5 && make release-verify      # k2-api reports the release
make access-verify                                              # every ECB row through SQL and REST, equal
KEY='<the key make api-key printed for p.analyst-a>'
curl -s -H "Authorization: Bearer $KEY" \
  '127.0.0.1:8095/v1/data/fx-reference/ecb-fx-rates/gold_eur_rates_daily?limit=5'
curl -s -o /dev/null -w '%{http_code}\n' \
  '127.0.0.1:8095/v1/data/fx-reference/ecb-fx-rates/gold_eur_rates_daily'   # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" \
  '127.0.0.1:8095/v1/data/fx-reference/ecb-fx-rates/bronze_daily'          # 403
bash scripts/verify/api-schema.sh            # /v1/products and every product document validate
curl -s 127.0.0.1:8095/v1/openapi.json | python3 -c "import json,sys; print(json.load(sys.stdin)['openapi'])"
bash scripts/verify/catalog-site.sh          # a page per product in the release, each linking /dbt-docs/
curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8095/dbt-docs/          # 200
cd services/k2-api && uv run --locked pytest
make test
make check-docs
```

The data request returns the rows the same SQL returns as `p.analyst-a`; that pair is an
expectation row, so `make access-verify` is the check and the `curl` shows it. The key is shown
once by `make api-key` and is never in the repository; only its sha256 is.
