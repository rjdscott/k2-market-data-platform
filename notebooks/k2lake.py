"""
One connection to the lake for every notebook: DuckDB attached to the Lakekeeper
REST catalog, reading Parquet from MinIO, in UTC.

    from k2lake import connect
    con = connect()
    con.sql("SELECT count(*) FROM lake.gold.trades").show()

Runs on the HOST (not in a container), so the endpoints are the published ports:
Lakekeeper on localhost:18181 and MinIO on localhost:9000. The Iceberg metadata
holds `s3://k2-lake/...` locations; the S3 secret below tells DuckDB which
endpoint that bucket lives on. Credentials come from ../.env (MINIO_ROOT_USER /
MINIO_ROOT_PASSWORD), never from this file.

`SET TimeZone = 'UTC'` is load-bearing: Spark writes Iceberg TIMESTAMP as
timestamptz and DuckDB renders it in the session zone, which defaults to the
host's — on a UTC+10 machine every bucket lands ten hours late
(scripts/parity_ohlcv.py found this the hard way, 2026-08-27).
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

LAKEKEEPER = os.environ.get("K2_LAKEKEEPER_HOST_URL", "http://localhost:18181/catalog")
S3_ENDPOINT = os.environ.get("K2_S3_HOST_ENDPOINT", "localhost:9000")
SCALE = 100_000_000  # 1e-8 fixed point, the wire's and gold's representation


def _env() -> dict:
    """MINIO_* from the repo's .env when not already in the environment."""
    env = dict(os.environ)
    dotenv = Path(__file__).resolve().parent.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())
    return env


def connect() -> duckdb.DuckDBPyConnection:
    env = _env()
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute(
        f"""CREATE SECRET s3sec (TYPE S3, KEY_ID '{env["MINIO_ROOT_USER"]}', SECRET '{env["MINIO_ROOT_PASSWORD"]}',
            ENDPOINT '{S3_ENDPOINT}', URL_STYLE 'path', USE_SSL false, REGION 'local-01')"""
    )
    con.execute(
        f"ATTACH 'k2' AS lake (TYPE ICEBERG, ENDPOINT '{LAKEKEEPER}', AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"
    )
    return con
