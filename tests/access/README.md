# Access expectations

`expectations.yaml` says, in plain rows, who may see what. A person writes each row and a
reviewer reads it. `make access-verify` runs every row against a running Trino as the principal
the row names. The procedure, and what to do when a row fails, is in
[verify-access-after-a-release](../../docs/runbooks/verify-access-after-a-release.md).

## What "non-circular" means here

The access rules (`compiled/trino/rules.json`) are generated from the product documents by
`k2gen`. A test generated from the same documents by the same code would repeat whatever the
generator got wrong, and pass. So these rows are never generated. Each one comes from what the
product's owner promised, as in "the delayed reader never sees a row younger than 20 minutes".
`access-verify` then asks the running Trino whether it keeps that promise. When the rules and the
rows disagree, a person decides which one is wrong.

`k2gen check` makes sure a row exists for every role with a policy, every base and every seed
(`checks/access.py`). It never says what a row must contain beyond that. `access-verify` adds
the run time: a row naming a principal or relation the running release does not have fails.

## The six kinds of row

| Kind | Looks like | Passes when |
|---|---|---|
| read | `{principal, table, visible, masked?, minRowAgeMinutes?, mustNotAppear?, mustAppear?}` | the principal reads the table and gets exactly the `visible` columns; every `masked` column is null in every row; no row is younger than `minRowAgeMinutes` by the table's delay column; no row matches `mustNotAppear`; at least `mustAppear.minRows` rows match `mustAppear.where` |
| denial | `{principal, table, denied: true}` | Trino refuses the read with an access denial (`PERMISSION_DENIED`) |
| statement | `{principal, sql, session?, expect}` | the statement ends as `expect` says: `denied` is an access denial; `error` is `NOT_SUPPORTED` (a version on a view); `noBounds` is `SHOW STATS` with no low value, high value or row count; `ok` is a statement that completes without error |
| probe | `{probe: {from, target}, expect}` | the network connection is refused or open; step S22's `scripts/verify/catalog-fence.sh` runs these |
| drift | `{drift: catalog, expect: clean}` | the catalog holds exactly what the release generates (`scripts/verify/catalog_drift.py`) |
| storage key | `{storageKey: {engine, target, operation}, expect}` | the engine's own `K2_<ENGINE>_S3_*` key, sent from where the suite runs to `$K2_S3_ENDPOINT`: `allowed` when every request answers 2xx; `refused` when the first answers 403 `AccessDenied` and some key shows the bucket exists (`scripts/access_verify.py`, `storage_key`) |

A row passes only on the outcome it names. A syntax error, a missing table, `NOT_SUPPORTED` or a
refused login never counts as a denial. Each of those means the row named something wrong, not
that a rule held.

## How to write a row

- Name a real principal, declared in `contracts/access/principals.yaml`, and the relation by its
  full name: `<catalog>.<namespace>.<table>`. A reader reads `<domain>.<fence>.<table>`. A base is
  `<domain>.<fence>__base.<table>`.
- Write a read row's predicates in the table's own columns, as SQL a reviewer can read:
  `mustNotAppear: "id NOT IN (1, 2, 4)"`. Every read row of a role with a policy needs a
  `mustAppear`. Without one, a table that built no rows would pass every negative assertion.
- One row asserts one thing. Comments say why the row exists and which gate row it comes from
  (`h1.08` and so on, in `docs/research/2026-11-26-tested-data-view-gate.md`).
- Rows run in file order. A row that depends on an earlier one says so; the operator's
  query-history row needs the readers' queries above it.
- A row that plants something needs no setup of its own. `access-verify` plants the foreign view
  for its row through the catalog API on the throwaway lake, then removes it, and skips that row
  on the live system.
- On the live system only rows whose principal's password the platform holds run (services and
  the access canary's readers), and only when they name nothing but the canary. Every other row
  prints a SKIP that names the release-time run where it did run.

Two kinds the gate asks for cannot be run from this file yet: an impersonation that must succeed
(the frozen schema has no such outcome), and a user no document declares. `k2gen check` accepts
a denial row as an undeclared principal, but the runner does not run such rows yet: it fails
every row naming a principal the release does not declare. `scripts/trino_rules_test.py` proves
both today (its rows `impersonation` and `strangers`).

## A read row on a placeholder

At release time `access_verify.py stage` builds each dbt output on the throwaway lake from its
model, rendering only `ref()` and `config()`. A model with any other Jinja (`source()`,
`k2_input()`, `is_incremental()`) is dbt's to render, so stage makes a placeholder instead: one
row in the contract's columns and types, its key a literal and the rest null, the shape checked
with `DESCRIBE`. The reader view over it and the rules are the release's own.

A read row that names only `principal`, `table` and `visible` runs on such a placeholder. It
proves that the principal may read the reader view under the release's rules and that the view
shows exactly those columns. It proves nothing about the model's rows, and not that dbt builds
the model to the contract's shape (`make test-dbt` does). A read row with `masked`,
`minRowAgeMinutes`, `mustNotAppear` or `mustAppear` needs the model's real rows, so stage refuses
it on a placeholder.

## The proof behind the suite

`make test-access RELEASE=<release dir>` runs `prove.sh` on a throwaway lake. It shows that the
suite passes. It shows that one loosened rule fails exactly the right row. It shows that the
drift and marker checks name each plant. `test_access_verify.py` (in `make test-python`) holds the
verdicts and the drift comparison without a lake.
