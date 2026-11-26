"""Two phase 001 interfaces this suite can hold without a stack
(docs/development/phase-001-interfaces.md): the REST routes' OpenAPI document (A12)
and the run-record table's DDL (A6). A change to either fails here until the test
moves with it, which is the point of freezing them.

Each validator runs in a subprocess under its own pin, so test-python's dependency
line, which the Makefile and CI each carry, stays as it is.
"""
# ponytail: a uv subprocess per validator; fold the pins into test-python's --with
# list when ci.yml next moves (step S21 owns it).

import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OPENAPI = ROOT / "services/k2-api/openapi.yaml"
RUN_RECORD = ROOT / "platform/runs/run_record.sql"
VALIDATOR = "openapi-spec-validator==0.9.0"
SQLGLOT = "sqlglot==30.35.0"  # the version dbt/uv.lock already pins


def uv(pin: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", "--no-project", "-q", "--with", pin, *argv],
                          cwd=ROOT, capture_output=True, text=True)


def test_the_openapi_document_validates_as_3_1(tmp_path):
    ok = uv(VALIDATOR, "openapi-spec-validator", "--schema", "3.1", str(OPENAPI))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    # The validator is not a no-op: a dangling $ref fails it.
    broken = tmp_path / "openapi.yaml"
    broken.write_text(OPENAPI.read_text().replace("#/components/schemas/DataPage", "#/components/schemas/Nope"))
    assert uv(VALIDATOR, "openapi-spec-validator", "--schema", "3.1", str(broken)).returncode != 0


def test_the_routes_are_the_frozen_set_and_only_data_needs_a_key():
    doc = yaml.safe_load(OPENAPI.read_text())
    assert sorted(doc["paths"]) == [
        "/healthz", "/v1/data/{domain}/{product}/{table}", "/v1/openapi.json", "/v1/products",
        "/v1/products/{domain}/{product}", "/v1/products/{domain}/{product}/contracts/{table}.odcs.yaml",
        "/v1/release"]
    assert doc["security"] == []  # metadata is open by default
    data = doc["paths"]["/v1/data/{domain}/{product}/{table}"]["get"]
    assert data["security"] == [{"apiKey": []}]
    assert {"401", "403", "404", "410", "422"} <= data["responses"].keys()
    assert doc["components"]["schemas"]["DataPage"]["required"] == ["release", "snapshot", "rows", "next"]


def test_as_of_is_declared_only_to_be_refused():
    # Not offered until the owner decides in phase 007: a client sending it gets 422
    # as_of_unavailable, never the current rows as if it had been honoured.
    doc = yaml.safe_load(OPENAPI.read_text())
    data = doc["paths"]["/v1/data/{domain}/{product}/{table}"]["get"]
    assert "as_of" in {p["name"] for p in data["parameters"]}
    assert data["responses"]["422"] == {"$ref": "#/components/responses/AsOfUnavailable"}
    answer = doc["components"]["responses"]["AsOfUnavailable"]["content"]["application/json"]["schema"]
    assert answer["allOf"][1]["properties"]["error"] == {"const": "as_of_unavailable"}
    assert "__base" not in OPENAPI.read_text()  # readers' names only (A11)


# Prints the parsed table as JSON. Run with -I under the pinned sqlglot.
PARSE = """
import json, sys
import sqlglot
from sqlglot import exp
(create,) = sqlglot.parse(open(sys.argv[1]).read(), read="trino")
assert isinstance(create, exp.Create) and create.kind == "TABLE", create
cols = []
for d in create.this.expressions:
    kinds = [c.args["kind"] for c in d.args.get("constraints") or []]
    comment = next((k.this.this for k in kinds if isinstance(k, exp.CommentColumnConstraint)), None)
    cols.append([d.name, d.args["kind"].sql("trino").lower(), comment])
parts = [p for p in create.args["properties"].expressions if p.name == "partitioning"]
print(json.dumps({"table": create.this.this.sql("trino"), "columns": cols,
                  "partitioning": [e.this for p in parts for e in p.args["value"].expressions]}))
"""

# A6, column for column. A changed column moves here, in the DDL and in the
# interfaces page together, or not at all.
A6 = [
    ("run_id", "varchar"), ("product_id", "varchar"), ("release_id", "varchar"), ("task", "varchar"),
    ("event", "varchar"), ("table_name", "varchar"), ("snapshot_before", "bigint"), ("snapshot_after", "bigint"),
    ("inputs", "varchar"), ("rows_written", "bigint"), ("bytes_written", "bigint"),
    ("started_at", "timestamp(6) with time zone"), ("recorded_at", "timestamp(6) with time zone"),
    ("duration_s", "double"), ("detail", "varchar"),
]


def parse_run_record(path: Path) -> dict:
    out = uv(SQLGLOT, "python", "-I", "-c", PARSE, str(path))
    assert out.returncode == 0, out.stdout + out.stderr
    return json.loads(out.stdout)


def test_the_run_record_ddl_is_a6_with_every_column_described():
    t = parse_run_record(RUN_RECORD)
    assert t["table"] == "governance_build.run_records.run_record"
    assert [(n, ty) for n, ty, _ in t["columns"]] == A6
    assert [n for n, _, comment in t["columns"] if not (comment or "").strip()] == []
    assert t["partitioning"] == ["day(recorded_at)"]


def test_a_column_without_a_description_fails(tmp_path):
    sql = RUN_RECORD.read_text().replace(
        " COMMENT 'Seconds from started_at to recorded_at, as the writer measured them'", "")
    (tmp_path / "r.sql").write_text(sql)
    t = parse_run_record(tmp_path / "r.sql")
    assert [n for n, _, comment in t["columns"] if not comment] == ["duration_s"]
