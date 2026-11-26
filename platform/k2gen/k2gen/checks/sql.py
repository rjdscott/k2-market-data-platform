"""The SQL lint of product models and config. Errors: env_var, a definer or materialized view,
incremental_predicates, source() of another product outside k2_input. Warnings: run_query,
statement, call, adapter., any other relation outside source, ref and k2_input.

Filled by step S07; its failing fixtures go under tests/fixtures/v2/bad/sql/.
"""

from k2gen.model import DocumentSet, Finding


def check(docs: DocumentSet, previous: DocumentSet | None) -> list[Finding]:
    raise NotImplementedError("S07")
