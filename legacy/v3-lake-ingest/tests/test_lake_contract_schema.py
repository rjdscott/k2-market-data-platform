"""
contract_schema.py: the nightly comparison of each live Iceberg table with its contract.

The live side is Iceberg's own JSON, spelled as Lakekeeper serves it and as
SchemaParser/PartitionSpecParser/SortOrderParser write it in the Spark job (a decimal
is "decimal(28, 10)", a zoned timestamp "timestamptz", a list carries element-required).
The contract side is an entry of docker/lake/generated/contract_schemas.json.
"""

import copy
import re

import contract_schema as C
import pytest

import offsets as O

CONTRACT = {
    "columns": [
        {"name": "exchange", "type": "STRING", "required": True},
        {"name": "price", "type": "DECIMAL(28,10)", "required": True},
        {"name": "exchange_ts", "type": "TIMESTAMP", "required": True},
        {"name": "bids", "type": "ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>>", "required": True},
        {"name": "headers", "type": "MAP<STRING, BINARY>", "required": False},
        {"name": "seq_gap", "type": "BOOLEAN", "required": False},
    ],
    "partitioning": ["exchange", "days(exchange_ts)"],
    "sort": ["exchange", "exchange_ts"],
    "identifier": ["exchange", "exchange_ts"],
}

_PAIR = {
    "type": "struct",
    "fields": [
        {"id": 8, "name": "px", "required": False, "type": "decimal(28, 10)"},
        {"id": 9, "name": "qty", "required": False, "type": "decimal(28, 10)"},
    ],
}
LIVE = {
    "schema": {
        "type": "struct",
        "schema-id": 0,
        "identifier-field-ids": [3, 1],
        "fields": [
            {"id": 1, "name": "exchange", "required": True, "type": "string"},
            {"id": 2, "name": "price", "required": True, "type": "decimal(28, 10)"},
            {"id": 3, "name": "exchange_ts", "required": True, "type": "timestamptz"},
            {"id": 4, "name": "bids", "required": True, "type": {"type": "list", "element-id": 7, "element-required": False, "element": _PAIR}},
            {"id": 5, "name": "headers", "required": False, "type": {"type": "map", "key-id": 10, "key": "string", "value-id": 11, "value-required": False, "value": "binary"}},
            {"id": 6, "name": "seq_gap", "required": False, "type": "boolean"},
        ],
    },
    "partition-spec": {
        "spec-id": 0,
        "fields": [
            {"source-id": 1, "field-id": 1000, "name": "exchange", "transform": "identity"},
            {"source-id": 3, "field-id": 1001, "name": "exchange_ts_day", "transform": "day"},
        ],
    },
    "sort-order": {
        "order-id": 1,
        "fields": [
            {"source-id": 1, "transform": "identity", "direction": "asc", "null-order": "nulls-first"},
            {"source-id": 3, "transform": "identity", "direction": "asc", "null-order": "nulls-first"},
        ],
    },
}


def live(edit=None):
    out = copy.deepcopy(LIVE)
    if edit:
        edit(out)
    return out


def field(table, name):
    return next(f for f in table["schema"]["fields"] if f["name"] == name)


def test_an_identical_table_passes():
    assert C.differences(CONTRACT, live()) == []


def test_whitespace_in_the_contracts_spelling_is_not_a_difference():
    contract = copy.deepcopy(CONTRACT)
    contract["columns"][1]["type"] = "DECIMAL(28, 10)"
    contract["columns"][3]["type"] = "ARRAY<STRUCT<px:DECIMAL(28,10),qty:DECIMAL(28,10)>>"
    assert C.differences(contract, live()) == []


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda t: t["schema"]["fields"].pop(5), ["missing column seq_gap"]),
        (
            lambda t: t["schema"]["fields"].insert(2, {"id": 12, "name": "venue", "required": False, "type": "long"}),
            ["extra column venue BIGINT"],
        ),
        (lambda t: field(t, "seq_gap").update(type="int"), ["type differs on seq_gap: contract BOOLEAN, live INT"]),
        (lambda t: field(t, "seq_gap").update(required=True), ["nullability differs on seq_gap: contract nullable, live NOT NULL"]),
        (lambda t: field(t, "exchange").update(required=False), ["nullability differs on exchange: contract NOT NULL, live nullable"]),
        (
            lambda t: t["partition-spec"]["fields"][1].update(transform="month"),
            ["partitioning differs: contract [exchange, days(exchange_ts)], live [exchange, months(exchange_ts)]"],
        ),
        (
            lambda t: t["partition-spec"]["fields"].pop(),
            ["partitioning differs: contract [exchange, days(exchange_ts)], live [exchange]"],
        ),
        (lambda t: t["sort-order"].update(fields=[]), ["sort differs: contract [exchange, exchange_ts], live []"]),
        (
            lambda t: t["sort-order"]["fields"][1].update(direction="desc", **{"null-order": "nulls-last"}),
            ["sort differs: contract [exchange, exchange_ts], live [exchange, exchange_ts DESC]"],
        ),
        (
            lambda t: t["schema"].update({"identifier-field-ids": [1]}),
            ["identifier fields differ: contract [exchange, exchange_ts], live [exchange]"],
        ),
    ],
    ids=["missing", "extra", "type", "nullable-to-required", "required-to-nullable", "partition-transform",
         "partition-dropped", "sort-dropped", "sort-direction", "identifier"],
)
def test_each_kind_of_difference_is_found_and_worded(edit, expected):
    assert C.differences(CONTRACT, live(edit)) == expected


def test_a_swap_reports_both_columns_and_a_missing_column_moves_nothing():
    swapped = live(lambda t: t["schema"]["fields"].insert(0, t["schema"]["fields"].pop(1)))
    assert C.differences(CONTRACT, swapped) == [
        "position differs on exchange: column 1 in the contract, column 2 live",
        "position differs on price: column 2 in the contract, column 1 live",
    ]
    # The column after a dropped one sits one place earlier and is still in order: one finding, not two.
    assert C.differences(CONTRACT, live(lambda t: t["schema"]["fields"].pop(4))) == ["missing column headers"]


@pytest.mark.parametrize(
    ("live_type", "spelled"),
    [("decimal(38, 10)", "DECIMAL(38,10)"), ("decimal(28, 8)", "DECIMAL(28,8)"), ("double", "DOUBLE")],
    ids=["precision", "scale", "not-a-decimal"],
)
def test_a_decimals_precision_and_scale_are_both_compared(live_type, spelled):
    assert C.differences(CONTRACT, live(lambda t: field(t, "price").update(type=live_type))) == [
        f"type differs on price: contract DECIMAL(28,10), live {spelled}"
    ]


def test_a_timestamp_without_its_zone_is_a_different_type():
    """Spark's TIMESTAMP is Iceberg's timestamptz; a table written as TIMESTAMP_NTZ reads back with no zone."""
    assert C.differences(CONTRACT, live(lambda t: field(t, "exchange_ts").update(type="timestamp"))) == [
        "type differs on exchange_ts: contract TIMESTAMP, live TIMESTAMP_NTZ"
    ]


@pytest.mark.parametrize(
    ("edit", "spelled"),
    [
        (lambda t: field(t, "bids")["type"]["element"]["fields"][1].update(type="double"), "ARRAY<STRUCT<px: DECIMAL(28,10), qty: DOUBLE>>"),
        (lambda t: field(t, "bids")["type"]["element"]["fields"][0].update(required=True), "ARRAY<STRUCT<px: DECIMAL(28,10) NOT NULL, qty: DECIMAL(28,10)>>"),
        (lambda t: field(t, "bids")["type"]["element"]["fields"].reverse(), "ARRAY<STRUCT<qty: DECIMAL(28,10), px: DECIMAL(28,10)>>"),
        (lambda t: field(t, "bids")["type"].update({"element-required": True}), "ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)> NOT NULL>"),
    ],
    ids=["nested-type", "nested-required", "nested-order", "element-required"],
)
def test_a_nested_field_is_compared_as_part_of_its_columns_type(edit, spelled):
    assert C.differences(CONTRACT, live(edit)) == [
        f"type differs on bids: contract ARRAY<STRUCT<px: DECIMAL(28,10), qty: DECIMAL(28,10)>>, live {spelled}"
    ]


def test_a_map_value_and_a_type_spark_cannot_declare_differ():
    assert C.differences(CONTRACT, live(lambda t: field(t, "headers")["type"].update(value="string"))) == [
        "type differs on headers: contract MAP<STRING, BINARY>, live MAP<STRING, STRING>"
    ]
    assert C.differences(CONTRACT, live(lambda t: field(t, "exchange").update(type="uuid"))) == [
        "type differs on exchange: contract STRING, live uuid"
    ]


CONTRACTS = {"gold.trades": CONTRACT, "gold.bars": CONTRACT, "raw.messages": CONTRACT}


def test_one_row_per_contracted_table_and_one_per_uncontracted_table_outside_scratch():
    listed = {"gold.trades", "raw.messages", "gold.handmade", "spike.tmp", "scratch_p4.wap_bronze", "scratch_p4.wap_silver"}
    rows = C.check(CONTRACTS, listed, lambda t: live())
    assert rows == [
        ("gold.bars", False, 1, "table missing: the contract names a table the catalog does not hold"),
        ("gold.trades", True, 0, "matches its contract: 6 columns, partitioning, sort order and identifier fields"),
        ("raw.messages", True, 0, "matches its contract: 6 columns, partitioning, sort order and identifier fields"),
        ("gold.handmade", False, 1, "no contract: gold.handmade is in namespace gold, which the contracts own, and no contract describes it"),
        ("spike.tmp", False, 1, "no contract: spike.tmp is in namespace spike, which no contract names and which is not a scratch_ namespace, and no contract describes it"),
    ]


def test_a_scratch_namespace_no_contract_names_is_not_the_lakes():
    rows = C.check(CONTRACTS, {*CONTRACTS, "scratch_p4.wap_bronze"}, lambda t: live())
    assert [r[0] for r in rows] == sorted(CONTRACTS) and all(r[1] for r in rows)
    assert C.owned(CONTRACTS) == {"gold", "raw"}
    assert C.audited({"gold", "raw", "spike", "scratch_p4"}) == ["gold", "raw", "spike"]


def test_a_failing_tables_detail_is_every_difference_and_observed_counts_them():
    def drift(t):
        t["schema"]["fields"].pop(5)
        t["sort-order"]["fields"] = []

    (row,) = C.check({"gold.trades": CONTRACT}, {"gold.trades"}, lambda t: live(drift))
    assert row == ("gold.trades", False, 2, "missing column seq_gap; sort differs: contract [exchange, exchange_ts], live []")


def test_a_table_that_raises_is_a_check_raised_row_and_the_rest_still_run():
    def load(table):
        if table == "gold.bars":
            raise RuntimeError("catalog said 503")
        return live()

    rows = C.check(CONTRACTS, set(CONTRACTS), load)
    assert rows[0] == ("gold.bars", False, -1, f"{O.CHECK_RAISED} RuntimeError: catalog said 503")
    assert [r[1] for r in rows[1:]] == [True, True]


def test_the_committed_contracts_load_and_each_names_its_columns():
    contracts = C.load()
    assert "gold.trades" in contracts and "audit.checks" in contracts
    assert all(c["columns"] and {"partitioning", "sort", "identifier"} <= c.keys() for c in contracts.values())


# What a contract may spell, for the comparison to render the same: the Spark SQL words
# contract_schema._SPARK maps Iceberg's primitives to, plus the type constructors; every
# DECIMAL with its precision and scale; a partition or sort term as Spark DDL writes it.
# A `lake.sql` type like INTEGER, VARCHAR(10) or lower-case `string`, or a transform like
# day(ts) or truncate(col, 4), passes k2gen and the wire-format test and then fails every
# night, so it fails here instead.
_WORD = re.compile(r"\b[A-Za-z_]\w*\b(?!\s*:)")  # not a struct field's name, which a colon follows
_SPELLED = set(C._SPARK.values()) | {"DECIMAL", "ARRAY", "STRUCT", "MAP", "NOT", "NULL"}
_TIME = sorted(v.split("(")[0] for v in C._TRANSFORMS.values() if "(" in v)  # years, months, days, hours
_TERM = re.compile(rf"(?:(?P<plain>\w+)|(?:{'|'.join(_TIME)})\((?P<timed>\w+)\)|(?:bucket|truncate)\(\d+, ?(?P<sized>\w+)\))")
_ORDER = re.compile(r"(?: DESC| ASC NULLS LAST| DESC NULLS FIRST)?")


def unrenderable(contracts):
    bad = []
    for table, c in contracts.items():
        names = {col["name"] for col in c["columns"]}
        for col in c["columns"]:
            bad += [f"{table}.{col['name']}: {w}" for w in _WORD.findall(col["type"]) if w not in _SPELLED]
            bad += [f"{table}.{col['name']}: DECIMAL without (precision,scale)" for _ in re.finditer(r"DECIMAL(?!\(\d+, ?\d+\))", col["type"])]
        for key, order in (("partitioning", ""), ("sort", _ORDER.pattern)):
            for term in c[key]:
                m = re.fullmatch(_TERM.pattern + order, term)
                if not m or (m["plain"] or m["timed"] or m["sized"]) not in names:
                    bad.append(f"{table} {key}: {term}")
    return bad


def test_every_committed_contract_uses_spellings_the_comparison_renders():
    contracts = C.load()
    assert len(contracts) >= 26 and unrenderable(contracts) == []


def test_a_spelling_the_comparison_cannot_render_is_caught():
    contract = {
        "columns": [
            {"name": "a", "type": "INTEGER", "required": True},
            {"name": "b", "type": "VARCHAR(10)", "required": False},
            {"name": "c", "type": "string", "required": False},
            {"name": "d", "type": "ARRAY<STRUCT<timestamp: STRING, px: DECIMAL>>", "required": False},
            {"name": "ts", "type": "TIMESTAMP", "required": True},
        ],
        "partitioning": ["day(ts)", "truncate(c, 4)", "days(ts)", "bucket(16, a)"],
        "sort": ["ts DESC", "c DESCENDING", "nope"],
        "identifier": [],
    }
    assert unrenderable({"x.y": contract}) == [
        "x.y.a: INTEGER",
        "x.y.b: VARCHAR",
        "x.y.c: string",
        "x.y.d: DECIMAL without (precision,scale)",
        "x.y partitioning: day(ts)",
        "x.y partitioning: truncate(c, 4)",
        "x.y sort: c DESCENDING",
        "x.y sort: nope",
    ]
