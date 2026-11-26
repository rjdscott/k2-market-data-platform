# Phase 007: REST complete, demo readiness and the tag

**Depends on:** `006` (tag `v3.0.0-alpha.8`: every grant, the full catalog site, lineage and
usage); the owner's answer on the reset for the live-onboarding moment.
**Decision it implements:** [`ADR-056-unified-data-product-platform.md`](../../adr/ADR-056-unified-data-product-platform.md): the REST routes
frozen in the interfaces pull request, completed; the owner's decision that the demo is only a
tag, `v3.0.0-beta.1`, placed when four moments can be shown on the real system, with no
demo-only code.
**Delivers:** the REST API complete (keyset cursor, `as_of` resolved to audited snapshots only,
state headers, 410 for a retired product, key rotation) and `make api-verify`; demo readiness:
a third product from a new domain on a prepared branch, `make demo`, its reset, the scripted
walkthrough, screenshots and a recorded run; the tag.
**Exit:** `make api-verify` passes; a rotated key is refused and its successor accepted; the
onboarding pull request's checks post a `k2gen diff`, and after merge, tag and deploy the new
product's first run is audited and its catalog page answers; `make demo` passes twice in a row
from its reset; the reset returns the stack to the baseline release; the four moments are shown
live on this host; the phase audit has no open critical or high finding; tag `v3.0.0-beta.1`.

## Scope

**Pull request 1: REST complete**, against the OpenAPI frozen in `001`, so routes are finished,
not reworked:

- Keyset cursor: the page token holds the last key returned; `next` is null on the last page.
- `as_of` resolves only to audited snapshots, within the 7-day window maintenance keeps.
- State headers from the lifecycle table: `K2-Product-State: paused`; `Deprecation` and `Sunset`
  on a deprecated product; 410 Gone for a retired one.
- Key rotation and revocation: `make api-key PRINCIPAL=<id> ROTATE=1` prints a new key once and
  replaces the stored hash in a pull request; the old key stops at the deploy that carries it.
- `make api-verify`: every route answers; REST equals SQL for every expectation row; an `as_of`
  before a rollback returns no failed row; a retired fixture returns 410; a cursor walk over a
  table repeats and skips nothing.

**Pull request 2: the onboarding product.** For moment (a): a small product from a new domain,
a public economic series from a public API, on the public-API connector built in `004`. Its
documents (ODPS file, ODCS contract, one model, unit tests, grants), its parser, its licence and
terms checked against the publisher's terms page before any non-owner grant, its expectation
rows. It is prepared on a branch and opened as a pull request during the walkthrough, so the
audience sees the checks, the review, the merge, the tag and the deploy; a new domain adds a
warehouse and two Trino catalogs, so the deploy drains and restarts Trino, as designed.

**Pull request 3: `make demo` and the reset** (review finding F10; no demo-only code):

- `make demo` restores a recorded baseline with `make restore` from a named backup set, deploys
  the baseline release, then runs only real commands, printing each moment's evidence. It is the
  release smoke test and the Trino smoke test: the follow-on plan's venue phase and the
  release check call it rather than building their own (review trap 7).
- The reset for moment (a), as the owner decides: either a revert of the onboarding merge
  through a pull request, a redeploy of the baseline release and `make drop` of the product's
  namespaces, so the prepared branch can be opened again; or the product onboarded once for real
  at the tag, with the recorded run serving later audiences. `make demo-reset` runs only in lab
  mode; it is refused once production mode exists.
- The scripted walkthrough, `docs/operations/demo.md`: what to say and what to run at each
  moment, the command for each, and what it shows. Results are read live from the platform's
  tables, never copied into the document.
- The fallback: screenshots through `make screenshots` and a recorded run of the walkthrough,
  both made on the real system at the tag.

**The four moments, on the real system**

| Moment | What is shown | Built in |
|---|---|---|
| (a) Onboard a product live | Pull request, checks with `k2gen diff`, merge, tag, `make deploy`, data arrives, catalog page appears | `001`, `004`, this phase |
| (b) Find, trust, query | The catalog page (owner, state, quality, lineage, terms), then the same answer through SQL and REST | `003`, `006`, this phase |
| (c) Permissions in action | Two roles on one table: `delayed_reader` sees only rows older than 20 minutes and a masked column as null; `full_reader` sees both | `001`, `004` |
| (d) Bad data stopped, and an enriched product | A corrupt ECB file lands but never reaches bronze `main`; a failing test rolls back; `candles-eur` with its lineage across three domains | `002`, `005`, `006` |

Audience: engineers and potential users. Live on this host, with the walkthrough; the
screenshots and the recording are the fallback.

**Learning aids and documents.** Runbooks: issue, rotate and revoke an API key; run the demo
and reset it. `README.md` points at the walkthrough.

## Verification

```bash
make api-verify
bash scripts/verify/key-rotation.sh          # old key 401 after the deploy, new key 200
gh pr checks <onboarding pull request>        # validation runner green, k2gen diff in the summary
make deploy RELEASE=v3.0.0-beta.1 && make release-verify
curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8095/catalog/<new domain>/<new product>/   # 200
make demo && make demo                        # passes twice in a row from its reset
make demo-reset && make release-verify        # every part reports the baseline release
docker exec k2-trino trino --execute "SHOW CATALOGS"   # the onboarding domain's catalogs absent after the reset
make test
make check-docs
```

`make demo` prints, per moment: (a) the onboarded product's first `audited` run record and its
page's status; (b) an empty difference between the SQL and REST row sets; (c) the two roles'
rows against their expectation rows; (d) bronze `main` unmoved after the corrupt file, silver at
its prior snapshot after the failing test, and the lineage route's three domains. The tag is
placed only after the four moments have been shown live, and the walkthrough names the
recording and screenshots made from that run.
