## What this does and why

<!-- Plain sentences. For a change under contracts/**/models/ this section is required: the grain
(what one row is), the rule the model applies, the fact that makes the rule necessary, and why
the materialization (table or incremental) fits. The same words open the model file.
How a reviewer reads it: docs/development/reviewing-dbt-work.md -->

## Verification

<!-- The commands you ran and what they printed. For a dbt product change: -->

- [ ] `make test-k2gen`
- [ ] `make test-dbt-tree`
- [ ] `make test-dbt` (needs a container: say so if it is left for the merge point)
