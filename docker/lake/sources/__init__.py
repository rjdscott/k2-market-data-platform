"""The parsers of the file sources (ADR-036), one module per source: `sources/<name>.py`.

A parser module has one required function and may have more:

    parse(data: bytes) -> list[dict]
        The landed file's bytes to its rows, one dict per row, keyed by the
        table's columns except `source_file_sha256`, which file_source.py adds.
        Values are the Python types Spark takes for the column (str for STRING,
        datetime.date for DATE). A value the source publishes as a number is kept
        as the string it published; the audits compare it as a decimal. Raise on a
        file it cannot read: file_source.py records that as the `parses` check.

    <audit>(rows: list[dict]) -> list[str]
        A product audit, named in the contract's `fileSource.audits`: one line per
        problem found in the parsed rows, none when it passes.

Standard library only: the Spark image has no pip layer. XML goes through the
bundled expat, which `parser()` first requires to be 2.4.1 or newer, the first
release that refuses entity expansion past a fixed amplification (a "billion
laughs" file); Python's own parsers never fetch an external entity.
"""

from __future__ import annotations

import importlib
import pyexpat
import re

MIN_EXPAT = (2, 4, 1)


def require_expat(version: tuple = pyexpat.version_info) -> None:
    if tuple(version) < MIN_EXPAT:
        raise RuntimeError(
            f"expat {'.'.join(map(str, version))} is older than {'.'.join(map(str, MIN_EXPAT))}, "
            "which does not refuse entity expansion; no file source may parse with it"
        )


def parser(name: str):
    """The module `sources/<name>.py`, after the expat check."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"{name!r} is not a parser module name")
    require_expat()
    return importlib.import_module(f"sources.{name}")
