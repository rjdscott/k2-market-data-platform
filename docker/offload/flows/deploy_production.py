#!/usr/bin/env python3
"""
K2 Market Data Platform - Production Deployment Script
Purpose: Deploy Iceberg offload flow to Prefect server with 15-minute schedule
Version: v3.0 (Prefect 3.x API)
Last Updated: 2026-02-14
"""

import os
import sys
from pathlib import Path

# Ensure Prefect can find the flow file
sys.path.insert(0, str(Path(__file__).parent))

from iceberg_offload_flow import iceberg_offload_main

# Set Prefect API URL (points to Prefect server)
os.environ["PREFECT_API_URL"] = os.getenv("PREFECT_API_URL", "http://localhost:4200/api")

def deploy_production_schedule():
    """
    Deploy the Iceberg offload flow to Prefect server with production schedule.

    This creates a deployment that:
    - Runs every 15 minutes (*/15 * * * *)
    - Uses work pool 'iceberg-offload' (picked up by running workers)
    - Tagged for production monitoring
    """
    print("=" * 80)
    print("K2 ICEBERG OFFLOAD - PRODUCTION DEPLOYMENT (Prefect 3.x)")
    print("=" * 80)
    print()
    print(f"Prefect API URL: {os.environ['PREFECT_API_URL']}")
    print(f"Deployment Name: iceberg-offload-15min")
    print(f"Schedule: */15 * * * * (every 15 minutes)")
    print(f"Work Pool: iceberg-offload")
    print()

    # Deploy the flow with 15-minute cron schedule using Prefect 3.x API
    # Code is mounted at /opt/prefect/flows in the worker container
    deployment_id = iceberg_offload_main.from_source(
        source="/opt/prefect/flows",  # Path in worker container where code is mounted
        entrypoint="iceberg_offload_flow.py:iceberg_offload_main",
    ).deploy(
        name="iceberg-offload-15min",
        work_pool_name="iceberg-offload",  # Work pool for Prefect 3.x workers
        cron="*/15 * * * *",  # Every 15 minutes
        tags=["iceberg", "offload", "production", "phase-5"],
        description="ClickHouse → Iceberg offload pipeline (Bronze layer, runs every 15 minutes)",
        version="3.0.0",
        parameters={},
        paused=False,  # Start immediately
    )

    print("✅ Deployment created successfully!")
    print(f"   Deployment ID: {deployment_id}")
    print()
    print("📋 Next steps:")
    print("   1. Verify deployment in Prefect UI: http://localhost:4200")
    print("   2. Check that Prefect worker is running:")
    print("      docker ps | grep prefect-worker")
    print("   3. Monitor first scheduled run (within 15 minutes)")
    print("   4. View logs in Prefect UI under 'Flow Runs'")
    print()
    print("🔧 Useful commands:")
    print("   - List deployments: prefect deployment ls")
    print("   - Run manually: prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'")
    print("   - Pause schedule: prefect deployment set-schedule iceberg-offload-main/iceberg-offload-15min --paused")
    print("   - Resume schedule: prefect deployment set-schedule iceberg-offload-main/iceberg-offload-15min --active")
    print()
    print("=" * 80)

if __name__ == "__main__":
    try:
        deploy_production_schedule()
    except Exception as e:
        print(f"❌ Deployment failed: {e}")
        sys.exit(1)
