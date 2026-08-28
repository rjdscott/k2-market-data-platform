#!/usr/bin/env python3
"""
Type-2 slowly-changing-dimension bookkeeping for the gold dimensions (ADR-030).

Pure — no Spark, no yaml, no clock, no I/O. Given the versions currently open in
the table and the rows the registry says are true now, `plan()` returns the keys
to close and the versions to insert. `gold.py` does the Iceberg half (a
copy-on-write MERGE to close, an append to insert, in that order);
`tests/test_lake_scd2.py` runs this one without a stack.

The four rules, and the reasoning is in
docs/research/2026-08-29-scd2-security-master.md:

  unchanged   attr_hash matches -> nothing. Running twice adds no rows, which is
              the property the ingest depends on and the test asserts.
  changed     close the open version at run_ts, insert a new one from run_ts.
  new         insert only.
  gone        an instrument that leaves config/instruments.yaml is CLOSED and
              reopened with subscribed = false, carrying its last known
              attributes forward. Never deleted: deleting makes every historical
              fact unjoinable and destroys the record that it existed. Once that
              version is open, a still-absent instrument is unchanged, so a
              delisting produces exactly one extra row however many runs follow.

The natural key is the caller's business — this module never looks inside it.
For `gold.dim_instrument` it is `(exchange, canonical_symbol)`, and the native
`symbol` is a tracked attribute rather than part of the key, so a venue rename
(Kraken `XBT/USD` -> `BTC/USD`) opens a version instead of inventing a second
instrument.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

# The open row's `valid_to`. A sentinel and not NULL, because `ts < NULL` is not
# TRUE: with NULL, the textbook `ts >= valid_from AND ts < valid_to` silently
# drops the CURRENT version — the newest rows, the ones most queries want — and
# the failure is a smaller result set rather than an error. DuckDB's ASOF JOIN
# happens to survive a NULL (it matches on valid_from alone), which makes it
# worse: the ASOF spelling works and the hand-written range join beside it does
# not. The sentinel makes both spellings total.
FOREVER = datetime(9999, 12, 31, 23, 59, 59)

# SCD2 bookkeeping, as opposed to the attributes a version is about. Never
# hashed — a hash over valid_from would make every row a change — and stripped
# when a previous version's attributes are carried into a new one.
BOOKKEEPING = ("attr_hash", "valid_from", "valid_to", "is_current", "recorded_at")

# 0x1F (unit separator) and 0x00 cannot occur in a symbol, a venue name or a
# decimal, so no pair of distinct attribute tuples can serialise to one string.
_SEP = "\x1f"
_NULL = "\x00"


def surrogate(*parts: str) -> str:
    """Deterministic 128-bit surrogate key over the natural key, as hex.

    Deterministic rather than a sequence because `rebuild.py --layer gold` drops
    and recreates gold: a sequence-generated id would be renumbered on every
    rebuild, invalidating every notebook and saved result that quoted one, and
    keeping it stable would need a durable generator outside Iceberg — the
    PostgreSQL watermark pattern ADR-022 deleted.
    """
    return hashlib.sha256(_SEP.join(parts).encode()).hexdigest()[:32]


def attr_hash(row: dict, tracked: tuple[str, ...]) -> str:
    """sha256 over `tracked`, canonically serialised: fixed order, NULL distinct.

    One comparison decides whether a version opens, however wide the row gets —
    the alternative is a column-by-column predicate that is silently wrong the
    day someone adds a column and forgets to extend it.

    `None` serialises to 0x00 rather than to "None", so a missing value and the
    literal string differ. Decimals serialise through `str`, which is exact;
    a float round-trip would make the hash unstable in the eighth place, the
    same reason gold.bars refuses decimal division.
    """
    payload = _SEP.join(_NULL if row.get(f) is None else str(row[f]) for f in tracked)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def plan(
    previous: dict[tuple, dict],
    current: dict[tuple, dict],
    tracked: tuple[str, ...],
    run_ts: datetime,
) -> tuple[list[tuple], list[dict]]:
    """The SCD2 diff.

    `previous`  {natural key: the open version, as stored} — the `is_current`
                slice of the table.
    `current`   {natural key: attributes that are true now}, no bookkeeping
                columns; the caller has already resolved the surrogate.
    `tracked`   attribute names, in the order they are hashed.

    Returns `(close, insert)`: the keys whose open version must be closed at
    `run_ts`, and the full rows to append. Both empty means nothing changed.
    """
    close: list[tuple] = []
    insert: list[dict] = []

    def version(row: dict) -> dict:
        return {
            **row,
            "attr_hash": attr_hash(row, tracked),
            "valid_from": run_ts,
            "valid_to": FOREVER,
            "is_current": True,
            # Equal to valid_from for every registry-sourced change, because the
            # run timestamp is both when it became true and when K2 learned it.
            # The day a source publishes an effective date of its own, the two
            # separate and `valid_from < recorded_at` is the evidence.
            "recorded_at": run_ts,
        }

    for key, row in current.items():
        open_version = previous.get(key)
        if open_version is not None and open_version["attr_hash"] == attr_hash(row, tracked):
            continue
        if open_version is not None:
            close.append(key)
        insert.append(version(row))

    for key, open_version in previous.items():
        if key in current or not open_version["subscribed"]:
            continue
        # Last known attributes, carried forward with subscribed flipped. The
        # venue attributes go stale from here, which is why `recorded_at` is on
        # the row: it dates the last time anything confirmed them.
        carried = {k: v for k, v in open_version.items() if k not in BOOKKEEPING}
        carried["subscribed"] = False
        close.append(key)
        insert.append(version(carried))

    return close, insert
