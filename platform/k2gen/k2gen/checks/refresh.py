"""Refresh checks (W1-6): what a product's `refresh` property may name before k2gen compiles it into
pipeline.json's `refresh`, the product whose DAG the factory's refresh task starts when a build is
degraded (docs/development/phase-001-interfaces.md, "Holds"). Runs after the documents check, so
every document here is schema-valid. Each rule a code in CODES:

- target-unresolved: the target is a product of the set, and not retired;
- target-self: the target is another product, never the one that names it;
- target-unscheduled: the target has a schedule, so a DAG to start (a product with none has none);
- target-not-upstream: the target is upstream of the product through its input ports, directly or
  through other products (kraken-instruments feeds kraken-trades through the security master): a
  degraded build may start only something it depends on, never an arbitrary DAG;
- target-chains: the target declares no refresh of its own, so the chain is one hop (the factory's
  parse refuses one too);
- never-degraded: the product has a warn-severity dbt test, a custom dbt rule whose test's config
  says `severity: warn` on a dbt table: only a failed one makes a build degraded (k2_dbt.py), so a
  refresh on a product without one could never run.

Failing cases: tests/test_checks_refresh.py, each one change to the good set's refresh overlay.
"""

from __future__ import annotations

from typing import Any

from k2gen import model
from k2gen.checks.docs import _build, _prop_at, _props, _read
from k2gen.emit.dbt import quality_test
from k2gen.model import DocumentSet, Finding

CODES = (
    "refresh.target-unresolved",
    "refresh.target-self",
    "refresh.target-unscheduled",
    "refresh.target-not-upstream",
    "refresh.target-chains",
    "refresh.never-degraded",
)


def _finding(code: str, path: str, message: str) -> Finding:
    assert code in CODES, code
    return Finding(code=code, severity="error", path=path, message=message)


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    _, dirs, found = _read(docs.documents)
    if found:
        return []  # the documents check reports them, and stops the run before this one
    products = {d.product["id"]: d for d in dirs.values() if d.product is not None}
    feeds = {pid: {port["contractId"].rsplit(".", 1)[0] for port in d.product.get("inputPorts", ())}
             for pid, d in products.items()}
    out: list[Finding] = []
    for pid, d in sorted(products.items()):
        target = _props(d.product).get("refresh")
        if target is None:
            continue
        at, path = _prop_at(d.product, "refresh"), d.at(model.V2_PRODUCT_FILE)
        if not _warns(d):
            out.append(_finding("refresh.never-degraded", path, f"{at}: {pid} has no warn-severity dbt test (a custom dbt rule on a dbt table whose test's config says severity: warn), and only a failed one makes its build degraded; a refresh it can never start is refused: add the rule, or leave the property out"))
        if target == pid:
            out.append(_finding("refresh.target-self", path, f"{at}: {pid} names itself; a degraded build starts another product's DAG, one its build reads"))
            continue
        t = products.get(target)
        if t is None or t.product["status"] == "retired":
            out.append(_finding("refresh.target-unresolved", path, f"{at}: {target} is {'retired' if t else 'no product of the set'}; name a live product this one reads"))
            continue
        if "schedule" not in _props(t.product):
            out.append(_finding("refresh.target-unscheduled", path, f"{at}: {target} has no schedule, so no DAG for the refresh to start"))
        if target not in _upstream(pid, feeds):
            out.append(_finding("refresh.target-not-upstream", path, f"{at}: {target} is not upstream of {pid} through its input ports ({', '.join(sorted(_upstream(pid, feeds))) or 'none'}); a degraded build may start only a product its build depends on"))
        if "refresh" in _props(t.product):
            out.append(_finding("refresh.target-chains", path, f"{at}: {target} names its own refresh, {_props(t.product)['refresh']}; a refresh is one hop, so a target declares none"))
    return out


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


def _warns(d: Any) -> bool:
    """A custom dbt rule on a dbt table whose one test's config says severity warn."""
    for c in d.contracts.values():
        if _build(c) != "dbt":
            continue
        for q in c["schema"][0].get("quality", ()):
            if q.get("type") == "custom" and q.get("engine") == "dbt":
                tests = (quality_test(model.quality_of(q), "dbt", []) or {}).values()
                if any(str(((t or {}).get("config") or {}).get("severity", "")).lower() == "warn" for t in tests):
                    return True
    return False
