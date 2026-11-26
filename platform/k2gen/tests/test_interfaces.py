"""The interfaces phase 001 froze (platform/k2gen/INTERFACES.md), held to account.

Every schema takes the good fixture set and refuses each bad case; the naming, quoting,
dispatch, parsing and secrets helpers do what the interfaces say; every emitter and check
module the phase names exists with its frozen signature, and any module a step adds is held
to the same signature; the CLI's spine commands reach their modules. Nothing here asserts that
a module is still a stub, so a step that fills its own module edits no line of this file.
"""

import copy
import dataclasses
import hashlib
import inspect
import json
import re
import typing
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

from k2gen import checks, cli, emit, model
from k2gen.model import DocumentSet, Finding, Model

REPO = model.REPO
FIXTURES = Path(__file__).parent / "fixtures" / "v2"
GOOD = FIXTURES / "good"
# Hand-written examples of each release file, as YAML: the fixtures gate reads JSON under
# a fixtures/ path as data.
RELEASE_EXAMPLES = {
    "manifest": "manifest.yaml",
    "images": "images.yaml",
    "provenance": "provenance.yaml",
    "pipeline": "pipeline.yaml",
    "catalog-product": "catalog-product.yaml",
    "catalog-index": "catalog-index.yaml",
    "backup-set": "backup-set.yaml",
    "run-record": "run-record.yaml",
    "storage-plan": "storage-plan.yaml",
    "dags": "dags.yaml",
    "oneshots": "oneshots.yaml",
    "deploy-record": "deploy-record.yaml",
}
CASES = model.parse("cases.yaml", (FIXTURES / "bad" / "schema" / "cases.yaml").read_bytes())["cases"]
# The modules phase 001 names. A step may add one: discovery is a superset, never an equality.
CHECKS = {"docs", "versions", "access", "sql"}
EMITTERS = {"trino_etc", "trino_rules", "trino_identity", "lake", "pipeline", "dbt", "catalog", "api"}
SILVER = "good/contracts/sensor-net/station-readings/silver_readings.odcs.yaml"
BRONZE = "good/contracts/sensor-net/station-readings/bronze_readings.odcs.yaml"
GOLD = "good/contracts/ops-insight/daily-load/gold_daily_load.odcs.yaml"


def good_documents() -> dict[str, tuple[str, Any]]:
    """Every schema-covered document of the good set: path in the set -> (kind, parsed)."""
    found = {}
    for path in sorted(GOOD.rglob("*")):
        rel = path.relative_to(GOOD).as_posix()
        kind = model.document_kind(rel)
        if kind:
            found[rel] = (kind, model.parse(rel, path.read_bytes()))
    return found


def examples() -> dict[str, Any]:
    """Every fixture file by its path under fixtures/v2/, parsed."""
    docs = {f"good/{p}": doc for p, (_, doc) in good_documents().items()}
    docs.update({f"release/{f}": model.parse(f, (FIXTURES / "release" / f).read_bytes()) for f in RELEASE_EXAMPLES.values()})
    return docs


def every_example() -> list[tuple[str, str]]:
    """(kind, path under fixtures/v2/) for every good document and release example."""
    return [(k, f"good/{p}") for p, (k, _) in good_documents().items()] + [(k, f"release/{f}") for k, f in RELEASE_EXAMPLES.items()]


def test_good_set_is_two_products_in_two_domains_one_reading_the_other() -> None:
    kinds = sorted({k for k, _ in good_documents().values()})
    assert kinds == ["contract", "domains", "expectations", "grants", "principals", "product", "roles"]
    products = {doc["id"]: doc for k, doc in good_documents().values() if k == "product"}
    assert sorted(products) == ["ops-insight.daily-load", "sensor-net.station-readings"]
    (port,) = products["ops-insight.daily-load"]["inputPorts"]
    assert port["contractId"] == "sensor-net.station-readings.silver_readings"


def test_every_schema_has_a_good_example_and_a_bad_case() -> None:
    assert sorted({k for k, _ in every_example()}) == sorted(model.SCHEMAS)
    assert sorted({c["kind"] for c in CASES}) == sorted(model.SCHEMAS)


@pytest.mark.parametrize(("kind", "path"), every_example(), ids=lambda v: str(v))
def test_good_example_is_valid(kind: str, path: str) -> None:
    assert model.schema_errors(kind, examples()[path]) == []


def _apply(doc: Any, case: dict[str, Any]) -> Any:
    doc = copy.deepcopy(doc)
    *parents, last = (case.get("remove") or case["set"]).split("/")
    node = doc
    for key in parents:
        node = node[int(key)] if isinstance(node, list) else node[key]
    index: str | int = int(last) if isinstance(node, list) else last
    if "remove" in case:
        del node[index]
    else:
        node[index] = case["value"]
    return doc


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['kind']}:{c.get('remove') or c['set']}")
def test_bad_case_is_refused(case: dict[str, Any]) -> None:
    errors = model.schema_errors(case["kind"], _apply(examples()[case["file"]], case))
    assert any(case["error"] in e for e in errors), errors


def _required(kind: str) -> list[str]:
    name, _, pointer = model.SCHEMAS[kind][-1].partition("#")
    node = json.loads((model.SCHEMA_DIR / name).read_text())
    for key in filter(None, pointer.split("/")):
        node = node[key]
    return list(node.get("required", []))


@pytest.mark.parametrize(("kind", "path"), every_example(), ids=lambda v: str(v))
def test_each_required_field_removed_and_an_unknown_field_added_fail(kind: str, path: str) -> None:
    doc = examples()[path]
    for key in _required(kind):
        assert model.schema_errors(kind, {k: v for k, v in doc.items() if k != key}), f"{path} without {key}"
    assert model.schema_errors(kind, {**doc, "Unexpected": 1}), f"{path} with an unknown field"


def test_fixture_names_follow_the_naming_helpers() -> None:
    docs = examples()
    rows = docs["good/tests/access/expectations.yaml"]["expectations"]
    denied = {r["table"] for r in rows if r.get("denied")}
    assert model.base_relation("sensor-net", "station-readings", "silver_readings") in denied
    assert model.base_relation("ops-insight", "daily-load", "gold_daily_load") in denied
    assert model.lake_relation("station-readings", model.LANDING_FILES) in denied
    read = {r["table"] for r in rows if "visible" in r}
    assert read == {model.view_relation("sensor-net", "station-readings", "silver_readings"),
                    model.view_relation("ops-insight", "daily-load", "gold_daily_load")}
    pipeline = docs["release/pipeline.yaml"]
    assert pipeline["build"] == {
        "engine": "dbt",
        "selector": model.dag_id("station-readings"),
        "writer": model.builder("station-readings"),
        "publisher": model.publisher("station-readings"),
        "outputs": [model.build_relation("sensor-net", "station-readings", "silver_readings")],
    }
    assert pipeline["landing"]["prefix"] == model.landing_prefix("sensor-net", "station-readings")
    # patterns come from the contract's logicalTypeOptions, never from nowhere (finding 9)
    bronze = docs[BRONZE]["schema"][0]["properties"]
    assert pipeline["bronze"][0]["patterns"] == {c["name"]: c["logicalTypeOptions"]["pattern"] for c in bronze if "logicalTypeOptions" in c}
    assert model.LINEAGE_COLUMN in {c["name"] for c in bronze} and model.LINEAGE_COLUMN not in pipeline["bronze"][0]["values"]
    principals = {p["id"] for p in docs["good/contracts/access/principals.yaml"]["principals"]}
    assert {model.builder(p) for p in ("station-readings", "daily-load")} <= principals
    assert {model.publisher(p) for p in ("station-readings", "daily-load")} <= principals
    assert "__base" not in json.dumps(docs["release/catalog-product.yaml"])
    assert docs["release/catalog-product.yaml"]["outputs"][0]["dbtDocs"] == model.dbt_docs("daily-load", "gold_daily_load")
    unit = model.parse("u", (GOOD / "contracts/sensor-net/station-readings/unit_tests.yml").read_bytes())
    assert unit["unit_tests"][0]["model"] == model.dbt_model("station-readings", "silver_readings")
    plan = docs["release/storage-plan.yaml"]
    assert {w["name"] for w in plan["warehouses"]} == {model.LAKE_WAREHOUSE, model.warehouse("sensor-net"), model.warehouse("ops-insight")}
    dags = {d["dagId"]: d for d in docs["release/dags.yaml"]["dags"]}
    assert dags[model.dag_id("station-readings")]["pipeline"] == model.pipeline_path("sensor-net.station-readings")


def test_the_good_expectations_carry_the_gates_rows() -> None:
    rows = examples()["good/tests/access/expectations.yaml"]["expectations"]
    statements = [r for r in rows if "sql" in r]
    assert any(r["principal"] == model.publisher("station-readings") and r["sql"].startswith("INSERT INTO sensor_net.") for r in statements)  # h1.02
    assert any(r["principal"] == "svc.platform" and "CREATE OR REPLACE VIEW" in r["sql"] for r in statements)  # h1.33
    assert any("SET AUTHORIZATION" in r["sql"] for r in statements)  # h1.19
    assert any("table_changes" in r["sql"] for r in statements)  # h1.08
    assert any(r.get("session") == {f"sensor_net.{model.STATISTICS_PROPERTY}": "true"} and r["expect"] == "denied" for r in statements)  # h1.24
    assert any(r["sql"].startswith(f"SET SESSION sensor_net.{model.STATISTICS_PROPERTY}") for r in statements)  # h1.23
    assert {"probe", "drift"} <= {k for r in rows for k in r}


def test_naming_helpers() -> None:
    d, p, t = "fx-reference", "ecb-fx-rates", "gold_eur_rates_daily"
    assert model.fence(p) == "ecb_fx_rates"
    assert model.product_id(d, p) == "fx-reference.ecb-fx-rates"
    assert model.contract_id(d, p, t) == "fx-reference.ecb-fx-rates.gold_eur_rates_daily"
    assert (model.reader_catalog(d), model.build_catalog(d), model.warehouse(d)) == ("fx_reference", "fx_reference_build", "fx_reference")
    assert (model.reader_namespace(p), model.base_namespace(p)) == ("ecb_fx_rates", "ecb_fx_rates__base")
    assert (model.builder(p), model.publisher(p), model.dag_id(p)) == ("dbt_ecb_fx_rates", "pub_ecb_fx_rates", "ecb_fx_rates")
    assert model.group(p, "delayed_reader") == "ecb_fx_rates__delayed_reader"
    assert model.view_relation(d, p, t) == "fx_reference.ecb_fx_rates.gold_eur_rates_daily"
    assert model.view_build_relation(d, p, t) == "fx_reference_build.ecb_fx_rates.gold_eur_rates_daily"
    assert model.base_relation(d, p, t) == "fx_reference.ecb_fx_rates__base.gold_eur_rates_daily"
    assert model.build_relation(d, p, t) == "fx_reference_build.ecb_fx_rates__base.gold_eur_rates_daily"
    assert model.lake_relation(p, "bronze_daily") == "lake.ecb_fx_rates.bronze_daily"
    assert model.landing_key(d, p, "2026-11-26", "ab" * 32, "xml") == f"s3://k2-landing/fx-reference/ecb-fx-rates/2026-11-26/{'ab' * 32}.xml"
    assert model.canary_user("0123456789abcdef" * 4) == "k2_release_0123456789ab"
    assert model.canary_password_file("0123456789abcdef" * 4) == "canary/k2_release_0123456789ab.password"
    assert model.dbt_model(p, t) == "ecb_fx_rates__gold_eur_rates_daily"
    assert model.dbt_docs(p, t) == "dbt-docs/#!/model/model.k2.ecb_fx_rates__gold_eur_rates_daily"
    assert model.pipeline_path("fx-reference.ecb-fx-rates") == "compiled/products/fx-reference.ecb-fx-rates/pipeline.json"
    assert model.view_sql_path(model.view_relation(d, p, t)) == "compiled/trino/views/fx_reference.ecb_fx_rates.gold_eur_rates_daily.sql"
    assert model.ddl_path("spark", "k2", "ecb_fx_rates", "bronze_daily") == "compiled/lake/ddl/spark/k2.ecb_fx_rates.bronze_daily.sql"


def test_dbt_model_names_are_unique_across_products_with_equal_table_names() -> None:
    # finding 4: every venue's trades product repeats table names; one compiled project cannot
    names = {model.dbt_model(p, "trades") for p in ("kraken-trades", "kraken", "coinbase-trades")}
    assert len(names) == 3
    # a fence holds no __, so splitting a dbt name at its first __ gives the product back
    assert model.dbt_model("kraken-trades", "silver_trades").split("__", 1) == ["kraken_trades", "silver_trades"]


def test_model_config_is_one_parser_for_every_check() -> None:
    silver = (GOOD / "contracts/sensor-net/station-readings/models/silver_readings.sql").read_text()
    gold = (GOOD / "contracts/ops-insight/daily-load/models/gold_daily_load.sql").read_text()
    assert model.model_config(silver) == {"materialized": "incremental", "incremental_strategy": "merge"}
    assert model.model_config(gold) == {"materialized": "table"}
    assert model.model_config("select 1") == {}
    assert model.model_config("{{ config(unique_key=['a', 'b'], full_refresh=false, incremental_predicates=[\"x > 1\"]) }}") == {
        "unique_key": ["a", "b"], "full_refresh": False, "incremental_predicates": ["x > 1"]}
    for bad in ("{{ config(materialized=var('m')) }}", "{{ config('table') }}", "{{ config(a=1) }}{{ config(b=2) }}",
                "{{ config(**opts) }}"):
        with pytest.raises(ValueError):
            model.model_config(bad)


# S07's cases (tests/test_checks_sql.py): a Jinja operator is a ValueError, never a SyntaxError, and
# a hook string holding a Jinja call is read whole, since the scan skips quoted strings.
@pytest.mark.parametrize("text", [
    "{{ config(materialized='ta' ~ 'ble') }}",  # was a SyntaxError
    "{{ config(materialized='table') ~ '' }}",
    "{{ config(materialized='table' | upper) }}",
    "{{ config(materialized='table', post_hook='x') ",  # never closed
    "{{ config(materialized='table', post_hook='x'",
    "{{ config(a={[1]: 2}) }}",  # literal_eval's TypeError
])
def test_model_config_refuses_with_a_value_error(text: str) -> None:
    with pytest.raises(ValueError):
        model.model_config(text)


@pytest.mark.parametrize(("text", "hook"), [
    ("{{ config(materialized='table', post_hook=\"{{ var('ddl') }}\") }}", "{{ var('ddl') }}"),
    ("{{ config(materialized='table', post_hook=\"insert into {{ this }} select * from {{ source('a', 'b') }}\") }}",
     "insert into {{ this }} select * from {{ source('a', 'b') }}"),
    ("{{- config(materialized='table', post_hook='select max(x)) }} from y') -}}\nselect 1", "select max(x)) }} from y"),
    ("{{config(materialized='table', post_hook='it\\'s (')}}", "it's ("),
])
def test_model_config_reads_a_hook_holding_jinja_or_brackets(text: str, hook: str) -> None:
    assert model.model_config(text) == {"materialized": "table", "post_hook": hook}


def test_dbt_seed_names_a_seed_as_dbt_model_names_a_table() -> None:
    assert model.dbt_seed("access-canary", "event_offsets.csv") == "access_canary__event_offsets" == model.dbt_seed(
        "access-canary", "seeds/event_offsets.csv") == model.dbt_model("access-canary", "event_offsets")


def test_the_reserved_domain_names_repeat_no_reserved_word() -> None:
    # TRINO_RESERVED is the parser's own list; RESERVED_DOMAIN_NAMES holds only the platform's names
    assert not model.RESERVED_DOMAIN_NAMES.keys() & model.TRINO_RESERVED
    assert not any("reserved word" in why for why in model.RESERVED_DOMAIN_NAMES.values())


def _table(contract_id: str, kind: model.BuildKind) -> model.Table:
    return model.Table(path="x", contract_id=contract_id, name=contract_id.rsplit(".", 1)[1], version="1.0.0",
                       physical_name="p.t", purpose="p", owner="o", layer="silver", build=model.Build(kind, None),
                       backup="rebuild", retention=None, partitioning=(), sort=(), columns=(), roles=(), policies={},
                       quality=(), sla=())


@pytest.mark.parametrize(
    ("kind", "reader", "input_", "ref"),
    [
        ("connector", "lake.station_readings.bronze_readings", "lake.station_readings.bronze_readings", "main"),
        ("dbt", "sensor_net.station_readings.bronze_readings", "sensor_net.station_readings__base.bronze_readings", "audited"),
        ("code", "sensor_net.station_readings.bronze_readings", "sensor_net.station_readings__base.bronze_readings", "audited"),
        ("platform", "sensor_net.station_readings.bronze_readings", "sensor_net.station_readings.bronze_readings", "main"),
    ],
)
def test_relations_dispatch_by_build_kind(kind: model.BuildKind, reader: str, input_: str, ref: str) -> None:
    t = _table("sensor-net.station-readings.bronze_readings", kind)
    assert (model.reader_relation(t), model.input_relation(t), model.tested_ref(t)) == (reader, input_, ref)


def test_a_serving_table_has_no_lake_relation() -> None:
    t = _table("market-analytics.hot-gold.bars", "serving")
    for helper in (model.reader_relation, model.input_relation, model.tested_ref):
        with pytest.raises(ValueError, match="LAKE_BUILDS"):
            helper(t)


def test_a_serving_only_paused_product_is_expressible() -> None:
    # finding 14: the paused hot tier has no lake table, only its serving copy
    docs = examples()
    product = copy.deepcopy(docs["good/contracts/ops-insight/daily-load/product.odps.yaml"])
    product.update(status="paused", id="ops-insight.hot-load", name="hot-load")
    del product["inputPorts"]
    port = product["outputPorts"][0]
    del port["inputContracts"]
    port["customProperties"] = [{"property": "serves", "value": {"engine": "clickhouse", "profile": "hot-gold", "status": "paused"}}]
    product["customProperties"] = [{"property": "lifecycleChange", "value": {"since": "2026-11-26", "reason": "Paused with the hot tier.", "decision": "ADR-057"}}]
    contract = copy.deepcopy(docs[GOLD])
    contract["customProperties"][1]["value"] = "serving"
    contract["schema"][0]["physicalName"] = "gold.gold_daily_load"
    del contract["schema"][0]["properties"][1]["relationships"]
    assert model.schema_errors("product", product) == []
    assert model.schema_errors("contract", contract) == []


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("station-readings", None),
        ("a1-b2", None),
        ("station--readings", "single hyphens"),  # its fence would hold __, the base separator
        ("station-", "single hyphens"),
        ("Station", "single hyphens"),
        ("station_readings", "single hyphens"),
        ("a" * 41, "at most 40"),
        ("bronze", "reserved"),
        ("runs", "reserved"),
    ],
)
def test_product_name_problem(name: str, problem: str | None) -> None:
    found = model.product_name_problem(name)
    assert found is None if problem is None else problem in (found or "")


@pytest.mark.parametrize(
    ("name", "problem"),
    [("fx-reference", None), ("fx-build", "writer catalog of domain fx"), ("lake", "read-only catalog"), ("access", "contracts/access/"), ("a--b", "single hyphens")],
)
def test_domain_name_problem(name: str, problem: str | None) -> None:
    found = model.domain_name_problem(name)
    assert found is None if problem is None else problem in (found or "")


def _defs(name: str) -> dict[str, Any]:
    return json.loads((model.SCHEMA_DIR / "k2-v2.schema.json").read_text())["$defs"][name]


def test_the_schema_and_the_model_hold_one_grammar() -> None:
    assert _defs("column")["properties"]["physicalType"]["pattern"] == model.PHYSICAL_TYPE
    assert sorted(_defs("identifier")["not"]["enum"]) == sorted(model.TRINO_RESERVED)
    assert _defs("identifier")["maxLength"] == model.MAX_IDENTIFIER and _defs("slug")["maxLength"] == model.MAX_SLUG
    assert _defs("template")["pattern"] == model.TEMPLATE
    assert _defs("tableName")["allOf"][1]["pattern"] == f"^{model.TABLE_NAME.pattern}$"
    values = _defs("rolePolicy")["properties"]["rowScope"]["properties"]["values"]["items"]["oneOf"][1]
    assert values["pattern"] == model.ROW_SCOPE_VALUE
    # every name the platform derives from a slug fits under the identifier limit
    longest = "a" * model.MAX_SLUG
    assert max(len(n) for n in (model.base_namespace(longest), model.builder(longest), model.publisher(longest),
                                model.build_catalog(longest))) <= model.MAX_IDENTIFIER


@pytest.mark.parametrize(
    ("physical_type", "ok"),
    [("varchar", True), ("timestamp(6) with time zone", True), ("decimal(12,3)", True), ("array(varchar)", True),
     ("map(varchar, bigint)", True), ("decimal(3,12)", False), ("varchar, injected bigint", False),
     ("varchar(10)", False), ("timestamp(3)", False), ("bigint)", False), ("row(a bigint)", False)],
)
def test_physical_type_problem(physical_type: str, ok: bool) -> None:
    assert (model.physical_type_problem(physical_type) is None) is ok


# The review's five attack strings (finding 10), each refused where it enters, and each made
# harmless by the quoting helpers in case one gets past.
ATTACKS = {
    "a reserved word as a column": ("current_user", "column"),
    "a rowScope value that closes the literal": ("north') OR (1=1", "value"),
    "a physical type with a second column": ("varchar, injected bigint", "type"),
    "a table named like a base": ("x__base", "table"),
    "a 300-character name": ("a" * 300, "column"),
}


@pytest.mark.parametrize("attack", sorted(ATTACKS))
def test_each_attack_string_is_refused_by_the_schema(attack: str) -> None:
    text, where = ATTACKS[attack]
    doc = copy.deepcopy(examples()[SILVER])
    if where == "column":
        doc["schema"][0]["properties"][3]["name"] = text
    elif where == "value":
        doc["customProperties"][6]["value"]["delayed_reader"]["rowScope"]["values"] = [text]
    elif where == "type":
        doc["schema"][0]["properties"][0]["physicalType"] = text
        assert model.physical_type_problem(text)
    else:
        doc["name"] = doc["schema"][0]["name"] = doc["schema"][0]["id"] = text
    assert model.schema_errors("contract", doc), attack


def test_quoting_makes_every_attack_string_one_token() -> None:
    assert model.quote_ident("current_user") == '"current_user"'
    assert model.quote_ident('a"b') == '"a""b"'
    assert model.quote_relation("sensor_net.station_readings.silver_readings") == '"sensor_net"."station_readings"."silver_readings"'
    literal = model.sql_literal("north') OR (1=1")
    assert literal == "'north'') OR (1=1'" and re.fullmatch(r"'(?:[^']|'')*'", literal)
    assert model.sql_literal(7) == "7"
    for bad in (True, 1.5, "a\nb", "\x00"):
        with pytest.raises(ValueError):
            model.sql_literal(bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        model.quote_ident("")


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (b"r:\n  mask: [a]\nr: {}\n", "appears twice"),  # the review's case: safe_load keeps the last r
        (b"policies:\n  delayed_reader:\n    mask: [note]\n    mask: []\n", "appears twice"),
        (b"a: &x [note]\nb: *x\n", "anchor or alias"),
        (b"a: {<<: {b: 1}}\n", "merge key"),
        (b"a: 1\n---\nb: 2\n", "single document"),
        (b"a: [1\n", "expected"),
    ],
)
def test_the_loader_refuses_text_that_reads_two_ways(text: bytes, why: str) -> None:
    with pytest.raises(model.DocumentError, match=why) as refused:
        model.parse("contracts/x/y/silver.odcs.yaml", text)
    assert str(refused.value).startswith("contracts/x/y/silver.odcs.yaml: ")


def test_safe_load_alone_drops_the_first_of_two_keys() -> None:
    # why the loader exists: a reviewer reads a mask that safe_load silently discards
    assert yaml.safe_load(b"r:\n  mask: [a]\nr: {}\n") == {"r": {}}


def test_the_loader_reads_every_good_document_as_safe_load_does() -> None:
    for path in sorted(GOOD.rglob("*.y*ml")):
        assert model.parse(path.name, path.read_bytes()) == yaml.safe_load(path.read_bytes()), path


def test_set_sha256_is_what_sha256sum_prints_hashed() -> None:
    docs = {"b.yaml": b"two\n", "a.yaml": b"one\n"}
    lines = "".join(f"{hashlib.sha256(docs[p]).hexdigest()}  {p}\n" for p in ("a.yaml", "b.yaml"))
    assert model.set_sha256(docs) == hashlib.sha256(lines.encode()).hexdigest()
    assert model.set_sha256(docs) != model.set_sha256({**docs, "a.yaml": b"one!\n"})


def test_release_id_is_the_tag_and_twelve_hex_of_files_and_images() -> None:
    files, images = {"b": "2" * 64, "a": "1" * 64}, {"trino": "trinodb/trino@sha256:" + "f" * 64}
    rid = model.release_id("v3.0.0-alpha.3", files, images)
    canonical = json.dumps({"files": files, "images": images}, sort_keys=True, separators=(",", ":"))
    assert rid == "v3.0.0-alpha.3-" + hashlib.sha256(canonical.encode()).hexdigest()[:12]
    assert rid == model.release_id("v3.0.0-alpha.3", dict(reversed(files.items())), images)  # order-free
    assert rid != model.release_id("v3.0.0-alpha.3", {**files, "a": "3" * 64}, images)
    assert model.schema_errors("manifest", {**examples()["release/manifest.yaml"], "release": rid}) == []


@pytest.mark.parametrize(
    ("path", "need"),
    [
        (model.RELEASE_COMPOSE, "recreate"),
        ("compiled/trino/etc/catalog/sensor_net.properties", "trinoRestart"),
        ("compiled/trino/etc/password-authenticator.properties", "trinoRestart"),
        ("compiled/trino/rules.json", None),
        ("compiled/trino/views/sensor_net.station_readings.silver_readings.sql", None),
        (model.DAGS, "dagShapeChange"),
        (model.ddl_path("spark", "k2", "station_readings", "bronze_readings"), "ddl"),
        (model.pipeline_path("sensor-net.station-readings"), None),
        ("documents/contracts/domains.yaml", None),
        (model.PROVENANCE, None),
    ],
)
def test_need_of_maps_release_paths_to_needs(path: str, need: str | None) -> None:
    assert model.need_of(path) == need


def test_need_of_refuses_an_unclassified_path() -> None:
    for path in ("notes.txt", "compose/other.yml", "compiledx/a"):
        with pytest.raises(KeyError):
            model.need_of(path)
    assert {model.need_of(p) for p in examples()["release/manifest.yaml"]["files"]} <= {None, "recreate", "dagShapeChange", "ddl", "trinoRestart"}


def test_the_secrets_table_is_one_table_in_code_and_both_maps() -> None:
    paths = [s.path for s in model.SECRET_FILES]
    assert len(paths) == len(set(paths))
    assert {"trino/password.db", "api/keys.json", "alertmanager/slack-webhook-url", "tls/ca.pem", "tls/trino.pem"} <= set(paths)
    for secret in model.SECRET_FILES:
        assert not secret.path.startswith(("compiled/", "documents/", "/")), secret
        assert secret.mount is None or secret.mount.startswith(model.SECRETS_MOUNT + "/"), secret
        assert bool(secret.mount) == bool(secret.services), secret
        assert re.search(r"\bS(09|13|14|19)\b", secret.writer), secret
    mounts = [s.mount for s in model.SECRET_FILES if s.mount]
    assert len(mounts) == len(set(mounts))
    maps = [REPO / "platform/k2gen/INTERFACES.md", REPO / "docs/development/phase-001-interfaces.md"]
    for doc in maps:
        text = doc.read_text()
        assert all(f"`{p}`" in text for p in paths), doc
        assert all(f"`{m}`" in text for m in mounts), doc
    assert model.password_file("svc.runs") == "principals/svc.runs.password"
    assert (model.password_mount("dbt_station_readings"), model.password_mount("pub_station_readings"), model.password_mount("svc.runs")) == (
        "/run/k2-secrets/builder.password", "/run/k2-secrets/publisher.password", "/run/k2-secrets/runs.password")
    with pytest.raises(ValueError):
        model.password_mount("p.analyst-full")


def test_no_release_file_has_a_place_for_a_secret() -> None:
    manifest = json.loads((model.SCHEMA_DIR / "manifest.schema.json").read_text())
    assert "secrets" not in manifest["properties"]
    assert "secrets" not in {f.name for f in dataclasses.fields(model.Manifest)}


def test_the_platform_tables_are_well_formed() -> None:
    assert set(model.PLATFORM_TABLES) == model.RESERVED_TABLE_NAMES
    for name, table in model.PLATFORM_TABLES.items():
        names = [c.name for c in table.columns]
        assert table.name == name and len(names) == len(set(names))
        for column in table.columns:
            assert model.IDENTIFIER.fullmatch(column.name) and column.name not in model.TRINO_RESERVED, column
            assert model.physical_type_problem(column.type) is None, column
            assert column.comment, column
        assert all(re.fullmatch(r"day\((\w+)\)", p) and re.fullmatch(r"day\((\w+)\)", p).group(1) in names for p in table.partitioning)  # type: ignore[union-attr]


def test_the_gate_amendments_are_in_the_principal_lists() -> None:
    def matches(patterns: tuple[str, ...], user: str) -> bool:
        return any(re.fullmatch(p, user) for p in patterns)

    assert matches(model.READER_CATALOG_ALL, "pub_station_readings") and not matches(model.READER_CATALOG_ALL, "dbt_station_readings")
    assert not matches(model.READER_NAMESPACE_OWNERS, "svc.platform")
    assert not matches(model.PIPELINE_PRINCIPALS, "svc.api") and not matches(model.PIPELINE_PRINCIPALS, "p.analyst-full")
    assert matches(model.RETENTION_PROPERTY_PRINCIPALS, "svc.platform") and not matches(model.RETENTION_PROPERTY_PRINCIPALS, "pub_x")
    assert "svc.api" in model.REFS_READERS


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("contracts/domains.yaml", "domains"),
        ("contracts/access/roles.yaml", "roles"),
        ("contracts/access/principals.yaml", "principals"),
        ("tests/access/expectations.yaml", "expectations"),
        ("contracts/fx-reference/ecb-fx-rates/product.odps.yaml", "product"),
        ("contracts/fx-reference/ecb-fx-rates/silver_rates.odcs.yaml", "contract"),
        ("contracts/fx-reference/ecb-fx-rates/grants.yaml", "grants"),
        ("contracts/fx-reference/ecb-fx-rates/models/silver_rates.sql", None),
        ("contracts/fx-reference/ecb-fx-rates/unit_tests.yml", None),
        ("contracts/schema/k2-v2.schema.json", None),
        ("domains/fx-reference/parsers/ecb_fx.py", None),
        (model.COMPOSE, None),
    ],
)
def test_document_kind(path: str, kind: str | None) -> None:
    assert model.document_kind(path) == kind


def test_the_compose_file_is_a_document_root() -> None:
    assert model.COMPOSE in model.DOCUMENT_ROOTS and (REPO / model.COMPOSE).is_file()


def _short(module: ModuleType) -> str:
    return module.__name__.rsplit(".", 1)[1]


def test_every_check_module_is_found_with_its_frozen_signature() -> None:
    found = [_short(m) for m in checks.modules()]
    assert CHECKS <= set(found) and found[0] == checks.FIRST
    for module in checks.modules():
        assert list(inspect.signature(module.check).parameters) == ["docs", "previous"], module
        assert typing.get_type_hints(module.check) == {"docs": DocumentSet, "previous": DocumentSet | None, "return": list[Finding]}, module


def test_every_emitter_module_is_found_with_its_frozen_signature() -> None:
    # An emitter takes the model and nothing else: no previous release, so one set gives one id.
    assert EMITTERS <= {_short(m) for m in emit.modules()}
    for module in emit.modules():
        assert list(inspect.signature(module.emit).parameters) == ["model"], module
        assert typing.get_type_hints(module.emit) == {"model": Model, "return": dict[str, str]}, module


def _finding(severity: str, pointer: str | None = None) -> Finding:
    return Finding(code="x.y", severity=severity, path="p", message="m", pointer=pointer)  # type: ignore[arg-type]


def test_a_finding_s_pointer_is_optional_and_printed_by_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from k2gen import docset

    assert Finding("x.y", "error", "p", "m").pointer is None
    monkeypatch.setattr(docset, "read", lambda path: None)
    monkeypatch.setattr(checks, "run", lambda docs, previous: [_finding("error", "/schema/0/quality/1"), _finding("warning")])
    assert cli.main(["check", "--set", "set", "--previous", "none"]) == 1
    assert capsys.readouterr().err.splitlines() == ["error x.y p /schema/0/quality/1: m", "warning x.y p: m"]


def test_checks_run_stops_after_docs_reports_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def fake(name: str, severity: str | None) -> SimpleNamespace:
        def check(docs: object, previous: object) -> list[Finding]:
            ran.append(name)
            return [_finding(severity)] if severity else []

        return SimpleNamespace(__name__=f"k2gen.checks.{name}", check=check)

    monkeypatch.setattr(checks, "modules", lambda: [fake("docs", "warning"), fake("sql", "error")])
    assert len(checks.run(None, None)) == 2 and ran == ["docs", "sql"]  # type: ignore[arg-type]
    ran.clear()
    monkeypatch.setattr(checks, "modules", lambda: [fake("docs", "error"), fake("sql", None)])
    assert [f.severity for f in checks.run(None, None)] == ["error"] and ran == ["docs"]  # type: ignore[arg-type]


def _emitter(name: str, out: dict[str, str] | None) -> SimpleNamespace:
    def emit_(model: object) -> dict[str, str]:
        if out is None:
            raise NotImplementedError("S99")
        return out

    return SimpleNamespace(__name__=f"k2gen.emit.{name}", emit=emit_)


def test_emit_run_skips_a_stub_aloud_and_merges_the_rest(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(emit, "modules", lambda: [_emitter("a", {"compiled/a.json": "1"}), _emitter("b", None), _emitter("c", {"compiled/c/x": "2"})])
    assert emit.run(None) == {"compiled/a.json": "1", "compiled/c/x": "2"}  # type: ignore[arg-type]
    assert "SKIP emitter b: a stub until step S99" in capsys.readouterr().err


@pytest.mark.parametrize(("first", "second", "why"), [("compiled/x", "compiled/x", "emitted by both"), ("compiled/x", "manifest.json", "not a path under compiled/"),
                                                       ("compiled/x", "compiled/../manifest.json", "not a path under compiled/")])
def test_emit_run_refuses_a_shared_or_stray_path(monkeypatch: pytest.MonkeyPatch, first: str, second: str, why: str) -> None:
    monkeypatch.setattr(emit, "modules", lambda: [_emitter("a", {first: "1"}), _emitter("b", {second: "2"})])
    with pytest.raises(ValueError, match=why):
        emit.run(None)  # type: ignore[arg-type]


def test_cli_lists_the_spine_commands_and_the_old_ones() -> None:
    sub = next(a for a in cli.parser()._actions if a.dest == "command")
    assert sorted(sub.choices) == ["check", "diff", "docset", "generate", "new", "release", "validate"]


def test_cli_spine_commands_reach_their_modules(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from k2gen import diff, docset, release

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(docset, "load", lambda tag, repo: calls.append(("load", tag, repo)) or "SET")
    monkeypatch.setattr(docset, "write", lambda docs, out: calls.append(("write", docs, out)))
    monkeypatch.setattr(docset, "read", lambda path: calls.append(("read", path)) or f"READ {path}")
    monkeypatch.setattr(checks, "run", lambda docs, previous: calls.append(("check", docs, previous)) or [_finding("warning")])
    monkeypatch.setattr(release, "release", lambda *a: calls.append(("release", *a)) or "v3.0.0-0123456789ab")
    monkeypatch.setattr(diff, "diff", lambda old, new: calls.append(("diff", old, new)) or "| diff |")

    assert cli.main(["docset", "--tag", "v3.0.0-alpha.3", "--out", "out", "--repo", "repo"]) == 0
    assert calls.pop() == ("write", "SET", Path("out")) and calls.pop() == ("load", "v3.0.0-alpha.3", Path("repo"))
    assert cli.main(["check", "--set", "set", "--previous", "none"]) == 0  # a warning is not an error
    assert calls == [("read", Path("set")), ("check", "READ set", None)]
    calls.clear()
    monkeypatch.setattr(checks, "run", lambda docs, previous: [_finding("error")])
    assert cli.main(["check", "--set", "set", "--previous", "old"]) == 1
    calls.clear()
    assert cli.main(["release", "--set", "set", "--previous", "none", "--images", "i.json", "--out", "releases"]) == 0
    assert calls.pop() == ("release", Path("set"), None, Path("i.json"), Path("releases"), Path("set") / model.COMPOSE)
    assert cli.main(["release", "--set", "set", "--previous", "prev", "--images", "i.json", "--out", "r", "--compose", "c.yml"]) == 0
    assert calls.pop() == ("release", Path("set"), Path("prev"), Path("i.json"), Path("r"), Path("c.yml"))
    assert cli.main(["diff", "old", "new"]) == 0 and calls.pop() == ("diff", Path("old"), Path("new"))
    assert "v3.0.0-0123456789ab" in capsys.readouterr().out
