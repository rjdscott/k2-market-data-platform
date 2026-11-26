# tests/parity

The pinned inputs of the last passing three-way OHLCV parity run
(`scripts/parity-ohlcv.sh`; runbook `docs/runbooks/clickhouse-rebuild-from-lake.md` §4).
`pinned.json` names the UTC day, the `lake.gold.ohlcv_1m` snapshot-id and the silver
snapshot-id per venue that `gold.trades` was built from — literal ids, never a moving
pointer, so the run is repeatable to the same answer. This is the seed of the Phase G CI
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

**Review:** the entry lands in a pull request with the pin it applies to, and the repo owner
reviews it. The reviewer checks that the row `audit_ref` names exists, with the same scope
and a cause that matches, and that the run's output shows the gap removing the differing
buckets of that minute and nothing else differing. For 2026-11-25 the query returns the one
row recorded at 2026-11-26 01:00:44 UTC (run read-only on 2026-11-26):

```bash
docker exec k2-trino trino --user reviewer --execute "SELECT run_ts, scope, passed, observed, detail
  FROM lake.audit.checks WHERE job = 'operator' AND check_name = 'capture_outage' ORDER BY run_ts"
# "2026-11-26 01:00:44.228723 UTC","stack/2026-11-25T22:33:43Z..2026-11-25T22:39:26Z","false","343","Host kernel OOM-killed the Docker VM ..."
```

`make test-python` checks the committed entry against every rule above except the last,
which needs the stack.

**Removing it:** delete the entry in the same pull request that pins a different day: a gap
outside the day compared stops the run, so re-pinning with `--pin-current` to another day
fails until it is gone.

The bars parity (`scripts/parity_bars.py`) has no known gaps: its three sides all read the
lake, so a loss in ClickHouse cannot reach it.
