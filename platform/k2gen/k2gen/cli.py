"""k2gen: the data product contracts under contracts/, checked (ADR-032).

    uv run --project platform/k2gen k2gen validate
    uv run --project platform/k2gen k2gen generate
    uv run --project platform/k2gen k2gen new <domain> <name>

`generate` and `new` are in k2gen/generate.py. `validate` checks, in order:

1. Every file against its schema: `contracts/domains.yaml` and each
   `product.yaml` against contracts/schema/k2.schema.json, and each
   `*.odcs.yaml` against the vendored Open Data Contract Standard schema, then
   K2's profile of it, then its custom properties.
2. Each product and contract against its place: the directory it lives in, its
   domain's owner and kind, the outputs it lists against the contracts beside it.
   And each domain's fence name, its id with underscores, against the names the
   lake and Trino already use (`RESERVED`); and each mart's name against its
   place, `<domain catalog>.<product schema>.<table>` (ADR-044).
3. Each contract against docker/lake/ddl/lake.sql: the table exists, and its
   partitioning and sort order are the ones declared there. Every table has
   exactly one contract, `audit` tables included: the nightly contract_schema
   audit (docker/lake/contract_schema.py) fails a table without one in any
   namespace the contracts own. A contract with inline columns is a table
   lake.sql does not create, and its partitioning and sort name its own columns.
   A latency service level names a column of its own table. A paused table
   (ADR-050) names a real day and an ADR file that exists, and promises no
   latency: nothing may promise freshness for a table nothing feeds.
4. Three rules across products: a table another table reads keeps snapshots for
   at least the seven-day floor; a table is at least as restricted as anything
   it reads; and every input resolves to a declared output or a declared
   external source. A mart reads only its product's inputs and outputs.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from k2gen import ddl

ROOT = Path(__file__).resolve().parents[3]
ODCS_SCHEMA = "odcs-json-schema-v3.2.0-20261117.json"
LAKE_SQL = "docker/lake/ddl/lake.sql"
ADR_DIR = "docs/adr"
SUFFIX = ".odcs.yaml"
# docker/lake/maintenance.py refuses --retain-days below 7: stage 2 resumes from
# the parent snapshot id it last read, and expiring that snapshot breaks the next run.
FLOOR_DAYS = 7
RANK = {"public": 0, "internal": 1, "restricted": 2}
# Names a domain's fence cannot take: its Trino catalog and Lakekeeper warehouse are
# the domain id with underscores (ADR-044), and these are already somebody's.
RESERVED = {
    "lake": "Trino's read-only catalog over the lake",
    "system": "Trino's own catalog",
    "k2": "the lake's warehouse",
    "information_schema": "the schema name every Trino catalog has",
    # Trino reserved words: a catalog named one would need quoting in every query.
    **{w: "a Trino reserved word" for w in ("table", "select", "schema", "catalog", "default", "user")},
}


# Product names a product cannot take: its marts' schema is the name with underscores, and
# these are every domain catalog's platform schemas, not products (ADR-044, ADR-046).
LAYER_SCHEMAS = {
    "staging": "the schema dbt keeps every domain's staging models in (dbt/macros/naming.sql)",
    "intermediate": "the schema dbt keeps every domain's intermediate models in (dbt/macros/naming.sql)",
    "dq": "the schema every domain's dbt run records its test results in (dbt/macros/record_test_results.sql)",
}


def fence(domain: str) -> str:
    """A domain's Trino catalog and Lakekeeper warehouse: `market-analytics` -> `market_analytics`."""
    return domain.replace("-", "_")


def _k2(schema: dict[str, Any], name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]})


def _schema_errors(validator: jsonschema.protocols.Validator, doc: Any, where: str) -> list[str]:
    return [f"{where}: {e.json_path}: {e.message}" for e in validator.iter_errors(doc)]


def validate(root: Path = ROOT) -> list[str]:
    """Every problem found under `root`, as one line each. Empty means valid."""
    contracts = root / "contracts"
    k2 = json.loads((contracts / "schema" / "k2.schema.json").read_text())
    odcs = jsonschema.Draft201909Validator(json.loads((contracts / "schema" / ODCS_SCHEMA).read_text()))

    def rel(path: Path) -> str:
        return str(path.relative_to(root))

    # 1. schemas. The checks after this assume well-formed documents, so stop here on any.
    domains = yaml.safe_load((contracts / "domains.yaml").read_text())
    errors = _schema_errors(_k2(k2, "domains"), domains, "contracts/domains.yaml")
    products: dict[Path, Any] = {}  # product directory -> descriptor
    for path in sorted(contracts.glob("*/*/product.yaml")):
        products[path.parent] = yaml.safe_load(path.read_text())
        errors += _schema_errors(_k2(k2, "product"), products[path.parent], rel(path))
    docs: dict[Path, tuple[Any, dict[str, Any]]] = {}  # contract path -> (contract, its custom properties)
    for path in sorted(contracts.glob(f"*/*/*{SUFFIX}")):
        doc = yaml.safe_load(path.read_text())
        found = _schema_errors(odcs, doc, rel(path)) or _schema_errors(_k2(k2, "contract"), doc, rel(path))
        if not found:
            pairs = [(p["property"], p["value"]) for p in doc["customProperties"]]
            props = dict(pairs)
            if len(props) != len(pairs):
                found.append(f"{rel(path)}: a custom property is declared twice")
            found += _schema_errors(_k2(k2, "customProperties"), props, f"{rel(path)}: customProperties")
            docs[path] = (doc, props)
        errors += found
    if errors:
        return errors

    # 2. place: directory, domain, outputs; and each domain's fence name.
    for name in domains["domains"]:
        if fence(name) in RESERVED:
            errors.append(f"contracts/domains.yaml: domain {name} would be catalog and warehouse {fence(name)}, which is {RESERVED[fence(name)]}")
    for pdir, product in products.items():
        where = rel(pdir / "product.yaml")
        if (pdir.parent.name, pdir.name) != (product["domain"], product["name"]):
            errors.append(f"{where}: must live at contracts/{product['domain']}/{product['name']}/")
        domain = domains["domains"].get(product["domain"])
        if domain is None:
            errors.append(f"{where}: domain {product['domain']} is not declared in contracts/domains.yaml")
            continue
        for key in ("owner", "kind"):
            if product[key] != domain[key]:
                errors.append(f"{where}: {key} {product[key]}, but its domain says {domain[key]}")
        beside = sorted(p.name[: -len(SUFFIX)] for p in pdir.glob(f"*{SUFFIX}"))
        if sorted(product["outputs"]) != beside:
            errors.append(f"{where}: outputs {sorted(product['outputs'])}, but the contracts beside it are {beside}")
        if fence(product["name"]) in LAYER_SCHEMAS:
            errors.append(f"{where}: product name {product['name']} is {LAYER_SCHEMAS[fence(product['name'])]}")
        place = f"{fence(product['domain'])}.{fence(product['name'])}"
        marts = [m["table"] for m in product.get("marts", [])]
        errors += [f"{where}: mart {t} must live in {place}, its domain's catalog and its product's schema (ADR-044)" for t in marts if t.rsplit(".", 1)[0] != place]
        errors += [f"{where}: mart {t} is declared twice" for t in sorted({t for t in marts if marts.count(t) > 1})]

    # 3. lake.sql.
    lake = ddl.parse(root / LAKE_SQL)
    home: dict[str, Path] = {}
    props_of: dict[str, dict[str, Any]] = {}  # table -> its custom properties
    for path, (doc, props) in docs.items():
        where, table = rel(path), doc["schema"][0]["physicalName"]
        product = products.get(path.parent)
        if product is None:
            errors.append(f"{where}: no product.yaml beside it")
            continue
        if table in home:
            errors.append(f"{where}: a second contract for {table}")
            continue
        home[table], props_of[table] = path.parent, props
        if path.name != table + SUFFIX:
            errors.append(f"{where}: describes {table}, so it must be named {table}{SUFFIX}")
        if doc["domain"] != product["domain"] or doc["team"]["name"] != product["owner"]:
            errors.append(f"{where}: domain and team.name must be the product's: {product['domain']}, {product['owner']}")
        if props["layer"] != table.split(".")[0]:
            errors.append(f"{where}: layer {props['layer']}, but {table} is in namespace {table.split('.')[0]}")
        inline = doc["schema"][0].get("properties")
        names = {c["name"] for c in inline} if inline else set(lake[table].columns) if table in lake else set()
        for sla in doc.get("slaProperties", []):
            if sla["property"] == "latency" and sla["element"] not in {f"{table}.{c}" for c in names}:
                errors.append(f"{where}: latency element {sla['element']} is not a column of {table}")
        errors += _paused_errors(root, where, doc, props.get("paused"))
        if inline:  # a new table: generate writes its DDL, lake.sql never holds it
            if table in lake:
                errors.append(f"{where}: {table} is created by {LAKE_SQL}, so the contract references it, not inline columns")
            for key in ("partitioning", "sort"):
                errors += [f"{where}: {key} {f} reads no column the contract declares" for f in props[key] if ddl.column_of(f) not in names]
            continue
        if table not in lake:
            errors.append(f"{where}: {table} is not a table in {LAKE_SQL}, and the contract declares no inline columns")
            continue
        for key in ("partitioning", "sort"):
            declared = list(getattr(lake[table], key))
            if props[key] != declared:
                errors.append(f"{where}: {key} {props[key]}, but {LAKE_SQL} declares {declared}")
    errors += [f"{LAKE_SQL}: {t} has no contract under contracts/" for t in lake if t not in home]

    # 4. across products.
    for pdir, product in products.items():
        where = rel(pdir / "product.yaml")
        external = {i["external"]: i["classification"] for i in product["inputs"] if "external" in i}
        tables = [i["table"] for i in product["inputs"] if "table" in i]
        for table in tables:
            if table not in home:
                errors.append(f"{where}: input {table} is not an output of any product")
            elif home[table] == pdir:
                errors.append(f"{where}: input {table} is one of its own outputs")
        readable = set(external) | set(tables) | set(product["outputs"])
        read: set[str] = set()
        for table in product["outputs"]:
            if home.get(table) != pdir:
                continue  # reported above
            props = props_of[table]
            for up in props["upstreams"]:
                read.add(up)
                if up not in readable or (up not in external and up not in props_of):
                    errors.append(f"{where}: {table} reads {up}, which is neither one of its outputs nor a resolved input")
                    continue
                cls = external[up] if up in external else props_of[up]["classification"]
                if RANK[props["classification"]] < RANK[cls]:
                    errors.append(f"{where}: {table} is {props['classification']} but reads {up}, which is {cls}")
                if up in props_of and props_of[up]["retention"] < FLOOR_DAYS:
                    errors.append(
                        f"{rel(home[up])}/{up}{SUFFIX}: {table} reads it, so it must keep snapshots for "
                        f"the {FLOOR_DAYS}-day floor, not {props_of[up]['retention']} (docker/lake/maintenance.py)"
                    )
        for mart in product.get("marts", []):
            for up in mart["reads"]:
                read.add(up)
                if up not in set(tables) | set(product["outputs"]) or up not in home:
                    errors.append(f"{where}: mart {mart['table']} reads {up}, which is neither one of its outputs nor a resolved input")
        errors += [f"{where}: input {name} is read by none of its outputs" for name in sorted(set(external) | set(tables)) if name not in read]
    return errors


def adr_file(root: Path, adr: str) -> Path | None:
    """`docs/adr/ADR-050-<title>.md` for `ADR-050`, or None if there is none."""
    return next(iter(sorted((root / ADR_DIR).glob(f"{adr}-*.md"))), None)


def _paused_errors(root: Path, where: str, doc: dict[str, Any], paused: dict[str, str] | None) -> list[str]:
    if paused is None:
        return []
    errors = []
    try:
        date.fromisoformat(paused["since"])
    except ValueError:
        errors.append(f"{where}: paused since {paused['since']}, which is not a day")
    if adr_file(root, paused["adr"]) is None:
        errors.append(f"{where}: paused by {paused['adr']}, which has no file in {ADR_DIR}/")
    if any(sla["property"] == "latency" for sla in doc.get("slaProperties", [])):
        errors.append(f"{where}: a paused table promises no freshness, so it declares no latency service level")
    return errors


def main(argv: list[str] | None = None) -> int:
    from k2gen import generate  # here, not at the top: generate.py imports this module's constants

    parser = argparse.ArgumentParser(prog="k2gen", description="Data product contracts for the K2 lake (ADR-032).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="check every descriptor and contract under contracts/")
    sub.add_parser("generate", help="validate, then rewrite every generated file from contracts/")
    new = sub.add_parser("new", help="scaffold contracts/<domain>/<name>/: a descriptor and one contract")
    new.add_argument("domain")
    new.add_argument("name")
    args = parser.parse_args(argv)

    if args.command == "new":
        for path in generate.scaffold(ROOT, args.domain, args.name):
            print(f"k2gen new: wrote {path.relative_to(ROOT)}")
        print("k2gen new: edit both, then `make generate` and commit what it writes")
        return 0
    errors = validate(ROOT)
    for line in errors:
        print(line, file=sys.stderr)
    if errors:
        print(f"k2gen {args.command}: {len(errors)} problem(s)" + (", nothing written" if args.command == "generate" else ""), file=sys.stderr)
        return 1
    if args.command == "generate":
        files = generate.generate(ROOT)
        print(f"k2gen generate: {len(files)} files: {generate.RETENTION}, {generate.CONTRACT_SCHEMAS}, {generate.WAREHOUSES}, {generate.TRINO_RULES}, "
              f"{generate.DBT_SOURCES}, {generate.DBT_SELECTORS}, "
              f"the domain catalogs in {generate.CATALOGS} and {generate.TEST_CATALOGS}, and under {', '.join(generate.OWNED)}")
        return 0
    products = len(list((ROOT / "contracts").glob("*/*/product.yaml")))
    contracts = len(list((ROOT / "contracts").glob(f"*/*/*{SUFFIX}")))
    print(f"k2gen validate: {products} products, {contracts} contracts, every lake table covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
