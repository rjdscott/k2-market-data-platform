"""k2gen generate and k2gen new on the repository's tree and on scaffolded ones (conftest.py).

The CI drift job (`make generate`, then no diff) proves the committed outputs are current.
These prove the properties that job relies on. A run is repeatable; a deleted contract
takes its outputs with it; a tree with no product still writes every domain's fence, and
a scaffold is a valid product whose generated DDL reads back as its contract declares.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import FRAMES, FRAMES_CONTRACT, MART, TRADES_CONTRACT, TRADES_PRODUCT, edit

from k2gen import cli, ddl, generate

ROOT = cli.ROOT
EXAMPLE = "contracts/market-analytics/example"
EXAMPLE_DDL = f"{generate.GENERATED_DDL}/gold.example.sql"
TRADES_PAGE = f"{generate.PAGES}/market-analytics/example-trades.md"


def snapshot(root: Path) -> dict:
    """Every file under the generator's outputs, path -> bytes."""
    places = [root / d for d in generate.OWNED] + [(root / generate.RETENTION).parent, root / "docker/trino", root / "dbt"]
    return {p.relative_to(root).as_posix(): p.read_bytes() for d in places for p in sorted(d.rglob("*")) if p.is_file()}


def test_a_second_generate_changes_nothing(tree):
    generate.generate(tree)
    first = snapshot(tree)
    generate.generate(tree)
    assert snapshot(tree) == first
    assert TRADES_PAGE in first and generate.RETENTION in first


def test_a_tree_with_no_product_writes_every_fence_and_no_table(bare):
    # The repository today: five domains, no product (ADR-056).
    files = generate.generate(bare)
    domains = yaml.safe_load((bare / "contracts/domains.yaml").read_text())["domains"]
    assert json.loads(files[generate.RETENTION]) == {} and json.loads(files[generate.CONTRACT_SCHEMAS]) == {}
    assert yaml.safe_load(files[generate.DBT_SOURCES])["sources"] == [] and yaml.safe_load(files[generate.DBT_SELECTORS])["selectors"] == []
    assert sorted(p for p in files if p.startswith(generate.PAGES)) == [f"{generate.PAGES}/README.md", generate.PRODUCT_TABLE]
    assert files[f"{generate.PAGES}/README.md"].count("No product yet.") == len(domains)
    assert files[generate.PRODUCT_TABLE].endswith("\nNo product yet.\n")
    assert sorted(p for p in files if p.startswith(generate.GENERATED_DDL)) == [f"{generate.GENERATED_DDL}/README.md"]
    for catalogs in (generate.CATALOGS, generate.TEST_CATALOGS):
        assert sorted(p for p in files if p.startswith(catalogs)) == sorted(f"{catalogs}/{cli.fence(d)}.properties" for d in domains)


def test_a_scaffolded_product_validates(bare):
    written = generate.scaffold(bare, "market-analytics", "example")
    assert [p.relative_to(bare).as_posix() for p in written] == [f"{EXAMPLE}/product.yaml", f"{EXAMPLE}/gold.example.odcs.yaml"]
    assert cli.validate(bare) == []


def test_the_scaffolds_marts_stub_validates_once_uncommented(bare):
    generate.scaffold(bare, "market-analytics", "example-desk")
    path = bare / "contracts/market-analytics/example-desk/product.yaml"
    text = path.read_text()
    stub = text[text.index("# marts:"):]
    path.write_text(text.replace(stub, "\n".join(line.removeprefix("# ") for line in stub.splitlines()) + "\n"))
    (mart,) = yaml.safe_load(path.read_text())["marts"]
    assert mart == {"table": "market_analytics.example_desk.example_desk", "grain": ["id"], "reads": ["gold.example_desk"]}
    assert cli.validate(bare) == []


def test_a_source_aligned_scaffold_is_bronze_and_validates(bare):
    generate.scaffold(bare, "fx-reference", "example-rates")
    assert (bare / "contracts/fx-reference/example-rates/bronze.example_rates.odcs.yaml").exists()
    assert cli.validate(bare) == []


def test_new_refuses_an_undeclared_domain_and_an_existing_product(tree):
    with pytest.raises(SystemExit, match="not a domain"):
        generate.scaffold(tree, "market-research", "example")
    with pytest.raises(SystemExit, match="already exists"):
        generate.scaffold(tree, "market-analytics", "example-trades")


def test_generated_ddl_reads_back_as_the_contract_declares(bare):
    generate.scaffold(bare, "market-analytics", "example")
    generate.generate(bare)
    (table,) = ddl.parse(bare / EXAMPLE_DDL).values()
    assert table.columns == ("id", "event_ts")
    assert table.partitioning == ("days(event_ts)",) and table.sort == ("event_ts",)
    assert table.schema[0] == ddl.Column("id", "STRING NOT NULL", "Placeholder key column.")


def test_contract_schemas_state_every_table_as_its_contract_does(tree):
    schemas = json.loads(generate.render(tree)[generate.CONTRACT_SCHEMAS])
    contracts = {yaml.safe_load(p.read_text())["schema"][0]["physicalName"] for p in cli.old_contracts(tree / "contracts")}
    assert schemas.keys() == contracts == {"bronze.example_frames", "gold.example_trades"}
    # Its own columns in order, NOT NULL split out, and no identifier fields, since its generated DDL sets none.
    assert schemas["gold.example_trades"] == {
        "columns": [{"name": "id", "type": "STRING", "required": True}, {"name": "event_ts", "type": "TIMESTAMP", "required": True}],
        "partitioning": ["days(event_ts)"],
        "sort": ["event_ts"],
        "identifier": [],
    }


def test_a_deleted_contract_removes_its_outputs(bare):
    generate.scaffold(bare, "market-analytics", "example")
    generate.generate(bare)
    page = f"{generate.PAGES}/market-analytics/example.md"
    before = snapshot(bare)
    assert {EXAMPLE_DDL, page} <= before.keys() and b'"gold.example"' in before[generate.RETENTION]

    shutil.rmtree(bare / EXAMPLE)
    generate.generate(bare)
    after = snapshot(bare)
    assert EXAMPLE_DDL not in after and page not in after
    assert b"gold.example" not in after[generate.RETENTION] + after[f"{generate.PAGES}/README.md"] + after[generate.PRODUCT_TABLE]


def test_the_product_table_has_one_row_per_product(tree):
    generate.scaffold(tree, "fx-reference", "example-rates")
    generate.generate(tree)
    rows = [r for r in (tree / generate.PRODUCT_TABLE).read_text().splitlines() if r.startswith("| [")]
    assert len(rows) == len(list(tree.glob("contracts/*/*/product.yaml"))) == 3
    new = "| [fx-reference](README.md#fx-reference) | [example-rates](fx-reference/example-rates.md) | `fx-reference-team` | draft | running | `bronze.example_rates` |"
    assert new in rows
    (trades,) = [r for r in rows if "| [example-trades](" in r]
    assert "`analytics-team`" in trades and "`gold.example_trades`" in trades and f"`{MART}`" in trades


def test_generate_writes_nothing_while_validate_fails(bare, monkeypatch):
    generate.scaffold(bare, "market-analytics", "example")
    contract = bare / EXAMPLE / "gold.example.odcs.yaml"
    contract.write_text(contract.read_text().replace('["days(event_ts)"]', '["days(recv_ts)"]'))
    monkeypatch.setattr(cli, "ROOT", bare)
    assert cli.main(["generate"]) == 1
    assert snapshot(bare) == {}


def test_the_parser_reads_types_and_comments(tmp_path):
    sql = tmp_path / "t.sql"
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


def test_pages_carry_no_em_dash_although_a_contract_does(tree):
    # check-docs gate (h) refuses an em-dash on an added line, and a page is regenerated whole.
    edit(tree / TRADES_PRODUCT, lambda d: d.update(purpose="Trades \u2014 made up."))
    edit(tree / TRADES_CONTRACT, lambda d: d["schema"][0]["properties"][0].update(description="Key \u2014 made up."))
    files = generate.render(tree)
    assert not [p for p, text in files.items() if "\u2014" in text]
    assert "Trades: made up." in files[TRADES_PAGE] and "| `id` | `STRING NOT NULL` | Key: made up. |" in files[TRADES_PAGE]


def add_domain(root: Path, name: str, owner: str = "research-team") -> None:
    path = root / "contracts/domains.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["domains"][name] = {"owner": owner, "kind": "consumer-aligned", "description": "A test domain."}
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_a_new_domain_gets_its_fence(bare):
    # ADR-044: adding a domain and running generate is the whole act of giving it a fence.
    add_domain(bare, "research-desk")
    assert cli.validate(bare) == []
    files = generate.generate(bare)
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


def test_a_removed_domain_takes_its_catalog_and_leaves_lake_properties(bare):
    lake = bare / generate.CATALOGS / "lake.properties"
    lake.parent.mkdir(parents=True)
    lake.write_text("connector.name=iceberg\niceberg.security=read_only\n")
    add_domain(bare, "research-desk")
    generate.generate(bare)
    assert (bare / generate.CATALOGS / "research_desk.properties").exists()

    path = bare / "contracts/domains.yaml"
    doc = yaml.safe_load(path.read_text())
    del doc["domains"]["research-desk"]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    generate.generate(bare)
    assert not (bare / generate.CATALOGS / "research_desk.properties").exists()
    assert not (bare / generate.TEST_CATALOGS / "research_desk.properties").exists()
    assert "research_desk" not in (bare / generate.TRINO_RULES).read_text() + (bare / generate.WAREHOUSES).read_text()
    assert lake.read_text() == "connector.name=iceberg\niceberg.security=read_only\n"


@pytest.mark.parametrize("name", ["lake", "system", "k2", "information-schema", "table", "default", "user"])
def test_a_domain_whose_fence_name_is_taken_is_rejected(bare, name):
    add_domain(bare, name)
    fence = cli.fence(name)
    assert [e for e in cli.validate(bare) if f"domain {name} would be catalog and warehouse {fence}" in e]


def test_a_domain_id_that_is_not_a_slug_is_rejected(bare):
    add_domain(bare, "Research_Desk")
    assert [e for e in cli.validate(bare) if "contracts/domains.yaml" in e and "Research_Desk" in e]


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


def test_dbt_sources_carry_trino_types_and_freshness(tree):
    # ADR-044 and dbt/README.md: the sources a mart reads come from the contracts, not by hand.
    # One source: bronze.example_frames is read by a lake table, not by a mart.
    (gold,) = yaml.safe_load(generate.render(tree)[generate.DBT_SOURCES])["sources"]
    assert (gold["name"], gold["database"], gold["schema"]) == ("gold", "lake", "gold")
    (trades,) = gold["tables"]
    assert trades["name"] == "example_trades"
    assert {c["name"]: c["data_type"] for c in trades["columns"]} == {"id": "varchar", "event_ts": "timestamp(6) with time zone"}
    assert trades["config"]["meta"] == {"contract": TRADES_CONTRACT, "owner": "analytics-team", "read_by": [MART]}
    assert trades["config"]["loaded_at_field"] == "event_ts"
    assert trades["config"]["freshness"] == {"error_after": {"count": 30, "period": "minute"}}


def test_a_source_with_no_latency_has_no_freshness(tree):
    edit(tree / TRADES_CONTRACT, lambda d: d.pop("slaProperties"))
    (gold,) = yaml.safe_load(generate.render(tree)[generate.DBT_SOURCES])["sources"]
    assert "freshness" not in gold["tables"][0]["config"] and "loaded_at_field" not in gold["tables"][0]["config"]


def test_one_dbt_selector_per_domain_with_marts(tree):
    # Domains with no mart (market-capture here) get none.
    (selector,) = yaml.safe_load(generate.render(tree)[generate.DBT_SELECTORS])["selectors"]
    assert selector["name"] == "market_analytics"
    assert selector["definition"]["union"] == [{"method": "fqn", "value": "market_analytics"}, {"method": "source", "value": "gold.example_trades"}]


def test_a_removed_mart_takes_its_sources_and_selector_with_it(tree):
    edit(tree / TRADES_PRODUCT, lambda d: d.pop("marts"))
    assert cli.validate(tree) == []
    files = generate.render(tree)
    assert files[generate.DBT_SOURCES] == generate._dbt_sources([], {})
    assert files[generate.DBT_SELECTORS] == generate._dbt_selectors([])
    assert yaml.safe_load(files[generate.DBT_SOURCES])["sources"] == []
    assert yaml.safe_load(files[generate.DBT_SELECTORS])["selectors"] == []


def test_a_mart_reading_a_type_with_no_trino_mapping_stops_generate():
    with pytest.raises(ValueError, match="no Trino type for MAP<STRING, BINARY>"):
        generate._trino_type("MAP<STRING, BINARY>")
    assert generate._trino_type("DECIMAL(38,10) NOT NULL") == "decimal(38,10)"


def test_the_product_page_says_how_each_mart_is_built(tree):
    page = generate.render(tree)[TRADES_PAGE]
    assert f"| `{MART}` | table |" in page
    # In the diagram too, fed by what it reads: the input is n0, the table n1, the mart n2.
    assert f'    n2["{MART}"]\n  end' in page and "  n0 --> n1\n" in page and "  n1 --> n2\n" in page
    edit(tree / TRADES_PRODUCT, lambda d: d["marts"][0].update(materialized="view"))
    assert f"| `{MART}` | view |" in generate.render(tree)[TRADES_PAGE]


def test_a_paused_table_shows_on_its_page_the_index_and_the_product_table(tree):
    """ADR-050: running, paused, or `n of m tables paused`, per product; the mark per table."""
    mark = {"property": "paused", "value": {"since": "2026-11-26", "adr": "ADR-050"}}
    # example-frames gets a second table, and only it is paused: 1 of 2.
    second = tree / FRAMES / "bronze.example_frames_b.odcs.yaml"
    shutil.copy(tree / FRAMES_CONTRACT, second)
    edit(second, lambda d: (d.update(name="example_frames_b"), d["schema"][0].update(physicalName="bronze.example_frames_b"), d["customProperties"].append(mark)))
    edit(tree / FRAMES / "product.yaml", lambda d: d["outputs"].append("bronze.example_frames_b"))
    # A third product whose only table is paused: paused.
    generate.scaffold(tree, "market-capture", "example-books")
    edit(tree / "contracts/market-capture/example-books/bronze.example_books.odcs.yaml", lambda d: d["customProperties"].append(mark))
    assert cli.validate(tree) == []
    files = generate.render(tree)

    adr = cli.adr_file(tree, "ADR-050")
    assert adr is not None
    status = f"paused since 2026-11-26, [ADR-050](../../../docs/adr/{adr.name}): the data is readable, nothing new arrives"
    frames = files[f"{generate.PAGES}/market-capture/example-frames.md"]
    assert "| Status | 1 of 2 tables paused |" in frames
    paused = frames[frames.index("## bronze.example_frames_b"):]
    assert paused.splitlines()[6] == f"| Status | {status} |", paused
    running = frames[frames.index("## bronze.example_frames\n"):frames.index("## bronze.example_frames_b")]
    assert "| Status | running |" in running
    assert f"| Status | {status} |" in files[f"{generate.PAGES}/market-capture/example-books.md"].split("## ")[0]

    for index in (f"{generate.PAGES}/README.md", generate.PRODUCT_TABLE):
        rows = {line.split("|")[1 if index.endswith("README.md") else 2].strip(): line for line in files[index].splitlines() if line.startswith("| [")}
        cell = {name: [c.strip() for c in line.split("|")] for name, line in rows.items()}
        state = {name.split("]")[0].lstrip("["): cells[-3] for name, cells in cell.items()}
        assert state == {"example-books": "paused", "example-frames": "1 of 2 tables paused", "example-trades": "running"}, state

