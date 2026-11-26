# Phase 001 interfaces: what is frozen, where, and who may change it

Frozen on 2026-11-26 by the interfaces step (S00) of
[phase 001](../plans/2026-11-26-unified-product-platform/001-release-and-rules-spine.md), before
any parallel building, so that two steps on either side of an interface build against one
definition. The step ran in two halves: **S00a** froze the document schemas, the k2gen types and
the fixtures (its page is `platform/k2gen/INTERFACES.md`); **S00b** froze the shared files and
the wiring listed here. The interfaces A1 to A13 are those of
[IMPLEMENTATION-N0-N1.md](../research/2026-11-26-unified-platform-architecture/IMPLEMENTATION-N0-N1.md)
section A, as revised for phase 001.

Everything here is **target state**. Nothing in the running stack reads these files yet: the
compose stubs are behind profiles nothing starts, the new `.env.example` names are commented
out, the macros are called by no model, and the `make` stubs print `SKIP`. Each step that makes
one real updates the documents describing the running system in that same change.

**The rule.** A frozen file changes only through the step that owns it (the tables below). A
change to a frozen interface itself goes through the orchestrator, and every step that depends
on it is told.

## A1 to A13: the files that freeze them

| | Interface | Frozen in (S00b) | Frozen in (S00a) | Built by |
|---|---|---|---|---|
| A1 | Release directory, manifest, pointers; the releases root mounted once, read-only, at `/k2/releases` | `docker-compose.yml` (`x-k2-releases-ro`, the `tools` read-write mount), `.env.example` (`K2_RELEASES`, `K2_HOME`), `mk/release.mk` | manifest schema | S08, S19, S22 |
| A2 | Document set and provenance | | `platform/k2gen/INTERFACES.md` | S04 |
| A3 | Compiler and checks: one module per emitter and per check | | `platform/k2gen/INTERFACES.md` | S05 to S08, S11 to S18 |
| A4 | Connector configuration, parser, fetch exits | | `platform/k2gen/INTERFACES.md` | phase 002 |
| A5 | Landing and bronze names | | `platform/k2gen/INTERFACES.md` | phase 002 |
| A6 | The run-record table | `platform/runs/run_record.sql` | | S19 creates it; phase 002 writes it |
| A7 | `k2_input`, the results hook, the dbt wrapper's environment | `dbt/macros/k2_input.sql`, `dbt/macros/k2_results.sql` (stub), `dbt/tests/k2_input/`, `.env.example` (the wrapper's names, never set there) | | phase 002 |
| A8 | Naming, catalogs, the `k2-catalog` network, storage keys | `docker-compose.yml` (`k2-catalog`), `.env.example` (`K2_*_S3_*`) | `platform/k2gen/INTERFACES.md` | S11, S12, S22; keys phase 002 |
| A9 | Access expectations | `mk/access.mk` | expectations schema | S20 |
| A10 | Roles, principals, grants, policies, identity | `mk/identity.mk`, `mk/principals.mk`, `.env.example` (`K2_SECRETS`, `K2_TRINO_SHARED_SECRET`), `.gitignore`, the secrets layout below | principals and grants schemas | S13, S14, S22 |
| A11 | Catalog JSON, one document per product | `services/k2-api/openapi.yaml` (cites it) | catalog schema | S18 |
| A12 | REST: every `/v1` route | `services/k2-api/openapi.yaml` | | phase 003; phase 007 completes it |
| A13 | One-shot containers | `docker-compose.yml`: `tools`, `fetch`, `dbt-runner`, `k2-api` | | S19 (`tools`), phase 002, phase 003 |

The tests that hold them: `tests/test_frozen_interfaces.py` (the OpenAPI document under a pinned
validator, the run-record DDL under a pinned parser), `tests/test_secrets_untracked.py`,
`scripts/dbt-test.sh` step 4 (`k2_input`'s render cases), and `python3 scripts/check-budget.py`
(the stubs' limits and pins; the default bootstrap peak stays 16.70 CPU / 28.688 GiB).

## Who owns which shared file

| File | Owner | Wave |
|---|---|---|
| `Makefile` | S00b (the `include mk/*.mk` line); nobody after | W0 |
| `mk/alerts.mk`, `mk/identity.mk`, `mk/principals.mk`, `mk/dbt.mk`, `mk/access.mk`, `mk/release.mk` | S09, S13, S14, S17, S20, S19 | W1, W2, W2, W2, W3, W3 |
| `docker-compose.yml` | S00b (stubs), then S09 alone, then S22 alone | W0, W1, W4 |
| `.github/workflows/ci.yml` | S21 only | W3 |
| `platform/k2gen/k2gen/cli.py` | S00a adds the subcommand stubs; each step fills its own module | W0 |
| `contracts/schema/`, `platform/k2gen/k2gen/schemas/` | S00a only | W0 |
| `platform/k2gen/tests/fixtures/v2/` | S00a the good set; each check step adds only `bad/<its check>/` | W0, W1 |
| `scripts/dbt-test.sh` | S00b (step 4), then S13 (TLS and passwords) | W0, W2 |
| `scripts/trino-rules-test.sh` | S12, then S13 after S12 merges | W2 |
| `scripts/lib/throwaway-lake.sh` | S12; S20 reuses it unchanged | W2, W3 |
| `scripts/drop.sh`, `CLAUDE.md`, `SECURITY.md` | S22 only | W4 |
| `.env.example` | S00b named every variable; each owning step uncomments its own line | any |
| Index files, `CHANGELOG.md` | one appended row each; the changelog by the orchestrator | any |
| ADR numbers | 058 (S19), 059 (S12), 060 (S13), 061 (S02), 062 (phase 002) | |

## Local secrets

Secrets are generated locally and never committed. They live in
`${K2_SECRETS:-$HOME/k2-secrets}`, outside any checkout:

| Path under `${K2_SECRETS}` | What | Written by |
|---|---|---|
| `<principal>.password`, mode 600 | a service principal's password (`dbt_*`, `pub_*`, `svc.*`) | `make password` (S14) |
| `tls/` | the local CA and Trino's server certificate; clients mount `ca.pem` | `make tls` (S13) |
| the Slack webhook URL file | Alertmanager's `api_url_file` | S09 |

Only hashes reach git (`contracts/access/principals.yaml`, S14); a release manifest may record
that a secrets file exists and its checksum, never its content. A copy that lands inside a
checkout is still kept out: `.gitignore` ignores `k2-secrets/` and `*.password` at any depth, and
`tests/test_secrets_untracked.py` fails if either line goes or a matching file is tracked.

## Provisional until S01

The gate `spine-h-tested-data-view` (S01) proves design (a), a definer view over the `audited`
tag owned by a publisher, or replaces it with the runner-up. Until it reports, these frozen
places rest on (a) and say so where they stand:

- `.env.example`: `K2_PUB_USER`, the publisher `pub_<fence>` the wrapper switches to for the tag
  commit and the view sync (A7 step 6, A8, A10).
- `dbt/macros/k2_results.sql`: results written to `<domain>_build.<fence>__base`, the only
  namespace a builder owns under (a).
- `platform/runs/run_record.sql`: an `audited` row as the tag's history and the drift check's
  reference.
- `services/k2-api/openapi.yaml`: `/v1/data` reading the reader view over the tag.

## Left to the step that goes live

The proposal gives the compose file `name: k2`. It is not set here: the running stack's
containers belong to the project named after the checkout's directory, so the line would leave
`docker compose` from the live checkout blind to them, and the next `up` would collide with
their container names. Step S22 sets it with the cutover that recreates the services
(`docker compose -p k2`, A1).
