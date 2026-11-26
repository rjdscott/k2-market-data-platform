"""The live Iceberg tables against their data contracts: the nightly `contract_schema` audit.

`k2gen validate` checks each contract against docker/lake/ddl/lake.sql, the file, in CI.
This checks it against what the catalog holds, which a hand-run ALTER, a DDL applied only in
part, or a contract merged without its DDL being applied would make differ from the file.
`k2gen generate` writes what each contract states to generated/contract_schemas.json (the
contracts directory is not mounted in the Spark container); `check` compares that with the
live table's Iceberg JSON: its schema, default partition spec and default sort order, the
shape both `SchemaParser.toJson` in the Spark job and `GET .../tables/<table>` on the REST
catalog return. Metadata only: no data file is opened.

Standard library only (and offsets.py, which is), so tests/ imports it with no Spark, and
it runs from the host against the REST catalog, read-only, with the same comparison:

    python3 docker/lake/contract_schema.py      # exit 1 if any table differs

What counts as a difference, and why:
- An extra live column fails. The contract is the interface: a column added by hand is one
  no contract, product page or dbt source describes.
- Column order is part of the contract. Iceberg and every reader here resolve columns by id
  or name, but a positional write does not: Spark's `INSERT INTO ... VALUES` or `SELECT`
  binds by position, as the operator rows in docs/runbooks/lake-audit-failed.md are filed,
  and `SELECT *` returns the live order. Only the order of the columns both sides have is
  compared, so one missing column does not also report every column after it as moved.
- Nested fields are compared whole, as part of the column's type, required flags included.
- A contracted table the catalog does not hold fails, and so does a table with no contract in
  a namespace the contracts own. A namespace no contract names (a spike's scratch space) is
  not the lake's and is not read.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import offsets as O

CHECK = "contract_schema"
CONTRACTS = Path(__file__).resolve().parent / "generated" / "contract_schemas.json"

# Iceberg's primitive types in Spark SQL's spelling, the one lake.sql and inline contracts use.
# Spark's TIMESTAMP carries a zone (Iceberg timestamptz); TIMESTAMP_NTZ is Iceberg's timestamp.
# A type Spark SQL cannot declare (uuid, fixed[n], time) stays Iceberg's lower-case spelling,
# so it never equals a contract's.
_SPARK = {
    "boolean": "BOOLEAN",
    "int": "INT",
    "long": "BIGINT",
    "float": "FLOAT",
    "double": "DOUBLE",
    "date": "DATE",
    "string": "STRING",
    "binary": "BINARY",
    "timestamptz": "TIMESTAMP",
    "timestamp": "TIMESTAMP_NTZ",
}
_DECIMAL = re.compile(r"decimal\((\d+),\s*(\d+)\)")
_TRANSFORMS = {"identity": "{}", "year": "years({})", "month": "months({})", "day": "days({})", "hour": "hours({})"}
_SIZED = re.compile(r"(bucket|truncate)\[(\d+)\]")


def load(path: Path = CONTRACTS) -> dict:
    return json.loads(path.read_text())


def _null(required: bool) -> str:
    return " NOT NULL" if required else ""


def spark_type(t) -> str:
    """An Iceberg JSON type in Spark SQL's spelling; a nested field's required flag as NOT NULL.

    Spark SQL has no NOT NULL for a list element or a map value; this spells one anyway, so a
    live table that has one differs from every contract, which cannot declare it.
    """
    if isinstance(t, str):
        m = _DECIMAL.fullmatch(t)
        return f"DECIMAL({m[1]},{m[2]})" if m else _SPARK.get(t, t)
    if t["type"] == "struct":
        return "STRUCT<" + ", ".join(f"{f['name']}: {spark_type(f['type'])}{_null(f['required'])}" for f in t["fields"]) + ">"
    if t["type"] == "list":
        return f"ARRAY<{spark_type(t['element'])}{_null(t['element-required'])}>"
    if t["type"] == "map":
        return f"MAP<{spark_type(t['key'])}, {spark_type(t['value'])}{_null(t['value-required'])}>"
    return json.dumps(t, sort_keys=True)


def _same(a: str, b: str) -> bool:
    """Equal but for whitespace: `DECIMAL(28, 10)` is `DECIMAL(28,10)`. Case is kept: `e` and `E` are two fields."""
    # ponytail: keywords compare case-sensitively too; every contract spells them upper case
    # today. A lower-case `string` in a contract would fail here, loudly, and want .upper() on
    # the keywords, not on field names.
    return re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b)


def _field(transform: str, column: str) -> str:
    """A partition or sort field as Spark DDL writes it: day + exchange_ts -> days(exchange_ts)."""
    sized = _SIZED.fullmatch(transform)
    if sized:
        return f"{sized[1]}({sized[2]}, {column})"
    return _TRANSFORMS.get(transform, transform + "({})").format(column)


def _sort_field(f: dict, names: dict) -> str:
    """`col` for Spark's default (ASC NULLS FIRST), `col DESC` for DESC NULLS LAST, else spelled out."""
    text = _field(f["transform"], names.get(f["source-id"], f"field id {f['source-id']}"))
    order = (f["direction"], f["null-order"])
    if order == ("asc", "nulls-first"):
        return text
    if order == ("desc", "nulls-last"):
        return text + " DESC"
    return f"{text} {f['direction'].upper()} {f['null-order'].upper().replace('-', ' ')}"


def _listed(items: list) -> str:
    return "[" + ", ".join(items) + "]"


def differences(contract: dict, live: dict) -> list[str]:
    """Every way the live table differs from its contract, one line each. Empty means it matches.

    `contract` is one entry of contract_schemas.json; `live` is {"schema", "partition-spec",
    "sort-order"}, each Iceberg's own JSON.
    """
    fields = live["schema"]["fields"]
    have = {f["name"]: f for f in fields}
    want = {c["name"]: c for c in contract["columns"]}
    out = [f"missing column {n}" for n in want if n not in have]
    out += [f"extra column {n} {spark_type(have[n]['type'])}{_null(have[n]['required'])}" for n in have if n not in want]
    for c in contract["columns"]:
        f = have.get(c["name"])
        if f is None:
            continue
        if not _same(c["type"], spark_type(f["type"])):
            out.append(f"type differs on {c['name']}: contract {c['type']}, live {spark_type(f['type'])}")
        if c["required"] != f["required"]:
            word = {True: "NOT NULL", False: "nullable"}
            out.append(f"nullability differs on {c['name']}: contract {word[c['required']]}, live {word[f['required']]}")
    ours = [c["name"] for c in contract["columns"] if c["name"] in have]
    theirs = [f["name"] for f in fields if f["name"] in want]
    at_contract = {n: i + 1 for i, n in enumerate(want)}
    at_live = {n: i + 1 for i, n in enumerate(have)}
    out += [
        f"position differs on {a}: column {at_contract[a]} in the contract, column {at_live[a]} live"
        for a, b in zip(ours, theirs)  # the same names, so the same length
        if a != b
    ]

    names = {f["id"]: f["name"] for f in fields}
    partitioning = [_field(p["transform"], names.get(p["source-id"], f"field id {p['source-id']}")) for p in live["partition-spec"]["fields"]]
    if len(partitioning) != len(contract["partitioning"]) or not all(map(_same, partitioning, contract["partitioning"])):
        out.append(f"partitioning differs: contract {_listed(contract['partitioning'])}, live {_listed(partitioning)}")
    sort = [_sort_field(f, names) for f in live["sort-order"]["fields"]]
    if len(sort) != len(contract["sort"]) or not all(map(_same, sort, contract["sort"])):
        out.append(f"sort differs: contract {_listed(contract['sort'])}, live {_listed(sort)}")
    identifier = sorted(names.get(i, f"field id {i}") for i in live["schema"].get("identifier-field-ids", []))
    if identifier != sorted(contract["identifier"]):
        out.append(f"identifier fields differ: contract {_listed(sorted(contract['identifier']))}, live {_listed(identifier)}")
    return out


def owned(contracts: dict) -> set[str]:
    """The namespaces the lake owns: every one a contract names a table in."""
    return {t.split(".")[0] for t in contracts}


def check(contracts: dict, listed: set[str], load_live) -> list[tuple[str, bool, int, str]]:
    """(table, passed, observed, detail): one per contracted table, then one per uncontracted table.

    `listed` is every `<namespace>.<table>` the catalog holds (any namespace; those the
    contracts do not own are ignored). `load_live(table)` returns the live table's Iceberg JSON
    as `differences` takes it. A load that raises files that table's row the way
    maintenance.run_audits files a check that raises: not passed, observed -1, `check raised`.
    """
    rows = []
    for table in sorted(contracts):
        if table not in listed:
            rows.append((table, False, 1, "table missing: the contract names a table the catalog does not hold"))
            continue
        try:
            found = differences(contracts[table], load_live(table))
        except Exception as exc:  # noqa: BLE001 - a table that cannot be read is a finding, not a crash
            rows.append((table, False, -1, O.check_raised(exc)))
            continue
        n = len(contracts[table]["columns"])
        rows.append((table, not found, len(found), "; ".join(found) or f"matches its contract: {n} columns, partitioning, sort order and identifier fields"))
    mine = owned(contracts)
    for table in sorted(t for t in listed if t.split(".")[0] in mine and t not in contracts):
        rows.append((table, False, 1, f"no contract: {table} is in namespace {table.split('.')[0]}, which the contracts own, and no contract describes it"))
    return rows


def main() -> int:
    """The same check over Iceberg REST from the host: what the nightly run would file, now, without Spark."""
    uri = os.environ.get("K2_LAKE_CATALOG_URI", "http://127.0.0.1:18181/catalog").rstrip("/")

    def get(path: str) -> dict:
        with urllib.request.urlopen(f"{uri}/v1/{path}", timeout=30) as response:  # noqa: S310 - the lake's own catalog
            return json.load(response)

    prefix = get(f"config?warehouse={os.environ.get('K2_LAKE_WAREHOUSE', 'k2')}")["defaults"]["prefix"]
    contracts = load()
    namespaces = {n[0] for n in get(f"{prefix}/namespaces")["namespaces"]}
    # ponytail: one page of each listing (Lakekeeper's is 100); a namespace past 100 tables wants next-page-token.
    listed = {f"{ns}.{i['name']}" for ns in sorted(owned(contracts) & namespaces) for i in get(f"{prefix}/namespaces/{ns}/tables")["identifiers"]}

    def live(table: str) -> dict:
        m = get(f"{prefix}/namespaces/{table.replace('.', '/tables/', 1)}")["metadata"]
        return {
            "schema": next(s for s in m["schemas"] if s["schema-id"] == m["current-schema-id"]),
            "partition-spec": next(s for s in m["partition-specs"] if s["spec-id"] == m["default-spec-id"]),
            "sort-order": next(s for s in m["sort-orders"] if s["order-id"] == m["default-sort-order-id"]),
        }

    rows = check(contracts, listed, live)
    for table, passed, observed, detail in rows:
        print(f"{'ok  ' if passed else 'FAIL'} {table:<30} {observed:>3}  {detail}")
    failed = sum(1 for r in rows if not r[1])
    print(f"{len(rows) - failed} of {len(rows)} match; namespaces read: {', '.join(sorted(owned(contracts) & namespaces))}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
