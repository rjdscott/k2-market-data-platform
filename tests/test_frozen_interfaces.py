"""The frozen interfaces this suite can hold without a stack
(docs/development/phase-001-interfaces.md): the REST routes' OpenAPI document (A12),
the run-record table's DDL (A6) and a Kafka connector's snapshot summary keys (I-1).
A change to any fails here until the test moves with it, which is the point of
freezing them.

Each validator runs in a subprocess under its own pin, so test-python's dependency
line, which the Makefile and CI each carry, stays as it is.
"""
# ponytail: a uv subprocess per validator; fold the pins into test-python's --with
# list when ci.yml next moves (step S21 owns it).

import ast
import json
import re
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
    assert data["responses"]["422"] == {"$ref": "#/components/responses/Unprocessable"}
    answer = doc["components"]["responses"]["Unprocessable"]["content"]["application/json"]["schema"]
    assert answer["allOf"][1]["properties"]["error"] == {"enum": ["as_of_unavailable", "result_too_large"]}
    assert "__base" not in OPENAPI.read_text()  # readers' names only (A11)


def test_filters_cannot_collide_and_a_page_never_hides_truncation():
    doc = yaml.safe_load(OPENAPI.read_text())
    data = doc["paths"]["/v1/data/{domain}/{product}/{table}"]["get"]
    names = {p["name"] for p in data["parameters"]}
    # filters are filter.<column>=, so a column named fields, limit, cursor or as_of filters too
    assert names == {"fields", "limit", "cursor", "as_of"} and "filter.<column>" in data["description"]
    page = doc["components"]["schemas"]["DataPage"]["properties"]
    assert page["snapshot"]["type"] == "string"  # an int64 id loses precision as a JavaScript number
    assert "result_too_large" in data["description"] and "never null" in page["next"]["description"]
    assert "k2gen/schemas/" not in OPENAPI.read_text() and "provisional" not in OPENAPI.read_text()


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
    ("duration_s", "double"), ("detail", "varchar"), ("dbt_invocation_id", "varchar"), ("openlineage_run_id", "varchar"),
]
RUN_RECORD_SCHEMA = ROOT / "contracts/schema/run-record.schema.json"
# The JSON type of each DDL type in one row; `inputs` is JSON text in its varchar column.
JSON_TYPE = {"varchar": "string", "bigint": "integer", "double": "number", "timestamp(6) with time zone": "string"}


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


def test_the_run_record_ddl_and_its_json_schema_are_one_row():
    # The two are each frozen; this holds them to each other, column for column and type for type.
    t = parse_run_record(RUN_RECORD)
    schema = json.loads(RUN_RECORD_SCHEMA.read_text())
    assert [n for n, _, _ in t["columns"]] == list(schema["properties"]) == schema["required"]
    for name, ddl_type, _ in t["columns"]:
        prop = schema["properties"][name]
        json_types = prop.get("type") or ("string" if all(isinstance(v, str) for v in prop["enum"]) else "?")  # an enum of strings
        json_types = {json_types} if isinstance(json_types, str) else set(json_types)
        expected = "array" if name == "inputs" else JSON_TYPE[ddl_type]
        assert expected in json_types, (name, ddl_type, json_types)
        assert json_types <= {expected, "null"}, (name, json_types)
    # recorded_at is the partition source: never null
    assert schema["properties"]["recorded_at"]["type"] == "string"


# I-1: the summary keys a Kafka connector commit carries, read from kafka.py's source (no Spark).
LAKE = ROOT / "docker/lake"
MODEL = ROOT / "platform/k2gen/k2gen/model.py"
SUMMARY_KEY = re.compile(r"k2\.[a-z0-9-]+")


def constants(source: str) -> dict:
    """A module's top-level `NAME = <literal>` assignments."""
    out = {}
    for n in ast.parse(source).body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            try:
                out[n.targets[0].id] = ast.literal_eval(n.value)
            except ValueError:
                pass
    return out


def kafka_keys(source: str) -> set:
    """Every k2.* key kafka.py names: a literal, one of its own constants, or one of offsets.py's
    (`O.`) or connector.py's (`C.`), a slice taken as written (`C.SUMMARY_KEYS[:3]`)."""
    own = constants(source)
    mods = {m: constants((LAKE / f).read_text()) for m, f in (("O", "offsets.py"), ("C", "connector.py"))}
    seen, skip = set(), set()

    def value(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return own.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return mods.get(node.value.id, {}).get(node.attr)
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            whole, s = value(node.value), node.slice
            if isinstance(whole, tuple):
                skip.add(id(node.value))  # the slice is what is written, not the whole tuple
                return whole[slice(*(ast.literal_eval(b) if b else None for b in (s.lower, s.upper, s.step)))]
        return None

    for node in ast.walk(ast.parse(source)):  # breadth first: a subscript before its operand
        if id(node) in skip:
            continue
        v = value(node)
        for k in v if isinstance(v, tuple) else (v,):
            if isinstance(k, str) and SUMMARY_KEY.fullmatch(k):
                seen.add(k)
    return seen


def test_kafka_py_writes_exactly_the_frozen_summary_keys():
    frozen = constants(MODEL.read_text())
    keys, source = frozen["KAFKA_SUMMARY_KEYS"], (LAKE / "kafka.py").read_text()
    assert kafka_keys(source) == {k for ks in keys.values() for k in ks}
    assert constants(source)["JOB_CONNECTOR"] == frozen["KAFKA_JOB"]
    # every commit carries the file connector's run keys but the sha256, as kafka.summary zips them
    assert keys["every"] == ("k2.job", *frozen["SNAPSHOT_SUMMARY_KEYS"][:3])
    assert set(keys) == {"every", "landing", "bronze"}


def test_a_key_kafka_py_starts_writing_is_seen():
    # Not a no-op: a new key by literal, or through offsets.py, fails the equality above.
    source = (LAKE / "kafka.py").read_text()
    assert "k2.extra" in kafka_keys(source + '\nprops = {"k2.extra": "1"}\n')
    assert "k2.kafka-backlog" in kafka_keys(source + "\nprops = {O.KAFKA_BACKLOG: 1}\n")
    assert "k2.source-sha256" not in kafka_keys(source)  # C.SUMMARY_KEYS[:3] is not the whole tuple
