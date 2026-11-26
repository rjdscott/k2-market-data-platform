"""A file source's contract (ADR-036): validated, then generated into the two configs.

The synthetic source in tests/synthetic/ is copied into a copy of contracts/. What it
generates is held to the two committed files the DAG factory's tests and the lake job's
tests read, so neither side can drift from what k2gen writes.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from k2gen import cli, ddl, generate

ROOT = cli.ROOT
SYNTHETIC = Path(__file__).parent / "synthetic"
CONTRACT = "contracts/fx-reference/synthetic-rates/bronze.synthetic_rates.odcs.yaml"
PRODUCT = "contracts/fx-reference/synthetic-rates/product.yaml"
PINNED = {generate.FILE_SOURCES: "tests/synthetic_file_sources.json", generate.DAG_SOURCES: "docker/airflow/tests/synthetic_file_sources.json"}


@pytest.fixture
def tree(tmp_path):
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts")
    shutil.copytree(SYNTHETIC, tmp_path / "contracts", dirs_exist_ok=True)
    (tmp_path / cli.LAKE_SQL).parent.mkdir(parents=True)
    shutil.copy(ROOT / cli.LAKE_SQL, tmp_path / cli.LAKE_SQL)
    shutil.copytree(ROOT / cli.ADR_DIR, tmp_path / cli.ADR_DIR)
    return tmp_path


def edit(path, change):
    doc = yaml.safe_load(path.read_text())
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def props(doc):
    return {p["property"]: p for p in doc["customProperties"]}


def column(doc, name):
    (col,) = [c for c in doc["schema"][0]["properties"] if c["name"] == name]
    return col


def test_the_synthetic_source_validates(tree):
    assert cli.validate(tree) == []


def test_it_generates_the_pinned_configs_and_its_ddl(tree):
    files = generate.generate(tree)
    for generated, pinned in PINNED.items():
        assert json.loads(files[generated]) == json.loads((ROOT / pinned).read_text()), f"regenerate {pinned}"
    (table,) = ddl.parse(tree / generate.GENERATED_DDL / "bronze.synthetic_rates.sql").values()
    assert table.columns == ("rate_date", "code", "value", "source_file_sha256")


def test_the_lake_config_reads_key_numbers_and_patterns_from_the_columns(tree):
    spec = json.loads(generate.render(tree)[generate.FILE_SOURCES])["bronze.synthetic_rates"]
    assert (spec["key"], spec["values"], spec["decimal"]) == (["rate_date", "code"], ["value"], ["value"])
    assert spec["patterns"] == {"code": "^[A-Z]{3}$"}
    # the key's order is primaryKeyPosition's, not the order the columns are declared in
    edit(tree / CONTRACT, lambda d: (column(d, "rate_date").update(primaryKeyPosition=2), column(d, "code").update(primaryKeyPosition=1)))
    assert json.loads(generate.render(tree)[generate.FILE_SOURCES])["bronze.synthetic_rates"]["key"] == ["code", "rate_date"]


def test_the_repository_declares_no_file_source_so_no_dag_is_built():
    files = generate.render(ROOT)
    assert files[generate.FILE_SOURCES] == files[generate.DAG_SOURCES] == "{}\n"


def broken(change):
    def apply(tree):
        edit(tree / CONTRACT, change)

    return apply


def drop_column(name):
    return broken(lambda d: d["schema"][0]["properties"].remove(column(d, name)))


@pytest.mark.parametrize(
    "breaks, message",
    [
        (drop_column("source_file_sha256"), "has column source_file_sha256 STRING, required"),
        (broken(lambda d: [column(d, c).pop("primaryKey") for c in ("rate_date", "code")]), "declares its key, primaryKey"),
        (broken(lambda d: column(d, "code").pop("required")), "key column code must be required"),
        (broken(lambda d: column(d, "value").update(physicalType="TIMESTAMP")), "column value is TIMESTAMP"),
        (broken(lambda d: column(d, "code")["logicalTypeOptions"].update(pattern="^[A-Z")), "pattern does not compile"),
        (broken(lambda d: props(d)["schedule"].update(value="")), "five fields"),
        # not a file-source rule but every generated table's: Spark SQL has no '' escape
        (broken(lambda d: column(d, "value").update(description="the file's value")), "column value holds a single quote"),
        (broken(lambda d: d["description"].update(purpose="the file's rows")), "description.purpose holds a single quote"),
    ],
)
def test_each_file_source_rule_fails_alone(tree, breaks, message):
    breaks(tree)
    errors = cli.validate(tree)
    assert len(errors) == 1 and message in errors[0], errors


def test_a_file_source_whose_upstreams_omit_its_url_fails(tree):
    edit(tree / CONTRACT, lambda d: props(d)["upstreams"].update(value=[]))
    assert cli.validate(tree) == [
        f"{CONTRACT}: a file source reads its url, so upstreams lists https://example.invalid/synthetic-rates.xml",
        f"{PRODUCT}: input https://example.invalid/synthetic-rates.xml is read by none of its outputs",
    ]


def test_a_file_source_outside_bronze_fails(tree):
    def silver(d):
        d["schema"][0]["physicalName"] = d["name"] = "silver.synthetic_rates"
        d["id"] = "lake.silver.synthetic_rates"
        props(d)["layer"]["value"] = "silver"

    edit(tree / CONTRACT, silver)
    (tree / CONTRACT).rename(tree / CONTRACT.replace("bronze.", "silver."))
    edit(tree / PRODUCT, lambda d: d.update(outputs=["silver.synthetic_rates"]))
    assert [e for e in cli.validate(tree) if "a file source lands in bronze, not silver" in e]


def test_a_dag_id_a_hand_written_dag_has_fails():
    errors = cli._file_source_errors("x", "bronze.lake", [{"name": "id", "physicalType": "STRING", "required": True, "primaryKey": True},
                                                          {"name": "source_file_sha256", "physicalType": "STRING", "required": True}],
                                     {"schedule": "0 6 * * *", "upstreams": ["https://e.invalid/x"], "fileSource": {"url": "https://e.invalid/x"}})
    assert errors == ["x: its DAG would be lake_ingest, which is a hand-written DAG in docker/airflow/dags/"]


@pytest.mark.parametrize(
    "field, value",
    [("url", "http://example.invalid/x.xml"), ("format", "csv"), ("calendar", "target"), ("audits", ["parses"]), ("parser", "Synthetic")],
)
def test_the_schema_refuses_a_bad_file_source_field(tree, field, value):
    edit(tree / CONTRACT, lambda d: props(d)["fileSource"]["value"].update({field: value}))
    errors = cli.validate(tree)
    assert errors and all("customProperties: $.fileSource" in e for e in errors), errors
