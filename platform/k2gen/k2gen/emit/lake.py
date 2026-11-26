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
  nothing else, `SELECT <contract columns> FROM <base> FOR VERSION AS OF 'audited' WHERE EXISTS
  (SELECT 1 FROM <base>$refs WHERE "name" = 'audited' AND "type" = 'TAG')` (ADR-061 part 1 and its
  Outcome of 2026-11-26: the guard fails a read when the base is not a tagged table), and
  model.VIEWS. A policy is a Trino rule on the view's name, never view SQL, so a
  masked or filtered column is projected like any other. `create_view` and `view_update` render
  the publisher's statements from the same function, for the wrapper and the drift check. A view
  never shares its name with a table, and no product's namespace is the platform's or another
  product's: each (warehouse, namespace) has one owner, a product or the platform.
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
from collections.abc import Iterable, Mapping, Sequence
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
PLATFORM_OWNER = "platform"  # the owner of the run-record namespace; a product's namespaces are owned by its id

# model.PHYSICAL_TYPE's scalars in Spark 3.5's spelling, which Iceberg stores as Trino reads them.
# time(6) and uuid are missing: Spark has no type that creates either Iceberg column. TIMESTAMP_LTZ,
# never bare TIMESTAMP, whose meaning follows the session's spark.sql.timestampType.
_SPARK_SCALARS = {
    "boolean": "BOOLEAN", "integer": "INT", "bigint": "BIGINT", "real": "FLOAT", "double": "DOUBLE",
    "varchar": "STRING", "varbinary": "BINARY", "date": "DATE",
    "timestamp(6)": "TIMESTAMP_NTZ", "timestamp(6) with time zone": "TIMESTAMP_LTZ",
}
# k2-v2.schema.json#/$defs/partitionField, in Trino's spelling; Spark's names the time transforms
# in the plural and takes the width first.
_NAME = r"[a-z][a-z0-9_]*"
_PARTITION = re.compile(rf"(?:(year|month|day|hour)\(({_NAME})\)|(bucket|truncate)\(({_NAME}), ?([1-9][0-9]*)\)|({_NAME}))")
_CREATE = re.compile(r"^CREATE TABLE ([a-z0-9_]+)\.([a-z0-9_]+)\.([a-z0-9_]+) \(", re.M)


class NoCommonColumns(ValueError):
    """view_update on a published view: no column of C is in both the tagged and the tested
    snapshot with one type, so no view reads both. Never a first publish: the wrapper takes a
    deliberate path (readers lose the view from the commit to the second statement) and pages."""


def view_sql(view: View, columns: Sequence[str] | None = None) -> str:
    """The view's SQL (ADR-061 part 1): its columns, each quoted, in the contract's order, from the
    base through the reader catalog at the tag. `columns` keeps only those of view.columns it
    names (the update rule's intersections); None keeps all."""
    # Trino folds a name's case even quoted (`SELECT "a", "A"` is a view of a, a), so every name is
    # held to model.IDENTIFIER, as the schema holds the documents' names, and none appears twice.
    if len({c.casefold() for c in view.columns}) != len(view.columns):
        raise ValueError(f"{view.relation}: a column appears twice, ignoring case")
    if bad := [n for n in (*view.relation.split("."), *view.base.split("."), *view.columns) if not k2.IDENTIFIER.fullmatch(n)]:
        raise ValueError(f"{view.relation}: {bad[0]!r} is not a name a view can carry (model.IDENTIFIER)")
    wanted = set(view.columns if columns is None else columns)
    keep = tuple(c for c in view.columns if c in wanted)
    if not keep:
        raise ValueError(f"{view.relation}: a view selects at least one column")
    # The guard (ADR-061's Outcome of 2026-11-26): Trino 484 ignores a string version on a view, and
    # the builder owns every name in <fence>__base, so it could swap the base for a view of its own.
    # `<base>$refs` exists only while the base is an Iceberg table (Trino never resolves it as a
    # view, even one created under that name), so a base that is not a table fails the reader's
    # query, and a table without the tag fails the version clause.
    tag = sql_literal(k2.AUDITED_TAG)
    return (f"SELECT {', '.join(quote_ident(c) for c in keep)} FROM {quote_relation(view.base)} FOR VERSION AS OF {tag} "
            f"WHERE EXISTS (SELECT 1 FROM {quote_relation(view.base + '$refs')} "
            f"WHERE {quote_ident('name')} = {tag} AND {quote_ident('type')} = {sql_literal('TAG')})")


def create_view(view: View, columns: Sequence[str] | None = None) -> str:
    """What the publisher runs: the view through the build catalog, in the reader namespace
    (gate h1.01), as a definer view."""
    catalog, namespace, table = view.relation.split(".")
    build = f"{k2.build_catalog(catalog)}.{namespace}.{table}"  # catalog is a fence already
    return f"CREATE OR REPLACE VIEW {quote_relation(build)} SECURITY DEFINER AS {view_sql(view, columns)}"


def view_update(view: View, old: Mapping[str, str] | None, new: Mapping[str, str]) -> tuple[str | None, str]:
    """ADR-061 part 5: the two statements around the tag commit, in the order the wrapper runs
    them. C is view.columns; old maps the tagged snapshot's columns to their types (None: no tag
    yet), new the tested one's, each type as Trino's DESCRIBE spells it. A view stores its
    columns' types, so a column is common only when its name and type agree in both: a type
    change is a drop and an add. Before the commit, C and old and new; after it, C and new.
    Before is None on a first publish only, which creates the view after the tag; a published
    view with nothing in common raises NoCommonColumns. Both are CREATE OR REPLACE and depend
    only on the two snapshots, so a wrapper that crashed reruns from what the tag holds then:
    after the commit old is new, and both statements are the second."""
    after = create_view(view, list(new))
    if old is None:
        return None, after
    common = [c for c in view.columns if c in old and c in new and old[c] == new[c]]
    if not common:
        raise NoCommonColumns(f"{view.relation}: no column of the contract is in both the tagged and the tested snapshot with one type")
    return create_view(view, common), after


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
        if k2.fence(name) in k2.TRINO_RESERVED:  # docs.name-reserved-word
            raise ValueError(f"{name}: {k2.fence(name)} is a Trino reserved word (model.TRINO_RESERVED)")
    # The identity and retention rules the checks hold too, since an emitted name is an owner's.
    names = [p.name for p in model.products]
    if taken := sorted({n for n in names if names.count(n) > 1}):
        raise ValueError(f"{taken[0]}: two products share the name, and so its principals {k2.publisher(taken[0])} and {k2.builder(taken[0])}")
    for p in model.products:
        for t in p.tables:
            if t.contract_id != (own_id := k2.contract_id(p.domain, p.name, t.name)):
                raise ValueError(f"{t.path}: contract id {t.contract_id!r} names another table than its own, {own_id}")
            if t.retention is not None and (type(t.retention) is not int or t.retention < 1):
                raise ValueError(f"{t.path}: retention {t.retention!r} is not a whole number of days, at least 1")
    out: dict[str, str] = {}

    def put(path: str, text: str) -> None:
        if path in out:
            raise ValueError(f"{path}: emitted twice; two tables share one name")
        out[path] = text

    tables = [t for p in model.products for t in p.tables if t.build.kind in k2.LAKE_BUILDS]
    lake: dict[tuple[str, str, str], tuple[int, k2.Backup, k2.Ref]] = {}  # location -> retention, backup, ref
    owners: dict[tuple[str, str], str] = {}  # (warehouse, namespace) -> a product id, or PLATFORM_OWNER

    def own(warehouse: str, namespace: str, owner: str) -> None:
        if (first := owners.setdefault((warehouse, namespace), owner)) != owner:
            raise ValueError(f"{warehouse}.{namespace}: one namespace, two owners: {first} and {owner}")

    def keep(location: tuple[str, str, str], days: int, backup: k2.Backup, ref: k2.Ref, owner: str) -> None:
        if location in lake:
            raise ValueError(f"{'.'.join(location)}: two tables in one place")
        own(*location[:2], owner)
        lake[location] = (days, backup, ref)

    # The run record's namespace is the platform's in its domain's warehouse, declared or not
    # yet, so no product's reader or base namespace is it: its publisher would own the history.
    run_record = k2.RUN_RECORD_SQL.read_text()
    ((run_catalog, run_namespace, run_table),) = _CREATE.findall(run_record)
    run_domains = [d for d in sorted(domains) if k2.build_catalog(d) == run_catalog]
    for d in run_domains:
        own(k2.warehouse(d), run_namespace, PLATFORM_OWNER)

    for t in tables:
        domain, product, table = _parts(t)
        owner = k2.product_id(domain, product)
        if t.build.kind == "platform":
            # The run record is the platform's one table, from RUN_RECORD_SQL; a second would need its DDL decided.
            raise ValueError(f"{t.path}: a contract's platform table has no DDL here; the run record comes from {k2.RUN_RECORD_SQL.name}")
        days = RETENTION_FLOOR_DAYS if t.retention is None else t.retention
        keep(_in_warehouse(k2.input_relation(t), domain), days, t.backup, k2.tested_ref(t), owner)
        own(*_in_warehouse(k2.reader_relation(t), domain)[:2], owner)
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
            keep((k2.LAKE_WAREHOUSE, k2.fence(p.name), name), RETENTION_FLOOR_DAYS, PLATFORM_BACKUP, "main",
                 k2.product_id(p.domain, p.name))

    declared = {d.name for d in model.domains}
    for d in run_domains:
        if d in declared:
            put(k2.ddl_path("trino", k2.warehouse(d), run_namespace, run_table), run_record)
            keep((k2.warehouse(d), run_namespace, run_table), RETENTION_FLOOR_DAYS, PLATFORM_BACKUP, "main", PLATFORM_OWNER)

    audited = [t for t in tables if k2.tested_ref(t) == k2.AUDITED_TAG]
    for t in audited:
        if (location := _in_warehouse(k2.reader_relation(t), _parts(t)[0])) in lake:
            raise ValueError(f"{'.'.join(location)}: a view and a table at one name")
    views = sorted((View(relation=k2.reader_relation(t), base=k2.input_relation(t), columns=tuple(c.name for c in t.columns),
                         owner=k2.publisher(_parts(t)[1]))
                    for t in audited), key=lambda v: v.relation)
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
