"""The checks library (A3): pure functions of a document set and the previous release's.

Every module in this package exposes

    check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]

and is found by pkgutil.iter_modules, so adding a check edits no shared list. `previous`
is None for a first release. A check never reads git or the network, so the same code runs
in CI now and in a registry later.
"""

import importlib
import pkgutil
from types import ModuleType

from k2gen.model import DocumentSet, Finding

FIRST = "docs"  # schemas and places: the others assume well-formed documents


def modules() -> list[ModuleType]:
    """Every check module, `docs` first, then by name."""
    names = sorted((m.name for m in pkgutil.iter_modules(__path__)), key=lambda n: (n != FIRST, n))
    return [importlib.import_module(f"{__name__}.{n}") for n in names]


def run(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    """Every finding, sorted by path and code. When `docs` reports an error, the other checks
    do not run: they parse the documents, which an invalid one would make fail."""
    found: list[Finding] = []
    for module in modules():
        found += module.check(docs, previous)
        if module.__name__.endswith(f".{FIRST}") and any(f.severity == "error" for f in found):
            break
    return sorted(found, key=lambda f: (f.path, f.code, f.message))
