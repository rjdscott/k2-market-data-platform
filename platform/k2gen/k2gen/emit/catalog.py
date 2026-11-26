"""Emits model.CATALOG_INDEX and model.catalog_path(<product id>) for every product
(contracts/schema/catalog-product.schema.json): reader names only (model.reader_relation), never a
__base name; dbtDocs is model.dbt_docs for a dbt output, null otherwise; a build serving output
has relation null and carries its serves.
Filled by step S18.

Only what the documents state: a policy in words, never the rule that enforces it; examples as
written; lineage edges from the output ports' inputContracts, never guessed from model SQL.
Run-time facts (the newest audited snapshot, test results) are not compiled: the site and
k2-api read them through each output's reader relation at run time.
"""

import dataclasses
import json
from typing import Any

from k2gen.model import (
    BASE_SUFFIX,
    CATALOG_INDEX,
    LAKE_BUILDS,
    Model,
    Product,
    RolePolicy,
    Table,
    catalog_path,
    dbt_docs,
    reader_relation,
)


def _listed(items: tuple[str | int, ...], conjunction: str) -> str:
    """`a`, `a or b`, `a, b or c`."""
    words = [str(i) for i in items]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} {conjunction} {words[-1]}"


def in_words(policy: RolePolicy | None) -> str:
    """A role's policy as the catalog page says it: what a reader sees, never the rule text."""
    if policy is None:
        return "every row and column"
    rows = []
    if policy.row_scope:
        rows.append(f"whose {policy.row_scope.column} is {_listed(policy.row_scope.values, 'or')}")
    if policy.delay:
        minutes = policy.delay.minutes
        rows.append(f"whose {policy.delay.column} is at least {minutes} minute{'s' * (minutes != 1)} old")
    parts = [f"only rows {' and '.join(rows)}"] if rows else []
    if policy.mask:
        parts.append(f"{_listed(policy.mask, 'and')} read as null")
    return "; ".join(parts)


def _stated(obj: Any) -> dict[str, Any]:
    """A dataclass's fields the document sets, by the schema's own (one-word) names."""
    return {k: v for k, v in dataclasses.asdict(obj).items() if v is not None}


def _policy(p: RolePolicy) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if p.delay:
        out["delay"] = {"column": p.delay.column, "minutes": p.delay.minutes}
    if p.mask:
        out["mask"] = list(p.mask)
    if p.row_scope:
        out["rowScope"] = {"column": p.row_scope.column, "values": list(p.row_scope.values)}
    return out


def _relation(t: Table) -> str | None:
    """The reader name; None for a build serving table, which only its serving copy holds."""
    return reader_relation(t) if t.build.kind in LAKE_BUILDS else None


def _find(tables: dict[str, Table], key: str, where: str) -> Table:
    if key not in tables:
        raise ValueError(f"{where}: names {key}, which no contract of the set declares")
    return tables[key]


def _product(p: Product, by_id: dict[str, Table], by_path: dict[str, Table], tag: str) -> dict[str, Any]:
    ports = {port.contract_id: port for port in p.output_ports}
    outputs, join_keys = [], []
    for t in p.tables:
        relation = _relation(t)
        columns = []
        for c in t.columns:
            column: dict[str, Any] = {"name": c.name, "type": c.physical_type, "required": c.required,
                                      "primaryKey": c.primary_key, "description": c.description}
            if c.unit:
                column["unit"] = c.unit
            if c.time:
                column["time"] = c.time
            references = []
            for r in c.relationships:
                file, _, pointer = r.to.partition("#")  # <domain>/<product>/<table>.odcs.yaml#/.../<column>
                to = {"relation": reader_relation(_find(by_path, f"contracts/{file}", t.path)),
                      "column": pointer.rsplit("/", 1)[-1]}
                as_of = {"asOf": r.as_of} if r.as_of else {}
                references.append(to | as_of)
                if relation:  # a serving copy has no reader name to join from
                    join_keys.append({"from": {"relation": relation, "column": c.name}, "to": to} | as_of)
            if references:
                column["references"] = references
            columns.append(column)
        offered = {r.role for r in t.roles}
        port = ports.get(t.contract_id)
        outputs.append({
            "contractId": t.contract_id, "relation": relation,
            **({"serves": _stated(port.serves)} if port and port.serves else {}),
            "layer": t.layer, "version": t.version, "purpose": t.purpose, "columns": columns,
            "roles": [{"role": r.role, **({"policies": _policy(t.policies[r.role])} if r.role in t.policies else {}),
                       "inWords": in_words(t.policies.get(r.role))} for r in t.roles],
            "readers": [{"principal": g.principal, "role": g.role} for g in p.grants if g.role in offered],
            "dbtDocs": dbt_docs(p.name, t.name) if t.build.kind == "dbt" else None,
        })

    nodes: dict[str, dict[str, Any]] = {}
    sources = [port.contract_id for port in p.input_ports] + [t.contract_id for t in p.tables]
    sources += [ref.id for port in p.output_ports for ref in port.input_contracts]
    for cid in sources:
        nodes.setdefault(cid, {"id": cid, "product": cid.rsplit(".", 1)[0], "relation": _relation(_find(by_id, cid, p.path))})
    edges = [{"from": ref.id, "to": port.contract_id} for port in p.output_ports for ref in port.input_contracts]

    return {
        "id": p.id, "name": p.name, "domain": p.domain, "version": p.version, "status": p.status,
        **({"lifecycleChange": _stated(p.lifecycle_change)} if p.lifecycle_change else {}),
        "type": p.type, "owner": p.owner, "purpose": p.purpose,
        **{k: v for k, v in (("limitations", p.limitations), ("usage", p.usage)) if v},
        **({"terms": _stated(p.terms)} if p.terms else {}),
        **({"licence": p.licence} if p.licence else {}),
        "inputs": [{"contractId": port.contract_id,
                    "relation": reader_relation(_find(by_id, port.contract_id, p.path)),
                    "required": port.required, "maxLagMinutes": port.max_lag_minutes} for port in p.input_ports],
        "outputs": outputs,
        **({"examples": [_stated(e) for e in p.examples]} if p.examples else {}),
        "joinKeys": join_keys,
        "lineage": {"nodes": list(nodes.values()), "edges": edges},
        "tag": tag,
    }


def _text(path: str, doc: dict[str, Any]) -> str:
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if BASE_SUFFIX in text:  # the schema's relation refuses one; this holds every other field to it
        raise ValueError(f"{path}: holds a {BASE_SUFFIX} name; a reader sees reader names only")
    return text


def emit(model: Model) -> dict[str, str]:
    tables = [t for p in model.products for t in p.tables]
    by_id, by_path = {t.contract_id: t for t in tables}, {t.path: t for t in tables}
    tag = model.provenance.tag
    files = {catalog_path(p.id): _text(p.id, _product(p, by_id, by_path, tag)) for p in model.products}
    if len(files) != len(model.products):
        raise ValueError("two products share an id, so they would share one catalog file")
    index = {"tag": tag, "products": sorted(
        ({"id": p.id, "name": p.name, "domain": p.domain, "status": p.status, "owner": p.owner, "purpose": p.purpose}
         for p in model.products), key=lambda e: e["id"])}
    return {**files, CATALOG_INDEX: _text(CATALOG_INDEX, index)}
