# ADR-023: Lakekeeper REST catalog on MinIO

**Status:** Proposed
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Storage

---

## Context

v2 runs Iceberg on a **Hadoop catalog over a bind-mounted local directory**:
`spark.sql.catalog.k2.type = "hadoop"`, warehouse `/home/iceberg/warehouse`, bound to
`docker/iceberg/warehouse` on the host
(`docker/offload/create_bronze_table_sql.py:10-11`). MinIO runs in the stack and holds
S3 credentials, and the offload does not use it
([ADR-007](ADR-007-iceberg-cold-storage.md) Outcome).

That was a deliberate and, at the time, correct choice.
[ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) records why: a REST catalog on
Spark 4.1.1 + Iceberg 1.10.1 failed every `CREATE TABLE` with `Invalid table identifier`
through 12+ attempts across two catalog versions, the failure was client-side and
undocumented, and after 2+ hours the pragmatic move was to take a file-based catalog and
a proven image. It produced a working cold tier in ~45 minutes and 9 tables. ADR-013's
own Final Recommendation says the quiet part out loud: Hadoop catalog "for single-node
POC", migrate to "JDBC or REST for multi-user concurrency" when production-ready.

Four things have since made it the wrong choice for v3, and they are worth stating as
facts rather than as a preference:

- **The Hadoop catalog has no atomic commit.** It resolves the current table version by
  reading a `version-hint.text` file and then writing the next `vN.metadata.json`. That
  is a filesystem-level compare-and-swap that no filesystem provides. Iceberg's own
  documentation states it is unsafe for anything but a single writer on a single
  filesystem; [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) makes the commit —
  and only the commit — the durable record of ingest position, so a commit primitive
  that is not atomic makes exactly-once impossible by construction.
- **It has no multi-writer story at all.** v2 got away with this because there was
  exactly one writer, a serialised Prefect flow. v3 has ingest every 5 minutes and a
  nightly maintenance job that rewrites files in the same tables, and Q6's 2 h parallel
  window deliberately runs old and new side by side.
- **ClickHouse cannot read it.** The rebuild path in
  [ADR-025](ADR-025-clickhouse-derived-hot-tier.md) uses ClickHouse's `iceberg()` table
  function, which takes an object-store URL. A catalog whose tables live on a bind-mounted
  host directory inside another container is not addressable from ClickHouse at all.
  Verified on the running stack, 2026-08-26: the function exists on 24.3
  (`SELECT name FROM system.table_functions WHERE name ILIKE '%iceberg%'` → `iceberg`),
  and there is nothing for it to point at.
- **A bind mount pins the inode, and this platform has been bitten by it.** Editing a
  file-level bind mount in place needs `docker compose up -d --force-recreate`; `docker
  restart` does not pick it up (`CLAUDE.md`, *Bind-mount gotcha*). The catalog's own
  state living behind that class of surprise is a bad place for it. The v2 warehouse's
  `version-hint.text` files show up as modified files in `git status` on this repo,
  which is its own comment on the arrangement.

Spikes S8 and S9 tested the replacement before this was decided: Lakekeeper v0.13.3
against the Iceberg 1.8.1 Spark client did create / insert / select with no errors,
including the snapshot-property commit ADR-022 rests on, and its warehouse-create body
for MinIO needs `region` even for `s3-compat`
([ADR-018 Appendix A](ADR-018-v3-lake-first-rust-capture.md#s8--lakekeeper--iceberg-spark-client)).

---

## Decision

**We will run Lakekeeper (`quay.io/lakekeeper/catalog:v0.13.3`) as an Iceberg REST
catalog backed by PostgreSQL, with all lake data on MinIO via `S3FileIO`, and delete the
Hadoop catalog and its bind-mounted warehouse — because the archive's correctness now
rests on atomic commits and readable-from-anywhere table locations, and a file-based
catalog on a bind mount provides neither.**

Scope: the v3 `lake` catalog (namespaces `raw`, `bronze`, `audit`). v2's `k2` hadoop
catalog and `cold.*` tables stay live and untouched until the Phase D cutover PR deletes
`docker/offload/` and the warehouse mount together
([Q5](../research/2026-08-26-v3-requirements-clarification.md#q5--cutover-authority-who-signs-off-on-the-destructive-steps)
authorises that cutover in advance, on its gates). The two catalogs coexist deliberately
in the meantime; this is not a migration of the v2 data, which
[Q7](../research/2026-08-26-v3-requirements-clarification.md#q7--v2-data-migrate-the-existing-clickhouse-and-iceberg-data-into-the-lake)
declares disposable.

---

## Rationale

**This ADR does not say ADR-013 was wrong.** It says ADR-013 solved a different problem —
"get a cold tier working today without paying a bleeding-edge tax" — and solved it well,
and that the conditions it named for revisiting have arrived. The catalog that failed in
February was `tabulario/iceberg-rest` against Iceberg 1.10.1 on Spark 4.1.1; the catalog
adopted here is a different implementation against Iceberg 1.8.1 on Spark 3.5.5, and it
was made to pass before it was chosen rather than after. The lesson carried forward from
ADR-013 is the method, not the conclusion: spike first, on the exact versions.

**Atomic commit is the requirement, and it is not negotiable.** ADR-022 makes the
snapshot summary the only durable record of ingest position. If two writers can race a
metadata pointer, "the commit happened or it did not" stops being true, and every failure
row in ADR-022's table stops holding. A REST catalog serialises commits behind a
transactional database; that is the entire reason it exists.

**Object storage is what makes the rest of the platform reachable.** With data on MinIO
under `s3://k2-lake/warehouse/k2/`, three consumers address the same bytes: Spark through
the catalog, DuckDB through `ATTACH … TYPE ICEBERG` against the same REST endpoint
(spike S10, passing on 1.4.4 and 1.5.5), and ClickHouse through `iceberg()` on an S3 URL
for the rebuild path (spike S11). On a bind mount, only the container that has the mount
can read anything.

**It is also the shape that scales without a rewrite.** The Lakekeeper-on-ECS + RDS,
data-on-S3 mapping in [`../architecture/scale-out-path.md`](../architecture/scale-out-path.md)
is a change of five environment variables, because the catalog protocol and the file IO
are the same on both sides. The Hadoop catalog has no such mapping — `s3a://` under a
Hadoop catalog reintroduces the same non-atomic pointer, on a store with weaker
guarantees.

**The honest price: a stateful service on the archive's critical path.** A bind mount
needed nothing. Lakekeeper is a container plus a PostgreSQL database plus a bootstrap
sequence (`docker/lake/init-lake.sh`), and if either is down, nothing commits to the lake.
Three things reduce that to an acceptable cost rather than arguing it away: reads of
already-committed data do not need the catalog (`iceberg()` and PyIceberg can address
metadata directly), a failed commit is a no-op that the next run repeats (ADR-022), and
the catalog database is small and is backed up with the Prefect database it shares a
server with. It is still one more thing that can be down, on a platform that has no HA
anywhere.

**Sharp edges found by spiking, recorded so nobody re-finds them:** the warehouse-create
body needs `region` even against MinIO (`local-01`), and `flavor: s3-compat`; the catalog
path prefix is the warehouse **UUID**, not its name — using the name returns
`400 WarehouseIdIsNotUUID`, and the UUID comes from `defaults.prefix` on
`GET /catalog/v1/config?warehouse=k2`; `lakekeeper migrate` creates its own PostgreSQL
extensions, so the DDL this repo ships is `CREATE DATABASE` and nothing else; the image
has no shell, so the compose healthcheck is exec-form; and a `PURGE` drop answers
`BadRequestException: Table does not exist` because Lakekeeper expires dropped tables
through its own task queue — a plain `DROP` is both what works and what is wanted
(`docker/lake/spark_conf.py`). MinIO object paths are keyed on table **UUID**, so a table
name means nothing at the object-store level and `mc ls` is not a way to find a table.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Keep the Hadoop catalog, point it at `s3a://` instead of a bind mount** | Removes the inode problem and keeps the fatal one: the commit is still read-`version-hint`-then-write-metadata, and S3's guarantees are weaker than a local filesystem's, not stronger. It would make the non-atomic commit *more* likely to bite, not less, and ClickHouse still could not resolve a table without a catalog. |
| **Iceberg JDBC catalog** (PostgreSQL, no separate service) | Atomic commits, no new container — genuinely the cheapest correct option, and it was tried in v2: `No suitable driver found for jdbc:postgresql` in the `tabulario` image (ADR-013, *Catalog Evolution*), fixable by baking a driver. Rejected on reach rather than correctness: DuckDB's `ATTACH … TYPE ICEBERG` speaks REST (S10), not JDBC, so the notebook query layer of ADR-018 §4 would need a second access path. One protocol that Spark, DuckDB and a future ECS deployment all speak is worth one container. |
| **`tabulario/iceberg-rest`** — the REST catalog v2 already had in compose | It is the one that failed in ADR-013, its last meaningful release predates the Iceberg versions in use, and it is a reference implementation rather than a maintained catalog. Choosing it would re-run February's afternoon. |
| **AWS Glue / a hosted catalog** | Not available: no AWS account, and Q9 puts the cloud deployment explicitly out of scope. It is the target of the scale-out path's catalog row, reached by pointing `K2_LAKE_CATALOG_URI` elsewhere. |
| **Nessie** | Git-like branching over Iceberg, and the branching is the feature — this platform has one writer per table and no use for branch/merge semantics. More concepts, no atomic-commit advantage over Lakekeeper. |
| **No catalog: PyIceberg with a static metadata pointer** | Removes the service and reintroduces the pointer-update race by hand, at which point the platform owns the commit protocol. The one thing worse than depending on a catalog is writing one. |

---

## Consequences

**Easier:** atomic commits, which is what makes ADR-022 true; three engines reading the
same tables (Spark, DuckDB, ClickHouse) with no per-engine copy; concurrent ingest and
maintenance without a lock convention; a `git status` that no longer shows Iceberg
metadata as modified working-tree files; and an AWS mapping that is configuration rather
than a rewrite.

**Harder:** two more moving parts on the archive path (the catalog and its database) with
a bootstrap order that must hold — MinIO bucket, then Lakekeeper bootstrap, then
warehouse, then namespaces — and a failure mode where a healthy Spark job cannot commit
because a healthy-looking catalog has not been bootstrapped. Debugging table storage is
worse, not better: paths are keyed on table UUID, so finding a table's files means asking
the catalog first. And the version matrix is now real — Lakekeeper 0.13.3 ↔ Iceberg
1.8.1 ↔ Spark 3.5.5 ↔ DuckDB 1.4.4, each pinned, each a thing an upgrade must re-verify.

**Committed to:** MinIO as the only lake storage, addressed through `S3FileIO` with
path-style access and region `local-01`; the REST protocol as the single catalog
interface for every engine; `docker/lake/init-lake.sh` as the idempotent bootstrap, whose
constants must stay in step with `docker/lake/spark_conf.py`; and deleting
`docker/iceberg/warehouse/`, `docker/iceberg/ddl/0{2,3,4}-*.sql` and the `iceberg-init`
one-shot in the Phase D cutover PR, together rather than in stages.

**Risks:** Lakekeeper is a young project and v0.13.3 is a specific patch release — S9
found that v0.13.2 and v0.14.0 do not exist as tags, so the upgrade path is whatever
appears next, and the snapshot-property behaviour ADR-022 depends on must be re-proved on
every bump (`spark_conf.py --smoke` is that check). Catalog metadata is now a backup
target that v2 did not have: losing the catalog database with the MinIO bucket intact
means the data files exist and no engine can find which of them are current. And a
single-broker, single-catalog, single-host platform has added one more single point of
failure to the list it already publishes.

**Revisit when:** the catalog database is lost or corrupted once and the recovery is
timed — that measurement decides whether it needs a backup schedule beyond the Prefect
database's; or Lakekeeper publishes a release that changes the snapshot-summary
round trip; or a second writer host exists, at which point the REST catalog's multi-writer
property stops being insurance and starts being load-bearing.

---

## Related

- [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) — the Hadoop-catalog decision this supersedes, and the version-compatibility method it taught
- [ADR-007](ADR-007-iceberg-cold-storage.md) — chose Iceberg, and its Outcome records the REST-catalog-to-Hadoop deviation
- [ADR-018](ADR-018-v3-lake-first-rust-capture.md) — Appendix A: S7 (image tag), S8 (Lakekeeper ↔ Spark client), S9 (bootstrap body), S10 (DuckDB attach), S11 (ClickHouse `iceberg()`)
- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) — the atomic commit this exists to provide
- [ADR-025](ADR-025-clickhouse-derived-hot-tier.md) — the rebuild path that needs tables addressable from outside Spark
- [`../architecture/scale-out-path.md`](../architecture/scale-out-path.md) — the ECS + RDS + S3 mapping, and the env vars that flip it
- [`../runbooks/lake-recovery.md`](../runbooks/lake-recovery.md) — Lakekeeper down, MinIO down

---

## Outcome

_To be appended after the Phase D burn-in._
