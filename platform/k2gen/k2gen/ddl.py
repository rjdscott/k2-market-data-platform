"""The Iceberg tables in docker/lake/ddl/lake.sql, read as text.

Standard library only: tests/ imports this module with no dependencies installed
(tests/conftest.py puts platform/k2gen on sys.path), and k2gen validates every
contract against what it returns.

A line-oriented parse, not a SQL parser. lake.sql is written to a fixed shape:
`CREATE TABLE IF NOT EXISTS lake.<ns>.<table> (` with one column per line at a
four-space indent, `)` and `USING iceberg` on their own lines, one
`PARTITIONED BY (...)` line, and the sort order in a separate
`ALTER TABLE ... WRITE ... ORDERED BY ...;`. A table written another way is not
found, and the coverage tests in platform/k2gen/tests/ fail on it.
"""

import re
from dataclasses import dataclass
from pathlib import Path

_CREATE = re.compile(
    r"^CREATE TABLE IF NOT EXISTS lake\.(\w+\.\w+) \((.*?)\n\)\nUSING iceberg\n(.*?)\n\);",
    re.M | re.S,
)
_COLUMN = re.compile(r"^ {4}(\w+)\s+[A-Z]", re.M)
_PARTITIONED = re.compile(r"^PARTITIONED BY \((.*)\)$", re.M)
_ORDERED = re.compile(r"^ALTER TABLE lake\.(\w+\.\w+)\s+WRITE\b[^;]*?\bORDERED BY ([^;]+);", re.M)
# One partition field: a transform with its arguments, or a bare column.
_FIELD = re.compile(r"\w+\([^)]*\)|\w+")


@dataclass(frozen=True)
class Table:
    columns: tuple
    partitioning: tuple
    sort: tuple


def parse(path) -> dict:
    """{"<ns>.<table>": Table} for every CREATE TABLE in the file, in file order."""
    text = Path(path).read_text()
    sort = {m.group(1): tuple(c.strip() for c in m.group(2).split(",")) for m in _ORDERED.finditer(text)}
    tables = {}
    for m in _CREATE.finditer(text):
        name, body, rest = m.groups()
        partitioned = _PARTITIONED.search(rest)
        tables[name] = Table(
            columns=tuple(_COLUMN.findall(body)),
            partitioning=tuple(_FIELD.findall(partitioned.group(1))) if partitioned else (),
            sort=sort.get(name, ()),
        )
    return tables
