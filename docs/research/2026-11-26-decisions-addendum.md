# Decisions addendum, 2026-11-26

**Question.** [`DECISIONS-FINAL.md`](./2026-11-26-unified-platform-architecture/DECISIONS-FINAL.md) is a dated
snapshot: it still lists two owner decisions as open, and the plan file
[`001-release-and-rules-spine.md`](../plans/2026-11-26-unified-product-platform/001-release-and-rules-spine.md)
predates several rulings made while phase 001 was built. Which decisions were made after them, when, and by whom?

**Answer.** Two owner decisions closed the snapshot's open items; nine decisions were taken by the build's
orchestrator under the owner's standing delegation and are flagged to the owner for confirmation. The snapshot
and the plan are left as written; this file is the record. Dates are UTC.

## Owner decisions, after the snapshot

| Item | Decision | Date, by |
|---|---|---|
| O1c FX content | **Capability only**: the FX products deliver rates, cross rates and euro-valued candles; no further FX content is in scope | 2026-11-26, the owner |
| O6 landing retention | **A rolling 14 days per product in the lab**: each product's landings are kept 14 days, then expired | 2026-11-26, the owner |

Both were given to the build in conversation; this file is their first record in the repository. ADR-056's
Risks line ("Open: the FX content and landing retention, each by its phase") is an Accepted ADR's text and is
not edited; the phases that build FX (the run spine) and landings carry these decisions.

## Orchestrator decisions under the standing delegation, flagged to the owner

| Decision | Where it is recorded | Date |
|---|---|---|
| Readers see dbt outputs only through a publisher-owned view over the `audited` tag | [ADR-061](../adr/ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md) | 2026-11-26 |
| The view guards on its base's `$refs`, so a read fails unless the base is a tagged Iceberg table | ADR-061, Outcome | 2026-11-26 |
| No credential hash is ever committed: documents declare principals and roles; password and key hashes live under `${K2_SECRETS}`, generated locally; a release never carries one | [`phase-001-interfaces.md`](../development/phase-001-interfaces.md), Local secrets | 2026-11-26 |
| The catalog is fenced by its own network, `k2-catalog`, plus a drift check, until catalog authentication at the release gate | [ADR-059](../adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md) part 7 | 2026-11-26 |
| Deploy by moving a pointer per service from inside the `tools` container, recreating only what changed, rolling back on any failure | [ADR-058](../adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md) | 2026-11-27, as the ADR records it |
| Identity: passwords over TLS under a local, name-constrained CA, the Web UI off | [ADR-060](../adr/ADR-060-trino-logins-by-password-over-tls-under-a-local-ca.md) and its Outcome | 2026-11-26 |
| When a file an ADR links to is archived or removed, the link's target may be repointed or unlinked; the ADR's text is never changed | [`docs/adr/README.md`](../adr/README.md), Conventions | 2026-11-26 |
| The required CI check `Docker build (lake-metrics)` is kept, building the archived exporter so the archive stays verifiable; dropping the leg and the required check together is left to the owner | `.github/workflows/ci.yml`, the comment on that leg | 2026-11-26 |
| The `delay` and `rowScope` policies are stated as suitable for readers the owner trusts not to probe, until delayed or scoped data is physically separated | ADR-059 and ADR-061, Outcomes | 2026-11-26 |

ADR-058's date is the one its header records (2026-11-27); every other date here is the UTC date of the ADR or
commit that carries the decision.

## What this changes

Nothing that runs. Each decision takes effect where and when its record says; this addendum only states, in one
place, which of them the owner has not yet confirmed. The owner reversing any of them is the first revisit
trigger its ADR names.
