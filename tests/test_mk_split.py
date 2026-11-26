"""The Makefile split of phase 001 (docs/development/phase-001-interfaces.md): one mk/<area>.mk
per owning step, included by the root Makefile.

An included file that names a target the root Makefile defines changes it with no error: make
only warns when both rules carry a recipe (the later one wins), and says nothing at all when
the root's rule has none, as `test:` has, so a step's file could add a recipe to `make test`.
Here any such target, or one two mk files share, is a failure. A stub target fails too
(exit 2), so nothing calls one and reads a pass.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MK = ROOT / "mk"


RULE = re.compile(r"^([^\s:#=.][^:#=]*):(?![=:])", re.M)  # a rule line's targets; not .PHONY, not :=


def targets(makefile: Path) -> set[str]:
    return {name for names in RULE.findall(makefile.read_text()) for name in names.split()}


def overrides(root: Path) -> list[str]:
    """Every target an mk file shares with the root Makefile or another mk file, and make's own
    warning lines from a dry run (which catch only a recipe defined twice)."""
    seen = targets(root / "Makefile")
    shared = []
    for mk in sorted((root / "mk").glob("*.mk")):
        mine = targets(mk)
        shared += [f"{mk.name}: {t}" for t in sorted(mine & seen)]
        seen |= mine
    out = subprocess.run(["make", "-n", "-C", str(root), "help"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return shared + [line for line in out.stderr.splitlines() if "overriding recipe for target" in line]


def copy_of_makefiles(tmp_path: Path) -> Path:
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    shutil.copytree(MK, tmp_path / "mk")
    return tmp_path


def test_no_mk_file_redefines_a_root_target():
    assert overrides(ROOT) == []


@pytest.mark.parametrize("planted", [
    "test:\n\t@echo every test passed\n",  # the root's test: has no recipe, so make is silent
    "up:\n\t@true\n",  # the root's up: has one, so make warns
    "deploy:\n\t@true\n",  # another step's target
])
def test_a_planted_redefinition_is_caught(tmp_path, planted):
    root = copy_of_makefiles(tmp_path)
    (root / "mk" / "zz-planted.mk").write_text(planted)
    assert any(f"zz-planted.mk: {planted.split(':')[0]}" == line for line in overrides(root)), overrides(root)


def stubs() -> list[str]:
    return sorted(t for f in MK.glob("*.mk") for t in re.findall(r"^([a-z-]+):.*## NOT BUILT", f.read_text(), re.M))


def test_every_stub_fails_and_says_why(tmp_path):
    root = copy_of_makefiles(tmp_path)
    assert stubs(), "no stub left: delete this test with the last one"
    for target in stubs():
        out = subprocess.run(["make", "-s", "-C", str(root), target], capture_output=True, text=True)
        assert out.returncode == 2 and f"NOT BUILT {target}:" in out.stderr, (target, out.returncode, out.stderr)
