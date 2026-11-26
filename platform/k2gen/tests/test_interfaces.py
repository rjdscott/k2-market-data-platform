"""The interfaces phase 001 froze (platform/k2gen/INTERFACES.md), held to account.

Every schema takes the good fixture set and refuses each bad case; the naming helpers name
what the proposal names; every emitter and check module exists with its frozen signature
and its stub names the step that fills it; the CLI's spine commands reach those stubs.
"""

import copy
import hashlib
import inspect
import json
import typing
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

from k2gen import checks, cli, emit, model
from k2gen.model import DocumentSet, Finding, Model, Release

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
}
CASES = yaml.safe_load((FIXTURES / "bad" / "schema" / "cases.yaml").read_text())["cases"]
CHECKS = {"docs": "S05", "versions": "S06", "access": "S07", "sql": "S07"}
EMITTERS = {
    "trino_etc": "S11",
    "trino_rules": "S12",
    "trino_identity": "S13",
    "lake": "S15",
    "pipeline": "S16",
    "dbt": "S17",
    "catalog": "S18",
    "api": "S14",
}


def good_documents() -> dict[str, tuple[str, Any]]:
    """Every schema-covered document of the good set: path in the set -> (kind, parsed)."""
    found = {}
    for path in sorted(GOOD.rglob("*")):
        rel = path.relative_to(GOOD).as_posix()
        kind = model.document_kind(rel)
        if kind:
            found[rel] = (kind, yaml.safe_load(path.read_text()))
    return found


def examples() -> dict[str, Any]:
    """Every fixture file by its path under fixtures/v2/, parsed."""
    docs = {f"good/{p}": doc for p, (_, doc) in good_documents().items()}
    docs.update({f"release/{f}": yaml.safe_load((FIXTURES / "release" / f).read_text()) for f in RELEASE_EXAMPLES.values()})
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
    principals = {p["id"] for p in docs["good/contracts/access/principals.yaml"]["principals"]}
    assert {model.builder(p) for p in ("station-readings", "daily-load")} <= principals
    assert {model.publisher(p) for p in ("station-readings", "daily-load")} <= principals
    assert "__base" not in json.dumps(docs["release/catalog-product.yaml"])


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


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("station-readings", None),
        ("a1-b2", None),
        ("station--readings", "single hyphens"),  # its fence would hold __, the base separator
        ("station-", "single hyphens"),
        ("Station", "single hyphens"),
        ("station_readings", "single hyphens"),
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
    ],
)
def test_document_kind(path: str, kind: str | None) -> None:
    assert model.document_kind(path) == kind


def _short(module: ModuleType) -> str:
    return module.__name__.rsplit(".", 1)[1]


def test_every_check_module_is_found_with_its_frozen_signature() -> None:
    assert [_short(m) for m in checks.modules()] == ["docs", "access", "sql", "versions"]  # docs first
    for module in checks.modules():
        assert list(inspect.signature(module.check).parameters) == ["docs", "previous"]
        assert typing.get_type_hints(module.check) == {"docs": DocumentSet, "previous": DocumentSet | None, "return": list[Finding]}
        with pytest.raises(NotImplementedError, match=CHECKS[_short(module)]):
            module.check(None, None)


def test_every_emitter_module_is_found_with_its_frozen_signature() -> None:
    assert [_short(m) for m in emit.modules()] == sorted(EMITTERS)
    for module in emit.modules():
        assert list(inspect.signature(module.emit).parameters) == ["model", "previous"]
        assert typing.get_type_hints(module.emit) == {"model": Model, "previous": Release | None, "return": dict[str, str]}
        with pytest.raises(NotImplementedError, match=EMITTERS[_short(module)]):
            module.emit(None, None)


def _finding(severity: str) -> Finding:
    return Finding(code="x.y", severity=severity, path="p", message="m")


def test_checks_run_stops_after_docs_reports_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def fake(name: str, severity: str | None) -> SimpleNamespace:
        def check(docs: object, previous: object) -> list[Finding]:
            ran.append(name)
            return [_finding(severity)] if severity else []

        return SimpleNamespace(__name__=f"k2gen.checks.{name}", check=check)

    monkeypatch.setattr(checks, "modules", lambda: [fake("docs", "warning"), fake("sql", "error")])
    assert len(checks.run(None, None)) == 2 and ran == ["docs", "sql"]
    ran.clear()
    monkeypatch.setattr(checks, "modules", lambda: [fake("docs", "error"), fake("sql", None)])
    assert [f.severity for f in checks.run(None, None)] == ["error"] and ran == ["docs"]


def test_cli_lists_the_spine_commands_and_the_old_ones() -> None:
    sub = next(a for a in cli.parser()._actions if a.dest == "command")
    assert sorted(sub.choices) == ["check", "diff", "docset", "generate", "new", "release", "validate"]


@pytest.mark.parametrize(
    ("argv", "step"),
    [
        (["docset", "--tag", "v3.0.0-alpha.3", "--out", "out"], "S04"),
        (["check", "--set", "set", "--previous", "none"], "S04"),
        (["release", "--set", "set", "--previous", "none", "--images", "images.json", "--out", "releases"], "S08"),
        (["diff", "old", "new"], "S08"),
    ],
)
def test_cli_spine_commands_reach_their_stubs(argv: list[str], step: str) -> None:
    with pytest.raises(NotImplementedError, match=step):
        cli.main(argv)


def test_model_load_is_s04s() -> None:
    with pytest.raises(NotImplementedError, match="S04"):
        model.load(None)
