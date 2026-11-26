#!/usr/bin/env python3
"""
Three-way OHLCV parity at a pinned lake snapshot — the Phase E exit gate and the
seed of the Phase G CI parity job (plan 004).

    uv run --no-project --with duckdb==1.4.4 --with clickhouse-connect \
        python scripts/parity_ohlcv.py --day 2026-08-27 \
        --trades-snapshot <lake.gold.trades snapshot id> --ohlcv-snapshot <lake.gold.ohlcv_1m snapshot id>

Three computations of the same 1-minute candles for one UTC day, compared at
tolerance ZERO on open/high/low/close (1e-8 fixed point), trade_count and volume:

  A  ClickHouse  gold.ohlcv_live(bucket = 60)      computed on read over gold.trades FINAL
  B  lake        gold.ohlcv_1m                     materialised by docker/lake/gold.py, read
                                                    by DuckDB at the pinned snapshot
  C  DuckDB      over lake.silver.trades_*         dedup applied in the query
                                                    (venue_replay = false), at the pinned
                                                    silver snapshots gold.trades recorded

Pinned, never `latest`: a parity check that reads whatever is current cannot be
re-run to the same answer, and a check that cannot be re-run is not a check.
`tests/parity/pinned.json` holds the ids of the last run this passed against. A pin
neither on main's lineage nor named by a tag stops the run (`resolve_snapshot`).

A, B and C compare volume as round(float(volume), 8): a double, exact to about 15
significant digits, so not to the eighth decimal of a large volume.

`--dbt` adds a fourth, compared with B on the same columns and on quote_volume,
volume and quote_volume as decimals, digit for digit:

  D  dbt on Trino  the compiled SQL of dbt's market_analytics.candles.ohlcv_1m,
                   its staging view inlined and lake.gold.trades read FOR VERSION
                   AS OF the pin's dbt_trades_snapshot: the newest gold.trades
                   snapshot B's rows for the day were computed from (their
                   src_snapshot_id), recorded by --dbt --write-pin

D is the SQL dbt builds the mart with, run on the trades Spark last read for the
day, not the table dbt last built: that table holds the lake as it was at build
time. `--skip-silver` and `--skip-clickhouse` leave B against D alone; with no
side to compare B with, the script refuses to run. With D, quote_volume typed
differently by the two engines is a failure (ADR-035).

Exit 0 when every side run equals B on every bucket of the day, non-zero with the
first differences printed otherwise.

`--known-gaps` (the wrapper passes `known_gaps` from the pin) takes one listed window,
at most 60 minutes, out of A against B and nothing else: a capture outage recorded as a
row in `lake.audit.checks`, inside whose scope the window must sit, where the stores
cannot be made equal without deleting data (tests/parity/README.md). Before anything is
compared the run reads that row from `lake.audit.checks` and stops if it is absent, not a
failed operator row, or cannot be read (`confirm_audit_row`). The gap is printed
with its cause, its audit row and the buckets it removed; a gap whose window has no
difference left fails the run, and a run with a gap applied ends `PARITY: ok (...)`,
never `PARITY: PASS`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

LAKEKEEPER = os.environ.get("K2_LAKEKEEPER_HOST_URL", "http://localhost:18181/catalog")
S3_ENDPOINT = os.environ.get("K2_S3_HOST_ENDPOINT", "localhost:9000")
CH_HOST = os.environ.get("K2_CLICKHOUSE_HOST", "localhost")
TRINO_HOST = os.environ.get("K2_TRINO_HOST", "localhost")
TRINO_PORT = int(os.environ.get("K2_TRINO_PORT", "8088"))
DBT = Path(__file__).resolve().parent.parent / "dbt"
DBT_MODEL = "model.k2.ohlcv_1m"
DBT_TRADES = "source.k2.gold.trades"
SCALE = 100_000_000

KEY = ["exchange", "canonical_symbol", "window_start"]
COLS = ["open_e8", "high_e8", "low_e8", "close_e8", "trade_count", "volume"]

# Known gaps (tests/parity/README.md): their fields, the one comparison a gap may name,
# the bounds that keep an exclusion to one short window, and the text forms it must use.
GAP_FIELDS = {"start", "end", "sides", "cause", "recorded", "audit_ref"}
GAP_SIDES = ["clickhouse"]
MAX_GAPS = 1
MAX_GAP = timedelta(minutes=60)
MINUTE, SECOND, DAY = "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"
AUDIT_REF = re.compile(r"lake\.audit\.checks job=([a-z_]+) check_name=([a-z_]+) scope=stack/([^ ]+)\.\.([^ ]+)")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "notebooks"))  # for k2lake, once


def pin_tag(day: str, suffix: str = "") -> str:
    """The one tag that validates a pin for `day`: `parity_2026_11_25`, or `parity_2026_11_25_dbt` (tests/parity/README.md)."""
    return f"parity_{day.replace('-', '_')}{suffix}"


def table_refs(table: str) -> tuple:
    """(main's snapshot ids, current first; {tag name: snapshot id}) for `table`, from one loadTable.

    notebooks/k2lake.py's `lineage` and `tags`, the one copy.
    """
    import k2lake  # imports duckdb, so not at module level

    meta = k2lake.table_metadata(table)
    return k2lake.lineage(meta), k2lake.tags(meta)


def resolve_snapshot(lineage: list, tags: dict, table: str, pinned: int, tag: str) -> int:
    """The pinned snapshot if it is on `main`'s lineage or the pin's own tag `tag` points at it; otherwise the run stops.

    `lineage` walks `parent-snapshot-id` from main's head and stops at the first
    parent that expiry removed. Expiry keeps a tagged snapshot, so a pin a week old
    is still in the table and readable, but off the walk: a resolver that read the
    walk alone took it for stale. The tag is what keeps a pin valid, not only what
    keeps it readable (tests/parity/README.md). Only the pin's own tag counts,
    `pin_tag(day)`: any tag on the table would let a scratch tag on a branch's commit
    validate it. A snapshot neither on the walk nor under that tag, a branch's
    commit among them, is refused. What this cannot check: once expiry has cut the
    walk, nothing records whether the tagged snapshot was ever on main, so the
    tagging step checks that before it tags.

    Anything else stops the run, naming the table, the id and the way out. Until
    2026-11-26 this fell back to main's current snapshot with one printed line and
    left the exit code to parity, so a week after pinning the gate compared engines
    on live data and called it the pin. The only path to "current" is an explicit
    `--pin-current`, which passes main's head as the pin.

    `lineage` and `tags` are `table_refs(table)`, passed in so the decision is
    testable without a catalog (tests/test_parity.py).
    """
    if pinned in lineage:
        return pinned
    if tags.get(tag) == pinned:
        print(f"pin {pinned} of {table}: held by its tag {tag}, off main's walk because expiry removed snapshots after it")
        return pinned
    raise SystemExit(
        f"{table}: pinned snapshot {pinned} is not on main's lineage and its tag {tag} does not point at it (expired, "
        f"a branch's commit, or from another table or host), so this run cannot read the pin. Nothing was compared. "
        f"Either restore the tag {tag} on {pinned} if the snapshot is still in the table, or re-pin with --pin-current "
        f"on a green run and tag the new snapshots (tests/parity/README.md, 'Keeping the pinned snapshots alive')."
    )


def duck(minio_user: str, minio_pass: str):
    import duckdb  # imported here so `resolve_snapshot` is importable without it

    c = duckdb.connect()
    # Spark writes Iceberg TIMESTAMP as timestamptz; DuckDB renders those in the
    # session time zone, which defaults to the HOST's. On a UTC+10 machine the
    # first run of this script bucketed every trade ten hours late and disagreed
    # with ClickHouse on all 29,407 buckets (2026-08-27). UTC, explicitly.
    c.execute("SET TimeZone = 'UTC'")
    c.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    c.execute(
        f"""CREATE SECRET s3sec (TYPE S3, KEY_ID '{minio_user}', SECRET '{minio_pass}',
            ENDPOINT '{S3_ENDPOINT}', URL_STYLE 'path', USE_SSL false, REGION 'local-01')"""
    )
    # Spike S10 variant A: no auth, no access delegation (Lakekeeper vends no credentials here).
    c.execute(
        f"ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT '{LAKEKEEPER}', AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"
    )
    return c


def lake_candles(c, day: str, snapshot: int) -> list:
    """B: the materialised 1m candles, at the pinned snapshot. quote_volume last, for D only."""
    return c.execute(
        f"""SELECT exchange, canonical_symbol, window_start, open_e8, high_e8, low_e8, close_e8, trade_count, volume, quote_volume
            FROM lake.gold.ohlcv_1m AT (VERSION => {snapshot})
            WHERE CAST(window_start AS DATE) = DATE '{day}'
            ORDER BY 1, 2, 3"""
    ).fetchall()


def silver_candles(c, day: str, snapshots: dict) -> list:
    """C: candles from silver, dedup in the query, at the pinned silver snapshots."""
    parts = []
    for venue, snap in snapshots.items():
        parts.append(
            f"""SELECT '{venue}' AS exchange, canonical_symbol, trade_id, trade_seq, price, qty, exchange_ts, recv_ts_ns
                FROM lake.silver.trades_{venue} AT (VERSION => {snap}) WHERE NOT venue_replay"""
        )
    union = " UNION ALL ".join(parts)
    return c.execute(
        f"""WITH t AS ({union}),
            b AS (SELECT exchange, canonical_symbol, date_trunc('minute', exchange_ts) AS window_start,
                         CAST(price * {SCALE} AS BIGINT) AS p8, qty, exchange_ts, recv_ts_ns, trade_seq
                  FROM t WHERE CAST(exchange_ts AS DATE) = DATE '{day}')
            SELECT exchange, canonical_symbol, window_start,
                   arg_min(p8, ROW(exchange_ts, recv_ts_ns, trade_seq)) AS open_e8, max(p8) AS high_e8, min(p8) AS low_e8,
                   arg_max(p8, ROW(exchange_ts, recv_ts_ns, trade_seq)) AS close_e8, count(*) AS trade_count,
                   CAST(sum(qty) AS DECIMAL(38,10)) AS volume
            FROM b GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
    ).fetchall()


def clickhouse_candles(day: str, password: str) -> list:
    """A: ClickHouse's on-read view over gold.trades FINAL (the topic-fed copy)."""
    import clickhouse_connect

    client = clickhouse_connect.get_client(host=CH_HOST, port=8123, username="default", password=password)
    rows = client.query(
        f"""SELECT exchange, canonical_symbol, window_start, open_e8, high_e8, low_e8, close_e8, trade_count, volume
            FROM gold.ohlcv_live(bucket = 60)
            WHERE toDate(window_start) = toDate('{day}')
            ORDER BY 1, 2, 3"""
    ).result_rows
    return rows


def _source_seq(src_ids, snapshots) -> dict:
    """{snapshot id: sequence number} from `iceberg_snapshots` rows; a NULL source id stops the run."""
    if not src_ids or None in src_ids:
        raise SystemExit("lake.gold.ohlcv_1m names no gold.trades snapshot, or a NULL one, for the day: D has nothing it can be pinned to")
    return {sid: n for n, sid in snapshots}


def newest_source_snapshot(src_ids, snapshots) -> int:
    """The newest of the gold.trades snapshots `src_ids` names, by sequence number: what `--dbt --write-pin` records.

    `snapshots` is (sequence_number, snapshot_id) rows, as `iceberg_snapshots`
    returns them. Snapshot ids are random, so the newest is the highest sequence
    number, never the highest id. Every bucket of a finished day was last computed
    at one of `src_ids`, and a trade landing later would have recomputed its bucket
    at a later one, so the newest holds every trade B counted. Every one must still
    be held: with one expired, the newest cannot be told, so nothing is pinned.
    """
    seq = _source_seq(src_ids, snapshots)
    gone = sorted(set(src_ids) - set(seq))
    if gone:
        raise SystemExit(f"gold.trades snapshot(s) {gone} are expired or from another table: the newest source snapshot cannot be told, so D cannot be pinned for this day")
    return max(set(src_ids), key=seq.__getitem__)


def confirm_dbt_snapshot(pinned, src_ids, snapshots, lineage: list, tags: dict, tag: str) -> int:
    """`pinned`, the pin's `dbt_trades_snapshot`, if it is still the snapshot D must read; otherwise the run stops.

    Recorded by `--dbt --write-pin` while every source snapshot was held, and never
    re-derived: a week later expiry has removed all of them but the tagged one, and
    "the newest still held" could then be an older snapshot, at which a candle that
    missed a late trade would match the lake and pass. So it must be one of the
    day's source snapshots, pass `resolve_snapshot` under its own tag, and no held
    source snapshot may be newer.
    """
    seq = _source_seq(src_ids, snapshots)
    if pinned not in set(src_ids):
        raise SystemExit(f"dbt_trades_snapshot {pinned} is not one of the gold.trades snapshots the day's lake candles were computed from: re-pin with --pin-current <day> --dbt")
    resolve_snapshot(lineage, tags, "lake.gold.trades", pinned, tag)
    newer = sorted(s for s in set(src_ids) if s in seq and seq[s] > seq.get(pinned, -1))
    if pinned not in seq or newer:
        raise SystemExit(f"dbt_trades_snapshot {pinned} is not the newest source snapshot: lake.gold.trades holds newer ones {newer}; re-pin with --pin-current <day> --dbt")
    return pinned


def pinned_sql(manifest: dict, unique_id: str, snapshots: dict) -> str:
    """A dbt model's compiled SQL, each model it reads inlined, each source read at a snapshot.

    `manifest` is dbt's target/manifest.json after `dbt compile`; `snapshots` maps a
    source's unique id to its snapshot id. A source without one is an error: the
    point is to read nothing at `latest`.
    """
    node = manifest["nodes"][unique_id]
    if "compiled_code" not in node:
        raise SystemExit(f"{unique_id} is not compiled: run dbt compile first")
    sql = node["compiled_code"]
    for dep in node["depends_on"]["nodes"]:
        if dep in manifest["sources"]:
            if dep not in snapshots:
                raise SystemExit(f"{unique_id} reads {dep}, which has no snapshot to read it at")
            name, replacement = manifest["sources"][dep]["relation_name"], f"{manifest['sources'][dep]['relation_name']} FOR VERSION AS OF {snapshots[dep]}"
        else:
            name, replacement = manifest["nodes"][dep]["relation_name"], f"({pinned_sql(manifest, dep, snapshots)})"
        if not name or name not in sql:  # an ephemeral model has no relation; a quoting change would hide one
            raise SystemExit(f"{dep} does not appear in the compiled SQL of {unique_id} as {name}")
        sql = sql.replace(name, replacement)
    return sql


def dbt_candles(day: str, trades_snapshot: int) -> list:
    """D: the dbt model's SQL through Trino, over gold.trades at `trades_snapshot`."""
    import trino  # imported here so the pure functions above import without it

    # The SQL as committed, not a manifest left over from another checkout. Reads only.
    done = subprocess.run(["uv", "run", "--locked", "-q", "dbt", "compile", "--select", "+ohlcv_1m"], cwd=DBT, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"dbt compile failed in {DBT}:\n{done.stdout}{done.stderr}")
    sql = pinned_sql(json.loads((DBT / "target" / "manifest.json").read_text()), DBT_MODEL, {DBT_TRADES: trades_snapshot})
    cur = trino.dbapi.connect(host=TRINO_HOST, port=TRINO_PORT, user="parity", timezone="UTC").cursor()
    cur.execute(
        f"""SELECT exchange, canonical_symbol, window_start, open_e8, high_e8, low_e8, close_e8, trade_count, volume,
                   quote_volume, typeof(quote_volume)
            FROM ({sql}) WHERE CAST(window_start AS DATE) = DATE '{day}' ORDER BY 1, 2, 3"""
    )
    return cur.fetchall()


def normalise(rows: list, exact: bool = False) -> dict:
    """{(exchange, symbol, window_start): (open, high, low, close, count, volume[, quote_volume])}.

    By default volume is a float rounded to 8 decimals, as sides A, B and C have always
    compared it. `exact` keeps volume and quote_volume, the tenth column, as Decimals.
    """
    out = {}
    for r in rows:
        exchange, symbol, ws = r[0], r[1], r[2]
        key = (exchange, symbol, ws.replace(tzinfo=None) if hasattr(ws, "replace") else ws)
        if exact:
            out[key] = (*(int(x) for x in r[3:8]), Decimal(r[8]), Decimal(r[9]))
        else:
            out[key] = tuple(int(x) if i < 5 else round(float(x), 8) for i, x in enumerate(r[3:9]))
    return out


def lake_vs_dbt(lake_rows: list, dbt_rows: list, lake_type: str) -> int:
    """B against D, exactly; quote_volume must be typed alike by both engines (ADR-035).

    `dbt_rows` carry Trino's typeof(quote_volume) as an eleventh column. Returns the
    number of failures: buckets that differ, plus one if the types do.
    """
    types = sorted({r[10] for r in dbt_rows})
    alike = types == [lake_type]
    print(f"quote_volume: lake {lake_type}, dbt {types}" + ("" if alike else ": FAIL, the engines type it differently"))
    return (0 if alike else 1) + diff("lake.gold.ohlcv_1m", normalise(lake_rows, exact=True), "dbt.ohlcv_1m", normalise(dbt_rows, exact=True))


def utc(text, fmt: str = MINUTE) -> datetime:
    """`text` in exactly `fmt`, read as UTC: a naive datetime, like the bucket keys. ValueError otherwise.

    Parsed, then printed back and compared, so only the one canonical spelling passes:
    no basic format, week date, missing field or unpadded number.
    """
    try:
        t = datetime.strptime(text, fmt)
    except (TypeError, ValueError):
        t = None
    if t is None or t.strftime(fmt) != text:
        raise ValueError(f"{text!r} is not written exactly like {datetime(2001, 1, 1).strftime(fmt)}")
    return t


def audit_scope(ref) -> tuple:
    """The [start, end) of the outage an `audit_ref` names, rounded outward to whole minutes."""
    m = AUDIT_REF.fullmatch(ref) if isinstance(ref, str) else None
    if not m:
        raise ValueError(
            f"audit_ref {ref!r} is not 'lake.audit.checks job=<job> check_name=<name> "
            f"scope=stack/<YYYY-MM-DDTHH:MM:SSZ>..<YYYY-MM-DDTHH:MM:SSZ>'"
        )
    lo, hi = utc(m[3], SECOND), utc(m[4], SECOND)
    if hi <= lo:
        raise ValueError(f"audit_ref scope {m[3]}..{m[4]} does not end after it starts")
    return lo.replace(second=0), hi if not hi.second else hi.replace(second=0) + timedelta(minutes=1)


def confirm_audit_row(c, ref: str) -> None:
    """Stop the run unless `lake.audit.checks` holds the failed operator row `ref` names, exactly.

    `load_gaps` checks the reference as text, which an invented row of the right
    shape passes; this reads the table, over the gate's own DuckDB connection to
    the lake, so it adds nothing the gate does not already need. Two failures, told
    apart: a query that raises (catalog or storage unreachable, table missing) is
    "could not read", and a query that answers with no row is "no row". Neither can
    pass, and the first is never read as an empty answer. Read at main's head, not
    at a pin: the row is recorded after the day it explains, so no pin of that day
    holds it.
    """
    job, check, lo, hi = AUDIT_REF.fullmatch(ref).groups()
    try:
        rows = c.execute(
            "SELECT job, passed FROM lake.audit.checks WHERE job = ? AND check_name = ? AND scope = ?", [job, check, f"stack/{lo}..{hi}"]
        ).fetchall()
    except Exception as e:  # duckdb raises its own types; any of them means the row was not read
        raise SystemExit(f"known gap: could not read lake.audit.checks to find {ref}: {e}. The gap is applied only once its row is read") from e
    if not rows:
        raise SystemExit(f"known gap: lake.audit.checks has no row job={job} check_name={check} scope=stack/{lo}..{hi}; a gap needs a recorded outage")
    if any(row_job != "operator" or passed is not False for row_job, passed in rows):  # NULL is not a failure
        raise SystemExit(f"known gap: the row {ref} names is not a failed operator row (job, passed: {sorted(set(rows))})")
    print(f"known gap: audit row found, {ref}")


def gap_problem(g, first: datetime) -> str | None:
    """Why one `known_gaps` entry is refused for the day starting at `first`, or None."""
    if not isinstance(g, dict):
        return "is not an object"
    if set(g) != GAP_FIELDS:
        return f"unknown keys {sorted(set(g) - GAP_FIELDS)}, missing keys {sorted(GAP_FIELDS - set(g))}"
    try:
        lo, hi = utc(g["start"]), utc(g["end"])
        utc(g["recorded"], DAY)
        if hi <= lo:
            return f"end {g['end']} is not after start {g['start']}"
        if hi - lo > MAX_GAP:
            return f"{g['start']} to {g['end']} is longer than {MAX_GAP // timedelta(minutes=1)} minutes"
        if lo < first or hi > first + timedelta(days=1):
            return f"{g['start']} to {g['end']} is not inside the day compared, {first:%Y-%m-%d}"
        if g["sides"] != GAP_SIDES:
            return (
                f"sides {g['sides']!r} must be {GAP_SIDES}: lake against DuckDB over silver and lake against dbt "
                f"check the lake's own computation, which a capture outage cannot make differ, so they are never excluded"
            )
        if not isinstance(g["cause"], str) or not g["cause"].strip():
            return "cause is not a non-empty string"
        scope_lo, scope_hi = audit_scope(g["audit_ref"])
    except ValueError as e:
        return str(e)
    if lo < scope_lo or hi > scope_hi:
        return f"{g['start']} to {g['end']} is not inside the audit row's scope, rounded out to whole minutes: {scope_lo:{MINUTE}} to {scope_hi:{MINUTE}}"
    return None


def load_gaps(gaps, day: str) -> list:
    """The pin's `known_gaps`, validated, each with `lo` and `hi` as naive UTC datetimes.

    A gap takes buckets out of a comparison, so every rule exits rather than warns:
    a wide, second, misdated or unrecorded exclusion must never pass quietly.
    """
    if not isinstance(gaps, list):
        raise SystemExit("known_gaps is not a list")
    if len(gaps) > MAX_GAPS:
        raise SystemExit(
            f"known_gaps has {len(gaps)} entries, at most {MAX_GAPS}: a second incident that needs an exclusion is the "
            f"trigger, in ADR-029's 2026-11-26 Outcome, to read loss windows from lake.audit.checks instead of listing them"
        )
    first = datetime.strptime(day, DAY)
    for i, g in enumerate(gaps):
        problem = gap_problem(g, first)
        if problem:
            raise SystemExit(f"known_gaps[{i}]: {problem}")
    return [{**g, "lo": utc(g["start"]), "hi": utc(g["end"])} for g in gaps]


def diff(name_a: str, a: dict, name_b: str, b: dict, gaps: list = (), limit: int = 5) -> int:
    """Buckets that differ, after `gaps` take their windows out; plus one per gap with no difference left in it."""
    keys = set(a) | set(b)
    dead = 0
    for g in gaps:
        inside = {k for k in keys if g["lo"] <= k[2] < g["hi"]}
        differing = sum(1 for k in inside if a.get(k) != b.get(k))
        keys -= inside
        line = f"known gap {g['start']} to {g['end']} ({g['cause']}; {g['audit_ref']}): {len(inside)} buckets removed, {differing} of them differing"
        if not differing:
            dead += 1
            why = "it matches no bucket, so it is stale or mistyped" if not inside else "its window now matches in full, so it excludes nothing"
            line += f": FAIL, {why}: remove it from tests/parity/pinned.json"
        print(f"  {name_a} vs {name_b} {line}")
    keys = sorted(keys)
    bad = [k for k in keys if a.get(k) != b.get(k)]
    for k in bad[:limit]:
        print(f"  {name_a} vs {name_b} {k}: {a.get(k)} != {b.get(k)}")
    print(f"{name_a} vs {name_b}: {len(keys)} buckets, {len(bad)} differ" + (f" (and {len(gaps)} known gap applied)" if gaps else ""))
    return len(bad) + dead


def verdict(bad: int, applied: int) -> str:
    """The last line. `PASS` only for a run that left nothing out."""
    if not applied:
        return "PARITY: " + ("PASS" if bad == 0 else "FAIL")
    return f"PARITY: {'ok' if bad == 0 else 'FAIL'} ({applied} known gap applied, see above)"


def not_empty(**sides) -> int:
    """0 when every side has rows; 1 with a message when one is empty.

    An empty side compares equal to another empty side, so without this a run
    against the wrong day or a stale snapshot id prints PASS and proves nothing.
    """
    empty = [name for name, rows in sides.items() if not rows]
    if empty:
        print(f"PARITY: FAIL - nothing to compare, {' and '.join(empty)} returned 0 rows (wrong --day, or a snapshot id from another table?)")
    return 1 if empty else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    ap.add_argument("--ohlcv-snapshot", type=int, required=True, help="lake.gold.ohlcv_1m snapshot id")
    ap.add_argument("--silver-snapshots", help='JSON: {"binance": id, "kraken": id, "coinbase": id}: the k2.src-snapshot-id.<venue> of the gold.trades snapshot; required unless --skip-silver')
    ap.add_argument("--skip-clickhouse", action="store_true", help="leave out A, ClickHouse")
    ap.add_argument("--skip-silver", action="store_true", help="leave out C, DuckDB over silver")
    ap.add_argument("--dbt", action="store_true", help="add D, dbt's ohlcv_1m SQL through Trino at the gold.trades snapshot B was computed from")
    ap.add_argument("--dbt-trades-snapshot", type=int, help="the pin's dbt_trades_snapshot, which the wrapper passes; --dbt needs it unless --write-pin records it")
    ap.add_argument("--write-pin", action="store_true", help="on success, record the ids in tests/parity/pinned.json")
    ap.add_argument("--known-gaps", default="[]", help="JSON: the pin's known_gaps, which the wrapper passes (tests/parity/README.md)")
    args = ap.parse_args()
    if not args.skip_silver and not args.silver_snapshots:
        ap.error("--silver-snapshots is required unless --skip-silver")
    if args.write_pin and args.skip_silver:
        ap.error("--write-pin records the silver ids, so it needs side C: drop --skip-silver")
    if args.skip_silver and args.skip_clickhouse and not args.dbt:
        ap.error("nothing to compare lake.gold.ohlcv_1m with: keep side A or C, or add --dbt")
    gaps = load_gaps(json.loads(args.known_gaps), args.day)
    applied = 0 if args.skip_clickhouse else len(gaps)
    for g in gaps if args.skip_clickhouse else ():
        print(f"known gap {g['start']} to {g['end']} not applied: clickhouse not compared in this run")

    c = duck(os.environ["MINIO_ROOT_USER"], os.environ["MINIO_ROOT_PASSWORD"])
    for g in gaps:  # before anything is compared, whether or not this run applies it
        confirm_audit_row(c, g["audit_ref"])

    def q(sql):
        return c.execute(sql).fetchall()

    ohlcv_snapshot = resolve_snapshot(*table_refs("lake.gold.ohlcv_1m"), "lake.gold.ohlcv_1m", args.ohlcv_snapshot, pin_tag(args.day))
    lake_rows = lake_candles(c, args.day, ohlcv_snapshot)
    b = normalise(lake_rows)
    if not_empty(**{"lake.gold.ohlcv_1m": b}):
        return 1
    bad = 0
    if not args.skip_silver:
        silver_snaps = {
            venue: resolve_snapshot(*table_refs(f"lake.silver.trades_{venue}"), f"lake.silver.trades_{venue}", snap, pin_tag(args.day))
            for venue, snap in json.loads(args.silver_snapshots).items()
        }
        cc = normalise(silver_candles(c, args.day, silver_snaps))
        if not_empty(**{"duckdb-over-silver": cc}):
            return 1
        bad += diff("lake.gold.ohlcv_1m", b, "duckdb-over-silver", cc)
    if args.dbt:
        src = [r[0] for r in q(f"""SELECT DISTINCT src_snapshot_id FROM lake.gold.ohlcv_1m AT (VERSION => {ohlcv_snapshot})
                                   WHERE CAST(window_start AS DATE) = DATE '{args.day}'""")]
        snaps = q("SELECT sequence_number, snapshot_id FROM iceberg_snapshots('lake.gold.trades')")
        pinned = args.dbt_trades_snapshot
        if pinned is None:
            if not args.write_pin:
                raise SystemExit("--dbt: the pin has no dbt_trades_snapshot, so D has no snapshot to read: re-pin with scripts/parity-ohlcv.sh --pin-current <day> --dbt")
            pinned = newest_source_snapshot(src, snaps)
        trades_snapshot = confirm_dbt_snapshot(pinned, src, snaps, *table_refs("lake.gold.trades"), pin_tag(args.day, "_dbt"))
        print(f"dbt side: lake.gold.trades FOR VERSION AS OF {trades_snapshot}, the pin's dbt_trades_snapshot: the newest of the snapshots the day's lake candles name, as recorded when pinned")
        d_rows = dbt_candles(args.day, trades_snapshot)
        if not_empty(**{"dbt.ohlcv_1m": d_rows}):
            return 1
        # Iceberg's type, for B, at B's snapshot: the head's could differ after a schema change.
        lake_type = q(f"SELECT typeof(quote_volume) FROM lake.gold.ohlcv_1m AT (VERSION => {ohlcv_snapshot}) LIMIT 1")[0][0].lower()
        bad += lake_vs_dbt(lake_rows, d_rows, lake_type)
    if not args.skip_clickhouse:
        a = normalise(clickhouse_candles(args.day, os.environ["CLICKHOUSE_PASSWORD"]))
        if not_empty(**{"clickhouse.ohlcv_live": a}):
            return 1
        bad += diff("clickhouse.ohlcv_live", a, "lake.gold.ohlcv_1m", b, gaps)
    if bad == 0 and args.write_pin:
        # Read-modify-write: scripts/parity_bars.py keeps its ids in the `bars`
        # block of the same file, and a plain overwrite here deleted them.
        pin = Path("tests/parity/pinned.json")
        doc = json.loads(pin.read_text()) if pin.exists() else {}
        doc.update({"day": args.day, "ohlcv_1m_snapshot": ohlcv_snapshot, "silver_snapshots": silver_snaps})
        if args.dbt:
            doc["dbt_trades_snapshot"] = trades_snapshot
        else:
            doc.pop("dbt_trades_snapshot", None)  # another day's, which a --dbt run would refuse
        pin.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"pinned in {pin}: tag these snapshots now (tests/parity/README.md); an untagged pin fails the gate once expiry removes it")
    print(verdict(bad, applied))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
