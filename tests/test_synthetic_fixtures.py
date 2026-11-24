"""
The committed venue-format fixtures are exactly what scripts/make-synthetic-fixtures.py
writes from its seed: a hand edit, a stale file or a generator change without a
regeneration fails here. The replay tests in services/capture-rust/tests/ then
prove the frames are valid (every Kraken checksum verifies, no sequence gap)
and pin what the adapters make of them.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_committed_fixtures_are_what_the_generator_writes():
    spec = importlib.util.spec_from_file_location("make_synthetic_fixtures", ROOT / "scripts/make-synthetic-fixtures.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    files, _ = generator.build()
    assert len(files) == 15
    for path, text in files.items():
        assert path.name.startswith("synthetic-"), path
        assert path.read_text() == text, f"{path.relative_to(ROOT)} differs; run python3 scripts/make-synthetic-fixtures.py"
