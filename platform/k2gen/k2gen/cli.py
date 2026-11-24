"""k2gen: the data product contracts under contracts/, checked (ADR-032).

    uv run --project platform/k2gen k2gen validate

`validate` checks, in order:

1. Every file against its schema: `contracts/domains.yaml` and each
   `product.yaml` against contracts/schema/k2.schema.json, and each
   `*.odcs.yaml` against the vendored Open Data Contract Standard schema, then
   K2's profile of it, then its custom properties.
2. Each product and contract against its place: the directory it lives in, its
   domain's owner and kind, the outputs it lists against the contracts beside it.
3. Each contract against docker/lake/ddl/lake.sql: the table exists, and its
   partitioning and sort order are the ones declared there. Every table outside
   `audit` has exactly one contract.
4. Three rules across products: a table another table reads keeps snapshots for
   at least the seven-day floor; a table is at least as restricted as anything
   it reads; and every input resolves to a declared output or a declared
   external source.
"""

import argparse
import json
import sys
from pathlib import Path

import jsonschema
import yaml

from k2gen import ddl

ROOT = Path(__file__).resolve().parents[3]
ODCS_SCHEMA = "odcs-json-schema-v3.2.0-20261117.json"
LAKE_SQL = "docker/lake/ddl/lake.sql"
SUFFIX = ".odcs.yaml"
# docker/lake/maintenance.py refuses --retain-days below 7: stage 2 resumes from
# the parent snapshot id it last read, and expiring that snapshot breaks the next run.
FLOOR_DAYS = 7
RANK = {"public": 0, "internal": 1, "restricted": 2}


def _k2(schema: dict, name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]})


def _schema_errors(validator, doc, where: str) -> list:
    return [f"{where}: {e.json_path}: {e.message}" for e in validator.iter_errors(doc)]


def validate(root: Path = ROOT) -> list:
    """Every problem found under `root`, as one line each. Empty means valid."""
    contracts = root / "contracts"
    k2 = json.loads((contracts / "schema" / "k2.schema.json").read_text())
    odcs = jsonschema.Draft201909Validator(json.loads((contracts / "schema" / ODCS_SCHEMA).read_text()))

    def rel(path: Path) -> str:
        return str(path.relative_to(root))

    # 1. schemas. The checks after this assume well-formed documents, so stop here on any.
    domains = yaml.safe_load((contracts / "domains.yaml").read_text())
    errors = _schema_errors(_k2(k2, "domains"), domains, "contracts/domains.yaml")
    products = {}  # product directory -> descriptor
    for path in sorted(contracts.glob("*/*/product.yaml")):
        products[path.parent] = yaml.safe_load(path.read_text())
        errors += _schema_errors(_k2(k2, "product"), products[path.parent], rel(path))
    docs = {}  # contract path -> (contract, its custom properties as one object)
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

    # 2. place: directory, domain, outputs.
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

    # 3. lake.sql.
    lake = {t: v for t, v in ddl.parse(root / LAKE_SQL).items() if not t.startswith("audit.")}
    home, props_of = {}, {}  # table -> product directory, table -> custom properties
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
        if table not in lake:
            errors.append(f"{where}: {table} is not a table in {LAKE_SQL}")
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
        read = set()
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
        errors += [f"{where}: input {name} is read by none of its outputs" for name in sorted(set(external) | set(tables)) if name not in read]
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="k2gen", description="Data product contracts for the K2 lake (ADR-032).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="check every descriptor and contract under contracts/")
    parser.parse_args(argv)

    errors = validate()
    for line in errors:
        print(line, file=sys.stderr)
    if errors:
        print(f"k2gen validate: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    products = len(list((ROOT / "contracts").glob("*/*/product.yaml")))
    contracts = len(list((ROOT / "contracts").glob(f"*/*/*{SUFFIX}")))
    print(f"k2gen validate: {products} products, {contracts} contracts, every lake table outside audit covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
