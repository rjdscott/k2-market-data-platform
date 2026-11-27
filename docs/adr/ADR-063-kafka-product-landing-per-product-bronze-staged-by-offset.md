# ADR-063: A Kafka product: a landing per product that holds every record, bronze staged by offset with the decoded envelope

**Status:** Proposed
**Date:** 2026-11-28
**Author:** Platform
**Category:** Ingestion / Storage
**Decided by:** proposed for the owner's acceptance. Two of its parts are the owner's already: the
Kafka landing is never expired (2026-11-28, plan 004's option (a)), and the review findings that moved
the filter from the landing to bronze staging and keyed staging by offset were accepted by the owner
the same day ([build breakdown](../research/2026-11-28-phases-004-005-build-breakdown.md), "Review of
2026-11-28").
**Would narrow:** [ADR-021](ADR-021-raw-first-archive-and-lineage.md) (Proposed), its shared archive
`raw.messages`, for the lake; and [ADR-056](ADR-056-unified-data-product-platform.md)'s line that a
product "lands what its filter keeps and counts the rest". Both bodies stay as written; their status
lines change only if this one is accepted.
**Keeps:** [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) (exactly-once resume from the
snapshot summary) and [ADR-047](ADR-047-ingest-batch-audit-after-publish.md) (the landing commits first
and is never blocked by an audit).
**Evidence:** `scripts/verify/kafka-path.sh` on throwaway lakes on 2026-11-28: group a `10 PASS, 0
FAIL`, group b `5 PASS, 0 FAIL`, group c `6 PASS, 0 FAIL`; claims a11, a12 and b6 were added by later
review fixes and their run is pending. Unit tests: `tests/test_lake_kafka.py`,
`tests/test_lake_maintain.py`, `platform/k2gen/tests/test_checks_connectors.py`. Built on branch
`phase/004-005-kraken-and-enriched`; **not yet deployed**.

---

## In plain words

**Each product that reads a Kafka topic keeps its own copy of every record it read, then builds its
first queryable table from that copy, position by position.** The old design kept one shared archive of
every venue's frames; one product's trouble then stopped all of them, so each product now has its own.
The copy holds every record of the range read, not only the trades, because the checks that prove
nothing was lost or doubled only work on an unbroken run of positions. The queryable table carries the
record already decoded, because the SQL engine cannot decode the binary format the capture writes. The
costs: the non-trade frames, heartbeats and status, are stored too, and the copy grows without limit
until the owner sets a retention at the 3.0 release gate. The owner decides whether to accept it.

---

## Context

### Terms, for a reader new to them

- **Kafka topic, partition, offset.** Redpanda (a Kafka-compatible broker) keeps each topic as several
  **partitions**, each an append-only log. Every record in a partition has an **offset**, its position,
  counting up by one. A range of records is named by a start offset (inclusive) and an end offset
  (exclusive). K2's capture writes every Kraken frame it receives to the topic
  `market.crypto.v3.raw.kraken`, 12 partitions, keyed by symbol (`docker/redpanda/init.sh`).
- **Log start and eviction.** The broker deletes the oldest records of a partition after 48 hours or
  2 GiB, whichever comes first. The lowest offset still held is the **log start**. A record deleted
  before anyone read it is gone: the venues do not replay
  ([glossary](glossary.md#raw-retention-and-eviction)).
- **The envelope.** The capture wraps every venue frame in an Avro record, `RawMessage`
  (`schemas/avro/raw-message.avsc`): `exchange`, `stream` (`trade`, `heartbeat`, `status` and so on),
  `symbol`, `recv_ts_ns`, `conn_id`, `conn_msg_seq` and `payload`, the venue's frame as sent. Each Kafka
  record's value is that Avro record behind a five-byte **Confluent header** naming the writer schema's
  id in the schema registry.
- **Iceberg snapshot, summary, branch.** Every commit to an Iceberg table makes a
  [snapshot](glossary.md#snapshot), and each snapshot carries a
  [summary](glossary.md#snapshot-summary-and-k2-properties), a map of string properties written in the
  same atomic commit as the rows. A [branch](glossary.md#branch-and-main) is a named pointer to a
  snapshot; `main` is the one readers read.
- **Exactly-once via snapshot offsets** ([ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)). A
  run writes the end offsets it read into `k2.kafka-offsets` on the same commit as the rows, and the next
  run starts from them. Rows and position move together or not at all, so a run killed at any point
  lands nothing twice and skips nothing.
- **Write-audit-publish** ([glossary](glossary.md#write-audit-publish)). Rows are written to a branch
  named `audit`, checked there, and only then made `main`'s head. A batch that fails its checks never
  reaches readers.

### What forced the decision

[ADR-021](ADR-021-raw-first-archive-and-lineage.md) made one table, `raw.messages`, the system of
record for every venue's frames, written by one shared ingest job. On 2026-11-26 one product's pause
failed that shared job for every venue ([ADR-056](ADR-056-unified-data-product-platform.md), Context),
and the unified platform gives each product its own connector, landing and lifecycle. Phase 004 builds
the first product that reads a topic, `market-capture/kraken-trades`. Four facts shaped how:

1. **The offset checks need an unbroken range.** The checks reused from phase 001 are pure functions:
   `offsets.batch_audit` passes a partition only if exactly `end - start` rows landed at offsets
   `start` to `end - 1`, and `offsets.offset_gaps` calls a partition intact only if its highest offset
   minus its lowest plus one equals its row count (`docker/lake/offsets.py`). The raw topic holds trade
   frames and control frames (heartbeats, status) interleaved; book frames have their own topic and book
   capture is off ([ADR-050](ADR-050-order-book-capture-paused-during-platform-build.md)). A landing of
   trades alone has a hole at every control frame, and both checks fail at the first one. Plan 004's
   text, "the landing table holds the kept records byte for byte", did not survive this.
2. **The landing's columns are frozen** (`model.PLATFORM_TABLES["landing"]`: partition, offset,
   timestamp, key, value, and four lineage columns: batch, landing time, run, release). There is no
   column for a skipped record, and counts kept only in snapshot summaries are lost when those
   snapshots expire.
3. **Trino has no SQL function that decodes Avro.** Silver is built by dbt on Trino
   ([ADR-062](ADR-062-dbt-builds-silver-and-gold-per-product-through-one-wrapper.md)), so a model cannot
   read `stream` or `payload` out of the Avro bytes.
4. **A staging step keyed by run id leaves holes.** The file connector stages bronze by the run that
   landed the file. For a topic, a retry after the landing commit, or a kill between landing and
   staging, would land a range that no later run stages (review finding 2 of 2026-11-28).

---

## Decision

**We will give each Kafka product its own landing table that holds every record of each offset range
it reads, byte for byte, with the end offsets in the same commit; stage bronze from that landing by
offset, filtered there, carrying the envelope decoded; and tie each topic to one product of the domain
that writes it, because only an unbroken landing lets the offset checks prove exactly-once, and only a
decoded bronze lets dbt build silver on Trino.** Scope: the `kafka` connector kind
(`docker/lake/kafka.py`, dispatched by `docker/lake/connector.py`), its maintenance
(`docker/lake/maintain.py`) and k2gen's checks of it. The file and public-API kinds keep
[ADR-036](ADR-036-connectors-land-verbatim-then-publish-bronze-after-audit.md).

### 1. A landing per product, every record of the range

`lake.<fence>.landing` (the fence is the product name with `-` as `_`: `lake.kraken_trades.landing`)
belongs to one product. A run plans its range per partition from the end offsets of the landing's
newest connector snapshot on `main` (a commit whose summary says `k2.job` = `connector`; compaction and
expiry commits say otherwise and are walked past), bounded by `maxOffsetsPerPartition`. With no such
snapshot it starts at the log start (`startFrom: earliest-retained`). It reads the range from the topic,
counts it, and commits every record of it, trade and control frames alike, `record_key` and
`record_value` exactly as read and never decoded. The count per partition must equal the span or
nothing commits. The same commit's summary carries `k2.kafka-offsets` (the end offsets, exclusive),
`k2.kafka-starts` (the batch's first offset per partition) and, once a loss has ever been accepted,
`k2.kafka-gaps` (part 5).

### 2. The landing commit requires its parent

The landing is written on a branch `land`, set to `main` as the plan read it, and made `main`'s head by
one Iceberg REST commit that requires `main` still to be that snapshot. Of two loads at once, one lands
and the other exits 2 having landed nothing; the next run plans from what landed (review finding D4). A
plain append would be retried by Iceberg on top of the newer head and land one range twice.

### 3. Bronze staged by offset, filtered at staging, decoded

After the landing commit, the same task stages bronze. Per partition it takes every landing row after
bronze's **through-offset** (the landing end offset bronze has staged up to, kept in `k2.kafka-offsets`
on bronze's own commits) up to the landing's newest end offset, read from the landing at one snapshot
and never from the topic. A partition bronze never staged starts at the landing's lowest offset in it,
never at 0, since the landing starts at the log start (review finding D1). The filter
(`stream` in `[trade]` for `kraken-trades`) keeps rows for bronze's `audit` branch and counts the rest:
kept plus skipped must equal the rows read, or nothing stages. The through-offset is the landing's end
offset, not `max(kafka_offset) + 1`, so trailing control frames advance it too. A run killed between
landing and staging therefore leaves nothing behind: the next run stages what the landing holds past
the through-offset, whatever run landed it.

Each kept frame is decoded with the writer schema its Confluent id names, fetched from the registry by
id, and bronze takes, by name, the frame's partition, offset and timestamp, its `schema_id`, the
envelope's top-level fields as the bronze contract declares them, and the landing batch's lineage. The
bytes stay in the landing; bronze is rebuildable from it. A field the record lacks, or holds as a type
that does not read as the column's, is written null and fails the audit (part 4). Only a registry 404
makes an id unresolvable; any other registry error fails the task before anything is written (review
finding D3).

### 4. The audit, and what blocks

Bronze's audit runs three checks on the batch at `audit`'s head, one `audit_checks` row each:
`accounting` (the landing holds every offset of the range but those an accepted loss records, and
staged plus skipped equals that count), `parses` (each staged frame, read again from the landing,
decodes to exactly the row bronze holds) and `keys_unique` (`(kafka_partition, kafka_offset)` once in
the batch, and none already on `main`). A passed batch is published by a compare-and-set commit; a
failed one leaves `main` where it was.

A batch is its offsets, reproducible from the landing, so an **unaudited batch is restaged**: when a
run finds another run's batch on `audit` that was never audited (its run was killed between stage and
audit) or passed and was never published, it resets `audit` to `main` and stages the range again under
its own audit (review finding D2). A **failed batch blocks**: the next load still lands, then refuses to
stage, naming the failed checks and the one statement that clears it. It is never reset by code.
The landing still commits first and is never blocked by bronze ([ADR-047](ADR-047-ingest-batch-audit-after-publish.md)):
the broker's eviction deadline does not wait for an operator.

### 5. Below the log start, and accepted losses

A planned start below the broker's log start is refused at plan time, before anything is read: load
exits 2, nothing lands, and the message names each partition with its committed offset, the log start
and the records gone. Only an operator's accept-data-loss run, a manual trigger of the product's own
DAG with conf `{"acceptDataLoss": true, "reason": "..."}`, resumes those partitions at the log start.
A paused product lands nothing; resumed after its point left the log, it is the same case and fails
loudly. The accepted losses are **cumulative**: every landing commit after the first carries every loss
accepted so far in `k2.kafka-gaps`, merged where ranges meet, so expiring the commit that accepted one
never drops its record. The run also writes one `offset_gap` run record with the operator's reason.

### 6. Retention

The Kafka landing is **never expired** (the owner, 2026-11-28); the file kinds' 14-day landing expiry
does not touch it. Snapshot expiry runs on the landing and on bronze, and never removes the newest
connector snapshot of either, nor the chain from `main`'s head to it (`retain_last` is that depth):
without it the next run would land again from the log start and stage again from the landing's first
offset. Compaction of both tables runs in the product's own DAG with the Spark key, after `maintain`,
and is skipped while bronze's `audit` holds an unpublished batch, since moving `main` under it would
break its publish.

### 7. A topic belongs to one product of its owning domain

`contracts/domains.yaml` lists the topics each domain's capture writes (`market-capture` lists the nine
`market.crypto.v3.{raw,trades,book}.<venue>` topics). A Kafka product's topic must be one its own domain
lists (`connectors.topic-not-owned`), and a topic is read by one product of the set and listed by one
domain (`connectors.topic-claimed-twice`). Redpanda has no ACLs and every load shares the Spark key and
network, so this compile-time rule is what ties a product to its topic: a second product reading the
raw topic could build a copy under looser policies.

---

## Rationale

- **Per product, not shared.** ADR-056 chose it for every source: each product's failure, pause or
  rebuild is its own. A shared archive would put one more writer in front of every venue's products.
- **Every record, not the kept ones.** The offset checks hold only over unbroken ranges, and they are
  the proof of exactly-once. Moving the filter one step down costs only the control frames' bytes and
  keeps `batch_audit` and `offset_gaps` as they were proven in phase 001.
- **Staged by offset.** A through-offset in bronze's own summary is ADR-022's mechanism one hop
  downstream: rows and position in one commit, so there is no window between them for a kill.
- **Decoded at staging.** Spark decodes each frame there already to apply the filter. Writing the
  decoded columns costs nothing extra and lets every later layer be plain SQL.
- **Restage the unaudited, block the failed.** An unaudited batch is a crash artefact with nothing
  wrong in it; a failed one is a fact about the data, and only a person can say whether the data or
  the check is wrong.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Keep ADR-021's shared `raw.messages` for every venue | One job in front of every product: the 2026-11-26 failure, again |
| Land only the kept records, count the skipped ones in the summary (plan 004's text, ADR-056's line) | Fails `batch_audit` and `offset_gaps` at the first control frame; the counts die with the snapshots that carry them; the frozen landing has no column for a skipped record |
| Stage bronze keyed by run id, as the file connector does | A retry after the landing commit, or a kill between landing and staging, leaves a range no run stages |
| Keep the frame's bytes in bronze and decode in SQL | Trino has no Avro function; writing a decoder in SQL over `varbinary` is unreadable and untestable |
| A Trino user-defined function that decodes Avro | A Java plugin built and shipped with Trino, outside the pinned image, for one format |
| Trino's Kafka connector over the topic | Reads the topic live with no landing, no exactly-once position and no audit; the broker's retention becomes the archive |
| Reset any blocked batch automatically | A failed audit would be erased by the next run, and a wrong check or bad data would never be seen |
| Expire the landing after a window, as file landings are | The owner chose never, for now: the landing is the only copy once the broker evicts |
| Let any product read any topic | The broker cannot stop it; without a rule a copy under looser policies is one document away |

---

## Consequences

**Easier:** a killed load, at any point, needs no operator: the next run lands and stages each record
once. A batch stuck by a crash heals itself. Bronze can be rebuilt from the landing alone. Silver and
everything after it are dbt models over typed columns. A gap in the data is recorded on every later
landing commit and on the product's catalog page.

**Harder:** the non-trade frames, heartbeats and status, are landed too, so the landing is larger than
the trades it serves. The landing grows without bound: nothing deletes its rows until a retention is
decided. Two summaries now carry positions (the landing's end offsets and bronze's through-offsets),
and expiry must keep the newest connector snapshot of both. A failed audit stops the product's bronze
until a person clears it, while its landing keeps growing.

**Committed to:** the landing's nine columns and the summary keys `k2.job`, `k2.kafka-offsets`,
`k2.kafka-starts`, `k2.kafka-gaps`, `k2.src-snapshot-id` and `k2.kafka-skipped`, held by
`tests/test_frozen_interfaces.py`; a renamed key orphans every earlier snapshot as a resume point.
One bronze table per Kafka product, keyed `(kafka_partition, kafka_offset)`
(`connectors.kafka-bronze-shape`). The accept-data-loss conf shape, by name.

**Risks:** the mechanism rests on Iceberg 1.8.1 and Lakekeeper keeping snapshot summaries as written;
an upgrade that drops them would restart from the log start. Expiry misconfigured to keep no connector
snapshot would do the same. Disk: on one host, unbounded growth is the risk most likely to end this
decision.

**What is not yet verified:** claims a11 (an unaudited batch healed by the next run), a12 (a failed
batch never reset) and b6 (a topic trimmed before the product's first run) of `kafka-path.sh` are
written and pending their run. Not yet run anywhere: the chain with rows from a real topic through
Airflow, an accept-data-loss run through Airflow, a deploy that adds a Kafka product, the 48-hour soak
and its measurement of the landing's growth on disk.

**Revisit when:** the soak's benchmark reports the landing's growth on disk, then at the 3.0 release
gate, where the owner sets a landing retention; or `DockerDiskUsageHigh` fires (the VM disk the
volumes share over 80% used for 15 minutes); or a second Kafka product needs more than one bronze table; or Trino gains an
Avro function in a pinned release; or a second writer of the same topic is wanted.

---

## References

- Plan [`004-kraken-trades-instruments-security-master.md`](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md),
  "Connectors"; the [build breakdown](../research/2026-11-28-phases-004-005-build-breakdown.md),
  "Departure from plan 004: what the landing holds" and the review's findings 1 and 2.
- The frozen interface: [`phase-001-interfaces.md`](../development/phase-001-interfaces.md), "A Kafka
  product's pipeline, 2026-11-28", with its dated review lines D1 to D4, and "The access review's fixes",
  A2.
- Code: `docker/lake/kafka.py` (its header is the contract), `docker/lake/maintain.py`,
  `platform/k2gen/k2gen/checks/connectors.py`, `contracts/market-capture/kraken-trades/`.
- Runbooks: [accept-data-loss.md](../runbooks/accept-data-loss.md),
  [kafka-batch-blocked.md](../runbooks/kafka-batch-blocked.md),
  [onboard-a-kafka-product.md](../runbooks/onboard-a-kafka-product.md).
- [ADR-021](ADR-021-raw-first-archive-and-lineage.md), [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md),
  [ADR-047](ADR-047-ingest-batch-audit-after-publish.md), [ADR-056](ADR-056-unified-data-product-platform.md).
