"""Access checks (A9, A10; ARCHITECTURE-V2 4.6; ADR-061 and its gate).

Every finding is an error, on the document that has to change. The documents are read through
`model.parse`; `checks.run` runs this after `docs`, so they are schema-valid here.

**Declarations.** Principal ids are unique (`access.principal-duplicate`). A table offers only
declared roles (`access.role-undeclared`); its `policies` name only roles it offers
(`access.policy-role-not-offered`: a misspelt role would leave the real one with no policy) and
only its own columns (`access.policy-column-unknown`: a misspelt mask leaves the column shown); a
role's `requires` are set for it on every table offering it (`access.role-requirement-unmet`).

**Grants.** A grant names a declared principal (`access.grant-unknown-principal`) and a declared
role (`access.grant-unknown-role`) that a table of the product offers
(`access.grant-role-not-offered`); a principal holds at most one role per product
(`access.grant-two-roles`, DECISIONS-FINAL O1a). A grantee is a consumer, so it is never a
principal the platform's patterns match (`model.PIPELINE_PRINCIPALS`, `BUILD_CATALOG_PRINCIPALS`,
`READER_CATALOG_ALL`, `READER_NAMESPACE_OWNERS`, `RETENTION_PROPERTY_PRINCIPALS`, `REFS_READERS`):
those hold writer rights, CALL or the statistics property (`access.grant-to-platform`). And no
declared principal falls under a platform pattern by accident: `dbt_<x>` and `pub_<x>` match
`dbt_.*` and `pub_.*`, so `<x>` must be a product's fence (`access.principal-collides`).

**Inheritance: "at least as strict".** For an output O, a role R that O offers, and each input I
that O derives from (the contracts its output port lists in `inputContracts`, a sibling table of
the product replaced by what that sibling derives from; every input port of the product when it
lists none), O's policy for R is at least as strict as I's when:

- role: I offers R. A role I does not offer is I's strictest policy, no access at all, so O may not
  offer it either (`access.inherit-role`);
- delay: if I delays R by m minutes on column c, O delays R by at least m minutes, on a column
  declared to derive from I.c (`access.inherit-delay`);
- mask: if I masks column x for R, every column of O declared to derive from I.x is masked for R
  on O; a masked column may also reach O through no column at all (`access.inherit-mask`);
- row scope: if I scopes R to values V on column c, O scopes R to a subset of V, on a column
  declared to derive from I.c (`access.inherit-row-scope`).

A role I offers with no policy constrains nothing. **Derivation** is the column's ODCS
`transformSourceObjects`, each entry `<contract id>.<column>` of an input. SQL does not tell it
(a column may be computed from any other), so the conservative rule: where a policy needs a
derivation the documents do not declare, the check does not guess but asks for it
(`access.inherit-undeclared`): for a delay or a row scope, on the column of O that carries it; for
a mask, on every column of O not masked for R, since any of them might be computed from the masked
one. Not proven here: that O's delay column is never earlier than I's (for candles the window end,
not its start). A declared derivation is the author's claim; ARCHITECTURE-V2 4.6 leaves that to a
generated test. A product's own tables (silver from its bronze) are not inputs: no document states
that derivation, and one owner writes both policies.

**Coverage** (A9, the gate's rows; on `tests/access/expectations.yaml`). Hand-written rows must
exist for (`access.expectation-missing`):

- each table in a lake build and each role it offers that someone holds on its product: a read
  row by a holder, at the table's reader name (`model.reader_relation`); when the role has a
  policy, every such row asserts it, masked columns listed, `minRowAgeMinutes` at least the delay,
  a `mustNotAppear` for a row scope, and at least one of them a `mustAppear`, the positive
  assertion a table that built no rows fails (`access.expectation-weak`);
- each reader view of a dbt or code output that nobody holds a role on: some row naming it;
- each such output's base (`model.input_relation`), each seed's table in the base
  (`<domain>.<fence>__base.<stem>`, model.dbt_seed), each bronze table that offers no role, and
  each connector product's landing (`landing_files`, `landing` for kafka): a denied row, by a
  principal holding a role on the product when anyone does, since a denial to a stranger proves
  only the absence of a grant;
- once a set has a reader catalog: the statistics property denied by `SET SESSION` (gate h1.23) and
  by the session header (h1.24); a view planted in a product's reader namespace under a name no
  table has, denied (the foreign definer view, h1.31); `CREATE OR REPLACE VIEW` on a view's build
  name denied to the product's builder (h1.12) and to `svc.platform` (h1.33).

A row's principal must be declared (`access.expectation-unknown-principal`): a denial to a user
that does not exist passes without proving anything. The gate's other rows (the publisher's write
floor, owner transfer, `table_changes`, time travel on a view, probes, drift) are the owner's to
write and S20's to run; this check does not require them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from k2gen import model
from k2gen.model import DocumentSet, Finding

_KINDS = {"roles", "principals", "grants", "contract", "product", "expectations"}
_PLATFORM = (*model.PIPELINE_PRINCIPALS, *model.BUILD_CATALOG_PRINCIPALS, *model.READER_CATALOG_ALL,
             *model.READER_NAMESPACE_OWNERS, *model.RETENTION_PROPERTY_PRINCIPALS,
             *(re.escape(p) for p in model.REFS_READERS))
_VIEW_BUILDS = ("dbt", "code")
_READER_CATALOG_BUILDS = ("dbt", "code", "platform")


@dataclass
class _Product:
    path: str  # contracts/<domain>/<name>
    domain: str
    name: str
    doc: dict[str, Any]
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)  # contract path -> contract
    grants: list[dict[str, Any]] = field(default_factory=list)


def _err(code: str, path: str, message: str) -> Finding:
    return Finding(f"access.{code}", "error", path, message)


def _props(doc: dict[str, Any]) -> dict[str, Any]:
    return {p["property"]: p["value"] for p in doc.get("customProperties", [])}


def _build(contract: dict[str, Any]) -> str:
    value = _props(contract)["build"]
    return "code" if isinstance(value, dict) else str(value)


def _offered(contract: dict[str, Any]) -> list[str]:
    return [r["role"] for r in contract.get("roles", [])]


def _policies(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(_props(contract).get("policies", {}))


def _columns(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return list(contract["schema"][0]["properties"])


def _table(contract: dict[str, Any]) -> model.Table:
    """Just enough of a model.Table for the relation helpers, which dispatch on id and build."""
    return model.Table(path="", contract_id=contract["id"], name=contract["name"], version="", physical_name="",
                       purpose="", owner="", layer="gold", build=model.Build(_build(contract), None),  # type: ignore[arg-type]
                       backup="rebuild", retention=None, partitioning=(), sort=(), columns=(), roles=(),
                       policies={}, quality=(), sla=())


def _platform(principal: str) -> bool:
    return any(re.fullmatch(p, principal) for p in _PLATFORM)


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    parsed = {p: model.parse(p, b) for p, b in docs.documents.items() if model.document_kind(p) in _KINDS}
    roles: dict[str, Any] = parsed.get(model.ROLES, {}).get("roles", {})
    principals = [p["id"] for p in parsed.get(model.PRINCIPALS, {}).get("principals", [])]
    rows: list[dict[str, Any]] = parsed.get(model.EXPECTATIONS, {}).get("expectations", [])
    products: dict[str, _Product] = {}
    for path, doc in parsed.items():
        if model.document_kind(path) == "product":
            directory = path.rsplit("/", 1)[0]
            _, domain, name = directory.split("/")
            products[directory] = _Product(directory, domain, name, doc)
    for path, doc in parsed.items():
        product = products.get(path.rsplit("/", 1)[0])
        if product and model.document_kind(path) == "contract":
            product.tables[path] = doc
        elif product and model.document_kind(path) == "grants":
            product.grants = list(doc.get("grants", []))
    return [*_declarations(roles, principals, products), *_grants(roles, principals, products),
            *_inheritance(products), *_coverage(principals, products, rows)]


def _declarations(roles: dict[str, Any], principals: list[str], products: dict[str, _Product]) -> list[Finding]:
    out = [_err("principal-duplicate", model.PRINCIPALS, f"principal {p} is declared {principals.count(p)} times; declare it once")
           for p in sorted({p for p in principals if principals.count(p) > 1})]
    fences = {model.fence(p.name) for p in products.values()}
    for p in principals:
        for prefix, what in (("dbt_", "builder"), ("pub_", "publisher")):
            if p.startswith(prefix) and p.removeprefix(prefix) not in fences:
                out.append(_err("principal-collides", model.PRINCIPALS,
                                f"{p} matches the platform pattern {prefix}.*, which every {what} has (builder, publisher and "
                                f"pipeline rights in Trino's rules), but no product has fence {p.removeprefix(prefix)}; "
                                f"name a consumer p.<name> or svc.<name>"))
    for product in products.values():
        for path, t in product.tables.items():
            columns = {c["name"] for c in _columns(t)}
            offered = _offered(t)
            for role in offered:
                if role not in roles:
                    out.append(_err("role-undeclared", path, f"{t['id']} offers role {role}, which contracts/access/roles.yaml does not declare"))
            for role, policy in _policies(t).items():
                if role not in offered:
                    out.append(_err("policy-role-not-offered", path,
                                    f"{t['id']} sets a policy for {role} but does not offer it (roles: {', '.join(offered) or 'none'}); "
                                    f"a misspelt role leaves the offered one with no policy"))
                named = [policy[k]["column"] for k in ("delay", "rowScope") if k in policy] + list(policy.get("mask", []))
                for column in sorted(set(named) - columns):
                    out.append(_err("policy-column-unknown", path,
                                    f"{t['id']}'s policy for {role} names column {column}, which the table does not have; "
                                    f"a misspelt column leaves the real one unprotected"))
            for role in offered:
                missing = [k for k in roles.get(role, {}).get("requires", []) if k not in _policies(t).get(role, {})]
                if missing:
                    out.append(_err("role-requirement-unmet", path,
                                    f"{t['id']} offers {role}, which requires {', '.join(missing)} (roles.yaml), and sets none for it"))
    return out


def _grants(roles: dict[str, Any], principals: list[str], products: dict[str, _Product]) -> list[Finding]:
    out = []
    for product in products.values():
        path = f"{product.path}/grants.yaml"
        offered = {r for t in product.tables.values() for r in _offered(t)}
        held: dict[str, list[str]] = defaultdict(list)
        for g in product.grants:
            principal, role = g["principal"], g["role"]
            held[principal].append(role)
            if principal not in principals:
                out.append(_err("grant-unknown-principal", path, f"{principal} is not declared in {model.PRINCIPALS}"))
            if role not in roles:
                out.append(_err("grant-unknown-role", path, f"{principal}'s role {role} is not declared in {model.ROLES}"))
            elif role not in offered:
                out.append(_err("grant-role-not-offered", path,
                                f"{principal} is granted {role}, which no table of {product.name} offers "
                                f"(offered: {', '.join(sorted(offered)) or 'none'})"))
            if _platform(principal):
                out.append(_err("grant-to-platform", path,
                                f"{principal} is a platform principal (it matches the platform's patterns in k2gen.model): it holds "
                                f"writer rights, CALL or the statistics property, so it is never a consumer; grant a p.<name> or "
                                f"svc.<name> of its own"))
        for principal, held_roles in held.items():
            if len(held_roles) > 1:
                out.append(_err("grant-two-roles", path,
                                f"{principal} holds {', '.join(held_roles)} on {product.name}; a principal holds one role per product"))
    return out


def _inputs(product: _Product, contract: dict[str, Any], seen: frozenset[str] = frozenset()) -> list[str]:
    """The inputs `contract` derives from. A sibling its port lists (silver from its own bronze) is
    not an input, but what the sibling derives from is, so it is followed, never dropped."""
    siblings = {t["id"]: t for t in product.tables.values()}
    for port in product.doc.get("outputPorts", []):
        if port.get("contractId") == contract["id"] and port.get("inputContracts"):
            found: list[str] = []
            for cid in (c["id"] for c in port["inputContracts"]):
                if cid not in siblings:
                    found.append(cid)
                elif cid not in seen | {contract["id"]}:
                    found += _inputs(product, siblings[cid], seen | {contract["id"]})
            return list(dict.fromkeys(found))
    return [p["contractId"] for p in product.doc.get("inputPorts", [])]


def _derives(column: dict[str, Any]) -> set[str] | None:
    sources = column.get("transformSourceObjects")
    return None if sources is None else set(sources)


def _inheritance(products: dict[str, _Product]) -> list[Finding]:
    contracts = {t["id"]: t for p in products.values() for t in p.tables.values()}
    out = []
    for product in products.values():
        for path, o in product.tables.items():
            columns = {c["name"]: c for c in _columns(o)}
            for role in _offered(o):
                mine = _policies(o).get(role, {})
                for input_id in _inputs(product, o):
                    i = contracts.get(input_id)
                    if i is None:  # an unknown input is the docs check's
                        continue
                    if role not in _offered(i):
                        out.append(_err("inherit-role", path,
                                        f"{o['id']} offers {role}, but its input {input_id} does not: holders of {role} may not read "
                                        f"the input, so not what is derived from it; offer {role} on {input_id} or stop offering it here"))
                        continue
                    out += _inherit(path, o, columns, role, mine, input_id, _policies(i).get(role, {}))
    return out


def _carrier(path: str, o: dict[str, Any], column: dict[str, Any] | None, role: str, kind: str, source: str) -> Finding | None:
    """None when `column` (O's column carrying the policy) is declared to derive from `source`."""
    if column is None:
        return None  # an unknown column is policy-column-unknown
    derives = _derives(column)
    if derives is None:
        return _err("inherit-undeclared", path,
                    f"{o['id']}'s {kind} for {role} is on {column['name']}, and k2gen cannot tell that it derives from {source}, "
                    f"the input's {kind} column; declare transformSourceObjects: [{source}] on {column['name']}")
    if source not in derives:
        return _err(f"inherit-{'delay' if kind == 'delay' else 'row-scope'}", path,
                    f"{o['id']}'s {kind} for {role} is on {column['name']}, which derives from {', '.join(sorted(derives))}, not from "
                    f"{source}, the input's {kind} column")
    return None


def _inherit(path: str, o: dict[str, Any], columns: dict[str, dict[str, Any]], role: str, mine: dict[str, Any],
             input_id: str, theirs: dict[str, Any]) -> list[Finding]:
    out: list[Finding | None] = []
    if "delay" in theirs:
        need, source = theirs["delay"]["minutes"], f"{input_id}.{theirs['delay']['column']}"
        if "delay" not in mine or mine["delay"]["minutes"] < need:
            have = f"{mine['delay']['minutes']} minutes" if "delay" in mine else "no delay"
            out.append(_err("inherit-delay", path, f"{input_id} delays {role} by {need} minutes on {source}; {o['id']} has {have} for "
                                                   f"{role}: delay it by at least {need} minutes on a column derived from {source}"))
        else:
            out.append(_carrier(path, o, columns.get(mine["delay"]["column"]), role, "delay", source))
    if "rowScope" in theirs:
        source, allowed = f"{input_id}.{theirs['rowScope']['column']}", theirs["rowScope"]["values"]
        extra = [v for v in mine["rowScope"]["values"] if v not in allowed] if "rowScope" in mine else None
        if extra is None or extra:
            have = f"adds {extra}" if extra else "has no row scope"
            out.append(_err("inherit-row-scope", path, f"{input_id} scopes {role} to {allowed} on {source}; {o['id']} {have} for {role}: "
                                                       f"scope it to a subset of {allowed} on a column derived from {source}"))
        else:
            out.append(_carrier(path, o, columns.get(mine["rowScope"]["column"]), role, "row scope", source))
    masked = {f"{input_id}.{x}" for x in theirs.get("mask", [])}
    for name, column in columns.items() if masked else ():
        if name in mine.get("mask", []):
            continue
        derives = _derives(column)
        if derives is None:
            out.append(_err("inherit-undeclared", path,
                            f"{input_id} masks {', '.join(sorted(masked))} for {role}, and k2gen cannot tell whether {o['id']}.{name} "
                            f"is computed from it; declare transformSourceObjects on {name} (the input columns it derives from), "
                            f"or mask {name} for {role}"))
        elif derives & masked:
            out.append(_err("inherit-mask", path, f"{o['id']}.{name} derives from {', '.join(sorted(derives & masked))}, which the "
                                                  f"input masks for {role}; mask {name} for {role} on {o['id']}"))
    return [f for f in out if f]


def _coverage(principals: list[str], products: dict[str, _Product], rows: list[dict[str, Any]]) -> list[Finding]:
    path = model.EXPECTATIONS
    out = [_err("expectation-unknown-principal", path,
                f"a row runs as {r['principal']}, which {model.PRINCIPALS} does not declare: a denial to a user that does not exist "
                f"proves nothing")
           for r in rows if "principal" in r and r["principal"] not in principals]
    named = {r["table"] for r in rows if "table" in r}

    def denied(relation: str, holders: dict[str, str], what: str) -> None:
        by = [r["principal"] for r in rows if r.get("denied") and r["table"] == relation]
        if not any(p in holders for p in by) if holders else not by:
            who = f"a holder of a role on the product ({', '.join(sorted(holders))})" if holders else "some principal"
            out.append(_err("expectation-missing", path, f"no denied row for {relation}, {what}: add "
                                                         f"{{principal: <{who}>, table: {relation}, denied: true}}"))

    reader_catalogs = False
    for product in products.values():
        holders: dict[str, str] = {}
        for g in product.grants:  # a second role is grant-two-roles; coverage counts the first
            holders.setdefault(g["principal"], g["role"])
        tables = [t for t in product.tables.values() if _build(t) in model.LAKE_BUILDS]
        for t in tables:
            build, relation, policies = _build(t), model.reader_relation(_table(t)), _policies(t)
            reader_catalogs |= build in _READER_CATALOG_BUILDS
            granted = sorted({r for r in holders.values() if r in _offered(t)})
            for role in granted:
                reads = [r for r in rows if "visible" in r and r["table"] == relation and holders.get(r["principal"]) == role]
                if not reads:
                    out.append(_err("expectation-missing", path,
                                    f"no read row for {relation} by a holder of {role} "
                                    f"({', '.join(sorted(p for p, r in holders.items() if r == role))}): add one listing the "
                                    f"columns it sees{' and asserting its policy' if role in policies else ''}"))
                for r in reads:
                    out += _weak(r, t, role, policies.get(role))
                if reads and policies.get(role) and not any("mustAppear" in r for r in reads):
                    out.append(_err("expectation-weak", path,
                                    f"no read row of {relation} by a holder of {role} has a mustAppear: every other part of a read "
                                    f"row is negative, so a table that built no rows passes them all; add mustAppear (the rows "
                                    f"{role} must see, {{where: <predicate>, minRows: <n>}}) to one"))
            if build in _VIEW_BUILDS:
                if not granted and relation not in named:
                    out.append(_err("expectation-missing", path, f"the reader view {relation} has no row: nobody holds a role on it, "
                                                                 f"so add {{principal: <someone>, table: {relation}, denied: true}}"))
                denied(model.input_relation(_table(t)), holders, "a base readers must never reach (ADR-061)")
            if _props(t)["layer"] == "bronze" and not _offered(t):
                denied(relation, holders, "a bronze table that offers no role")
        for seed in _props(product.doc).get("seeds", []):  # a seed is a table of the base, built beside its models
            stem = seed["path"].removeprefix("seeds/").removesuffix(".csv")
            denied(model.base_relation(product.domain, product.name, stem), holders, "a seed's table in the base, which readers must never reach")
        connector = _props(product.doc).get("connector")
        if connector:
            landing = model.KAFKA_LANDING if connector["kind"] == "kafka" else model.LANDING_FILES
            denied(model.lake_relation(product.name, landing), holders, "the product's landing")
    if reader_catalogs:
        out += _gate_rows(products, rows)
    return out


def _weak(row: dict[str, Any], contract: dict[str, Any], role: str, policy: dict[str, Any] | None) -> list[Finding]:
    if not policy:
        return []
    columns = {c["name"] for c in _columns(contract)}
    lacks = []
    unmasked = sorted(set(policy.get("mask", [])) & columns - set(row.get("masked", [])))
    if unmasked:
        lacks.append(f"masked to include {', '.join(unmasked)}")
    if "delay" in policy and row.get("minRowAgeMinutes", -1) < policy["delay"]["minutes"]:
        lacks.append(f"minRowAgeMinutes of at least {policy['delay']['minutes']}")
    if "rowScope" in policy and "mustNotAppear" not in row:
        lacks.append(f"a mustNotAppear predicate outside {policy['rowScope']['column']} IN {policy['rowScope']['values']}")
    return [_err("expectation-weak", model.EXPECTATIONS,
                 f"the read row of {row['principal']} on {row['table']} does not assert {role}'s policy: it needs {'; '.join(lacks)}")
            ] if lacks else []


def _gate_rows(products: dict[str, _Product], rows: list[dict[str, Any]]) -> list[Finding]:
    statements = [r for r in rows if "sql" in r and r["expect"] == "denied"]
    stat = model.STATISTICS_PROPERTY
    views = {p.name: {model.view_build_relation(p.domain, p.name, t["name"]) for t in p.tables.values() if _build(t) in _VIEW_BUILDS}
             for p in products.values()}
    namespaces = {f"{model.reader_catalog(p.domain)}.{model.reader_namespace(p.name)}": {t["name"] for t in p.tables.values()}
                  for p in products.values() if views[p.name]}

    def replaces(principal: str, product: str | None) -> bool:
        for r in statements:
            m = re.match(r"\s*create\s+(?:or\s+replace\s+)?view\s+(\S+)", r["sql"], re.I)
            if m and r["principal"] == principal and any(m.group(1) in v for n, v in views.items() if product in (None, n)):
                return True
        return False

    wanted = {
        f"SET SESSION <catalog>.{stat} denied to a reader (gate h1.23)":
            any(not r.get("session") and re.match(rf"\s*set\s+session\s+\w+\.{stat}\b", r["sql"], re.I) for r in statements),
        f"a statement sent with the session header {{<catalog>.{stat}: \"true\"}}, denied (gate h1.24)":
            any(k.endswith(f".{stat}") for r in statements for k in r.get("session", {})),
        "a denied read of a view planted in a product's reader namespace under a name no table has (the foreign definer view, gate h1.31)":
            any(r.get("denied") and r["table"].rpartition(".")[0] in namespaces
                and r["table"].rpartition(".")[2] not in namespaces[r["table"].rpartition(".")[0]] for r in rows if "table" in r),
        "CREATE OR REPLACE VIEW on a view's build name (<domain>_build.<fence>.<table>) by that product's builder, denied (gate h1.12)":
            any(replaces(model.builder(name), name) for name, v in views.items() if v),
        "CREATE OR REPLACE VIEW on a view's build name by svc.platform, denied (gate h1.33)": replaces("svc.platform", None),
    }
    return [_err("expectation-missing", model.EXPECTATIONS, f"no row for {what}: add one") for what, found in wanted.items() if not found]
