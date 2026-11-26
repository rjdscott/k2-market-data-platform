# ADR-053: Recoverable deletes, and guards against the catalog's word

**Status:** Proposed
**Date:** 2026-11-26
**Author:** Platform
**Category:** Storage / Security
**Decided by:** the owner on 2026-11-26, in outline (security and guard fixes before phase P4; an
independent review before acceptance). Split from the first draft of
[ADR-052](ADR-052-backup-and-restore-of-irreplaceable-tables.md) by the review of 2026-11-26, whose
findings B1, B2, S1 and S2 this answers. It moves to Accepted when the owner accepts it.
**Depends on:** ADR-052's copy and its desktop notice, which land first.

---

## In plain words

The lake's catalog lets anyone on this machine move any table's pointer, rename or drop tables,
or switch a warehouse off, with no password, and the platform's own nightly maintenance then
deletes whatever files the catalog says are no longer needed. We will keep every deleted file in
storage for a fixed window (2 days now, 14 once order-book capture is paused), refuse ordinary
drops of the irreplaceable tables, and make maintenance and the ingest check each of those tables
against identities pinned on the host and record counts the catalog cannot forge before they
delete or read anything, so a forged instruction stops them and raises a desktop notice. We will
also give the catalog database its own login and a strong superuser password, refuse guessable
credentials at start, and switch off the Spark image's unauthenticated servers. It costs about
23 GiB of kept files now (17 GiB once paused), about a second per ingest run, and short restarts in
three paused windows. The catalog still cannot tell callers apart; what it can still do, and how
each is undone, is listed in part 9.

---

## Context

Terms are explained where they first appear; the [glossary](glossary.md) has each one too.

### The catalog answers anyone, and the pointer is the table

Lakekeeper, the Iceberg catalog, holds one pointer per table to its current `metadata.json`, the
file that lists every **snapshot** (one version of the table) and leads to its data files
([glossary](glossary.md#registering-a-table-and-the-metadata-chain)). Whoever moves the pointer
changes the table for every reader. Lakekeeper's authorization backend is `allow-all`
(`GET /management/v1/info`, 2026-11-26), so every caller is an anonymous administrator: any process
on this host through `127.0.0.1:18181`, and any container on the Docker network. It cannot be
anything else without an identity provider: Lakekeeper authenticates only through OpenID Connect
or Kubernetes tokens and issues no keys of its own
([Lakekeeper, Authentication](https://docs.lakekeeper.io/docs/latest/authentication/)), and its
other backend, OpenFGA, decides by an authenticated user. Through the Iceberg REST protocol such a
caller can commit `set-snapshot-ref` (point `main` at an old snapshot), `remove-snapshots`,
`add-snapshot` (a new snapshot whose summary, a map of strings, says anything), `set-properties`
and `set-location`; rename, register (`"overwrite": true` replaces a pointer), drop and force-drop
tables and namespaces; deactivate a warehouse or remove its storage credential; and run a pending
expiration now. All six warehouses delete hard today: a dropped table's files are purged at once
(`GET /management/v1/warehouse`, `delete-profile` `hard`).

### The platform's own jobs act on the catalog's word

- **Maintenance** (`docker/lake/maintenance.py`, nightly at 03:00 UTC, under the **writer lock**:
  one exclusive file lock every Spark job that writes the lake takes, so only one runs at a time)
  compacts, expires snapshots older than 7 days and removes orphan files, files under a table's
  location that no snapshot references ([glossary](glossary.md#orphan-files)). Every one of those
  decisions is read from the catalog. A commit that points `raw.messages`' `main` at an old snapshot
  and removes the newer ones deletes no object, so no storage refusal is ever tested; at 03:00 the
  newer files are unreferenced, and `remove_orphans()` deletes them with the key maintenance holds.
  A commit that sets a derived table's location to raw's prefix makes that table's orphan removal
  list raw's files as its own orphans. Whether Lakekeeper refuses such a location change is not
  verified; maintenance must not depend on it.
- **The ingest** (`docker/lake/ingest.py`, every 5 minutes) resumes from `k2.kafka-offsets`, the
  per-partition end offsets in its newest snapshot's summary
  ([exactly-once](glossary.md#exactly-once-via-snapshot-offsets)). Offsets above what Redpanda holds
  are read as "the topic was recreated; the honest read is an empty one" (`bounded_offsets` in
  `docker/lake/offsets.py`), so a forged summary makes it read nothing, silently, while Redpanda
  evicts what it never read: the busiest partition holds 13.2 hours at full capture (ADR-052's
  measurement). A forged offset inside the held range skips records until the nightly continuity
  audit, by when that partition may have evicted them.
- **`docker compose up`** runs `lake-ddl`, which creates any lake table that is missing. Rename
  `raw.messages` through the catalog, run `up`, and an empty `raw.messages` appears, with a new
  identifier; anything that finds the irreplaceable tables by name now protects the empty one.

### Other doors

- The PostgreSQL superuser, which Lakekeeper connects as, has a password equal to its user name, and
  every container can log in with it over the network (compared in-process, booleans only).
  `scripts/check-env.py` does not refuse that, and a bare `docker compose up` skips it anyway.
- The Spark image's entrypoint (`/opt/spark/entrypoint.sh`, read with `docker cp`) starts a Spark
  master on port 7077, a worker, a Thrift SQL server and a Jupyter server with no token, as root,
  in the container that holds the storage keys (`docker top k2-spark-iceberg`); any container on
  the network can submit code to the master or the notebook. K2's jobs run as local drivers and use
  none of them.

### What keeping deleted files costs

On 2026-11-25, at full capture, the lake's compactions and overwrites removed 11.62 GiB of files,
which expiry deletes for real seven days later (Trino, `removed-files-size` summed over every lake
table's `$snapshots` by day). The VM disk had about 101 GiB free after the nightly compaction
(ADR-052). With [ADR-050](ADR-050-order-book-capture-paused-during-platform-build.md)'s order-book
pause in effect, about 90% of those bytes stop, an estimate from ADR-050's shares.

---

## Decision

**We will make every deletion that does not use the MinIO root credential recoverable for a fixed
window by versioning the lake bucket, and stop the platform's own jobs from carrying out a
destructive instruction that only the unauthenticated catalog vouches for: maintenance and the
ingest check each irreplaceable table against pinned identities and against record counts the
catalog cannot forge before they delete or read, orphan removal on those tables runs dry first
under a cap, those tables and their namespaces are protected and dropped producer-lake tables
soft-deleted, the catalog database gets its own login and a strong superuser password, and the
Spark image's unauthenticated servers are switched off, because no identity provider exists to
make the catalog itself refuse anyone.** Scope: this single host, until authentication reaches the
catalog (the entitlements phase, P5) or the platform moves to AWS.

### 1. Pinned identities

The **pins** ([glossary](glossary.md#pinned-table-identity)) are one line per irreplaceable table:
warehouse, name, table UUID, location. `docker/lake/irreplaceable.txt` (in the repository) lists the
names; `make pin-irreplaceable` writes `docker/lake/pins/irreplaceable.lock` (untracked, one per
host) from the catalog once the tables exist, and then only appends: it refuses to change a line
that is there, so a changed identity needs the owner to delete the line by hand after reading why.
ADR-052's backup holds its own root-owned copy. The file is mounted read-only into `lake-init` and
`spark-iceberg`, and no container mounts it writable. A test asserts that every table in
`docker/lake/ddl/lake.sql` is either rebuilt by `docker/lake/rebuild.py` or listed as
irreplaceable, so a new table cannot miss both.

`lake-init` fails closed when a pinned table's UUID or location differs from the catalog's, and
since `lake-ddl` waits for `lake-init`, a renamed-and-replaced `raw.messages` stops `up` before an
empty one is created. A table not yet in the catalog (a domain's results table before its first dbt
run) is pinned on the next run.

### 2. Maintenance guards

Before compaction, expiry or orphan removal of any table, `maintenance.py` refuses that table, files
a `maintenance_refused` row in `audit.checks` and exits non-zero, which fires
`LakeMaintenanceRefused` (part 8), when:

- its location is not `s3://k2-lake/warehouse/k2/<its own UUID>`, or any of the properties that move
  where files are written or looked for (`write.data.path`, `write.metadata.path`,
  `write.object-storage.path`, `write.folder-storage.path`) is set, or its location lies inside a
  pinned table's location and it is not that table;
- it is irreplaceable and its UUID or location differs from the pins;
- it is irreplaceable and the records summed over its current snapshot's data files (read from its
  manifests, which are files the catalog cannot write) fall below the floor: the count in ADR-052's
  newest verified restore point, read from the backup's status object, which only the backup's key
  can write. With no status yet, on a fresh clone, this check is skipped with a warning and
  ADR-052's alert is already firing;
- it is irreplaceable and its current snapshot does not descend from the snapshot of that verified
  restore point: a rollback of `main`.

And for the irreplaceable tables, orphan removal runs first with `dry_run => true` and refuses when
it would delete more than 100 files or 1 GiB; the files a crashed write leaves are a handful. The
caps are constants in `maintenance.py`, each with its reason; the guard's own test proves each
refusal.

### 3. Ingest guards

Before reading, `ingest.py` refuses to run, with `LakeIngestGuardFailed` (critical, routed):

- when a committed offset is above the broker's newest for that partition, unless an operator passes
  `--accept-offsets-reset`, which first records an `offsets_reset` row in `audit.checks`, as
  `--accept-data-loss` already does for evicted records;
- when the newest ingest snapshot on `main` does not hold exactly the records its offsets claim: the
  [batch audit](glossary.md#batch-audit) of ADR-047 (per partition, rows equal the range, first and
  last offsets match) re-run on that snapshot's added files before the next read. A snapshot added
  through the catalog with forged offsets adds no files of its own and fails it; the check reads
  three narrow columns of one commit, 0.4 to 1.2 s (ADR-047's measurement);
- when `raw.messages`' UUID or location differs from the pins.

A forged position then stops the ingest loudly within five minutes instead of silently, which turns
it into the stopped-ingest case of part 9: recovered by a person before Redpanda evicts.

### 4. Bucket versioning, and why not object lock

With versioning on, MinIO keeps every version of every object: a plain delete adds a **delete
marker** and the data becomes a **noncurrent version**, still on disk and restorable; a write over
an existing name keeps the old one the same way ([glossary](glossary.md#bucket-versioning-delete-markers-and-noncurrent-versions)).
Only `s3:DeleteObjectVersion` erases a version. A **lifecycle rule**, run by MinIO itself, erases
noncurrent versions a set number of days after they became noncurrent and removes delete markers
left with nothing behind them.

The window, **W**: **2 days from now**, **14 days once ADR-050's pause is in effect**. Iceberg never
overwrites a file, so the cost is what is deleted, held W days: about 23 GiB at full capture
(11.62 GiB a day times 2, 23% of the VM's free disk), and about 17 GiB with the pause (about
1.2 GiB a day times 14, an estimate). Fourteen days at full capture would be 163 GiB, more than the
VM has. One number, W, also sets soft deletion (part 6).

Until [ADR-054](ADR-054-scoped-storage-credentials.md) lands, every engine holds the root
credential, so versioning protects against mistakes and the platform's own deletes, not against a
root holder; after ADR-054, no key but root can erase a version.

**Object lock** (WORM, write once, read many) forbids erasing a version before a retain-until date
([glossary](glossary.md#object-lock)). A compacted table rewrites every recent byte nightly and must
eventually delete what it replaced, so a lock of R days holds R days of every superseded file, the
same cost as versioning with no way to free it early; in compliance mode not even root can shorten
it, so a mistaken retention on one disk is an outage nobody can undo; in governance mode root can
bypass it, and against every other key it adds nothing versioning without `DeleteObjectVersion`
does not. It fits a **landing zone**, where a downloaded source file is kept exactly as fetched and
never rewritten, which is where the P4 plan puts it ([glossary](glossary.md#landing-zone)).

### 5. Rewind: restoring from object versions

`k2-backup rewind --table <name> --to <UTC time>` (in ADR-052's root-owned tool, run with the root
credential, because erasing a delete marker is erasing a version): for every object under the
table's location, it makes the version that was current at that time current again (removing a
later delete marker, or copying the earlier version over a later overwrite), and then registers the
`metadata.json` that was current at that time with `"overwrite": true`. It prints what it will do
and acts only with `--apply`. Within W, this undoes everything in part 9 that reached storage.

### 6. Protection and soft deletion in the catalog

- **Protected** (`POST .../protection`, `{"protected": true}`, which makes a drop fail unless
  `force` is sent): the four irreplaceable lake tables, each domain's `dq.dbt_test_results`, the
  namespaces `raw` and `audit`, and warehouse `k2`. Nothing in K2 drops them; `rebuild.py` leaves the
  dimensions alone.
- **Soft deletion** on warehouse `k2` ([glossary](glossary.md#soft-deletion-undrop-protection-and-force)):
  `delete-profile` `{"type": "soft", "expiration-seconds": W}`. A dropped table is hidden but kept
  for W, listed by `GET .../deleted-tabulars` and restored by `POST .../deleted-tabulars/undrop`.
  The domain warehouses stay hard: `dbt_marts` replaces every mart hourly, and marts are rebuilt
  from the lake.
- **A trap, avoided first.** Every warehouse's profile reads `push-s3-delete-disabled: True`, which
  "has no effect if Soft-Deletion is disabled". Once soft deletion is on it sends
  `s3.delete-enabled=false` to clients, and that "affects other Spark operations that require file
  deletion, such as removing snapshots" (Lakekeeper 0.13.9's management OpenAPI): expiry and orphan
  removal would report success and delete nothing until the disk filled. So it goes to `false`, in
  the same `POST .../storage` that must carry the warehouse's credential, before soft deletion is
  turned on.
- Protection and soft deletion stop mistakes, not a caller: `force` bypasses both, a recursive
  namespace drop with `force` deletes at once even under soft deletion, and `allow-all` lets anyone
  send it (0.13.9's catalog OpenAPI; Lakekeeper's [Concepts](https://docs.lakekeeper.io/docs/latest/concepts/)).

### 7. The catalog database, credentials at start, and the Spark container

**PostgreSQL.** One server holds the `lakekeeper` and `airflow` databases.

- Lakekeeper gets its own role, `lakekeeper`: login, not superuser, no role or database creation,
  owner of database `lakekeeper` and every object in it. Its five extensions (`btree_gin`,
  `btree_gist`, `pg_trgm`, `pgcrypto`, `uuid-ossp`, read with `SELECT extname FROM pg_extension`)
  are all ones PostgreSQL 13 and later mark trusted, so a database owner may create them; a fresh
  `lakekeeper migrate` as that role is still a rehearsal line. A new one-shot `db-init`, on the
  `postgres:15-alpine` image the server already uses, converges the roles (`lakekeeper`, ADR-052's
  `k2_backup`) idempotently and transfers ownership without `REASSIGN OWNED`, which would also hand
  over every database the superuser owns. Its 0.25 CPU and 128 MiB count at the **bootstrap peak**,
  the moment during `up` when one-shots run beside everything else, which the 25 CPU / 48 GiB
  ceiling is gated at; it fits the 3.70 CPU / 10.19 GiB headroom and goes into ADR-010's Outcome and
  the compose footer.
- The superuser's password is rotated, in one paused window, in this order: new value in `.env`;
  `ALTER ROLE ... PASSWORD` sent on standard input to `docker exec -i k2-prefect-db psql` (the
  container trusts its own local connections, and `log_statement` is `none`, read 2026-11-26, so the
  value appears on no command line and in no log); `docker compose up -d --no-deps db-init`;
  recreate `lakekeeper-migrate` and `lakekeeper` as `lakekeeper` (catalog down 15 to 30 s); recreate
  `prefect-db` so its environment no longer carries the old value (about 15 s).
- The legacy Prefect profile, stopped since the Airflow cutover and the only other user of the
  superuser login, is retired, as the review advised: its services and `docker/prefect/` move to
  the Prefect archive already planned, so no service keeps that login.

**Credentials at start.** `scripts/check-env.py` refuses, for every credential the compose file
takes: a password equal to its user, ignoring case (each paired with its user variable or the fixed
name compose uses); shorter than 16 characters (32 for the Airflow signing secrets and
`LAKEKEEPER_ENCRYPTION_KEY`); or a known default (MinIO's factory pair, `postgres`, `admin`,
`password`, `changeme`, the placeholder, the user names). A test reads every `${NAME}` in
`docker-compose.yml` ending in `PASSWORD`, `SECRET` or `KEY` and fails if no rule covers it. Because a
bare `docker compose up` skips `check-env.py`, the one-shots that set a credential refuse a weak one
themselves (`db-init`; `lake-init` once ADR-054 gives it keys to set), which holds back what depends
on them.

**The Spark container.** `entrypoint: ["bash", "-c", "/opt/spark/sbin/start-history-server.sh &&
exec sleep infinity"]` with `init: true` (a small init process that reaps what `docker exec`
leaves): overriding `command:` alone is not enough, because the image's entrypoint starts the
master, worker and Thrift server before it reads its argument. The History Server stays: it is a
read-only view of past jobs that `docker/lake/spark_conf.py` reads to find failed stages, and Spark
redacts properties matching `secret` in it. Host port 18080 then serves it instead of the empty
master page. `./docker/lake` becomes a read-only mount, so code in that container cannot rewrite the
scripts maintenance later runs; the writer lock is in `/tmp`.

### 8. Alerts

`LakeMaintenanceRefused` and `LakeIngestGuardFailed` are critical and labelled `notify: desktop`,
so ADR-052's host watcher raises a notice; each has a runbook and a `promtool` test. Without that
routing, every window in part 9 is theory.

### 9. What an unauthenticated caller can still do, after ADR-052, this ADR and ADR-054

| Through the catalog | What happens now | Recovered by | Within |
|---|---|---|---|
| Deactivate warehouse `k2`, remove its storage credential, or forge offsets | the ingest stops (the forged case loudly, part 3); nothing is deleted | reactivate, or `docker compose up -d --no-deps lake-init` to post the credential, or point `main` back; then the ingest catches up | Redpanda's hold on the busiest partition: about 13 h at full capture, 48 h with the pause. **The cheapest irrecoverable loss: only a person told in time closes it** |
| Point `main` of an irreplaceable table back, remove snapshots, or register an older pointer with `overwrite` | maintenance refuses (part 2); readers see the old version | `rewind`, or register the newest `metadata.json` | the copy's restore points and the files in storage, while W lasts and after |
| Move a table's location or data path | maintenance refuses that table | set it back | no deadline |
| Rename an irreplaceable table | `lake-init` fails closed on the next `up`; ingest refuses | rename it back | no deadline |
| Drop an irreplaceable table with `force`, drop its namespace recursively, run its expiration now, delete warehouse or project | the pointer goes; ADR-054's refusal keeps the catalog's key from deleting those files; before ADR-054, they are delete markers for W | register the newest `metadata.json` | storage: no deadline after ADR-054, W before; records: the Redpanda hold, since the ingest has stopped |
| Drop a derived table | soft-deleted for W in `k2`; hard in a domain warehouse | `undrop`, or rebuild from raw | W |
| Change a schema or a property that is not a location | the contract check (pull request #224, open) reports drift nightly | register the previous `metadata.json` | W |

With root, or with the Docker socket, nothing in this table holds; ADR-052's copy is what survives.

### 10. Rehearsal, and the landing steps

On ADR-052's rehearsal stack, before each step:

- **G1** (the review's B1, and S1) on a scratch table holding rows: commit `set-snapshot-ref` to an
  old snapshot and `remove-snapshots` through the REST API; run the real `maintenance.py`; with the
  table pinned, it refuses; unpinned, it deletes the orphans, then `rewind` restores the exact rows.
- **G2** location attacks: `set-location` of a derived table to a pinned prefix, and
  `write.data.path`, each refused by maintenance; record whether Lakekeeper itself refuses them.
- **G3** forged offsets above latest and inside the range: the ingest refuses both within one run.
- **G4** rename `raw.messages`, then `up`: `lake-init` fails and no empty table appears.
- **G5** catalog probes: `register` with `"overwrite": true` over a pinned table; `DELETE` of the
  project; deactivation; an empty storage credential; `run-now` on an expiration: each recorded with
  what it did and how it was recovered.
- **G6** soft drop, `undrop`, protection, `force`; `rebuild.py`'s drop and recreate of the same name
  under soft deletion; with `push-s3-delete-disabled` false, expiry deletes, and with it true, it
  does not (the trap, shown once).
- **G7** `lakekeeper migrate` as `lakekeeper` on a fresh database and on a restore of the live dump;
  the superuser rotation in its order.
- **G8** the Spark container: jobs and maintenance run; 7077, 8888 and 10000 refuse connections from
  another container; the History Server's environment page redacts the secret.

| ADR-052 step | Change | What breaks, for how long | Verify | Roll back |
|---|---|---|---|---|
| 3 | `db-init`, superuser rotated, Lakekeeper's role, Prefect profile retired, `check-env.py` | catalog 15 to 30 s; PostgreSQL about 15 s | old password refused from the host; `lakekeeper` not superuser; `COPY ... FROM PROGRAM` as `lakekeeper` refused | `ALTER ROLE` back, previous compose |
| 4 | versioning, lifecycle at W = 2 days, protection | nothing: no restart | a scratch delete shows a marker and a noncurrent version, and `rewind` restores it; a drop of a protected table without `force` is refused | suspend versioning (existing versions stay until W) |
| 5 | pins in `lake-init` and the guards | nothing if pins match | G1 to G4 in rehearsal; on the live stack, `maintenance.py --audit-only` and one ingest run pass | the previous commit |
| 6 | soft deletion on `k2`, after `push-s3-delete-disabled` false | nothing | a scratch table's drop and `undrop`; the next maintenance leaves noncurrent versions | `delete-profile` hard |
| 7 | the Spark container | seconds, paused | `docker top` lists the History Server alone; the three ports refuse | the previous compose block |

When ADR-050's pause is in effect, a one-line `lake-init` change raises W from 2 to 14 days.

---

## Rationale

Storage is the one layer here that checks a secret, so versioning is what turns every delete
without root into a delay, at the cost of what is deleted. But the review showed that the dangerous
instructions are not deletes: they are pointer moves that make the platform's own jobs delete, or
stop reading. The guards put the check where the destructive act happens, in the job, against facts
the catalog cannot write: pinned identities on the host, record counts in manifests, and the
backup's verified floor. Protection and soft deletion are cheap, and they stop the likeliest event,
a mistaken drop.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Trust storage refusals alone (the first draft's "fence that holds") | a rollback deletes nothing, so no refusal is tested, and maintenance then deletes with its own key (B1) |
| An identity provider for Lakekeeper now | the real fix, and P5's scope: a new service with its own state, on a host short of memory |
| Object lock | see part 4 |
| 14 days of versioning now | 163 GiB at full capture, more than the VM's free disk |
| Re-deriving the irreplaceable tables' identities on every `up` | the rename attack moves the protection to an empty table (B2) |

---

## Consequences

**Easier:** a deleted or overwritten file comes back for W days by a printed, reviewed command; a
forged pointer or position stops the jobs and tells a person.

**Harder:** a legitimate rename or move of an irreplaceable table needs the owner to edit the pins;
maintenance can refuse and leave files to grow until a person looks; one more one-shot.

**Committed to:** W (2, then 14 days); the pins file and its append-only rule; the guard caps; the
Prefect archive.

**Risks:** a guard that refuses for a legitimate reason stops maintenance until someone reads it;
W at full capture is 23 GiB of a 101 GiB margin.

**What is not yet verified:** G1 to G8; whether Lakekeeper refuses `set-location` or a data-path
property; `force` on a single table under soft deletion in 0.13.9 (only documented for the current
release); that the dry-run cap catches every orphan-removal attack; the paused-capture byte rate.

**Revisit when:** ADR-050's pause takes effect (W to 14 days); order-book capture resumes (W back to
the arithmetic of part 4); a guard refuses twice for a legitimate reason (adjust the cap with its
evidence); authentication reaches the catalog (part 9 shrinks).

---

## Questions for the owner

1. W at 2 days now and 14 with the pause, as the review advised: accept?
2. Retiring the legacy Prefect profile now, before the archive is written: accept?

---

## Implementation plan

| PR | Change | Test | Attacker-side proof |
|---|---|---|---|
| G-a | `db-init`, Lakekeeper's role, `check-env.py` and its completeness test, the Prefect retirement | `tests/test_check_env.py` (pairs, length, defaults, completeness); `scripts/check-budget.py` | G7 |
| G-b | versioning, lifecycle, protection in `lake-init`; `rewind` | `tests/test_backup.py` covers `rewind` over a fake versioned store | step 4's checks |
| G-c | pins, `lake-init`'s check, the maintenance and ingest guards, two alerts | `tests/test_lake_maintenance.py` and `tests/test_lake_offsets.py`: each refusal of parts 2 and 3; the classification test; `promtool` tests | G1 to G4 |
| G-d | soft deletion, `push-s3-delete-disabled` false | rehearsal G6 | G6 |
| G-e | the Spark container; the operations guide's idle filter | a compose test that the service has neither the image's entrypoint nor a writable `docker/lake` | G8 |

---

## References

- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md), [ADR-028](ADR-028-non-goals-and-honest-limits.md),
  [ADR-047](ADR-047-ingest-batch-audit-after-publish.md), [ADR-050](ADR-050-order-book-capture-paused-during-platform-build.md),
  [ADR-052](ADR-052-backup-and-restore-of-irreplaceable-tables.md), [ADR-054](ADR-054-scoped-storage-credentials.md).
- [Research note](../research/2026-11-26-storage-credentials-least-privilege.md); the P0 audit (branch
  `docs/audit-p0-phase`).
- Lakekeeper 0.13.9's OpenAPI documents at `/api-docs/management/v1/openapi.json` and
  `/api-docs/catalog/v1/openapi.json`; [Concepts](https://docs.lakekeeper.io/docs/latest/concepts/).
- `docker/lake/maintenance.py`, `docker/lake/offsets.py`, `docker/lake/ingest.py`.
