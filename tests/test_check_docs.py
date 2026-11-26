"""scripts/check_docs.py, the Python half of scripts/check-docs.sh. Every case
uses synthetic terms and files made here; no real gated term is written in a
test, because the point of hashing them is that none is readable in the tree."""

import hashlib
import io
import os
import re
import shutil
import subprocess
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


OLD_RULE_PATHS = ["tests/fixtures/x{ext}", "services/a/tests/fixtures/deep/x{ext}", "docs/samples/x{ext}",
                  "docs/research/s/frames.sample{ext}", "tests/sample-trades{ext}"]


@pytest.mark.parametrize("path", [p.format(ext=e) for p in OLD_RULE_PATHS for e in sorted(check_docs.OLD_EXT)])
def test_everything_the_old_path_rule_failed_still_fails(path):
    # The rule before this gate (fixtures/ or samples/ or *sample*, by extension),
    # on content no sniffer would call data: it is the floor.
    assert problems(tree(**{path.replace("/", "__"): b"plain text\n"})) == [path]


@pytest.mark.parametrize("content", [
    b'[{"px": 1, "qty": 2}, {"px": 3, "qty": 4}]',                       # one line
    b'[\n  {\n    "px": 1\n  },\n  {\n    "px": 3\n  }\n]\n',            # pretty-printed
    b"[[1, 2], [3, 4]]",
])
def test_a_json_array_of_records_in_a_fixtures_path_fails(content):
    # the review's case: tests/fixtures/trades.json passed the sniffers
    assert problems(tree(**{"tests__fixtures__trades.json": content})) == ["tests/fixtures/trades.json"]


@pytest.mark.parametrize("content", [b"- px: 1\n  qty: 2\n- px: 3\n  qty: 4\n", b"# c\n---\n- {px: 1}\n"])
def test_a_list_of_records_in_a_fixtures_path_passes_only_from_the_manifest(content):
    name = "tests/fixtures/book.yaml"
    assert problems(tree(**{name.replace("/", "__"): content}), notice=name) == [name]
    t = tree(**{name.replace("/", "__"): content})
    t[check_docs.MANIFEST] += f"{hashlib.sha256(content).hexdigest()}  {name}\n".encode()
    assert problems(t) == [name], "a manifest entry does not make a non-synthetic name pass"
    synthetic = "tests/fixtures/synthetic-book.yaml"
    t = tree(**{synthetic.replace("/", "__"): content})
    t[check_docs.MANIFEST] += f"{hashlib.sha256(content).hexdigest()}  {synthetic}\n".encode()
    assert problems(t) == []


def test_config_shaped_yaml_and_json_in_a_fixtures_path_pass():
    files = tree(**{"tests__fixtures__config.yaml": b"name: x\nitems:\n  - a: 1\n",
                    "tests__fixtures__contract.yml": b"# c\nkind: contract\n"})
    assert problems(files) == []


@pytest.mark.parametrize("path, content", [
    ("docs/x/archive.zip", b"PK\x03\x04\x14\x00\x00\x00"),
    ("docs/x/bundle.tar", b"frames.jsonl" + b"\x00" * 88 + b"0000644\x00"),
    ("tests/x/state.db", b"SQLite format 3\x00\x10\x00"),
    ("tests/x/frames.pkl", b"\x80\x04\x95\x10\x00\x00\x00"),
    ("tests/x/frames.lz4", b"\x04\x22\x4d\x18\x64\x40"),
    ("tests/x/frames.md", b"PAR1\x15\x04\x15"),
    ("tests/x/frames.txt", b"Obj\x01\x04\x16avro.schema"),
    ("legacy/v1/x.jar", b"PK\x03\x04\x14\x00"),
])
def test_a_binary_file_under_any_name_fails_unless_listed(path, content):
    assert problems(tree(**{path.replace("/", "__"): content})) == [path]
    assert problems(tree(**{path.replace("/", "__"): content}), notice=path) == []


def test_the_known_binaries_pass():
    files = tree(**{"legacy__v2-kotlin__gradle__wrapper__gradle-wrapper.jar": b"PK\x03\x04\x14\x00",
                    "docs__images__a.png": b"\x89PNG\r\n\x1a\n\x00"})
    assert problems(files) == []
    assert problems(tree(**{"docs__readme.txt": b"BMW is not a bitmap\n"})) == []  # image magic needs binary


def test_the_tree_as_committed_passes():
    assert check_docs.data_problems(check_docs.tracked_files(), (ROOT / "NOTICE").read_text()) == []


# ── gate (j): captured-data counts in editable Markdown ─────────────────────
# Every figure below is invented.


def figures(text):
    return [p.split(": ", 1)[1] for p in check_docs.count_problems("doc.md", text)]


@pytest.mark.parametrize("line", [
    "the whole archive (12,345,678 rows, six tables)", "stage 2b: venue: 1234567 frames = ...",
    "the 12.3 M trades in gold", "456k rows a day", "the producer dropped 12,345 Venue records",
    "1,111,111 lake book-seconds pulled", "| x | 1,234 rows |", "2222222 OFFSETS", "2.5 million events",
    # the review's shapes: the unit before the figure, emphasis, singular, the new units
    "| records lost, 388 s paused | **12,345** on venue alone |", "records lost: 12,345",
    "1,234 trade", "12,345 ids arrived twice", "1,234 ticks", "5,000 fills", "a total = 12,345 rows",
])
def test_the_high_risk_shapes_fail(line):
    assert len(figures("intro\n" + line)) == 1


def test_a_non_breaking_space_is_a_thousands_separator():
    assert figures("12\u00a0345 rows and 1\u202f234\u202f567 frames") == ["12,345", "1,234,567"]


def test_a_table_row_is_read_with_its_header():
    table = "| Venue | Rows | Replays |\n|---|---:|---:|\n| A | 12,345 | 0 |\n| B | 23,456 | 1 |\n"
    assert check_docs.count_problems("d.md", table) == ["d.md:3: 12,345", "d.md:4: 23,456"]
    assert check_docs.count_problems("d.md", "text\n\n| Venue | p50 |\n|---|---|\n| A | 12,345 |\n") == []


@pytest.mark.parametrize("line", [
    "rebuilt in 520 s", "999 rows", "a 2,048 MiB heap", "on 2026-01-01 rows were", "v1.2.3 rows",
    "port 18181", "0 records lost", "x1234 rows", "5.7 s",
    # sizes, durations, settings and ids are not counts
    "5,195,904 B in one frame", "5,195,904 bytes of frames", "25,227,274 ms between the records",
    "`message.timeout.ms=300000` fails any record", "snapshot 6911535457573800498 of trades",
    "in 2026 the rows", "a 10 x 1,000 % rows",
])
def test_other_numbers_pass(line):
    assert figures(line) == []


def test_an_opt_out_needs_a_reason_of_twelve_characters():
    assert figures("1,000 rows <!-- counts-ok: synthetic table -->") == []
    assert figures("1,000 rows <!-- counts-ok: eleven char -->") == ["1,000"]
    assert figures("1,000 rows <!-- counts-ok: x -->") == ["1,000"]


def test_one_opt_out_covers_one_figure():
    two = "12,345 rows and 23,456 rows"
    assert figures(two + " <!-- counts-ok: a synthetic example -->") == ["12,345"]
    assert figures("12,345 <!-- counts-ok: a synthetic example --> rows and 23,456 rows") == ["23,456"]
    assert figures("12,345 <!-- counts-ok: a synthetic example --> rows and 23,456 "
                   "<!-- counts-ok: a synthetic example --> rows") == []


def test_a_platform_throughput_rate_passes_with_its_opt_out():
    assert figures("an offload at 236k rows/s") == ["236k"]
    assert figures("an offload at 236k rows/s <!-- counts-ok: platform throughput -->") == []


def test_only_editable_markdown_is_checked_code_blocks_included():
    text = b"```\n1000000 records evicted\n```\n"
    tracked = {"docs/runbooks/x.md": text, "docs/benchmarks/2001-01-01.md": text, "docs/adr/ADR-001-x.md": text,
               "legacy/v1/README.md": text, "docs/runbooks/x.sql": text}
    assert check_docs.counts(tracked) == ["docs/runbooks/x.md:2: 1000000"]


# ── gate (h): em-dash on added lines, on a push to main too ─────────────────


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def gate_h(repo, **env):
    """Gate (h)'s verdict line from check-docs.sh run in `repo`. The other gates
    run too and fail on the bare repo; only (h)'s line is read. A `docker` that
    fails at once keeps promtool out of it."""
    stub = repo / ".bin"
    stub.mkdir(exist_ok=True)
    (stub / "docker").write_text("#!/bin/sh\nexit 1\n")
    (stub / "docker").chmod(0o755)
    base = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH")}
    run = subprocess.run(["bash", "scripts/check-docs.sh"], cwd=repo, capture_output=True, text=True,
                         env={**base, "PATH": f"{stub}:{base['PATH']}", **env})
    lines = run.stdout.splitlines()
    i = next(i for i, x in enumerate(lines) if "(h)" in x)
    flagged = []
    for x in lines[i + 1:]:
        if not x.startswith("  "):
            break
        flagged.append(x.strip())
    return "\n".join([lines[i], *flagged])


def scratch_repo(tmp_path, files=None):
    """A git repo holding check-docs.sh and its helper, plus `files`, committed once."""
    repo = tmp_path / "r"
    (repo / "scripts").mkdir(parents=True)
    for f in ("check-docs.sh", "check_docs.py"):
        shutil.copy(ROOT / "scripts" / f, repo / "scripts" / f)
    (repo / ".gitignore").write_text(".bin/\n")
    for name, text in (files or {}).items():
        (repo / name).write_text(text)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return repo, git(repo, "rev-parse", "HEAD")


def test_gate_h_reads_a_commit_pushed_straight_to_main(tmp_path):
    repo, before = scratch_repo(tmp_path)
    (repo / "doc.md").write_text(f"a dash {chr(0x2014)} here\n")  # built, so this file holds none
    git(repo, "add", "doc.md")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "pushed to main")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # CI's checkout of a push: origin/main is HEAD
    assert gate_h(repo).startswith("PASS")  # the bypass: the merge base is HEAD, nothing to read
    assert gate_h(repo, K2_DIFF_BASE=before).startswith("FAIL: (h) em-dash")
    assert gate_h(repo, K2_DIFF_BASE="0" * 40).startswith("FAIL: (h) K2_DIFF_BASE")


def test_gate_h_lets_an_old_dash_through_once_and_never_a_new_one(tmp_path):
    d = chr(0x2014)   # built, so this file holds none
    repo, before = scratch_repo(tmp_path, {
        "moved.md": f"an old {d} dash, see [the page](old/page.md)\n",
        "twice.md": f"kept {d} once, see [it](old/it.md)\n",
        "target.md": "a plain line, see [this](old/this.md)\n",
        "both.md": f"prose {d} here, see [that](old/that.md)\n",
    })
    (repo / "moved.md").write_text(f"an old {d} dash, see [the page](archive/page.md)\n")      # target only: passes
    (repo / "twice.md").write_text(f"kept {d} once, see [it](new/it.md)\n" * 2)               # one removal, two adds
    (repo / "target.md").write_text(f"a plain line, see [this](old/th{d}is.md)\n")           # dash inside a target
    (repo / "both.md").write_text(f"prose {d} here, see [that](n{d}ew/that.md)\n")            # old dash, new one in the target
    (repo / "new.md").write_text(f"a brand new {d} line\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "change")
    verdict, *flagged = gate_h(repo, K2_DIFF_BASE=before).splitlines()
    assert verdict.startswith("FAIL: (h) em-dash")
    assert sorted(x.split(": ")[0] for x in flagged) == ["both.md:1", "new.md:1", "target.md:1", "twice.md:2"]
