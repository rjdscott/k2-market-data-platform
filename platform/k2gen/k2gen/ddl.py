"""The Iceberg DDL `k2gen generate` writes (docker/lake/ddl/generated/), read back as text.

Standard library only, so it imports with no dependencies installed (tests/conftest.py
puts platform/k2gen on sys.path). The tests in platform/k2gen/tests/ read the generated
DDL back with it and compare it with the contract it came from.

A line-oriented parse, not a SQL parser, of the one shape the generator writes:
`CREATE TABLE IF NOT EXISTS lake.<ns>.<table> (` with one column per line at a
four-space indent, `)` and `USING iceberg` on their own lines, one
`PARTITIONED BY (...)` line, and the sort order in a separate
`ALTER TABLE ... WRITE ... ORDERED BY ...;`. A table written another way is not found.
"""

import re
from dataclasses import dataclass
from pathlib import Path

_CREATE = re.compile(
    r"^CREATE TABLE IF NOT EXISTS lake\.(\w+\.\w+) \((.*?)\n\)\nUSING iceberg\n(.*?)\n\);",
    re.M | re.S,
)
_COLUMN = re.compile(r"^ {4}(\w+)\s+[A-Z]", re.M)
_COMMENT = re.compile(r"\bCOMMENT '((?:[^']|'')*)'")
_PARTITIONED = re.compile(r"^PARTITIONED BY \((.*)\)$", re.M)
_ORDERED = re.compile(r"^ALTER TABLE lake\.(\w+\.\w+)\s+WRITE\b[^;]*?\bORDERED BY ([^;]+);", re.M)
# One partition field: a transform with its arguments, or a bare column.
_FIELD = re.compile(r"\w+\([^)]*\)|\w+")


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # as written, NOT NULL included: "STRING NOT NULL"
    comment: str  # "" where the DDL gives none


@dataclass(frozen=True)
class Table:
    schema: tuple[Column, ...]
    partitioning: tuple[str, ...]
    sort: tuple[str, ...]

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.schema)


def _columns(body: str) -> tuple[Column, ...]:
    """One Column per definition; a definition runs from its name to the next one's."""
    starts = list(_COLUMN.finditer(body))
    ends = [m.start() for m in starts[1:]] + [len(body)]
    out = []
    for m, end in zip(starts, ends, strict=True):
        text = body[m.end(1) : end].strip().rstrip(",")
        comment = _COMMENT.search(text)
        out.append(
            Column(
                name=m.group(1),
                type=" ".join(text[: comment.start() if comment else len(text)].split()),
                comment=comment.group(1).replace("''", "'") if comment else "",
            )
        )
    return tuple(out)


def column_of(field: str) -> str:
    """The column a partition or sort field reads: days(recv_ts) -> recv_ts, bucket(16, id) -> id."""
    return str(re.findall(r"\w+", field)[-1])


def parse(path: Path | str) -> dict[str, Table]:
    """{"<ns>.<table>": Table} for every CREATE TABLE in the file, in file order."""
    text = Path(path).read_text()
    sort = {m.group(1): tuple(c.strip() for c in m.group(2).split(",")) for m in _ORDERED.finditer(text)}
    tables = {}
    for m in _CREATE.finditer(text):
        name, body, rest = m.groups()
        partitioned = _PARTITIONED.search(rest)
        tables[name] = Table(
            schema=_columns(body),
            partitioning=tuple(_FIELD.findall(partitioned.group(1))) if partitioned else (),
            sort=sort.get(name, ()),
        )
    return tables
