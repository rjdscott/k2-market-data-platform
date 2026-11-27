# Runbook: Grant and revoke access to a product

Use this to give a principal a role on a product, to take it away, and to prove each. Which roles exist
and what each sees: [`contracts/access/roles.yaml`](../../contracts/access/roles.yaml); how the rules are
compiled and why default deny holds: [chapter 22](../architecture/22-access-and-identity.md) and
[ADR-059](../adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md). Passwords and
keys, rotation included: [create-or-rotate-a-principal.md](./create-or-rotate-a-principal.md) and
[tls-and-passwords.md](./tls-and-passwords.md), not repeated here. Proving a release's rows:
[verify-access-after-a-release.md](./verify-access-after-a-release.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **Where these commands ran.** The documents and the checks, on 2026-11-27, in a scratch clone of the repository
> through `scripts/verify/ci-validate.sh <scratch dir>` (CI's validation runner): nothing deployed. A grant
> has never been deployed on the live stack, and the live stack has deployed one release, the cutover's
> ([first-cutover-to-releases.md](./first-cutover-to-releases.md)). The deploy steps are as in
> [deploy-a-release.md](./deploy-a-release.md), run on the rehearsal only.

| # | Task | MTTR target | Measured |
|---|------|-------------|----------|
| 1 | Grant a role | one pull request, then a deploy under 1 min | the checks not timed; a rules-only deploy 9.1 to 13.4 s, its rules loaded 0.19 to 4.25 s after the swap (2026-11-27, rehearsal); live: not yet |
| 2 | Revoke a role | as 1 | a removed grant refused after 5.0 to 5.1 s, no restart (2026-11-26, throwaway lake); live: not yet |

---

## 1. Grant a principal a role on a product

**Symptom**, a person or service must read a product (for example `governance.access-canary`).

**Detection**, manual only: nothing alerts on a missing grant; the reader gets an access denial.

**Expected behaviour**, access is default deny. Nothing is edited on the stack: the rules are compiled from
the documents into each release (ADR-059), so a grant is a document change that travels as a release. A
rules-only release restarts nothing; Trino rereads the rules on its refresh (5 s), so the grant is usable
within about that long after the pointer swap.

**Recovery**

1. In a branch, edit the documents:
   - `contracts/access/principals.yaml`: declare a new principal (`id`, `kind`, `owner`, `description`; a
     person under a pseudonymous id, never a credential). Skip this for an existing one.
   - the product's `grants.yaml`, as [`contracts/governance/access-canary/grants.yaml`](../../contracts/governance/access-canary/grants.yaml)
     is: `- principal: <id>` and `role: <full_reader | delayed_reader | public_reader>`. One role per
     principal per product.
   - `tests/access/expectations.yaml`: a read row for the new holder when the check below asks for one. It asks
     for a row per table and role someone holds, not per principal: a second holder of a role that already
     has a row needs none, a first holder does. Rows are hand-written, never generated
     ([`tests/access/README.md`](../../tests/access/README.md)).
2. Check the documents as CI will, in a scratch copy, and read any `error` line (section 3):

```bash
bash scripts/verify/ci-validate.sh "$(mktemp -d)"     # outside the repo; prints the trial release's path; exit 0 is the verdict
```

3. A new principal needs a credential, filed outside the repository (the id must be declared first, so run it
   after step 1 on the same checkout): `make password PRINCIPAL=<id>` prints the password once. Hand it over
   by a private channel; for a service use `TO_FILE=1`.
4. Open the pull request. CI's validation runner shows the `k2gen diff` against the release live now: the
   rules lines are the grant. The product's owner reviews it. Merge, then an annotated `v*` tag on `main`.
5. Release, read the plan, deploy:

```bash
make release TAG=<tag>
make release-diff RELEASE=<release id>       # the grant in the rules; "Recreate: none" for a rules-only release
make deploy RELEASE=<release id> PLAN=1
make deploy RELEASE=<release id>
```

6. Verify: `make release-verify` (exit 0), and the expectation row passes: `make access-verify RELEASE=$HOME/k2-releases/<release id>`
   on a throwaway lake. The live suite runs only rows whose principal's password the platform holds, and is
   a visible SKIP until phase 002 publishes the access canary.
   <!-- VERIFY: first real grant, phase 002 -->

**Measured**, see the table. A grant deployed live is not measured: it has never run on this stack.

---

## 2. Revoke a role

**Symptom**, a person leaves, a role is too wide, or a service no longer reads a product.

**Detection**, manual only.

**Expected behaviour**, two things end at different times. The password line is removed at once and a
login is refused within seconds. The role ends when the release without the grant is deployed and its
rules load (5 s refresh). Until then the old rules stand, so for an urgent revoke remove the credential
first.

**Recovery**

```bash
make principal-remove PRINCIPAL=<id> FORCE=1  # the password line, keys and file; FORCE=1 because it is still declared until the release
```

Then, in a branch, delete the grant from the product's `grants.yaml`, and the principal from
`contracts/access/principals.yaml` if it leaves the platform. Remove its expectation rows that name it, and keep one row per held role. Run the check as in
section 1, pull request, tag, `make release TAG=<tag>`, `make deploy RELEASE=<release id>`. Verify the denial:
the row for the old holder is a denial row (`denied: true`), and `make release-verify` exits 0.
<!-- VERIFY: first real grant, phase 002 -->

**Measured**, a removed grant was refused after 5.0 to 5.1 s (n=5), no restart, on a throwaway lake
(2026-11-26, [verify-access-after-a-release.md](./verify-access-after-a-release.md) section 6). Not yet live.

---

## 3. What you cannot do, and what a wrong grant says

- **No edit on the stack.** A rules file in a running Trino is a release's own, sealed read-only; a manual
  edit is overwritten by the next deploy and is not reviewed. Every change is a document and a release.
- **One role per principal per product.** A second is refused (`access.grant-two-roles` below).
- **Delay and row scope are for trusted readers.** They shape what a reader sees; they are not a boundary
  against a reader who is hostile (ADR-061, Outcome). Do not grant them to an untrusted party.

The check names the fault with a code. Real lines from the scratch runs of 2026-11-27:

```text
error access.grant-two-roles contracts/governance/access-canary/grants.yaml: p.canary-full holds full_reader, public_reader on access-canary; a principal holds one role per product
error access.grant-unknown-principal contracts/governance/access-canary/grants.yaml: p.new-analyst is not declared in contracts/access/principals.yaml
error access.grant-unknown-role contracts/governance/access-canary/grants.yaml: p.new-analyst's role auditor_reader is not declared in contracts/access/roles.yaml
error access.expectation-missing tests/access/expectations.yaml: no read row for governance.access_canary.events by a holder of full_reader (p.canary-full): add one listing the columns it sees
```

The first is a second role; the second a principal not yet declared; the third a role no table offers; the
fourth a held role with no hand-written read row. A grant of a role the product's tables do not offer is
`access.grant-role-not-offered`. The remaining codes are listed in chapter 22.

---

## Failure modes / incidents

- None yet: no grant has been deployed on the live stack.

---

**Last verified:** 2026-11-27, the documents and the checks only, in a scratch clone, on the phase 001 branch;
the deploy steps as in [deploy-a-release.md](./deploy-a-release.md) (the rehearsal). A live grant and a live
revoke are not run.
