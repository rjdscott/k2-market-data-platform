"""
The committed venue-format fixtures are exactly what scripts/make-synthetic-fixtures.py
writes from its seed: a hand edit, a stale file or a generator change without a
regeneration fails here. The replay tests in services/capture-rust/tests/ then
prove the frames are valid (every Kraken checksum verifies, no sequence gap)
and pin what the adapters make of them. The generator's manifest is what
check-docs.sh gate (i) trusts, so the set of synthetic-* files in the tree must
be exactly the set it wrote.
"""

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def generated():
    spec = importlib.util.spec_from_file_location("make_synthetic_fixtures", ROOT / "scripts/make-synthetic-fixtures.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    return generator.build()[0], generator.MANIFEST


def test_committed_fixtures_are_what_the_generator_writes():
    files, manifest = generated()
    assert len(files) == 16 and manifest in files
    for path, text in files.items():
        assert path == manifest or path.name.startswith("synthetic-"), path
        assert path.read_text() == text, f"{path.relative_to(ROOT)} differs; run python3 scripts/make-synthetic-fixtures.py"


def test_every_tracked_synthetic_file_is_one_the_generator_writes():
    files, manifest = generated()
    tracked = [t for t in subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True,
                                         text=True).stdout.split() if Path(t).name.startswith("synthetic-")]
    # The replay tests' golden digests are pinned from `k2-capture replay`, not generated.
    assert {t for t in tracked if not t.endswith(".sha256")} == {str(p.relative_to(ROOT)) for p in files if p != manifest}
    listed = [row.split("  ", 1)[1] for row in files[manifest].splitlines()]
    assert listed == sorted(str(p.relative_to(ROOT)) for p in files if p != manifest)
