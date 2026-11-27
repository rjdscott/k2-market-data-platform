# A compiled release for k2-api's tests

`v3.0.0-alpha.3-rc.1-<sha12>/` holds the files k2-api reads from a release, copied unchanged from
the release k2gen compiles out of its good fixture set (`platform/k2gen/tests/fixtures/v2/good/`,
every product, principal and value in it invented). Nothing here is written by hand, and no file
holds a row of data.

| File | Read by |
|---|---|
| `manifest.json` | `GET /v1/release` |
| `compiled/api/principals.json` | the key check: the principals k2-api may act as |
| `compiled/catalog/index.json`, `compiled/catalog/products/*.json` | the metadata routes; every identifier a data query names |
| `compiled/trino/views.json` | the base whose `$refs` holds a view's tested snapshot |
| `documents/contracts/**/*.odcs.yaml` | the contract route |
| `compiled/trino/rules.json`, `compiled/trino/groups.txt` | the tests' fake Trino only, which enforces these rules |

Regenerate after a change to the good set, to k2gen's emitters or to `docker-compose.yml`, then
review the diff as code:

```bash
bash services/k2-api/tests/release/regenerate.sh
```

It compiles in a temporary directory, with no container and no network, and replaces the release
directory here; the tests find it by its name, so a new release id needs no test change.

It is not under a `fixtures/` directory because the data gate of `make check-docs`
(`scripts/check_docs.py`, gate (i)) reads every `.json` file on such a path as a data file.
