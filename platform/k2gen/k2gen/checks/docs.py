"""Document checks (S05): what makes a document set well formed, before any other check reads it.

Every document is read by model.parse and held to its schemas (model.schema_errors: the vendored
standard, then K2's profile); a set with any such error stops there, since every rule after it reads
schema-valid documents. Then, each rule a code in CODES:

- places: a product's documents sit in contracts/<domain>/<product>/ beside its product.odps.yaml,
  every id, name, domain and physicalName follows from that path, the domain is declared and the
  product's team is its owner, each customProperties name appears once in a list;
- names: domains and products as model.domain_name_problem and model.product_name_problem allow, no
  fence a Trino reserved word (it is a catalog or a namespace), product names unique on the
  platform, dbt model names (model.dbt_model) unique in the one compiled project, no table named as
  one of the platform's own (model.RESERVED_TABLE_NAMES);
- one producer per table: each table contract has exactly one output port, in its own product, and
  one writer: a connector table its product's connector, a dbt table models/<table>.sql, and no
  model for anything else; a serving table its port's `serves`, and a serving-only product is
  paused (ADR-057);
- columns: every column described, physical types as model.physical_type_problem allows, every
  column a partitioning, sort, policy or latency element names exists, every port resolves;
- relationships (plan 005, the owner's decisions of 2026-11-28): a column's key into another
  product's table names a product of the set (docs.reference-unresolved) that is not retired, a
  table and column it has, of the column's physical type, through one of the referring product's
  input ports (docs.foreign-key-not-read), so the product's own build tests it through k2_input at
  the tested snapshot and policy inheritance runs over it; a target with history (a valid_from
  column, ADR-030) needs asOf, valid or known, and asOfColumn, the referring table's
  timestamp(6) with time zone the join is as of; a target without history takes neither;
- quality: every rule is one K2 runs (k2gen.emit.dbt.quality_test, the one mapping), so none is
  dropped: a custom dbt, sql or library rule on a dbt table, a k2-connector rule on a connector
  table, or text;
- unit tests: a unit test of an incremental model states which branch it tests,
  `overrides: {macros: {is_incremental: <bool>}}`, which dbt refuses to parse without;
- layers: bronze is written only by connectors and a connector writes only bronze (ADR-056 part 2);
  a bronze table is named bronze_<name> and keyed (pipeline.schema.json), and a fetch kind's carries
  model.LINEAGE_COLUMN;
- terms before a grant outside the owning team (ADR-055 part 5; a grant to a partner, another
  organisation's service, is always outside it, whichever team answers for the partner), on the product and on every
  product upstream of it through input ports, directly or through others (A3 of the 2026-11-28
  review: a derived product passes its inputs' data on). An upstream product is exempt only when
  everything it holds was written on the platform: no connector, no input port, every table built
  by dbt (from seeds, which the schema records synthetic, or from SQL alone), as the calendars
  product is; a table another product reads keeps at least the retention floor;
- an incremental dbt table's key holds every partition source column (A7; experiment (a) of
  docs/research/2026-11-26-release-spine-spikes.md);
- an input port read empty when absent (`whenAbsent: empty`) only in a product whose every dbt model
  is incremental: k2_input's zero rows rebuild a table model, which then publishes empty (a type-2
  history included), unless the port says `emptyIntoTable`.

Failing fixtures: tests/fixtures/v2/bad/docs/cases.yaml, one or more per code.
"""

from __future__ import annotations

import re
import typing
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from k2gen import model
from k2gen.emit.dbt import quality_test
from k2gen.model import DocumentSet, Finding

CODES = (
    "docs.unreadable",
    "docs.schema-invalid",
    "docs.property-repeated",
    "docs.orphan-document",
    "docs.product-misplaced",
    "docs.contract-misplaced",
    "docs.physical-name-mismatch",
    "docs.domain-undeclared",
    "docs.domain-name-invalid",
    "docs.product-name-invalid",
    "docs.name-reserved-word",
    "docs.product-name-taken",
    "docs.dbt-model-name-taken",
    "docs.table-name-reserved",
    "docs.team-mismatch",
    "docs.output-port-foreign",
    "docs.output-port-repeated",
    "docs.table-unpublished",
    "docs.reference-unresolved",
    "docs.connector-mismatch",
    "docs.model-missing",
    "docs.model-without-dbt-table",
    "docs.model-config-unreadable",
    "docs.unit-test-model-unknown",
    "docs.unit-test-branch-unstated",
    "docs.foreign-key-not-read",
    "docs.relationship-retired",
    "docs.relationship-column-unknown",
    "docs.relationship-type-mismatch",
    "docs.relationship-as-of-missing",
    "docs.relationship-as-of-column",
    "docs.relationship-as-of-unexpected",
    "docs.quality-unsupported",
    "docs.serving-without-serves",
    "docs.serving-only-not-paused",
    "docs.column-undescribed",
    "docs.physical-type-invalid",
    "docs.column-unknown",
    "docs.layer-build-mismatch",
    "docs.bronze-name",
    "docs.bronze-key-missing",
    "docs.lineage-column-missing",
    "docs.terms-missing",
    "docs.retention-below-floor",
    "docs.incremental-key-misses-partition",
    "docs.empty-input-into-table",
)
RETENTION_FLOOR_DAYS = 7  # what model.Table.retention None takes; a table another product reads keeps at least it
FETCH_KINDS = frozenset(typing.get_args(model.ConnectorKind)) - {"kafka"}  # the kinds the fetch container runs
SPIKE_A = "experiment (a) of docs/research/2026-11-26-release-spine-spikes.md"
HISTORY_COLUMN = "valid_from"  # a type-2 table's (ADR-030): a key into it is joined as of a time
AS_OF_TYPE = "timestamp(6) with time zone"  # valid_from's: any other type compares in a time zone the reader sets

_PRODUCT_PATH = re.compile(r"contracts/([^/]+)/([^/]+)/(.+)")
_PARTITION_SOURCE = re.compile(r"(?:[a-z]+\()?([a-z][a-z0-9_]*)(?:, ?[0-9]+)?\)?")  # day(ts), bucket(id, 16), ts
_RELATIONSHIP = re.compile(r"([^/]+)/([^/]+)/([^/]+)\.odcs\.yaml#/schema/([^/]+)/properties/([^/]+)")


def _finding(code: str, path: str, message: str) -> Finding:
    assert code in CODES, code
    return Finding(code=code, severity="error", path=path, message=message)


@dataclass
class _Dir:
    """contracts/<domain>/<name>/: one product's documents, parsed."""

    domain: str
    name: str
    paths: list[str] = field(default_factory=list)
    product: Any = None
    contracts: dict[str, Any] = field(default_factory=dict)  # file stem, the table's name -> contract
    grants: Any = None
    models: dict[str, str] = field(default_factory=dict)  # path in the set -> SQL
    unit_tests: Any = None

    def at(self, file: str) -> str:
        return f"contracts/{self.domain}/{self.name}/{file}"

    def contract_id(self, table: str) -> str:
        return model.contract_id(self.domain, self.name, table)


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    shared, dirs, found = _read(docs.documents)
    if found:
        return found  # every rule below reads schema-valid documents
    return list(_rules(shared, dirs))


def _read(documents: Mapping[str, bytes]) -> tuple[dict[str, Any], dict[tuple[str, str], _Dir], list[Finding]]:
    shared: dict[str, Any] = {}
    dirs: dict[tuple[str, str], _Dir] = {}
    found: list[Finding] = []
    for path, data in sorted(documents.items()):
        kind = model.document_kind(path)
        place = _PRODUCT_PATH.fullmatch(path)
        if place and place[1] in model.RESERVED_DOMAIN_NAMES:
            place = None
        if not kind and not place:
            continue  # schemas, the compose file, parsers: not documents this check reads
        file = place[3] if place else ""
        doc: Any = None
        try:
            if file.startswith("models/"):
                doc = data.decode()
            elif kind or file == "unit_tests.yml":
                doc = model.parse(path, data)
        except (model.DocumentError, UnicodeDecodeError) as e:
            found.append(_finding("docs.unreadable", path, f"$: {str(e).removeprefix(path + ': ')}"))
            continue
        if kind:
            found += [_finding("docs.schema-invalid", path, e) for e in model.schema_errors(kind, doc)]
        if kind in ("domains", "principals"):
            shared[kind] = doc
        if place:
            d = dirs.setdefault((place[1], place[2]), _Dir(place[1], place[2]))
            d.paths.append(path)
            if file == model.V2_PRODUCT_FILE:
                d.product = doc
            elif kind == "contract":
                d.contracts[file.removesuffix(".odcs.yaml")] = doc
            elif kind == "grants":
                d.grants = doc
            elif file.startswith("models/"):
                d.models[path] = doc
            elif file == "unit_tests.yml":
                d.unit_tests = doc
    return shared, dirs, found


def _props(doc: Any) -> dict[str, Any]:
    return {p["property"]: p["value"] for p in doc.get("customProperties", [])}


def _prop_at(doc: Any, name: str) -> str:
    index = next(i for i, p in enumerate(doc["customProperties"]) if p["property"] == name)
    return f"$.customProperties[{index}].value"


def _build(contract: Any) -> str:
    build = _props(contract)["build"]
    return build if isinstance(build, str) else "code"


def _columns(contract: Any) -> list[str]:
    return [c["name"] for c in contract["schema"][0]["properties"]]


def _rules(shared: dict[str, Any], dirs: dict[tuple[str, str], _Dir]) -> Iterator[Finding]:
    domains: dict[str, Any] = shared.get("domains", {}).get("domains", {})
    owners = {p["id"]: p for p in shared.get("principals", {}).get("principals", [])}
    for name in domains:
        problem = model.domain_name_problem(name)
        if problem:
            yield _finding("docs.domain-name-invalid", model.DOMAINS, f"$.domains.{name}: domain {name}: {problem}; choose another name")
        elif model.fence(name) in model.TRINO_RESERVED:
            yield _finding("docs.name-reserved-word", model.DOMAINS, f"$.domains.{name}: {model.fence(name)}, the domain's catalog, is a Trino reserved word (model.TRINO_RESERVED); choose another name")
    for d in dirs.values():
        if d.product is None:
            for path in d.paths:
                yield _finding("docs.orphan-document", path, f"$: {d.at('')} has no {model.V2_PRODUCT_FILE}; a product's documents sit beside its product file, so add it or move this file")
    products = [d for d in dirs.values() if d.product is not None]
    tables = {d.contract_id(t): c for d in products for t, c in d.contracts.items()}
    read = {port["contractId"] for d in products for port in d.product.get("inputPorts", [])}
    by_id = {model.product_id(d.domain, d.name): d for d in products}
    feeds = {pid: {port["contractId"].rsplit(".", 1)[0] for port in d.product.get("inputPorts", [])} for pid, d in by_id.items()}
    for pid, d in by_id.items():
        upstream = [by_id[u] for u in sorted(_upstream(pid, feeds)) if u in by_id and u != pid]
        yield from _product(d, domains, owners, tables, upstream)
        for table, contract in d.contracts.items():
            yield from _table(d, table, contract, tables, read, by_id)
    yield from _taken("docs.product-name-taken", [(d.name, d.at(model.V2_PRODUCT_FILE)) for d in products],
                      "$.name: {} is also the name of {}; a product's name is unique on the platform, since its fence names its lake namespace, principals and DAG")
    dbt = [(model.dbt_model(d.name, t), d.at(f"{t}.odcs.yaml")) for d in products for t, c in d.contracts.items() if _build(c) == "dbt"]
    dbt += [(model.dbt_seed(d.name, s["path"]), d.at(model.V2_PRODUCT_FILE)) for d in products for s in _props(d.product).get("seeds", [])]
    yield from _taken("docs.dbt-model-name-taken", dbt,
                      "$.name: dbt model or seed {} is also built from {}; model.dbt_model and model.dbt_seed names are unique in the "
                      "one compiled dbt project, and each is a table of the product's base namespace")


def _taken(code: str, names: list[tuple[str, str]], message: str) -> Iterator[Finding]:
    first: dict[str, str] = {}
    for name, path in sorted(names, key=lambda n: n[1]):
        if name in first:
            yield _finding(code, path, message.format(name, first[name]))
        first.setdefault(name, path)


def _repeated(path: str, node: Any, at: str = "$") -> Iterator[Finding]:
    """Each customProperties list names a property once: a second would win or lose silently."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _repeated(path, item, f"{at}[{i}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _repeated(path, value, f"{at}.{key}")
        if isinstance(node.get("customProperties"), list):
            for name, n in Counter(p.get("property") for p in node["customProperties"]).items():
                if n > 1:
                    yield _finding("docs.property-repeated", path, f"{at}.customProperties: {name} appears {n} times; keep one")


def _upstream(pid: str, feeds: dict[str, set[str]]) -> set[str]:
    """Every product `pid` reads through input ports, directly or through another."""
    seen: set[str] = set()
    todo = list(feeds.get(pid, ()))
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo += feeds.get(p, ())
    return seen


def _platform_written(d: _Dir) -> bool:
    """Everything `d` holds was written on the platform, so no source's terms apply to it."""
    p = d.product
    return ("connector" not in _props(p) and not p.get("inputPorts")
            and bool(d.contracts) and all(_build(c) == "dbt" for c in d.contracts.values()))


def _product(d: _Dir, domains: dict[str, Any], owners: dict[str, Any], tables: dict[str, Any], upstream: list[_Dir]) -> Iterator[Finding]:
    p, path = d.product, d.at(model.V2_PRODUCT_FILE)
    props, team = _props(p), p["team"]["name"]
    yield from _repeated(path, p)
    expect = {"id": model.product_id(d.domain, d.name), "name": d.name, "domain": d.domain}
    wrong = [f"{k} is {p[k]}" for k, v in expect.items() if p[k] != v]
    if wrong:
        yield _finding("docs.product-misplaced", path, f"$: {', '.join(wrong)}; in {d.at('')} a product has id {expect['id']}, name {d.name} and domain {d.domain}: correct the fields or move the directory")
    problem = model.product_name_problem(d.name)
    if problem:
        yield _finding("docs.product-name-invalid", path, f"$.name: product {d.name}: {problem}; choose another name")
    elif model.fence(d.name) in model.TRINO_RESERVED:
        yield _finding("docs.name-reserved-word", path, f"$.name: {model.fence(d.name)}, the product's namespace, is a Trino reserved word (model.TRINO_RESERVED); choose another name")
    if d.domain not in domains:
        yield _finding("docs.domain-undeclared", path, f"$.domain: domain {d.domain} is not in {model.DOMAINS}; declare it there with its owner, or move the product under a declared domain")
    elif team != domains[d.domain]["owner"]:
        yield _finding("docs.team-mismatch", path, f"$.team.name: {team} is not the owner of domain {d.domain}, {domains[d.domain]['owner']} ({model.DOMAINS}); a product's team is its domain's owner")

    own, ports = set(map(d.contract_id, d.contracts)), Counter(port["contractId"] for port in p["outputPorts"])
    for i, port in enumerate(p["outputPorts"]):
        if port["contractId"] not in own:
            yield _finding("docs.output-port-foreign", path, f"$.outputPorts[{i}].contractId: {port['contractId']} is not a contract beside this product file; an output port names a table of its own product, which has one producer")
    for table in d.contracts:
        n = ports[d.contract_id(table)]
        if n == 0:
            yield _finding("docs.table-unpublished", path, f"$.outputPorts: no output port names {d.contract_id(table)}; add one for every contract beside the product file")
        elif n > 1:
            yield _finding("docs.output-port-repeated", path, f"$.outputPorts: {n} output ports name {d.contract_id(table)}; keep one")
    inputs = [port["contractId"] for port in p.get("inputPorts", [])]
    for i, cid in enumerate(inputs):
        if cid not in tables:
            yield _finding("docs.reference-unresolved", path, f"$.inputPorts[{i}].contractId: no contract in the set has id {cid}; name an existing table, contracts/<domain>/<product>/<table>.odcs.yaml")
    for i, port in enumerate(p["outputPorts"]):
        for j, ref in enumerate(port.get("inputContracts", [])):
            if ref["id"] not in inputs and ref["id"] not in own - {port["contractId"]}:
                yield _finding("docs.reference-unresolved", path, f"$.outputPorts[{i}].inputContracts[{j}].id: {ref['id']} is neither one of this product's input ports nor another table of it; declare it under inputPorts first")

    builds = {t: _build(c) for t, c in d.contracts.items()}
    if "connector" in builds.values() and "connector" not in props:
        yield _finding("docs.connector-mismatch", path, f"$.customProperties: tables {', '.join(t for t, b in builds.items() if b == 'connector')} are built by the connector, and the product declares none; add the connector property or change their build")
    if "connector" in props and "connector" not in builds.values():
        yield _finding("docs.connector-mismatch", path, f"{_prop_at(p, 'connector')}: the product declares a connector but no table is built by it; a connector publishes bronze, so add its bronze table or remove the connector")
    if builds and set(builds.values()) == {"serving"} and p["status"] != "paused":
        yield _finding("docs.serving-only-not-paused", path, "$.status: every table is build serving, so the product has no lake table and nothing runs it; a serving-only product is valid only paused (ADR-057), so set status paused with its lifecycleChange, or give a table a lake build")

    lacking = [model.product_id(u.domain, u.name) for u in upstream if "terms" not in _props(u.product) and not _platform_written(u)]
    for i, grant in enumerate(d.grants["grants"] if d.grants is not None else []):
        who = owners.get(grant["principal"])  # an undeclared principal is the access check's
        if who is None or (who["owner"] == team and who["kind"] != "partner"):
            continue
        outside = ("is a partner, another organisation's service, outside every team" if who["kind"] == "partner"
                   else f"belongs to {who['owner']}, outside the product's team {team}")
        if "terms" not in props:
            yield _finding("docs.terms-missing", d.at("grants.yaml"), f"$.grants[{i}]: {grant['principal']} {outside}; record the source's terms in the product's terms property before a grant outside the owning team (ADR-055 part 5)")
        if lacking:
            yield _finding("docs.terms-missing", d.at("grants.yaml"), f"$.grants[{i}]: {grant['principal']} {outside}, and reads through it the data of {', '.join(lacking)}, upstream through input ports, which record no terms; each records its source's terms before a grant outside the owning team downstream of it (ADR-055 part 5)")

    # ponytail: any model of the product, not only those calling k2_input on the port: an ephemeral
    # or view between them passes the zero rows on. Name the readers when a product needs one exempt.
    rebuilt = sorted(t for t, b in builds.items() if b == "dbt" and not _incremental(d.models.get(d.at(f"models/{t}.sql"))))
    for i, port in enumerate(p.get("inputPorts", [])):
        if rebuilt and _props(port).get("whenAbsent") == "empty":
            yield _finding("docs.empty-input-into-table", path, f"$.inputPorts[{i}].customProperties: whenAbsent empty reads {port['contractId']} as zero rows while it is paused or never audited, and a build then rebuilds this product's models that are not incremental ({', '.join(rebuilt)}) from those rows and publishes them empty; make the models incremental, read the input at its last audited snapshot (whenAbsent fail), or accept it with whenAbsent emptyIntoTable")

    models = {d.at(f"models/{t}.sql") for t, b in builds.items() if b == "dbt"}
    for model_path in d.models:
        if model_path not in models:
            yield _finding("docs.model-without-dbt-table", model_path, f"$: no build dbt table of this product is named by this file; a product's models are models/<table>.sql, one per dbt table ({', '.join(sorted(models)) or 'none'}), so a table has one producer")
    if d.unit_tests is not None:
        names = {model.dbt_model(d.name, t) for t, b in builds.items() if b == "dbt"}
        incremental = {model.dbt_model(d.name, t) for t, b in builds.items() if b == "dbt" and _incremental(d.models.get(d.at(f"models/{t}.sql")))}
        for i, test in enumerate(d.unit_tests.get("unit_tests", []) if isinstance(d.unit_tests, dict) else []):
            name = test.get("model") if isinstance(test, dict) else None
            if name not in names:
                yield _finding("docs.unit-test-model-unknown", d.at("unit_tests.yml"), f"$.unit_tests[{i}].model: {name} is not a dbt model of this product; name it <fence>__<table> (model.dbt_model): {', '.join(sorted(names)) or 'none'}")
            elif name in incremental and not isinstance(((test.get("overrides") or {}).get("macros") or {}).get("is_incremental"), bool):
                yield _finding("docs.unit-test-branch-unstated", d.at("unit_tests.yml"), f"$.unit_tests[{i}].overrides.macros.is_incremental: {name} is incremental, so its unit test states which branch it tests, true or false; dbt refuses to parse a unit test of an incremental model without it")


def _incremental(sql: str | None) -> bool:
    try:
        return sql is not None and model.model_config(sql).get("materialized") == "incremental"
    except ValueError:  # docs.model-config-unreadable reports it
        return False


def _table(d: _Dir, table: str, c: Any, tables: dict[str, Any], read: set[str], by_id: dict[str, _Dir]) -> Iterator[Finding]:
    path, props, build = d.at(f"{table}.odcs.yaml"), _props(c), _build(c)
    spec, columns = c["schema"][0], c["schema"][0]["properties"]
    names = _columns(c)
    yield from _repeated(path, c)
    expect = {"id": (c["id"], d.contract_id(table)), "name": (c["name"], table), "domain": (c["domain"], d.domain),
              "schema[0].id": (spec["id"], table), "schema[0].name": (spec["name"], table)}
    wrong = [f"{k} is {v[0]}, not {v[1]}" for k, v in expect.items() if v[0] != v[1]]
    if wrong:
        yield _finding("docs.contract-misplaced", path, f"$: {'; '.join(wrong)}: a contract's id, name and domain follow its path, contracts/<domain>/<product>/<table>.odcs.yaml")
    if c["team"]["name"] != d.product["team"]["name"]:
        yield _finding("docs.team-mismatch", path, f"$.team.name: {c['team']['name']} is not the product's team, {d.product['team']['name']}")
    if build != "serving" and spec["physicalName"] != f"{model.fence(d.name)}.{table}":
        yield _finding("docs.physical-name-mismatch", path, f"$.schema[0].physicalName: {spec['physicalName']} is not {model.fence(d.name)}.{table}, the product's fence and the table")
    if table in model.RESERVED_TABLE_NAMES:
        yield _finding("docs.table-name-reserved", path, f"$.name: {table} is one of the platform's own tables in a product's lake namespace (model.RESERVED_TABLE_NAMES); choose another name")

    for i, col in enumerate(columns):
        if not str(col.get("description") or "").strip():
            yield _finding("docs.column-undescribed", path, f"$.schema[0].properties[{i}]: column {col['name']} has no description; say what one value is, for the catalog page and the dbt docs")
        problem = model.physical_type_problem(col["physicalType"])
        if problem:
            yield _finding("docs.physical-type-invalid", path, f"$.schema[0].properties[{i}].physicalType: {problem}")
        for j, rel in enumerate(col.get("relationships", [])):
            yield from _relationship(d, c, col, path, f"$.schema[0].properties[{i}].relationships[{j}]", rel, tables, by_id)

    for i, entry in enumerate(spec.get("quality", [])):
        try:
            quality_test(model.quality_of(entry), build, names)
        except (ValueError, model.DocumentError) as e:
            yield _finding("docs.quality-unsupported", path, f"$.schema[0].quality[{i}]: quality rule {entry.get('name') or entry.get('description') or i}: {str(e).removeprefix('implementation: ')}; K2 would run nothing for it")

    named = [(_prop_at(c, "partitioning"), _source(f)) for f in props.get("partitioning", [])]
    named += [(_prop_at(c, "sort"), s.split()[0]) for s in props.get("sort", [])]
    for role, policy in props.get("policies", {}).items():
        at = f"{_prop_at(c, 'policies')}.{role}"
        named += [(f"{at}.{kind}.column", policy[kind]["column"]) for kind in ("delay", "rowScope") if kind in policy]
        named += [(f"{at}.mask", m) for m in policy.get("mask", [])]
    for pointer, name in named:
        if name not in names:
            yield _finding("docs.column-unknown", path, f"{pointer}: {name} is not a column of {table}; name one of {', '.join(names)}")
    for i, sla in enumerate(c.get("slaProperties", [])):
        if sla.get("property") == "latency" and sla["element"] not in {f"{table}.{n}" for n in names}:
            yield _finding("docs.column-unknown", path, f"$.slaProperties[{i}].element: {sla['element']} is not {table}.<column> of this table")

    if (props["layer"] == "bronze") != (build == "connector"):
        yield _finding("docs.layer-build-mismatch", path, f"{_prop_at(c, 'layer')}: layer {props['layer']} with build {build}; bronze is written only by its product's connector and a connector writes only bronze (ADR-056 part 2), so a table is both or neither")
    if build == "connector":
        if not table.startswith("bronze_"):
            yield _finding("docs.bronze-name", path, f"$.name: a connector's table is named bronze_<name> (lake.<fence>.bronze_<name>, pipeline.schema.json); rename {table}")
        if not any(col.get("primaryKey") for col in columns):
            yield _finding("docs.bronze-key-missing", path, "$.schema[0].properties: a connector's table has a primaryKey, the key its audit holds rows unique on (pipeline.schema.json bronze key); mark its key columns")
        connector = _props(d.product).get("connector")
        if connector and connector["kind"] in FETCH_KINDS and model.LINEAGE_COLUMN not in names:
            yield _finding("docs.lineage-column-missing", path, f"$.schema[0].properties: the {connector['kind']} connector's bronze table carries {model.LINEAGE_COLUMN}, the landed object's sha256 (model.LINEAGE_COLUMN); add it, physicalType varchar")
    if d.contract_id(table) in read and props.get("retention", RETENTION_FLOOR_DAYS) < RETENTION_FLOOR_DAYS:
        yield _finding("docs.retention-below-floor", path, f"{_prop_at(c, 'retention')}: another product reads {table} at a pinned snapshot, so it keeps at least {RETENTION_FLOOR_DAYS} days; raise it or leave it out")
    if build == "serving":
        ports = [port for port in d.product["outputPorts"] if port["contractId"] == d.contract_id(table)]
        if ports and not any("serves" in _props(port) for port in ports):
            yield _finding("docs.serving-without-serves", d.at(model.V2_PRODUCT_FILE), f"$.outputPorts: {table} is build serving, so its only copy is a serving engine's; its output port says which, with the serves property")
    if build == "dbt":
        yield from _dbt(d, table, c, names)


def _relationship(d: _Dir, c: Any, col: Any, path: str, at: str, rel: Any, tables: dict[str, Any],
                  by_id: dict[str, _Dir]) -> Iterator[Finding]:
    """One key of `col` (of contract `c`, product `d`) into another product's table, both ends named."""
    to = _RELATIONSHIP.fullmatch(rel["to"])  # the schema's pattern, so it matches
    assert to, rel["to"]
    here, pid = f"{c['id']}.{col['name']}", model.product_id(to[1], to[2])
    there = f"{model.contract_id(to[1], to[2], to[3])}.{to[5]}"
    product, target = by_id.get(pid), tables.get(model.contract_id(to[1], to[2], to[3]))
    if product is None:
        yield _finding("docs.reference-unresolved", path, f"{at}.to: {here} refers to {rel['to']}, and no product of the set has id {pid}; name contracts/<domain>/<product>/<table>.odcs.yaml of an existing product")
        return
    if product.product["status"] == "retired":
        yield _finding("docs.relationship-retired", path, f"{at}.to: {here} refers to {there}, whose product {pid} is retired (ADR-055 part 4): it builds no more rows, so the key resolves to nothing; refer to its successor, or drop the relationship")
        return
    if target is None or target["schema"][0]["id"] != to[4] or to[5] not in _columns(target):
        has = f"its tables: {', '.join(sorted(product.contracts))}" if target is None else f"columns of {to[3]}: {', '.join(_columns(target))}"
        yield _finding("docs.relationship-column-unknown", path, f"{at}.to: {here} refers to {there}, which {pid} does not have ({has})")
        return
    theirs = next(t["physicalType"] for t in target["schema"][0]["properties"] if t["name"] == to[5])
    if theirs != col["physicalType"]:
        yield _finding("docs.relationship-type-mismatch", path, f"{at}.to: {here} is {col['physicalType']} and refers to {there}, a {theirs}: a key compares equal only at one type, and Trino would cast one side in the join; make them one type")
    if target["id"] not in {port["contractId"] for port in d.product.get("inputPorts", [])}:
        yield _finding("docs.foreign-key-not-read", path, f"{at}.to: {here} refers to {there}, and {target['id']} is not an input port of {model.product_id(d.domain, d.name)}: the key's test reads the target through k2_input at its tested snapshot, as the build does, and policy inheritance runs over input ports; add it as an input port, or drop the relationship")
    props = _props(rel)
    as_of = {k: props[k] for k in ("asOf", "asOfColumn") if k in props}
    if HISTORY_COLUMN not in _columns(target):
        if as_of:
            yield _finding("docs.relationship-as-of-unexpected", path, f"{at}.customProperties: {here} refers to {there}, whose table keeps no history (no {HISTORY_COLUMN}), and declares {', '.join(as_of)}; one row per key holds at every time, so drop them")
        return
    if len(as_of) < 2:
        yield _finding("docs.relationship-as-of-missing", path, f"{at}.customProperties: {here} refers to {there}, whose table keeps history ({HISTORY_COLUMN}, ADR-030), and does not state its point in time; add asOf (valid or known) and asOfColumn (the {c['name']} column the join is as of)")
        return
    types = {x["name"]: x["physicalType"] for x in c["schema"][0]["properties"]}
    if types.get(as_of["asOfColumn"]) != AS_OF_TYPE:
        what = f"a {types[as_of['asOfColumn']]}" if as_of["asOfColumn"] in types else f"not a column of {c['name']}"
        yield _finding("docs.relationship-as-of-column", path, f"{at}.customProperties: {here} joins {there} as of {as_of['asOfColumn']}, {what}; name a {AS_OF_TYPE} column of {c['name']}, the type of {HISTORY_COLUMN}: any other compares in a time zone the reader sets")


def _source(partition_field: str) -> str:
    """The column a partition field reads: day(read_at) -> read_at, bucket(id, 16) -> id."""
    match = _PARTITION_SOURCE.fullmatch(partition_field)
    return match[1] if match else partition_field


def _dbt(d: _Dir, table: str, c: Any, names: list[str]) -> Iterator[Finding]:
    model_path = d.at(f"models/{table}.sql")
    if model_path not in d.models:
        yield _finding("docs.model-missing", d.at(f"{table}.odcs.yaml"), f"$: {table} is build dbt and {model_path} does not exist; write the model, or change the build")
        return
    try:
        config = model.model_config(d.models[model_path])
    except ValueError as e:
        yield _finding("docs.model-config-unreadable", model_path, f"$: {e}; config() takes literal keyword arguments only, once (model.model_config)")
        return
    if config.get("materialized") != "incremental":
        return
    sources = [s for s in map(_source, _props(c).get("partitioning", [])) if s in names]
    key = [col["name"] for col in c["schema"][0]["properties"] if col.get("primaryKey")]
    why = (f"{SPIKE_A} found that a merge key holding the partition column prunes the MERGE to the partitions a batch "
           "touches and stays correct, while the incremental_predicates window that would prune it instead inserted a "
           "late row as a second row with the same key, and dbt reported success")
    missing = [s for s in sources if s not in key]
    if missing:
        yield _finding("docs.incremental-key-misses-partition", d.at(f"{table}.odcs.yaml"), f"{_prop_at(c, 'partitioning')}: {table} is an incremental dbt table partitioned on {', '.join(missing)}, which its primaryKey ({', '.join(key) or 'none'}) does not hold; add it to the key, which is the MERGE's unique_key: {why}")
    unique_key = config.get("unique_key")
    if unique_key is not None:
        held = [unique_key] if isinstance(unique_key, str) else list(unique_key)
        missing = [s for s in sources if s not in held]
        if missing:
            yield _finding("docs.incremental-key-misses-partition", model_path, f"$: config(unique_key=...) lacks {', '.join(missing)}, a partition source column of {table}; hold every one, or leave unique_key out so the model takes the primaryKey: {why}")
