# Phase 001 interfaces: the proposal and its two reviews

**Date:** 2026-11-26 (UTC)
**Question:** what must phase 001 of the [unified product platform plan](../plans/2026-11-26-unified-product-platform/001-release-and-rules-spine.md)
freeze before its steps build in parallel, and does what was frozen hold up?
**Status:** a record of inputs, not decisions. The decisions are
[ADR-061](../adr/ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md) and the frozen
interfaces themselves: [`platform/k2gen/INTERFACES.md`](../../platform/k2gen/INTERFACES.md) and
[`docs/development/phase-001-interfaces.md`](../development/phase-001-interfaces.md), which win where these
files differ.

## The three files

Retained in [`2026-11-26-phase-001-interfaces/`](./2026-11-26-phase-001-interfaces/), each with a two-line
header; session-only paths are rewritten as "session scratch", and the places that put a credential hash in
git carry a note that the ruling on Q4 reversed them.

| File | What it is | What became of it |
|---|---|---|
| [`phase001-interfaces-proposal.md`](./2026-11-26-phase-001-interfaces/phase001-interfaces-proposal.md) | The interfaces A1 to A13 revised for phase 001, how decision O8 is enforced, the build breakdown S00 to S24 and its waves, with the orchestrator's rulings at its top | Built against by every step; its section 1 became ADR-061 after the gate [`2026-11-26-tested-data-view-gate.md`](./2026-11-26-tested-data-view-gate.md) |
| [`phase001-o8-review.md`](./2026-11-26-phase-001-interfaces/phase001-o8-review.md) | An independent adversarial review of the proposal's section 1, with experiments on a throwaway Trino | Its findings B1 to B5 (the builder owning the view, a forgeable view owner, statistics bounds, a copied `main`, visible view SQL) are the ones ADR-061's Context names; the gate re-ran them |
| [`phase001-interfaces-review.md`](./2026-11-26-phase-001-interfaces/phase001-interfaces-review.md) | An independent review of what steps S00a and S00b froze: 25 findings and a collision list | Step S00c acted on every critical, high and medium finding and the lows; each surface of its collision list has one owner and one frozen place in the phase page |

## In short

- The proposal's enforcement of O8 (a definer view over an `audited` tag) survived both the O8 review and
  the gate, with conditions the review added (a publisher distinct from the builder, statistics off for
  readers, a network fence and a drift check) and two amendments the gate found (the publisher's catalog
  access on the reader catalog; `svc.platform` holding nothing in reader namespaces).
- The interfaces review found that the frozen test would have made every filling step edit the same lines,
  that the old validator would fail on the first v2 product, and that the secrets layout, dbt model names,
  relation dispatch and several release files were left for each step to invent. The frozen interfaces now
  fix each of those in one place.
