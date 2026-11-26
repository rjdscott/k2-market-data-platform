# The golden path: adding a data product (retired)

This page walked a team through adding a product in the old contract format: `make new-product`
scaffolded a `product.yaml` and an ODCS contract under `contracts/<domain>/<name>/`, `k2gen
validate` checked it, and `make generate` wrote its lake DDL, its retention entry and its
product page. That path is **retired** with the v3 lake ingest pipeline on 2026-11-26: the
thirteen products it described were archived in
[`legacy/v3-lake-ingest/contracts/`](../../legacy/v3-lake-ingest/contracts/), the one-shot that
applied the generated DDL is gone, and nothing ingests or builds a product made this way
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)). The page as it stood is in git
history (`git show 6429a58:docs/development/golden-path.md`). The tools went too, on 2026-11-27:
`make new-product`, `k2gen validate`, `k2gen generate` and `make generate` are gone, and a
product's documents are now checked and compiled into a sealed release by `k2gen release`
([chapter 21](../architecture/21-releases-and-deploy.md)).

The new path defines a product once, in its contract, and carries it through one ingestion,
storage, catalog and service layer
([chapter 20](../architecture/20-target-data-product-platform.md), the target, not yet built).
It arrives in the phases of the
[unified platform plan](../plans/2026-11-26-unified-product-platform/README.md):

| What | Phase |
|---|---|
| The ODPS and ODCS document model and the validation runner in CI | [001](../plans/2026-11-26-unified-product-platform/001-release-and-rules-spine.md) |
| One product's whole run path, proved on the ECB products | [002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) |
| The runbook for onboarding a file product, written from a run of that path | [003](../plans/2026-11-26-unified-product-platform/003-serving-and-teaching.md) |
| The runbooks for onboarding a Kafka product and a public-API product | [004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md) |

What domains, products and contracts are: [chapter 18](../architecture/18-data-mesh.md).
