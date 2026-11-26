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
