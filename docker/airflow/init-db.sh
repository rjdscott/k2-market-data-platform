#!/usr/bin/env bash
# The airflow-migrate one-shot: make sure the `airflow` role and database exist
# on prefect-db, then migrate the schema.
#
# Not an init SQL file in docker/postgres/ddl/: those run only when the
# postgres-data volume is first created, and every existing stack has one.
# Idempotent: the role is altered on every run, so the password in .env and
# the connection limit converge, and the database is created only if missing.
#
# CONNECTION LIMIT 100 of the instance's 200 (max_connections, docker-compose.yml)
# keeps Airflow from starving the `prefect` role, which Prefect and the
# Lakekeeper catalog both connect as. Airflow hits "too many connections for
# role" first; the catalog keeps its half. ADR-034 has the arithmetic.
set -euo pipefail
: "${AIRFLOW_DB_PASSWORD:?AIRFLOW_DB_PASSWORD is not set (.env.example)}"

# PGHOST, PGUSER and PGPASSWORD come from the compose service: the superuser
# the postgres image created, the only role that may create another.
psql -v ON_ERROR_STOP=1 -d postgres -v pw="$AIRFLOW_DB_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE airflow LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'airflow')\gexec
ALTER ROLE airflow WITH LOGIN PASSWORD :'pw' CONNECTION LIMIT 100;
SELECT 'CREATE DATABASE airflow OWNER airflow' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow')\gexec
SQL

exec airflow db migrate
