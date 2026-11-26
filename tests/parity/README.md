# tests/parity

The pinned inputs of the last passing three-way OHLCV parity run
(`scripts/parity-ohlcv.sh`; runbook `docs/runbooks/clickhouse-rebuild-from-lake.md` §4).
`pinned.json` names the UTC day, the `lake.gold.ohlcv_1m` snapshot-id and the silver
snapshot-id per venue that `gold.trades` was built from — literal ids, never a moving
pointer, so the run is repeatable to the same answer. `dbt_trades_snapshot` is the `--dbt`
side's: the `gold.trades` snapshot D reads, recorded by `--pin-current <day> --dbt`. This is the seed of the Phase G CI
parity job (plan 004).

The `bars` block is `scripts/parity-bars.sh`'s: the UTC day, the `lake.gold.bars` and
`lake.gold.trades` snapshot ids, and the symbols the pure-Python reference was run over
(every symbol is a 10 M-row Python loop; two is the arbiter at CI cost).

## Known gaps

`known_gaps` is an optional list in `pinned.json`, of at most one entry. The entry names one
window of the pinned day, at most 60 minutes, that `scripts/parity_ohlcv.py` takes out of
ClickHouse against the lake and out of nothing else. Every other bucket is still compared at
tolerance zero, and the lake against DuckDB over silver and the lake against dbt still
compare the window: they check the lake's own computation, which a capture outage cannot
make differ.

**When one may be listed:** only for a capture outage already recorded as a row in
`lake.audit.checks` (the lake's table of check results), with its cause, where the two
stores cannot be made equal without deleting data: a hard kill of the host or the Docker VM
is the case it was built for
([`failure-recovery.md` §8](../../docs/runbooks/failure-recovery.md#8-host-or-docker-vm-killed-the-hot-tier-against-the-lake)).
Never for a difference nobody has explained, and never to get a run green: that is the
tolerance [ADR-029](../../docs/adr/ADR-029-research-production-parity-contract.md) rejects.
A second incident is not a second entry: it is the trigger in ADR-029's 2026-11-26 Outcome
to read loss windows from `lake.audit.checks` instead.

```json
"known_gaps": [
  {
    "start": "2001-01-01T00:01Z",
    "end": "2001-01-01T00:02Z",
    "sides": ["clickhouse"],
    "cause": "The host killed the Docker VM and the stack restarted; both stores hold different trades for this minute.",
    "recorded": "2001-01-02",
    "audit_ref": "lake.audit.checks job=operator check_name=capture_outage scope=stack/2001-01-01T00:01:43Z..2001-01-01T00:07:26Z"
  }
]
```

| Field | What it holds |
|-------|---------------|
| `start`, `end` | UTC, written exactly `YYYY-MM-DDTHH:MMZ`; `end` is exclusive, so the example is one minute |
| `sides` | `["clickhouse"]`, and nothing else: it says which comparison skips the window |
| `cause` | one sentence: what happened |
| `recorded` | the date the entry was added, written exactly `YYYY-MM-DD` |
| `audit_ref` | the row, written exactly `lake.audit.checks job=<job> check_name=<name> scope=stack/<start>..<end>`, each time `YYYY-MM-DDTHH:MM:SSZ`, as the row's `scope` holds it |

**The run stops**, non-zero and naming the entry, on: more than one entry; an unknown key or
a missing field; time text not in exactly the form above (no basic format, week date, missing
minutes, other zone or unpadded number); an `end` not after its `start`; a window longer than
60 minutes or outside the day compared; `sides` other than `["clickhouse"]`; an empty
`cause`; an `audit_ref` not in exactly the form above, or whose scope does not end after it
starts; and a window not inside the scope rounded out to whole minutes (an outage from
22:33:43 to 22:39:26 allows 22:33 to 22:40). It also fails when the window has no difference
left in it: a gap that matches no bucket is stale or mistyped, and one whose window now
matches in full is a dead exclusion that would hide the next difference there.

**Every run prints** the gap with its cause and `audit_ref`, the buckets it removed and how
many of them differed, and the comparison's bucket count after the removal. A run with the gap
applied ends `PARITY: ok (1 known gap applied, see above)`: `PARITY: PASS` is kept for a run
that left nothing out. With `--skip-clickhouse` the gap is printed as not applied.

**The run reads the row.** Before anything is compared, `scripts/parity_ohlcv.py` reads
`lake.audit.checks` over its own DuckDB connection for exactly the `job`, `check_name` and
`scope` that `audit_ref` names, and stops unless the row exists, with `job` `operator` and
`passed` false. It prints `known gap: audit row found, <audit_ref>` when it does. Two failures
are kept apart: a query that raises, because the catalog or storage cannot be reached, stops
the run with `could not read lake.audit.checks`, and a query that answers with no row stops it
with `has no row`. Neither passes, and an unreachable lake is never read as an empty answer.

**Review:** the entry lands in a pull request with the pin it applies to, and the repo owner
reviews it. The reviewer checks that the row's cause matches the entry's, and that the run's
output shows the gap removing the differing buckets of that minute and nothing else
differing. For 2026-11-25 the query returns the one row recorded at 2026-11-26 01:00:44 UTC
(run read-only on 2026-11-26):

```bash
docker exec k2-trino trino --user reviewer --execute "SELECT run_ts, scope, passed, observed, detail
  FROM lake.audit.checks WHERE job = 'operator' AND check_name = 'capture_outage' ORDER BY run_ts"
# "2026-11-26 01:00:44.228723 UTC","stack/2026-11-25T22:33:43Z..2026-11-25T22:39:26Z","false","343","Host kernel OOM-killed the Docker VM ..."
```

`make test-python` checks the committed entry against every rule above except the two that
need the stack: the row read and the difference left in the window.

**Removing it:** delete the entry in the same pull request that pins a different day: a gap
outside the day compared stops the run, so re-pinning with `--pin-current` to another day
fails until it is gone.

The bars parity (`scripts/parity_bars.py`) has no known gaps: its three sides all read the
lake, so a loss in ClickHouse cannot reach it.

## Keeping the pinned snapshots alive

A pin names snapshots by id. The nightly maintenance expires snapshots older than seven days
(it keeps the newest ten), and an expired snapshot cannot be read. An Iceberg *tag* is a named
pointer to one snapshot; expiry keeps every snapshot a tag points at, and the data files that
snapshot needs.

**The tag is what keeps a pin valid, not only what keeps it readable.** The scripts accept a
pinned id only if it is on `main`'s lineage, the chain of parent links walked back from the
snapshot `main` points at, or if the pin's own tag on that table points at it: `parity_<day>`,
the pinned day with underscores, or `parity_<day>_dbt` for `dbt_trades_snapshot`. Any other
tag validates nothing, so a scratch tag on a branch's commit cannot make it a pin. Both come
from the catalog's `loadTable` answer (`refs`, `type: tag`). Expiry breaks the chain: once the
snapshots after a pin are expired, the walk from `main` no longer reaches it, so about a week
after pinning the tag is the only thing that still lets the run use the pin. A tagged snapshot
whose ancestors are expired reads normally: on 2026-11-26 Trino read every pin below by id and
by tag name, and Trino and DuckDB each read a snapshot whose parent was already expired, by
id. DuckDB 1.4.4 reads by id only, which is what the scripts pass.

**What the run cannot check:** once expiry has cut the chain, nothing in the table says
whether a tagged snapshot was ever on `main` or only on a branch. So the tagging step checks it,
before tagging, while the chain still reaches the pin (below).

**A pin that cannot be resolved stops the run**, before anything is read at it, with the
table, the id and the way out: restore the tag if the snapshot is still in the table, or
re-pin with `--pin-current` on a green run and tag the new snapshots. There is no fallback:
until 2026-11-26 the run fell back to `main`'s current snapshot and still exited 0. The one
way to run at the current snapshot is `--pin-current`, which pins `main`'s head and writes it
here.

**The dbt side reads `dbt_trades_snapshot` and nothing else.** `--pin-current <day> --dbt`
records it while every `gold.trades` snapshot the day's lake candles were computed from is
still held: the newest of them by sequence number, which holds every trade those candles
counted. It is never re-derived, because a week later all of them but the tagged one are
expired and "the newest still held" could be an older one. Every `--dbt` run requires that it
is one of the day's source snapshots, that it passes the rule above under `parity_<day>_dbt`,
and that no source snapshot still held is newer. A `--dbt` run with a pin that lacks it stops,
saying to re-pin with `--dbt`; a re-pin without `--dbt` drops it.

So each pinned snapshot carries its tag, `parity_<day>`. A tag's 180 days count from the
snapshot's own commit time, not from tagging: these lapse about 2027-05-25.

| Table | Tag | Pin field |
|---|---|---|
| `lake.gold.ohlcv_1m` | `parity_2026_11_25` | `ohlcv_1m_snapshot` |
| `lake.silver.trades_binance`, `_coinbase`, `_kraken` | `parity_2026_11_25` | `silver_snapshots` |
| `lake.gold.bars` | `parity_2026_11_25` | `bars.bars_snapshot` |
| `lake.gold.trades` | `parity_2026_11_25` | `bars.trades_snapshot` |
| `lake.gold.trades` | `parity_2026_11_25_dbt` | `dbt_trades_snapshot` |

Before tagging, check each id is on `main`'s lineage (read-only; 1 if it is, 0 for a branch's
commit or an id from elsewhere; run 2026-11-26):

```bash
docker exec k2-trino trino --execute 'SELECT count(*) FROM lake.gold."trades$history" WHERE snapshot_id = 6911535457573800498 AND is_current_ancestor'
#   "1"
```

Created on 2026-11-26 in one Spark session in `k2-spark-iceberg`, one statement per row:

```sql
ALTER TABLE lake.gold.ohlcv_1m CREATE TAG `parity_2026_11_25` AS OF VERSION <snapshot id> RETAIN 180 DAYS;
```

Check them (the lake is read-only through Trino, so this is safe at any time):

```bash
docker exec k2-trino trino --execute 'SELECT name, type, snapshot_id FROM lake.gold."trades$refs" ORDER BY 1'
#   "main","BRANCH",...
#   "parity_2026_11_25","TAG","2055991547363439727"
#   "parity_2026_11_25_dbt","TAG","6911535457573800498"

# Is a pinned id still in the table? 1 if so, 0 if expiry removed it (run 2026-11-26):
docker exec k2-trino trino --execute 'SELECT count(*) FROM lake.gold."ohlcv_1m$snapshots" WHERE snapshot_id = 1578447691193969083'
#   "1"
```

After a re-pin: tag the new snapshots the same way (an untagged pin stops the run once expiry
removes it, about a week later), then drop the old tags
(`ALTER TABLE <table> DROP TAG parity_<old day>`), or their files stay for the 180 days. A tag
is not a branch: nothing is written through it, and readers that follow `main` never see it.
