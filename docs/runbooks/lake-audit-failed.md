# Runbook: A lake audit failed

The nightly maintenance run ends in **six checks of four kinds** over the lake — two of the
kinds run against both bronze tables — and any failure exits non-zero, fails the Prefect run
and raises an alert. `docker/lake/maintenance.py`'s `AUDITS` tuple is the list:
`offset_continuity` on `raw.messages`, `duplicate_identifiers` on each of `bronze.trades`
and `bronze.book_snapshots_l2`, `venue_replay` on `bronze.trades`, and `sequence_gaps` on
each bronze table. They ask different questions, have different causes, and two of the four
kinds are **findings to record** rather than faults to repair — telling them apart is what
this runbook is for.

`venue_replay` is the odd one out and it is here so that it cannot be mistaken for a
failure: it is **informational**, has no pass/fail semantics, and is excluded from the
`N audits passed` summary line (`INFORMATIONAL` in `maintenance.py`). It publishes the
Coinbase replay rate that §2 would otherwise be blamed for — see §4. Stage 2 of the ingest
also files rows into the same table under `job='ingest'` — see §5.

It does **not** cover ingest being behind ([lake-ingest-lag.md](./lake-ingest-lag.md)) or
the tier being down ([lake-recovery.md](./lake-recovery.md)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Not yet verified — the Phase D burn-in fills this in.** The audits exist in
> `docker/lake/maintenance.py` and every tuple and check name below is read from it, but
> nothing here has been run against a populated `raw`/`bronze`/`audit`. Commands marked ✅
> were run read-only against the running stack on 2026-08-26.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | `offset_continuity` — a hole in the archive | **investigation, not repair** | not yet verified — Phase D burn-in |
| 2 | `duplicate_identifiers` — a row landed twice | < 60 min | not yet verified — Phase D burn-in |
| 3 | `sequence_gaps` — venue sequence discontinuity | **investigation, not repair** | not yet verified — Phase D burn-in |
| 4 | `venue_replay` — informational; **cannot fail** | n/a — read the rate, do not repair it | not yet verified — Phase D burn-in |
| 5 | `unresolvable_schema_id` — filed by the ingest, not by the audit | < 60 min | not yet verified — Phase D burn-in |

---

## Start here: what failed, and when did it last pass?

`lake.audit.checks` is append-only, one row per check per run, so the question "when did
this last hold" is a query rather than a log grep. That is the whole reason it is a table.

**Detection** — `LakeAuditFailed` from `docker/prometheus/rules/lake-alerts.yml`, over
`k2_lake_audit_failures_total`, which `docker/lake/metrics.py` reads from this table.

**Prometheus knows how many, the table knows which.** `k2_lake_audit_failures_total` is
declared label-free in `docker/lake/metrics.py` — it is the failed-check *count* stamped
into the `audit.checks` snapshot summary by the run that wrote it — and `LakeAuditFailed`
adds only `severity`, `component` and `tier`. There is no `check_name` or `scope` label to
select on, on either the metric or the alert; the check identity only ever exists as a row.

```bash
# How many checks failed in the last maintenance run?  not yet run — Phase D burn-in
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=k2_lake_audit_failures_total' | jq -r '.data.result[].value[1]'

# Is the alert firing, and since when?                                     ✅ verified
curl -s localhost:9090/api/v1/alerts | \
  jq -r '.data.alerts[] | select(.labels.alertname=="LakeAuditFailed")
         | "\(.labels.severity) \(.activeAt)"'
# (empty on 2026-08-26 — the only alert firing is CaptureFeedStale)
```

```sql
-- WHICH checks failed, and the last time each scope passed. This query, not the
-- alert labels, is the diagnosis.            not yet run — Phase D burn-in
SELECT run_ts, check_name, scope, observed, detail
FROM lake.audit.checks
WHERE passed = false AND run_ts >= current_date() - INTERVAL 7 DAYS
ORDER BY run_ts DESC;

SELECT check_name, scope, max(run_ts) AS last_pass
FROM lake.audit.checks WHERE passed = true
GROUP BY check_name, scope ORDER BY last_pass;
```

**A failed audit row is never edited or deleted.** It is the record that something was
wrong on a given night; a later passing row is how recovery is expressed. Removing it makes
the archive's completeness claim untrue in exactly the way the audits exist to prevent.

---

## 1. `offset_continuity` — a hole in the archive

**Symptom** — the audit reports non-abutting offset ranges for a `(topic, partition)`:
consecutive ingest snapshots leave a numeric gap, or the day seam does not join up.

**Expected behaviour** — this is the archive's most serious check, and it fails for exactly
two reasons that demand opposite responses:

| Cause | Distinguishing evidence | Response |
|---|---|---|
| **Real loss** — records were evicted from Redpanda before ingest read them | a `failOnDataLoss` failure in the same window ([lake-ingest-lag.md §3](./lake-ingest-lag.md#3-failondataloss--the-offsets-point-below-what-the-broker-holds)); the broker's `LOG-START-OFFSET` above the last committed offset | **record the gap** — it is unrecoverable, public feeds do not replay |
| **An audit bug** — a legitimate seam counted as a gap | no ingest failure, no eviction, and the "gap" sits at a day boundary, a topic recreation, or a compaction snapshot | **fix the audit** — a false positive here trains people to ignore the one alert that must never be ignored |

**Recovery**

```bash
# 1. Was there an eviction? Committed offsets vs what the broker holds.    ✅ verified
docker exec k2-redpanda rpk topic describe -p market.crypto.v3.raw.kraken
```

```sql
-- 2. The offsets on the snapshots either side of the reported gap.
--    not yet run — Phase D burn-in
SELECT snapshot_id, committed_at, summary['k2.kafka-offsets']
FROM lake.raw.messages.snapshots ORDER BY committed_at DESC LIMIT 10;

-- 3. Confirm against the data rather than the metadata: is the offset
--    sequence actually dense across the reported boundary?
SELECT offset FROM lake.raw.messages
WHERE topic = 'market.crypto.v3.raw.kraken' AND partition = 7
  AND offset BETWEEN 918430 AND 918460 ORDER BY offset;
```

**If the gap is real**, its deliverable is a record, not a repair: the topic, partition,
offset range, row count and wall-clock window, written into `lake.audit.checks` and into
this runbook's incident log. Then fix the cause upstream — ingest downtime, or retention
too short for the real rate.

**If the gap is not real**, the bug is in the audit, and it is worth naming the two
lookalikes that will produce it. **Compaction snapshots carry no `k2.kafka-offsets`** and
must be skipped when walking the offset history
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)) — an audit that treats a
compaction snapshot as an ingest one sees a gap the size of a night. And a **topic
recreation resets the offset space**, so continuity is only meaningful within one epoch of
a topic; that is an event to record, not a routine operation
([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md), *Risks*).

**Measured** — not yet verified.

---

## 2. `duplicate_identifiers` — a row landed twice

**Symptom** — the audit finds more than one row per identifier-field tuple. The tuples are
the ones `docker/lake/maintenance.py` passes into `audit_duplicates`, which are the
`SET IDENTIFIER FIELDS` clauses in `docker/lake/ddl/lake.sql`:

| Table | Duplicate key |
|---|---|
| `bronze.trades` | `(exchange, symbol, trade_id, conn_id)` — **`conn_id` included** |
| `bronze.book_snapshots_l2` | `(exchange, symbol, conn_id, snapshot_ts_ns)` |

**Both keys were settled by measurement, and the trade key is the most misread line on this
page.** Over 287,184 trades captured on 2026-08-26, `(exchange, symbol, trade_id)` *alone*
had **956 duplicated keys** — every one Coinbase, every one a pair of rows with identical
price, qty, side and `exchange_ts` under two different `conn_id`s. Coinbase replays recent
`market_trades` on resubscribe, so a reconnect genuinely delivers the same trade twice and
the append-only archive genuinely holds both frames. The book table went the other way for
the same kind of reason: `conn_msg_seq` records which frame the book last folded in, so a
quiet book gives two consecutive 1 Hz samples the same value — **484 duplicated
`(exchange, symbol, conn_id, conn_msg_seq)` keys over 47,331 snapshots** — while the
sampler's own clock, `snapshot_ts_ns`, had zero. Both numbers and their commands are in
`docker/lake/ddl/lake.sql`.

**So cross-`conn_id` replay of the same `(exchange, symbol, trade_id)` is EXPECTED, and it
is not this check.** It is counted separately by `venue_replay` (§4), which reports it as a
rate and never fails. On the measured 2026-08-26 rate that is roughly a thousand trades a
day; escalating it as an ingest bug is escalating the venue's documented behaviour.

**Expected behaviour** — a `duplicate_identifiers` failure on the **four-column** key is the
one that means the exactly-once contract broke, and that should be impossible. The ingest is
exactly-once by construction: a run's own commit moves its start offset, so no run can
re-read a range another run committed
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)). A duplicate on the full
key therefore means one of the mechanism's assumptions has broken — not that a venue
repeated itself.

**Recovery**

```sql
-- 1. What exactly is duplicated, and does it come from one source row or two?
--    Note the four-column key: dropping conn_id here re-finds the venue replay
--    §4 already reports.                             not yet run — Phase D burn-in
SELECT exchange, symbol, trade_id, conn_id, count(*) AS n,
       count(DISTINCT (src_topic, src_partition, src_offset)) AS distinct_sources
FROM lake.bronze.trades
WHERE exchange_ts >= current_date() - INTERVAL 1 DAYS
GROUP BY exchange, symbol, trade_id, conn_id HAVING count(*) > 1 LIMIT 20;
```

The `distinct_sources` column splits the diagnosis in one query — this is why the lineage
columns exist:

| `distinct_sources` | Meaning | Where the bug is |
|---|---|---|
| **1** | one archived frame decoded into two bronze rows | stage 2 ran twice over the same source snapshot range — check `k2.src-snapshot-id` on consecutive `bronze.*` commits |
| **2** | two archived frames, on the *same connection*, carry the same venue trade id | `raw.messages` holds the record twice, so run §1 first: if `offset_continuity` also fails, stage 1 wrote a range twice. If it passes, the venue re-sent the trade on one connection — a **data finding**, not an ingest bug |

```bash
# 2. Was ingest running more than once at a time? Concurrency 1 is a
#    correctness setting, not a politeness one.   not yet run — Phase D burn-in
docker exec k2-prefect-server prefect deployment inspect 'lake-ingest/lake-ingest-5min'
```

```sql
-- 3. Source-snapshot lineage on the bronze commits around the window.
SELECT snapshot_id, committed_at, summary['k2.src-snapshot-id'], summary['added-records']
FROM lake.bronze.trades.snapshots ORDER BY committed_at DESC LIMIT 10;
```

**Do not delete the duplicates first.** They are the evidence, the tables are copy-on-write
so a delete rewrites files, and a duplicate that is *the venue's* fault must not be silently
removed — it is a fact about the feed and belongs in ADR-027's or ADR-024's Outcome, with
the measurement behind it. Fix the cause; then decide, deliberately, whether to rewrite the
affected partition from `raw.messages`, which is always available because `bronze.*` is a
pure function of the archive.

**Measured** — not yet verified.

---

## 3. `sequence_gaps` — venue sequence discontinuity

**Symptom** — the audit's lag-over-`seq` check finds a jump in a venue's sequence numbers
within one `conn_id`.

**Expected behaviour** — **this is a data finding, not a platform fault**, and it usually
means a message was lost between the venue and this host. It cannot be repaired: public
feeds do not replay, so the frames are gone. The counter and the audit exist so the loss is
*recorded* rather than silent — v2 had no gap detection at all, which is why a dropped
message there was invisible ([ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md),
Context).

Sequencing means three different things across the three venues, and reading the failure
wrong is easy. The authoritative table is in
[ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md); the short form:

| Venue | `seq` is | A "gap" means |
|---|---|---|
| **Binance** | `lastUpdateId` | it regressed or repeated backwards |
| **Kraken** | **always 0** — the venue does not sequence this stream | nothing. `seq = 0` is a documented sentinel for "unanswerable", and an audit treating it as a gap is an **audit bug** |
| **Coinbase** | `sequence_num`, **connection-wide across all channels** | a message was lost — but not which stream lost it |

**Recovery**

```bash
# 1. Did capture see it too? The capture-side counters are the corroboration. ✅ verified
for m in gaps reconnects resyncs; do
  echo "== $m"
  curl -s --get localhost:9090/api/v1/query \
    --data-urlencode "query=increase(k2_capture_${m}_total[24h])" | \
    jq -r '.data.result[] | "  \(.metric.exchange) \(.value[1])"'
done
```

```sql
-- 2. Does the gap sit at a conn_id boundary? If it does, it is a reconnect,
--    not a loss — a new connection starts a new sequence space.
--    not yet run — Phase D burn-in
SELECT conn_id, min(seq), max(seq), count(*), min(exchange_ts), max(exchange_ts)
FROM lake.bronze.trades
WHERE exchange = 'coinbase' AND exchange_ts >= current_date() - INTERVAL 1 DAYS
GROUP BY conn_id ORDER BY min(exchange_ts);
```

Then classify — the same three-way split the capture runbook uses, applied to the archive
instead of to a counter:

- **Gap at a `conn_id` change** → expected. Binance also does a scheduled reconnect at
  ~23 h of connection life. If the audit counts these, that is an **audit bug**: the
  boundary is detectable from `conn_id` and should be excluded.
- **Gap inside one `conn_id`, with a matching `k2_capture_gaps_total` increment** → a real
  loss. Record the window.
- **Gap in the archive with no capture-side increment** → the two disagree, which is worse
  than either alone. One of them is wrong, and finding out which is the priority.

Capture-side triage is [capture-sequence-gaps.md](./capture-sequence-gaps.md); this
runbook's job is the archive-side record.

**Measured** — not yet verified.

---

## 4. `venue_replay` — informational, and it cannot fail

**Symptom** — none. This check never fails; `passed` is always `true` and `maintenance.py`
lists it under `INFORMATIONAL`, so it is excluded from the `N audits passed` count and
printed with an `info` marker rather than `ok`. It cannot raise `LakeAuditFailed`.

**What it measures** — how many logical trades arrived on more than one connection:
`(exchange, symbol, trade_id)` groups on `bronze.trades` with `count(DISTINCT conn_id) > 1`.
That is the Coinbase resubscribe replay described in §2, measured at **956 such trades in
287,184 over 30 min on 2026-08-26**. Those rows are real frames that really arrived, and
the archive keeps both.

**What to do with it** — read the *rate*, not the level. The number exists so that a
**change** in it is visible: a jump means reconnect churn, which is a capture-tier
question ([capture-sequence-gaps.md](./capture-sequence-gaps.md)), not a lake one.

```sql
-- The replay count per run, over the last fortnight. A step change is the signal;
-- a steady few hundred a day is the venue behaving as documented.
-- not yet run — Phase D burn-in
SELECT run_ts, observed, detail
FROM lake.audit.checks
WHERE check_name = 'venue_replay' AND run_ts >= current_date() - INTERVAL 14 DAYS
ORDER BY run_ts;
```

A research query that wants one row per logical trade deduplicates on
`(exchange, symbol, trade_id)` itself — that is the logical key, and `conn_id` is in the
*identifier* key so that the storage claim stays true without hiding the replay
(`docker/lake/ddl/lake.sql`, `bronze.trades`).

**Measured** — not yet verified.

---

## 5. `unresolvable_schema_id` — a record framed with a schema the registry will not serve

**Symptom** — a row in `lake.audit.checks` with `job = 'ingest'`, `check_name =
'unresolvable_schema_id'`, `scope` of the form `<table>/schema_id=<n>` and `passed = false`.
Stage 2 prints `stage 2: SKIPPING schema id <n>: … — filing an audit row` and carries on.

**Expected behaviour — the run does not die, and that is deliberate.** The record is already
in `raw.messages`, `raw.messages` is never expired, and stage 2 re-reads the same snapshot
range until it succeeds — so raising on an unregistered id would be a **permanent** outage
from one bad frame rather than one skipped id (`fetch_schema` /
`UnresolvableSchema` in `docker/lake/ingest.py`). Everything else in the batch decodes
normally; only that id is skipped.

**This has its own alert, and it does not disturb `LakeAuditFailed`.** `audit.checks` has
two writers, so each gauge names the job whose number it claims to be:
`k2_lake_audit_failures_total` is read off the newest snapshot carrying
`k2.job=maintenance`, `k2_lake_unresolvable_schema_ids_total` off the newest carrying
`k2.job=ingest` (`latest_job_summary()` in `docker/lake/metrics.py`). Reading either off
the *current* snapshot is the bug that pair replaced: the ingest's commit lands on top of
the audit's, so its own count — 0 for a clean run — used to become
`k2_lake_audit_failures_total` and clear a firing `LakeAuditFailed` with no audit having
passed. `LakeUnresolvableSchemaId` (warning, `for: 15m`) is this finding's own signal, and
the regression is asserted in `docker/prometheus/rules/tests/lake-alerts_test.yml`.

Stage 2 re-files the row every cycle while the id stays unserved, so the gauge holds above
0 until the schema is registered — which is what makes a 15-minute `for` a threshold
rather than a race against the next commit. All of a run's findings ride on one commit, so
the gauge is the number of unserved ids that run saw, not 1 for whichever row went last.

**Recovery** — the schema id is the whole diagnosis.

```bash
# 1. Does the registry hold it at all?             not yet run — Phase D burn-in
curl -s "localhost:8081/schemas/ids/<n>" | jq .

# 2. What is on the topic right now, and how is it framed?
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --probe
```

Three causes, in order of likelihood: the registry volume was rebuilt and lost the
subject (restore it, then re-run the ingest — the range is re-read and the rows decode);
a foreign producer wrote to a v3 topic (a capture-tier finding, and the archive keeps the
bytes either way); or the id is genuinely fabricated, which `wire.schema_id_guarded_expr`
already rejects at stage 1 for short frames.

**The skipped rows are not lost, but they are not always retried either**, and the
difference is worth knowing before deciding how fast to act (`_decode_into` in
`docker/lake/ingest.py`):

- **Some other id in the batch decoded** → the commit advances `k2.src-snapshot-id` past the
  whole range, skipped rows included. Fixing the registry afterwards does *not* bring them
  back on its own; they need a bounded re-read of that snapshot range from `raw.messages`.
- **Nothing in the batch decoded** → no commit, `k2.src-snapshot-id` does not move, and the
  next run re-reads the same range and decodes it once the id resolves.

Either way the bytes are in `raw.messages` verbatim and `bronze.*` stays a pure function of
the archive, so the repair is a replay rather than a loss. Fix the registry first.

**Measured** — not yet verified.

---

## After any failure: do not silence the audit

The audit fails the maintenance run on purpose — that is
[ADR-017](../adr/ADR-017-iceberg-maintenance-pipeline.md)'s "fail-fast for audit" policy
carried into the lake. Loosening a threshold to get a green run turns the one signal that
proves the archive is complete into decoration. If a venue genuinely behaves in a way the
audit calls a failure, that is a fact worth recording in the relevant ADR's Outcome, with
the measurement behind it — not a threshold to move.

---

## Failure modes / incidents

_None yet. Appended with their date as they happen; never overwritten. **Every real gap
found by §1 or §3 is recorded here**, with its window and its evidence._

---

## Related

- [ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md) — offset continuity, and why compaction snapshots must be skipped when reading the offset history
- [ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md) — the identifier fields the duplicate check uses, and why the book table's differ from the trade table's
- [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql) — the `SET IDENTIFIER FIELDS` clauses §2 asserts, with the 956/287,184 and 484/47,331 measurements that put `conn_id` and `snapshot_ts_ns` in them
- [`docker/lake/maintenance.py`](../../docker/lake/maintenance.py) — the `AUDITS` tuple: the six checks, their scopes, and `INFORMATIONAL`
- [ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md) — three sequencing mechanisms, and the `seq = 0` sentinel
- [ADR-017](../adr/ADR-017-iceberg-maintenance-pipeline.md) — the compact → expire → audit ordering and the fail-fast-on-audit policy the lake inherits
- [ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md) — the lineage columns that make §2 a one-query diagnosis
- [capture-sequence-gaps.md](./capture-sequence-gaps.md) — the capture-side half of §3
- [lake-ingest-lag.md](./lake-ingest-lag.md) — `failOnDataLoss`, the usual cause of a real §1 failure

---

**Last verified:** not yet verified — the audit code exists but has never run against a
populated lake, and the `raw`/`bronze`/`audit` tables hold no rows on this host. Commands
marked ✅ were run read-only against the running stack on 2026-08-26. Stamp this line at the
Phase D burn-in and replace each "not yet run" marker with real output.
