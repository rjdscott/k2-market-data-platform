# Independent review of PR #239 (file-source pattern), 2026-11-26

## One HIGH, must be fixed before merge
`docker/lake/file_source.py:145-146`: fetch follows redirects. A 30x from the declared https URL to http (or to an internal host such as MinIO or Lakekeeper on the compose network) is followed. Consequences: a plaintext leg lets forged rows pass every generic audit and publish; and blind GETs from the Spark container to internal services, bodies landing in the landing bucket (SSRF). Fix: an opener whose redirect handler refuses any non-https target (and, better, any private address), with a test.
Medium: audit task has retries=0; a source scheduled during the 03:00 maintenance window waits 600 s for the writer lock, exits 75, and raises a false audit-failed alert.
Checked and sound: the LakeScript move, the publish guard (audit bound to table, hash and branch head; fast-forward under the lock), content-hash landing, lock behaviour, download cap 64 MiB and timeouts, TLS verification, XML parser safety, secrets, generator idempotence. After merge, `lake-ddl` must run before the nightly contract-schema audit.

## Where it is file-specific (for reuse as a generic connector)
- Unit of work is an S3 object (`--object` through load, audit, publish; `file_source.py:157-175, 321, 344, 364`); audit re-downloads and re-parses bytes. The seam should be a batch id plus rows.
- Lineage is a file hash everywhere (`LINEAGE` at :69, branch name :278, audit scope :282, snapshot property :71, `values_unchanged` :242).
- Load itself writes `bronze.source_files` from S3 metadata (:323-327).
- Parser input is bytes, tied to the contract's row and decimal checks (:185-201) and the expat check.
- Already generic apart from the hash argument: `to_stage`, `checks`, `unaudited`, `publish` (:214-379).
- Config mixes transport and table rules: `fileSource` in `contracts/schema/k2.schema.json:232`, generated spec `platform/k2gen/.../generate.py:519-531`, validation `cli.py:245-273` (bronze-only, url in upstreams).
- Factory hard-codes the script, the fetch-to-`--object` hand-off, four steps and the DAG id (`docker/airflow/dags/factory.py:30-59`).
- Every step builds landing credentials (:397); `JOB="file_source"` (:70) is the `audit.checks.job` value.
