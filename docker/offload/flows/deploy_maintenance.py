#!/usr/bin/env python3
"""
K2 Market Data Platform - Maintenance Flow Deployment Script
Purpose: Deploy iceberg_maintenance_main to Prefect with a daily 02:00 UTC schedule
Version: v1.0 (Prefect 3.x API)
Last Updated: 2026-02-18
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from iceberg_maintenance_flow import iceberg_maintenance_main

os.environ.setdefault("PREFECT_API_URL", "http://localhost:4200/api")


def deploy_maintenance_schedule() -> None:
    print("=" * 70)
    print("K2 ICEBERG MAINTENANCE - DEPLOYMENT (Prefect 3.x)")
    print("=" * 70)
    print()
    print(f"Prefect API URL : {os.environ['PREFECT_API_URL']}")
    print(f"Deployment name : iceberg-maintenance-daily")
    print(f"Schedule        : 0 2 * * * (02:00 UTC daily)")
    print(f"Work pool       : iceberg-offload  (shared with offload flow)")
    print()

    deployment_id = iceberg_maintenance_main.from_source(
        source="/opt/prefect/flows",
        entrypoint="iceberg_maintenance_flow.py:iceberg_maintenance_main",
    ).deploy(
        name="iceberg-maintenance-daily",
        work_pool_name="iceberg-offload",
        cron="0 2 * * *",          # 02:00 UTC daily
        tags=["iceberg", "maintenance", "production", "phase-5"],
        description=(
            "Daily Iceberg maintenance: compact → expire → audit. "
            "Runs at 02:00 UTC. Raises on audit failures."
        ),
        version="1.0.0",
        parameters={
            "target_file_size_mb": 128,
            "snapshot_max_age_hours": 168,
            "snapshot_retain_last": 3,
            "audit_window_hours": 24,
        },
        paused=False,
    )

    print("✅ Deployment created successfully!")
    print(f"   Deployment ID : {deployment_id}")
    print()
    print("📋 Next steps:")
    print("   1. Verify in Prefect UI: http://localhost:4200")
    print("   2. Run a test cycle manually:")
    print("      prefect deployment run 'iceberg-maintenance-main/iceberg-maintenance-daily'")
    print("   3. Check maintenance_audit_log in PostgreSQL:")
    print("      SELECT * FROM maintenance_audit_log ORDER BY run_timestamp DESC LIMIT 10;")
    print()
    print("=" * 70)


if __name__ == "__main__":
    try:
        deploy_maintenance_schedule()
    except Exception as exc:
        print(f"❌ Deployment failed: {exc}")
        sys.exit(1)
