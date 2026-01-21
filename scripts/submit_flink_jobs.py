#!/usr/bin/env python3
"""
Flink Bronze Job Submission via SQL Client
Best Practice: Use SQL client with persistent session for streaming jobs
"""

import subprocess
import time
import sys

def submit_job(exchange: str, setup_sql: str, job_sql: str):
    """Submit Flink SQL job using SQL client in persistent mode"""
    print(f"\n{'='*60}")
    print(f"Submitting {exchange} Bronze Job")
    print(f"{'='*60}")

    # Step 1: Create tables
    print(f"Step 1: Creating {exchange} table definitions...")
    result = subprocess.run(
        ["/opt/flink/bin/sql-client.sh", "embedded", "-f", setup_sql],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"✗ Failed to create tables for {exchange}")
        print(result.stderr)
        return False

    print(f"✓ Tables created for {exchange}")

    # Step 2: Submit streaming job using nohup to keep it running
    print(f"Step 2: Submitting {exchange} streaming INSERT job...")
    combined_sql = f"/tmp/{exchange}_combined.sql"

    # Create combined SQL file
    with open(setup_sql, 'r') as f:
        setup_content = f.read()
    with open(job_sql, 'r') as f:
        job_content = f.read()

    with open(combined_sql, 'w') as f:
        f.write(setup_content)
        f.write("\n\n")
        f.write(job_content)

    # Submit job in background using nohup
    subprocess.Popen(
        f"nohup /opt/flink/bin/sql-client.sh embedded -f {combined_sql} > /tmp/{exchange}_job.log 2>&1 &",
        shell=True
    )

    print(f"✓ {exchange} job submitted (running in background)")
    return True

def main():
    print("Waiting for Flink cluster...")
    time.sleep(30)

    # Submit Binance job
    success_binance = submit_job(
        exchange="binance",
        setup_sql="/opt/flink/sql/bronze_binance_setup.sql",
        job_sql="/opt/flink/sql/bronze_binance_job.sql"
    )

    # Submit Kraken job
    success_kraken = submit_job(
        exchange="kraken",
        setup_sql="/opt/flink/sql/bronze_kraken_setup.sql",
        job_sql="/opt/flink/sql/bronze_kraken_job.sql"
    )

    if success_binance and success_kraken:
        print(f"\n{'='*60}")
        print("✓ All jobs submitted successfully")
        print(f"{'='*60}\n")
        print("Monitor jobs at: http://localhost:8082")

        # Keep container alive
        print("\nKeeping container alive to maintain SQL client sessions...")
        subprocess.run(["tail", "-f", "/dev/null"])
    else:
        print("\n✗ Some jobs failed to submit")
        sys.exit(1)

if __name__ == "__main__":
    main()
