"""scripts/dbt_manifest_check.py on a manifest shaped like dbt's, no dbt and no stack.

make test-dbt runs the check on the real manifest; these make each rule fail once.
"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from dbt_manifest_check import check, contracts  # noqa: E402

MART = "model.k2.ohlcv_1m"
STG = "model.k2.stg_gold__trades"
SRC = "source.k2.gold.trades"
FENCES = {"market_analytics", "reference_data"}
MARTS = {"market_analytics.candles.ohlcv_1m": {"grain": ["exchange", "window_start"], "reads": ["gold.trades"], "product": "p.yaml"}}


def model(fqn, materialized, schema):
    return {
        "resource_type": "model",
        "fqn": ["k2", *fqn],
        "original_file_path": "models/" + "/".join(fqn) + ".sql",
        "database": fqn[0],
        "schema": schema,
        "alias": fqn[-1],
        "config": {"materialized": materialized},
    }


def unique_test(columns, **kwargs):
    return {
        "resource_type": "test",
        "attached_node": MART,
        "config": {},
        "test_metadata": {"name": "expect_compound_columns_to_be_unique", "namespace": "dbt_expectations", "kwargs": {"column_list": columns, **kwargs}},
    }


@pytest.fixture
def manifest():
    return {
        "sources": {SRC: {"database": "lake", "schema": "gold", "name": "trades"}},
        "nodes": {
            STG: model(["market_analytics", "staging", "stg_gold__trades"], "view", "staging"),
            MART: model(["market_analytics", "marts", "candles", "ohlcv_1m"], "table", "candles"),
            "test.k2.grain": unique_test(["window_start", "exchange"]),
        },
        "parent_map": {STG: [SRC], MART: [STG], "test.k2.grain": [MART]},
    }


def test_the_project_as_built_passes(manifest):
    assert check(manifest, FENCES, MARTS) == []


def test_a_mart_selecting_from_a_source_fails(manifest):
    manifest["parent_map"][MART] = [STG, SRC]
    assert check(manifest, FENCES, MARTS) == ["models/market_analytics/marts/candles/ohlcv_1m.sql: selects from a source; only a staging model may, so read its staging view"]


@pytest.mark.parametrize(
    "test",
    [
        None,  # no test at all
        unique_test(["exchange"]),  # not the grain
        unique_test(["exchange", "window_start"], row_condition="window_start > now()"),  # a slice
    ],
)
def test_a_mart_without_a_whole_table_uniqueness_test_on_its_grain_fails(manifest, test):
    if test is None:
        del manifest["nodes"]["test.k2.grain"]
    else:
        manifest["nodes"]["test.k2.grain"] = test
    (error,) = check(manifest, FENCES, MARTS)
    assert "no uniqueness test on its grain ['exchange', 'window_start']" in error


def test_a_core_unique_test_counts_for_a_one_column_grain(manifest):
    marts = copy.deepcopy(MARTS)
    marts["market_analytics.candles.ohlcv_1m"]["grain"] = ["window_start"]
    manifest["nodes"]["test.k2.grain"] = {"resource_type": "test", "attached_node": MART, "config": {}, "test_metadata": {"name": "unique", "kwargs": {"column_name": "window_start"}}}
    assert check(manifest, FENCES, marts) == []


def test_a_mart_is_a_table_or_a_view_and_nothing_else(manifest):
    manifest["nodes"][MART]["config"]["materialized"] = "view"  # ADR-035: the delayed product is one
    assert check(manifest, FENCES, MARTS) == []
    manifest["nodes"][MART]["config"]["materialized"] = "ephemeral"
    assert [e for e in check(manifest, FENCES, MARTS) if "a mart is a table or a view, not ephemeral" in e]


def test_an_undeclared_mart_and_a_declared_mart_with_no_model_both_fail(manifest):
    manifest["nodes"][MART]["alias"] = manifest["nodes"][MART]["fqn"][-1] = "ohlcv_2m"
    errors = check(manifest, FENCES, MARTS)
    assert any("builds market_analytics.candles.ohlcv_2m, which no product.yaml declares" in e for e in errors)
    assert any(e == "p.yaml: mart market_analytics.candles.ohlcv_1m has no model at models/market_analytics/marts/candles/ohlcv_1m.sql" for e in errors)


def test_a_mart_reading_a_table_its_contract_does_not_declare_fails(manifest):
    manifest["sources"]["source.k2.gold.bars"] = {"database": "lake", "schema": "gold", "name": "bars"}
    manifest["parent_map"][STG] = [SRC, "source.k2.gold.bars"]
    assert check(manifest, FENCES, MARTS) == [
        "models/market_analytics/marts/candles/ohlcv_1m.sql: reads ['gold.bars', 'gold.trades'], but p.yaml declares ['gold.trades']"
    ]


def test_a_model_outside_every_domain_catalog_fails(manifest):
    manifest["nodes"][STG]["database"] = "lake"
    assert check(manifest, FENCES, MARTS) == ["models/market_analytics/staging/stg_gold__trades.sql: catalog lake is not a domain's in contracts/domains.yaml"]


def test_the_contracts_declare_the_candles_mart_in_its_domain_catalog():
    fences, marts = contracts()
    assert "market_analytics" in fences
    mart = marts["market_analytics.candles.ohlcv_1m"]
    assert mart["grain"] == ["exchange", "canonical_symbol", "window_start"] and mart["reads"] == ["gold.trades"]
