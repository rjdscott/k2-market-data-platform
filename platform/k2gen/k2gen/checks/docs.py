"""Document checks: both schemas on every document; product names unique, valid and not reserved
(model.product_name_problem); one producer per table; every column described; terms before a
non-owner grant; for an incremental dbt table, primaryKey holds every partition source column (A7).

Filled by step S05; its failing fixtures go under tests/fixtures/v2/bad/docs/.
"""

from k2gen.model import DocumentSet, Finding


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    raise NotImplementedError("S05")
