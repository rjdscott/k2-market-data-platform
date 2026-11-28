"""k2gen.checks.access (step S07): the good set is clean, and each failing fixture of
tests/fixtures/v2/bad/access/cases.yaml yields exactly its code."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import checks, model
from k2gen.checks import access

FIXTURES = Path(__file__).parent / "fixtures" / "v2"
GOOD = FIXTURES / "good"
CASES = model.parse("cases.yaml", (FIXTURES / "bad" / "access" / "cases.yaml").read_bytes())["cases"]
CODES = {"principal-duplicate", "principal-collides", "role-undeclared", "policy-role-not-offered", "policy-column-unknown",
         "role-requirement-unmet", "grant-unknown-principal", "grant-unknown-role", "grant-role-not-offered", "grant-two-roles",
         "grant-to-platform", "inherit-role", "inherit-delay", "inherit-mask", "inherit-row-scope", "inherit-undeclared",
         "expectation-missing", "expectation-weak", "expectation-unknown-principal", "delay-type", "row-scope-unpartitioned",
         "connector-role", "input-undeclared", "role-within-invalid"}
SILVER = "contracts/sensor-net/station-readings/silver_readings.odcs.yaml"
GOLD = "contracts/ops-insight/daily-load/gold_daily_load.odcs.yaml"


def good() -> dict[str, bytes]:
    return {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in GOOD.rglob("*") if p.is_file()}


def docset(documents: dict[str, bytes]) -> model.DocumentSet:
    provenance = model.Provenance("git", "fixture", "0" * 40, "fixture", "2026-01-01T00:00:00Z", model.set_sha256(documents), ())
    return model.DocumentSet(documents, provenance)


def change(doc: Any, step: dict[str, Any]) -> None:
    op = next(k for k in ("set", "append", "remove") if k in step)
    *parents, last = step[op].split("/")
    node = doc
    for key in parents:
        node = node[int(key) if isinstance(node, list) else key]
    key = int(last) if isinstance(node, list) else last
    if op == "set":
        node[key] = copy.deepcopy(step["value"])
    elif op == "append":
        node[key].append(copy.deepcopy(step["value"]))
    else:
        del node[key]


def changed(steps: list[dict[str, Any]], documents: dict[str, bytes] | None = None) -> dict[str, bytes]:
    documents = good() if documents is None else dict(documents)
    parsed: dict[str, Any] = {}
    for step in steps:
        if "text" in step:  # a model's SQL, written whole
            documents[step["file"]] = step["text"].encode()
            continue
        doc = parsed.setdefault(step["file"], model.parse(step["file"], documents[step["file"]]))
        change(doc, step)
    for path, doc in parsed.items():
        assert model.schema_errors(model.document_kind(path), doc) == [], f"{path} no longer fits its schema"
        documents[path] = yaml.safe_dump(doc, sort_keys=False).encode()
    return documents


def codes(documents: dict[str, bytes]) -> set[str]:
    return {f.code for f in access.check(docset(documents), None)}


def test_the_good_set_is_clean() -> None:
    assert access.check(docset(good()), None) == []


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_each_failing_fixture_yields_exactly_its_code(case: dict[str, Any]) -> None:
    found = access.check(docset(changed(case["changes"])), None)
    assert {f.code for f in found} == {case["code"]}, [f.message for f in found]
    assert all(f.severity == "error" and f.message for f in found)


@pytest.mark.parametrize("row", [
    {"principal": "p.ghost", "table": "sensor_net.station_readings__base.silver_readings", "denied": True},
    {"principal": "p.ghost", "sql": "SELECT 1", "expect": "denied"}], ids=["table", "statement"])
def test_an_undeclared_principal_may_be_denied(row: dict[str, Any]) -> None:
    assert "access.expectation-unknown-principal" not in codes(changed(
        [{"file": "tests/access/expectations.yaml", "append": "expectations", "value": row}]))


def test_every_code_has_a_failing_fixture() -> None:
    assert {c["code"] for c in CASES} == {f"access.{c}" for c in CODES}


def test_a_deprecated_product_takes_no_new_grant() -> None:
    """ADR-055 part 4 against the previous release: a grant the previous release held stays; a new
    principal, or a held principal under another role, is refused; an active product takes both."""
    product, grants = "contracts/sensor-net/station-readings/product.odps.yaml", "contracts/sensor-net/station-readings/grants.yaml"
    previous = docset(good())
    deprecated = changed([
        {"file": product, "set": "status", "value": "deprecated"},
        {"file": product, "append": "customProperties", "value": {"property": "lifecycleChange", "value": {
            "since": "2026-11-01", "reason": "Replaced by a made-up successor.", "decision": "ADR-055",
            "sunset": "2027-01-01", "successor": "sensor-net.station-readings-next"}}}])
    assert access.check(docset(deprecated), previous) == []  # every grant held before
    for new in ({"principal": "p.twin", "role": "full_reader"}, {"principal": "p.analyst-delayed", "role": "full_reader"}):
        docs = dict(deprecated)
        g = model.parse(grants, docs[grants])
        g["grants"] = [x for x in g["grants"] if x["principal"] != new["principal"]] + [new]
        docs[grants] = yaml.safe_dump(g, sort_keys=False).encode()
        found = [f for f in access.check(docset(docs), previous) if f.code == "access.grant-new-on-deprecated"]
        assert [f.path for f in found] == [grants] and new["principal"] in found[0].message, found
    assert not [f for f in access.check(docset(good()), docset(deprecated)) if f.code == "access.grant-new-on-deprecated"]


def _derived(column: int, *sources: str) -> dict[str, Any]:
    return {"file": GOLD, "set": f"schema/0/properties/{column}/transformSourceObjects",
            "value": [f"sensor-net.station-readings.silver_readings.{s}" for s in sources]}


def test_an_output_at_least_as_strict_on_every_dimension_passes() -> None:
    # delay 30 >= 20 on a column derived from read_at; scope [north] within [north, south] on a
    # column derived from region; operator_note reaches no column of the output.
    steps = [
        {"file": SILVER, "set": "customProperties/6/value/delayed_reader",
         "value": {"delay": {"column": "read_at", "minutes": 20}, "mask": ["operator_note"],
                   "rowScope": {"column": "region", "values": ["north", "south"]}}},
        {"file": GOLD, "append": "roles", "value": {"role": "delayed_reader", "access": "read"}},
        {"file": GOLD, "append": "customProperties", "value": {"property": "policies", "value": {"delayed_reader": {
            "delay": {"column": "load_date", "minutes": 30}, "rowScope": {"column": "station_id", "values": ["north"]}}}}},
        {"file": GOLD, "set": "schema/0/properties/0/physicalType", "value": "timestamp(6) with time zone"},
        {"file": GOLD, "append": "customProperties", "value": {"property": "partitioning", "value": ["station_id"]}},
        _derived(0, "read_at"), _derived(1, "station_id", "region"), _derived(2, "station_id"), _derived(3, "value"),
    ]
    assert codes(changed(steps)) == set()


def test_a_masked_input_column_may_reach_the_output_masked() -> None:
    steps = [
        {"file": SILVER, "set": "customProperties/6/value/delayed_reader",
         "value": {"delay": {"column": "read_at", "minutes": 20}, "mask": ["operator_note"]}},
        {"file": GOLD, "append": "roles", "value": {"role": "delayed_reader", "access": "read"}},
        {"file": GOLD, "append": "customProperties", "value": {"property": "policies", "value": {"delayed_reader": {
            "delay": {"column": "load_date", "minutes": 20}, "mask": ["readings"]}}}},
        {"file": GOLD, "set": "schema/0/properties/0/physicalType", "value": "timestamp(6) with time zone"},
        _derived(0, "read_at"), _derived(1, "station_id"), _derived(2, "operator_note"), _derived(3, "value"),
    ]
    assert codes(changed(steps)) == set()


def test_an_output_may_offer_a_role_within_one_its_input_offers() -> None:
    """roles.yaml declares delayed_reader within full_reader: an output may offer it over an input
    offering full_reader alone with no policy (reference data, with no event time to delay), its
    delay on a column of its own. Without the declaration the same documents fail: the order is
    read, never inferred. The other way, and over an input offering nothing, cases.yaml fails."""
    full_only = [{"file": SILVER, "remove": "roles/1"}, {"file": SILVER, "remove": "customProperties/6"},
                 {"file": "contracts/sensor-net/station-readings/grants.yaml", "set": "grants/1/role", "value": "full_reader"},
                 {"file": GOLD, "append": "roles", "value": {"role": "delayed_reader", "access": "read"}},
                 {"file": GOLD, "append": "customProperties", "value": {"property": "policies", "value": {
                     "delayed_reader": {"delay": {"column": "load_date", "minutes": 20}}}}},
                 {"file": GOLD, "set": "schema/0/properties/0/physicalType", "value": "timestamp(6) with time zone"}]
    assert codes(changed(full_only)) == set()
    undeclared = [*full_only, {"file": "contracts/access/roles.yaml", "remove": "roles/delayed_reader/within"}]
    assert codes(changed(undeclared)) == {"access.inherit-role"}


def test_inputs_are_every_input_port_when_the_output_port_lists_none() -> None:
    # without inputContracts the gold table still derives from silver, through the input port
    steps = [{"file": "contracts/ops-insight/daily-load/product.odps.yaml", "remove": "outputPorts/0/inputContracts"},
             {"file": GOLD, "append": "roles", "value": {"role": "public_reader", "access": "read"}}]
    assert codes(changed(steps)) == {"access.inherit-role"}


def test_a_grant_to_each_platform_principal_is_refused() -> None:
    for principal in ("dbt_station_readings", "pub_station_readings", "svc.platform", "svc.catalog", "svc.api"):
        steps = [{"file": "contracts/ops-insight/daily-load/grants.yaml", "append": "grants",
                  "value": {"principal": principal, "role": "full_reader"}}]
        assert codes(changed(steps)) == {"access.grant-to-platform"}, principal


def test_a_missing_row_names_what_to_add() -> None:
    found = access.check(docset(changed([{"file": "tests/access/expectations.yaml", "remove": "expectations/1"}])), None)
    assert len(found) == 1 and found[0].path == model.EXPECTATIONS
    assert "sensor_net.station_readings.silver_readings" in found[0].message and "delayed_reader" in found[0].message


def test_a_sibling_in_input_contracts_is_followed_to_what_it_derives_from() -> None:
    # gold lists its sibling silver, which derives from the input X: gold inherits X's policy
    def port(cid: str, *inputs: str) -> dict[str, Any]:
        return {"contractId": cid, "inputContracts": [{"id": i, "version": "1.0.0"} for i in inputs]}

    doc = {"inputPorts": [{"contractId": "d.other.x"}], "outputPorts": [port("d.p.silver", "d.other.x"), port("d.p.gold", "d.p.silver")]}
    product = access._Product("contracts/d/p", "d", "p", doc, {"s": {"id": "d.p.silver"}, "g": {"id": "d.p.gold"}})
    assert access._inputs(product, {"id": "d.p.gold"}) == ["d.other.x"]
    doc["outputPorts"][0] = port("d.p.silver", "d.p.gold")  # a cycle of siblings ends, deriving from nothing
    assert access._inputs(product, {"id": "d.p.gold"}) == []


DAILY = "contracts/ops-insight/daily-load/product.odps.yaml"
GOLD_SQL = "contracts/ops-insight/daily-load/models/gold_daily_load.sql"
BRONZE_PORT = {"file": DAILY, "append": "inputPorts",
               "value": {"name": "bronze_readings", "version": "1.0.0", "contractId": "sensor-net.station-readings.bronze_readings"}}


def _gold(where: str) -> dict[str, str]:
    return {"file": GOLD_SQL, "text": "{{ config(materialized='table') }}\nselect cast(read_at as date) as load_date, station_id, "
            "count(*) as readings, cast(avg(value) as decimal(12, 3)) as mean_value\nfrom {{ k2_input('station_readings', "
            f"'silver_readings') }}}}\nwhere {where}\ngroup by 1, 2\n"}


def test_a_model_reading_an_input_its_port_omits_is_refused_and_held_to_it() -> None:
    """A1 of the 2026-11-28 review, its exploit: gold's port lists silver alone, and its model reads
    bronze, an input port of its product offering no role, through k2_input. Read from the port's
    list, every inherit-* rule skipped bronze; now it is refused, and full_reader meets bronze's
    floor. Declared as a port and never read, bronze is no finding."""
    assert codes(changed([BRONZE_PORT])) == set()
    exploit = [BRONZE_PORT, _gold("station_id in (select station_id from {{ k2_input('station_readings', 'bronze_readings') }})")]
    found = access.check(docset(changed(exploit)), None)
    assert {f.code for f in found} == {"access.input-undeclared", "access.inherit-role"}, [f.message for f in found]
    (undeclared,) = [f for f in found if f.code == "access.input-undeclared"]
    assert undeclared.path == DAILY
    assert "models/gold_daily_load.sql: k2_input('station_readings', 'bronze_readings')" in undeclared.message


def test_an_expression_k2gen_cannot_read_stands_for_every_input_port() -> None:
    for hidden in ("{% set f = k2_input %}station_id in (select station_id from {{ f('station_readings', 'bronze_readings') }})",
                   "station_id in (select station_id from {{ k2_input('station_' ~ 'readings', 'bronze_readings') }})",
                   "station_id in (select station_id from {{ api.Relation.create(schema='station_readings__base') }})"):
        found = access.check(docset(changed([BRONZE_PORT, _gold(hidden)])), None)
        assert {f.code for f in found} == {"access.input-undeclared", "access.inherit-role"}, hidden
        assert any("which k2gen cannot read" in f.message for f in found), hidden


def test_the_reviewers_exploit_on_the_security_master_is_refused() -> None:
    """instrument offers full_reader; currency offers no role and is an input port of the product,
    not of instrument's output port. Read through k2_input, k2gen check passed it before the fix."""
    from test_repository_documents import repository_set

    path = "contracts/reference-data/security-master/models/instrument.sql"
    repo = repository_set().documents
    assert access.check(docset(dict(repo)), None) == []
    sql = repo[path].decode().replace("  where held_reason is null\n", "  where held_reason is null\n    and base_asset in "
                                      "(select code from {{ k2_input('currency_master', 'currency') }})\n", 1)
    found = access.check(docset({**repo, path: sql.encode()}), None)
    assert sorted(f.code for f in found) == ["access.inherit-role", "access.input-undeclared"], [f.message for f in found]
    assert all(f.message.startswith("reference-data.security-master.instrument") for f in found)


def test_a_sibling_reached_by_ref_passes_its_reads_on() -> None:
    """The same read in venue_symbol_claim reaches every model that refs it: each is refused, and
    the two offering full_reader meet currency's floor; asset declares currency, so it passes."""
    from test_repository_documents import repository_set

    path = "contracts/reference-data/security-master/models/venue_symbol_claim.sql"
    repo = repository_set().documents
    sql = repo[path].decode().replace("  from {{ ref('security_master__code_exceptions') }}\n", "  from {{ ref("
                                      "'security_master__code_exceptions') }}\n  union all\n  select 'kraken', upper(code), "
                                      "upper(code) from {{ k2_input('currency_master', 'currency') }}\n", 1)
    found = access.check(docset({**repo, path: sql.encode()}), None)
    ids = "reference-data.security-master."
    assert sorted((f.code, f.message.split("'s model")[0].split(" offers")[0]) for f in found) == [
        ("access.inherit-role", ids + "instrument"), ("access.inherit-role", ids + "venue_symbol"),
        *(("access.input-undeclared", ids + t) for t in ("instrument", "venue_symbol", "venue_symbol_claim", "venue_symbol_held"))]
    (chain,) = [f.message for f in found if f.code == "access.input-undeclared" and f.message.startswith(ids + "instrument")]
    assert ("models/instrument.sql: ref('security_master__venue_symbol_claim') -> models/venue_symbol_claim.sql: "
            "k2_input('currency_master', 'currency')") in chain


def test_public_reader_within_full_reader_would_offer_internal_data_publicly() -> None:
    """Why A6 forbids the one direction: declared, gold could offer public_reader over silver,
    which offers no public_reader, and only the order check stops it; the other direction, an
    internal role within public_reader, is no finding."""
    steps = [{"file": "contracts/access/roles.yaml", "set": "roles/public_reader/within", "value": ["full_reader"]},
             {"file": GOLD, "append": "roles", "value": {"role": "public_reader", "access": "read"}}]
    assert codes(changed(steps)) == {"access.role-within-invalid"}
    assert codes(changed(steps[1:])) == {"access.inherit-role"}
    assert codes(changed([{"file": "contracts/access/roles.yaml", "set": "roles/full_reader/within", "value": ["public_reader"]}])) == set()


ENRICHED = FIXTURES / "enriched"
EUR = "contracts/market-analytics/enriched-candles/ohlcv_1d_eur.odcs.yaml"
EUR_PRODUCT = "contracts/market-analytics/enriched-candles/product.odps.yaml"


def enriched(*steps: dict[str, Any]) -> list[model.Finding]:
    """Every check (`k2gen check`) over the repository's documents with fixtures/v2/enriched laid
    over them, its coverage rows appended, then `steps`."""
    from test_repository_documents import repository_set

    documents = dict(repository_set().documents)
    documents |= {p.relative_to(ENRICHED).as_posix(): p.read_bytes() for p in ENRICHED.rglob("*") if p.is_file()}
    rows = [{"file": model.EXPECTATIONS, "append": "expectations",
             "value": {"principal": "p.canary-full", "table": table, "denied": True}}
            for table in ("market_analytics.enriched_candles.ohlcv_1d_eur", "market_analytics.enriched_candles__base.ohlcv_1d_eur")]
    return checks.run(docset(changed([*rows, *steps], documents)), None)


def test_the_enriched_fixture_over_three_domains_passes_every_check() -> None:
    """Plan 005's shape: candles (delayed_reader 20 minutes on window_end), the security master's
    type-2 instrument joined as of window_end and the ECB rates, both full_reader only, which
    delayed_reader is within. The output delays delayed_reader 20 minutes on its window_end."""
    assert enriched() == []


def test_the_enriched_fixture_delayed_10_minutes_over_candles_20_fails() -> None:
    """Plan 005's exit: an output policy weaker than its input's fails `k2gen check`."""
    (finding,) = enriched({"file": EUR, "set": "customProperties/3/value/delayed_reader/delay/minutes", "value": 10})
    assert finding.code == "access.inherit-delay" and finding.path == EUR
    assert "market-analytics.candles.ohlcv_1d delays delayed_reader by 20 minutes" in finding.message
    assert "has 10 minutes" in finding.message


def test_the_enriched_fixture_over_an_input_offering_no_role_fails() -> None:
    """Plan 005's exit, the role: the security master's asset offers no role today, and nothing
    stands in for it, so an output reading it offers neither full_reader nor delayed_reader."""
    asset = "reference-data.security-master.asset"
    found = enriched(
        {"file": EUR_PRODUCT, "append": "inputPorts", "value": {"name": "asset", "version": "1.0.0", "contractId": asset,
                                                                "customProperties": [{"property": "required", "value": True},
                                                                                     {"property": "maxLagMinutes", "value": None}]}},
        {"file": EUR_PRODUCT, "append": "outputPorts/0/inputContracts", "value": {"id": asset, "version": "1.0.0"}})
    assert sorted((f.code, f.message.split(" offers ")[1].split(",")[0]) for f in found) == [
        ("access.inherit-role", "delayed_reader"), ("access.inherit-role", "full_reader")], [f.message for f in found]
    assert all(f"its input {asset} offers neither" in f.message for f in found)


def test_the_enriched_fixture_joins_the_type_2_instrument_only_as_of_a_time() -> None:
    """The docs rules on the repository's own reference data: instrument keeps history, so its
    key states its point in time; the ECB table keeps none, so its key states none."""
    (missing,) = enriched({"file": EUR, "remove": "schema/0/properties/1/relationships/0/customProperties"})
    assert missing.code == "docs.relationship-as-of-missing"
    assert "reference-data.security-master.instrument.instrument_id" in missing.message
    (unexpected,) = enriched({"file": EUR, "set": "schema/0/properties/4/relationships/0/customProperties",
                              "value": [{"property": "asOf", "value": "known"}, {"property": "asOfColumn", "value": "window_end"}]})
    assert unexpected.code == "docs.relationship-as-of-unexpected"
