# Phase 000: clear the ground

**Depends on:** nothing; `main` at `64a07c2` and this documentation update (ADR-056, chapter 20,
the research record, these plans).
**Decision it implements:** [`ADR-056-unified-data-product-platform.md`](../../adr/ADR-056-unified-data-product-platform.md). The ClickHouse
pause is recorded in its own ADR in this phase, with its resume trigger and the 7-day expiry of
its consumer offsets. [ADR-034](../../adr/ADR-034-airflow-3-replaces-prefect.md) and
[ADR-050](../../adr/ADR-050-order-book-capture-paused-during-platform-build.md) gain Outcome
sections; [ADR-048](../../adr/ADR-048-gold-step-reads-touched-data-writes-bars-once.md), never
built, gains a superseded status line. Each is an edit the ADR conventions permit.
**Delivers:** ClickHouse paused behind a profile; the Spark image rebased and proven; `make drop`
with its own backup; the measured spikes committed and the remaining spikes run; the old
pipeline backed up, paused, dropped and archived to `legacy/v3-lake-ingest/`, with every reader
of its tables moved or retired in the same phase.
**Exit:** ClickHouse is absent from `docker compose config --services`; the budget gate reports
17.30 CPU / 29.812 GiB at the bootstrap peak; the new Spark image passed `make gates` on the
live old pipeline before the drop; `make drop` mirrored, counted and then dropped the scratch
objects and the old namespaces; Trino's `lake` catalog lists no old namespace; no file outside
the archive and the dated surfaces names `raw.messages` or `audit.checks`; `make gates` passes
with a visible SKIP for every stood-down gate; the spike scripts exit 0; tag `v3.0.0-alpha.2`.

## What this phase costs, said plainly

From the drop at the end of this phase until phase `004` lands `kraken-trades`, **the lake
receives no live crypto data**, and every dashboard, notebook, lab and screenshot that read the
old tables is stale or retired. The captures keep writing to Redpanda, which keeps 48 hours or
2 GiB per partition (`docker/redpanda/init.sh:139-146`), so `kraken-trades` starts at the log
start when it first runs and nothing older is recovered. `make lake-verify` and the 2026-11-25
parity pins stop being live gates; the scripts are kept for the re-pin in phase `004`.
ClickHouse's reload from lake gold goes with gold, so a resume of the hot tier works only from
topics within 7 days, and its runbook says so. In a lab whose data is disposable this is cheap,
and the alternative, keeping the old pipeline alive, would make the next three phases carry
thirteen old-format products, the old writers and the old mounts through a new compiler and a
new deploy model (review finding F1).

## Scope

Steps in order, as pull-request-sized pieces. The step list with files, dependencies and proofs
is section B of [`IMPLEMENTATION-N0-N1.md`](../../research/2026-11-26-unified-platform-architecture/IMPLEMENTATION-N0-N1.md);
this phase applies it with the plan review's changes (retirement moved here, three measurements
struck).

**Wave 1, in parallel on disjoint files**

- **The measured spikes, committed first** (the scratchpad they live in is session storage): the
  three spike directories into `scripts/spikes/2026-11-26/`, each with a `run.sh`, and
  `docs/benchmarks/2026-11-26-product-run-cost.md` through `/benchmark-report`, with n and the
  command on every row. With n = 5 the published p90 is the maximum and is labelled so; no
  timeout is derived from it (review finding F24). The decision is recorded there: one product
  run costs about 6.5 s of Spark, far under the 200 s line, so per-product Spark sessions stay.
  The same document states the delete-file bound per silver table that phase `004`'s 48-hour
  exit tests, with the reasoning that sets it (F13).
- **Struck as already answered** ([`SPIKE-DBT.md`](../../research/2026-11-26-unified-platform-architecture/SPIKE-DBT.md), [`SPIKE-ACCESS.md`](../../research/2026-11-26-unified-platform-architecture/SPIKE-ACCESS.md), [`SPIKE-RUNCOST.md`](../../research/2026-11-26-unified-platform-architecture/SPIKE-RUNCOST.md) in the
  research directory): per-product run cost, dbt merge and rollback through the catalog, masks
  and filters on Iceberg, the read-only reader catalog, the refresh period itself. The contended
  chain measurement moves to phase `004`, where the first five-minute chain exists.
- **The remaining spikes**, each a directory under `scripts/spikes/<date>/` whose `run.sh` exits 0
  and prints its finding, written up in `docs/research/<date>-release-spine-spikes.md` with the interface
  each one confirms or changes: (a) a dbt-trino merge with `incremental_predicates` on a
  partitioned table; (b) Spark `expire_snapshots` by id on a Trino-written table, with a
  non-root key; (c) Trino's rules refresh and file group provider, and an Airflow dag-processor,
  after an `mv -T` pointer swap inside a read-only directory mount on this host; (d) `k2_input`'s
  `FOR VERSION AS OF` beside a mocked source in one `dbt build`; (e) writer A's
  `rollback_to_snapshot` and `optimize` on writer B's table in one writable catalog under file
  rules; (f) a rollback across a contracted table's create-or-replace (F12).
- **`make drop`, a backup first**: `scripts/drop.sh` runs in a container on `k2-net`; it mirrors
  the target with `mc mirror` to `$K2_BACKUP_DIR/drop/<date>/`, compares object count and bytes,
  then purges through Lakekeeper or removes the bucket; `TARGET=<warehouse>.<ns>[.<table>]` or
  `bucket:<name>`, and `DRY_RUN=1`. Test in `tests/test_drop.py`; runbook
  `docs/runbooks/drop-and-rebuild.md`.
- **The Spark image rebased** on `spark:3.5.9-scala2.12-java17-python3-ubuntu` by digest, with
  the Iceberg runtime and AWS bundle 1.8.1 and the Kafka jars at 3.5.9's versions,
  sha256-pinned as today, and the settings the stack relies on restated (Iceberg extensions,
  event-log cleaner, `/home/iceberg` paths). The old base carries Python and OpenSSL past their
  end of life. Runbook `docs/runbooks/upgrade-a-pinned-component.md`.

**Wave 2, after the pause ADR merges**

- **ClickHouse behind the profile `hot-gold`**, image line kept for `make test-clickhouse`; the
  compose summary comment, `scripts/check-budget.py` and its test, and an ADR-010 Outcome line
  carry the new figures.
- **Gates without ClickHouse**: `make gates` passes `--skip-clickhouse`; its two chaos scripts and
  their `Makefile` lines, the failure-mode rows, and screenshots 08 and 09 are removed.
- **No ClickHouse signals**: step (2) of `scripts/health.sh` loses its ClickHouse half; the six
  rules, their test file, the scrape job and the ClickHouse dashboard go, and the ClickHouse
  panels leave the pipeline overview.
- **Documents**: every operations document, runbook, chapter, `README.md`, `CLAUDE.md` and
  `docker/README.md` line that calls ClickHouse running, per the inventory; Avro doc strings are
  not touched (they change the registered schema). New runbook
  `docs/runbooks/pause-and-resume-a-serving-product.md`. `hot-gold` is declared paused in ODPS in
  phase `001`, not in the old format here (F20).

**Wave 3: the image goes live, then the old pipeline retires**, in this order (review section 2):

1. Switch `spark-iceberg` and `lake-ddl` to the new image. Prove it on the only real workload
   available: a scheduled `lake_ingest` run succeeds, then `make gates` passes on it.
2. Back up and pause. `make drop` mirrors first, after a check that the backup disk has room for
   the mirror. Pause `lake_ingest`, `lake_maintenance` and `dbt_marts`.
3. Drop the scratch objects (`lake.scratch_p4`, `lake.scratch_p4_pr1`, bucket
   `scratch-p4-landing`) and the old namespaces with `make drop`. The marts' definer views
   (`ohlcv_delayed_20m`, `dq_results`) go with them, so none survives into the new access rules.
4. Stand down, in one sweep with the ClickHouse stand-down because it is the same pattern on the
   same files: the 14 rules in `lake-alerts.yml` and their tests, the lake panels, and every gate
   that reads the old tables, each with a visible SKIP line naming the phase that restores it.
5. Archive to `legacy/v3-lake-ingest/`, with the README the legacy archive rule requires (what it
   did, why archived, what replaced it): the thirteen old-format contracts, the ingest stage
   code, the old DAGs and the old marts. Keep in place the modules the new connectors reuse:
   `offsets.py`, the framing half of `wire.py`, `bars.py`, `books.py`.
6. Move or retire every generic reader of the old names in the same pull request (F8): the
   README, `docs/operations/`, chapters, runbooks, notebooks, labs, scripts, tests, the
   `release-check` and `benchmark-report` skills, dashboards and screenshots. The replay export
   retires with the archive it reads and returns on the Kraken landing in phase `004`. Only
   files specific to Binance and Coinbase wait for the follow-on plan.
7. Record the Outcomes: ADR-034 (its ingest-duration trigger fired; resolved by retiring
   `lake_ingest`), ADR-050 (book data let go, so the owed `--with-paused` audit is not run, and
   why), and ADR-048's status line. The trades-only measurement day is dropped: the ingest it
   would measure is gone.

**Wave 4:** the phase audit through `/audit`, critical and high findings fixed, the tag, and the
phase-boundary update in Slack.

**Learning aids and documents.** Runbooks: pause and resume a serving product; upgrade a pinned
component; drop and rebuild. Chapter 20 is published by this documentation update.

## Verification

```bash
docker compose config --services | grep -cx clickhouse                   # prints 0
docker ps -a --filter name=k2-clickhouse -q                               # prints nothing
python3 scripts/check-budget.py                                           # 17.30 CPU / 29.812 GiB
docker run --rm <new spark tag> python3 -c "import sys, ssl; print(sys.version, ssl.OPENSSL_VERSION)"
for s in scripts/spikes/*/*/run.sh; do bash "$s" || echo "FAIL $s"; done # prints no FAIL
make drop TARGET=k2.scratch_p4 DRY_RUN=1                                  # lists objects, drops none
docker exec k2-airflow-scheduler airflow dags list                       # no lake_ingest, lake_maintenance, dbt_marts
docker exec k2-trino trino --execute "SHOW SCHEMAS FROM lake"            # only information_schema
git grep -l -E '\b(raw\.messages|audit\.checks)\b' -- ':!legacy' ':!docs/adr' ':!docs/audits' \
  ':!docs/benchmarks' ':!docs/research' ':!docs/plans' ':!docs/MIGRATION-JOURNEY.md' | wc -l   # prints 0
make gates                                                                # passes, a SKIP line per stood-down gate
make check-alerts
bash scripts/health.sh                                                    # exit 0, no ClickHouse line
make test
make check-docs
```

The image check prints Python 3.10 and OpenSSL 3; Ubuntu 22.04's standard support ends in April
2027, a dated trigger the hardening phase carries. The `make gates` run on the new image before
the drop, and the `make drop` log naming each mirror with its matching object count and bytes,
are linked from the pull requests that did them.

_Phase 000 landed 2026-11-26, tag `v3.0.0-alpha.2`. Where it differs from the text above: the
budget at the bootstrap peak is 16.70 CPU / 28.688 GiB, because `lake-ddl` and `lake-metrics`
retired with the pipeline they served; the scratch bucket `scratch-p4-landing` was copied but is
under object lock until 2026-11-27 06:00 UTC and is dropped after that, so the
`make drop TARGET=k2.scratch_p4 DRY_RUN=1` check has no target left; `SHOW SCHEMAS FROM lake`
prints `system` beside `information_schema`; `health.sh` prints a SKIP line for ClickHouse
rather than no line; the parity and lake-verify scripts were archived with the pipeline and
return in phase 004; the spikes added an experiment (g), the tested-data marker._
