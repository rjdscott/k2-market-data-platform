# Audit: <surface> — YYYY-MM-DD

**Verdict:** one paragraph. Is this surface trustworthy as published? What is
the single worst thing found? What must change before the next tag?

| | |
|---|---|
| **Commit** | `<short sha>` |
| **Scope** | paths audited |
| **Out of scope** | what was deliberately not looked at |
| **Lens** | doc accuracy / correctness / security / coverage / resources |
| **Method** | how claims were checked (commands run, stack state, agents used) |
| **Findings** | N BLOCKER · N HIGH · N MED · N LOW |

---

## Findings

| Severity | Location | Claim | Reality | Fix |
|----------|----------|-------|---------|-----|
| BLOCKER | `docs/x.md:31` | what the doc says | what the command showed | what to change |
| HIGH | … | … | … | … |

Every row carries `file:line` or a command with its output. A row you could not
demonstrate was dropped, not softened.

---

## Resolutions

Appended after publication; the table above is never edited.

- **HIGH `docs/x.md:31`** — Resolved in `<commit>`.
