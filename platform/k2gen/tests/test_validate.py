"""k2gen validate, against the real contracts/ and lake.sql and against copies broken one way each.

Each failure test breaks one thing in a copy of the tree and asserts the line
that names it, so a check that stops firing fails here rather than in review.
"""

import shutil

import pytest
import yaml

from k2gen import cli, ddl

ROOT = cli.ROOT
TRADES = "contracts/market-analytics/canonical-trades/gold.trades.odcs.yaml"
BBO = "contracts/market-analytics/book-snapshots/gold.bbo_1s.odcs.yaml"
CANDLES = "contracts/market-analytics/candles/product.yaml"


@pytest.fixture
def tree(tmp_path):
    """A copy of contracts/ and lake.sql that a test can break."""
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    (tmp_path / cli.LAKE_SQL).parent.mkdir(parents=True)
    shutil.copy(ROOT / cli.LAKE_SQL, tmp_path / cli.LAKE_SQL)
    return tmp_path


def edit(path, change):
    doc = yaml.safe_load(path.read_text())
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def set_property(name, value):
    def change(doc):
        (prop,) = [p for p in doc["customProperties"] if p["property"] == name]
        prop["value"] = value

    return change


def test_the_repository_validates():
    assert cli.validate() == []


def test_every_table_outside_audit_has_exactly_one_contract():
    tables = [t for t in ddl.parse(ROOT / cli.LAKE_SQL) if not t.startswith("audit.")]
    described = [yaml.safe_load(p.read_text())["schema"][0]["physicalName"] for p in (ROOT / "contracts").glob("*/*/*.odcs.yaml")]
    assert sorted(described) == sorted(tables)


def test_the_parser_finds_every_table_in_lake_sql():
    # A table the parser misses would also be missing from the coverage test above.
    text = (ROOT / cli.LAKE_SQL).read_text()
    assert len(ddl.parse(ROOT / cli.LAKE_SQL)) == text.count("CREATE TABLE IF NOT EXISTS lake.")


def test_the_floor_is_the_one_maintenance_enforces():
    assert f"args.retain_days < {cli.FLOOR_DAYS}:" in (ROOT / "docker/lake/maintenance.py").read_text()


def test_a_wrong_partition_spec_fails(tree):
    edit(tree / TRADES, set_property("partitioning", ["days(exchange_ts)"]))
    assert cli.validate(tree) == [
        f"{TRADES}: partitioning ['days(exchange_ts)'], but {cli.LAKE_SQL} declares ['exchange', 'days(exchange_ts)']"
    ]


def test_a_wrong_sort_order_fails(tree):
    edit(tree / TRADES, set_property("sort", ["exchange_ts", "canonical_symbol"]))
    assert len(errors := cli.validate(tree)) == 1 and "sort" in errors[0], errors


def test_a_parent_with_expiry_under_the_floor_fails(tree):
    edit(tree / TRADES, set_property("retention", 3))
    errors = cli.validate(tree)
    # gold.trades is read by the four candle tables and by gold.bars.
    assert len(errors) == 5 and all(e.startswith(f"{TRADES}: ") and "7-day floor, not 3" in e for e in errors), errors


def test_a_table_nothing_reads_may_expire_sooner(tree):
    edit(tree / BBO, set_property("retention", 3))
    assert cli.validate(tree) == []


def test_a_table_with_no_contract_fails(tree):
    (tree / BBO).unlink()
    edit(tree / "contracts/market-analytics/book-snapshots/product.yaml", lambda d: d["outputs"].remove("gold.bbo_1s"))
    assert cli.validate(tree) == [f"{cli.LAKE_SQL}: gold.bbo_1s has no contract under contracts/"]


def test_a_table_less_restricted_than_its_input_fails(tree):
    edit(tree / TRADES, set_property("classification", "public"))
    errors = cli.validate(tree)
    assert any("gold.trades is public but reads silver.trades_kraken, which is internal" in e for e in errors), errors


def test_an_input_no_product_declares_fails(tree):
    edit(tree / CANDLES, lambda d: d["inputs"].append({"table": "gold.trades_v2"}))
    errors = cli.validate(tree)
    assert f"{CANDLES}: input gold.trades_v2 is not an output of any product" in errors, errors


def test_a_contract_outside_the_standard_fails(tree):
    edit(tree / TRADES, lambda d: d.pop("apiVersion"))
    errors = cli.validate(tree)
    assert any(e.startswith(f"{TRADES}: $: 'apiVersion' is a required property") for e in errors), errors
