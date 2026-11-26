"""The compiler's emitters (A3): one module per output, each owned by one step.

Every module in this package exposes

    emit(model: Model, previous: Release | None) -> dict[str, str]

returning release-relative path to file text, and is found by pkgutil.iter_modules, so
adding an emitter edits no shared list. An emitter is pure: no clock, no git, no network,
no secret. Two emitters returning one path is a compile error (k2gen.release, S08).
"""

import importlib
import pkgutil
from types import ModuleType


def modules() -> list[ModuleType]:
    """Every emitter module, by name."""
    return [importlib.import_module(f"{__name__}.{m.name}") for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name)]
