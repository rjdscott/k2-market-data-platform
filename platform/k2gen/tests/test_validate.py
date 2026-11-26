"""k2gen validate, against the repository and against a scaffolded tree broken one way each.

Each failure test breaks one thing in a copy of the tree (conftest.py) and asserts the line
that names it, so a check that stops firing fails here rather than in review.
"""

import shutil
from datetime import date

import pytest
import yaml
from conftest import (
    FRAMES,
    FRAMES_CONTRACT,
    MART,
    TRADES_CONTRACT,
    TRADES_PRODUCT,
    edit,
    set_property,
)

from k2gen import cli, generate


def test_the_repository_validates():
    assert cli.validate() == []


V2_GOOD = cli.ROOT / "platform/k2gen/tests/fixtures/v2/good/contracts"


def test_a_v2_product_directory_is_left_to_k2gen_check(tree):
    # The old validator and generate read only the old format; a directory holding
    # product.odps.yaml is the v2 path's (k2gen check, S05) until S23 deletes these two.
    before = generate.render(tree)
    for product in ("sensor-net/station-readings", "ops-insight/daily-load"):
        shutil.copytree(V2_GOOD / product, tree / "contracts" / product)
    assert cli.validate(tree) == []
    assert generate.render(tree) == before
    # the skip is the product file, not the directory: without it the contracts are read as old ones
    (tree / "contracts/sensor-net/station-readings/product.odps.yaml").unlink()
    assert any("station-readings" in line for line in cli.validate(tree))


def test_a_contract_that_references_lake_sql_fails(tree):
    # The schema still admits the reference form; the DDL it points at retired (ADR-056).
    def reference(doc):
        del doc["schema"][0]["properties"]
        doc["schema"][0]["authoritativeDefinitions"] = [{"type": "implementation", "url": "docker/lake/ddl/lake.sql"}]
        doc.pop("slaProperties")

    edit(tree / TRADES_CONTRACT, reference)
    assert cli.validate(tree) == [
        f"{TRADES_CONTRACT}: gold.example_trades declares no inline columns; docker/lake/ddl/lake.sql, "
        "which it references, retired to legacy/v3-lake-ingest/ (ADR-056)"
    ]


def test_a_second_contract_for_a_table_fails(tree):
    copy = tree / "contracts/market-analytics/example-copy"
    copy.mkdir()
    shutil.copy(tree / TRADES_PRODUCT, copy / "product.yaml")
    shutil.copy(tree / TRADES_CONTRACT, copy / "gold.example_trades.odcs.yaml")
    edit(copy / "product.yaml", lambda d: d.update(name="example-copy", marts=[]))
    # example-copy sorts first, so the original is the second.
    assert f"{TRADES_CONTRACT}: a second contract for gold.example_trades" in cli.validate(tree)


def test_an_output_with_no_contract_beside_it_fails(tree):
    edit(tree / TRADES_PRODUCT, lambda d: d["outputs"].append("gold.example_more"))
    assert cli.validate(tree) == [
        f"{TRADES_PRODUCT}: outputs ['gold.example_more', 'gold.example_trades'], but the contracts beside it are ['gold.example_trades']"
    ]


def test_a_layer_other_than_its_namespace_fails(tree):
    edit(tree / TRADES_CONTRACT, set_property("layer", "silver"))
    assert cli.validate(tree) == [f"{TRADES_CONTRACT}: layer silver, but gold.example_trades is in namespace gold"]


def test_a_made_up_layer_fails_the_profile(tree):
    edit(tree / TRADES_CONTRACT, set_property("layer", "quality"))
    assert [e for e in cli.validate(tree) if "customProperties" in e and "'quality' is not one of" in e]


@pytest.mark.parametrize("key", ["partitioning", "sort"])
def test_a_partition_or_sort_on_an_undeclared_column_fails(tree, key):
    edit(tree / TRADES_CONTRACT, set_property(key, ["days(recv_ts)"]))
    assert cli.validate(tree) == [f"{TRADES_CONTRACT}: {key} days(recv_ts) reads no column the contract declares"]


def test_a_parent_with_expiry_under_the_floor_fails(tree):
    edit(tree / FRAMES_CONTRACT, set_property("retention", 3))
    assert cli.validate(tree) == [
        f"{FRAMES_CONTRACT}: gold.example_trades reads it, so it must keep snapshots for the {cli.FLOOR_DAYS}-day floor, not 3"
    ]


def test_a_table_nothing_reads_may_expire_sooner(tree):
    edit(tree / TRADES_CONTRACT, set_property("retention", 3))
    assert cli.validate(tree) == []


def test_a_table_less_restricted_than_its_input_fails(tree):
    edit(tree / TRADES_CONTRACT, set_property("classification", "public"))
    assert cli.validate(tree) == [f"{TRADES_PRODUCT}: gold.example_trades is public but reads bronze.example_frames, which is internal"]


def test_a_table_less_restricted_than_its_external_source_fails(tree):
    edit(tree / FRAMES / "product.yaml", lambda d: d["inputs"][0].update(classification="restricted"))
    assert cli.validate(tree) == [f"{FRAMES}/product.yaml: bronze.example_frames is internal but reads synthetic venue feed, which is restricted"]


def test_an_input_no_product_declares_fails(tree):
    edit(tree / TRADES_PRODUCT, lambda d: d["inputs"].append({"table": "gold.example_missing"}))
    assert f"{TRADES_PRODUCT}: input gold.example_missing is not an output of any product" in cli.validate(tree)


def test_an_input_nothing_reads_fails(tree):
    edit(tree / TRADES_CONTRACT, set_property("upstreams", []))
    edit(tree / TRADES_PRODUCT, lambda d: d.update(marts=[]))
    assert cli.validate(tree) == [f"{TRADES_PRODUCT}: input bronze.example_frames is read by none of its outputs"]


def test_a_contract_outside_the_standard_fails(tree):
    edit(tree / TRADES_CONTRACT, lambda d: d.pop("apiVersion"))
    errors = cli.validate(tree)
    assert any(e.startswith(f"{TRADES_CONTRACT}: $: 'apiVersion' is a required property") for e in errors), errors


def set_mart(**fields):
    def change(doc):
        doc["marts"][0].update(fields)

    return change


@pytest.mark.parametrize("table", ["market_capture.example_trades.example_trades", "market_analytics.trades.example_trades"])
def test_a_mart_outside_its_domain_catalog_or_product_schema_fails(tree, table):
    # ADR-044: a domain writes only its own catalog, and each product has its own schema.
    edit(tree / TRADES_PRODUCT, set_mart(table=table))
    assert cli.validate(tree) == [
        f"{TRADES_PRODUCT}: mart {table} must live in market_analytics.example_trades, its domain's catalog and its product's schema (ADR-044)"
    ]


def test_a_mart_reading_a_table_its_product_does_not_declare_fails(tree):
    edit(tree / TRADES_PRODUCT, set_mart(reads=["gold.example_trades", "gold.example_other"]))
    assert cli.validate(tree) == [f"{TRADES_PRODUCT}: mart {MART} reads gold.example_other, which is neither one of its outputs nor a resolved input"]


def test_a_mart_declared_twice_fails(tree):
    edit(tree / TRADES_PRODUCT, lambda doc: doc["marts"].append(dict(doc["marts"][0])))
    assert cli.validate(tree) == [f"{TRADES_PRODUCT}: mart {MART} is declared twice"]


def test_a_mart_is_a_table_or_a_view_by_its_contract(tree):
    edit(tree / TRADES_PRODUCT, set_mart(materialized="view"))
    assert cli.validate(tree) == []
    edit(tree / TRADES_PRODUCT, set_mart(materialized="ephemeral"))
    assert [e for e in cli.validate(tree) if e.startswith(f"{TRADES_PRODUCT}: $.marts[0].materialized") and "'ephemeral' is not one of ['table', 'view']" in e]


def test_a_product_may_build_only_marts_but_not_nothing(tree):
    # As ADR-035's delayed product did: a view, and no lake table of its own.
    generate.scaffold(tree, "market-analytics", "example-delayed")
    pdir = tree / "contracts/market-analytics/example-delayed"
    (pdir / "gold.example_delayed.odcs.yaml").unlink()
    view = {"table": "market_analytics.example_delayed.example_delayed", "grain": ["id"], "reads": ["gold.example_trades"], "materialized": "view"}
    edit(pdir / "product.yaml", lambda d: d.update(inputs=[{"table": "gold.example_trades"}], outputs=[], marts=[view]))
    assert cli.validate(tree) == []
    edit(pdir / "product.yaml", lambda d: d.pop("marts"))
    errors = cli.validate(tree)
    where = "contracts/market-analytics/example-delayed/product.yaml"
    assert [e for e in errors if e.startswith(f"{where}: $: ") and "is not valid under any of the given schemas" in e], errors


def test_a_latency_on_a_column_the_table_lacks_fails(tree):
    def change(doc):
        doc["slaProperties"][0]["element"] = "gold.example_trades.loaded_at"

    edit(tree / TRADES_CONTRACT, change)
    assert cli.validate(tree) == [f"{TRADES_CONTRACT}: latency element gold.example_trades.loaded_at is not a column of gold.example_trades"]


def test_a_latency_without_a_unit_fails_the_k2_profile(tree):
    edit(tree / TRADES_CONTRACT, lambda doc: doc["slaProperties"][0].pop("unit"))
    assert [e for e in cli.validate(tree) if e.startswith(f"{TRADES_CONTRACT}: $.slaProperties[0]") and "'unit' is a required property" in e]


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("staging", "the schema dbt keeps every domain's staging models in (dbt/macros/naming.sql)"),
        ("intermediate", "the schema dbt keeps every domain's intermediate models in (dbt/macros/naming.sql)"),
        ("dq", "the schema every domain's dbt run records its test results in (dbt/macros/record_test_results.sql)"),
    ],
)
def test_a_product_named_like_a_platform_schema_is_rejected(bare, name, why):
    # Its marts would share <catalog>.<name> with the domain's staging or intermediate views,
    # or with the test results its dbt runs record (ADR-046).
    generate.scaffold(bare, "market-analytics", name)
    assert cli.validate(bare) == [f"contracts/market-analytics/{name}/product.yaml: product name {name} is {why}"]


# ── paused tables (ADR-050) ────────────────────────────────────────────────


def pause(since="2026-11-26", adr="ADR-050"):
    def change(doc):
        doc["customProperties"].append({"property": "paused", "value": {"since": since, "adr": adr}})

    return change


def test_a_paused_table_validates(tree):
    edit(tree / FRAMES_CONTRACT, pause())
    assert cli.validate(tree) == []


def test_a_paused_table_names_an_adr_that_exists(tree):
    edit(tree / FRAMES_CONTRACT, pause(adr="ADR-999"))
    assert cli.validate(tree) == [f"{FRAMES_CONTRACT}: paused by ADR-999, which has no file in docs/adr/"]


def test_a_paused_table_names_a_real_day(tree):
    edit(tree / FRAMES_CONTRACT, pause(since="2026-02-30"))
    assert cli.validate(tree) == [f"{FRAMES_CONTRACT}: paused since 2026-02-30, which is not a day"]


def test_an_unquoted_day_is_a_date_to_yaml_and_fails_the_profile(tree):
    edit(tree / FRAMES_CONTRACT, pause(since=date(2026, 11, 26)))
    assert yaml.safe_load((tree / FRAMES_CONTRACT).read_text())["customProperties"][-1]["value"]["since"] == date(2026, 11, 26)
    errors = cli.validate(tree)
    assert any(e.startswith(f"{FRAMES_CONTRACT}: customProperties: $.paused.since") for e in errors), errors


def test_a_paused_table_promises_no_freshness(tree):
    # gold.example_trades declares a latency service level, which its mart's dbt source freshness reads.
    edit(tree / TRADES_CONTRACT, pause())
    assert cli.validate(tree) == [f"{TRADES_CONTRACT}: a paused table promises no freshness, so it declares no latency service level"]

