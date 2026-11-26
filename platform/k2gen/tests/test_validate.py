"""k2gen validate, against the real contracts/ and lake.sql and against copies broken one way each.

Each failure test breaks one thing in a copy of the tree and asserts the line
that names it, so a check that stops firing fails here rather than in review.
"""

import shutil

import pytest
import yaml

from k2gen import cli, ddl, generate

ROOT = cli.ROOT
TRADES = "contracts/market-analytics/canonical-trades/gold.trades.odcs.yaml"
BBO = "contracts/market-analytics/book-snapshots/gold.bbo_1s.odcs.yaml"
CANDLES = "contracts/market-analytics/candles/product.yaml"
AUDIT = "contracts/market-capture/lake-audit"
DELAYED = "contracts/market-analytics/delayed-candles/product.yaml"


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


def test_every_table_has_exactly_one_contract():
    tables = list(ddl.parse(ROOT / cli.LAKE_SQL))
    described = [yaml.safe_load(p.read_text())["schema"][0]["physicalName"] for p in (ROOT / "contracts").glob("*/*/*.odcs.yaml")]
    assert sorted(described) == sorted(tables)


def test_an_audit_table_without_a_contract_fails(tree):
    # The nightly contract_schema audit fails it in the audit namespace, so CI must too.
    # The marts that read audit.checks go with its contract, so the one error left is the table's.
    shutil.rmtree(tree / AUDIT)
    shutil.rmtree(tree / "contracts/governance/quality-results")
    edit(tree / "contracts/market-analytics/venue-quality/product.yaml", lambda d: d.update(inputs=[{"table": "gold.trades"}], marts=[{**d["marts"][0], "reads": ["gold.trades"]}]))
    assert cli.validate(tree) == [f"{cli.LAKE_SQL}: audit.checks has no contract under contracts/"]


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


def set_mart(**fields):
    def change(doc):
        doc["marts"][0].update(fields)

    return change


@pytest.mark.parametrize("table", ["market_capture.candles.ohlcv_1m", "market_analytics.ohlcv.ohlcv_1m"])
def test_a_mart_outside_its_domain_catalog_or_product_schema_fails(tree, table):
    # ADR-044: a domain writes only its own catalog, and each product has its own schema.
    edit(tree / CANDLES, set_mart(table=table))
    assert cli.validate(tree) == [f"{CANDLES}: mart {table} must live in market_analytics.candles, its domain's catalog and its product's schema (ADR-044)"]


def test_a_mart_reading_a_table_its_product_does_not_declare_fails(tree):
    edit(tree / CANDLES, set_mart(reads=["gold.trades", "gold.bars"]))
    assert cli.validate(tree) == [f"{CANDLES}: mart market_analytics.candles.ohlcv_1m reads gold.bars, which is neither one of its outputs nor a resolved input"]


def test_a_mart_declared_twice_fails(tree):
    edit(tree / CANDLES, lambda doc: doc["marts"].append(dict(doc["marts"][0])))
    assert cli.validate(tree) == [f"{CANDLES}: mart market_analytics.candles.ohlcv_1m is declared twice"]


def test_a_latency_on_a_column_the_table_lacks_fails(tree):
    def change(doc):
        doc["slaProperties"][0]["element"] = "gold.trades.loaded_at"

    edit(tree / TRADES, change)
    assert cli.validate(tree) == [f"{TRADES}: latency element gold.trades.loaded_at is not a column of gold.trades"]


def test_a_latency_without_a_unit_fails_the_k2_profile(tree):
    edit(tree / TRADES, lambda doc: doc["slaProperties"][0].pop("unit"))
    assert [e for e in cli.validate(tree) if e.startswith(f"{TRADES}: $.slaProperties[0]") and "'unit' is a required property" in e]


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("staging", "the schema dbt keeps every domain's staging models in (dbt/macros/naming.sql)"),
        ("intermediate", "the schema dbt keeps every domain's intermediate models in (dbt/macros/naming.sql)"),
        ("dq", "the schema every domain's dbt run records its test results in (dbt/macros/record_test_results.sql)"),
    ],
)
def test_a_product_named_like_a_platform_schema_is_rejected(tree, name, why):
    # Its marts would share <catalog>.<name> with the domain's staging or intermediate views,
    # or with the test results its dbt runs record (ADR-046).
    generate.scaffold(tree, "market-analytics", name)
    assert cli.validate(tree) == [f"contracts/market-analytics/{name}/product.yaml: product name {name} is {why}"]


def test_a_mart_is_a_table_or_a_view_by_its_contract(tree):
    edit(tree / CANDLES, set_mart(materialized="view"))
    assert cli.validate(tree) == []
    edit(tree / CANDLES, set_mart(materialized="ephemeral"))
    assert [e for e in cli.validate(tree) if e.startswith(f"{CANDLES}: $.marts[0].materialized") and "'ephemeral' is not one of ['table', 'view']" in e]


def test_a_product_may_build_only_marts_but_not_nothing(tree):
    # ADR-035's delayed product is a view and writes no lake table.
    assert yaml.safe_load((tree / DELAYED).read_text())["outputs"] == []
    assert cli.validate(tree) == []
    edit(tree / DELAYED, lambda d: d.pop("marts"))
    errors = cli.validate(tree)
    assert [e for e in errors if e.startswith(f"{DELAYED}: $: ") and "is not valid under any of the given schemas" in e], errors


def test_audit_is_a_layer_but_not_a_made_up_one(tree):
    contract = tree / AUDIT / "audit.checks.odcs.yaml"
    edit(contract, set_property("layer", "quality"))
    assert [e for e in cli.validate(tree) if "customProperties" in e and "'quality' is not one of" in e]
