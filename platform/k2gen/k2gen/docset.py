"""The document-set adapter (A2), filled by step S04.

In 3.0 the store is git: a set is `git archive <tag>` of model.DOCUMENT_ROOTS, byte-identical
on a re-run, with its provenance record. After 3.0 only this module changes, to read a
registry's approved version (ADR-055 part 7).
"""

from pathlib import Path

from k2gen.model import DocumentSet


def load(tag: str, repo: Path) -> DocumentSet:
    """The document set of an annotated release tag in `repo`, with its provenance."""
    raise NotImplementedError("S04")


def write(docs: DocumentSet, out: Path) -> None:
    """Write the set to `out`: each document at its path, and provenance.json (model.PROVENANCE)."""
    raise NotImplementedError("S04")


def read(set_dir: Path) -> DocumentSet:
    """A set `write` wrote, or a release's documents/ directory; `--set` and `--previous` read this."""
    raise NotImplementedError("S04")
