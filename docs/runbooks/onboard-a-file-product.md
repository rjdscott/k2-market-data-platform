# Runbook: Onboard a file product

Use this to add a product whose source is one file fetched over HTTPS (connector kind
`https-file`): its documents, its parser and audits, the checks before a pull request, the release,
the secrets it needs, the deploy, its first run, and its catalog page. The worked example is the
three products this platform runs: `fx-reference/ecb-fx-rates` (a file, bronze, silver and gold),
`reference-data/currency-master` (a file, bronze and one dbt output) and `reference-data/calendars`
(no file: a seed a person writes, built by dbt). A Kafka source is phase 004's; granting a reader is
[grant-and-revoke-access.md](./grant-and-revoke-access.md). Why the path is shaped this way:
[ADR-056](../adr/ADR-056-unified-data-product-platform.md) and
[ADR-036](../adr/ADR-036-connectors-land-verbatim-then-publish-bronze-after-audit.md).

> **Plan 003 says this runbook is written last, from a run of the whole path on the live stack.**
> Sections 1 to 3 ran on 2026-11-27 on the working tree, no container started; sections 4 to 7
> are written from the scripts that print their output and have not run on the live stack yet.
> Each such step says so.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded** (sections 4 to
7, in the live checkout); sections 1 to 3 need no `.env`.

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Write the documents and the parser | a day | not measured: a person's work |
| 2 | Check them, no container | < 5 min | `ci-validate.sh` 1.6 s, `k2gen check --previous` 0.3 s, `make test-k2gen` 37 s, the dbt tree proof 75 s (2026-11-27, below) |
| 3 | The pull request | review | not measured |
| 4 | Release and secrets | < 10 min | not yet measured: the first product onboarded after the deploy of this release |
| 5 | Deploy | < 10 min | not yet measured, same trigger |
| 6 | First run | the product's own | not yet measured, same trigger |
| 7 | Its catalog page | < 1 min | not yet measured, same trigger |

---

## 1. Write the documents and the parser

**When**, a source has been chosen and its terms of reuse read.

**Expected behaviour**, every fact about the product lives in its documents; the compiler derives
the rest. Names follow from two words, the domain and the product, kebab case:

| Name | Rule (`platform/k2gen/k2gen/model.py`) | ECB example |
|------|-----------------------------------------|-------------|
| fence | the product name with `-` as `_` (`fence()`) | `ecb_fx_rates` |
| DAG id | the fence | `ecb_fx_rates` |
| bronze, landing log, audit results | `lake.<fence>.<table>`, `lake.<fence>.landing_files`, `lake.<fence>.audit_checks`, in warehouse `k2` | `lake.ecb_fx_rates.bronze_daily` |
| landing prefix | `k2-landing/<domain>/<product>/<fetch day>/<sha256>.<ext>` | `fx-reference/ecb-fx-rates/` |
| dbt outputs | `<domain fence>_build.<fence>__base.<table>`, read by readers as `<domain fence>.<fence>.<table>` | `fx_reference.ecb_fx_rates.gold_eur_rates_daily` |
| builder, publisher | `dbt_<fence>`, `pub_<fence>` (`builder()`, `publisher()`): not declared, known from the product | `dbt_ecb_fx_rates`, `pub_ecb_fx_rates` |

**The documents**, under `contracts/<domain>/<product>/`; the domain must be one of
[`contracts/domains.yaml`](../../contracts/domains.yaml) (a new domain is a new entry there, with
its owner and kind, and a new Lakekeeper warehouse at the next deploy, ADR-044). The two file
products hold:

| File | `ecb-fx-rates` | `currency-master` | What it says |
|------|----------------|-------------------|--------------|
| `product.odps.yaml` | yes | yes | the product (ODPS): id, status, purpose, limitations, its input ports, and `customProperties`: `connector` (`kind: https-file`, `url`, `allowHosts`, `format`, `parser`), `schedule` (`cron`, later `timeoutMinutes` from measured runs), `terms`, `examples` |
| `<bronze>.odcs.yaml` | `bronze_daily` | `bronze_currencies` | the connector's table (ODCS): its columns, `source_file_sha256` among the primary key, `quality` entries of `type: custom, engine: k2-connector` naming a parser audit, `customProperties` `layer: bronze`, `build: connector`, `backup: restore` or `rebuild` |
| `<output>.odcs.yaml` | `silver_rates`, `gold_eur_rates_daily` | `currency` | each dbt output's contract, its roles and policies |
| `models/<output>.sql` | 2 models | 1 model | the dbt models; their opening words are the pull request's "What this does and why" |
| `unit_tests.yml` | yes | yes | dbt unit tests, fixtures typed in SQL |
| `grants.yaml` | `p.analyst-a`, `full_reader` | `grants: []` | who reads which role; a grant outside the owning team needs `terms` recorded first (`k2gen check` refuses it otherwise) |

`backup: restore` is for what cannot be fetched again: the ECB's bronze, since its 90-day file
forgets; `currency-master`'s bronze is `rebuild` (the list can be fetched again). The backup set is
compiled from it ([lake-backup-and-restore.md](./lake-backup-and-restore.md)). `allowHosts` is the
only place a host is allowed: fetch refuses any other, redirects included, and widening it is a
contract change in a pull request, never a setting ([connector-failure.md](./connector-failure.md)).

**The parser**, `domains/<domain>/parsers/<parser>.py`, its name the connector's `parser`
([`ecb_fx.py`](../../domains/fx-reference/parsers/ecb_fx.py),
[`iso4217.py`](../../domains/reference-data/parsers/iso4217.py)). The contract fetch holds it to
([`docker/fetch/fetch.py`](../../docker/fetch/fetch.py)):

- `def parse(raw: bytes) -> list[dict]`: one dict per row, exactly the bronze table's columns but
  `source_file_sha256`, which the connector sets. Any raise is a parse failure: fetch exits 30,
  the bytes land, nothing is staged, and the audit fails the batch. Keep values as published
  strings; typing is the model's job.
- `def <audit>(rows: list[dict]) -> list[str]` for each `implementation` the bronze `quality` names:
  one description per failing row, `[]` passes. An audit that is missing, raises, or returns
  anything else is recorded as failed with the reason.
- Standard library only, and no import of a sibling module: fetch loads the file by its path, in a
  container on `k2-egress` with no secret. Untrusted bytes: refuse a DOCTYPE or entity declaration
  and any encoding but UTF-8, as both parsers do.

**Fixtures are synthetic**: `domains/<domain>/tests/fixtures/`, invented values in the exact format,
with a `README.md` that says so (the ECB set: a good file, one that parses and fails both audits,
one cut mid-document). No third-party data is committed: `python3 scripts/check_docs.py data` is
the gate. Tests: `domains/<domain>/tests/test_<parser>.py`.

**Measured**, not measured: a person's work.

---

## 2. Check them, no container

**When**, before the pull request, and after every change to it.

**Expected behaviour**, each command below starts no container. `ci-validate.sh` is CI's own
validation runner: from a scratch clone of the current tree (tracked and untracked files) at a
scratch tag, `k2gen docset`, `k2gen check --previous none` and a trial compile with placeholder
image digests, never deployed; the release directory is its last line.

**Recovery**

```bash
uv run --no-project --with pytest==9.2.0 pytest domains -q          # the parsers, on their fixtures
python3 scripts/check_docs.py data                                    # no real data committed
bash scripts/verify/ci-validate.sh /tmp/k2-ci                         # docset, check, trial release
uv run --project platform/k2gen --locked -q k2gen check --set /tmp/k2-ci/pr/set \
  --previous "${K2_RELEASES:-$HOME/k2-releases}/pointers/trino/documents"   # against the live release's set
make test-k2gen
(cd dbt && uv sync --locked -q) && (cd platform/k2gen && K2_DBT_PROOF=1 uv run --locked -q pytest -q -rs tests/test_emit_dbt.py)
```

Run on the working tree on 2026-11-27 (the `--previous` set was the tag `v3.0.0-alpha.3.2`'s, from
`k2gen docset --tag v3.0.0-alpha.3.2 --out <dir>`, standing in for the live release's documents):

```text
53 passed in 0.03s
k2gen release: NO PIPELINE market-analytics.hot-gold: it has no schedule, so no DAG runs it
<dir>/pr/releases/v0.0.0-ci.1-a6f273a3498f
1394 passed, 4 skipped in 36.96s
31 passed in 75.01s (0:01:15)
```

`check_docs.py data` and `k2gen check` print nothing and exit 0 when clean; a finding is printed as
`<severity> <code> <path> <pointer>: <message>`, and any `error` exits 1. In the trial release, read
what the product compiled to: `compiled/products/<domain>.<product>/pipeline.json` (fence, DAG id,
connector, bronze checks, builder and publisher), `compiled/airflow/dags.json` (its task list),
`compiled/lake/backup-set.json` and `compiled/catalog/products/<domain>.<product>.json`.

Three more need containers and are not run here:

- `make test-dbt-tree` is the dbt tree proof above, then `bash scripts/trino-rules-test.sh`, the
  rules suite on a throwaway lake (Trino, Lakekeeper, MinIO): containers, no stack.
- `make test-dbt`: lint, parse and compile, then each compiled product built with its tests on a
  throwaway lake and its results table read back (`scripts/dbt-test.sh`, about 75 s).
- `make access-verify RELEASE=<the trial release dir>`: every row of
  `tests/access/expectations.yaml` on a throwaway lake. Add the product's rows there in the same
  pull request, as the ECB rows do: its bronze and its landing log denied, each base denied, each
  reader view as its grants say.

**Measured**, 2026-11-27 on the working tree: `ci-validate.sh` 1.6 s, `k2gen check --previous` 0.3 s,
`make test-k2gen` 37 s, the dbt tree proof 75 s.

---

## 3. The pull request

**When**, the checks of section 2 pass.

**Expected behaviour**, one pull request carries the documents, the parser, its tests and fixtures,
and the access rows. Its template ([`.github/pull_request_template.md`](../../.github/pull_request_template.md))
opens with **What this does and why**, required for a change under `contracts/**/models/`: the
grain (what one row is), the rule each model applies, the fact that makes the rule necessary, and
why its materialization fits, in the same words that open the model file. How a reviewer reads it:
[`docs/development/reviewing-dbt-work.md`](../development/reviewing-dbt-work.md). CI runs
`ci-validate.sh --diff`, which writes the `k2gen diff` from the newest `v3.*` tag's release into
the job summary: the new DAG, tables, landing and backup entries should be all it adds.

**Recovery**, a red check: the job's log names the command; rerun it locally from section 2.

**Measured**, not measured.

---

## 4. Release and secrets

**When**, the pull request is merged and an annotated tag is approved on `main`.

**Expected behaviour**, `make release` compiles the tag into a sealed release
([deploy-a-release.md](./deploy-a-release.md) section 1). A dbt product needs two password files
before its deploy, its builder's and its publisher's: deploy's preflight refuses without them,
naming each with the command that writes it (`scripts/deploy.py` `required_secrets`): `deploy:
REFUSED: secret files missing under <K2_SECRETS>: principals/dbt_<fence>.password (make password
PRINCIPAL=dbt_<fence> TO_FILE=1); ...; nothing changed`. `make password` knows a builder or
publisher only from the product's documents in the checkout it runs in (`scripts/principal-secret.py`),
so run it from the live checkout at the tag. A reader the grant names needs its own password, and
a k2-api key to read over REST ([create-or-rotate-a-principal.md](./create-or-rotate-a-principal.md)).

**Recovery**, not yet run on the live stack:

```bash
make release TAG=<tag>
make password PRINCIPAL=dbt_<fence> TO_FILE=1
make password PRINCIPAL=pub_<fence> TO_FILE=1
make principals-status                 # the two show a password line and a password file
```

**Measured**, not yet measured.

---

## 5. Deploy

**When**, the release is compiled and its secrets written.

**Expected behaviour**, the plan names the DDL the product needs (its Spark tables, bronze,
`landing_files` and `audit_checks`, created in the `ddl` step) and the namespaces of its storage
plan; the release-time access suite runs its rows on a throwaway lake in
preflight; after the swap the factory builds the new DAG within 30 s of the pointer moving
([airflow-scheduling.md](./airflow-scheduling.md)); the site step regenerates the catalog site and
the dbt docs, and never fails the deploy.

**Recovery**, not yet run on the live stack:

```bash
make release-diff RELEASE=<release id>
make deploy RELEASE=<release id> PLAN=1
make deploy RELEASE=<release id>
make release-verify
docker exec k2-airflow-scheduler airflow dags list            # the new DAG id is listed
```

A refusal or a rollback: [deploy-a-release.md](./deploy-a-release.md) sections 5 and 6.

**Measured**, not yet measured.

---

## 6. First run

**When**, the deploy is verified.

**Expected behaviour**, an `active` product's DAG starts unpaused and runs at its cron; a trigger
runs it now. Its inputs must be tested first: `ecb-fx-rates` requires `calendars`' tested
`target_days`, or its build exits 4. A first run of a file product fetches, lands the file, stages
and audits its rows on bronze's `audit` branch, publishes `main`, builds and tests the dbt outputs,
moves their `audited` tags, and writes one run-record row per step.

**Recovery**, not yet run on the live stack:

```bash
docker exec k2-airflow-scheduler airflow dags trigger <fence>
docker exec k2-airflow-scheduler airflow dags list-runs <fence>
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run <fence> <run_id>

# The run record, as svc.catalog (trino_cli: tls-and-passwords.md, NET=${K2_COMPOSE_PROJECT}_k2-net).
trino_cli svc.catalog "SELECT task, event, table_name, rows_written, release_id, recorded_at
  FROM governance.run_records.run_record WHERE run_id = '<fence>/<run_id>' ORDER BY recorded_at"
```

Every task `success` (`maintain` and `expire` may be `skipped`); run-record rows `fetch`, `load`,
`audit`, `publish` `passed`, and the build's `started` then `audited` per output. A red task:
[airflow-scheduling.md](./airflow-scheduling.md) section 2, then
[connector-failure.md](./connector-failure.md) or
[file-source-audit-failed.md](./file-source-audit-failed.md).

**Measured**, not yet measured.

---

## 7. Its catalog page, and a read

**When**, after the first run.

**Expected behaviour**, k2-api (`127.0.0.1:${K2_API_PORT:-8095}`) serves the catalog site deploy
wrote: an index at `/catalog/`, and one page per product at
`/catalog/<domain>/<product>/index.html` (`platform/catalog-site/k2site.py`), with each output's
tested snapshot, the product's newest run and its test counts, which `svc.catalog` reads from the
run records and the quality results. A page shows the run only once the site is regenerated: deploy
does it after each deploy and rollback, `make site` now, and the daily timer of `make
site-install` at 05:15 UTC. A reader the grant names reads the gold view over SQL or REST.

**Recovery**, not yet run on the live stack:

```bash
make site
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:${K2_API_PORT:-8095}/catalog/fx-reference/ecb-fx-rates/index.html   # 200
curl -s http://127.0.0.1:${K2_API_PORT:-8095}/v1/products/fx-reference/ecb-fx-rates | python3 -m json.tool | head -20
make access-verify LIVE=1              # the product's rows on the stack, once it is published
```

The REST data route is `GET /v1/data/<domain>/<product>/<table>` with `Authorization: Bearer
<key>` (`services/k2-api/openapi.yaml`); the key's principal must hold a role on the table.

**Measured**, not yet measured.

---

## Failure modes / incidents

- None yet.

---

**Last verified:** sections 1 to 3 on 2026-11-27 against the working tree of the phase 002 and 003
branch, no container started; sections 4 to 7: not yet: first run after the deploy of this release.
