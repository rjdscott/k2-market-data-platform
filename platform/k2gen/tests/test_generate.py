"""k2gen generate and k2gen new, on a copy of contracts/ and lake.sql.

The CI drift job (`make generate`, then no diff) proves the committed outputs are
current. These prove the properties that job relies on: a run is repeatable, a
deleted contract takes its outputs with it, and a scaffold is a valid product
whose generated DDL reads back as its contract declares.
"""

import json
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
    places = [root / d for d in generate.OWNED] + [(root / generate.RETENTION).parent, root / "docker/trino"]
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


def test_the_scaffolds_marts_stub_validates_once_uncommented(tree):
    generate.scaffold(tree, "market-analytics", "example-desk")
    path = tree / "contracts/market-analytics/example-desk/product.yaml"
    text = path.read_text()
    stub = text[text.index("# marts:"):]
    path.write_text(text.replace(stub, "\n".join(line.removeprefix("# ") for line in stub.splitlines()) + "\n"))
    (mart,) = yaml.safe_load(path.read_text())["marts"]
    assert mart == {"table": "market_analytics.example_desk.example_desk", "grain": ["id"], "reads": ["gold.example_desk"]}
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
    assert b"gold.example" not in after[generate.RETENTION] + after[f"{generate.PAGES}/README.md"] + after[generate.PRODUCT_TABLE]


def test_the_product_table_has_one_row_per_product(tree):
    generate.scaffold(tree, "fx-reference", "example-rates")
    generate.generate(tree)
    rows = [r for r in (tree / generate.PRODUCT_TABLE).read_text().splitlines() if r.startswith("| [")]
    assert len(rows) == len(list(tree.glob("contracts/*/*/product.yaml")))
    new = "| [fx-reference](README.md#fx-reference) | [example-rates](fx-reference/example-rates.md) | `fx-reference-team` | draft | `bronze.example_rates` |"
    assert new in rows
    (candles,) = [r for r in rows if "| [candles](" in r]
    assert "`analytics-team`" in candles and "`gold.ohlcv_1m`" in candles and "`market_analytics.candles.ohlcv_1m`" in candles


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


def add_domain(root: Path, name: str, owner: str = "research-team") -> None:
    path = root / "contracts/domains.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["domains"][name] = {"owner": owner, "kind": "consumer-aligned", "description": "A test domain."}
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_a_new_domain_gets_its_fence(tree):
    # ADR-044: adding a domain and running generate is the whole act of giving it a fence.
    add_domain(tree, "research-desk")
    assert cli.validate(tree) == []
    files = generate.generate(tree)
    catalog = files[f"{generate.CATALOGS}/research_desk.properties"]
    assert "iceberg.rest-catalog.warehouse=research_desk\n" in catalog and "warehouse=k2" not in catalog
    assert "iceberg.add-files-procedure.enabled=false\n" in catalog and "iceberg.register-table-procedure.enabled=false\n" in catalog
    assert "iceberg.catalog.type=TESTING_FILE_METASTORE\n" in files[f"{generate.TEST_CATALOGS}/research_desk.properties"]
    assert "research_desk" in files[generate.WAREHOUSES].splitlines()
    rules = json.loads(files[generate.TRINO_RULES])
    assert {"lake", "research_desk"} <= set(rules["catalogs"][-1]["catalog"].split("|"))
    assert {"user": "dbt_research_desk", "catalog": "research_desk", "owner": True} in rules["schemas"]
    writer = {"user": "dbt_research_desk", "catalog": "research_desk"}
    assert [r for r in rules["tables"] if writer.items() <= r.items() and "OWNERSHIP" in r["privileges"]]
    # Everyone else, everywhere: the last rule in each list, which matches any user.
    assert rules["schemas"][-1] == {"owner": False}
    assert rules["tables"][-1] == {"privileges": ["SELECT", "GRANT_SELECT"]}
    # No rule names lake for a writer: only its read-only connector and the last rules apply there.
    assert not [r for r in rules["schemas"] + rules["tables"] if r.get("catalog") == "lake"]


def test_a_removed_domain_takes_its_catalog_and_leaves_lake_properties(tree):
    lake = tree / generate.CATALOGS / "lake.properties"
    lake.parent.mkdir(parents=True)
    lake.write_text("connector.name=iceberg\niceberg.security=read_only\n")
    add_domain(tree, "research-desk")
    generate.generate(tree)
    assert (tree / generate.CATALOGS / "research_desk.properties").exists()

    path = tree / "contracts/domains.yaml"
    doc = yaml.safe_load(path.read_text())
    del doc["domains"]["research-desk"]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    generate.generate(tree)
    assert not (tree / generate.CATALOGS / "research_desk.properties").exists()
    assert not (tree / generate.TEST_CATALOGS / "research_desk.properties").exists()
    assert "research_desk" not in (tree / generate.TRINO_RULES).read_text() + (tree / generate.WAREHOUSES).read_text()
    assert lake.read_text() == "connector.name=iceberg\niceberg.security=read_only\n"


@pytest.mark.parametrize("name", ["lake", "system", "k2", "information-schema", "table", "default", "user"])
def test_a_domain_whose_fence_name_is_taken_is_rejected(tree, name):
    add_domain(tree, name)
    fence = cli.fence(name)
    assert [e for e in cli.validate(tree) if f"domain {name} would be catalog and warehouse {fence}" in e]


def test_a_domain_id_that_is_not_a_slug_is_rejected(tree):
    add_domain(tree, "Research_Desk")
    assert [e for e in cli.validate(tree) if "contracts/domains.yaml" in e and "Research_Desk" in e]


def test_the_rules_keep_the_retention_floors_and_other_peoples_queries_out_of_reach():
    rules = json.loads(generate.render(ROOT)[generate.TRINO_RULES])
    first, last = rules["catalog_session_properties"]
    assert first == {"property": "expire_snapshots_min_retention|remove_orphan_files_min_retention", "allow": False}
    assert "user" not in first and "catalog" not in first and last == {"allow": True}
    assert rules["queries"] == [{"allow": ["execute"]}]
    assert "procedures" not in rules  # so CALL runs only Trino's built-in procedures


def test_warehouses_txt_ends_with_a_newline():
    # init-lake.sh reads it with `while read`, which drops an unterminated last line.
    text = generate.render(ROOT)[generate.WAREHOUSES]
    assert text.endswith("\n") and text.splitlines()[-1] == sorted(cli.fence(d) for d in yaml.safe_load((ROOT / "contracts/domains.yaml").read_text())["domains"])[-1]


def test_dbt_sources_carry_trino_types_the_identifier_check_and_freshness():
    # ADR-044 and dbt/README.md: the sources a mart reads come from the contracts, not by hand.
    files = generate.render(ROOT)
    audit, gold = yaml.safe_load(files[generate.DBT_SOURCES])["sources"]
    assert (gold["name"], gold["database"], gold["schema"]) == ("gold", "lake", "gold")
    ohlcv, trades = gold["tables"]
    types = {c["name"]: c["data_type"] for c in trades["columns"]}
    assert types["exchange_ts"] == "timestamp(6) with time zone" and types["price_e8"] == "bigint" and types["src_index"] == "integer"
    assert list(types) == list(ddl.parse(ROOT / cli.LAKE_SQL)["gold.trades"].columns)
    assert trades["config"]["loaded_at_field"] == "ingest_ts"
    assert trades["config"]["freshness"] == {"error_after": {"count": 30, "period": "minute"}}
    assert trades["config"]["meta"]["read_by"] == ["market_analytics.candles.ohlcv_1m", "market_analytics.venue_quality.venue_quality_scorecard"]
    # The delayed view reads the lake's candles (ADR-035), checked on their identifier fields, with no freshness declared.
    assert ohlcv["config"]["meta"]["read_by"] == ["market_analytics.delayed_candles.ohlcv_delayed_20m"]
    assert ohlcv["data_tests"][0]["dbt_expectations.expect_compound_columns_to_be_unique"]["arguments"]["column_list"] == ["exchange", "canonical_symbol", "window_start"]
    assert "freshness" not in ohlcv["config"]
    (test,) = trades["data_tests"]
    args = test["dbt_expectations.expect_compound_columns_to_be_unique"]["arguments"]
    assert args == {"column_list": ["exchange", "canonical_symbol", "trade_id"], "row_condition": "exchange_ts >= current_timestamp - interval '6' hour"}
    # audit.checks: its own types, no identifier fields so no uniqueness check, no latency so no freshness.
    (checks,) = audit["tables"]
    assert {c["name"]: c["data_type"] for c in checks["columns"]} == {
        "run_ts": "timestamp(6) with time zone", "job": "varchar", "check_name": "varchar", "scope": "varchar",
        "passed": "boolean", "observed": "bigint", "detail": "varchar",
    }
    assert "data_tests" not in checks and "freshness" not in checks["config"]


def test_one_dbt_selector_per_domain_with_marts():
    # Domains with no mart (fx-reference, market-capture, reference-data) get none.
    governance, selector = yaml.safe_load(generate.render(ROOT)[generate.DBT_SELECTORS])["selectors"]
    assert governance["name"] == "governance"
    assert governance["definition"]["union"] == [{"method": "fqn", "value": "governance"}, {"method": "source", "value": "audit.checks"}]
    assert selector["name"] == "market_analytics"
    assert selector["definition"]["union"] == [
        {"method": "fqn", "value": "market_analytics"},
        {"method": "source", "value": "audit.checks"},
        {"method": "source", "value": "gold.ohlcv_1m"},
        {"method": "source", "value": "gold.trades"},
    ]


def test_a_removed_mart_takes_its_sources_and_selector_with_it(tree):
    # The last marts gone: what scripts/dbt-test.sh then shows dbt still parses.
    shutil.rmtree(tree / "contracts/market-analytics/delayed-candles")
    shutil.rmtree(tree / "contracts/market-analytics/venue-quality")
    shutil.rmtree(tree / "contracts/governance/quality-results")
    path = tree / "contracts/market-analytics/candles/product.yaml"
    doc = yaml.safe_load(path.read_text())
    del doc["marts"]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert cli.validate(tree) == []
    assert generate.render(tree)[generate.DBT_SOURCES] == generate._dbt_sources([], {}, {})
    assert generate.render(tree)[generate.DBT_SELECTORS] == generate._dbt_selectors([])
    files = generate.render(tree)
    assert yaml.safe_load(files[generate.DBT_SOURCES])["sources"] == []
    assert yaml.safe_load(files[generate.DBT_SELECTORS])["selectors"] == []


def test_a_mart_reading_a_type_with_no_trino_mapping_stops_generate():
    with pytest.raises(ValueError, match="no Trino type for MAP<STRING, BINARY>"):
        generate._trino_type("MAP<STRING, BINARY>")
    assert generate._trino_type("DECIMAL(38,10) NOT NULL") == "decimal(38,10)"


def test_the_parser_reads_identifier_fields():
    assert ddl.parse(ROOT / cli.LAKE_SQL)["gold.trades"].identifier == ("exchange", "canonical_symbol", "trade_id")


def test_the_product_page_says_how_each_mart_is_built(tree):
    page = generate.render(ROOT)[f"{generate.PAGES}/market-analytics/candles.md"]
    assert "| `market_analytics.candles.ohlcv_1m` | table |" in page
    # In the diagram too, fed by what it reads: gold.trades is node n0, the mart the last one in the box.
    assert '    n5["market_analytics.candles.ohlcv_1m"]\n  end' in page and "  n0 --> n5\n" in page
    path = tree / "contracts/market-analytics/candles/product.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["marts"][0]["materialized"] = "view"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert "| `market_analytics.candles.ohlcv_1m` | view |" in generate.render(tree)[f"{generate.PAGES}/market-analytics/candles.md"]
