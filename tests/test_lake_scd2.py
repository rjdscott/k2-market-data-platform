"""
The SCD2 diff (docker/lake/scd2.py) and its contract with ddl/lake.sql.

Pure — no Spark, no catalog, no stack. `plan()` is the whole of the type-2
decision (ADR-030): what closes, what opens, and — the property the five-minute
ingest depends on — what happens when nothing changed, which must be nothing.

The last test is the other half of the contract. `gold.py` appends the new
versions with `writeTo(...).append()`, which resolves POSITIONALLY, so the
column tuple in `gold.py` and the column order in `lake.sql` are a contract.
Swapping two columns of the same type there would write `base` into `quote` for
every instrument without raising anything, which is the schema-change failure
mode CLAUDE.md warns about: silent, and hours downstream.
"""

import ast
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import scd2

_ROOT = Path(__file__).resolve().parent.parent
_DDL = _ROOT / "docker" / "lake" / "ddl" / "lake.sql"
_GOLD = _ROOT / "docker" / "lake" / "gold.py"

TRACKED = ("symbol", "base", "quote", "book_depth", "subscribed", "tick_size", "source")
T0 = datetime(2026, 8, 29, 10, 0, 0)
T1 = T0 + timedelta(hours=1)


def instrument(exchange="kraken", canonical="BTC/USD", symbol="BTC/USD", **over):
    row = {
        "instrument_id": scd2.surrogate(exchange, canonical),
        "exchange": exchange,
        "canonical_symbol": canonical,
        "symbol": symbol,
        "base": canonical.split("/")[0],
        "quote": canonical.split("/")[1],
        "book_depth": 25,
        "subscribed": True,
        "tick_size": None,
        "source": "registry",
    }
    row.update(over)
    return row


def stored(row, valid_from=T0):
    """`row` as `plan()` would find it in the table: an open version."""
    return {
        **row,
        "attr_hash": scd2.attr_hash(row, TRACKED),
        "valid_from": valid_from,
        "valid_to": scd2.FOREVER,
        "is_current": True,
        "recorded_at": valid_from,
    }


KEY = ("kraken", "BTC/USD")


class TestPlan:
    def test_nothing_changed_writes_nothing(self):
        row = instrument()
        close, insert = scd2.plan({KEY: stored(row)}, {KEY: row}, TRACKED, T1)
        assert (close, insert) == ([], [])

    def test_a_new_instrument_is_inserted_and_nothing_closes(self):
        close, insert = scd2.plan({}, {KEY: instrument()}, TRACKED, T1)
        assert close == []
        assert len(insert) == 1
        assert insert[0]["valid_from"] == scd2.EPOCH
        assert insert[0]["valid_to"] == scd2.FOREVER
        assert insert[0]["is_current"] is True
        assert insert[0]["recorded_at"] == T1

    def test_a_first_version_opens_in_the_past_and_a_changed_one_opens_at_run_ts(self):
        # The registry asserts its attributes for all history: the first version of
        # a key must cover trades older than the first run, or an ASOF join drops
        # them silently. A CHANGED attribute is genuinely new as of run_ts.
        first = scd2.plan({}, {KEY: instrument()}, TRACKED, T1)[1][0]
        assert first["valid_from"] == scd2.EPOCH == datetime(1970, 1, 1)
        assert first["recorded_at"] == T1           # when K2 learned it, not when it became true
        assert first["valid_from"] < first["recorded_at"]

        changed = scd2.plan(
            {KEY: stored(instrument(), valid_from=scd2.EPOCH)},
            {KEY: instrument(book_depth=100)},
            TRACKED,
            T1,
        )[1][0]
        assert changed["valid_from"] == T1
        assert changed["recorded_at"] == T1

    def test_a_trade_older_than_the_first_run_still_joins(self):
        # The bug this rule fixes: gold.trades starts before the dimension does.
        insert = scd2.plan({}, {KEY: instrument()}, TRACKED, T1)[1][0]
        trade_ts = T0 - timedelta(days=365)
        assert insert["valid_from"] <= trade_ts < insert["valid_to"]

    def test_a_changed_attribute_closes_the_old_version_and_opens_a_new_one(self):
        before = instrument()
        after = instrument(tick_size=Decimal("0.1000000000"), source="venue:kraken")
        close, insert = scd2.plan({KEY: stored(before)}, {KEY: after}, TRACKED, T1)
        assert close == [KEY]
        assert len(insert) == 1
        assert insert[0]["tick_size"] == Decimal("0.1000000000")
        # The version boundary is a single instant: the old row's valid_to is set
        # to run_ts by the MERGE, and the new row's valid_from is the same run_ts,
        # so [valid_from, valid_to) tiles the timeline with no gap and no overlap.
        assert insert[0]["valid_from"] == T1

    def test_a_venue_rename_keeps_the_instrument_id(self):
        # The reason the natural key is (exchange, canonical_symbol) and the
        # native symbol is an attribute: Kraken WS v1 XBT/USD -> v2 BTC/USD.
        before = instrument(symbol="XBT/USD")
        after = instrument(symbol="BTC/USD")
        close, insert = scd2.plan({KEY: stored(before)}, {KEY: after}, TRACKED, T1)
        assert close == [KEY]
        assert insert[0]["instrument_id"] == before["instrument_id"]
        assert insert[0]["symbol"] == "BTC/USD"

    def test_an_instrument_that_leaves_the_registry_is_closed_not_deleted(self):
        before = instrument(tick_size=Decimal("0.1000000000"))
        close, insert = scd2.plan({KEY: stored(before)}, {}, TRACKED, T1)
        assert close == [KEY]
        assert len(insert) == 1
        assert insert[0]["subscribed"] is False
        assert insert[0]["instrument_id"] == before["instrument_id"]
        # Last known attributes carried forward, not nulled.
        assert insert[0]["tick_size"] == Decimal("0.1000000000")
        assert insert[0]["is_current"] is True

    def test_an_already_delisted_instrument_stays_quiet(self):
        # The delisting must produce exactly ONE extra row however many runs
        # follow it, or the five-minute ingest would append a version forever.
        gone = stored(instrument(subscribed=False), valid_from=T1)
        close, insert = scd2.plan({KEY: gone}, {}, TRACKED, T1 + timedelta(hours=1))
        assert (close, insert) == ([], [])

    def test_a_delisted_instrument_that_comes_back_reopens(self):
        gone = stored(instrument(subscribed=False), valid_from=T0)
        close, insert = scd2.plan({KEY: gone}, {KEY: instrument()}, TRACKED, T1)
        assert close == [KEY]
        assert insert[0]["subscribed"] is True

    def test_plan_is_idempotent_when_its_own_output_is_applied(self):
        # Apply the plan (close + insert), feed the resulting open slice back in,
        # and a second run must decide to do nothing. This is the live check the
        # verification run makes against the stack, without the stack.
        row = instrument()
        close, insert = scd2.plan({}, {KEY: row}, TRACKED, T0)
        assert len(insert) == 1
        applied = {KEY: insert[0]}
        assert scd2.plan(applied, {KEY: row}, TRACKED, T1) == ([], [])

    def test_several_instruments_are_decided_independently(self):
        eth = ("kraken", "ETH/USD")
        sol = ("kraken", "SOL/USD")
        previous = {
            KEY: stored(instrument()),
            eth: stored(instrument(canonical="ETH/USD", symbol="ETH/USD")),
        }
        current = {
            KEY: instrument(),                                              # unchanged
            eth: instrument(canonical="ETH/USD", symbol="ETH/USD", book_depth=100),  # changed
            sol: instrument(canonical="SOL/USD", symbol="SOL/USD"),         # new
        }
        close, insert = scd2.plan(previous, current, TRACKED, T1)
        assert close == [eth]
        assert {r["canonical_symbol"] for r in insert} == {"ETH/USD", "SOL/USD"}


class TestHashing:
    def test_the_surrogate_is_stable_and_distinct_per_venue(self):
        assert scd2.surrogate("kraken", "BTC/USD") == scd2.surrogate("kraken", "BTC/USD")
        assert scd2.surrogate("kraken", "BTC/USD") != scd2.surrogate("coinbase", "BTC/USD")
        assert len(scd2.surrogate("kraken", "BTC/USD")) == 32

    def test_a_missing_value_and_the_string_none_hash_differently(self):
        # `str(None)` is "None". Serialising NULL as 0x00 is what keeps an
        # unpublished tick_size distinct from a venue that literally sent "None".
        assert scd2.attr_hash({"a": None}, ("a",)) != scd2.attr_hash({"a": "None"}, ("a",))

    def test_the_separator_prevents_a_field_boundary_collision(self):
        # Without a separator, ("AB", "C") and ("A", "BC") hash the same.
        assert scd2.attr_hash({"a": "AB", "b": "C"}, ("a", "b")) != scd2.attr_hash(
            {"a": "A", "b": "BC"}, ("a", "b")
        )

    def test_an_untracked_field_does_not_open_a_version(self):
        row = instrument()
        assert scd2.attr_hash(row, TRACKED) == scd2.attr_hash({**row, "loaded_at": T1}, TRACKED)

    def test_the_open_row_sentinel_is_a_timestamp_not_null(self):
        # `ts < NULL` is not TRUE, so a NULL upper bound drops the current row
        # from every range join. The sentinel keeps the predicate total.
        assert scd2.FOREVER == datetime(9999, 12, 31, 23, 59, 59)
        assert T1 < scd2.FOREVER


def _ddl_columns(table):
    """Column names of one CREATE TABLE block in lake.sql, in declaration order."""
    body = re.search(
        rf"CREATE TABLE IF NOT EXISTS lake\.{re.escape(table)} \((.*?)\n\)\nUSING iceberg",
        _DDL.read_text(),
        re.DOTALL,
    )
    assert body, f"no CREATE TABLE block for lake.{table}"
    return [m.group(1) for m in re.finditer(r"^ {4}(\w+)\s+[A-Z]", body.group(1), re.M)]


def _gold_tuple(name):
    """A module-level tuple literal from gold.py, without importing pyspark."""
    tree = ast.parse(_GOLD.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == name:
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in {_GOLD}")


class TestColumnOrderMatchesTheDDL:
    def test_dim_instrument(self):
        assert _gold_tuple("DIM_INSTRUMENT_COLUMNS") == tuple(_ddl_columns("gold.dim_instrument"))

    def test_dim_venue(self):
        assert _gold_tuple("DIM_VENUE_COLUMNS") == tuple(_ddl_columns("gold.dim_venue"))

    def test_every_tracked_attribute_is_a_real_column(self):
        for name, table in (("DIM_INSTRUMENT_TRACKED", "gold.dim_instrument"),
                            ("DIM_VENUE_TRACKED", "gold.dim_venue")):
            assert set(_gold_tuple(name)) <= set(_ddl_columns(table)), name

    def test_the_bookkeeping_columns_are_never_tracked(self):
        # Hashing valid_from would make every row a change, forever.
        for name in ("DIM_INSTRUMENT_TRACKED", "DIM_VENUE_TRACKED"):
            assert not set(_gold_tuple(name)) & set(scd2.BOOKKEEPING), name
