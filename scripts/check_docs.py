#!/usr/bin/env python3
"""The Python half of scripts/check-docs.sh. Standard library only.

    python3 scripts/check_docs.py words FILE...   # gate (b): forbidden words
    python3 scripts/check_docs.py hash            # print the entry for a new forbidden term
    python3 scripts/check_docs.py data            # gate (i): data files, notebooks, images
    python3 scripts/check_docs.py counts          # gate (j): captured-data counts in editable Markdown

Each gate prints one line per problem and exits 1 on any, 0 on none; a crash
exits with Python's own non-zero code, which check-docs.sh also counts as a
failure.

Gate (b) holds its terms as salted SHA-256 hashes, so the list the gate exists
to keep out of the repository is not readable in it. Every commit before this
file held the list in plain text in check-docs.sh, and history is not
rewritten (the owner's standing rule; NOTICE, ADR-029 Outcome 2026-11). The
hashes stop a read, a grep and a code search, not a guess: the salt is here, so
anyone can hash a dictionary against them.

Matching is case-insensitive, per line, on runs of letters and digits. A
one-word term matches inside a run, as the `grep -i` it replaced did (a plural
or a prefixed form still hits); a term of N words matches N consecutive whole
runs on one line, whatever separates them. A hit prints `file:line` only, never
the line: CI logs on a public repository are public too.

To add a term: `python3 scripts/check_docs.py hash` prompts for it without
echoing (so it lands in no shell history) and prints the entry to paste into
TERMS. Nothing prints an existing term.
"""

import getpass
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SALT = bytes.fromhex("0975cf9f1b96ab1000e115fc9b29b344")
# (words in the term, sha256 of SALT + the term lower-cased, words joined by one space)
TERMS = [
    (1, "410068b4f942cc1078d4293afbe05d7cbb853f27240f78b45cf27e041a0aed40"),
    (1, "a7dd46f9c722bc64b4087129078b2f3d3765f4cdfcfb4f65e7a16d02343986bd"),
    (1, "8233d2985b6d73da1722da51f073243faa3deeb0c370b0a324b50abfd3a957aa"),
    (2, "a085556c07e9ad0047b638db9b37cf94c442f3200c17174b1d5d11ce3d6984fe"),
]
RUN = re.compile(r"[^\W_]+")
# ponytail: a one-word term longer than this is not looked for inside a run;
# it bounds the substrings hashed per run (a 64-char digest makes 64 * 40, not 2,080).
MAX_WORD = 40


def normalise(term: str) -> str:
    return " ".join(RUN.findall(term.lower()))


def digest(text: str) -> str:
    return hashlib.sha256(SALT + text.encode()).hexdigest()


def entry(term: str) -> tuple[int, str]:
    words = normalise(term)
    return len(words.split()), digest(words)


def matcher(terms=TERMS):
    """A function from one line to whether any term occurs on it. One per run of
    the gate, so a run of letters seen on many lines is hashed once."""
    wanted = {h for _, h in terms}
    sizes = sorted({n for n, _ in terms if n > 1})
    single = any(n == 1 for n, _ in terms)
    cache: dict[str, bool] = {}

    def inside(run: str) -> bool:
        if run not in cache:
            cache[run] = digest(run) in wanted or any(
                digest(run[i:j]) in wanted
                for i in range(len(run))
                for j in range(i + 1, min(len(run), i + MAX_WORD) + 1)
            )
        return cache[run]

    def match(line: str) -> bool:
        runs = RUN.findall(line.lower())
        return (single and any(inside(r) for r in runs)) or any(
            digest(" ".join(runs[k : k + n])) in wanted for n in sizes for k in range(len(runs) - n + 1)
        )

    return match


def words(paths: list[str], terms=TERMS) -> list[str]:
    """`file:line` for every line of every file on which a term occurs."""
    match, problems = matcher(terms), []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            problems += [f"{path}:{n}" for n, line in enumerate(f, 1) if match(line)]
    return problems


# ── gate (i): data files, notebooks, images ────────────────────────────────
#
# The repository ships no venue market data (NOTICE). A tracked data file, by
# extension or by its content whatever its name, passes only when it is
#
# - in tests/fixtures/SHA256SUMS, the manifest scripts/make-synthetic-fixtures.py
#   writes beside its output, with the hash it has there; or
# - listed by its exact path in NOTICE, with its source and terms.
#
# Every tracked synthetic-* file is in that manifest too, except the replay
# tests' golden digests (synthetic-*.sha256), which must be one digest and
# nothing else: a name is not evidence, the generator's hash is. A committed
# notebook carries no outputs and no execution counts; under legacy/ (archived
# unmodified) one that does must be listed in NOTICE. Raster images, by
# extension or by content, live only under docs/images/ (NOTICE, Screenshots).
#
# Three more rules, each a floor the ones above do not lower:
#
# - the path rule this gate had before: a file under a fixtures/ or samples/
#   directory, or named *sample*, with one of OLD_EXT, is a data file whatever
#   its content (a one-line or pretty-printed JSON array is one);
# - a .json or .yaml file on such a path that holds a list of records passes
#   only from the manifest, NOTICE does not excuse it;
# - any binary file (zip, tar, sqlite, pickle, lz4, parquet, avro, under any
#   name) fails unless NOTICE lists it, except images under docs/images/ and
#   KNOWN_BINARY.

DATA_EXT = {".jsonl", ".ndjson", ".csv", ".tsv", ".parquet", ".avro", ".pcap", ".pcapng",
            ".arrow", ".feather", ".orc", ".gz", ".zst", ".xz", ".bz2"}
DATA_MAGIC = {b"PAR1": "parquet", b"Obj\x01": "avro", b"\xd4\xc3\xb2\xa1": "pcap", b"\xa1\xb2\xc3\xd4": "pcap",
              b"\x4d\x3c\xb2\xa1": "pcap", b"\xa1\xb2\x3c\x4d": "pcap", b"\x0a\x0d\x0d\x0a": "pcapng",
              b"\x1f\x8b": "gzip", b"\x28\xb5\x2f\xfd": "zstd", b"\xfd7zXZ\x00": "xz", b"BZh": "bzip2",
              b"ORC": "orc", b"ARROW1": "arrow"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"II*\x00", b"MM\x00*")
MANIFEST = "tests/fixtures/SHA256SUMS"
DIGEST = re.compile(rb"[0-9a-f]{64}\n?")
FIXTURE_PATH = re.compile(r"(^|/)(fixtures|samples)/|sample[^/]*$", re.I)
OLD_EXT = {".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".txt", ".parquet", ".avro", ".pcap", ".pcapng",
           ".bin", ".gz", ".zst", ".sha256"}
KNOWN_BINARY = {"legacy/v2-kotlin/gradle/wrapper/gradle-wrapper.jar"}  # the archived Gradle wrapper
YAML_RECORD = re.compile(r"""-[ \t]+(\{|\[|("[^"]*"|'[^']*'|[^\s#:][^:#]*?)[ \t]*:([ \t]|$))""")


def binary(head: bytes) -> bool:
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return any(c < "\t" or "\r" < c < " " for c in text)


def sniff(head: bytes) -> str | None:
    """The data format the first bytes of a file are in, or None: binary magic,
    or text whose first five non-blank lines are each a JSON object or array
    (JSON lines), or carry the same non-zero count of commas or tabs (CSV, TSV)."""
    if binary(head):
        return next((fmt for magic, fmt in DATA_MAGIC.items() if head.startswith(magic)), None)
    lines = [x for x in head.decode("utf-8").splitlines() if x.strip()][:5]

    def json_value(x):
        try:
            return isinstance(json.loads(x), (dict, list))
        except ValueError:
            return False

    if len(lines) >= 2 and all(json_value(x) for x in lines):
        return "json lines"
    for delim, fmt in ((",", "csv"), ("\t", "tsv")):
        if len(lines) >= 3 and lines[0].count(delim) and len({x.count(delim) for x in lines}) == 1:
            return fmt
    return None


def image(path: str, head: bytes) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXT or binary(head) and (
        head.startswith(IMAGE_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def records(path: str, content: bytes) -> bool:
    """A .json or .yaml/.yml document whose top level is a list of objects or
    of rows. YAML is read for its first line of content only: stdlib has no
    parser, and a top-level `- key: value` or `- {` is the shape of a record
    list."""
    suffix = Path(path).suffix.lower()
    text = content.decode("utf-8", errors="replace")
    if suffix == ".json":
        try:
            doc = json.loads(text)
        except ValueError:
            return False
        return isinstance(doc, list) and bool(doc) and (
            all(isinstance(x, dict) for x in doc) or all(isinstance(x, list) for x in doc))
    if suffix in (".yaml", ".yml"):
        first = next((x for x in text.splitlines() if x.strip() and not x.lstrip().startswith("#")
                      and x.strip() != "---"), "")
        return bool(YAML_RECORD.match(first))
    return False


def notebook_problem(content: bytes) -> str | None:
    try:
        cells = json.loads(content)["cells"]
    except (ValueError, KeyError, TypeError):
        return "not a notebook JSON document"
    code = [c for c in cells if c.get("cell_type") == "code"]
    dirty = sum(bool(c.get("outputs")) or c.get("execution_count") is not None for c in code)
    return f"{dirty} code cells carry outputs or an execution count" if dirty else None


def data_problems(tracked: dict[str, bytes], notice: str) -> list[str]:
    """Pure: every tracked path and its bytes, and NOTICE's text."""
    listed = set(notice.split())
    manifest = {}
    for row in tracked.get(MANIFEST, b"").decode().splitlines():
        digest, _, path = row.partition("  ")
        manifest[path] = digest
    problems = []
    for path, content in sorted(tracked.items()):
        name, head = Path(path).name, content[:65536]
        if path in manifest:
            if not name.startswith("synthetic-"):
                problems.append(f"{path}: in {MANIFEST} but not named synthetic-*, as everything the generator writes is")
            elif hashlib.sha256(content).hexdigest() != manifest[path]:
                problems.append(f"{path}: differs from its hash in {MANIFEST}; run python3 scripts/make-synthetic-fixtures.py")
            continue
        if name.startswith("synthetic-"):
            if not (name.endswith(".sha256") and DIGEST.fullmatch(content)):
                problems.append(f"{path}: named synthetic-* but not in {MANIFEST}, which only the generator writes")
            continue
        if path.endswith(".ipynb"):
            problem = notebook_problem(content)
            if problem and not (path.startswith("legacy/") and path in listed):
                problems.append(f"{path}: {problem}; clear them (Jupyter: Clear All Outputs)")
            continue
        if image(path, head):
            if not path.startswith("docs/images/"):
                problems.append(f"{path}: a raster image outside docs/images/")
            continue
        if binary(head):
            if path not in KNOWN_BINARY and path not in listed:
                problems.append(f"{path}: a binary file ({sniff(head) or 'format unknown'}) not listed in NOTICE")
            continue
        under = bool(FIXTURE_PATH.search(path))
        if under and records(path, content):
            problems.append(f"{path}: a list of records under a fixtures or samples path, not in {MANIFEST}")
            continue
        suffix = Path(path).suffix.lower()
        fmt = "extension" if suffix in DATA_EXT or (under and suffix in OLD_EXT) else sniff(head)
        if fmt and path not in listed:
            problems.append(f"{path}: a data file ({fmt}) neither in {MANIFEST} nor listed by path in NOTICE")
    problems += [f"{MANIFEST}: lists {path}, which is not tracked" for path in sorted(manifest.keys() - tracked.keys())]
    return problems


def tracked_files() -> dict[str, bytes]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    paths = [p for p in out.decode().split("\0") if p]
    # ponytail: a tracked path deleted in the working tree is skipped, as git diff would show it.
    return {p: (ROOT / p).read_bytes() for p in paths if (ROOT / p).is_file()}


# ── gate (j): captured-data counts in editable Markdown ─────────────────────
#
# The rule (ADR-029, NOTICE): the repository ships no venue market data and no
# numbers derived from it. A count of captured trades, rows, records, frames
# and the like is such a number. Editable documents state a ratio, a size in
# bytes, a duration, or a pointer to the platform table that holds the figure;
# an example of a command's output uses invented values.
#
# The exceptions, and the only ones: the immutable snapshots below, which keep
# what was true when they were written (CLAUDE.md, Immutability; NOTICE lists
# the files among them that carry such counts). Every other tracked *.md is
# checked, code blocks included, because a pasted log line is where most of
# these were found.
#
# What it looks for is the high-risk shape only, not every number: four or more
# digits (separators allowed), or a figure with a magnitude (10.4 M, 236k),
# then at most one word, then one of UNITS. A count that is the platform's own
# (tests, alerts, a configured bound) or invented carries `counts-ok: <reason>`
# on its line, in an HTML comment outside code (`<!-- counts-ok: ... -->`).

IMMUTABLE = ("docs/adr/", "docs/audits/", "docs/benchmarks/", "docs/research/", "docs/plans/", "legacy/")
UNITS = ("trades", "rows", "records", "frames", "buckets", "snapshots", "messages", "updates", "events",
         "offsets", "book-seconds")
COUNT = re.compile(
    r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d{4,}|\d+(?:\.\d+)?\s?(?:[kKMB]|million|billion))"
    r"\s+(?:[\w`-]+\s+)?(?:" + "|".join(UNITS) + r")\b", re.I)
OPT_OUT = re.compile(r"counts-ok:\s*\S.{7,}")


def count_problems(path: str, text: str) -> list[str]:
    return [f"{path}:{n}: {m.group(0)}" for n, line in enumerate(text.splitlines(), 1)
            if not OPT_OUT.search(line) for m in COUNT.finditer(line)]


def counts(tracked: dict[str, bytes]) -> list[str]:
    problems = []
    for path, content in sorted(tracked.items()):
        if path.endswith(".md") and not path.startswith(IMMUTABLE):
            problems += count_problems(path, content.decode("utf-8", errors="replace"))
    return problems


def read_term() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("term (not echoed): ")
    return sys.stdin.readline()


def main(argv: list[str], terms=TERMS) -> int:
    if argv[:1] == ["hash"]:
        n, h = entry(read_term())
        print(f'    ({n}, "{h}"),')
        return 0
    if argv[:1] == ["words"]:
        problems = words(argv[1:], terms)
    elif argv[:1] == ["data"]:
        problems = data_problems(tracked_files(), (ROOT / "NOTICE").read_text())
    elif argv[:1] == ["counts"]:
        problems = counts(tracked_files())
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
