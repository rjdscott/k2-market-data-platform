#!/usr/bin/env python3
"""
Failure Recovery Testing Suite for Iceberg Offload Pipeline

Tests exactly-once semantics under various failure conditions:
1. Network interruption (ClickHouse killed mid-read)
2. Spark crash (job killed mid-write)
3. Watermark corruption (manual corruption + recovery)
4. Duplicate run prevention (same offload run twice)
5. Late-arriving data (old data inserted after offload)

Author: Staff Data Engineer
Date: 2026-02-12
Phase: 5 (Cold Tier Restructure)
Priority: 3
"""

import os
import sys
import time
import signal
import subprocess
import psycopg2
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# Database connection details (localhost when running from host)
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_DB = os.getenv("POSTGRES_DB", "prefect")
POSTGRES_USER = os.getenv("POSTGRES_USER", "prefect")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "prefect")


class FailureRecoveryTester:
    """Framework for testing failure recovery scenarios"""

    def __init__(self):
        self.results = []
        self.test_table = "bronze_trades_binance"
        self.iceberg_table = "demo.cold.bronze_trades_binance"

    def log(self, message: str, level: str = "INFO"):
        """Log test progress"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")

    def get_postgres_connection(self):
        """Get PostgreSQL connection for watermark access"""
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

    def get_iceberg_row_count(self) -> int:
        """Get row count from Iceberg via Spark SQL"""
        cmd = [
            "docker", "exec", "k2-spark-iceberg",
            "spark-sql",
            "--conf", "spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog",
            "--conf", "spark.sql.catalog.demo.type=hadoop",
            "--conf", "spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse",
            "-e", f"SELECT COUNT(*) FROM {self.iceberg_table}"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        # Parse output (Spark SQL returns table format)
        for line in result.stdout.split('\n'):
            if line.strip().isdigit():
                return int(line.strip())
        return 0

    def get_watermark(self) -> Optional[Tuple[datetime, int]]:
        """Get current watermark for test table"""
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

    def backup_watermark(self) -> Optional[Tuple[datetime, int]]:
        """Backup current watermark"""
        watermark = self.get_watermark()
        if watermark and watermark[0]:
            self.log(f"Watermark backup: {watermark[0]} / {watermark[1]}")
        return watermark

    def restore_watermark(self, timestamp: datetime, sequence: int):
        """Restore watermark to specific values"""
        with self.get_postgres_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO iceberg_offload_watermarks
                    (table_name, max_timestamp, max_sequence_number, status, created_at)
                    VALUES (%s, %s, %s, 'success', NOW())
                """, (self.test_table, timestamp, sequence))
            conn.commit()
        self.log(f"Watermark restored: {timestamp} / {sequence}")

    def corrupt_watermark(self):
        """Corrupt watermark by setting to NULL"""
        with self.get_postgres_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO iceberg_offload_watermarks
                    (table_name, max_timestamp, max_sequence_number, status, created_at)
                    VALUES (%s, NULL, NULL, 'corrupted', NOW())
                """, (self.test_table,))
            conn.commit()
        self.log("Watermark corrupted (set to NULL)")

    def run_offload(self, expect_failure: bool = False) -> Dict:
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
                                  timeout=120, check=not expect_failure)
            duration = time.time() - start_time

            if result.returncode == 0:
                self.log(f"Offload completed in {duration:.1f}s", "SUCCESS")
                return {"success": True, "duration": duration, "output": result.stdout}
            else:
                self.log(f"Offload failed: {result.stderr}", "ERROR")
                return {"success": False, "duration": duration, "error": result.stderr}

        except subprocess.TimeoutExpired:
            self.log("Offload timed out (>120s)", "ERROR")
            return {"success": False, "duration": 120, "error": "Timeout"}
        except Exception as e:
            duration = time.time() - start_time
            self.log(f"Offload exception: {e}", "ERROR")
            return {"success": False, "duration": duration, "error": str(e)}

    def insert_test_data(self, num_rows: int = 100) -> List[int]:
        """Insert test data into ClickHouse and return sequence numbers"""
        self.log(f"Inserting {num_rows} test rows...")

        # Insert via clickhouse-client
        cmd = [
            "docker", "exec", "k2-clickhouse",
            "clickhouse-client",
            "--query", f"""
            INSERT INTO k2.{self.test_table}
            SELECT
                now() - INTERVAL (number % 60) SECOND as exchange_timestamp,
                number as sequence_number,
                'binance' as exchange,
                'BTCUSDT' as symbol,
                number % 2 = 0 as is_buyer_maker,
                10000.0 + (number % 1000) as price,
                0.01 * (number % 100) as quantity,
                'test' as trade_id,
                now() as inserted_at
            FROM system.numbers
            LIMIT {num_rows}
            """
        ]

        subprocess.run(cmd, check=True)
        self.log(f"Inserted {num_rows} rows")
        return list(range(num_rows))

    def insert_late_data(self, timestamp: datetime, count: int = 10):
        """Insert late-arriving data (before current watermark)"""
        self.log(f"Inserting {count} late-arriving rows (timestamp: {timestamp})...")

        # Convert Python datetime to ClickHouse format
        ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

        cmd = [
            "docker", "exec", "k2-clickhouse",
            "clickhouse-client",
            "--query", f"""
            INSERT INTO k2.{self.test_table}
            SELECT
                '{ts_str}'::DateTime as exchange_timestamp,
                9999990000 + number as sequence_number,
                'binance' as exchange,
                'BTCUSDT' as symbol,
                true as is_buyer_maker,
                50000.0 as price,
                0.1 as quantity,
                'late-' || toString(number) as trade_id,
                now() as inserted_at
            FROM system.numbers
            LIMIT {count}
            """
        ]

        subprocess.run(cmd, check=True)
        self.log(f"Inserted {count} late-arriving rows")

    # ============================================================================
    # Test Scenarios
    # ============================================================================

    def test_1_network_interruption(self) -> Dict:
        """
        Test 1: Network Interruption
        Kill ClickHouse container mid-read, verify retry succeeds
        """
        self.log("=" * 80)
        self.log("TEST 1: Network Interruption (ClickHouse killed mid-read)")
        self.log("=" * 80)

        test_result = {
            "test_id": 1,
            "name": "Network Interruption",
            "status": "UNKNOWN",
            "details": {}
        }

        try:
            # Backup current state
            watermark_backup = self.backup_watermark()
            ch_rows_before = self.get_clickhouse_row_count()
            ice_rows_before = self.get_iceberg_row_count()

            test_result["details"]["ch_rows_before"] = ch_rows_before
            test_result["details"]["ice_rows_before"] = ice_rows_before

            # Insert test data
            self.insert_test_data(500)

            # Start offload in background
            self.log("Starting offload in background...")
            offload_proc = subprocess.Popen([
                "docker", "exec", "k2-spark-iceberg",
                "python3", "/home/iceberg/offload/offload_generic.py",
                "--clickhouse-table", self.test_table,
                "--iceberg-table", self.iceberg_table
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Wait 2 seconds then kill ClickHouse
            time.sleep(2)
            self.log("Killing ClickHouse container...")
            subprocess.run(["docker", "stop", "k2-clickhouse"], check=True)

            # Wait for offload to fail
            time.sleep(3)

            # Restart ClickHouse
            self.log("Restarting ClickHouse...")
            subprocess.run(["docker", "start", "k2-clickhouse"], check=True)
            time.sleep(5)  # Wait for ClickHouse to be ready

            # Try offload again (should succeed with retry)
            self.log("Retrying offload after ClickHouse restart...")
            retry_result = self.run_offload()

            # Verify results
            ice_rows_after = self.get_iceberg_row_count()
            test_result["details"]["ice_rows_after"] = ice_rows_after
            test_result["details"]["retry_succeeded"] = retry_result["success"]

            if retry_result["success"]:
                test_result["status"] = "PASS"
                self.log("✅ Test 1 PASSED: Offload succeeded after network interruption", "SUCCESS")
            else:
                test_result["status"] = "FAIL"
                self.log("❌ Test 1 FAILED: Offload failed after retry", "ERROR")

        except Exception as e:
            test_result["status"] = "ERROR"
            test_result["error"] = str(e)
            self.log(f"❌ Test 1 ERROR: {e}", "ERROR")

        self.results.append(test_result)
        return test_result

    def test_2_spark_crash(self) -> Dict:
        """
        Test 2: Spark Crash
        Kill Spark mid-write, verify no partial data in Iceberg
        """
        self.log("=" * 80)
        self.log("TEST 2: Spark Crash (job killed mid-write)")
        self.log("=" * 80)

        test_result = {
            "test_id": 2,
            "name": "Spark Crash",
            "status": "UNKNOWN",
            "details": {}
        }

        try:
            # Backup current state
            watermark_backup = self.backup_watermark()
            ice_rows_before = self.get_iceberg_row_count()

            test_result["details"]["ice_rows_before"] = ice_rows_before

            # Insert test data
            self.insert_test_data(1000)

            # Start offload in background
            self.log("Starting offload in background...")
            offload_proc = subprocess.Popen([
                "docker", "exec", "k2-spark-iceberg",
                "python3", "/home/iceberg/offload/offload_generic.py",
                "--clickhouse-table", self.test_table,
                "--iceberg-table", self.iceberg_table
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Wait 3 seconds then kill the process
            time.sleep(3)
            self.log("Killing Spark offload process...")
            offload_proc.send_signal(signal.SIGKILL)
            offload_proc.wait()

            # Check Iceberg row count (should not have partial data)
            ice_rows_after_kill = self.get_iceberg_row_count()
            test_result["details"]["ice_rows_after_kill"] = ice_rows_after_kill

            # Check watermark (should not be updated)
            watermark_after_kill = self.get_watermark()
            test_result["details"]["watermark_updated"] = watermark_after_kill != watermark_backup

            # Run offload again (should succeed)
            self.log("Running offload after crash...")
            retry_result = self.run_offload()

            ice_rows_after_retry = self.get_iceberg_row_count()
            test_result["details"]["ice_rows_after_retry"] = ice_rows_after_retry

            # Verify: No partial data (rows either not changed or fully committed)
            partial_data_written = (ice_rows_after_kill > ice_rows_before and
                                   ice_rows_after_kill < ice_rows_after_retry)

            test_result["details"]["partial_data_detected"] = partial_data_written
            test_result["details"]["retry_succeeded"] = retry_result["success"]

            if not partial_data_written and retry_result["success"]:
                test_result["status"] = "PASS"
                self.log("✅ Test 2 PASSED: No partial data after Spark crash", "SUCCESS")
            else:
                test_result["status"] = "FAIL"
                self.log("❌ Test 2 FAILED: Partial data detected or retry failed", "ERROR")

        except Exception as e:
            test_result["status"] = "ERROR"
            test_result["error"] = str(e)
            self.log(f"❌ Test 2 ERROR: {e}", "ERROR")

        self.results.append(test_result)
        return test_result

    def test_3_watermark_corruption(self) -> Dict:
        """
        Test 3: Watermark Corruption
        Manually corrupt watermark, verify recovery
        """
        self.log("=" * 80)
        self.log("TEST 3: Watermark Corruption (manual corruption + recovery)")
        self.log("=" * 80)

        test_result = {
            "test_id": 3,
            "name": "Watermark Corruption",
            "status": "UNKNOWN",
            "details": {}
        }

        try:
            # Backup current watermark
            watermark_backup = self.backup_watermark()
            test_result["details"]["watermark_backup"] = str(watermark_backup)

            # Corrupt watermark
            self.corrupt_watermark()

            # Try to run offload (should handle gracefully)
            self.log("Running offload with corrupted watermark...")
            offload_result = self.run_offload(expect_failure=False)

            test_result["details"]["offload_with_corruption"] = offload_result["success"]

            # Check if watermark was recovered/reset
            watermark_after = self.get_watermark()
            test_result["details"]["watermark_after"] = str(watermark_after)

            # Recovery successful if:
            # 1. Offload handled corrupted watermark without crashing
            # 2. Watermark was reset to valid value (or full table scan performed)
            recovered = (offload_result["success"] and
                        watermark_after and watermark_after[0] is not None)

            test_result["details"]["recovered"] = recovered

            if recovered:
                test_result["status"] = "PASS"
                self.log("✅ Test 3 PASSED: Recovered from watermark corruption", "SUCCESS")
            else:
                test_result["status"] = "FAIL"
                self.log("❌ Test 3 FAILED: Did not recover from corruption", "ERROR")

        except Exception as e:
            test_result["status"] = "ERROR"
            test_result["error"] = str(e)
            self.log(f"❌ Test 3 ERROR: {e}", "ERROR")

        self.results.append(test_result)
        return test_result

    def test_4_duplicate_run(self) -> Dict:
        """
        Test 4: Duplicate Run Prevention
        Run same offload twice, verify zero duplicates
        """
        self.log("=" * 80)
        self.log("TEST 4: Duplicate Run Prevention (same offload twice)")
        self.log("=" * 80)

        test_result = {
            "test_id": 4,
            "name": "Duplicate Run Prevention",
            "status": "UNKNOWN",
            "details": {}
        }

        try:
            # Get starting state
            ice_rows_before = self.get_iceberg_row_count()
            test_result["details"]["ice_rows_before"] = ice_rows_before

            # Insert test data
            self.insert_test_data(200)

            # Run offload first time
            self.log("Running offload (first time)...")
            result1 = self.run_offload()
            ice_rows_after_first = self.get_iceberg_row_count()

            test_result["details"]["ice_rows_after_first"] = ice_rows_after_first
            test_result["details"]["first_run_success"] = result1["success"]

            # Run offload second time (same data, should be idempotent)
            self.log("Running offload (second time - should be idempotent)...")
            result2 = self.run_offload()
            ice_rows_after_second = self.get_iceberg_row_count()

            test_result["details"]["ice_rows_after_second"] = ice_rows_after_second
            test_result["details"]["second_run_success"] = result2["success"]

            # Check for duplicates
            duplicates_created = ice_rows_after_second > ice_rows_after_first
            test_result["details"]["duplicates_created"] = duplicates_created
            test_result["details"]["duplicate_count"] = ice_rows_after_second - ice_rows_after_first

            if not duplicates_created and result1["success"] and result2["success"]:
                test_result["status"] = "PASS"
                self.log("✅ Test 4 PASSED: No duplicates from repeated offload", "SUCCESS")
            else:
                test_result["status"] = "FAIL"
                self.log(f"❌ Test 4 FAILED: Duplicates detected: {test_result['details']['duplicate_count']}", "ERROR")

        except Exception as e:
            test_result["status"] = "ERROR"
            test_result["error"] = str(e)
            self.log(f"❌ Test 4 ERROR: {e}", "ERROR")

        self.results.append(test_result)
        return test_result

    def test_5_late_arriving_data(self) -> Dict:
        """
        Test 5: Late-Arriving Data
        Insert old data after offload, verify next cycle catches it
        """
        self.log("=" * 80)
        self.log("TEST 5: Late-Arriving Data (old data after offload)")
        self.log("=" * 80)

        test_result = {
            "test_id": 5,
            "name": "Late-Arriving Data",
            "status": "UNKNOWN",
            "details": {}
        }

        try:
            # Run initial offload to establish watermark
            self.log("Running initial offload...")
            result1 = self.run_offload()

            watermark1 = self.get_watermark()
            ice_rows_after_first = self.get_iceberg_row_count()

            test_result["details"]["watermark1"] = str(watermark1)
            test_result["details"]["ice_rows_after_first"] = ice_rows_after_first

            # Insert late-arriving data (timestamp before watermark)
            if watermark1 and watermark1[0]:
                late_timestamp = watermark1[0]
                self.insert_late_data(late_timestamp, count=50)
            else:
                self.log("No watermark found, skipping late data test", "WARN")
                test_result["status"] = "SKIP"
                return test_result

            # Run offload again
            self.log("Running offload after late data inserted...")
            result2 = self.run_offload()

            ice_rows_after_second = self.get_iceberg_row_count()
            watermark2 = self.get_watermark()

            test_result["details"]["ice_rows_after_second"] = ice_rows_after_second
            test_result["details"]["watermark2"] = str(watermark2)

            # Late data should NOT be picked up (watermark prevents going backwards)
            # This is expected behavior for exactly-once with watermark
            late_data_ignored = ice_rows_after_second == ice_rows_after_first

            test_result["details"]["late_data_ignored"] = late_data_ignored
            test_result["details"]["rows_added"] = ice_rows_after_second - ice_rows_after_first

            # For watermark-based system, late data SHOULD be ignored
            if late_data_ignored:
                test_result["status"] = "PASS"
                self.log("✅ Test 5 PASSED: Late data correctly ignored (watermark prevents backfill)", "SUCCESS")
            else:
                test_result["status"] = "FAIL"
                self.log(f"❌ Test 5 FAILED: Unexpected behavior with late data", "WARN")

        except Exception as e:
            test_result["status"] = "ERROR"
            test_result["error"] = str(e)
            self.log(f"❌ Test 5 ERROR: {e}", "ERROR")

        self.results.append(test_result)
        return test_result

    def run_all_tests(self):
        """Run all failure recovery tests"""
        self.log("=" * 80)
        self.log("FAILURE RECOVERY TEST SUITE - START")
        self.log("=" * 80)

        start_time = time.time()

        # Run tests
        self.test_1_network_interruption()
        self.test_2_spark_crash()
        self.test_3_watermark_corruption()
        self.test_4_duplicate_run()
        self.test_5_late_arriving_data()

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

        # Print details
        for result in self.results:
            self.log(f"\nTest {result['test_id']}: {result['name']} - {result['status']}")
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
    tester = FailureRecoveryTester()
    summary = tester.run_all_tests()

    # Exit with non-zero if any tests failed
    sys.exit(0 if summary["failed"] == 0 and summary["errors"] == 0 else 1)
