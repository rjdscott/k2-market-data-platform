# Security

This is a personal portfolio project designed to run on a single host. It is **not** hardened for production use:

- Every service authenticates with the passwords in `.env`. Copy `.env.example` and change all of them before starting the stack — the defaults are placeholders, not secrets.
- No TLS between services or on any exposed port.
- ClickHouse, Redpanda, MinIO, Prefect, Prometheus and Grafana listen on `localhost` only via Docker port mappings; do not expose them to a network without adding authentication and TLS.
- Container images are pinned but not rebuilt on a schedule; Dependabot and a Trivy scan in CI surface known CVEs but nothing auto-patches.

## Reporting

If you find something worth reporting, open a [private security advisory](https://github.com/rjdscott/k2-market-data-platform/security/advisories/new) or an issue. There is no response-time commitment.

Only the `main` branch is maintained. `legacy/v1/` is archived code with [known issues](legacy/v1/README.md#known-issues-left-as-is) and is not deployed anywhere.
