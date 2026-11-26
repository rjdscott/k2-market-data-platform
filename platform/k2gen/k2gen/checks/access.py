"""Access checks: grants name declared roles and principals, one role per principal per product;
an output's policy at least as strict as each input's (ARCHITECTURE-V2 4.6); coverage (A9):
every view and role with a policy, every base table and every denied layer has an expectation row.

Filled by step S07; its failing fixtures go under tests/fixtures/v2/bad/access/.
"""

from k2gen.model import DocumentSet, Finding


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    raise NotImplementedError("S07")
