# Runbook: A file source's audit failed

A file source's DAG, `<table>_ingest`, stopped at `load` or `audit` and did not publish
([ADR-036](../adr/ADR-036-file-sources-land-unchanged-then-publish-after-audit.md)). This
finds the failed check and the landed file, shows what the file would have changed, and then
rejects the file, re-runs it after a fix, or restores a landed file that changed. It does not
cover the Redpanda ingest's audits ([lake-ingest-batch-audit.md](./lake-ingest-batch-audit.md),
[lake-audit-failed.md](./lake-audit-failed.md)) or a fetch that cannot reach its publisher
(section 4).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.** Replace
`<table>` with the table's name without its namespace, `<sha256>` with the file's, and
`<key>` with its landed key, `<domain>/<product>/<fetch date>/<sha256>.<ext>`.

**Nothing self-heals, and nothing is lost.** A failed audit fails its task, so Airflow never
starts `publish`: `main` keeps its snapshot and every reader sees the last file that passed.
The file stays in the landing zone, its rows on branch `wap_<first 16 of sha256>`, its results
in `lake.audit.checks`. The next scheduled run fetches the publisher's file again; if that is
a corrected file, it has another fingerprint, another branch, and publishes on its own. The
failed branch stays until someone drops it (step 4a).

> No file source is declared yet. Every command here was run on 2026-11-26 against the
> synthetic test source in the scratch namespace `lake.scratch_p4_pr1` and the scratch bucket
> `scratch-p4-landing`, with those two names in place of `lake.bronze` and `k2-landing`, and
> the `file_source.py` flags `--config` and `--namespace-map bronze=scratch_p4_pr1,audit=scratch_p4_pr1`
> (the pull request that added this runbook has the transcript). The first real source runs
> them against `lake.bronze`.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | `audit` failed a check: the file is wrong, the check is wrong, or the publisher revised a value | < 30 min to a decision | steps 1 to 4a ran in 7 s on scratch tables (2026-11-26); the decision is a person's, not measured |
| 2 | `load` failed: the file does not parse | < 30 min to a decision | induced on scratch tables (2026-11-26); steps 1 and 2 of section 1 |
| 3 | `load` refused a landed file that no longer hashes to its key | < 15 min | induced and restored on scratch (2026-11-26), the restore under a minute |
| 4 | `fetch` failed: publisher unreachable, the file over 64 MiB, or a refused URL or redirect | next scheduled run; a refused redirect < 1 day to a decision | not induced; the refusals are unit-tested |

---

## 1. An audit check failed

**Symptom**, a red `<table>_ingest` run in the Airflow UI, its `audit` task failed and
`publish` never started. The task log ends with the check lines, one `FAIL`, and
`audit: FAILED <check>; main not moved, branch wap_<sha16> kept`.

**Detection**, `AirflowTaskFailed` from `docker/prometheus/rules/airflow-alerts.yml`, labels
`dag_id="<table>_ingest"`, `task_id="audit"`. An audit is never retried, so the alert follows
the first failure.

**Expected behaviour**, see above: nothing reaches `main`.

**Recovery**

1. Which checks failed, and for which file. The scope is `<table> <sha256> <branch snapshot id>`.

   ```bash
   docker exec k2-trino trino --output-format ALIGNED --execute "
     SELECT run_ts, check_name, observed, split_part(scope, ' ', 2) AS sha256,
            split_part(scope, ' ', 3) AS branch_snapshot, detail
     FROM lake.audit.checks
     WHERE job = 'file_source' AND scope LIKE 'bronze.<table> %' AND NOT passed
       AND run_ts > current_timestamp - INTERVAL '2' DAY
     ORDER BY run_ts DESC"
   ```

   What each check means is in ADR-036, section 4. `detail` quotes up to five problems and
   counts the rest; a product check's name is a function in `docker/lake/sources/<parser>.py`.

2. The file: where it came from, and its bytes.

   ```bash
   docker exec k2-trino trino --output-format ALIGNED --execute "
     SELECT url, fetched_at, landing_path, byte_size FROM lake.bronze.source_files
     WHERE sha256 = '<sha256>'"
   docker exec k2-minio sh -c 'export MC_HOST_k2="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000"; mc cat k2/k2-landing/<key>' | head -40
   ```

3. What the branch would publish that `main` does not hold, and confirm `main` did not move.

   ```bash
   docker exec k2-trino trino --output-format ALIGNED --execute "
     SELECT name, snapshot_id FROM lake.bronze.\"<table>\$refs\" ORDER BY name"
   docker exec k2-trino trino --output-format ALIGNED --execute "
     SELECT * FROM lake.bronze.<table> FOR VERSION AS OF 'wap_<sha16>'
     EXCEPT SELECT * FROM lake.bronze.<table>"
   ```

   `main` is the snapshot every reader sees; the branch's rows here are the file's new rows,
   and for `values_unchanged` the changed value beside the published one.

4. Decide, with the product's owner (`contracts/<domain>/<product>/product.yaml`):

   a. **The file is wrong** (truncated, corrupted, a publisher's error): reject it. Drop its
      branch; the landed file and its audit rows stay as the record. Under the writer lock,
      waiting up to 600 s for it:

      ```bash
      docker exec -e T=bronze.<table> -e B=wap_<sha16> -e K2_LAKE_DRIVER_MEMORY=512m k2-spark-iceberg python3 -c "
      import os, sys; sys.path.insert(0, '/home/iceberg/lake')
      from lock import LOCK_PATH, acquire_lock
      lock = acquire_lock(LOCK_PATH, wait_s=600)
      if lock is None:
          sys.exit(f'another lake writer held {LOCK_PATH} for 600 s: run this again')
      from spark_conf import CATALOG, lake_session
      s = lake_session('k2-file-source-drop-branch')
      s.sql(f\"ALTER TABLE {CATALOG}.{os.environ['T']} DROP BRANCH {os.environ['B']}\")
      print(s.sql(f\"SELECT name, snapshot_id FROM {CATALOG}.{os.environ['T']}.refs\").collect())
      s.stop()"
      ```

      It prints the table's references, `main` with the snapshot step 3 showed and no
      `wap_<sha16>`.

   b. **The check is wrong** (a parser or product-audit bug): fix it in a pull request, merge,
      then run the file again from its landed key. The load resets the branch, so this is safe
      to repeat:

      ```bash
      K='<key>'
      for step in load audit publish; do
        docker exec -e K2_LAKE_DRIVER_MEMORY=512m k2-spark-iceberg \
          python3 /home/iceberg/lake/file_source.py $step --table bronze.<table> --object "$K" || break
      done
      ```

      `publish` refuses unless the newest audit of the branch's current snapshot passed, so a
      skipped or stale audit cannot publish.

   c. **The publisher revised a published value on purpose**: the pattern never publishes a
      changed value (`values_unchanged`), and every later file that repeats the revision fails
      the same way. That is a decision about the product's contract for its owner, not a
      repair; reject each file as in (a) until it is made.

**Measured**, 2026-11-26, on the synthetic source in `lake.scratch_p4_pr1`. A file that changed
one published value, `1.2300` to `1.2310` for one key, staged only that row; its audit failed
`values_unchanged` with `observed` 1 and the detail `key 2026-01-05, AAA: published 1.2300,
this file 1.2310`, `publish` refused, and `main` kept snapshot `8513884295106788607` while the
branch held `8864279381361643437`. Steps 1 to 4a then ran in 7 s, and step 4a left `main` alone
at that snapshot. A file restating `40.5` as `40.50` and `1.2400` as `1.24` staged nothing for
them: decimals, not strings.

---

## 2. The file does not parse

**Symptom**, the `load` task failed; its log ends `load: s3://k2-landing/<key> does not parse;
nothing staged`. No branch exists for the file.

**Detection**, `AirflowTaskFailed`, `task_id="load"`, after its one retry.

**Expected behaviour**, the file is in the landing zone and in `bronze.source_files`, and
one `parses` row with `observed` 0 and the parser's error in `detail` is in
`lake.audit.checks`, scope `<table> <sha256> none`. `main` did not move.

**Recovery**, steps 1 and 2 of section 1 (step 1 shows the row with `branch_snapshot` `none`).
A publisher's broken file is rejected by doing nothing: there is no branch to drop. A parser
bug is 4b.

**Measured**, 2026-11-26, scratch: a file cut off in the middle of an element failed `parses`
at load with `ParseError: unclosed token: line 4, column 4`, scope snapshot `none`, no branch,
and its `bronze.source_files` row written.

---

## 3. A landed file no longer hashes to its key

**Symptom**, the `load` (or `audit`) task failed with `s3://k2-landing/<key> hashes to <x>,
not to its key: the landed file was changed after landing; nothing is read from it`.

**Detection**, `AirflowTaskFailed`.

**Expected behaviour**, nothing is read from the object. Something overwrote it outside the
pattern, which only a holder of the MinIO root credentials can do: fetch writes with a
conditional PUT that the store refuses for an existing key. The bucket is versioned, so the
landed bytes are the object's oldest version.

**Recovery**

```bash
mcx() { docker exec k2-minio sh -c "export MC_HOST_k2=\"http://\$MINIO_ROOT_USER:\$MINIO_ROOT_PASSWORD@localhost:9000\"; $1"; }
mcx "mc ls --versions k2/k2-landing/<key>"                  # the oldest PUT is the one that landed
mcx "mc cp --version-id <oldest version id> k2/k2-landing/<key> k2/k2-landing/<key>"
mcx "mc cat k2/k2-landing/<key>" | sha256sum                # must print <sha256>
```

Then run the file again from `load`, as in 4b. Find who wrote the object before anything else:
the landing zone is the verbatim record, and it was changed.

**Measured**, 2026-11-26, scratch: a landed object overwritten with nine bytes by `mc pipe`
became version 2; `load` refused it (`hashes to b2865fbc...`, not its key); `mc cp --version-id`
of version 1 made it hash to its key again, and `load` then ran clean, nothing new to stage.

---

## 4. The fetch failed

**Symptom**, the `fetch` task failed after two retries five minutes apart.

**Detection**, `AirflowTaskFailed`, `task_id="fetch"`.

**Expected behaviour**, nothing landed, nothing changed. The next scheduled run fetches again.
The task log has the error: an HTTP status or a network error from the publisher, or
`larger than 67108864 bytes, refused and not landed`, or a refusal that names only the
target's scheme and host, never its path, query string or credentials:

- `fetch refused source URL <scheme>://<host>: not https`, or `...: it resolves to non-global <address>`:
  the contract's own URL is plain http, or its host resolves to a loopback, private,
  link-local or reserved address. Nothing was requested.
- `fetch refused redirect to <scheme>://<host>: not https`, or `...: it resolves to non-global <address>`:
  the publisher answered with a redirect to plain http, or to an address inside a network,
  such as the object store or the catalog on the compose network. Nothing past the redirect
  was requested and nothing landed.
- `fetch refused redirect 6 to <scheme>://<host>: more than 5 redirects`: a redirect loop, or a
  chain longer than `MAX_REDIRECTS`.

**Recovery**, none for an unreachable publisher but waiting for the next run. A file over the
cap is a change to `MAX_BYTES` in `docker/lake/file_source.py`, in a pull request, after
checking the publisher meant it.

A refused redirect is not retried away and is not worked around: do not download the file by
hand into `k2-landing`, and do not loosen `require_public_https`. A redirect to plain http
lets anyone on the path serve rows that pass the audits; one to an internal address makes the
Spark container read an internal service and land its answer. Find out where the publisher's
URL goes, without following it, from a host outside the compose network:

```bash
curl -sS -o /dev/null -D - '<the contract fileSource url>' | grep -i '^location'
```

Without `-L`, curl prints the redirect and does not follow it (run on 2026-11-26 against a
public URL that redirects: it printed one `location:` line).

If the publisher moved the file to another https URL on a public host, change `fileSource.url`
in the table's contract to the new URL, `make generate`, and land it in a pull request; the next
run then fetches it with no redirect. If the redirect goes to plain http or to a private
address, the publisher is misconfigured or something on the path is rewriting answers: leave
the source failing and report it to the publisher. A source whose contract URL is refused is
fixed the same way, in its contract.

**Measured**, not induced on the stack; `tests/test_lake_file_source.py` drives each refusal
through the default opener with no network.

---

## Failure modes / incidents

None yet.

---

**Last verified:** 2026-11-26, sections 1 to 3 against the synthetic source in
`lake.scratch_p4_pr1` and `scratch-p4-landing` on the live stack, at the commit that added this
runbook.
