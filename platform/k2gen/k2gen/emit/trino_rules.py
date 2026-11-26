"""Emits compiled/trino/rules.json (Trino 484 file-based access control) and compiled/trino/groups.txt
(the file group provider's `<fence>__<role>:<member>,...` lines), from the model alone (A8, ADR-059,
ADR-061). Filled by step S12.

Default deny. Every section is a list of rules matched top to bottom, the first match deciding, and
no section ends in an allow that matches everyone: a principal, catalog, schema, table, property or
query no rule names is refused. Every name in a rule is a whole-name regex of exact names, each
through re.escape (Trino matches a rule's regex against the whole name), so `t` never matches
`t$files`, `fence` never `fence__base` or `fence_x`, and `svc.platform` never `svcXplatform`.

- Readers (the members of `<fence>__<role>`, from grants.yaml) reach their reader catalogs read-only
  and get SELECT on each exact reader relation (model.reader_relation) of a table offering their
  role, with that role's policy as the rule's row filter (delay on the declared time column,
  rowScope with each value through model.sql_literal) and column masks to typed nulls. No reader
  rule names a base namespace or a `$` metadata table.
- A builder `dbt_<fence>` owns `<fence>__base` in its domain's `_build` catalog (every privilege but
  GRANT_SELECT) and reads exactly its own bronze in `lake` and its input ports' tables
  (model.input_relation) through read-only catalogs: SELECT on a table in a writable catalog is the
  right to optimize, expire and roll it back (spike (e)).
- A publisher `pub_<fence>` owns `<fence>` in its domain's `_build` catalog, holds SELECT and
  OWNERSHIP on its views' exact names there, SELECT and GRANT_SELECT on its own base tables
  through the reader catalog, and catalog access `all` on that one reader catalog, which Trino's
  definer-view owner check demands (the gate, h1.02: the read-only connector still refuses writes).
  Nobody else holds GRANT_SELECT anywhere.
- svc.platform, the operator: `all` on every `_build` catalog, SELECT in base namespaces there (table
  maintenance), the run-record table's DDL, every `system` table, viewing and killing any query,
  system information, every session property. It holds no privilege in a reader namespace.
- model.REFS_READERS read exactly `<table>$refs` of each lake table, at its model.input_relation.
- svc.api may impersonate the declared `p.*` principals by exact name and nobody else.
- Session properties: catalog ones only for svc.platform (any) and the other pipeline principals
  (model.STATISTICS_PROPERTY only); system ones only for pipeline principals.
- The release canary model.canary_user may execute a query and reach nothing, so `SELECT 1` as it
  succeeds only while this release's rules are loaded.
- Absent on purpose: `functions` (Trino then runs only `system.builtin`, so `table_changes` and
  every catalog function are refused), `authorization` (SET AUTHORIZATION refused), and every
  procedure but the operator's kill_query.

Build serving tables have no lake table and no rule (model.LAKE_BUILDS). What the documents leave
inconsistent (two roles for one principal on one product, a grant to a pipeline principal, a
policy naming a column or role the table lacks, an input no product offers) is a ValueError: the
compiler runs no check first, so the emitter fails closed.
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from k2gen.model import (
    LAKE,
    LAKE_BUILDS,
    PIPELINE_PRINCIPALS,
    REFS_READERS,
    RUN_RECORD_SQL,
    STATISTICS_PROPERTY,
    Model,
    Product,
    RolePolicy,
    Table,
    base_namespace,
    build_catalog,
    builder,
    canary_user,
    fence,
    group,
    input_relation,
    publisher,
    quote_ident,
    reader_catalog,
    reader_namespace,
    reader_relation,
    sql_literal,
)

RULES = "compiled/trino/rules.json"
GROUPS = "compiled/trino/groups.txt"
OPERATOR = "svc.platform"
API = "svc.api"
SYSTEM = "system"
WRITE = ["SELECT", "INSERT", "DELETE", "UPDATE", "OWNERSHIP"]  # a builder's: never GRANT_SELECT
# The release canary's one statement: it runs only under the rules that name the canary, and reads nothing.
CANARY_STATEMENT = "SELECT 1"
# Trino 484's metadata tables (system.metadata) and JDBC tables (system.jdbc): each lists only the
# objects its reader may already see. Readers get both (a JDBC client browses through jdbc),
# builders get metadata (dbt-trino reads materialized_views and table_comments). Everything else
# in `system` is the operator's: system.runtime (query history and text above all) and the three
# `*_authorization` tables of system.metadata, which list owners. Each name is one Trino 484
# lists (the rules test's row system.names).
SYSTEM_TABLES = {
    "jdbc": ("attributes", "catalogs", "columns", "procedure_columns", "procedures", "pseudo_columns", "schemas",
             "super_tables", "super_types", "table_types", "tables", "types", "udts"),
    "metadata": ("analyze_properties", "catalogs", "column_properties", "materialized_view_properties",
                 "materialized_views", "schema_properties", "table_comments", "table_properties"),
}
# The services that are the platform's own: never a reader, never impersonated (A10).
PLATFORM_SERVICES = ("svc.runs", "svc.catalog", OPERATOR, API)
_NOT_A_READER = re.compile(r"(dbt|pub)_.*|k2_release_.*|" + "|".join(re.escape(s) for s in PLATFORM_SERVICES))
# The run-record table (A6): created by svc.platform at deploy, written only by svc.runs, both
# through `<domain>_build`; read by svc.catalog through the reader catalog.
_RUN_RECORD = re.search(r"CREATE TABLE (\w+)_build\.(\w+)\.(\w+) ", RUN_RECORD_SQL.read_text()).groups()  # type: ignore[union-attr]


def exact(names: Iterable[str]) -> str:
    """A regex Trino matches against a whole name that matches exactly these names."""
    found = sorted(set(names))
    if not found:
        raise ValueError("a rule names nobody")
    return "|".join(re.escape(n) for n in found)


def _split(relation: str) -> dict[str, str]:
    catalog, schema, table = relation.split(".")
    return {"catalog": exact([catalog]), "schema": exact([schema]), "table": exact([table])}


def _filter(t: Table, policy: RolePolicy) -> str | None:
    """The row filter of one role's policy: rowScope, then delay, each a whole expression."""
    columns = {c.name for c in t.columns}
    parts = []
    if policy.row_scope:
        if policy.row_scope.column not in columns:
            raise ValueError(f"{t.path}: rowScope column {policy.row_scope.column} is not a column of {t.name}")
        values = ", ".join(sql_literal(v) for v in policy.row_scope.values)
        parts.append(f"{quote_ident(policy.row_scope.column)} IN ({values})")
    if policy.delay:
        if policy.delay.column not in columns:
            raise ValueError(f"{t.path}: delay column {policy.delay.column} is not a column of {t.name}")
        minutes = sql_literal(str(int(policy.delay.minutes)))
        parts.append(f"{quote_ident(policy.delay.column)} < current_timestamp - INTERVAL {minutes} MINUTE")
    return " AND ".join(f"({p})" for p in parts) if parts else None


def _reader_rule(p: Product, t: Table, role: str) -> dict[str, Any]:
    policy = t.policies.get(role, RolePolicy(None, (), None))
    rule: dict[str, Any] = {"group": exact([group(p.name, role)]), **_split(reader_relation(t)), "privileges": ["SELECT"]}
    if (where := _filter(t, policy)) is not None:
        rule["filter"] = where
    types = {c.name: c.physical_type for c in t.columns}
    if missing := sorted(set(policy.mask) - types.keys()):
        raise ValueError(f"{t.path}: mask names {missing}, not columns of {t.name}")
    if policy.mask:
        rule["columns"] = [{"name": c, "mask": f"CAST(NULL AS {types[c]})"} for c in sorted(set(policy.mask))]
    return rule


def _lake_tables(p: Product) -> list[Table]:
    return [t for t in p.tables if t.build.kind in LAKE_BUILDS]


def _built(p: Product) -> list[Table]:
    """The tables a builder writes and a publisher serves through views (dbt and code)."""
    return [t for t in p.tables if t.build.kind in ("dbt", "code")]


def known_principals(m: Model) -> list[str]:
    """Everyone the rules let run a query: the declared principals, every builder and publisher,
    and the release canary. Anyone else is refused at the first statement."""
    known = {p.id for p in m.principals} | {OPERATOR, canary_user(m.provenance.set_sha256)}
    for p in m.products:
        if _built(p):
            known |= {builder(p.name), publisher(p.name)}
    return sorted(known)


def pipeline_principals(m: Model) -> list[str]:
    """The known principals model.PIPELINE_PRINCIPALS matches: builders, publishers, svc.runs,
    svc.catalog and svc.platform, by exact name."""
    return [u for u in known_principals(m) if any(re.fullmatch(pat, u) for pat in PIPELINE_PRINCIPALS)]


def emit(model: Model) -> dict[str, str]:
    tables = {t.contract_id: t for p in model.products for t in _lake_tables(p)}
    domains = sorted({d.name for d in model.domains})
    users: dict[str, dict[str, str]] = defaultdict(dict)  # catalog -> user -> all | read-only
    group_catalogs: dict[str, set[str]] = defaultdict(set)  # catalog -> reader groups, read-only
    schemas: list[dict[str, Any]] = []
    owned: list[dict[str, Any]] = []  # builders' and publishers' table rules
    readers: list[dict[str, Any]] = []
    members: dict[str, set[str]] = defaultdict(set)
    builders: list[str] = []

    def reach(catalog: str, user: str, level: str) -> None:
        if users[catalog].get(user) != "all":
            users[catalog][user] = level

    for d in domains:
        reach(build_catalog(d), OPERATOR, "all")
        reach(reader_catalog(d), OPERATOR, "read-only")
    reach(LAKE, OPERATOR, "read-only")

    for p in sorted(model.products, key=lambda p: p.id):
        roles: dict[str, str] = {}
        for g in p.grants:
            if _NOT_A_READER.fullmatch(g.principal):
                raise ValueError(f"{p.path}/grants.yaml: {g.principal} is a platform principal, never a reader")
            if roles.setdefault(g.principal, g.role) != g.role:
                raise ValueError(f"{p.path}/grants.yaml: {g.principal} holds two roles on {p.id}; one role per principal per product")
        for t in _lake_tables(p):
            if stray := sorted(set(t.policies) - {r.role for r in t.roles}):
                raise ValueError(f"{t.path}: policies for {stray}, which the table does not offer")
            catalog = reader_relation(t).split(".")[0]
            for r in sorted({r.role for r in t.roles}):
                readers.append(_reader_rule(p, t, r))
                group_catalogs[catalog].add(group(p.name, r))
                members[group(p.name, r)] |= {u for u, role in roles.items() if role == r}
        built = _built(p)
        if not built:
            continue
        dbt, pub = builder(p.name), publisher(p.name)
        builders.append(dbt)
        reach(build_catalog(p.domain), dbt, "all")
        reach(build_catalog(p.domain), pub, "all")
        reach(reader_catalog(p.domain), pub, "all")
        schemas += [{"user": exact([dbt]), "catalog": exact([build_catalog(p.domain)]), "schema": exact([base_namespace(p.name)]), "owner": True},
                    {"user": exact([pub]), "catalog": exact([build_catalog(p.domain)]), "schema": exact([reader_namespace(p.name)]), "owner": True}]
        owned.append({"user": exact([dbt]), "catalog": exact([build_catalog(p.domain)]), "schema": exact([base_namespace(p.name)]), "privileges": WRITE})
        reads = [t for t in p.tables if t.build.kind == "connector"]
        for port in p.input_ports:
            if port.contract_id not in tables:
                raise ValueError(f"{p.path}/product.odps.yaml: input {port.contract_id} is no product's lake table")
            reads.append(tables[port.contract_id])
        for t in sorted(reads, key=lambda t: input_relation(t)):
            reach(input_relation(t).split(".")[0], dbt, "read-only")
            owned.append({"user": exact([dbt]), **_split(input_relation(t)), "privileges": ["SELECT"]})
        names = [t.name for t in built]
        owned.append({"user": exact([pub]), "catalog": exact([build_catalog(p.domain)]), "schema": exact([reader_namespace(p.name)]),
                      "table": exact(names), "privileges": ["SELECT", "OWNERSHIP"]})
        owned.append({"user": exact([pub]), "catalog": exact([reader_catalog(p.domain)]), "schema": exact([base_namespace(p.name)]),
                      "table": exact(names), "privileges": ["SELECT", "GRANT_SELECT"]})

    refs = []
    for cid in sorted(tables):
        relation = input_relation(tables[cid])
        for u in REFS_READERS:
            reach(relation.split(".")[0], u, "read-only")
        catalog, schema, table = relation.split(".")
        refs.append({"user": exact(REFS_READERS), **_split(f"{catalog}.{schema}.{table}$refs"), "privileges": ["SELECT"]})

    operator_tables = [{"user": exact([OPERATOR]), "catalog": exact([SYSTEM]), "privileges": ["SELECT"]}]
    for d in domains:
        bases = [base_namespace(p.name) for p in model.products if p.domain == d and _built(p)]
        if bases:
            operator_tables.append({"user": exact([OPERATOR]), "catalog": exact([build_catalog(d)]), "schema": exact(bases), "privileges": ["SELECT"]})
    run_records: list[dict[str, Any]] = []
    rr_domain, rr_schema, rr_table = _RUN_RECORD
    if rr_domain in {fence(d) for d in domains}:
        reach(f"{rr_domain}_build", "svc.runs", "all")
        reach(rr_domain, "svc.catalog", "read-only")
        where = {"schema": exact([rr_schema]), "table": exact([rr_table])}
        schemas.append({"user": exact([OPERATOR]), "catalog": exact([f"{rr_domain}_build"]), "schema": exact([rr_schema]), "owner": True})
        run_records = [
            {"user": exact([OPERATOR]), "catalog": exact([f"{rr_domain}_build"]), **where, "privileges": ["SELECT", "OWNERSHIP"]},
            {"user": exact(["svc.runs"]), "catalog": exact([f"{rr_domain}_build"]), **where, "privileges": ["SELECT", "INSERT"]},
            {"user": exact(["svc.catalog"]), "catalog": exact([rr_domain]), **where, "privileges": ["SELECT"]}]

    reader_groups = sorted({g for gs in group_catalogs.values() for g in gs})
    system_tables: list[dict[str, Any]] = []
    if builders:
        system_tables.append({"user": exact(builders), "catalog": exact([SYSTEM]), "schema": exact(["metadata"]),
                              "table": exact(SYSTEM_TABLES["metadata"]), "privileges": ["SELECT"]})
    if reader_groups:
        system_tables += [{"group": exact(reader_groups), "catalog": exact([SYSTEM]), "schema": exact([s]), "table": exact(SYSTEM_TABLES[s]),
                           "privileges": ["SELECT"]} for s in sorted(SYSTEM_TABLES)]
    reach(SYSTEM, OPERATOR, "read-only")
    for b in builders:
        reach(SYSTEM, b, "read-only")

    catalogs: list[dict[str, Any]] = []
    for level in ("all", "read-only"):
        for catalog in sorted(users):
            who = [u for u, lv in users[catalog].items() if lv == level]
            if who:
                catalogs.append({"user": exact(who), "catalog": exact([catalog]), "allow": level})
    for catalog in sorted(group_catalogs):
        catalogs.append({"group": exact(group_catalogs[catalog]), "catalog": exact([catalog]), "allow": "read-only"})
    if reader_groups:
        catalogs.append({"group": exact(reader_groups), "catalog": exact([SYSTEM]), "allow": "read-only"})
    catalogs.append({"catalog": exact([SYSTEM]), "allow": "none"})  # Trino grants `system` to all unless a rule says otherwise

    known = known_principals(model)
    pipeline = pipeline_principals(model)
    people = [p.id for p in model.principals if p.id.startswith("p.")]
    rules: dict[str, Any] = {
        "catalogs": catalogs,
        "schemas": schemas,
        "tables": operator_tables + system_tables + owned + run_records + refs + readers,
        "system_session_properties": [{"user": exact(pipeline), "allow": True}],
        "catalog_session_properties": [
            {"user": exact([OPERATOR]), "allow": True},
            *([{"user": exact(others), "property": exact([STATISTICS_PROPERTY]), "allow": True}]
              if (others := [u for u in pipeline if u != OPERATOR]) else [])],
        "queries": [{"user": exact([OPERATOR]), "allow": ["execute", "kill", "view"]},
                    {"user": exact([u for u in known if u != OPERATOR]), "allow": ["execute"]}],
        "impersonation": [{"original_user": exact([API]), "new_user": exact(people), "allow": True}] if people else [],
        "system_information": [{"user": exact([OPERATOR]), "allow": ["read"]}],
        "procedures": [{"user": exact([OPERATOR]), "catalog": exact([SYSTEM]), "schema": exact(["runtime"]),
                        "procedure": exact(["kill_query"]), "privileges": ["EXECUTE"]}],
    }
    groups = "".join(f"{g}:{','.join(sorted(members[g]))}\n" for g in sorted(members) if members[g])
    return {RULES: json.dumps(rules, indent=2) + "\n", GROUPS: groups}
