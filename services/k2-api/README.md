# k2-api

Read-only REST over the data products of the release it runs, on `127.0.0.1:8095`. Its routes,
parameters and error bodies are [`openapi.yaml`](openapi.yaml), frozen in phase 001 and served as
it is at `/v1/openapi.json`. k2-api decides no access: a data request runs on Trino as the key's
principal (svc.api logs in with its own password and impersonates the caller, which the release's
`rules.json` allows only for the principals of `compiled/api/principals.json`), and Trino's rules
decide what it sees. It never writes, accepts SQL, reads storage or Lakekeeper, or keeps a row.

| Module | What |
|---|---|
| `k2api/app.py` | the routes; every name a statement holds comes from the release's catalog document |
| `k2api/keys.py` | `Authorization: Bearer <key>` to a principal through `/run/k2-secrets/api/keys.json` |
| `k2api/db.py` | the one Trino client: svc.api's login, the caller as session user, values bound |

`/catalog/` and `/dbt-docs/` are files, not API routes (so not in `openapi.yaml`): the trees that
`k2site.py` and `k2-dbt docs` publish as `<name>/current` on the `k2-site` volume, mounted read-only
at `/srv/k2-site`. Each answer is 503 `not generated yet` before the first tree, carries
`Cache-Control: no-cache`, `nosniff` and a CSP of its own, and never follows a link out of the tree.

```bash
cd services/k2-api && uv run --locked pytest -q && uv run --locked ruff check .   # or: make test-api
```

The tests need no network and no Trino: a fake at `db.run` enforces the compiled rules of the
release in [`tests/release/`](tests/release/README.md).

## Status codes

| Status | Error codes | When |
|---|---|---|
| 400 | `unknown_parameter`, `unknown_column`, `bad_limit`, `bad_filter`, `bad_cursor`, `bad_request` | a name the route or the contract does not declare, a malformed value, a parameter twice, any `cursor` |
| 401 | `unauthorized` | no key on a data route; an unknown key, or one whose principal the release does not serve |
| 403 | `forbidden` | Trino denied the caller (a bronze table offers no role) |
| 404 | `not_found`, `not_published` | no such product or table in the release, or an output with no reader relation; a table never tested, to a caller Trino lets read it |
| 422 | `as_of_unavailable`, `result_too_large` | `as_of` given; more rows match than `limit` |
| 503 | `trino_unavailable`, `query_failed`, `snapshot_moving`, `no_release` | Trino did not answer or could not run the read; the tested tag moved during two reads; the pointer names no release |

## Where the frozen document is silent

Decided 2026-11-27, for phase 007 to fold into `openapi.yaml`:

- 503 and `not_published` stay as above; the document declares no 5xx and no code for a table
  never tested.
- Row values: a decimal is its exact string, a time is an ISO date and time in UTC, a bigint is a JSON
  number. The snapshot id is a decimal string, as the document says.
- Rows are sorted by the table's declared primary key (`primaryKeyPosition`, then declaration
  order), so SQL and REST answers compare row for row; a table with no key is not sorted.
- `access.allowed` is false for any refusal, a relation not yet published included.
- 2026-11-27: a data request asks Trino first, as the caller, for a zero-row read of the relation
  (the probe `access` uses): a denial is 403 whatever the relation's publication state, and only an
  allowed caller learns `not_published`. It costs one more query per data request, accepted.
