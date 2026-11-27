"""The compiler's emitters (A3): one module per output, each owned by one step.

Every module in this package exposes

    emit(model: Model) -> dict[str, str]

returning release-relative path (under compiled/) to file text, and is found by
pkgutil.iter_modules, so adding an emitter edits no shared list. An emitter is pure: no clock,
no git, no network, no secret, and no previous release, so one document set and one image set
compile to one release id in CI and at deploy alike. What changed against the previous release
is the compiler's to work out (k2gen.release, model.NEEDS_BY_PREFIX).
"""

import importlib
import pkgutil
from types import ModuleType

from k2gen.model import Model


def modules() -> list[ModuleType]:
    """Every emitter module, by name."""
    return [importlib.import_module(f"{__name__}.{m.name}") for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name)]


def run(model: Model) -> dict[str, str]:
    """Every emitter's files, which k2gen.release writes. Two emitters returning one path, or a
    path outside compiled/, is an error, and so is an emitter still a stub (NotImplementedError):
    since step S23 a compile emits every file or none."""
    files: dict[str, str] = {}
    owner: dict[str, str] = {}
    for module in modules():
        name = module.__name__.rsplit(".", 1)[1]
        try:
            out = module.emit(model)
        except NotImplementedError as stub:
            raise ValueError(f"emitter {name} is a stub (step {stub})") from stub
        for path in out:
            if not path.startswith("compiled/") or "/../" in f"/{path}/" or "//" in path:
                raise ValueError(f"emitter {name}: {path} is not a path under compiled/")
            if path in owner:
                raise ValueError(f"{path}: emitted by both {owner[path]} and {name}")
            owner[path] = name
        files.update(out)
    return files
