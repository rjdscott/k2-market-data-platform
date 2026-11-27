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
  rule names a base namespace or a `$` metadata table. A delay's column is DELAY_TYPE: on
  `timestamp(6)` or `date` the filter follows the reader's session time zone, which the reader
  sets (`SET TIME ZONE`, the X-Trino-Time-Zone header), and on `time(6)` it fails. A connector
  table offers no role: SELECT on an Iceberg table reads every ref and snapshot of it, the bronze
  audit branch's unaudited rows included, so readers reach bronze through a dbt output only.
- A builder `dbt_<fence>` owns `<fence>__base` in its domain's `_build` catalog (every privilege but
  GRANT_SELECT) and reads exactly its own bronze in `lake` and its input ports' tables
  (model.input_relation) through read-only catalogs: SELECT on a table in a writable catalog is the
  right to optimize, expire and roll it back (spike (e)).
- A publisher `pub_<fence>` owns `<fence>` in its domain's `_build` catalog, holds SELECT and
  OWNERSHIP on its views' exact names there, SELECT and GRANT_SELECT on its own base tables and on
  their `<table>$refs`, which each view's guard reads (emit/lake.view_sql; Trino's definer-view
  owner check asks GRANT_SELECT of every relation a view reads), through the reader catalog, and
  catalog access `all` on that one reader catalog, which Trino's
  definer-view owner check demands (the gate, h1.02: the read-only connector still refuses writes).
  Nobody else holds GRANT_SELECT anywhere.
- svc.platform, the operator: `all` on every `_build` catalog, SELECT in base namespaces there (table
  maintenance), the run-record table's DDL, every `system` table, viewing and killing any query,
  system information, every session property. It holds no privilege in a reader namespace.
- The builder of model.QUALITY_RESULTS alone also reads each model.test_results_writers product's
  `<domain>.<fence>__base.dq_test_results` (model.TEST_RESULTS), its own included, through the
  reader catalog at main; svc.catalog reads its views by exact name, as it reads the run record.
- model.REFS_READERS read exactly `<table>$refs` of each lake table, at its model.input_relation;
  a publisher reads its own bases' `$refs` too, through its views' guard.
- svc.api may impersonate the declared `p.*` principals by exact name and nobody else.
- Session properties: catalog ones only for svc.platform (any) and the other pipeline principals
  (model.STATISTICS_PROPERTY only); system ones only for pipeline principals.
- The release canary model.canary_user may execute a query and reach nothing, so `SELECT 1` as it
  succeeds only while this release's rules are loaded.
- Absent on purpose: `functions` (Trino then runs only `system.builtin`, so `table_changes` and
  every catalog function are refused), `authorization` (SET AUTHORIZATION refused), and every
  procedure but the operator's kill_query.

Product status (ADR-055 part 4), least privilege: `active` and `deprecated` (still runs; ADR-055's
"no new reader" is checks.access's, against the previous release) get every rule above;
`paused` (released, consuming nothing, rows readable) keeps its readers and its publisher, whose
rights keep the definer views readable and let a release re-sync them, and loses its builder,
which builds nothing until a release resumes it; `retired` (not built) gets no rule at all, its
grants, publisher, builder and `$refs` included: the platform reads its rows through Spark.

Not a rule: Trino 484 refuses CREATE BRANCH, DROP BRANCH and FAST FORWARD to every principal,
the operator included, with an UnsupportedOperationException from the file access control
(FileBasedSystemAccessControl.checkCanCreateBranch), not because a rule says so. Should an upgrade
let a builder make a branch named `audited` on a recreated base, the view's guard asks for a tag
of that name and readers get no rows. Revisit on any Trino upgrade (the rules suite's branch row).

Build serving tables have no lake table and no rule (model.LAKE_BUILDS). What the documents leave
inconsistent (two roles for one principal on one product, a grant to a pipeline principal, a
policy naming a column or role the table lacks, a delay on a column of another type than
DELAY_TYPE, a connector table offering a role, an input no live product offers) is a ValueError:
the compiler runs no check first, so the emitter fails closed.
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
    QUALITY_RESULTS,
    REFS_READERS,
    RUN_RECORD_SQL,
    STATISTICS_PROPERTY,
    TEST_RESULTS,
    Model,
    Product,
    RolePolicy,
    Table,
    base_namespace,
    base_relation,
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
    test_results_writers,
)

RULES = "compiled/trino/rules.json"
GROUPS = "compiled/trino/groups.txt"
OPERATOR = "svc.platform"
API = "svc.api"
SYSTEM = "system"
WRITE = ["SELECT", "INSERT", "DELETE", "UPDATE", "OWNERSHIP"]  # a builder's: never GRANT_SELECT
# The one column type a delay's filter, `<column> < current_timestamp - INTERVAL ...`, compares as an
# instant (the rules suite's c1 rows: only this type gave every session time zone the same rows).
DELAY_TYPE = "timestamp(6) with time zone"
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
        if (kind := next(c.physical_type for c in t.columns if c.name == policy.delay.column)) != DELAY_TYPE:
            raise ValueError(f"{t.path}: delay column {policy.delay.column} is {kind}, not {DELAY_TYPE}: the reader's time zone would move the delay")
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


def _live(m: Model) -> list[Product]:
    """The products the rules name: every one but a retired one (ADR-055 part 4)."""
    return [p for p in m.products if p.status != "retired"]


def _builds(p: Product) -> bool:
    """Whether p's builder gets rules: it has built tables and runs (not paused)."""
    return bool(_built(p)) and p.status in ("active", "deprecated")


def impersonable(m: Model) -> list[str]:
    """Whom svc.api may become: the declared people, `p.*`, by exact name (A10). k2-api's caller
    list (emit/api.py) is exactly this list, so it never serves a key the rules would refuse."""
    return sorted(p.id for p in m.principals if p.id.startswith("p."))


def known_principals(m: Model) -> list[str]:
    """Everyone the rules let run a query: the declared principals, the builder of every product
    that builds and the publisher of every live one, and the release canary. Anyone else is
    refused at the first statement."""
    pipeline = {publisher(p.name) for p in _live(m) if _built(p)} | {builder(p.name) for p in _live(m) if _builds(p)}
    # A declared dbt_<x> or pub_<x> whose product gets no such rules (paused, retired, none) runs nothing.
    declared = {p.id for p in m.principals if not re.fullmatch(r"(dbt|pub)_.*", p.id)}
    return sorted(declared | pipeline | {OPERATOR, canary_user(m.provenance.set_sha256)})


def pipeline_principals(m: Model) -> list[str]:
    """The known principals model.PIPELINE_PRINCIPALS matches: builders, publishers, svc.runs,
    svc.catalog and svc.platform, by exact name."""
    return [u for u in known_principals(m) if any(re.fullmatch(pat, u) for pat in PIPELINE_PRINCIPALS)]


def emit(model: Model) -> dict[str, str]:
    tables = {t.contract_id: t for p in _live(model) for t in _lake_tables(p)}
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

    for p in sorted(_live(model), key=lambda p: p.id):
        roles: dict[str, str] = {}
        for g in p.grants:
            if _NOT_A_READER.fullmatch(g.principal):
                raise ValueError(f"{p.path}/grants.yaml: {g.principal} is a platform principal, never a reader")
            if roles.setdefault(g.principal, g.role) != g.role:
                raise ValueError(f"{p.path}/grants.yaml: {g.principal} holds two roles on {p.id}; one role per principal per product")
        for t in _lake_tables(p):
            if t.build.kind == "connector" and t.roles:
                raise ValueError(f"{t.path}: a connector table offers {sorted({r.role for r in t.roles})}; SELECT on it reads every "
                                 "ref and snapshot, the audit branch included, so readers reach bronze through a dbt output only")
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
        reach(build_catalog(p.domain), pub, "all")
        reach(reader_catalog(p.domain), pub, "all")
        reads = [t for t in p.tables if t.build.kind == "connector"]
        for port in p.input_ports:
            if port.contract_id not in tables:
                raise ValueError(f"{p.path}/product.odps.yaml: input {port.contract_id} is no live product's lake table")
            reads.append(tables[port.contract_id])
        if _builds(p):
            builders.append(dbt)
            reach(build_catalog(p.domain), dbt, "all")
            schemas.append({"user": exact([dbt]), "catalog": exact([build_catalog(p.domain)]), "schema": exact([base_namespace(p.name)]), "owner": True})
            owned.append({"user": exact([dbt]), "catalog": exact([build_catalog(p.domain)]), "schema": exact([base_namespace(p.name)]), "privileges": WRITE})
            relations = [input_relation(t) for t in reads]
            if p.id == QUALITY_RESULTS:  # every builder's test results, its own included, at main (ADR-046)
                relations += [base_relation(q.domain, q.name, TEST_RESULTS) for q in test_results_writers(model)]
            for relation in sorted(relations):
                reach(relation.split(".")[0], dbt, "read-only")
                owned.append({"user": exact([dbt]), **_split(relation), "privileges": ["SELECT"]})
        schemas.append({"user": exact([pub]), "catalog": exact([build_catalog(p.domain)]), "schema": exact([reader_namespace(p.name)]), "owner": True})
        names = [t.name for t in built]
        owned.append({"user": exact([pub]), "catalog": exact([build_catalog(p.domain)]), "schema": exact([reader_namespace(p.name)]),
                      "table": exact(names), "privileges": ["SELECT", "OWNERSHIP"]})
        owned.append({"user": exact([pub]), "catalog": exact([reader_catalog(p.domain)]), "schema": exact([base_namespace(p.name)]),
                      "table": exact([*names, *(f"{n}$refs" for n in names)]), "privileges": ["SELECT", "GRANT_SELECT"]})

    refs = []
    for cid in sorted(tables):
        relation = input_relation(tables[cid])
        for u in REFS_READERS:
            reach(relation.split(".")[0], u, "read-only")
        catalog, schema, table = relation.split(".")
        refs.append({"user": exact(REFS_READERS), **_split(f"{catalog}.{schema}.{table}$refs"), "privileges": ["SELECT"]})

    operator_tables = [{"user": exact([OPERATOR]), "catalog": exact([SYSTEM]), "privileges": ["SELECT"]}]
    for d in domains:
        bases = [base_namespace(p.name) for p in _live(model) if p.domain == d and _built(p)]
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

    # svc.catalog reads the quality results' views as it reads the run record: a platform principal
    # takes no grant (checks.access, access.grant-to-platform), so the rule names it, by exact view.
    quality = [reader_relation(t) for p in _live(model) if p.id == QUALITY_RESULTS for t in _built(p)]
    for relation in quality:
        reach(relation.split(".")[0], "svc.catalog", "read-only")
    catalog_reads = [{"user": exact(["svc.catalog"]), **_split(r), "privileges": ["SELECT"]} for r in sorted(quality)]

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
    people = impersonable(model)
    rules: dict[str, Any] = {
        "catalogs": catalogs,
        "schemas": schemas,
        "tables": operator_tables + system_tables + owned + run_records + catalog_reads + refs + readers,
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
