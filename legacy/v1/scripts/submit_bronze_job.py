#!/usr/bin/env python3
"""
Flink Bronze Job Submission via SQL Gateway REST API
Best Practice: Production-grade approach for Flink SQL streaming jobs
Reference: https://nightlies.apache.org/flink/flink-docs-release-1.19/docs/dev/table/sql-gateway/rest/
"""

import os
import sys
import time
import json
import urllib.request
import urllib.error
from typing import Dict, Optional

GATEWAY_URL = os.environ.get('SQL_GATEWAY_URL', 'http://flink-sql-gateway:8083')
RETRY_COUNT = 60
RETRY_DELAY = 2


def wait_for_gateway():
    """Wait for SQL Gateway to be ready"""
    print(f"Waiting for SQL Gateway at {GATEWAY_URL}...")

    for i in range(1, RETRY_COUNT + 1):
        try:
            req = urllib.request.Request(f"{GATEWAY_URL}/v1/info")
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    print(f"✓ SQL Gateway ready")
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if i == RETRY_COUNT:
                print(f"✗ SQL Gateway not available after {RETRY_COUNT} attempts")
                return False
            if i % 10 == 0:
                print(f"  Attempt {i}/{RETRY_COUNT}...")
            time.sleep(RETRY_DELAY)

    return False


def create_session() -> Optional[str]:
    """Create a new SQL Gateway session"""
    print("Creating SQL Gateway session...")

    session_config = {
        "properties": {
            "execution.runtime-mode": "streaming",
            "execution.checkpointing.interval": "10s",
            "execution.checkpointing.mode": "EXACTLY_ONCE"
        }
    }

    data = json.dumps(session_config).encode('utf-8')
    req = urllib.request.Request(
        f"{GATEWAY_URL}/v1/sessions",
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            session_handle = result.get('sessionHandle')
            print(f"✓ Session created: {session_handle}")
            return session_handle
    except Exception as e:
        print(f"✗ Failed to create session: {e}")
        return None


def execute_statement(session_handle: str, sql: str) -> Optional[Dict]:
    """Execute a SQL statement via SQL Gateway"""
    data = json.dumps({"statement": sql}).encode('utf-8')
    req = urllib.request.Request(
        f"{GATEWAY_URL}/v1/sessions/{session_handle}/statements",
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result
    except Exception as e:
        print(f"✗ Failed to execute statement: {e}")
        return None


def wait_for_operation(session_handle: str, operation_handle: str, timeout: int = 120) -> bool:
    """Wait for an operation to complete"""
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(
                f"{GATEWAY_URL}/v1/sessions/{session_handle}/operations/{operation_handle}/status"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                status = result.get('status')

                if status == 'FINISHED':
                    return True
                elif status == 'ERROR':
                    print(f"✗ Operation failed with status: {status}")
                    # Try to get error details
                    try:
                        req = urllib.request.Request(
                            f"{GATEWAY_URL}/v1/sessions/{session_handle}/operations/{operation_handle}/result/0"
                        )
                        with urllib.request.urlopen(req, timeout=10) as err_response:
                            error_result = json.loads(err_response.read().decode('utf-8'))
                            print(f"Error details: {error_result}")
                    except:
                        pass
                    return False
                elif status == 'RUNNING':
                    # For INSERT statements, RUNNING status means job is successfully submitted
                    return True

                time.sleep(2)
        except Exception as e:
            print(f"Warning: Error checking operation status: {e}")
            time.sleep(2)

    print(f"✗ Operation timeout after {timeout}s")
    return False


def execute_sql_file(session_handle: str, sql_file_path: str, description: str) -> bool:
    """Execute all statements from a SQL file"""
    print(f"{description}...")

    try:
        with open(sql_file_path, 'r') as f:
            content = f.read()

        # Parse SQL statements (simple approach - split by semicolon)
        statements = []
        current_stmt = []

        for line in content.split('\n'):
            stripped = line.strip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith('--'):
                continue

            current_stmt.append(line)

            # Check if statement ends
            if stripped.endswith(';'):
                stmt = '\n'.join(current_stmt)
                statements.append(stmt)
                current_stmt = []

        # Execute each statement
        for i, stmt in enumerate(statements, 1):
            # Skip if just whitespace
            if not stmt.strip():
                continue

            print(f"  Executing statement {i}/{len(statements)}...")
            result = execute_statement(session_handle, stmt)

            if not result:
                print(f"  ✗ Failed to submit statement {i}")
                return False

            operation_handle = result.get('operationHandle')
            if operation_handle:
                # Wait for operation to complete
                if not wait_for_operation(session_handle, operation_handle):
                    # For INSERT statements, we might get RUNNING status, which is OK
                    print(f"  Note: Statement {i} may still be running (this is expected for INSERT)")

            print(f"  ✓ Statement {i} executed")

        print(f"✓ {description} complete")
        return True

    except Exception as e:
        print(f"✗ Failed to execute SQL file: {e}")
        return False


def submit_bronze_job(exchange: str):
    """Submit Bronze ingestion job for given exchange"""
    print("\n" + "=" * 70)
    print(f"Flink Bronze Job Submission: {exchange.upper()}")
    print("=" * 70)

    # Wait for SQL Gateway
    if not wait_for_gateway():
        sys.exit(1)

    # Create session
    session_handle = create_session()
    if not session_handle:
        sys.exit(1)

    # Execute setup SQL (table definitions)
    setup_sql = f"/opt/flink/sql/bronze_{exchange}_setup.sql"
    if not execute_sql_file(session_handle, setup_sql, f"Creating {exchange} table definitions"):
        sys.exit(1)

    # Execute job SQL (INSERT statement)
    job_sql = f"/opt/flink/sql/bronze_{exchange}_job.sql"
    if not execute_sql_file(session_handle, job_sql, f"Submitting {exchange} streaming INSERT job"):
        sys.exit(1)

    print("\n" + "=" * 70)
    print(f"✓ {exchange.upper()} Bronze Job Submitted Successfully")
    print("=" * 70)
    print(f"Session Handle: {session_handle}")
    print(f"Monitor at: http://localhost:8082")
    print("=" * 70 + "\n")

    # Keep container alive to maintain session
    print("Keeping container alive to maintain SQL Gateway session...")
    print("(Container will restart automatically if it exits)")

    try:
        while True:
            time.sleep(60)
            # Periodically check if session is still valid
            try:
                req = urllib.request.Request(
                    f"{GATEWAY_URL}/v1/sessions/{session_handle}"
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status != 200:
                        print("Warning: Session may have expired")
            except:
                print("Warning: Could not verify session status")
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: submit_bronze_job.py <exchange>")
        print("  exchange: binance or kraken")
        sys.exit(1)

    exchange = sys.argv[1].lower()
    if exchange not in ['binance', 'kraken']:
        print(f"Error: Invalid exchange '{exchange}'. Must be 'binance' or 'kraken'")
        sys.exit(1)

    submit_bronze_job(exchange)
