"""The lake emitter (S15): what deploy creates in the lake, what readers query, what a backup copies.

For every table in model.LAKE_BUILDS (a serving table has no lake table and is skipped):

- model.ddl_path DDL, one CREATE TABLE per file, never IF NOT EXISTS, OR REPLACE, ALTER or DROP:
  a changed file applied to an existing table fails aloud at deploy, so no column leaves or
  joins a table silently. spark, warehouse k2 (Trino's `lake` is read-only): every connector
  table, and per connector product the platform tables of model.PLATFORM_TABLES (landing_files
  for a fetch kind, landing for kafka, audit_checks for both). trino: the run-record table,
  model.RUN_RECORD_SQL byte for byte, when the domain whose build catalog it names is declared.
  dbt and code outputs get none: their build creates them (model.ddl_path).
- model.view_sql_path per dbt or code output (model.tested_ref is `audited`): the view's SQL and
  nothing else, `SELECT <contract columns> FROM <base> FOR VERSION AS OF 'audited'` (ADR-061
  part 1), and model.VIEWS. A policy is a Trino rule on the view's name, never view SQL, so a
  masked or filtered column is projected like any other. `create_view` and `view_update` render
  the publisher's statements from the same function, for the wrapper and the drift check.
- model.CONTRACT_SCHEMAS, model.RETENTION (the contract's days, else the floor),
  model.STORAGE_PLAN and model.BACKUP_SET, the last two against their schemas.

Every name in emitted SQL goes through model.quote_ident or model.quote_relation and every value
through model.sql_literal; the one exception is the frozen run-record file, copied unchanged.
Spark reads those double-quoted names only with `spark.sql.ansi.doubleQuotedIdentifiers` (Spark
3.4 and later), so each Spark file sets it first and is applied in one session (`spark-sql -f`).
Spark refuses sql_literal's doubled quote (`'The key''s bytes'` is a syntax error on 3.5.9), so no
Spark file carries a COMMENT; descriptions reach readers through the catalog JSON (S18).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from typing import Any

from k2gen import model as k2
from k2gen.checks.docs import FETCH_KINDS, RETENTION_FLOOR_DAYS
from k2gen.model import Model, Table, View, quote_ident, quote_relation, sql_literal

SPARK_SESSION = ("SET spark.sql.ansi.enabled=true;", "SET spark.sql.ansi.doubleQuotedIdentifiers=true;")
# Every Spark-made table's properties, as the retired lake.sql set them (legacy/v3-lake-ingest/):
# copy-on-write, since no row is updated or deleted and merge-on-read puts delete files before readers.
TBLPROPERTIES = (
    ("format-version", "2"),
    ("write.format.default", "parquet"),
    ("write.parquet.compression-codec", "zstd"),
    ("write.distribution-mode", "hash"),
    ("write.delete.mode", "copy-on-write"),
    ("write.update.mode", "copy-on-write"),
    ("write.merge.mode", "copy-on-write"),
)
# The platform tables' backup: none can be rebuilt from an input (the fetch record, the audit's
# evidence, Kafka's records past its retention, the run history), so all are restored.
PLATFORM_BACKUP: k2.Backup = "restore"

# model.PHYSICAL_TYPE's scalars in Spark 3.5's spelling, which Iceberg stores as Trino reads them.
# time(6) and uuid are missing: Spark has no type that creates either Iceberg column.
_SPARK_SCALARS = {
    "boolean": "BOOLEAN", "integer": "INT", "bigint": "BIGINT", "real": "FLOAT", "double": "DOUBLE",
    "varchar": "STRING", "varbinary": "BINARY", "date": "DATE",
    "timestamp(6)": "TIMESTAMP_NTZ", "timestamp(6) with time zone": "TIMESTAMP",
}
# k2-v2.schema.json#/$defs/partitionField, in Trino's spelling; Spark's names the time transforms
# in the plural and takes the width first.
_NAME = r"[a-z][a-z0-9_]*"
_PARTITION = re.compile(rf"(?:(year|month|day|hour)\(({_NAME})\)|(bucket|truncate)\(({_NAME}), ?([1-9][0-9]*)\)|({_NAME}))")
_CREATE = re.compile(r"^CREATE TABLE ([a-z0-9_]+)\.([a-z0-9_]+)\.([a-z0-9_]+) \(", re.M)


def view_sql(view: View, columns: Sequence[str] | None = None) -> str:
    """The view's SQL (ADR-061 part 1): its columns, each quoted, in the contract's order, from the
    base through the reader catalog at the tag. `columns` keeps only those of view.columns it
    names (the update rule's intersections); None keeps all."""
    wanted = set(view.columns if columns is None else columns)
    keep = tuple(c for c in view.columns if c in wanted)
    if not keep:
        raise ValueError(f"{view.relation}: a view selects at least one column")
    return (f"SELECT {', '.join(quote_ident(c) for c in keep)} FROM {quote_relation(view.base)} "
            f"FOR VERSION AS OF {sql_literal(k2.AUDITED_TAG)}")


def create_view(view: View, columns: Sequence[str] | None = None) -> str:
    """What the publisher runs: the view through the build catalog, in the reader namespace
    (gate h1.01), as a definer view."""
    catalog, namespace, table = view.relation.split(".")
    build = f"{k2.build_catalog(catalog)}.{namespace}.{table}"  # catalog is a fence already
    return f"CREATE OR REPLACE VIEW {quote_relation(build)} SECURITY DEFINER AS {view_sql(view, columns)}"


def view_update(view: View, old: Sequence[str] | None, new: Sequence[str]) -> tuple[str | None, str]:
    """ADR-061 part 5: the two statements around the tag commit, in the order the wrapper runs
    them. C is view.columns; old the tagged snapshot's columns (None: no tag yet), new the tested
    one's. Before the commit, C and old and new; after it, C and new. Before is None on a first
    publish, which creates the view after the tag, and when no column is common to all three.
    Both are CREATE OR REPLACE, so a reconcile reruns them harmlessly."""
    common = [c for c in new if old is not None and c in old]
    before = create_view(view, common) if set(common) & set(view.columns) else None
    return before, create_view(view, new)


def _spark_type(physical_type: str, where: str) -> str:
    if problem := k2.physical_type_problem(physical_type):
        raise ValueError(f"{where}: {problem}")

    def scalar(t: str) -> str:
        if m := re.fullmatch(r"decimal\(([0-9]+),([0-9]+)\)", t):
            return f"DECIMAL({sql_literal(int(m[1]))},{sql_literal(int(m[2]))})"
        if t not in _SPARK_SCALARS:
            raise ValueError(f"{where}: Spark 3.5 cannot create an Iceberg {t} column; a Spark-made table cannot hold one")
        return _SPARK_SCALARS[t]

    if m := re.fullmatch(r"array\((.+)\)", physical_type):
        return f"ARRAY<{scalar(m[1])}>"
    if m := re.fullmatch(r"map\((.+?), (.+)\)", physical_type):
        return f"MAP<{scalar(m[1])}, {scalar(m[2])}>"
    return scalar(physical_type)


def _spark_partition(field: str, where: str) -> str:
    m = _PARTITION.fullmatch(field)
    if not m:
        raise ValueError(f"{where}: partition field {field!r} is not one k2-v2.schema.json allows")
    time, time_col, width, width_col, n, column = m.groups()
    if time:
        return f"{time}s({quote_ident(time_col)})"
    if width:
        return f"{width}({sql_literal(int(n))}, {quote_ident(width_col)})"
    return quote_ident(column)


def _spark_ddl(relation: str, columns: Iterable[tuple[str, str, bool]], partitioning: Sequence[str],
               sort: Sequence[str], where: str) -> str:
    """One Iceberg table in Spark's dialect, every name double-quoted under SPARK_SESSION."""
    if sort:
        # Iceberg's ALTER TABLE ... WRITE ORDERED BY has its own parser, which reads backquoted
        # names only (a double-quoted one is a syntax error on 1.8.1), and model.py has no quoting
        # for it. Refused rather than dropped: the contract's sort would silently not hold.
        raise ValueError(f"{where}: a write order on a Spark-made table cannot be written through model.quote_ident; "
                         "it needs a backquoting helper in model.py (the orchestrator's)")
    columns = list(columns)
    # Spark reads a backslash inside a double-quoted name as an escape, which quote_ident leaves
    # alone (`"a\\"` runs on past its closing quote on 3.5.9), so a Spark name is held to
    # model.IDENTIFIER as well; the schema holds every document name to it already.
    if bad := [n for n in (*relation.split("."), *(c for c, _, _ in columns)) if not k2.IDENTIFIER.fullmatch(n)]:
        raise ValueError(f"{where}: {bad[0]!r} is not a name Spark DDL can quote safely")
    body = ",\n".join(f"    {quote_ident(name)} {_spark_type(t, f'{where}: column {name}')}{' NOT NULL' if required else ''}"
                      for name, t, required in columns)
    lines = [*SPARK_SESSION, f"CREATE TABLE {quote_relation(relation)} (", body, ")", "USING iceberg"]
    if partitioning:
        lines.append(f"PARTITIONED BY ({', '.join(_spark_partition(f, where) for f in partitioning)})")
    lines.append(f"TBLPROPERTIES ({', '.join(f'{sql_literal(k)} = {sql_literal(v)}' for k, v in TBLPROPERTIES)});")
    return "\n".join(lines) + "\n"


def _in_warehouse(relation: str, domain: str) -> tuple[str, str, str]:
    """A Trino relation as (warehouse, namespace, table): `lake` is warehouse k2, a domain's
    reader catalog its warehouse."""
    catalog, namespace, table = relation.split(".")
    warehouse = {k2.LAKE: k2.LAKE_WAREHOUSE, k2.reader_catalog(domain): k2.warehouse(domain)}[catalog]
    return warehouse, namespace, table


def _parts(t: Table) -> tuple[str, str, str]:
    domain, product, table = t.contract_id.split(".")
    return domain, product, table


def _json(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def emit(model: Model) -> dict[str, str]:
    contracts = [t.contract_id.split(".") for p in model.products for t in p.tables]
    domains = {d.name for d in model.domains} | {p.domain for p in model.products} | {c[0] for c in contracts}
    products = {p.name for p in model.products} | {c[1] for c in contracts}
    for name, problem in [*((d, k2.domain_name_problem(d)) for d in sorted(domains)), *((p, k2.product_name_problem(p)) for p in sorted(products))]:
        if problem:  # the checks refuse these too (S05); k2gen release does not run them
            raise ValueError(f"{name}: {problem}")
    out: dict[str, str] = {}

    def put(path: str, text: str) -> None:
        if path in out:
            raise ValueError(f"{path}: emitted twice; two tables share one name")
        out[path] = text

    tables = [t for p in model.products for t in p.tables if t.build.kind in k2.LAKE_BUILDS]
    lake: dict[tuple[str, str, str], tuple[int, k2.Backup, k2.Ref]] = {}  # location -> retention, backup, ref

    def keep(location: tuple[str, str, str], days: int, backup: k2.Backup, ref: k2.Ref) -> None:
        if location in lake:
            raise ValueError(f"{'.'.join(location)}: two tables in one place")
        lake[location] = (days, backup, ref)

    for t in tables:
        domain, product, table = _parts(t)
        if t.build.kind == "platform":
            # The run record is the platform's one table, from RUN_RECORD_SQL; a second would need its DDL decided.
            raise ValueError(f"{t.path}: a contract's platform table has no DDL here; the run record comes from {k2.RUN_RECORD_SQL.name}")
        days = RETENTION_FLOOR_DAYS if t.retention is None else t.retention
        keep(_in_warehouse(k2.input_relation(t), domain), days, t.backup, k2.tested_ref(t))
        if t.build.kind == "connector":
            put(k2.ddl_path("spark", k2.LAKE_WAREHOUSE, k2.fence(product), table),
                _spark_ddl(k2.lake_relation(product, table), ((c.name, c.physical_type, c.required) for c in t.columns),
                           t.partitioning, t.sort, t.path))

    connected = sorted(((p, p.connector.kind) for p in model.products if p.connector), key=lambda pk: pk[0].id)
    for p, kind in connected:
        landing = k2.LANDING_FILES if kind in FETCH_KINDS else k2.KAFKA_LANDING
        for name in (landing, k2.AUDIT_CHECKS):
            pt = k2.PLATFORM_TABLES[name]
            put(k2.ddl_path("spark", k2.LAKE_WAREHOUSE, k2.fence(p.name), name),
                _spark_ddl(k2.lake_relation(p.name, name), ((c.name, c.type, False) for c in pt.columns),
                           pt.partitioning, (), f"model.PLATFORM_TABLES[{name!r}]"))
            keep((k2.LAKE_WAREHOUSE, k2.fence(p.name), name), RETENTION_FLOOR_DAYS, PLATFORM_BACKUP, "main")

    run_record = k2.RUN_RECORD_SQL.read_text()
    ((catalog, namespace, table),) = _CREATE.findall(run_record)
    for d in model.domains:
        if k2.build_catalog(d.name) == catalog:
            put(k2.ddl_path("trino", k2.warehouse(d.name), namespace, table), run_record)
            keep((k2.warehouse(d.name), namespace, table), RETENTION_FLOOR_DAYS, PLATFORM_BACKUP, "main")

    views = sorted((View(relation=k2.reader_relation(t), base=k2.input_relation(t), columns=tuple(c.name for c in t.columns),
                         owner=k2.publisher(_parts(t)[1]))
                    for t in tables if k2.tested_ref(t) == k2.AUDITED_TAG), key=lambda v: v.relation)
    for v in views:
        put(k2.view_sql_path(v.relation), view_sql(v) + "\n")
    put(k2.VIEWS, _json({"views": [asdict(v) for v in views]}))

    put(k2.CONTRACT_SCHEMAS, _json({
        ".".join(_in_warehouse(k2.input_relation(t), _parts(t)[0])): {
            "columns": [{"name": c.name, "type": c.physical_type, "required": c.required} for c in t.columns],
            "partitioning": list(t.partitioning), "sort": list(t.sort)}
        for t in tables}))
    put(k2.RETENTION, _json({".".join(loc): days for loc, (days, _, _) in lake.items()}))

    namespaces: dict[str, set[str]] = {w: set() for w in (k2.LAKE_WAREHOUSE, *(k2.warehouse(d.name) for d in model.domains))}
    readers = [_in_warehouse(k2.reader_relation(t), _parts(t)[0]) for t in tables]
    for warehouse, namespace, _ in [*lake, *readers]:
        namespaces.setdefault(warehouse, set()).add(namespace)
    prefixes = sorted(k2.landing_prefix(p.domain, p.name) for p, kind in connected if kind in FETCH_KINDS)
    plan = {"warehouses": [{"name": w, "remoteSigningEnabled": False, "namespaces": sorted(ns)} for w, ns in sorted(namespaces.items())],
            "landing": {"bucket": k2.LANDING_BUCKET, "prefixes": prefixes}}
    backup = {"tag": model.provenance.tag, "entries": [
        *({"kind": "landing", "bucket": k2.LANDING_BUCKET, "prefix": prefix} for prefix in prefixes),
        *({"kind": "table", "table": ".".join(loc), "mode": mode, "ref": ref} for loc, (_, mode, ref) in sorted(lake.items())),
        {"kind": "lakekeeper-db"}, {"kind": "releases"}]}
    for path, schema, doc in ((k2.STORAGE_PLAN, "storage-plan", plan), (k2.BACKUP_SET, "backup-set", backup)):
        if errors := k2.schema_errors(schema, doc):
            raise ValueError(f"{path}: " + "; ".join(errors))
        put(path, _json(doc))
    return out
