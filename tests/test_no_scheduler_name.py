"""
The retired scheduler's name stays out of the live tree (plan phase P2, "What keeps
the name"). Airflow schedules the lake; the old scheduler is archived under
legacy/. What may still carry the name is the PostgreSQL service, role and
variables that were not renamed (ADR-034), and a few files that explain why.

Runs `git ls-files`, so an untracked file is not checked until it is added.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Spelled in two halves so this file does not trip its own check.
NAME = "pre" + "fect"

# Immutable records and the archive: dated, never rewritten.
IMMUTABLE = (
    "legacy/", "docs/adr/", "docs/audits/", "docs/benchmarks/", "docs/research/",
    "docs/plans/", "docs/MIGRATION-JOURNEY.md",
)

# Files that name the retained database role in prose, because the role is
# physically named that in every existing volume (ADR-034, Consequences).
KEPT_NAME_FILES = {
    "docker-compose.yml",
    ".env.example",
    "docker/postgres/ddl/10-lakekeeper-db.sql",
}

# Names that are kept, wherever they are quoted: the database service and its
# container, the database variables, the role's name as a quoted string (the
# value .env.example ships and check-env refuses as a password), the archive
# path, and the two ADR files whose names carry the word.
KEPT_TOKENS = re.compile(
    "|".join(
        [
            rf"(?:k2-)?{NAME}-db",
            rf"{NAME.upper()}_DB_\w*",
            rf'"{NAME}"',
            rf"legacy/{NAME}",
            rf"ADR-008-eliminate-{NAME}-orchestration",
            rf"ADR-034-airflow-3-replaces-{NAME}",
        ]
    ),
    re.IGNORECASE,
)


def offenders() -> list[str]:
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    found = []
    for f in files:
        if f.startswith(IMMUTABLE) or f in KEPT_NAME_FILES or not (ROOT / f).is_file():
            continue
        text = (ROOT / f).read_text(errors="ignore")
        if NAME in KEPT_TOKENS.sub("", text).lower():
            found.append(f)
    return found


def test_the_retired_scheduler_is_named_only_where_the_name_is_kept():
    assert offenders() == []


def test_the_token_filter_keeps_only_the_kept_names():
    kept = f"docker exec k2-{NAME}-db psql -U ${NAME.upper()}_DB_USER; see legacy/{NAME}/README.md"
    kept += f'; KNOWN = {{"{NAME}", "airflow"}}'
    assert KEPT_TOKENS.sub("", kept).lower().count(NAME) == 0
    assert NAME in KEPT_TOKENS.sub("", f"the `{NAME}` role").lower()
    assert NAME in KEPT_TOKENS.sub("", f"{NAME.title()} schedules the lake").lower()
    assert NAME in KEPT_TOKENS.sub("", f"docker exec k2-{NAME}-server ls").lower()
