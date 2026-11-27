"""emit/dbt.py (S17): the compiled dbt tree of the good fixture set and of the repository's products.

Goldens: `golden/dbt/<release path>` for the good set, every file; `golden/dbt/repository/<release
path>` for each real product under contracts/, compared while that product exists: its own files
byte for byte, and its entries in the three shared files (dbt_project.yml, selectors.yml,
models/_sources.yml), which grow with every product. Regenerate both, then review the diff as code:

    cd platform/k2gen && uv run --locked python tests/test_emit_dbt.py [<repository root>]

The dbt proof, dbt parse, compile and ls --selector of each tree with dbt/'s pinned dbt-core and
dbt-trino and no warehouse, runs only with K2_DBT_PROOF=1: `make test-dbt-tree`.
"""

import dataclasses
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from k2gen import model
from k2gen.emit import dbt
from k2gen.model import DocumentSet, Model

GOOD = Path(__file__).parent / "fixtures" / "v2" / "good"
GOLDEN = Path(__file__).parent / "golden" / "dbt"
REPOSITORY = GOLDEN / "repository"
SHARED = ("dbt_project.yml", "selectors.yml", "models/_sources.yml")
PROOF_PORT = 18089  # nothing may listen here: a dbt step that reached for a warehouse fails


def good() -> dict[str, bytes]:
    return {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in sorted(GOOD.rglob("*")) if p.is_file()}


def repository(root: Path = model.REPO) -> dict[str, bytes]:
    """The files under model.DOCUMENT_ROOTS at `root`, as a tag's document set holds them."""
    return dict(dbt._documents(root).documents)


def loaded(documents: dict[str, bytes]) -> Model:
    provenance = model.Provenance("git", "v0.1.0", "0" * 40, "Example Owner <owner@example.org>",
                                  "2026-01-01T00:00:00Z", model.set_sha256(documents), ())
    return model.load(DocumentSet(documents, provenance))


def goldens(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted((root / dbt.ROOT).rglob("*")) if p.is_file()}


def parsed(tree: dict[str, str], path: str) -> Any:
    return model.parse(path, tree[f"{dbt.ROOT}/{path}"].encode())


def product(m: Model, pid: str) -> model.Product:
    (p,) = [p for p in m.products if p.id == pid]
    return p


def replaced(m: Model, pid: str, **changes: Any) -> Model:
    return dataclasses.replace(m, products=tuple(dataclasses.replace(p, **changes) if p.id == pid else p for p in m.products))


def shared_entries(tree: dict[str, str]) -> dict[str, Any]:
    """The three shared files as one entry per product, selector and source table."""
    project = parsed(tree, "dbt_project.yml")
    entries: dict[str, Any] = {}
    for kind in ("models", "seeds"):
        for domain, products in project[kind][model.DBT_PROJECT].items():
            if not domain.startswith("+"):
                entries |= {f"{kind} {domain}/{p}": route for p, route in products.items()}
    entries |= {f"selector {s['name']}": s for s in parsed(tree, "selectors.yml")["selectors"]}
    for source in parsed(tree, "models/_sources.yml")["sources"]:
        entries |= {f"source {source['name']}.{t['name']} at {source['database']}.{source['schema']}": t for t in source["tables"]}
    return entries


def product_files(tree: dict[str, str]) -> dict[str, str]:
    return {p: text for p, text in tree.items() if p.removeprefix(f"{dbt.ROOT}/") not in SHARED and "/macros/" not in p}


def contract_problems(tree: dict[str, str], documents: dict[str, bytes]) -> list[str]:
    """Where the emitted model contracts differ from the ODCS documents, read here on their own:
    each dbt table's model, named and aliased by the helpers, with the contract's columns in order,
    each data_type exactly the physicalType (precision included: dbt-trino's own contract check
    compares only the type's name), and not_null exactly where the column is required."""
    problems = []
    models = {}
    for path in tree:
        if path.endswith("/_models.yml"):
            models |= {m["name"]: m for m in model.parse(path, tree[path].encode()).get("models", [])}
    for path, data in sorted(documents.items()):
        if model.document_kind(path) != "contract":
            continue
        doc = model.parse(path, data)
        if next(p["value"] for p in doc["customProperties"] if p["property"] == "build") != "dbt":
            continue
        _, product_name, table = doc["id"].split(".")
        name = model.dbt_model(product_name, table)
        if name not in models:
            problems.append(f"{doc['id']}: no model {name}")
            continue
        emitted = models.pop(name)
        if emitted["config"]["alias"] != table or emitted["config"]["meta"]["k2"]["contract"] != doc["id"]:
            problems.append(f"{name}: alias or contract id is not {table}, {doc['id']}")
        want = [(c["name"], c["physicalType"], bool(c.get("required"))) for c in doc["schema"][0]["properties"]]
        got = [(c["name"], c["data_type"], {"type": "not_null"} in c.get("constraints", [])) for c in emitted["columns"]]
        problems += [f"{name}: column {w} is emitted as {g}" for w, g in zip(want, got, strict=False) if w != g]
        if len(want) != len(got):
            problems.append(f"{name}: {len(got)} columns, the contract has {len(want)}")
    return problems + [f"{name}: a model no dbt contract declares" for name in sorted(models)]


# --- The trees, against their goldens ------------------------------------------------------


def test_the_good_set_compiles_to_its_golden() -> None:
    assert dbt.emit(loaded(good())) == goldens(GOLDEN)


def test_each_repository_product_compiles_to_its_golden() -> None:
    emitted = dbt.emit(loaded(repository()))
    if not product_files(emitted):
        pytest.skip("contracts/ holds no product that dbt builds")
    kept = goldens(REPOSITORY)
    assert product_files(emitted).keys() <= kept.keys(), "a new dbt product: regenerate its golden and review it"
    assert product_files(emitted) == {p: kept[p] for p in product_files(emitted)}
    golden_entries = shared_entries(kept)
    for key, entry in shared_entries(emitted).items():
        assert golden_entries.get(key) == entry, key
    # the macros are the repository's, so they are the good set's too
    assert {p: t for p, t in emitted.items() if "/macros/" in p} == {p: t for p, t in goldens(GOLDEN).items() if "/macros/" in p}


def test_the_canary_seed_is_named_as_a_model_is_and_built_beside_its_table() -> None:
    m = loaded(repository())
    if "governance.access-canary" not in {p.id for p in m.products}:
        pytest.skip("the access canary (S10) is not in contracts/")
    tree = dbt.emit(m)
    seed = f"{dbt.ROOT}/seeds/governance/access_canary/{model.dbt_model('access-canary', 'event_offsets')}.csv"
    assert tree[seed].encode() == (model.REPO / "contracts/governance/access-canary/seeds/event_offsets.csv").read_bytes()
    (entry,) = parsed(tree, "models/governance/access_canary/_models.yml")["seeds"]
    assert (entry["name"], entry["config"]["alias"]) == ("access_canary__event_offsets", "event_offsets")
    route = {"+database": model.build_catalog("governance"), "+schema": model.base_namespace("access-canary")}
    project = parsed(tree, "dbt_project.yml")
    assert project["seeds"]["k2"]["governance"]["access_canary"] == route == project["models"]["k2"]["governance"]["access_canary"]
    assert "ref('access_canary__event_offsets')" in tree[f"{dbt.ROOT}/models/governance/access_canary/access_canary__events.sql"]


def test_a_serving_product_has_no_model_and_no_selector() -> None:
    m = loaded(repository())
    if "market-analytics.hot-gold" not in {p.id for p in m.products}:
        pytest.skip("hot-gold (S10) is not in contracts/")
    tree = dbt.emit(m)
    assert not [p for p in tree if "hot_gold" in p]
    assert "hot_gold" not in [s["name"] for s in parsed(tree, "selectors.yml")["selectors"]]


# --- What the tree promises ----------------------------------------------------------------


@pytest.mark.parametrize("documents", [good(), repository()], ids=["good", "repository"])
def test_each_model_contract_is_its_odcs_columns_exactly(documents: dict[str, bytes]) -> None:
    assert contract_problems(dbt.emit(loaded(documents)), documents) == []


@pytest.mark.parametrize(("column", "drifted"), [("read_at", "timestamp(3) with time zone"), ("value", "decimal(12,2)"), ("value", "double")])
def test_a_drifted_column_type_fails_the_contract_comparison(column: str, drifted: str) -> None:
    # The emitter writing any type but the contract's, a precision included, is caught.
    m = loaded(good())
    station = product(m, "sensor-net.station-readings")
    silver = next(t for t in station.tables if t.name == "silver_readings")
    columns = tuple(dataclasses.replace(c, physical_type=drifted) if c.name == column else c for c in silver.columns)
    tables = tuple(dataclasses.replace(t, columns=columns) if t is silver else t for t in station.tables)
    problems = contract_problems(dbt.emit(replaced(m, station.id, tables=tables)), good())
    assert len(problems) == 1 and column in problems[0] and drifted in problems[0], problems


@pytest.mark.parametrize("documents", [good(), repository()], ids=["good", "repository"])
def test_the_project_default_is_an_enforced_contract_table_and_any_view_is_invoker(documents: dict[str, bytes]) -> None:
    # A model with no materialized takes this default, and dbt-trino's view is a definer view (S07).
    tree = dbt.emit(loaded(documents))
    defaults = parsed(tree, "dbt_project.yml")["models"][model.DBT_PROJECT]
    assert defaults["+materialized"] == "table"
    assert defaults["+view_security"] == "invoker"
    assert defaults["+contract"] == {"enforced": True} and defaults["+on_table_exists"] == "replace"
    for path in tree:
        if path.endswith("/_models.yml"):
            for m in model.parse(path, tree[path].encode()).get("models", []):
                assert not m["config"].keys() & {"materialized", "view_security", "contract", "database", "schema"}, m["name"]


@pytest.mark.parametrize("documents", [good(), repository()], ids=["good", "repository"])
def test_every_product_is_routed_to_its_base_and_selected_by_its_dag_id(documents: dict[str, bytes]) -> None:
    m = loaded(documents)
    tree = dbt.emit(m)
    project = parsed(tree, "dbt_project.yml")
    selectors = {s["name"]: s["definition"] for s in parsed(tree, "selectors.yml")["selectors"]}
    built = [p for p in m.products if any(t.build.kind == "dbt" for t in p.tables) or p.seeds]
    assert list(selectors) == [model.dag_id(p.name) for p in built]
    for p in built:
        d, f = model.fence(p.domain), model.fence(p.name)
        assert selectors[model.dag_id(p.name)] == {"method": "fqn", "value": f"k2.{d}.{f}"}
        route = {"+database": model.build_catalog(p.domain), "+schema": model.base_namespace(p.name)}
        assert project["models"]["k2"][d][f] == route
        for t in p.tables:
            if t.build.kind == "dbt":
                assert tree[f"{dbt.ROOT}/models/{d}/{f}/{model.dbt_model(p.name, t.name)}.sql"] == p.models[f"{t.name}.sql"]


@pytest.mark.parametrize("name", ["good", "repository"])
def test_an_incremental_model_merges_on_the_primary_key_which_holds_every_partition_column(name: str) -> None:
    m = loaded(good() if name == "good" else repository())
    tree = dbt.emit(m)
    assert not [p for p, text in tree.items() if "incremental_predicates" in text and "/macros/" not in p and not p.endswith("dbt_project.yml")]
    assert parsed(tree, "dbt_project.yml")["models"]["k2"]["+incremental_strategy"] == "merge"
    seen = 0
    for p in m.products:
        for t in p.tables:
            if t.build.kind != "dbt" or model.model_config(p.models[f"{t.name}.sql"]).get("materialized") != "incremental":
                continue
            seen += 1
            props = parsed(tree, f"models/{model.fence(p.domain)}/{model.fence(p.name)}/_models.yml")
            (entry,) = [e for e in props["models"] if e["name"] == model.dbt_model(p.name, t.name)]
            key = [c.name for c in sorted((c for c in t.columns if c.primary_key), key=lambda c: c.primary_key_position or 0)]
            assert entry["config"]["unique_key"] == key
            assert {re.sub(r"^\w+\((\w+).*\)$", r"\1", f) for f in t.partitioning} <= set(key)
    assert seen or name == "repository", "the good set's silver_readings is incremental"


def test_the_tree_holds_no_credential_and_no_env_var() -> None:
    for documents in (good(), repository()):
        tree = dbt.emit(loaded(documents))
        assert not [p for p in tree if p.endswith("profiles.yml")]
        assert not [p for p, text in tree.items() if re.search(r"env_var|password", text, re.I)]


def test_a_model_reading_the_environment_is_refused() -> None:
    m = loaded(good())
    load = product(m, "ops-insight.daily-load")
    sql = load.models["gold_daily_load.sql"].replace("count(*) as readings", "count(*) as readings, '{{ env_var(\"K2_X\") }}' as x")
    with pytest.raises(ValueError, match="env_var in compiled/dbt/models/ops_insight/daily_load/daily_load__gold_daily_load.sql"):
        dbt.emit(replaced(m, load.id, models={"gold_daily_load.sql": sql}))


@pytest.mark.parametrize("config", ["schema='elsewhere'", "alias='other'", "contract={'enforced': False}",
                                    "incremental_predicates=['x']", "properties={'location': \"'s3://x'\"}"])
def test_a_model_config_the_release_decides_is_refused(config: str) -> None:
    m = loaded(good())
    load = product(m, "ops-insight.daily-load")
    sql = load.models["gold_daily_load.sql"].replace("materialized='table'", f"materialized='table', {config}")
    with pytest.raises(ValueError, match=r"a model sets only materialized, unique_key"):
        dbt.emit(replaced(m, load.id, models={"gold_daily_load.sql": sql}))


def test_a_foreign_key_into_an_input_is_tested_at_the_pinned_snapshot() -> None:
    tree = dbt.emit(loaded(good()))
    (gold,) = parsed(tree, "models/ops_insight/daily_load/_models.yml")["models"]
    (station,) = [c for c in gold["columns"] if c["name"] == "station_id"]
    assert {"k2_input_relationships": {"arguments": {"product_fence": "station_readings", "table": "silver_readings", "field": "station_id"}}} in station["data_tests"]
    assert gold["data_tests"] == [{"k2_unique_key": {"arguments": {"columns": ["load_date", "station_id"]}}}]


def test_a_foreign_key_into_a_table_the_product_does_not_read_is_refused() -> None:
    m = loaded(good())
    with pytest.raises(ValueError, match="neither builds nor reads; add it as an input port"):
        dbt.emit(replaced(m, "ops-insight.daily-load", input_ports=()))


def test_each_quality_rule_becomes_a_test_or_is_refused() -> None:
    m = loaded(good())
    station = product(m, "sensor-net.station-readings")
    rules = (model.Quality("regions", "custom", "dbt", None, {"accepted_values": {"column_name": "region", "arguments": {"values": ["north", "south"]}}}),
             model.Quality("as text", "custom", "dbt", None, "not_null:\n  column_name: operator_note\n"),
             model.Quality("words", "text", None, "Readings are what the feed sent.", None),
             model.Quality("in range", "sql", None, None, None, query="select max(value) from ${table}", thresholds={"mustBeBetween": [-90, 60]}),
             model.Quality("built", "library", None, None, None, metric="rowCount", thresholds={"mustBeGreaterThan": 0}),
             model.Quality("unique share", "library", None, None, None, metric="duplicateValues", arguments={"properties": ["station_id"]},
                           unit="percent", thresholds={"mustBeLessThan": 5}))
    tables = tuple(dataclasses.replace(t, quality=rules) if t.name == "silver_readings" else t for t in station.tables)
    tree = dbt.emit(replaced(m, station.id, tables=tables))
    (silver,) = parsed(tree, "models/sensor_net/station_readings/_models.yml")["models"]
    assert silver["data_tests"][1:] == [
        rules[0].implementation, {"not_null": {"column_name": "operator_note"}},
        {"k2_quality": {"arguments": {"query": "select max(value) from ${table}", "holds": "metric_value between -90 and 60"}}},
        {"k2_quality": {"arguments": {"query": "select count(*) from ${table}", "holds": "metric_value > 0"}}},
        {"k2_quality": {"arguments": {"query": 'select coalesce(100.0 * sum(n - 1) / sum(n), 0) from (select count(*) as n from ${table} '
                                               'group by "station_id") as k2_groups', "holds": "metric_value < 5"}}}]
    # never dropped: a form K2 does not run is refused (S05 reports it first, docs.quality-unsupported)
    for bad, why in ((model.Quality("two tests", "custom", "dbt", None, {"unique": {}, "not_null": {}}), "one data test"),
                     (model.Quality("no query", "sql", None, None, None, thresholds={"mustBe": 0}), "reads the table as"),
                     (model.Quality("audit", "custom", "k2-connector", None, "value_parses"), "bronze audit"),
                     (model.Quality("nulls", "library", None, None, None, metric="nullValues", thresholds={"mustBe": 0}), "column rules")):
        tables = tuple(dataclasses.replace(t, quality=(bad,)) if t.name == "silver_readings" else t for t in station.tables)
        with pytest.raises(ValueError, match=why):
            dbt.emit(replaced(m, station.id, tables=tables))


def test_the_output_is_deterministic() -> None:
    documents = good()
    first = dbt.emit(loaded(documents))
    assert dbt.emit(loaded(dict(reversed(documents.items())))) == first
    assert not [p for p, text in first.items() if "&id" in text or "*id" in text]  # no YAML alias for model.parse to refuse


# --- The dbt proof: make test-dbt-tree ----------------------------------------------------

PROFILE = f"""k2:
  target: proof
  outputs:
    proof:
      type: trino
      method: none
      http_scheme: http
      host: 127.0.0.1
      port: {PROOF_PORT}
      user: dbt_proof
      database: proof
      schema: proof
      threads: 1
"""
# Rendering an incremental model asks the warehouse whether its table exists (dbt's is_incremental),
# so the proof copy of the tree, never the release's, answers from a var: both branches render.
PROOF_MACRO = "{% macro is_incremental() %}{{ return(var('proof_incremental', false)) }}{% endmacro %}\n"


def run_dbt(tmp: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    out = subprocess.run(["uv", "run", "--locked", "dbt", *args, "--project-dir", str(tmp / "tree" / dbt.ROOT),
                          "--profiles-dir", str(tmp), "--target-path", str(tmp / "target"), "--log-path", str(tmp / "logs"),
                          "--no-partial-parse"], cwd=model.REPO / "dbt", env=env, capture_output=True, text=True)
    assert out.returncode == 0, f"dbt {' '.join(args)}:\n{out.stdout}\n{out.stderr}"
    return out.stdout


@pytest.mark.skipif(os.environ.get("K2_DBT_PROOF") != "1", reason="the dbt proof runs under make test-dbt-tree (K2_DBT_PROOF=1)")
@pytest.mark.parametrize("name", ["good", "repository"])
def test_dbt_parses_compiles_and_selects_the_tree(tmp_path: Path, name: str) -> None:
    try:
        subprocess.run(["bash", "-c", f"exec 3<>/dev/tcp/127.0.0.1/{PROOF_PORT}"], check=True, capture_output=True)
        pytest.fail(f"something listens on 127.0.0.1:{PROOF_PORT}, and the proof must reach no warehouse")
    except subprocess.CalledProcessError:
        pass
    m = loaded(repository() if name == "repository" else good())
    for path, text in dbt.emit(m).items():
        (tmp_path / "tree" / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "tree" / path).write_text(text)
    (tmp_path / "profiles.yml").write_text(PROFILE)
    (tmp_path / "tree" / dbt.ROOT / "macros" / "proof_is_incremental.sql").write_text(PROOF_MACRO)

    out = run_dbt(tmp_path, "parse")
    assert "WARNING" not in out, out
    manifest = json.loads((tmp_path / "target" / "manifest.json").read_text())
    built = [(p, t) for p in m.products for t in p.tables if t.build.kind == "dbt"]
    for p, t in built:
        node = manifest["nodes"][model.dbt_docs(p.name, t.name).split("/model/")[1]]  # the docs link is the node's id
        assert (node["database"], node["schema"], node["alias"]) == tuple(model.build_relation(p.domain, p.name, t.name).split("."))
        assert node["contract"]["enforced"] and [(c["name"], c["data_type"]) for c in node["columns"].values()] == [
            (c.name, c.physical_type) for c in t.columns]
    for p in m.products:
        for s in p.seeds:
            node = manifest["nodes"][f"seed.k2.{model.dbt_model(p.name, s.removesuffix('.csv'))}"]
            assert f"{node['database']}.{node['schema']}.{node['alias']}" == model.build_relation(p.domain, p.name, s.removesuffix(".csv"))

    pins = {f"{model.fence(port.contract_id.split('.')[1])}/{port.contract_id.split('.')[2]}": {"snapshot": 42}
            for p in m.products for port in p.input_ports}
    compiled = {}
    for incremental in (False, True):
        target = tmp_path / "target"
        run_dbt(tmp_path, "compile", "--exclude", "resource_type:operation",  # the k2_results stub refuses to render (phase 002)
                "--vars", json.dumps({"proof_incremental": incremental, "k2_inputs": pins}))
        compiled[incremental] = {p.relative_to(target).as_posix(): p.read_text() for p in (target / "compiled").rglob("*.sql")}
    for p, t in built:
        sql = compiled[False][f"compiled/k2/models/{model.fence(p.domain)}/{model.fence(p.name)}/{model.dbt_model(p.name, t.name)}.sql"]
        for port in p.input_ports:
            assert f"{model.quote_relation(model.base_relation(*port.contract_id.split('.')))} FOR VERSION AS OF BIGINT '42'" in sql
        if model.model_config(p.models[f"{t.name}.sql"]).get("materialized") == "incremental":
            assert compiled[True][f"compiled/k2/models/{model.fence(p.domain)}/{model.fence(p.name)}/{model.dbt_model(p.name, t.name)}.sql"] != sql
    foreign = [sql for s, sql in compiled[False].items() if "/k2_input_relationships_" in s]
    assert len(foreign) == (1 if name == "good" else 0)  # daily-load's station_id into its input
    assert all("FOR VERSION AS OF BIGINT '42'" in sql for sql in foreign)  # the foreign-key test reads the pinned snapshot too
    quality = [sql for s, sql in compiled[False].items() if "/k2_quality_" in s]
    rules = sum(1 for p, t in built for q in t.quality if q.type in ("sql", "library"))
    assert len(quality) == rules and (rules or name == "repository"), "the good set's silver_readings has sql and library rules"
    assert not [sql for sql in quality if "${table}" in sql or "metric_value" not in sql]  # ${table} is the model's relation

    for p in m.products:
        if not any(t.build.kind == "dbt" for t in p.tables) and not p.seeds:
            continue
        listed = run_dbt(tmp_path, "ls", "-q", "--selector", model.dag_id(p.name), "--resource-types", "model", "seed", "--output", "name")
        want = {model.dbt_model(p.name, t.name) for t in p.tables if t.build.kind == "dbt"} | {model.dbt_model(p.name, s.removesuffix(".csv")) for s in p.seeds}
        assert set(listed.split()) == want, listed


def regenerate(root: Path) -> None:
    for out, documents in ((GOLDEN, good()), (REPOSITORY, repository(root))):
        tree = dbt.emit(loaded(documents))
        if out == REPOSITORY and not product_files(tree):
            continue
        for path, text in tree.items():
            if out == REPOSITORY and "/macros/" in path:
                continue  # the repository's macros are the good set's, compared there
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            (out / path).write_text(text)


if __name__ == "__main__":
    regenerate(Path(sys.argv[1]) if len(sys.argv) > 1 else model.REPO)
