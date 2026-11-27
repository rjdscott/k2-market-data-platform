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


def stubs(root: Path = ROOT) -> list[str]:
    return sorted(t for f in (root / "mk").glob("*.mk") for t in re.findall(r"^([a-z-]+):.*## NOT BUILT", f.read_text(), re.M))


def stub_problems(root: Path) -> list[str]:
    """Each stub target (`## NOT BUILT`) that does not fail with exit 2 and say why."""
    problems = []
    for target in stubs(root):
        out = subprocess.run(["make", "-s", "-C", str(root), target], capture_output=True, text=True)
        if not (out.returncode == 2 and f"NOT BUILT {target}:" in out.stderr):
            problems.append(f"{target}: exit {out.returncode}, stderr {out.stderr.strip()!r}")
    return problems


def test_every_stub_fails_and_says_why(tmp_path):
    # Phase 001 built every target it stubbed (the stub list is empty since S19 and S20 landed); a
    # later phase that stubs a target is held to the same rule by this test.
    assert stub_problems(copy_of_makefiles(tmp_path)) == []


@pytest.mark.parametrize("recipe, caught", [
    ('\t@echo "NOT BUILT zz-stub: a later step builds it" >&2; exit 2\n', False),  # an honest stub
    ("\t@echo every row passed\n", True),  # a stub that reads as a pass
    ('\t@echo "NOT BUILT zz-stub: a later step builds it" >&2\n', True),  # says why, but exits 0
])
def test_a_planted_stub_is_held_to_the_rule(tmp_path, recipe, caught):
    root = copy_of_makefiles(tmp_path)
    (root / "mk" / "zz-planted.mk").write_text(f".PHONY: zz-stub\nzz-stub:  ## NOT BUILT until a later step\n{recipe}")
    assert stubs(root)[-1:] == ["zz-stub"]
    assert bool(stub_problems(root)) is caught, stub_problems(root)
