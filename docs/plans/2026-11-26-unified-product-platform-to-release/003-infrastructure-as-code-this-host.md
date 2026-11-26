# Phase 003: infrastructure as code, this host

**Depends on:** `002` (tag `v3.0.0-beta.4`); the release's storage plan, built in the first
plan's release spine so that this phase converges with it rather than rebuilding resource
creation (review finding F21).
**Decision it implements:** `docs/adr/ADR-056-unified-data-product-platform.md` and the owner's
decision on scope: this host only, fully proven. A new ADR, in the pull request that builds it,
records the tool and what it owns.
**Delivers:** every resource the platform needs on this host declared and checkable: MinIO
buckets, users, keys and policies; Lakekeeper warehouses and namespaces; Redpanda topics and
their retention; Postgres roles and databases; Trino catalogs. `make converge CHECK=1` reports
drift and changes nothing; `make converge` makes the host match.
**Exit:** on a converged host `make converge CHECK=1` exits 0 with an empty report; after one
hand edit it reports exactly that change and exits non-zero; `make converge` then removes the
drift and the check is empty again; the report names the listener on `0.0.0.0:5432` as outside
K2; tag `v3.0.0-beta.5`.

## Scope

- **What is declared**: the release's storage plan (tables, columns, buckets, keys, namespaces)
  extended to every other resource of this host the platform creates today in init scripts:
  topics and their retention (`docker/redpanda/init.sh`), MinIO users and policies, Postgres
  roles and databases, Trino catalogs.
- **The tool** is the ADR's choice between an existing tool with providers for each resource and
  the storage plan's own engine given a check mode. Either way there is one declaration and one
  engine, and deploy and converge share it.
- **Check mode** reads every resource, compares it with the declaration, prints one line per
  difference and changes nothing. **Converge** applies the differences in dependency order and
  is safe to repeat.
- **Out of scope:** a cloud deployment, its own plan after 3.0. Resources that are not K2's are
  reported, never changed: the non-K2 listener on `0.0.0.0:5432` is the owner's.
- The infrastructure-drift control of `002` stops reporting "not applicable" and reads this
  check.

**Learning aids and documents.** Runbook: converge the host and read a drift report.

## Verification

```bash
make converge CHECK=1; echo "exit $?"          # empty report, exit 0
docker exec k2-redpanda rpk topic alter-config <a raw topic> --set retention.ms=259200000   # longer, loses nothing
make converge CHECK=1; echo "exit $?"          # one line, the retention change; exit non-zero
make converge && make converge CHECK=1          # empty again
make converge CHECK=1 | grep 5432               # reported as outside K2
make test
make check-docs
```
