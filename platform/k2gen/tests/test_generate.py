"""k2gen generate and k2gen new, on a copy of contracts/ and lake.sql.

The CI drift job (`make generate`, then no diff) proves the committed outputs are
current. These prove the properties that job relies on: a run is repeatable, a
deleted contract takes its outputs with it, and a scaffold is a valid product
whose generated DDL reads back as its contract declares.
"""

import shutil
from pathlib import Path

import pytest
import yaml

from k2gen import cli, ddl, generate

ROOT = cli.ROOT
EXAMPLE = "contracts/market-analytics/example"
EXAMPLE_DDL = f"{generate.GENERATED_DDL}/gold.example.sql"


@pytest.fixture
def tree(tmp_path):
    """contracts/ and lake.sql, without any generated file."""
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    (tmp_path / cli.LAKE_SQL).parent.mkdir(parents=True)
    shutil.copy(ROOT / cli.LAKE_SQL, tmp_path / cli.LAKE_SQL)
    return tmp_path


def snapshot(root: Path) -> dict:
    """Every file under the generator's outputs, path -> bytes."""
    places = [root / d for d in generate.OWNED] + [(root / generate.RETENTION).parent]
    return {p.relative_to(root).as_posix(): p.read_bytes() for d in places for p in sorted(d.rglob("*")) if p.is_file()}


def test_a_second_generate_changes_nothing(tree):
    generate.generate(tree)
    first = snapshot(tree)
    generate.generate(tree)
    assert snapshot(tree) == first
    assert f"{generate.PAGES}/market-analytics/candles.md" in first and generate.RETENTION in first


def test_a_scaffolded_product_validates(tree):
    written = generate.scaffold(tree, "market-analytics", "example")
    assert [p.relative_to(tree).as_posix() for p in written] == [f"{EXAMPLE}/product.yaml", f"{EXAMPLE}/gold.example.odcs.yaml"]
    assert cli.validate(tree) == []


def test_a_source_aligned_scaffold_is_bronze_and_validates(tree):
    generate.scaffold(tree, "fx-reference", "example-rates")
    assert (tree / "contracts/fx-reference/example-rates/bronze.example_rates.odcs.yaml").exists()
    assert cli.validate(tree) == []


def test_new_refuses_an_undeclared_domain_and_an_existing_product(tree):
    with pytest.raises(SystemExit, match="not a domain"):
        generate.scaffold(tree, "market-research", "example")
    with pytest.raises(SystemExit, match="already exists"):
        generate.scaffold(tree, "market-analytics", "candles")


def test_generated_ddl_reads_back_as_the_contract_declares(tree):
    generate.scaffold(tree, "market-analytics", "example")
    generate.generate(tree)
    (table,) = ddl.parse(tree / EXAMPLE_DDL).values()
    assert table.columns == ("id", "event_ts")
    assert table.partitioning == ("days(event_ts)",) and table.sort == ("event_ts",)
    assert table.schema[0] == ddl.Column("id", "STRING NOT NULL", "Placeholder key column.")


def test_a_deleted_contract_removes_its_outputs(tree):
    generate.scaffold(tree, "market-analytics", "example")
    generate.generate(tree)
    page = f"{generate.PAGES}/market-analytics/example.md"
    before = snapshot(tree)
    assert {EXAMPLE_DDL, page} <= before.keys() and b'"gold.example"' in before[generate.RETENTION]

    shutil.rmtree(tree / EXAMPLE)
    generate.generate(tree)
    after = snapshot(tree)
    assert EXAMPLE_DDL not in after and page not in after
    assert b"gold.example" not in after[generate.RETENTION] + after[f"{generate.PAGES}/README.md"]


def test_generate_writes_nothing_while_validate_fails(tree, monkeypatch):
    generate.scaffold(tree, "market-analytics", "example")
    contract = tree / EXAMPLE / "gold.example.odcs.yaml"
    contract.write_text(contract.read_text().replace('["days(event_ts)"]', '["days(recv_ts)"]'))
    monkeypatch.setattr(cli, "ROOT", tree)
    assert cli.main(["generate"]) == 1
    assert snapshot(tree) == {}


def test_inline_columns_for_a_table_in_lake_sql_fail(tree):
    contract = tree / "contracts/market-analytics/canonical-trades/gold.trades.odcs.yaml"
    doc = yaml.safe_load(contract.read_text())
    del doc["schema"][0]["authoritativeDefinitions"]
    doc["schema"][0]["properties"] = [{"name": "exchange", "physicalType": "STRING", "description": "The venue."}]
    contract.write_text(yaml.safe_dump(doc, sort_keys=False))
    errors = cli.validate(tree)
    assert any(e.endswith("gold.trades is created by docker/lake/ddl/lake.sql, so the contract references it, not inline columns") for e in errors), errors


def test_a_partition_on_an_undeclared_column_fails(tree):
    generate.scaffold(tree, "market-analytics", "example")
    contract = tree / EXAMPLE / "gold.example.odcs.yaml"
    contract.write_text(contract.read_text().replace('["days(event_ts)"]', '["days(recv_ts)"]'))
    assert cli.validate(tree) == [f"{EXAMPLE}/gold.example.odcs.yaml: partitioning days(recv_ts) reads no column the contract declares"]


def test_the_parser_reads_types_and_comments(tmp_path):
    sql = tmp_path / "lake.sql"
    sql.write_text(
        "CREATE TABLE IF NOT EXISTS lake.gold.t (\n"
        "    a     STRING  NOT NULL COMMENT 'It''s a key; with a semicolon',\n"
        "    b     STRUCT<x: BIGINT, y: STRING>\n"
        "                  COMMENT 'Split over two lines',\n"
        "    c     INT\n"
        ")\nUSING iceberg\nPARTITIONED BY (days(c), a)\nTBLPROPERTIES (\n    'format-version' = '2'\n);\n"
    )
    assert ddl.parse(sql)["gold.t"].schema == (
        ddl.Column("a", "STRING NOT NULL", "It's a key; with a semicolon"),
        ddl.Column("b", "STRUCT<x: BIGINT, y: STRING>", "Split over two lines"),
        ddl.Column("c", "INT", ""),
    )


def test_pages_carry_no_em_dash_although_lake_sql_comments_do():
    # check-docs gate (h) refuses an em-dash on an added line, and a page is regenerated whole.
    assert "\u2014" in (ROOT / cli.LAKE_SQL).read_text()
    assert not [p for p, text in generate.render(ROOT).items() if "\u2014" in text]
