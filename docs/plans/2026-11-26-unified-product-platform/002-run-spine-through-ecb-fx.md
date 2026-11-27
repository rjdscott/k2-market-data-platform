# Phase 002: the run spine, through ECB FX

**Depends on:** `001` (tag `v3.0.0-alpha.3`: releases, the document model, catalogs, rules,
identity, the reader side of the tested-data marker); the owner's answers on committing the
published files as fixtures and on rotating the MinIO root credential.
**Decision it implements:** [`ADR-056-unified-data-product-platform.md`](../../adr/ADR-056-unified-data-product-platform.md); ADR-036 as
accepted in `001`. New ADR, in the pull request that builds it: dbt builds silver and gold per
product, which supersedes the boundary row of
[ADR-035](../../adr/ADR-035-trino-and-dbt-for-marts.md) (status line only). ADR-054 (scoped
storage credentials, drafted in #227) is finished and accepted with the key per engine.
**Delivers:** one product's whole run path, proved on three real products: the fetch container
and the `https-file` connector, a per-product landing, bronze published by WAP in order; the dbt
runner and its wrapper, run records, `k2_input`, rollback and reconciliation, the tested-data
marker moved after tests; one DAG per product, pinned to one release; a storage key per engine
and the paths around Trino closed; the backup set compiled from contracts; `currency-master`,
`calendars` and `ecb-fx-rates` to gold, with the source's terms recorded.
**Exit:** the three DAGs run; a corrupt file lands but never reaches bronze `main`, and a later
file waits behind it; the same bytes again add no row; a failing test rolls silver back and no
reader saw the failed rows; a Trino stop mid-build is reconciled by the next run; a deploy
during a run leaves that run on its release; redirects to plain HTTP, to a host off the list or
to a private address fail with no network; each engine's key is refused outside its warehouse;
MinIO and Lakekeeper are unreachable from the host and from a fetch container; the backup lists
the ECB landing; tag `v3.0.0-alpha.4`.

## Scope

The steps, their files and proofs are in section B of
[`IMPLEMENTATION-N0-N1.md`](../../research/2026-11-26-unified-platform-architecture/IMPLEMENTATION-N0-N1.md), against the
interfaces frozen in `001`. Three of the plan review's riskiest unknowns are this phase's first
pull requests: one MinIO key per engine while Lakekeeper vends no credentials; the fetch
allow-list enforced in-process; Airflow starting one-shot containers through the Docker socket,
with `on_kill`, inside four LocalExecutor slots.

**#239 is closed unmerged and its sound parts are ported here** (review finding F19). It follows
the old model: a shared landed-files table, a `fileSource` block in the old schema, fetch and
parse inside Spark, the global lock. What carries over: the refusal of redirects to plain HTTP
or to private addresses (its `9b7d664`), the generic stage, check and publish half of its loader,
and the conditional write that never overwrites a landed object.

**First wave: keys, images and the fetch container**

- **A storage key per engine**: MinIO users and policies created by `lake-init`; Spark writes
  warehouse `k2` and registered-code outputs, Trino writes the domain warehouses, maintenance,
  init and backup each hold their own key, a read-only key serves the tools container, and the
  fetch container holds none.
- **Images**: `docker/fetch/`, `docker/dbt-runner/` (dbt from `dbt/uv.lock`), `docker/tools/`,
  each pinned by digest; their compose services under the profiles `fetch`, `build` and
  `tools`, so the budget gate counts and pin-checks them.
- **The fetch container**: connects only to addresses resolved from `allowHosts`, refusing
  private, loopback, link-local and unique-local IPv4 and IPv6 ranges; follows redirects only to
  `https://` on an allowed host; a 64 MiB cap, timeouts and TLS verification. It parses with the
  product's registered parser (`domains/<domain>/parsers/<name>.py`, bytes in, rows out), never
  in Spark. The redirect and address proofs land here, with the connector, not in a later phase
  (F7).
- **Notebooks through Trino**: `notebooks/k2lake.py` queries Trino as a named principal with the
  `trino` client, instead of DuckDB with the root key.
- **Backup, drop and replay scripts in the tools container** on `k2-net` with the read key, not
  on host ports.

**Second wave: the run path**

- **The Spark connector for files**: checks the spooled sha256 against the fetch record, moves
  the object into the landing bucket, loads rows onto the table's one fixed audit branch, audits
  counts, parse errors and the declared checks, and fast-forwards. Bronze publishes in order: a
  failed batch blocks later ones, never the landing. Audits are scoped to the batch, since a
  whole-branch uniqueness check grew from 0.47 s to 4.40 s on the spike's larger synthetic table
  ([`SPIKE-RUNCOST.md`](../../research/2026-11-26-unified-platform-architecture/SPIKE-RUNCOST.md), F12); whole-table checks run in maintenance.
- **Run records**: the `governance.run-records` product and the one client module every writer
  of it uses.
- **`k2_input` and the dbt wrapper**: `k2_input` renders `FOR VERSION AS OF` the input's newest
  audited snapshot, resolved once per run, and a plain `source()` in unit tests. The wrapper
  reconciles a run that never finished, writes `started` with each output's snapshot, runs unit
  tests and then the build, rolls each failed table back as its own writer, records
  `rolled_back` or `audited`, and moves the tested-data marker only after tests pass. The
  container's environment is an allow-list: Trino's address, the writer and its password, the
  release path. No Docker socket, no Airflow secret.
- **One DAG per product**, generated from the release: resolve the release once and pass it to
  every task, then fetch, load, audit, publish, build, expire, record on every outcome, and a
  daily maintenance task. Pools: one Spark slot, one fetch slot, two dbt slots.
- **The backup set compiled from contracts** replaces `make backup`'s four fixed tables, and
  `make drop` calls the same command (review trap 10); `make backup-verify` lists each copied
  snapshot id.
- **No path around Trino**: Lakekeeper and MinIO are no longer published on host ports; the
  MinIO root key is rotated by the owner and held only by `lake-init` and backup.

**Third wave: the three products**

- `reference-data/currency-master` (an `https-file` product for the ISO 4217 list, with its own
  parser and audit) and `reference-data/calendars` (table `target_days` from a seed of TARGET
  closing days a person writes; TARGET is the euro payment system, whose working days are the
  ECB's publication days).
- `fx-reference/ecb-fx-rates`: the ECB 90-day reference rate file to `bronze_daily`,
  `silver_rates` (typed and keyed, newest file wins, `DECIMAL(20,10)`) and `gold_eur_rates_daily`
  (one row per TARGET working day and currency, a missing rate carried at most five days, with
  `rate_available_at`). Conventions are the old P4's: quote units per euro, a reference fixing
  about 16:00 Frankfurt time, not a market close. The licence link and terms are checked against
  the ECB's terms page before any grant to a non-owner. Its timeout is set from 20 triggered
  runs (their maximum times a stated factor, F24).
- `governance/quality-results` rebuilt as the first governance product, over each writer's own
  test results.

**Learning aids and documents.** Runbooks: a failed audit; connector failure; backup and
restore of a file product. The ECB diagram added to chapter 21 when ECB is live.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test ecb_fx_rates
docker exec k2-airflow-scheduler airflow dags test currency_master
docker exec k2-airflow-scheduler airflow dags test calendars
bash scripts/chaos/fx-bad-file.sh            # bronze main unmoved, bytes kept, the next file waits
bash scripts/verify/fx-idempotent.sh         # the same bytes again: no new row
bash scripts/chaos/dbt-failing-test.sh       # silver at its prior snapshot; rolled_back and expired rows
bash scripts/chaos/trino-stop-mid-build.sh   # the next run writes reconciled before it builds
bash scripts/chaos/deploy-mid-run.sh         # the run finishes on R-1 and its run record says R-1
make test-connectors                         # no network: http redirect, off-list host, private address all fail
docker compose --profile build config dbt-runner | grep -A12 'environment:'   # only allow-listed names
make access-verify                           # key probes per engine, MinIO and Lakekeeper refused
docker exec k2-trino trino --execute \
  "ALTER TABLE fx_reference.ecb_fx_rates.silver_rates EXECUTE optimize"   # as a consumer: denied
make backup && make backup-verify            # lists the ECB landing
python3 scripts/check-budget.py              # fetch and build profiles counted, under 25 / 48
make test
make check-docs
```

`airflow dags test` runs in process and skips pools, the executor and the release pinning, so it
proves task logic only; `deploy-mid-run.sh` and the run records of scheduled runs prove the
scheduling. `dbt-failing-test.sh` also polls the table as a reader through the test window and
fails if a failed row was ever visible. `trino-stop-mid-build.sh` captures the environment names
of the running dbt runner before it stops Trino, since the container is removed after a run
(F25). The consumer statements run with the consumer's password over TLS, as `001` set up.

_Phase 002 landed 2026-11-27, tag `v3.0.0-alpha.4`. Where it differs from the text above: the run records have no `governance/run-records` product document (the namespace is reserved in k2gen and the table has one DDL); the wrapper runs one `dbt build` with unit tests inside it, and its publish guard knows every statement of the build by a Trino client tag per run, since dbt-trino reports only the `INSERT` of a contract-enforced table (ADR-062's Outcome); the backup copies with a read-only backup key, and the root pair stays with MinIO, `lake-init`, Lakekeeper's stored warehouse credential and `make drop` (ADR-054); notebooks through Trino move to phase 004; landing retention, 14 days per product, is built here; the five scripts of the Verification block exist, four recorded on throwaway lakes (`fx-idempotent`, `fx-bad-file`, `dbt-failing-test`, `trino-stop-mid-build`) and `deploy-mid-run.sh` on the live stack, where its first run found the deploy failing on a running build's `__dbt_tmp` relation (fixed in `v3.0.0-alpha.5.1`; `scripts/chaos/README.md`); the product DAGs ran on their schedules and by trigger in place of `airflow dags test`; the `expired` run record is the expire task's row, `task=expire, event=passed`; the ECB timeout is 10 minutes, 4.93 times the largest measured run (`docs/benchmarks/2026-11-27-ecb-fx-run-times.md`); the bootstrap peak of the default set is 17.30 CPU / 29.375 GiB (`python3 scripts/check-budget.py`); the consumer line `docker exec k2-trino trino --execute "ALTER TABLE ... EXECUTE optimize"` is replaced by the `p.analyst-a` statement rows of `tests/access/expectations.yaml`, run over TLS; `make access-verify`'s key probes are its storage-key rows, and its fence rows run in `make catalog-fence`._
