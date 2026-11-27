"""Build order of a release's dbt selectors, from each product's input ports.

    python3 scripts/dbt_order.py <release dir> [selector]

Prints the selectors, each after the products its inputs name: all of them, or one selector
with its inputs closed transitively and nothing else. An unknown selector exits 1 listing the
valid ones. No containers: scripts/dbt-test.sh calls it before any starts.
"""
import json
import sys
from graphlib import TopologicalSorter
from pathlib import Path


def order(pipes, only=None):
    """pipes: parsed pipeline.json documents. Raises KeyError(sorted valid selectors) on an unknown `only`."""
    selector = {d["product"]: d["build"]["selector"] for d in pipes if d.get("build", {}).get("selector")}
    deps = {selector[d["product"]]: {selector[c] for i in d.get("inputs", []) if (c := i["contractId"].rsplit(".", 1)[0]) in selector}
            for d in pipes if d["product"] in selector}
    if only is not None:
        if only not in deps:
            raise KeyError(sorted(deps))
        keep, todo = set(), [only]
        while todo:
            if (s := todo.pop()) not in keep:
                keep.add(s)
                todo += deps[s]
        deps = {s: d for s, d in deps.items() if s in keep}
    return list(TopologicalSorter(deps).static_order())


if __name__ == "__main__":
    pipes = [json.loads(f.read_text()) for f in Path(sys.argv[1], "compiled/products").glob("*/pipeline.json")]
    try:
        print(" ".join(order(pipes, sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None)))
    except KeyError as e:
        sys.exit(f"dbt-test: unknown SELECTOR {sys.argv[2]!r}; valid: {' '.join(e.args[0])}")
