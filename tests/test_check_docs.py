"""scripts/check_docs.py, the Python half of scripts/check-docs.sh. Every case
uses synthetic terms and files made here; no real gated term is written in a
test, because the point of hashing them is that none is readable in the tree."""

import hashlib
import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_docs  # noqa: E402

# ── gate (b): forbidden words, as hashes ────────────────────────────────────

SYNTHETIC = [check_docs.entry("Zorbel"), check_docs.entry("quax  flim")]


def hits(text):
    match = check_docs.matcher(SYNTHETIC)
    return [n for n, line in enumerate(text.splitlines(), 1) if match(line)]


def test_entries_are_word_counts_and_salted_digests():
    assert SYNTHETIC[0] == (1, check_docs.digest("zorbel"))
    assert SYNTHETIC[1] == (2, check_docs.digest("quax flim"))
    assert SYNTHETIC[0][1] != hashlib.sha256(b"zorbel").hexdigest(), "unsalted"


@pytest.mark.parametrize("line", [
    "a ZORBEL here", "zorbels, plural", "the unzorbeled draft", "x(zorbel)y",
    "quax flim", "Quax, flim.", "quax\tflim and more", "quax_flim",
])
def test_a_term_is_found_where_grep_i_found_it(line):
    assert hits("first line\n" + line) == [2]


@pytest.mark.parametrize("line", ["zorbe l", "quax", "flim", "quaxflim", "quaxe flim"])
def test_near_misses_are_not_hits(line):
    assert hits(line) == []


def test_a_multi_word_term_split_across_lines_is_not_a_hit():
    assert hits("quax\nflim") == []


def test_the_shipped_list_is_hashes_only():
    assert check_docs.TERMS and all(
        n >= 1 and re.fullmatch(r"[0-9a-f]{64}", h) for n, h in check_docs.TERMS
    )


def test_check_docs_sh_gates_b_through_the_helper_with_no_word_list():
    sh = (ROOT / "scripts/check-docs.sh").read_text()
    assert "python3 scripts/check_docs.py words" in sh
    assert "grep -niE" not in sh


def test_cli_prints_file_and_line_never_the_line(tmp_path, capsys):
    doc = tmp_path / "doc.md"
    doc.write_text("clean\nsome zorbel text\n")
    assert check_docs.main(["words", str(doc)], terms=SYNTHETIC) == 1
    out = capsys.readouterr().out
    assert out == f"{doc}:2\n" and "zorbel" not in out
    doc.write_text("clean\n")
    assert check_docs.main(["words", str(doc)], terms=SYNTHETIC) == 0


def test_hash_prints_an_entry_the_matcher_then_finds(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("Brand New Term\n"))
    assert check_docs.main(["hash"]) == 0
    n, h = re.fullmatch(r'\s*\((\d), "([0-9a-f]{64})"\),\n', capsys.readouterr().out).groups()
    match = check_docs.matcher([(int(n), h)])
    assert match("a brand-new term appears") and not match("a brand new idea")


# ── gate (i): data files, notebooks, images ─────────────────────────────────

FRAMES = b'{"recv_ts_ns":1,"payload":"{}"}\n{"recv_ts_ns":2,"payload":"{}"}\n'
CLEAN_NB = b'{"cells":[{"cell_type":"code","source":["1"],"outputs":[],"execution_count":null}]}'
RUN_NB = b'{"cells":[{"cell_type":"code","source":["1"],"outputs":[{"text":"row"}],"execution_count":3}]}'


def tree(**files):
    """A tracked tree plus a manifest listing `synthetic-ok.jsonl` at its hash."""
    ok = FRAMES
    manifest = f"{hashlib.sha256(ok).hexdigest()}  tests/fixtures/synthetic-ok.jsonl\n".encode()
    return {check_docs.MANIFEST: manifest, "tests/fixtures/synthetic-ok.jsonl": ok,
            **{k.replace("__", "/"): v for k, v in files.items()}}


def problems(files, notice=""):
    return [p.split(":")[0] for p in check_docs.data_problems(files, notice)]


def test_a_generated_file_at_its_manifest_hash_passes():
    assert problems(tree()) == []


def test_a_generated_file_edited_by_hand_fails_on_its_hash():
    t = tree()
    t["tests/fixtures/synthetic-ok.jsonl"] = FRAMES + b'{"recv_ts_ns":3,"payload":"{}"}\n'
    assert problems(t) == ["tests/fixtures/synthetic-ok.jsonl"]


def test_a_real_recording_named_synthetic_fails():
    # the audit's bypass: the name alone used to pass
    assert problems(tree(**{"services__capture-rust__tests__fixtures__synthetic-real-recording.jsonl": FRAMES})) == [
        "services/capture-rust/tests/fixtures/synthetic-real-recording.jsonl"]


def test_a_golden_digest_named_synthetic_must_be_only_a_digest():
    assert problems(tree(**{"tests__fixtures__synthetic-ok.sha256": b"0" * 64 + b"\n"})) == []
    assert problems(tree(**{"tests__fixtures__synthetic-ok.sha256": FRAMES})) == ["tests/fixtures/synthetic-ok.sha256"]


@pytest.mark.parametrize("path, content", [
    ("tests/data/binance-trades.jsonl", FRAMES),                 # the audit's paths, by extension
    ("tests/parity/kraken-session.jsonl", FRAMES),
    ("docs/research/x/frames.jsonl", FRAMES),
    ("services/capture-rust/tests/fixtures/kraken-real.jsonl.xz", b"\xfd7zXZ\x00\x00\x04"),
    ("notebooks/out.parquet", b"PAR1\x15\x04"),
    ("docs/research/x/frames.txt", FRAMES),                      # by content, whatever the name
    ("tests/fixtures/book.yaml", b"ts,px,qty\n1,2,3\n4,5,6\n"),
    ("tests/fixtures/book.dat", b"ts\tpx\n1\t2\n3\t4\n"),
    ("docs/x/blob.bin", b"PAR1\x00\x01binary"),
    ("docs/x/rec", b"Obj\x01\x04\x14avro.schema"),
    ("docs/x/cap", b"\xd4\xc3\xb2\xa1\x02\x00\x04\x00"),
    ("docs/x/cap2", b"\x0a\x0d\x0d\x0a\x1c\x00\x00\x00"),
    ("docs/x/z", b"\x1f\x8b\x08\x00"),
])
def test_a_data_file_anywhere_fails_unless_listed_in_notice(path, content):
    assert problems(tree(**{path.replace("/", "__"): content})) == [path]
    assert problems(tree(**{path.replace("/", "__"): content}), notice=f"  {path}   its source and terms\n") == []


def test_code_and_config_are_not_data():
    files = tree(**{"scripts__x.py": b'"""doc."""\nimport a, b\n\nx = 1\n', "docker__x.json": b'{\n  "a": [1, 2]\n}\n',
                    "docker__x.yml": b"a: 1\nb: [1, 2]\n", "legacy__v2-kotlin__gradle__wrapper__gradle-wrapper.jar": b"PK\x03\x04\x00"})
    assert problems(files) == []


def test_a_listing_must_be_the_exact_path():
    files = tree(**{"tests__data__a.jsonl": FRAMES})
    assert problems(files, notice="xtests/data/a.jsonl tests/data/ tests/data/*.jsonl") == ["tests/data/a.jsonl"]


def test_a_notebook_with_outputs_fails_outside_legacy_even_if_listed():
    assert problems(tree(**{"notebooks__01.ipynb": CLEAN_NB})) == []
    assert problems(tree(**{"notebooks__01.ipynb": RUN_NB}), notice="notebooks/01.ipynb") == ["notebooks/01.ipynb"]
    only_count = RUN_NB.replace(b'[{"text":"row"}]', b"[]")
    assert problems(tree(**{"notebooks__01.ipynb": only_count})) == ["notebooks/01.ipynb"]


def test_a_legacy_notebook_with_outputs_passes_only_when_listed():
    assert problems(tree(**{"legacy__v1__a.ipynb": RUN_NB})) == ["legacy/v1/a.ipynb"]
    assert problems(tree(**{"legacy__v1__a.ipynb": RUN_NB}), notice="legacy/v1/a.ipynb") == []


def test_raster_images_only_under_docs_images_by_name_or_content():
    assert problems(tree(**{"docs__images__a.png": b"\x89PNG\r\n"})) == []
    assert problems(tree(**{"docs__a.png": b"\x89PNG\r\n", "docs__b.dat": b"\xff\xd8\xff\xe0",
                            "docs__c": b"RIFF\x00\x00\x00\x00WEBPVP8"})) == ["docs/a.png", "docs/b.dat", "docs/c"]


def test_a_manifest_entry_for_a_file_not_tracked_fails():
    t = tree()
    del t["tests/fixtures/synthetic-ok.jsonl"]
    assert problems(t) == [check_docs.MANIFEST]


def test_the_tree_as_committed_passes():
    assert check_docs.data_problems(check_docs.tracked_files(), (ROOT / "NOTICE").read_text()) == []
