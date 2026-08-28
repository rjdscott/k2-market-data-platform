"""
ADR-029: no notebook reads the moving head of a lake table. Every query goes
through the `pinned.*` views `k2lake.pin()` creates at a snapshot id, so a
number a notebook prints names the snapshot it came from.

`01_connect.ipynb` is exempt: it lists the catalog and reads no table.
"""

import json
import re
from pathlib import Path

import pytest

NOTEBOOKS = sorted(p for p in (Path(__file__).parent.parent / "notebooks").glob("0*.ipynb") if not p.name.startswith("01_"))
HEAD_READ = re.compile(r"\blake\.(gold|silver|audit)\.")


def code(nb: Path) -> str:
    doc = json.loads(nb.read_text())
    return "\n".join("".join(c["source"]) for c in doc["cells"] if c["cell_type"] == "code")


@pytest.mark.parametrize("nb", NOTEBOOKS, ids=[p.name for p in NOTEBOOKS])
def test_notebook_reads_only_pinned_views(nb):
    src = code(nb)
    assert "pin(con)" in src, f"{nb.name} never calls k2lake.pin()"
    assert not HEAD_READ.search(src), f"{nb.name} reads a lake table at its head; use the pinned.* views"


def test_there_are_notebooks_to_check():
    assert len(NOTEBOOKS) >= 4
