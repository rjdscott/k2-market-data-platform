"""emit/pipeline.py (S16): pipelines, the DAG list and the one-shots, for the good fixture set and
the repository's products.

Goldens: `golden/pipeline/<release path>` for the good set; `golden/pipeline/repository/<release
path>` for each real product under contracts/ with a schedule, compared while that product exists.
Regenerate both, then review the diff as code:

    cd platform/k2gen && uv run --locked python tests/test_emit_pipeline.py [<repository root>]
"""

import dataclasses
import hashlib
import json
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from k2gen import model, release
from k2gen.emit import pipeline
from k2gen.model import DocumentError, DocumentSet, Model

GOOD = Path(__file__).parent / "fixtures" / "v2" / "good"
GOLDEN = Path(__file__).parent / "golden" / "pipeline"
REPOSITORY = GOLDEN / "repository"
# Made-up local builds, `<name>:<image id 12>` (manifest.schema.json#/$defs/imageRef).
IMAGES = {"tools": "tools:0a1b2c3d4e5f", "dbt-runner": "dbt-runner:5a6b7c8d9e0f", "fetch": "fetch:4008294739ab"}
STATION = "contracts/sensor-net/station-readings"
STATION_ID, DAILY_ID = "sensor-net.station-readings", "ops-insight.daily-load"


def good() -> dict[str, bytes]:
    return {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in sorted(GOOD.rglob("*")) if p.is_file()}


def repository(root: Path = model.REPO) -> dict[str, bytes]:
    """The files under model.DOCUMENT_ROOTS at `root`, as a tag's document set holds them."""
    documents = {}
    for name in model.DOCUMENT_ROOTS:
        base = root / name
        paths = sorted(base.rglob("*")) if name.endswith("/") else [base]
        documents |= {p.relative_to(root).as_posix(): p.read_bytes() for p in paths if p.is_file()}
    return documents


def loaded(documents: dict[str, bytes], images: dict[str, str] = IMAGES) -> Model:
    provenance = model.Provenance("git", "v3.0.0-alpha.3-rc.1", "0" * 40, "Example Owner <owner@example.org>",
                                  "2026-01-01T00:00:00Z", model.set_sha256(documents), ())
    return model.load(DocumentSet(documents, provenance), images)


def goldens(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted((root / "compiled").rglob("*")) if p.is_file()}


def product(m: Model, pid: str) -> model.Product:
    (p,) = [p for p in m.products if p.id == pid]
    return p


def replaced(m: Model, pid: str, **changes: Any) -> Model:
    """The model with one product's fields changed."""
    return dataclasses.replace(m, products=tuple(dataclasses.replace(p, **changes) if p.id == pid else p for p in m.products))


def kind(path: str) -> str:
    """The schema of an emitted path, by the compiler's own table."""
    (found,) = [k for pattern, k in release.EMITTED_SCHEMAS.items() if fnmatchcase(path, pattern)]
    return found


def test_the_good_set_compiles_to_its_goldens() -> None:
    assert pipeline.emit(loaded(good())) == goldens(GOLDEN)


def test_each_repository_product_compiles_to_its_golden(capsys: pytest.CaptureFixture[str]) -> None:
    emitted = pipeline.emit(loaded(repository()))
    pipelines = {p for p in emitted if fnmatchcase(p, model.pipeline_path("*"))}
    if not pipelines:
        pytest.skip("contracts/ holds no v2 product with a schedule")
    kept = goldens(REPOSITORY)
    assert pipelines <= kept.keys(), "a new scheduled product: regenerate its golden and review it"
    assert {p: emitted[p] for p in pipelines} == {p: kept[p] for p in pipelines}
    # each DAG pinned on its own: the list grows with every product
    golden_dags = {d["dagId"]: d for d in json.loads(kept[model.DAGS])["dags"]}
    for dag in json.loads(emitted[model.DAGS])["dags"]:
        assert dag == golden_dags[dag["dagId"]]
    if "market-analytics.hot-gold" in {p.id for p in loaded(repository()).products}:
        assert "NO PIPELINE market-analytics.hot-gold: it has no schedule" in capsys.readouterr().err


@pytest.mark.parametrize("documents", [good(), repository()], ids=["good", "repository"])
def test_every_emitted_file_validates_against_its_frozen_schema(documents: dict[str, bytes]) -> None:
    emitted = pipeline.emit(loaded(documents))
    assert {model.DAGS, model.ONESHOTS} <= emitted.keys()
    for path, text in emitted.items():
        assert model.schema_errors(kind(path), json.loads(text)) == [], path


def test_the_output_is_deterministic() -> None:
    documents = good()
    first = pipeline.emit(loaded(documents))
    m = loaded(dict(reversed(documents.items())), dict(reversed(IMAGES.items())))
    assert pipeline.emit(dataclasses.replace(m, products=m.products[::-1])) == first


def test_the_seed_built_canary_has_a_build_and_no_fetch_or_landing() -> None:
    """A dbt-only product (the canary builds from its seed) has a pipeline the schema takes: no
    connector, landing or bronze, and a DAG without fetch, load, audit or publish."""
    m = loaded(repository())
    if "governance.access-canary" not in {p.id for p in m.products}:
        pytest.skip("the access canary is not in contracts/")
    emitted = pipeline.emit(m)
    doc = json.loads(emitted[model.pipeline_path("governance.access-canary")])
    assert not {"connector", "landing", "bronze"} & doc.keys() and doc["inputs"] == []
    assert doc["build"]["outputs"] == [model.build_relation("governance", "access-canary", "events")]
    (dag,) = [d for d in json.loads(emitted[model.DAGS])["dags"] if d["product"] == "governance.access-canary"]
    assert dag["tasks"] == ["resolve", "build", "expire", "record", "maintain"]


def test_a_product_without_a_schedule_emits_nothing_and_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    emitted = pipeline.emit(replaced(loaded(good()), DAILY_ID, schedule=None))
    assert model.pipeline_path(DAILY_ID) not in emitted
    assert [d["product"] for d in json.loads(emitted[model.DAGS])["dags"]] == [STATION_ID]
    assert f"NO PIPELINE {DAILY_ID}: it has no schedule, so no DAG runs it" in capsys.readouterr().err


def test_the_connector_is_copied_never_rendered_and_a_forbidden_macro_never_passes() -> None:
    m = loaded(good())
    doc = json.loads(pipeline.emit(m)[model.pipeline_path(STATION_ID)])
    assert doc["connector"]["url"] == "https://data.example.org/readings/{{ds}}.csv"  # {{ds}} as written

    url = "https://data.example.org/{{ var.value.token }}.csv"  # a macro that could read a secret
    documents = good()
    documents[f"{STATION}/product.odps.yaml"] = documents[f"{STATION}/product.odps.yaml"].replace(
        b"https://data.example.org/readings/{{ds}}.csv", url.encode())
    with pytest.raises(DocumentError, match=r"customProperties/0/value/url: .* does not match"):
        loaded(documents)  # refused upstream, at load
    # A model built around the loader gets the macro copied verbatim, and the release's schema
    # check on pipeline.json refuses it: the emitter is never the guard, and never renders.
    station = product(m, STATION_ID)
    assert station.connector
    forged = replaced(m, STATION_ID, connector=model.Connector("https-file", {**station.connector.settings, "url": url}))
    doc = json.loads(pipeline.emit(forged)[model.pipeline_path(STATION_ID)])
    assert doc["connector"]["url"] == url
    assert [e for e in model.schema_errors("pipeline", doc) if "$.connector.url" in e]


def test_bronze_follows_the_contract() -> None:
    (bronze,) = json.loads(pipeline.emit(loaded(good()))[model.pipeline_path(STATION_ID)])["bronze"]
    assert bronze["key"] == ["station_id", "read_at", model.LINEAGE_COLUMN]  # primaryKeyPosition order
    assert model.LINEAGE_COLUMN not in bronze["values"] and bronze["patterns"] == {"station_id": "^s-[0-9]+$"}
    assert bronze["audits"] == ["value_parses"] and bronze["branch"] == model.AUDIT_BRANCH
    # the lineage column is never a value, in the key or not
    m = loaded(good())
    (table,) = [t for t in product(m, STATION_ID).tables if t.build.kind == "connector"]
    unkeyed = dataclasses.replace(table, columns=tuple(
        dataclasses.replace(c, primary_key=False, primary_key_position=None) if c.name == model.LINEAGE_COLUMN else c for c in table.columns))
    tables = tuple(unkeyed if t is table else t for t in product(m, STATION_ID).tables)
    (bronze,) = json.loads(pipeline.emit(replaced(m, STATION_ID, tables=tables))[model.pipeline_path(STATION_ID)])["bronze"]
    assert bronze["key"] == ["station_id", "read_at"] and bronze["values"] == ["value_text", "region", "operator_note"]


def test_a_kafka_connector_lands_a_table_and_has_no_fetch_task() -> None:
    m = loaded(good())
    kafka = model.Connector("kafka", {"brokers": "redpanda:9092", "registry": "http://redpanda:8081", "topic": "example.readings",
                                      "envelope": {"format": "avro-confluent", "contract": "sensor-net.station-readings.bronze_readings"},
                                      "payload": "json", "startFrom": "earliest-retained", "maxOffsetsPerPartition": 1000})
    emitted = pipeline.emit(replaced(m, STATION_ID, connector=kafka))
    doc = json.loads(emitted[model.pipeline_path(STATION_ID)])
    assert doc["landing"] == {"table": model.lake_relation("station-readings", model.KAFKA_LANDING)}
    assert model.schema_errors("pipeline", doc) == []
    dags = {d["product"]: d["tasks"] for d in json.loads(emitted[model.DAGS])["dags"]}
    assert dags[STATION_ID] == ["resolve", "load", "audit", "publish", "build", "expire", "record", "maintain"]


def test_an_unresolved_input_or_a_code_build_is_refused() -> None:
    m = loaded(good())
    port = dataclasses.replace(product(m, DAILY_ID).input_ports[0], contract_id="sensor-net.station-readings.gone")
    with pytest.raises(ValueError, match="input sensor-net.station-readings.gone is no table"):
        pipeline.emit(replaced(m, DAILY_ID, input_ports=(port,)))
    table = dataclasses.replace(product(m, DAILY_ID).tables[0], build=model.Build("code", "daily@1"))
    with pytest.raises(ValueError, match="gold_daily_load built by code"):
        pipeline.emit(replaced(m, DAILY_ID, tables=(table,)))


def _cron_fields(cron: str) -> list[str]:
    fields = cron.split()
    assert len(fields) == 5, cron
    return fields


def test_schedules_are_the_products_and_maintenance_is_daily_in_its_window() -> None:
    m = loaded(good())
    emitted = pipeline.emit(m)
    crons = set()
    for dag in json.loads(emitted[model.DAGS])["dags"]:
        p = product(m, dag["product"])
        assert p.schedule and dag["schedule"] == p.schedule.cron
        doc = json.loads(emitted[dag["pipeline"]])
        assert doc["schedule"]["cron"] == p.schedule.cron and doc["dagId"] == dag["dagId"] == model.dag_id(p.name)
        minute, hour, *rest = _cron_fields(doc["maintain"]["cron"])
        assert rest == ["*", "*", "*"] and 0 <= int(minute) < 60
        assert pipeline.MAINTAIN_FIRST_HOUR <= int(hour) < pipeline.MAINTAIN_FIRST_HOUR + pipeline.MAINTAIN_HOURS
        crons.add(doc["maintain"]["cron"])
    assert len(crons) == 2  # staggered: the two fixture products draw different minutes
    # pools and the one-active-run lock are the factory's constants: no DAG entry can carry one
    assert json.loads((model.SCHEMA_DIR / "dags.schema.json").read_text())["properties"]["dags"]["items"]["additionalProperties"] is False


def shape_changed(old: Model, new: Model, tmp: Path) -> bool:
    """The compiler's needs.dagShapeChange (release.needs) from `old`'s dags.json to `new`'s."""
    before, after, compose = pipeline.emit(old)[model.DAGS].encode(), pipeline.emit(new)[model.DAGS].encode(), b"services: {}\n"
    for path, data in ((model.DAGS, before), (model.RELEASE_COMPOSE, compose)):
        (tmp / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp / path).write_bytes(data)
    files = {model.DAGS: hashlib.sha256(before).hexdigest(), model.RELEASE_COMPOSE: hashlib.sha256(compose).hexdigest()}
    previous = SimpleNamespace(root=tmp, manifest=SimpleNamespace(files=files, images={}))
    return release.needs(previous, {model.DAGS: after, model.RELEASE_COMPOSE: compose}, {}).dag_shape_change


def test_the_dag_shape_changes_with_its_tasks_and_not_with_a_description(tmp_path: Path) -> None:
    m = loaded(good())
    described = replaced(m, STATION_ID, purpose="Another synthetic purpose.")
    assert pipeline.emit(described)[model.DAGS] == pipeline.emit(m)[model.DAGS]
    assert not shape_changed(m, described, tmp_path / "a")
    station = product(m, STATION_ID)
    assert station.schedule
    recron = replaced(m, STATION_ID, schedule=dataclasses.replace(station.schedule, cron="45 * * * *"))
    assert not shape_changed(m, recron, tmp_path / "b")  # a new cron, the same tasks
    no_connector = replaced(m, STATION_ID, connector=None, tables=tuple(t for t in station.tables if t.build.kind == "dbt"))
    assert shape_changed(m, no_connector, tmp_path / "c")  # fetch, load, audit, publish gone


def test_a_one_shot_kind_without_an_image_is_left_out_and_said(capsys: pytest.CaptureFixture[str]) -> None:
    emitted = pipeline.emit(loaded(good(), {"tools": IMAGES["tools"]}))
    assert list(json.loads(emitted[model.ONESHOTS])["oneshots"]) == ["tools"]
    err = capsys.readouterr().err
    assert "NO ONESHOT fetch" in err and "NO ONESHOT dbt-runner" in err
    assert model.ONESHOTS not in pipeline.emit(loaded(good(), {}))  # the schema needs at least one kind


def test_the_one_shot_kinds_agree_with_the_compose_stubs() -> None:
    """Limits and networks as docker-compose.yml declares each stub; fetch on k2-egress alone and
    without a secret, no other kind on k2-egress; dbt-runner's env is the wrapper's exact list (the phase page)."""
    services = yaml.safe_load((model.REPO / model.COMPOSE).read_text())["services"]
    for name, spec in pipeline.ONESHOT_KINDS.items():
        limits = services[name]["deploy"]["resources"]["limits"]
        assert (spec["cpus"], spec["memory"], spec["networks"]) == (limits["cpus"], limits["memory"], services[name]["networks"]), name
    assert pipeline.ONESHOT_KINDS["fetch"]["networks"] == ["k2-egress"] and pipeline.ONESHOT_KINDS["fetch"]["secrets"] == []
    assert not [k for k, spec in pipeline.ONESHOT_KINDS.items() if k != "fetch" and "k2-egress" in spec["networks"]]
    page = (model.REPO / "docs/development/phase-001-interfaces.md").read_text()
    assert "Its environment is exactly `" + " ".join(pipeline.ONESHOT_KINDS["dbt-runner"]["env"]) + "`" in page.replace("\n  ", " ")


def regenerate(root: Path) -> None:
    for out, documents in ((GOLDEN, good()), (REPOSITORY, repository(root))):
        for path, text in pipeline.emit(loaded(documents)).items():
            if out == REPOSITORY and path == model.ONESHOTS:
                continue  # the good set's: it depends on the images only
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            (out / path).write_text(text)


if __name__ == "__main__":
    regenerate(Path(sys.argv[1]) if len(sys.argv) > 1 else model.REPO)
