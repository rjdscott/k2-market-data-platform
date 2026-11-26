#!/usr/bin/env python3
"""The Python half of scripts/check-docs.sh. Standard library only.

    python3 scripts/check_docs.py words FILE...   # gate (b): forbidden words
    python3 scripts/check_docs.py hash            # print the entry for a new forbidden term

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
import re
import sys

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
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
