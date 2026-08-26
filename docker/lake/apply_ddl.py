#!/usr/bin/env python3
"""
Apply docker/lake/ddl/lake.sql through the `lake` catalog. The `lake-ddl`
one-shot compose service runs this; it is idempotent, so a re-run on a live
warehouse is a no-op.

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/apply_ddl.py
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/apply_ddl.py --dry-run

`--namespace-map raw=scratch_raw,bronze=scratch_bronze` rewrites the namespace
of every table reference, which is how the DDL is exercised against a throwaway
namespace without touching the real tables.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from spark_conf import CATALOG, lake_session

DDL_FILE = Path(__file__).parent / "ddl" / "lake.sql"


def statements(sql: str) -> list[str]:
    """Split `sql` into statements: drop `--` comment lines, then split on the
    semicolons that are not inside a string literal.

    The quote tracking is not decoration. lake.sql is mostly prose — column
    COMMENTs and comment lines both contain semicolons ("Gapless by
    construction; the continuity audit proves it"), and a `sql.split(";")`
    chops that column definition in half and hands Spark a fragment.

    # ponytail: single-quote literals and `--` line comments are the only two
    # constructs lake.sql uses, so those are the only two handled. A `/* */`
    # block comment or a dollar-quoted string needs sqlparse, not another
    # branch here.
    """
    stripped = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )

    out, buf, in_string = [], [], False
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if in_string:
            if ch == "'":
                if stripped[i + 1 : i + 2] == "'":  # '' is an escaped quote
                    buf.append("''")
                    i += 2
                    continue
                in_string = False
        elif ch == "'":
            in_string = True
        elif ch == ";":
            if buf and "".join(buf).strip():
                out.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1

    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def remap(sql: str, mapping: dict[str, str]) -> str:
    """Rewrite `<catalog>.<ns>.` to `<catalog>.<mapping[ns]>.` for each mapped ns."""
    for old, new in mapping.items():
        sql = re.sub(rf"\b{re.escape(CATALOG)}\.{re.escape(old)}\.", f"{CATALOG}.{new}.", sql)
    return sql


def _parse_map(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    return dict(pair.split("=", 1) for pair in raw.split(","))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print statements, run nothing")
    ap.add_argument("--namespace-map", default="", help="raw=scratch_raw,bronze=scratch_bronze")
    ap.add_argument("--file", default=str(DDL_FILE))
    args = ap.parse_args()

    sql = remap(Path(args.file).read_text(), _parse_map(args.namespace_map))
    stmts = statements(sql)

    if args.dry_run:
        for i, stmt in enumerate(stmts, 1):
            print(f"-- [{i}/{len(stmts)}]\n{stmt};\n")
        return 0

    spark = lake_session("k2-lake-ddl")
    try:
        for i, stmt in enumerate(stmts, 1):
            head = " ".join(stmt.split())[:90]
            print(f"[{i}/{len(stmts)}] {head}", flush=True)
            spark.sql(stmt)
    finally:
        spark.stop()
    print(f"✓ {len(stmts)} statements applied to catalog `{CATALOG}`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
