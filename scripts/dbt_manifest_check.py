#!/usr/bin/env python3
"""The dbt project held to its layering and to the contracts (make test-dbt, CI job dbt).

    cd dbt && uv run --locked python ../scripts/dbt_manifest_check.py target/manifest.json

Reads dbt's manifest after `dbt compile`, and the domains and marts the contracts
declare (contracts/domains.yaml, contracts/<domain>/<product>/product.yaml). Fails on:

1. Layering: a model outside staging that selects from a source. A lake table is read
   by one staging view per domain; marts and intermediate models read those.
2. Place: a model whose catalog is not a domain's fence name (ADR-044).
3. Marts, each model under models/<catalog>/marts/: it is a table; a contract declares
   it at its relation <catalog>.<product>.<model>; it has a uniqueness test on exactly
   the contract's grain, over the whole table (no row_condition, no where); and the lake
   tables it reads, through any number of models, are exactly the contract's `reads`.
4. Every mart a contract declares has its model.

Standard library and PyYAML, both in dbt/'s environment and in the root test run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def contracts(root: Path = ROOT) -> tuple[set[str], dict[str, dict]]:
    """(domain fence names, {mart relation: its contract entry plus the product file})."""
    domains = yaml.safe_load((root / "contracts" / "domains.yaml").read_text())["domains"]
    marts = {}
    for path in sorted((root / "contracts").glob("*/*/product.yaml")):
        for mart in yaml.safe_load(path.read_text()).get("marts", []):
            marts[mart["table"]] = {**mart, "product": str(path.relative_to(root))}
    return {d.replace("-", "_") for d in domains}, marts


def _sources_read(manifest: dict, uid: str) -> set[str]:
    """Every source upstream of `uid`, as <schema>.<table> for those in catalog lake."""
    seen, todo, found = set(), [uid], set()
    while todo:
        for parent in manifest["parent_map"].get(todo.pop(), []):
            if parent in seen:
                continue
            seen.add(parent)
            if parent in manifest["sources"]:
                src = manifest["sources"][parent]
                found.add(f"{src['schema']}.{src['name']}" if src["database"] == "lake" else f"{src['database']}.{src['schema']}.{src['name']}")
            else:
                todo.append(parent)
    return found


def _unique_on(test: dict) -> set[str] | None:
    """The columns a test proves unique over the whole table, or None if it is not such a test."""
    meta, kwargs = test.get("test_metadata") or {}, (test.get("test_metadata") or {}).get("kwargs", {})
    if test["config"].get("where") or kwargs.get("row_condition"):
        return None  # unique over a slice proves nothing about the grain
    if meta.get("name") == "expect_compound_columns_to_be_unique" and meta.get("namespace") == "dbt_expectations":
        return set(kwargs["column_list"])
    if meta.get("name") == "unique" and not meta.get("namespace"):
        return {kwargs["column_name"]}
    return None


def check(manifest: dict, fences: set[str], marts: dict[str, dict]) -> list[str]:
    errors = []
    models = {uid: n for uid, n in manifest["nodes"].items() if n["resource_type"] == "model"}
    tests = [n for n in manifest["nodes"].values() if n["resource_type"] == "test"]
    built = set()
    for uid, node in sorted(models.items()):
        where, layer = node["original_file_path"], node["fqn"][2] if len(node["fqn"]) > 2 else ""
        if layer != "staging" and any(p in manifest["sources"] for p in manifest["parent_map"].get(uid, [])):
            errors.append(f"{where}: selects from a source; only a staging model may, so read its staging view")
        if node["database"] not in fences:
            errors.append(f"{where}: catalog {node['database']} is not a domain's in contracts/domains.yaml")
        if layer != "marts":
            continue
        relation = f"{node['database']}.{node['schema']}.{node['alias']}"
        built.add(relation)
        if node["config"]["materialized"] != "table":
            errors.append(f"{where}: a mart is a table, not a {node['config']['materialized']} (dbt_project.yml, one block per domain)")
        mart = marts.get(relation)
        if mart is None:
            errors.append(f"{where}: builds {relation}, which no product.yaml declares under marts")
            continue
        grains = [cols for t in tests if t.get("attached_node") == uid and (cols := _unique_on(t)) is not None]
        if set(mart["grain"]) not in grains:
            errors.append(f"{where}: no uniqueness test on its grain {mart['grain']} ({mart['product']}); it has {[sorted(g) for g in grains]}")
        reads = _sources_read(manifest, uid)
        if reads != set(mart["reads"]):
            errors.append(f"{where}: reads {sorted(reads)}, but {mart['product']} declares {sorted(mart['reads'])}")
    errors += [f"{m['product']}: mart {t} has no model at models/{t.split('.')[0]}/marts/{t.split('.')[1]}/{t.split('.')[2]}.sql" for t, m in sorted(marts.items()) if t not in built]
    return errors


def main(argv: list[str]) -> int:
    manifest = json.loads(Path(argv[1] if len(argv) > 1 else "target/manifest.json").read_text())
    fences, marts = contracts()
    errors = check(manifest, fences, marts)
    for e in errors:
        print(f"dbt manifest check: {e}", file=sys.stderr)
    n = sum(1 for n in manifest["nodes"].values() if n["resource_type"] == "model")
    print(f"dbt manifest check: {'FAIL' if errors else 'PASS'}, {n} models, {len(marts)} contract marts, {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
