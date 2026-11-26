# Phase 000: the remaining connector kinds

**Depends on:** the demo tag `v3.0.0-beta.1` (the fetch container, the `https-file` and
public-API connectors, the redirect and address proofs, `make test-connectors`).
**Decision it implements:** [`ADR-056-unified-data-product-platform.md`](../../adr/ADR-056-unified-data-product-platform.md): the platform
starts at the topic, the file or the API, and all four connector kinds are proven before the
crypto venues widen. ADR-036, accepted in the first plan, gains the three kinds in an Outcome
if their shape differs from it.
**Delivers:** the `sftp`, `ftp` (TLS required) and `s3-file` connectors in the fetch container;
SFTP and FTPS test servers under a `test` compose profile; a short SFTP CSV product to silver.
**Exit:** each new kind's DAG runs; `make test-connectors` covers the new kinds with no network;
an unknown SFTP host key fails; FTP without TLS is refused unless the source is anonymous public
data; the SFTP product passes `make product-verify` and its expectation rows; tag
`v3.0.0-beta.2`.

## Scope

**Connectors**, each running in the fetch container under the same address allow-list, size cap,
timeouts and spool as the first plan built:

| Kind | Required fields | Client |
|---|---|---|
| `sftp` | `host`, `path` (templated by run date), `user`, `keyFileEnv`, `hostKey` | OpenSSH `sftp -b`, with the declared host key as the only known host |
| `ftp` | `host`, `path`, `tls: required` (plain only for anonymous public data), `credentialsEnv` | `ftplib.FTP_TLS` |
| `s3-file` | `endpoint`, `bucket`, `key`, `credentialsEnv`, `format`, `parser` | the S3 API, with the endpoint on the allow-list |

The landing, the load onto the audit branch, the in-order publish and the run records are the
`https-file` path, unchanged. Secrets are named by environment variable in the product's
documents and never compiled into a release.

**Test servers.** SFTP and FTPS servers built from a Debian base pinned by digest, under the
compose profile `test`, so they never start with `make up` and the budget gate counts them only
when the profile is on. `make test-connectors` runs every kind against them and against local
fakes with no outside network.

**The SFTP product.** A short CSV product: `connector: {kind: sftp, host: sftp.example.test,
path: "/out/prices_{{ds}}.csv", user: k2, keyFileEnv: K2_SFTP_KEY, hostKey: "ssh-ed25519 ...",
format: csv, parser: csv_records}`; bronze lists the columns as `varchar` with a key; one dbt
model types them into silver. Its data is synthetic and generated for the test server. It brings
its own grants and expectation rows.

**Learning aids and documents.** Runbooks: onboard an FTP or SFTP product; rotate a credential.

## Verification

```bash
docker compose --profile test up -d sftp-test ftps-test
docker exec k2-airflow-scheduler airflow dags test <sftp product>
docker exec k2-airflow-scheduler airflow dags test <ftp product fixture>
docker exec k2-airflow-scheduler airflow dags test <s3-file product fixture>
make test-connectors        # no network: every kind; unknown host key, plain FTP, off-list host all fail
make product-verify PRODUCT=<sftp product>
make access-verify
make test
make check-docs
```
