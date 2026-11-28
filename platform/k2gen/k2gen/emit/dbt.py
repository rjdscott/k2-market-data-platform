"""Emits compiled/dbt/ (S17): one dbt project, named model.DBT_PROJECT, holding every product
of the release that dbt builds. A product's documents become:

    models/<domain fence>/<fence>/<fence>__<table>.sql   its models/<table>.sql, byte for byte
    models/<domain fence>/<fence>/_models.yml            each dbt table's model contract and tests,
                                                         from its ODCS contract; its seeds' aliases
    models/<domain fence>/<fence>/_unit_tests.yml        its unit_tests.yml, byte for byte
    seeds/<domain fence>/<fence>/<fence>__<stem>.csv     each declared seed, byte for byte
    models/_sources.yml     every table a product reads: its own bronze on `lake`, and each input
                            port's table at model.input_relation, which k2_input pins
    dbt_project.yml         the defaults, and each product folder routed to <domain>_build.<fence>__base
    selectors.yml           one selector per product, named model.dag_id: the build the wrapper runs
    macros/                 k2_input.sql and k2_results.sql from dbt/macros/, byte for byte; the
                            schema-name rule; the generic tests; k2_test_results, the union of
                            every builder's results table that model.QUALITY_RESULTS's model is

A table's model is model.dbt_model, `<fence>__<table>`, with `alias: <table>`, so dbt builds it
at model.build_relation; a seed likewise, model.dbt_seed, `<fence>__<stem>` with `alias: <stem>`. The model
contract is enforced and its columns are the ODCS columns, name for name and type for type, so
dbt's contract is the table contract. Readers never see these tables: S15 emits the views over
the `audited` tag and the wrapper applies them (ADR-061). Build serving has no lake table and so
no model; a product with no dbt table and no seed has nothing here. The tree holds no credential
and no env_var: the wrapper's profile names the builder (dbt/profiles.yml, S13).

    python -m k2gen.emit.dbt <documents root> <out>   # <out>/compiled/dbt/ of a working tree (make dbt-dev)
"""

from __future__ import annotations

import math
import re
import shutil
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from k2gen import model as k2
from k2gen.checks.sql import ALLOWED_CONFIG
from k2gen.model import Model, Product, Quality, Table

ROOT = "compiled/dbt"
# The frozen macros (A7), shipped as the repository has them: the one tested is the one compiled.
COPIED_MACROS = ("k2_input.sql", "k2_results.sql")
MACRO_DIR = k2.REPO / "dbt" / "macros"
HEADER = "# Compiled by k2gen (platform/k2gen/k2gen/emit/dbt.py) from the release's documents. Never edit it: change the documents.\n"
_RELATIONSHIP = re.compile(r"([^/]+)/([^/]+)/([^/]+)\.odcs\.yaml#/schema/([^/]+)/properties/([^/]+)")

PROJECT_COMMENT = """\
# The defaults below hold for every product; a model's own {{ config(...) }} wins over them.
#   materialized table: a product model is a table unless it says incremental (or a view, which
#     is then an invoker view: never a definer view, ADR-061).
#   on_table_exists replace: a table is rebuilt with CREATE OR REPLACE, one Iceberg table whose
#     history keeps every build (spike f).
#   incremental_strategy merge, with unique_key the contract's primaryKey (set per model): the
#     key holds every partition column, and incremental_predicates is never used (spike a).
#   on_schema_change append_new_columns: a column a contract adds is added to the table.
#   contract enforced: each model's columns and types are its ODCS contract's (_models.yml).
#   populate_cache false: dbt looks up only the relations a node needs; a builder sees no other
#     product's namespaces, so listing every schema of the project would fail.
#   on-run-end k2_results: every test of the run, one row each, into <fence>__base.dq_test_results.
# Each product folder is built into <domain>_build.<fence>__base, as dbt_<fence>, the only
# principal that writes there; macros/k2_naming.sql takes the schema as written.
"""

NAMING_MACRO = """\
{#- Where a model or seed is built: the +database and +schema its product folder sets in
    dbt_project.yml, <domain>_build and <fence>__base, the schema as written (dbt's own rule would
    prefix the profile's schema). A model or seed outside a product folder is a compiler error,
    never a write elsewhere. A test keeps dbt's default: it writes nothing. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- if custom_schema_name is not none -%}
    {{ custom_schema_name | trim }}
  {%- elif node.resource_type in ('model', 'seed', 'snapshot') -%}
    {{ exceptions.raise_compiler_error(node.resource_type ~ " " ~ node.name ~ " is in no product folder of dbt_project.yml") }}
  {%- else -%}
    {{ default__generate_schema_name(custom_schema_name, node) }}
  {%- endif -%}
{%- endmacro %}
"""

TESTS_MACRO = """\
{#- The generic tests k2gen adds to dbt's own (not_null, unique, relationships).

    k2_unique_key(model, columns): fails on every combination of the columns more than one row
    holds: a contract's primaryKey of two or more columns (one column is dbt's unique).

    k2_input_relationships(model, column_name, product_fence, table, field): dbt's relationships
    test against another product's table read through k2_input, so the test reads the snapshot
    the build read, never that table's untested main.

    k2_quality(model, query, holds): an ODCS sql or library quality rule. The query, with
    ${table} as the model, returns one column, the measured value; the test returns each value
    for which `holds`, the rule's thresholds as a condition on metric_value, is not true, so a
    null value fails too. k2gen writes both arguments (emit/dbt.py quality_test). -#}
{% test k2_unique_key(model, columns) -%}
  {%- set key = [] -%}
  {%- for c in columns -%}{%- do key.append(adapter.quote(c)) -%}{%- endfor -%}
select {{ key | join(', ') }}, count(*) as row_count
from {{ model }}
group by {{ key | join(', ') }}
having count(*) > 1
{%- endtest %}

{% test k2_input_relationships(model, column_name, product_fence, table, field) -%}
  {{ default__test_relationships(model, column_name, k2_input(product_fence, table), field) }}
{%- endtest %}

{% test k2_quality(model, query, holds) -%}
select metric_value
from ({{ query | replace('${table}', model) }}) as k2_quality (metric_value)
where not coalesce({{ holds }}, false)
{%- endtest %}
"""


class _Dumper(yaml.SafeDumper):
    """No anchors or aliases: dbt reads them, but k2gen's own loader (model.parse) refuses them."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _yaml(doc: Any, comment: str = "") -> str:
    return HEADER + comment + yaml.dump(doc, Dumper=_Dumper, sort_keys=False, width=120, allow_unicode=True)


def _folder(p: Product) -> str:
    return f"{k2.fence(p.domain)}/{k2.fence(p.name)}"


def _dbt_tables(p: Product) -> list[Table]:
    return [t for t in p.tables if t.build.kind == "dbt"]


def _array(values: tuple[str, ...]) -> str:
    """A Trino ARRAY of strings, as dbt-trino's `properties` passes it into WITH (...)."""
    return "ARRAY[" + ", ".join(k2.sql_literal(v) for v in values) + "]"


def _key(t: Table) -> list[str]:
    return [c.name for c in sorted((c for c in t.columns if c.primary_key), key=lambda c: c.primary_key_position or 0)]


# ODCS's comparison operators as SQL on the measured value; between and not between apart.
QUALITY_TABLE = "${table}"
_COMPARE = {"mustBe": "=", "mustNotBe": "<>", "mustBeGreaterThan": ">", "mustBeGreaterOrEqualTo": ">=",
            "mustBeLessThan": "<", "mustBeLessOrEqualTo": "<="}
_BETWEEN = {"mustBeBetween": "between", "mustNotBeBetween": "not between"}
assert set(_COMPARE) | set(_BETWEEN) == set(k2.QUALITY_OPERATORS)
# ODCS's library metrics a table-level rule takes; nullValues, missingValues and invalidValues
# are column rules in ODCS, and K2 reads quality on the table only (model.load).
LIBRARY_METRICS = ("rowCount", "duplicateValues")


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"a threshold is a finite number here, not {value!r}")
    return repr(value).upper() if isinstance(value, float) else str(value)


def _holds(thresholds: Mapping[str, Any]) -> str:
    """Every threshold the rule states, as one condition on metric_value."""
    if not thresholds:
        raise ValueError(f"it states no threshold: one of {', '.join(k2.QUALITY_OPERATORS)}")
    parts = []
    for op, value in thresholds.items():
        if op in _COMPARE:
            parts.append(f"metric_value {_COMPARE[op]} {_number(value)}")
            continue
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"{op} takes two numbers, the smallest first")
        low, high = _number(value[0]), _number(value[1])
        if not value[0] < value[1]:
            raise ValueError(f"{op} takes two numbers, the smallest first, not {value}")
        parts.append(f"metric_value {_BETWEEN[op]} {low} and {high}")
    return " and ".join(parts)


def _query(q: Quality, columns: Sequence[str]) -> str:
    """The rule's query, reading the table as ${table}: a sql rule's own, or a library metric's."""
    if q.type == "sql":
        if not isinstance(q.query, str) or QUALITY_TABLE not in q.query:
            raise ValueError(f"a sql rule's query reads the table as {QUALITY_TABLE}")
        if re.search(r"\{[{%#]|;", q.query) or q.query.replace(QUALITY_TABLE, "").count("${"):
            raise ValueError(f"a sql rule's query is one SELECT with no Jinja and no placeholder but {QUALITY_TABLE}, since dbt renders a test's arguments")
        return q.query
    if q.metric not in LIBRARY_METRICS:
        raise ValueError(f"library metric {q.metric}: a table's rule takes {' or '.join(LIBRARY_METRICS)}; "
                         "nullValues, missingValues and invalidValues are column rules, which K2 states as a column's "
                         "required, or a custom dbt rule (accepted_values)")
    unit = q.unit or "rows"
    if q.metric == "rowCount":
        if q.arguments or unit != "rows":
            raise ValueError("rowCount takes no arguments and counts rows")
        return f"select count(*) from {QUALITY_TABLE}"
    key = (q.arguments or {}).get("properties")
    if set(q.arguments or {}) != {"properties"} or not isinstance(key, list) or not key or not set(key) <= set(columns):
        raise ValueError(f"duplicateValues takes arguments.properties, a list of the table's columns ({', '.join(columns)})")
    if unit not in ("rows", "percent"):
        raise ValueError(f"duplicateValues counts rows or a percent of them, not {unit}")
    groups = f"(select count(*) as n from {QUALITY_TABLE} group by {', '.join(k2.quote_ident(c) for c in key)})"
    measure = "coalesce(sum(n - 1), 0)" if unit == "rows" else "coalesce(100.0 * sum(n - 1) / sum(n), 0)"
    return f"select {measure} from {groups} as k2_groups"


def quality_test(q: Quality, build: str, columns: Sequence[str]) -> dict[str, Any] | None:
    """One ODCS quality rule of a table with this build and these columns, as the dbt data test
    its model holds; None for a rule no dbt test carries (text: words; custom k2-connector on a
    connector table: the bronze audit's, pipeline.json `audits`). Any form K2 does not run is a
    ValueError, so no rule is ever dropped: S05 reports it first (docs.quality-unsupported).

    custom, engine dbt: its implementation, one dbt data test (a mapping, or its YAML text).
    sql: k2_quality over its query. library: k2_quality over the metric's query, rowCount (rows)
    or duplicateValues (arguments.properties; rows or percent). Every must* threshold of ODCS v3.2.0
    is supported, with numbers; a string or other value is not. A rule with a name gives the test
    that name (dbt's `name:`), so dbt's node, the wrapper's `warned` and dq_test_results.test_name
    call it what its contract does; a rule with none keeps the name dbt makes of its arguments."""
    if q.type == "text":
        return None
    if q.type == "custom" and q.engine == "k2-connector":
        if build == "connector":
            return None
        raise ValueError(f"a k2-connector rule is a bronze audit, and a {build} table has none")
    if build != "dbt":
        raise ValueError(f"a {q.type} rule runs as a dbt test, and dbt does not build a {build} table")
    if q.type == "custom":
        entry = k2.parse("implementation", q.implementation.encode()) if isinstance(q.implementation, str) else q.implementation
        if q.engine != "dbt" or not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError("a dbt implementation is one data test, {<test>: {...}}")
        ((test, body),) = entry.items()
    else:
        test, body = "k2_quality", {"arguments": {"query": _query(q, columns), "holds": _holds(q.thresholds)}}
    if not q.name:
        return {test: body}
    body = {} if body is None else body  # `not_null:` with nothing under it
    if not isinstance(body, dict):
        raise ValueError(f"a dbt implementation's {test} takes a mapping, which holds the rule's name")
    return {test: {"name": q.name, **{k: v for k, v in body.items() if k != "name"}}}


def _quality_tests(t: Table) -> Iterator[Any]:
    """Each ODCS quality rule of a dbt table that a dbt test carries (quality_test). Two rules of one
    table with the same name are refused: dbt names the test by it, and refuses the pair itself."""
    names = [q.name for q in t.quality if q.name]
    if len(names) != len(set(names)):
        raise ValueError(f"{t.path}: two quality rules are named {sorted({n for n in names if names.count(n) > 1})}; a rule's name is its test's, once per table")
    for q in t.quality:
        try:
            test = quality_test(q, t.build.kind, [c.name for c in t.columns])
        except (ValueError, k2.DocumentError) as e:
            raise ValueError(f"{t.path}: quality {q.name or q.description}: {e}") from None
        if test is not None:
            yield test


def _relationship(m: Model, p: Product, t: Table, column: str, to: str) -> dict[str, Any]:
    """dbt's relationships test of a foreign key, against the relation the model itself reads: its
    own model by ref, its own bronze by source, an input port's table through k2_input, so the test
    reads the snapshot the build read."""
    match = _RELATIONSHIP.fullmatch(to)
    if not match:
        raise ValueError(f"{t.path}: relationship {to} is not <domain>/<product>/<table>.odcs.yaml#/schema/<table>/properties/<column>")
    target_id, field = k2.contract_id(match[1], match[2], match[3]), match[5]
    target = _table(m, target_id)
    if target_id.startswith(f"{p.id}.") and target.build.kind == "dbt":
        relation = f"ref('{k2.dbt_model(p.name, target.name)}')"
    elif target_id.startswith(f"{p.id}.") and target.build.kind == "connector":
        relation = f"source('{k2.fence(p.name)}', '{target.name}')"
    elif target_id in {port.contract_id for port in p.input_ports}:
        return {"k2_input_relationships": {"arguments": {"product_fence": k2.fence(match[2]), "table": target.name, "field": field}}}
    else:
        raise ValueError(f"{t.path}: {column} refers to {target_id}, which {p.id} neither builds nor reads; add it as an input port")
    return {"relationships": {"arguments": {"to": relation, "field": field}}}


def _table(m: Model, contract_id: str) -> Table:
    for p in m.products:
        for t in p.tables:
            if t.contract_id == contract_id:
                return t
    raise ValueError(f"{contract_id}: no table of the release has this contract id")


def _model(m: Model, p: Product, t: Table) -> dict[str, Any]:
    """One model's properties: the contract's columns, its constraints and its tests."""
    sql = p.models[f"{t.name}.sql"]
    own = k2.model_config(sql)
    # A model's own config() wins over everything here, so it may set only what S07 allows: never
    # where it is built, its name, its contract or incremental_predicates (spike a).
    refused = sorted(own.keys() - set(ALLOWED_CONFIG))
    if refused:
        raise ValueError(f"{p.path}/models/{t.name}.sql: config({', '.join(refused)}=...): a model sets only {', '.join(ALLOWED_CONFIG)}")
    config: dict[str, Any] = {"alias": t.name}
    properties = {k: _array(v) for k, v in (("partitioning", t.partitioning), ("sorted_by", t.sort)) if v}
    if properties:
        config["properties"] = properties
    key = _key(t)
    if own.get("materialized") == "incremental" and key:
        config["unique_key"] = key
    config["meta"] = {"k2": {"contract": t.contract_id, "reader": k2.reader_relation(t)}}
    columns = []
    for c in t.columns:
        column: dict[str, Any] = {"name": c.name}
        if c.description is not None:
            column["description"] = c.description
        column["data_type"] = c.physical_type
        tests: list[Any] = []
        if c.required:
            column["constraints"] = [{"type": "not_null"}]
            tests.append("not_null")
        if c.unique or key == [c.name]:
            tests.append("unique")
        tests += [_relationship(m, p, t, c.name, r.to) for r in c.relationships]
        if tests:
            column["data_tests"] = tests
        columns.append(column)
    entry: dict[str, Any] = {"name": k2.dbt_model(p.name, t.name), "description": t.purpose, "config": config, "columns": columns}
    tests = [{"k2_unique_key": {"arguments": {"columns": key}}}] if len(key) > 1 else []
    tests += list(_quality_tests(t))
    if tests:
        entry["data_tests"] = tests
    return entry


def _properties(m: Model, p: Product) -> str:
    doc: dict[str, Any] = {"version": 2}
    if _dbt_tables(p):
        doc["models"] = [_model(m, p, t) for t in _dbt_tables(p)]
    if p.seeds:
        doc["seeds"] = [{"name": k2.dbt_seed(p.name, s), "description": f"The synthetic seed {p.path}/seeds/{s}.",
                         "config": {"alias": _stem(s)}} for s in p.seeds]
    return _yaml(doc)


def _stem(seed: str) -> str:
    return seed.removesuffix(".csv")


def _sources(m: Model, products: list[Product]) -> str:
    """Every table a dbt product reads, one source per producer fence and relation namespace: the
    source name is what k2_input(product_fence, table) and source() take."""
    read: dict[str, Table] = {}
    for p in products:
        read |= {t.contract_id: t for t in p.tables if t.build.kind == "connector"}
        read |= {port.contract_id: _table(m, port.contract_id) for port in p.input_ports}
    blocks: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for contract_id in sorted(read):
        t = read[contract_id]
        database, schema, identifier = k2.input_relation(t).split(".")
        columns = [{"name": c.name, "data_type": c.physical_type, **({"description": c.description} if c.description else {})} for c in t.columns]
        entry = {"name": identifier, "description": t.purpose, "meta": {"k2": {"contract": contract_id, "tested_ref": k2.tested_ref(t)}},
                 "columns": columns}
        blocks.setdefault((k2.fence(contract_id.split(".")[1]), database, schema), []).append(entry)
    return _yaml({"version": 2, "sources": [{"name": name, "database": database, "schema": schema, "tables": tables}
                                            for (name, database, schema), tables in sorted(blocks.items())]})


def _routes(products: list[Product]) -> dict[str, dict[str, dict[str, str]]]:
    """Each product folder to its build catalog and base namespace, under its domain folder."""
    routes: dict[str, dict[str, dict[str, str]]] = {}
    for p in products:
        routes.setdefault(k2.fence(p.domain), {})[k2.fence(p.name)] = {
            "+database": k2.build_catalog(p.domain), "+schema": k2.base_namespace(p.name)}
    return dict(sorted(routes.items()))


def _project(products: list[Product]) -> str:
    doc = {
        "name": k2.DBT_PROJECT,
        "version": "1.0.0",
        "profile": "k2",  # the wrapper's, as the builder: dbt/profiles.yml (S13)
        "config-version": 2,
        "require-dbt-version": [">=1.12.0", "<1.13.0"],  # dbt-core, never Fusion; dbt/pyproject.toml pins 1.12.7
        "model-paths": ["models"],
        "macro-paths": ["macros"],
        "seed-paths": ["seeds"],
        "flags": {"populate_cache": False, "require_certificate_validation": True},
        "on-run-end": ["{{ k2_results(results) }}"],
        "models": {k2.DBT_PROJECT: {
            "+materialized": "table",
            "+on_table_exists": "replace",
            "+view_security": "invoker",
            "+incremental_strategy": "merge",
            "+on_schema_change": "append_new_columns",
            "+contract": {"enforced": True},
            **_routes([p for p in products if _dbt_tables(p)])}},
        "seeds": {k2.DBT_PROJECT: _routes([p for p in products if p.seeds])},
    }
    return _yaml(doc, PROJECT_COMMENT)


RESULTS_MACRO = """\
{#- k2_test_results(): every builder's test results (dbt/macros/k2_results.sql), in one union, the
    model of model.QUALITY_RESULTS (ADR-046). Generated by k2gen from the release's products,
    model.test_results_writers: one branch per product, its <domain>.<fence>__base.dq_test_results
    read through the reader catalog at main, which Trino's rules let only that model's builder
    read. At dbt build or run, a branch whose table does not exist yet (the product has not built
    with a test) is left out rather than failing the build; parse, compile and docs render every
    branch and reach no warehouse. The first branch is empty and types the columns, so the model
    builds with no results anywhere. A branch keeps only its own builder's rows. -#}
{% macro k2_test_results() -%}
"""
RESULTS_EXISTS = """
{% macro k2_test_results_exist(catalog, schema) -%}
  {{- return(not (execute and flags.WHICH in ('build', 'run'))
      or adapter.get_relation(database=catalog, schema=schema, identifier='""" + k2.TEST_RESULTS + """') is not none) -}}
{%- endmacro %}
"""


def _results_macro(m: Model) -> str:
    """macros/k2_test_results.sql: the union of every writer's TEST_RESULTS, each name quoted."""
    columns = k2.TEST_RESULTS_TABLE.columns
    names = ", ".join(k2.quote_ident(c.name) for c in columns)
    lines = ["select cast(null as varchar) as " + k2.quote_ident("product_id") + ", "
             + ", ".join(f"cast(null as {c.type}) as {k2.quote_ident(c.name)}" for c in columns) + " where false"]
    for p in k2.test_results_writers(m):
        catalog, schema, _ = k2.base_relation(p.domain, p.name, k2.TEST_RESULTS).split(".")
        lines += [f"{{%- if k2_test_results_exist('{catalog}', '{schema}') %}}", "union all",
                  f"select {k2.sql_literal(p.id)} as {k2.quote_ident('product_id')}, {names}"
                  f" from {k2.quote_relation(k2.base_relation(p.domain, p.name, k2.TEST_RESULTS))}"
                  f" where {k2.quote_ident('writer')} = {k2.sql_literal(k2.builder(p.name))}", "{%- endif %}"]
    return RESULTS_MACRO + "\n".join(lines) + "\n{%- endmacro %}\n" + RESULTS_EXISTS


def _selectors(products: list[Product]) -> str:
    return _yaml({"selectors": [{
        "name": k2.dag_id(p.name),
        "description": f"{p.id}: its seeds and models, with their tests and unit tests; built as {k2.builder(p.name)} (the wrapper's dbt build --selector).",
        "definition": {"method": "fqn", "value": f"{k2.DBT_PROJECT}.{_folder(p).replace('/', '.')}"},
    } for p in products]})


def emit(model: Model) -> dict[str, str]:
    m = model
    products = [p for p in m.products if _dbt_tables(p) or p.seeds]
    out = {
        f"{ROOT}/dbt_project.yml": _project(products),
        f"{ROOT}/selectors.yml": _selectors(products),
        f"{ROOT}/models/_sources.yml": _sources(m, products),
        f"{ROOT}/macros/k2_naming.sql": NAMING_MACRO,
        f"{ROOT}/macros/k2_tests.sql": TESTS_MACRO,
        f"{ROOT}/macros/k2_test_results.sql": _results_macro(m),
    }
    out |= {f"{ROOT}/macros/{name}": (MACRO_DIR / name).read_text() for name in COPIED_MACROS}
    for p in products:
        folder = _folder(p)
        out[f"{ROOT}/models/{folder}/_models.yml"] = _properties(m, p)
        out |= {f"{ROOT}/models/{folder}/{k2.dbt_model(p.name, t.name)}.sql": p.models[f"{t.name}.sql"] for t in _dbt_tables(p)}
        if p.unit_tests is not None:
            out[f"{ROOT}/models/{folder}/_unit_tests.yml"] = p.unit_tests
        out |= {f"{ROOT}/seeds/{folder}/{k2.dbt_seed(p.name, s)}.csv": data.decode("utf-8") for s, data in p.seeds.items()}
    leaked = [path for path, text in out.items() if "env_var" in text]
    if leaked:  # S07 refuses it in a model; the tree as a whole is held to it here
        raise ValueError(f"env_var in {', '.join(leaked)}: the compiled dbt tree reads no environment")
    return out


def _documents(root: Path) -> k2.DocumentSet:
    """The files under `root` at k2.DOCUMENT_ROOTS, as a tag's document set holds them."""
    documents: dict[str, bytes] = {}
    for top in k2.DOCUMENT_ROOTS:
        base = root / top
        paths = sorted(base.rglob("*")) if top.endswith("/") else [base]
        documents |= {p.relative_to(root).as_posix(): p.read_bytes() for p in paths if p.is_file()}
    provenance = k2.Provenance("git", "working-tree", "0" * 40, "k2gen emit.dbt", "1970-01-01T00:00:00Z",
                                  k2.set_sha256(documents), ())
    return k2.DocumentSet(documents, provenance)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m k2gen.emit.dbt <documents root> <out>", file=sys.stderr)
        return 2
    root, out = Path(argv[0]), Path(argv[1])
    try:
        files = emit(k2.load(_documents(root)))
    except ValueError as e:  # model.DocumentError is one
        print(f"k2gen emit.dbt: {e}", file=sys.stderr)
        return 1
    shutil.rmtree(out / ROOT, ignore_errors=True)
    for path, text in files.items():
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_text(text)
    print(out / ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
