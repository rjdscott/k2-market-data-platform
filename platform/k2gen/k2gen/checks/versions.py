"""Versions and transitions against the previous release: the lifecycle moves of ADR-055 part 4;
a dropped, retyped, renamed or re-keyed column is a new major version; no retirement while
another product declares the table as an input. `previous` None: every product is new.

Filled by step S06; its failing fixtures go under tests/fixtures/v2/bad/versions/.
"""

from k2gen.model import DocumentSet, Finding


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    raise NotImplementedError("S06")
