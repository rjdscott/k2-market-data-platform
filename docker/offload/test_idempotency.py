#!/usr/bin/env python3
"""
Idempotency & Duplicate Run Testing (Safe Failure Recovery Tests)

Tests exactly-once semantics without destructive operations:
1. Duplicate run prevention (same offload twice)
2. Late-arriving data handling (watermark behavior)
3. Incremental loading validation

These tests are SAFE to run on a running system.
Destructive tests (killing containers) are documented in manual procedures.

Author: Staff Data Engineer
Date: 2026-02-12
Phase: 5 (Cold Tier Restructure), Priority: 3
"""

import os
import sys
import time
import subprocess
import psycopg2
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

# Database connection details
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_DB = os.getenv("POSTGRES_DB", "prefect")
POSTGRES_USER = os.getenv("POSTGRES_USER", "prefect")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "prefect")


class IdempotencyTester:
    """Test framework for safe failure recovery scenarios"""

    def __init__(self):
        self.results = []
        self.test_table = "bronze_trades_binance"
        self.iceberg_table = "demo.cold.bronze_trades_binance"

    def log(self, message: str, level: str = "INFO"):
        """Log test progress"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")

    def get_postgres_connection(self):
        """Get PostgreSQL connection"""
        return psycopg2.connect(
            host=POSTGRES_HOST,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )

    def get_clickhouse_row_count(self) -> int:
        """Get row count from ClickHouse"""
        cmd = [
            "docker", "exec", "k2-clickhouse",
            "clickhouse-client",
            "--query", f"SELECT COUNT(*) FROM k2.{self.test_table}"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return int(result.stdout.strip())

    def get_iceberg_snapshot_count(self) -> int:
        """Get number of snapshots in Iceberg table"""
        cmd = [
            "docker", "exec", "k2-spark-iceberg",
            "spark-sql",
            "--conf", "spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog",
            "--conf", "spark.sql.catalog.demo.type=hadoop",
            "--conf", "spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse",
            "-e", f"SELECT COUNT(*) FROM {self.iceberg_table}.snapshots"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        # Parse output
        for line in result.stdout.split('\n'):
            if line.strip().isdigit():
                return int(line.strip())
        return 0

    def get_watermark(self) -> Optional[Tuple[datetime, int]]:
        """Get current watermark"""
        with self.get_postgres_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT max_timestamp, max_sequence_number
                    FROM iceberg_offload_watermarks
                    WHERE table_name = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (self.test_table,))
                result = cur.fetchone()
                return result if result else (None, None)

    def insert_test_data(self, count: int, sequence_start: int = None) -> None:
        """Insert test data into ClickHouse"""
        self.log(f"Inserting {count} test rows...")

        if sequence_start is None:
            # Use current max + 1
            max_seq_query = f"SELECT MAX(sequence_number) FROM k2.{self.test_table}"
            cmd = ["docker", "exec", "k2-clickhouse", "clickhouse-client", "--query", max_seq_query]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            sequence_start = int(result.stdout.strip()) + 1 if result.stdout.strip().isdigit() else 1000000

        cmd = [
            "docker", "exec", "k2-clickhouse",
            "clickhouse-client",
            "--query", f"""
            INSERT INTO k2.{self.test_table}
            SELECT
                now() - INTERVAL (number % 60) SECOND as exchange_timestamp,
                {sequence_start} + number as sequence_number,
                'binance' as exchange,
                'BTCUSDT' as symbol,
                number % 2 = 0 as is_buyer_maker,
                45000.0 + (number % 1000) as price,
                0.01 * (number % 50) as quantity,
                'test-' || toString({sequence_start} + number) as trade_id,
                now() as inserted_at
            FROM system.numbers
            LIMIT {count}
            """
        ]

        subprocess.run(cmd, check=True)
        self.log(f"Inserted {count} rows starting at sequence {sequence_start}")

    def insert_late_data(self, timestamp: datetime, count: int) -> None:
        """Insert late-arriving data (before watermark)"""
        self.log(f"Inserting {count} late-arriving rows...")

        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        cmd = [
            "docker", "exec", "k2-clickhouse",
            "clickhouse-client",
            "--query", f"""
            INSERT INTO k2.{self.test_table}
            SELECT
                '{ts_str}'::DateTime as exchange_timestamp,
                8888000000 + number as sequence_number,
                'binance' as exchange,
                'BTCUSDT' as symbol,
                true as is_buyer_maker,
                44000.0 as price,
                0.05 as quantity,
                'late-' || toString(number) as trade_id,
                now() as inserted_at
            FROM system.numbers
            LIMIT {count}
            """
        ]

        subprocess.run(cmd, check=True)
        self.log(f"Inserted {count} late-arriving rows")

    def run_offload(self) -> Dict:
        """Run offload script"""
        self.log("Starting offload...")
        start_time = time.time()

        cmd = [
            "docker", "exec", "k2-spark-iceberg",
            "python3", "/home/iceberg/offload/offload_generic.py",
            "--clickhouse-table", self.test_table,
            "--iceberg-table", self.iceberg_table
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=180, check=False)
            duration = time.time() - start_time

            # Parse output for row counts
            rows_read = 0
            rows_written = 0
            for line in result.stdout.split('\n'):
                if "Rows read from ClickHouse:" in line:
                    try:
                        rows_read = int(line.split(':')[1].strip())
                    except:
                        pass
                elif "Rows written to Iceberg:" in line:
                    try:
                        rows_written = int(line.split(':')[1].strip())
                    except:
                        pass

            if result.returncode == 0:
                self.log(f"Offload completed: {rows_read} read, {rows_written} written ({duration:.1f}s)", "SUCCESS")
                return {
                    "success": True,
                    "duration": duration,
                    "rows_read": rows_read,
                    "rows_written": rows_written
                }
            else:
                self.log(f"Offload failed: {result.stderr[:200]}", "ERROR")
                return {
                    "success": False,
                    "duration": duration,
                    "error": result.stderr[:500]
                }

        except subprocess.TimeoutExpired:
            self.log("Offload timed out (>180s)", "ERROR")
            return {"success": False, "duration": 180, "error": "Timeout"}
        except Exception as e:
            duration = time.time() - start_time
            self.log(f"Offload exception: {e}", "ERROR")
            return {"success": False, "duration": duration, "error": str(e)}

    # =================================================================
    # Test Scenarios
    # =================================================================

    def test_duplicate_run_prevention(self) -> Dict:
        """
        Test: Duplicate Run Prevention
        Run same offload twice, verify zero duplicates
        """
        self.log("=" * 80)
        self.log("TEST: Duplicate Run Prevention")
        self.log("=" * 80)

        test_result = {
            "name": "Duplicate Run Prevention",
            "status": "UNKNOWN",
            "details": {}
        }

        try:
            # Get starting watermark
            watermark_before = self.get_watermark()
            test_result["details"]["watermark_before"] = str(watermark_before)

            # Insert test data
            self.insert_test_data(100)
            ch_rows = self.get_clickhouse_row_count()
            test_result["details"]["ch_rows"] = ch_rows

            # Run offload first time
            self.log("Running offload (1st time)...")
            result1 = self.run_offload()
            watermark_after_first = self.get_watermark()

            test_result["details"]["first_run"] = {
                "success": result1["success"],
                "rows_written": result1.get("rows_written", 0),
                "watermark": str(watermark_after_first)
            }

            # Run offload second time (should be idempotent - no new data)
            self.log("Running offload (2nd time - should find no new data)...")
            result2 = self.run_offload()
            watermark_after_second = self.get_watermark()

            test_result["details"]["second_run"] = {
                "success": result2["success"],
                "rows_written": result2.get("rows_written", 0),
                "watermark": str(watermark_after_second)
            }

            # Verify idempotency
            no_duplicates = result2.get("rows_written", 0) == 0
            watermark_unchanged = watermark_after_first == watermark_after_second

            test_result["details"]["idempotent"] = no_duplicates and watermark_unchanged

            if no_duplicates and watermark_unchanged and result1["success"] and result2["success"]:
                test_result["status"] = "PASS"
                self.log("✅ Test PASSED: No duplicates, watermark preserved", "SUCCESS")
            else:
                test_result["status"] = "FAIL"
                self.log(f"❌ Test FAILED: Duplicates={not no_duplicates}, WM changed={not watermark_unchanged}", "ERROR")

        except Exception as e:
            test_result["status"] = "ERROR"
            test_result["error"] = str(e)
            self.log(f"❌ Test ERROR: {e}", "ERROR")

        self.results.append(test_result)
        return test_result

    def test_late_arriving_data(self) -> Dict:
        """
        Test: Late-Arriving Data
        Insert old data after offload, verify watermark prevents backfill
        """
        self.log("=" * 80)
        self.log("TEST: Late-Arriving Data Handling")
        self.log("=" * 80)

        test_result = {
            "name": "Late-Arriving Data",
            "status": "UNKNOWN",
            "details": {}
        }

        try:
            # Run initial offload
            self.log("Running initial offload...")
            result1 = self.run_offload()
            watermark1 = self.get_watermark()

            if not watermark1 or watermark1[0] is None:
                test_result["status"] = "SKIP"
                test_result["details"]["reason"] = "No watermark established"
                self.log("⏭️  Test SKIPPED: No watermark to test against", "WARN")
                return test_result

            test_result["details"]["watermark_before_late_data"] = str(watermark1)
            test_result["details"]["first_offload_rows"] = result1.get("rows_written", 0)

            # Insert late-arriving data (1 hour before watermark)
            late_timestamp = watermark1[0] - timedelta(hours=1)
            self.insert_late_data(late_timestamp, count=25)

            # Run offload again
            self.log("Running offload after late data...")
            result2 = self.run_offload()
            watermark2 = self.get_watermark()

            test_result["details"]["watermark_after_late_data"] = str(watermark2)
            test_result["details"]["second_offload_rows"] = result2.get("rows_written", 0)

            # Late data should be ignored (watermark prevents going backwards)
            late_data_ignored = result2.get("rows_written", 0) == 0

            test_result["details"]["late_data_ignored"] = late_data_ignored

            if late_data_ignored and result1["success"] and result2["success"]:
                test_result["status"] = "PASS"
                self.log("✅ Test PASSED: Late data correctly ignored (watermark preserved)", "SUCCESS")
            else:
                test_result["status"] = "FAIL"
                self.log(f"❌ Test FAILED: Late data behavior unexpected", "ERROR")

        except Exception as e:
            test_result["status"] = "ERROR"
            test_result["error"] = str(e)
            self.log(f"❌ Test ERROR: {e}", "ERROR")

        self.results.append(test_result)
        return test_result

    def test_incremental_loading(self) -> Dict:
        """
        Test: Incremental Loading
        Add new data after offload, verify only new data is processed
        """
        self.log("=" * 80)
        self.log("TEST: Incremental Loading")
        self.log("=" * 80)

        test_result = {
            "name": "Incremental Loading",
            "status": "UNKNOWN",
            "details": {}
        }

        try:
            # Initial offload
            self.log("Running initial offload...")
            result1 = self.run_offload()
            watermark1 = self.get_watermark()

            test_result["details"]["initial_rows_written"] = result1.get("rows_written", 0)
            test_result["details"]["watermark_after_initial"] = str(watermark1)

            # Add new data
            self.insert_test_data(50)

            # Second offload (should only process new data)
            self.log("Running incremental offload...")
            result2 = self.run_offload()
            watermark2 = self.get_watermark()

            test_result["details"]["incremental_rows_written"] = result2.get("rows_written", 0)
            test_result["details"]["watermark_after_incremental"] = str(watermark2)

            # Verify only new data processed
            only_new_data = result2.get("rows_written", 0) == 50
            watermark_advanced = watermark2 > watermark1 if watermark1 and watermark2 else False

            test_result["details"]["only_new_data_processed"] = only_new_data
            test_result["details"]["watermark_advanced"] = watermark_advanced

            if only_new_data and watermark_advanced and result1["success"] and result2["success"]:
                test_result["status"] = "PASS"
                self.log("✅ Test PASSED: Only new data processed incrementally", "SUCCESS")
            else:
                test_result["status"] = "FAIL"
                self.log(f"❌ Test FAILED: Incremental behavior incorrect", "ERROR")

        except Exception as e:
            test_result["status"] = "ERROR"
            test_result["error"] = str(e)
            self.log(f"❌ Test ERROR: {e}", "ERROR")

        self.results.append(test_result)
        return test_result

    def run_all_tests(self):
        """Run all safe failure recovery tests"""
        self.log("=" * 80)
        self.log("IDEMPOTENCY & DUPLICATE RUN TEST SUITE - START")
        self.log("=" * 80)

        start_time = time.time()

        # Run tests
        self.test_duplicate_run_prevention()
        self.test_late_arriving_data()
        self.test_incremental_loading()

        total_duration = time.time() - start_time

        # Print summary
        self.log("=" * 80)
        self.log("TEST SUITE SUMMARY")
        self.log("=" * 80)

        passed = sum(1 for r in self.results if r["status"] == "PASS")
        failed = sum(1 for r in self.results if r["status"] == "FAIL")
        errors = sum(1 for r in self.results if r["status"] == "ERROR")
        skipped = sum(1 for r in self.results if r["status"] == "SKIP")

        self.log(f"Total Tests: {len(self.results)}")
        self.log(f"✅ Passed: {passed}")
        self.log(f"❌ Failed: {failed}")
        self.log(f"⚠️  Errors: {errors}")
        self.log(f"⏭️  Skipped: {skipped}")
        self.log(f"Duration: {total_duration:.1f}s")

        for result in self.results:
            self.log(f"\n{result['name']}: {result['status']}")
            if "error" in result:
                self.log(f"  Error: {result['error']}")

        return {
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "duration": total_duration,
            "results": self.results
        }


if __name__ == "__main__":
    tester = IdempotencyTester()
    summary = tester.run_all_tests()

    # Exit with non-zero if any tests failed
    sys.exit(0 if summary["failed"] == 0 and summary["errors"] == 0 else 1)
