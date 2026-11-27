# Runbook: A file product's audit failed

Use this when a file product's DAG (`ecb_fx_rates`, `currency_master`) stopped at `audit`, or its
`load` was refused because an earlier batch waits unpublished. It finds the failed checks and the
landed file, decides between a bad file and a wrong check, clears the blocked batch and verifies
the next one publishes. Why bronze works this way, write, audit, publish in order on one branch:
[ADR-036](../adr/ADR-036-connectors-land-verbatim-then-publish-bronze-after-audit.md). A fetch that
failed or never started is [connector-failure.md](./connector-failure.md); a dbt build that failed
its tests is the wrapper's, [airflow-scheduling.md](./airflow-scheduling.md) section 2.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.** Placeholders:
`<dag>` the DAG id and `<fence>` the product's namespace (the same for the two file products:
`ecb_fx_rates`, `currency_master`), `<bronze>` its bronze table (`bronze_daily`,
`bronze_currencies`), `<domain>.<product>` its id (`fx-reference.ecb-fx-rates`), `<run>` the Airflow
run id. A batch's id is `<dag>/<run>`; every table and check named below is in the release's
`compiled/products/<domain>.<product>/pipeline.json`.

> **No file product has run on the live stack yet.** Every command below is written from
> [`docker/lake/connector.py`](../../docker/lake/connector.py) at this release, and the log lines
> quoted are its own strings; each runs for the first time after the deploy of this release.
> Plan 002's `scripts/chaos/fx-bad-file.sh` is the drill that will measure this page.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | `audit` failed a check on a staged batch | < 30 min to a decision | not yet measured: plan 002's `scripts/chaos/fx-bad-file.sh`, first run after this release deploys |
| 2 | The file did not parse (fetch exit 30) | < 30 min to a decision | not yet measured: the same drill |
| 3 | `load` refused: an earlier batch waits on `audit` | < 15 min after section 1 | not yet measured: the same drill |

---

## Reading the lake: who may, and how

The connector's tables are in warehouse `k2`, which Trino serves read-only as catalog `lake`. No
Trino principal holds a table rule on `lake.<fence>.audit_checks` or `lake.<fence>.landing_files`
(the release's `compiled/trino/rules.json`; `tests/access/expectations.yaml` holds a reader denied
`landing_files`), and Trino 484 refuses CREATE BRANCH, DROP BRANCH and FAST FORWARD to every
principal, the operator included
([`trino_rules.py`](../../platform/k2gen/k2gen/emit/trino_rules.py)'s docstring). So an operator
reads and resets them where the connector writes them: in the Spark container, as the Spark
engine's storage key (`K2_SPARK_S3_*`, already in that container), with the release's own
`spark_conf.lake_session`, the builder every Spark task uses. Define this once in the shell:

```bash
lake_sql() {  # one Spark SQL statement, its result printed whole
  docker exec -e Q="$1" -w /k2/releases/pointers/spark/platform/docker/lake k2-spark-iceberg python3 -c '
import os
from spark_conf import lake_session
s = lake_session("k2-operator")
s.sql(os.environ["Q"]).show(500, truncate=False)
s.stop()'
}
```

A driver starts for each call (seconds, not minutes), in the same container the DAG's Spark tasks
use: run these between runs, not during one. The run's raw bytes are in the spool, which the Spark
container mounts read-only at `/spool`: `/spool/<dag>/<run>/` holds `raw.<format>`, `fetch.json`,
and, when the parse succeeded, `rows.jsonl` and `audits.json`. Nothing removes a spool entry.

---

## 1. An audit check failed

**Symptom**, the DAG's run is failed: `audit` failed, `publish` and `build` are `upstream_failed`,
`expire` was skipped and `record` succeeded. The `audit` log has one line per check, `pass` or
`FAIL` with its detail, then

```text
audit: batch <dag>/<run> at audit snapshot <snapshot id>: FAILED <check>, <check>; main not moved
{"exit": 3, "task": "audit", "batchId": "<dag>/<run>", "table": "lake.<fence>.<bronze>", "snapshot": <snapshot id>, "failed": ["<check>"]}
```

and the task ends `audit exited 3`.

**Detection**, `AirflowTaskFailed` (`docker/prometheus/rules/airflow-alerts.yml`), labels
`dag_id="<dag>"`, `task_id="audit"`. Exit 3 is never retried (`NO_RETRY` in
`docker/airflow/dags/factory.py`), so the alert follows the first failure. **No alert is specific to
an audit yet**: the per-product alerts are the
[to-release plan's phase 002](../plans/2026-11-26-unified-product-platform-to-release/002-controls-alerts-release-engineering.md).

**Expected behaviour**, nothing self-heals and nothing is lost:

- the file is landed at `s3://k2-landing/<domain>/<product>/<fetch day, UTC>/<sha256>.<ext>`, never to
  be overwritten, with its row in `lake.<fence>.landing_files`;
- bronze `main` did not move, and no dbt output was built from the batch, so every reader sees the
  last published data;
- branch `audit` of the bronze table holds the batch's rows, and **the next file waits behind it**:
  the next run that fetches new bytes lands its file and its `load` is refused (section 3);
- the same bytes again are not retried: fetch compares with its newest earlier fetch and exits 10,
  unchanged, so load, audit and publish are skipped and build runs on the published data;
- the daily maintenance keeps the failed batch's landed file (counted `unpublished`,
  [`maintain.py`](../../docker/lake/maintain.py)) until an operator clears the batch.

**Recovery**

1. **Which checks failed.** The checks are `parses`, `non_empty`, `row_count`, `keys_unique`, one
   `in_domain:<column>` per declared pattern, `values_unchanged` (no published key with other
   values) and each parser audit by name (`check_names` in `connector.py`).

   ```bash
   lake_sql "SELECT check_name, passed, detail, snapshot_id, checked_at
             FROM lake.<fence>.audit_checks WHERE batch_id = '<dag>/<run>' ORDER BY checked_at, check_name"
   ```

   A parser audit's detail is `<n> failing rows: <the first five>`, as the parser's function in
   `domains/<domain>/parsers/<parser>.py` described them.

2. **The file.** Where it came from, and its bytes:

   ```bash
   lake_sql "SELECT object_key, sha256, bytes, url, final_url, http_status, fetched_at, landed_at, parsed
             FROM lake.<fence>.landing_files WHERE batch_id = '<dag>/<run>'"
   docker exec k2-spark-iceberg ls -l /spool/<dag>/<run>/
   docker exec k2-spark-iceberg cat /spool/<dag>/<run>/fetch.json /spool/<dag>/<run>/audits.json
   docker exec k2-spark-iceberg head -c 4000 /spool/<dag>/<run>/raw.<format>
   ```

3. **What `main` holds and what waits.** `main` is what readers' builds read; `audit` is one
   snapshot ahead with the batch:

   ```bash
   lake_sql "SELECT name, type, snapshot_id FROM lake.<fence>.<bronze>.refs"
   lake_sql "SELECT snapshot_id, committed_at, summary['k2.batch-id'] AS batch
             FROM lake.<fence>.<bronze>.snapshots ORDER BY committed_at DESC LIMIT 5"
   lake_sql "SELECT * FROM lake.<fence>.<bronze>.branch_audit
             WHERE source_file_sha256 = '<sha256 from step 2>' LIMIT 50"
   ```

4. **Decide**, with the product's owner (`contracts/<domain>/<product>/product.odps.yaml`):

   a. **The file is bad** (truncated, a publisher's error, values out of their domain): discard the
      batch from `audit` (step 5). Its landed file and its `audit_checks` rows stay as the record. The
      next file the publisher serves publishes on its own; until it changes, fetch skips the same
      bytes.

   b. **The check is wrong** (a pattern in the bronze contract, a parser audit, a key): fix it in a
      pull request with a synthetic fixture that the old check failed
      ([onboard-a-file-product.md](./onboard-a-file-product.md) names the tests), tag, `make release`
      and `make deploy` ([deploy-a-release.md](./deploy-a-release.md)). Then audit and publish the
      held batch again on the new release, by hand: the DAG's own tasks would rerun on the release
      the run pinned at its start, with the old check.

      ```bash
      docker exec k2-airflow-scheduler airflow dags pause <dag>
      R=$(basename "$(readlink "${K2_RELEASES:-$HOME/k2-releases}/pointers/spark")")
      for t in audit publish; do
        docker exec k2-spark-iceberg python3 "/k2/releases/$R/platform/docker/lake/connector.py" "$t" \
          --release "$R" --product <domain>.<product> --run-id <dag>/<run> || break
      done
      docker exec k2-airflow-scheduler airflow dags unpause <dag>
      ```

      `publish` refuses unless the batch's newest audit at `audit`'s current head passed every check
      of the new release (`publish refused at audit snapshot <id>: ...; main <id> not moved`), so a
      stale audit cannot publish. A parser audit is the exception: the connector records it from
      `audits.json`, which fetch wrote with the old parser, so a fixed parser audit takes effect from
      the next fetched file; for the held batch, take (a). A batch published by hand has no run
      record of its own; the next run's build reads it, and `airflow dags trigger <dag>` runs that
      build now (fetch skips the same bytes, build runs).

   c. **The publisher revised a published value on purpose** (`values_unchanged` failed): the pattern
      never publishes a changed value, and every later file that repeats the revision fails the same
      way. That is a decision about the product's contract for its owner, not a repair; meanwhile
      discard each such batch as in (a).

5. **Clear the blocked batch: reset `audit` to `main`.** This is the statement the connector itself
   runs when `audit` is behind `main` (`reset_branch` in `connector.py`), here with `main`'s snapshot
   id from step 3, as the Spark engine's key. Pause the DAG first so no load runs between the read
   and the reset:

   ```bash
   docker exec k2-airflow-scheduler airflow dags pause <dag>
   lake_sql "SELECT name, snapshot_id FROM lake.<fence>.<bronze>.refs"
   lake_sql "ALTER TABLE lake.<fence>.<bronze> REPLACE BRANCH audit AS OF VERSION <main snapshot id>"
   lake_sql "SELECT name, snapshot_id FROM lake.<fence>.<bronze>.refs"
   docker exec k2-airflow-scheduler airflow dags unpause <dag>
   ```

   The second `refs` read shows `audit` at `main`'s snapshot id. If `refs` lists no `main` (the
   product's first batch failed, so nothing was ever published), drop the branch instead; the next
   load creates it again from `main`:

   ```bash
   lake_sql "ALTER TABLE lake.<fence>.<bronze> DROP BRANCH audit"
   ```

   The discarded snapshot is no longer referenced; the daily maintenance expires it by the table's
   retention.

6. **Rerun and verify.** A batch that waits behind the one cleared (section 3) is released by
   clearing its own `load`. Otherwise wait for the next scheduled run, or start one:

   ```bash
   docker exec k2-airflow-scheduler airflow dags trigger <dag>
   docker exec k2-airflow-scheduler airflow dags list-runs <dag> -o json
   lake_sql "SELECT name, snapshot_id FROM lake.<fence>.<bronze>.refs"
   ```

   A new file prints `load: <n> rows staged on audit of lake.<fence>.<bronze>, snapshot <id>`, an
   audit ending `passed`, and `publish: main <old> -> <new>, batch <dag>/<run>`; afterwards `main`
   and `audit` name the same snapshot. The same bytes print `fetch: unchanged, sha256 <sha256>` and
   move nothing.

**Measured**, not yet measured: plan 002's `scripts/chaos/fx-bad-file.sh` will induce it.

---

## 2. The file did not parse

**Symptom**, `fetch` is green with the warning `k2-fetch-<dag>-<try> exited 30`, its log has
`fetch: parse failed, the bytes are kept: <exception>: <message>`; `load` prints `load: landed
s3://k2-landing/<key>, <n> bytes, parsed False` and `load: the parse failed, so nothing is staged;
the audit fails the batch`; `audit` fails with the single row `parses`, detail `fetch could not
parse the file; its bytes are landed`.

**Detection**, `AirflowTaskFailed`, `task_id="audit"`.

**Expected behaviour**, the file is landed and logged with `parsed` false. Nothing was staged, so
`audit` still names `main`'s snapshot and **the next file does not wait**: its load stages as usual.
The spool entry holds `raw.<format>` and `fetch.json`, no `rows.jsonl` and no `audits.json`.

**Recovery**, steps 1 and 2 of section 1, then decide:

- **The file is bad**: nothing to clear. The next changed file publishes.
- **The parser is wrong**: fix `domains/<domain>/parsers/<parser>.py` with a synthetic fixture of the
  shape that failed, tag, release, deploy. There is no command to parse the same bytes again: fetch
  skips bytes equal to its newest earlier fetch, so the fixed parser applies from the publisher's
  next changed file.

**Measured**, not yet measured.

---

## 3. A later file waits behind a failed batch

**Symptom**, `load` failed on its first try; its log ends

```text
load: landed s3://k2-landing/<key>, <n> bytes, parsed True
load refused: audit of lake.<fence>.<bronze> holds batch <the earlier batch>, unpublished; this batch waits behind it (its file is landed)
```

and the task ends `load exited 2`.

**Detection**, `AirflowTaskFailed`, `task_id="load"`.

**Expected behaviour**, this run's file is landed and logged (the landing happens before the
staging), nothing of it is staged, and `main` did not move. Bronze publishes in order: a failed
batch blocks the next, never its landing.

**Recovery**, section 1 for the batch the message names, through step 5. Then rerun this run's
`load` and everything after it, on its own pinned release and spool entry: in the Airflow UI, open
this run, select `load`, **Clear** with **Downstream** checked. If several runs wait, clear them
oldest first, one at a time: each publishes before the next stages. A new trigger would not do it:
its fetch sees the waiting file's bytes as its newest earlier fetch and exits 10.

**Measured**, not yet measured.

---

## Failure modes / incidents

- None yet.

---

**Last verified:** not yet: first run after the deploy of this release. Written from
`docker/lake/connector.py`, `docker/fetch/fetch.py` and `docker/airflow/dags/factory.py` at this
release; the connector's flows are unit-tested in `tests/test_lake_connector.py`.
