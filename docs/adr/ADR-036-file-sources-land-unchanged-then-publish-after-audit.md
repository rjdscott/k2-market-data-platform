# ADR-036: File sources land unchanged, then publish only after an audit

**Status:** Proposed
**Date:** 2026-11-26
**Author:** Platform
**Category:** Ingestion / Data quality

---

## In plain words

Some data reaches the platform as a file a publisher puts on a web site, such as a list of
exchange rates, not as a stream of messages. We will keep every downloaded file exactly as it
arrived, named by a fingerprint of its content, and never overwrite it; then load it into a
hidden copy of its table, check it there, and make it visible only if every check passes. A bad
file then waits, invisible, for a person, while readers keep seeing the last good version. The
cost is one storage bucket, one small job and four scheduled tasks per source, and a rule that a
value once published never changes. The owner decides whether to accept it.

---

## Context

### What a file source is, and why it is not the stream

Everything the lake holds today arrives through Redpanda, a message bus: the captures publish
each venue message, and the ingest copies a range of them into the lake every five minutes. The
bus deletes old messages on its own schedule, so the ingest must commit what it read promptly,
and it checks a batch only after committing it
([ADR-047](ADR-047-ingest-batch-audit-after-publish.md)): holding a batch back would stop the
ingest while the bus kept deleting unread messages.

A **file source** is different. A publisher serves a file at a URL, and the platform downloads it
on a schedule, under Airflow, the scheduler, where each scheduled workflow of tasks is a **DAG**: the European Central Bank's daily reference rates and the ISO 4217 currency list
are the first two ([plan P4](../plans/2026-11-24-governed-self-serve-platform/004-fx-data-product.md)).
The publisher keeps the file, and a day's rates are only needed by the next day. So a file
that fails a check can wait, unpublished, for as long as a person takes to look at it. That
is the property this decision is built on.

### What a file source needs

- **A verbatim record.** The bytes as downloaded, kept, so any load can be repeated and any
  value traced back to the file it came from. The place those bytes are kept is the
  **landing zone**: for the bus it is `raw.messages`, for file sources a bucket in MinIO, the
  S3-compatible object store the lake already lives in.
- **Repeatable loads.** The ECB's 90-day file overlaps the previous day's in 89 of its days, and
  a retried task loads the same file twice. Loading a file again must add nothing.
- **Formatting is not change.** The same rate is written `137.20` in one ECB file and `137.2` in
  another (plan P4), so values must be compared as numbers, never as text.
- **No bad file is ever visible.** A reader should see the last file that passed its checks, or
  nothing, never a half-checked one.
- **No network in tests**, and nothing outside the standard library in the Spark container: its
  image has no package-install layer (`docker exec k2-spark-iceberg python3 -c "import boto3"`
  prints `ModuleNotFoundError`, 2026-11-26). Its Python is 3.10.16 with expat 2.6.4, the XML
  parser library the standard library bundles.
- **One writer at a time.** Every Spark job that writes the lake holds one file lock, the
  [writer lock](glossary.md#writer-lock-and-exit-75), and the ingest gives up (exit 75) if it
  is held.

### The Iceberg mechanisms this uses

An Iceberg table is a sequence of [snapshots](glossary.md#snapshot): each commit publishes a new
complete version, and readers see one version or the next, never half of one. A table has named
references to snapshots. **`main`** is the one every reader sees. A **branch** is another named
line of snapshots: commits to it are invisible to `main` readers
([branch and main](glossary.md#branch-and-main)). To **fast-forward** `main` to a branch is to move
`main` to the branch's newest snapshot in one atomic catalog update, so readers gain exactly what
the branch holds, with each snapshot's summary properties intact
([fast-forward](glossary.md#fast-forward)). Together these make **write-audit-publish**: write
where readers cannot see, audit there, publish only if the audit passed
([write-audit-publish](glossary.md#write-audit-publish)). Every reader in the repository already
resolves the snapshot `main` points at rather than the newest snapshot by time
([#214](https://github.com/rjdscott/k2-market-data-platform/pull/214), `offsets.main_lineage`),
which is what makes a branch safe to have.

### What was tested before deciding

Each row was run on the live stack on 2026-11-26, in the scratch bucket `scratch-p4-landing`
and the scratch namespace `lake.scratch_p4_pr1` only; the commands and their output are in this
decision's pull request.

| Question | Result |
|---|---|
| Does a write to a branch of a table with no snapshot work, and can `main`, which does not exist yet, be fast-forwarded to it? (Spark 3.5.5, Iceberg 1.8.1, Lakekeeper) | Yes. `CREATE OR REPLACE BRANCH` on the empty table made an empty snapshot for the branch; the write landed on the branch with `main` still empty; `CALL lake.system.fast_forward(..., 'main', 'b1')` returned `previous_ref=None` and `main` then read the row |
| Does an empty append to a branch make a commit? | Yes, a snapshot with no added records. So the load appends only when it has rows |
| Does a versioned bucket with object lock refuse an overwrite, as the plan assumed? | **No.** `scratch-p4-landing` has versioning on and object lock in GOVERNANCE mode for one day. A plain PUT to an existing key returned 200 and made a second version, and a GET then returned the new bytes. The earlier spike's listing of that bucket shows the same key overwritten with different bytes and deleted (a delete marker) |
| Does MinIO (`RELEASE.2025-09-07T16-13-09Z`) refuse a conditional PUT to an existing key? | **Yes.** A PUT with `If-None-Match: *` to a new key returned 200; to the same key, with the same bytes or different ones, 412 Precondition Failed, and the object kept its first bytes and metadata. On the key the plain PUT had overwritten, 412 as well |
| Does the request signing written with the standard library work? | Yes: it reproduces the AWS Signature Version 4 test suite's `get-vanilla` signature (`tests/test_lake_file_source.py`) and MinIO accepted every request above |
| Does the bundled expat refuse entity expansion? | Yes: a nine-level nested entity ("billion laughs") file fails to parse with expat's amplification limit (`tests/test_lake_file_source.py`) |
| Does the whole pattern work on the stack? (synthetic source, scratch tables) | Yes. A good file published (`main` from none to its snapshot); fetched and loaded again, it wrote nothing and staged nothing; a file that changed one published value failed `values_unchanged`, the publish was refused and `main` kept its snapshot id; a truncated file failed `parses` at load; Trino, through its read-only catalog, read `main` and, with `FOR VERSION AS OF '<branch>'`, the branch |
| Is a publish bound to what its audit read? | Yes, after a change made during this test: a file loaded again after its audit has a new branch snapshot, and publish refused it ("no audit of this file at this branch snapshot") until it was audited again |
| Is a landed file that changed detected, and can it be put back? | Yes: overwritten outside the pattern, the object no longer hashed to its name and the load refused it; copying its first version back made it load |
| Do generated table definitions apply? | Not as first written: the first ever generated table failed in Spark, because Spark SQL has no `''` escape for a quote inside a string. `k2gen validate` now refuses a quote in generated comment text |

The plan's sentence "the landing bucket is versioned and locked, so a landed file cannot be
altered" is therefore wrong as written. Versioning keeps the old bytes as an older version; it
never refuses a write, so the key a loader reads can be made to return other bytes. Object lock
protects each version from deletion for its retention period, and GOVERNANCE mode lets a user
with the bypass permission remove even that (not tested here). Neither stops the current
content of a key changing. The plan file is immutable, so it keeps the sentence; this record
corrects it.

---

## Decision

**We will ingest every file source in four steps, the same for every source: land the file
unchanged under its content hash with a write the store refuses if the name exists, load it
onto an Iceberg branch, audit the branch, and fast-forward `main` only after that audit passed;
because a file source's publisher keeps the file, so a bad file can wait for a person while
readers keep the last good snapshot.** Scope: tables whose contract declares `fileSource`. The
Redpanda ingest keeps [ADR-047](ADR-047-ingest-batch-audit-after-publish.md).

### 1. A source is declared in its contract, and its DAG is generated

A file source is a new [bronze](glossary.md#raw-bronze-silver-gold) table (bronze holds what a source
sent, typed and nothing more), so its contract declares its columns inline
([ADR-032](ADR-032-odcs-contracts-and-k2gen.md)), and adds one custom property, `fileSource`:
`url` (HTTPS only, and also one of the table's `upstreams`, so its product declares it as an
external input with a classification), `format` (the landed file's extension, `xml` only today),
`calendar` (the days the publisher publishes on, `none` only today: every scheduled run fetches),
`parser` (the module that reads the file), and `audits` (the product's own checks). The schedule
is the contract's existing `schedule`. Three things the audits need come from the standard
column fields of the Open Data Contract Standard rather than new properties: the **key** is the
columns marked `primaryKey`, in `primaryKeyPosition` order; the **numbers** are the columns of
`logicalType` `number` or `integer`, which are compared as decimals; and each column's
**domain**, the set of values it may hold, is its `logicalTypeOptions.pattern`. Every row carries
`source_file_sha256`, the fingerprint of the file it came from.

`k2gen validate` refuses a file source that is not a new bronze table, lacks that column or a
key, has a column type the audits cannot compare (a timestamp, for one: Python reads Spark
timestamps in the local zone), a pattern Python cannot compile, a schedule that is not a cron,
or a DAG id a hand-written DAG has. A cron schedule is the five-field `minute hour day month
weekday` form. `make generate` writes two configurations, because two
containers read them: `docker/lake/generated/file_sources.json` for the job and
`docker/airflow/dags/file_sources.json` for Airflow. One factory,
`docker/airflow/dags/factory.py`, builds a DAG per source from the second, named
`<table>_ingest`. No contract declares a file source yet, so both files are `{}` and the
factory builds nothing; a synthetic source used only by tests holds the shape.

### 2. fetch: land the bytes, never over other bytes

The fetch task downloads the URL with the standard library (at most 64 MiB), computes the
**sha256** of the bytes (a 64-character fingerprint that changes if any byte does), and writes
them to bucket `k2-landing` at

    <domain>/<product>/<fetch date>/<sha256>.<ext>

with a **conditional PUT**: an S3 write carrying the header `If-None-Match: *`, which asks the
store to write only if no object of that name exists, and to answer 412 Precondition Failed
otherwise. Because the name contains the fingerprint, an existing name means the same bytes
already landed that day, so a 412 is a no-op and the task carries on; and no write through this
path ever replaces a landed file. The object carries the URL and the fetch time as metadata.
The bucket is versioned as well, so an overwrite that bypasses this path keeps the original
bytes as an older version. The requests are signed with AWS Signature Version 4 in about
twenty lines of the standard library (`file_source.sign`). The task prints the landed name
last, and Airflow hands it to the next three tasks as the fetch task's return value, through
XCom, Airflow's store for small values one task passes to the next.

### 3. load: verify, record, parse, stage what is new

The load task takes the writer lock, waiting at most 600 seconds (two ingest cycles), and exits
75 for its retry if it cannot. It reads the object back and refuses it if its bytes no longer
hash to its name: a landed file that was changed is detected, not loaded. It records the file
in `bronze.source_files` (domain, product, URL, fetch time, sha256, landing path, size), one row
per landed file whatever happens next. It parses the bytes with `sources/<parser>.py`, whose one
required function takes bytes and returns the table's rows; parsers use only the standard
library, after a check that the bundled expat is 2.4.1 or newer, the first release that refuses
entity expansion past a fixed amplification. A file that does not parse is a failed `parses`
check and the task fails.

It then resets the branch `wap_<first 16 of the sha256>` to `main` and appends to it only the
file's rows that `main` does not already hold **with the same key and the same values**, numbers
compared as decimals. Loading the same file again stages nothing; a later file stages only its
new rows; a file that restates a published rate as `137.2` stages nothing for it; and a file
that changes a published value stages the changed row, which the audit then sees.

### 4. audit: check the branch, record every result

The audit task parses the file again, reads the branch, and runs the generic checks, then the
product's own, and files every result, passed or failed, in `lake.audit.checks` in one commit,
`job = 'file_source'`, `scope = '<table> <sha256> <branch snapshot id>'`, the snapshot id being
the branch's newest snapshot as the audit read it:

| Check | Fails when |
|---|---|
| `parses` | the file does not read as the table's rows, or a number column holds something that is not a finite decimal |
| `non_empty` | the file holds no rows |
| `keys_unique` | a key appears twice in the file |
| `in_domain` | a value does not match its column's declared pattern |
| `values_unchanged` | the file gives a key that another file already published a different value, compared as decimals |
| `staged` | a row of the file is not on the branch as parsed: the load did not write what the file says |

A product audit is a function in the parser module, from the parsed rows to a list of problems.
Any failure fails the task. Airflow then never starts the publish task, the branch stays for
inspection, `main` does not move, and `AirflowTaskFailed` fires
([runbook](../runbooks/file-source-audit-failed.md)). An audit is never retried: a failed audit
is a finding, not a blip.

### 5. publish: fast-forward, only what an audit passed

The publish task reads the file's audit rows at the branch's current snapshot and refuses
unless the newest of them passed every generic check and every declared product check. Binding
the audit to the snapshot means a branch loaded again after its audit is refused until audited
again. Then it fast-forwards `main` to the branch and drops the branch. Fast-forward, never cherry-pick: a cherry-pick writes a copy that loses the
snapshot's `k2.*` properties ([fast-forward](glossary.md#fast-forward)). Plan P4's "then merge to
silver" arrives with its first silver table in pull request 2.

---

## Rationale

Every property in the context section falls out of two choices. Naming a file by its own
fingerprint makes "the same file" decidable by the store, so a conditional write gives both the
no-op re-fetch and the refusal to overwrite in one request, and the load can prove the bytes it
reads are the bytes that landed. A branch makes "not visible yet" a property of the table
itself, so the audit reads exactly what a publish would expose, and the publish is one atomic
pointer move whose failure leaves `main` where it was. Staging by key and decimal value is what
makes a reload a no-op and a restated value not a change, without a merge statement: a SQL
`MERGE` carries no snapshot properties, and Spark's form of it rewrites more than it must
([research note](../research/2026-11-26-incremental-bars-recompute.md), section 8).

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| A versioned, object-locked bucket as the guarantee, as the plan proposed | Tested: it accepts an overwrite and a delete of the current object; it only keeps older versions, for the lock's retention, and GOVERNANCE mode can be bypassed. It is kept as a second line (versioning only), not as the guarantee |
| Load straight to `main` and audit after, as the ingest does (ADR-047) | Readers would see a bad file until someone repaired it. The ingest accepts that because the bus deletes what it holds back; a file source has no such pressure |
| A staging table per source, then `INSERT` or `MERGE` into the real one | Two tables per source, and the publish becomes a second write that the audit never saw; `MERGE` loses the snapshot properties |
| Publish a branch by cherry-pick | The copy on `main` drops the `k2.*` summary properties every resuming reader depends on |
| An S3 client library in the Spark or Airflow image | The Spark image has no package layer, and adding one for two requests is a new dependency to pin and patch; the standard library signer is checked against the AWS test suite and MinIO |
| A hand-written DAG per source | The four tasks are identical for every source; a factory makes the contract the only file a source team writes |
| Keep the file in a table, as a binary column | Files of kilobytes to megabytes in Parquet, rewritten by compaction, and no longer the verbatim object a person can download; the table keeps the index, the bucket the bytes |

---

## Consequences

**Easier:** a new file source is a contract, a parser module and a test; the DAG, the landing,
the audits and the publish come with it. Every value in a file-source table names the file it
came from, and every file has a row saying where it lies. A bad file is caught before any reader
sees it, and the evidence (the file, the branch, the audit rows) stays.

**Harder:** each run starts three Spark drivers (load, audit, publish), each holding the writer
lock for seconds, so an ingest tick that lands on one exits 75 and is skipped; a source scheduled
inside the 03:00 UTC maintenance waits up to 600 seconds per try and then fails its retries. A
failed branch stays until a person drops it. A publisher that legitimately revises a published
value fails `values_unchanged` every run until the product's owner decides what a revision means.

**Committed to:** the landing key layout and bucket name, the lineage column, the scope format
of a file source's audit rows (the publish guard reads it), and the rule that a published value
never changes.

**Risks:** the load and the audit read the whole table into the driver; at a 512m heap that is
fine for a reference source of a few hundred thousand rows and not for a large one. The landing
bucket has root credentials in the Spark container, like the lake's own bucket: a process with
them can still delete a version, and only the fingerprint check at load would notice a changed
object. The nightly backup (`scripts/backup-lake.sh`) does not copy the landing bucket.

**What is not yet verified:** no real source has been fetched over the internet from the Spark
container; the generated DAG has run only as unit tests and as the four commands run by hand
against scratch tables, not under the scheduler; the bucket `k2-landing` and the table
`bronze.source_files` do not exist on the live stack until `lake-init` and `lake-ddl` run again
after this merges, and until then the nightly `contract_schema` audit reports
`bronze.source_files` missing; seeding the landing zone from committed samples, so a fresh clone
builds every product without the network, arrives with the first product's samples (pull
request 2); the calendar has no value but `none`.

**Revisit when:** a file-source table passes one million rows (the whole-table read), the first
source whose publisher revises a published value, a second process that writes the landing
bucket, or the object store changes from MinIO, whose conditional-write behaviour is what was
tested.

---

## References

- [Plan P4, pull request 1](../plans/2026-11-24-governed-self-serve-platform/004-fx-data-product.md)
  and [ADR-031](ADR-031-governed-self-serve-platform.md), which reserved this number.
- [ADR-032](ADR-032-odcs-contracts-and-k2gen.md) (contracts and k2gen),
  [ADR-034](ADR-034-airflow-3-replaces-prefect.md) (Airflow and the `docker exec` dispatch),
  [ADR-035](ADR-035-trino-and-dbt-for-marts.md) (Spark writes the lake),
  [ADR-047](ADR-047-ingest-batch-audit-after-publish.md) (why the stream audits after publishing).
- `docker/lake/file_source.py`, `docker/lake/sources/`, `docker/airflow/dags/factory.py`.
- [Runbook: a file-source audit failed](../runbooks/file-source-audit-failed.md).
- Amazon S3 conditional writes, `If-None-Match`:
  <https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html>.
