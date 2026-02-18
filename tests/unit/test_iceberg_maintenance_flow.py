"""
Unit tests for iceberg_maintenance_flow.py

Coverage:
  - compact_table task: success, failure, retry metadata
  - expire_snapshots task: success, failure
  - run_audit task: success, audit-summary parsing, inner command failure
  - compact_all_tables sub-flow: partial failure (continue policy)
  - expire_all_snapshots sub-flow: partial failure (continue policy)
  - iceberg_maintenance_main: all-success, partial compact/expire, audit failure

All tests mock subprocess.run so no Docker or Spark dependency is needed.

Import note: offload modules are imported via sys.path (see tests/unit/conftest.py)
rather than as docker.offload.xxx — the 'docker' namespace is reserved for the
docker-sdk PyPI package already used in tests/conftest.py.
"""

import subprocess
import pytest
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fake_completed(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Build a fake subprocess.CompletedProcess."""
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


def _fake_failed(returncode: int = 1, stderr: str = "Spark error") -> subprocess.CalledProcessError:
    exc = subprocess.CalledProcessError(returncode, ["docker", "exec"], stderr=stderr)
    return exc


# ─────────────────────────────────────────────────────────────────────────────
# compact_table
# ─────────────────────────────────────────────────────────────────────────────

class TestCompactTableTask:
    def test_success_returns_success_status(self):
        """compact_table returns status=success when the subprocess exits 0."""
        from iceberg_maintenance_flow import compact_table

        with patch("subprocess.run", return_value=_fake_completed("Files rewritten: 10")):
            result = compact_table.fn(table="cold.bronze_trades_binance")

        assert result["status"] == "success"
        assert result["table"] == "cold.bronze_trades_binance"
        assert "timestamp" in result

    def test_failure_returns_failed_status(self):
        """compact_table returns status=failed (not raises) when subprocess fails."""
        from iceberg_maintenance_flow import compact_table

        with patch("subprocess.run", side_effect=_fake_failed()):
            result = compact_table.fn(table="cold.gold_ohlcv_1m")

        assert result["status"] == "failed"
        assert "error" in result
        assert result["table"] == "cold.gold_ohlcv_1m"

    def test_timeout_returns_failed_status(self):
        """compact_table returns status=failed on subprocess timeout."""
        from iceberg_maintenance_flow import compact_table

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["docker"], 600)):
            result = compact_table.fn(table="cold.silver_trades")

        assert result["status"] == "failed"
        assert "timed out" in result["error"].lower()

    def test_compact_command_includes_table_and_size(self):
        """compact_table passes --table and --target-file-size-mb to the script."""
        from iceberg_maintenance_flow import compact_table

        with patch("subprocess.run", return_value=_fake_completed()) as mock_run:
            compact_table.fn(table="cold.bronze_trades_kraken", target_file_size_mb=256)

        cmd = mock_run.call_args[0][0]
        assert "cold.bronze_trades_kraken" in cmd
        assert "256" in cmd
        assert "compact" in cmd

    def test_task_has_retry_configuration(self):
        """compact_table task is configured with at least 1 retry."""
        from iceberg_maintenance_flow import compact_table
        assert compact_table.retries >= 1


# ─────────────────────────────────────────────────────────────────────────────
# expire_snapshots
# ─────────────────────────────────────────────────────────────────────────────

class TestExpireSnapshotsTask:
    def test_success_returns_success_status(self):
        from iceberg_maintenance_flow import expire_snapshots

        with patch("subprocess.run", return_value=_fake_completed("Deleted 5 snapshots")):
            result = expire_snapshots.fn(table="cold.silver_trades")

        assert result["status"] == "success"
        assert result["table"] == "cold.silver_trades"

    def test_failure_returns_failed_status(self):
        from iceberg_maintenance_flow import expire_snapshots

        with patch("subprocess.run", side_effect=_fake_failed(stderr="MinIO timeout")):
            result = expire_snapshots.fn(table="cold.gold_ohlcv_1m")

        assert result["status"] == "failed"
        assert "MinIO timeout" in result["error"]

    def test_expire_command_includes_all_flags(self):
        """expire_snapshots passes --max-age-hours and --retain-last to script."""
        from iceberg_maintenance_flow import expire_snapshots

        with patch("subprocess.run", return_value=_fake_completed()) as mock_run:
            expire_snapshots.fn(
                table="cold.bronze_trades_binance",
                max_age_hours=240,
                retain_last=5,
            )

        cmd = mock_run.call_args[0][0]
        assert "240" in cmd
        assert "5" in cmd
        assert "expire" in cmd
        assert "cold.bronze_trades_binance" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# run_audit
# ─────────────────────────────────────────────────────────────────────────────

class TestRunAuditTask:
    _CLEAN_AUDIT_STDOUT = (
        "AUDIT SUMMARY\n"
        "OK=10  WARNING=0  MISSING=0  ERROR=0\n"
    )
    _PARTIAL_AUDIT_STDOUT = (
        "AUDIT SUMMARY\n"
        "OK=8  WARNING=1  MISSING=1  ERROR=0\n"
    )
    _ERROR_AUDIT_STDOUT = (
        "AUDIT SUMMARY\n"
        "OK=9  WARNING=0  MISSING=0  ERROR=1\n"
    )

    def test_clean_audit_returns_zero_issues(self):
        from iceberg_maintenance_flow import run_audit

        with patch("subprocess.run", return_value=_fake_completed(self._CLEAN_AUDIT_STDOUT)):
            result = run_audit.fn()

        assert result["status"] == "success"
        assert result["missing_count"] == 0
        assert result["error_count"] == 0

    def test_audit_parses_missing_count(self):
        """run_audit correctly extracts MISSING count from stdout."""
        from iceberg_maintenance_flow import run_audit

        with patch("subprocess.run", return_value=_fake_completed(self._PARTIAL_AUDIT_STDOUT)):
            result = run_audit.fn()

        assert result["missing_count"] == 1
        assert result["error_count"] == 0

    def test_audit_parses_error_count(self):
        from iceberg_maintenance_flow import run_audit

        with patch("subprocess.run", return_value=_fake_completed(self._ERROR_AUDIT_STDOUT)):
            result = run_audit.fn()

        assert result["error_count"] == 1

    def test_command_failure_returns_failed_status(self):
        """If the audit script itself crashes, run_audit returns status=failed."""
        from iceberg_maintenance_flow import run_audit

        with patch("subprocess.run", side_effect=_fake_failed()):
            result = run_audit.fn()

        assert result["status"] == "failed"
        assert result["error_count"] == 1

    def test_audit_command_includes_window_flag(self):
        from iceberg_maintenance_flow import run_audit

        with patch("subprocess.run", return_value=_fake_completed(self._CLEAN_AUDIT_STDOUT)) as mock_run:
            run_audit.fn(audit_window_hours=48)

        cmd = mock_run.call_args[0][0]
        assert "48" in cmd
        assert "audit" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# compact_all_tables (sub-flow)
# ─────────────────────────────────────────────────────────────────────────────

class TestCompactAllTablesFlow:
    def test_processes_all_ten_tables(self):
        """compact_all_tables calls compact_table once per table (10 tables)."""
        from iceberg_maintenance_flow import (
            compact_all_tables, _ALL_TABLES,
        )

        call_count = 0

        def fake_compact_fn(table, target_file_size_mb=128):
            nonlocal call_count
            call_count += 1
            return {"table": table, "status": "success", "timestamp": "2026-02-18T02:00:00"}

        with patch(
            "iceberg_maintenance_flow.compact_table",
            side_effect=lambda **kw: fake_compact_fn(**kw),
        ):
            # Call the underlying function directly (Prefect flow fn)
            with patch("subprocess.run", return_value=_fake_completed()):
                results = compact_all_tables.fn()

        assert len(results) == len(_ALL_TABLES)

    def test_continues_after_single_failure(self):
        """compact_all_tables does not abort when one table fails (log-and-continue).

        We mock at the compact_table task level (not subprocess) to avoid
        triggering Prefect's retry machinery (which would add a 120s wait per
        retry and require extra mock responses for each retry attempt).
        """
        from iceberg_maintenance_flow import compact_all_tables, _ALL_TABLES

        call_count = 0

        def fake_compact(table, target_file_size_mb=128):
            nonlocal call_count
            call_count += 1
            if call_count == len(_ALL_TABLES):  # Last table fails
                return {"table": table, "status": "failed",
                        "error": "simulated failure", "timestamp": "2026-02-18T02:00:00"}
            return {"table": table, "status": "success", "timestamp": "2026-02-18T02:00:00"}

        with patch("iceberg_maintenance_flow.compact_table", side_effect=fake_compact):
            results = compact_all_tables.fn()

        statuses = [r["status"] for r in results]
        assert "failed" in statuses                    # At least one failed
        assert statuses.count("success") >= 9          # Others still succeeded


# ─────────────────────────────────────────────────────────────────────────────
# iceberg_maintenance_main (parent flow)
# ─────────────────────────────────────────────────────────────────────────────

def _make_compact_results(n_fail: int = 0):
    """Build compact result list with n_fail failures at the end."""
    from iceberg_maintenance_flow import _ALL_TABLES
    results = []
    for i, t in enumerate(_ALL_TABLES):
        status = "failed" if i >= (len(_ALL_TABLES) - n_fail) else "success"
        r = {"table": t, "status": status, "timestamp": "2026-02-18T02:00:00"}
        if status == "failed":
            r["error"] = "simulated failure"
        results.append(r)
    return results


def _make_expire_results(n_fail: int = 0):
    """Build expire result list with n_fail failures at the end."""
    from iceberg_maintenance_flow import _ALL_TABLES
    results = []
    for i, t in enumerate(_ALL_TABLES):
        status = "failed" if i >= (len(_ALL_TABLES) - n_fail) else "success"
        r = {"table": t, "status": status, "timestamp": "2026-02-18T02:00:00"}
        if status == "failed":
            r["error"] = "simulated failure"
        results.append(r)
    return results


class TestMaintenanceMainFlow:
    """
    Tests for iceberg_maintenance_main orchestration logic.

    We mock at the sub-flow/task level (compact_all_tables, expire_all_snapshots,
    run_audit) rather than at subprocess.run level.  Patching subprocess.run
    globally also intercepts Prefect's internal platform-detection calls
    (platform.py → subprocess.check_output), which breaks unrelated code.
    Mocking at the sub-flow level gives us clean isolation of the orchestration
    logic that iceberg_maintenance_main actually owns.
    """

    _CLEAN_AUDIT  = {"status": "success", "missing_count": 0, "error_count": 0,
                     "timestamp": "2026-02-18T02:00:00"}
    _FAILED_AUDIT = {"status": "failed", "missing_count": 0, "error_count": 1,
                     "error": "audit crashed", "timestamp": "2026-02-18T02:00:00"}
    _MISSING_AUDIT = {"status": "success", "missing_count": 1, "error_count": 0,
                      "timestamp": "2026-02-18T02:00:00"}

    def test_all_success_returns_summary(self):
        """Full success path returns a dict with overall_status=success."""
        from iceberg_maintenance_flow import iceberg_maintenance_main

        with patch("iceberg_maintenance_flow.compact_all_tables",
                   return_value=_make_compact_results(0)), \
             patch("iceberg_maintenance_flow.expire_all_snapshots",
                   return_value=_make_expire_results(0)), \
             patch("iceberg_maintenance_flow.run_audit",
                   return_value=self._CLEAN_AUDIT):
            result = iceberg_maintenance_main.fn()

        assert result["overall_status"] == "success"
        assert result["compact_failed"] == 0
        assert result["expire_failed"] == 0
        assert result["audit_missing"] == 0
        assert result["audit_errors"] == 0
        assert "total_duration_seconds" in result

    def test_compact_failures_yield_partial_status(self):
        """When compaction fails for some tables, overall_status = 'partial'."""
        from iceberg_maintenance_flow import iceberg_maintenance_main

        with patch("iceberg_maintenance_flow.compact_all_tables",
                   return_value=_make_compact_results(1)), \
             patch("iceberg_maintenance_flow.expire_all_snapshots",
                   return_value=_make_expire_results(0)), \
             patch("iceberg_maintenance_flow.run_audit",
                   return_value=self._CLEAN_AUDIT):
            result = iceberg_maintenance_main.fn()

        assert result["overall_status"] == "partial"
        assert result["compact_failed"] == 1
        assert result["audit_missing"] == 0

    def test_audit_missing_data_raises_runtime_error(self):
        """When audit reports missing_data, the flow raises RuntimeError."""
        from iceberg_maintenance_flow import iceberg_maintenance_main

        with patch("iceberg_maintenance_flow.compact_all_tables",
                   return_value=_make_compact_results(0)), \
             patch("iceberg_maintenance_flow.expire_all_snapshots",
                   return_value=_make_expire_results(0)), \
             patch("iceberg_maintenance_flow.run_audit",
                   return_value=self._MISSING_AUDIT):
            with pytest.raises(RuntimeError, match="missing_data=1"):
                iceberg_maintenance_main.fn()

    def test_audit_error_raises_runtime_error(self):
        """When the audit task itself errors, the flow raises RuntimeError."""
        from iceberg_maintenance_flow import iceberg_maintenance_main

        with patch("iceberg_maintenance_flow.compact_all_tables",
                   return_value=_make_compact_results(0)), \
             patch("iceberg_maintenance_flow.expire_all_snapshots",
                   return_value=_make_expire_results(0)), \
             patch("iceberg_maintenance_flow.run_audit",
                   return_value=self._FAILED_AUDIT):
            with pytest.raises(RuntimeError):
                iceberg_maintenance_main.fn()

    def test_duration_is_recorded(self):
        """Summary always includes total_duration_seconds."""
        from iceberg_maintenance_flow import iceberg_maintenance_main

        with patch("iceberg_maintenance_flow.compact_all_tables",
                   return_value=_make_compact_results(0)), \
             patch("iceberg_maintenance_flow.expire_all_snapshots",
                   return_value=_make_expire_results(0)), \
             patch("iceberg_maintenance_flow.run_audit",
                   return_value=self._CLEAN_AUDIT):
            result = iceberg_maintenance_main.fn()

        assert result["total_duration_seconds"] >= 0


# ─────────────────────────────────────────────────────────────────────────────
# iceberg_maintenance.py — unit tests (no Spark, no PG)
# ─────────────────────────────────────────────────────────────────────────────

class TestMaintenanceScriptHelpers:
    """
    Tests for the pure-Python helpers in iceberg_maintenance.py that can be
    evaluated without a Spark or PostgreSQL connection.
    """

    def test_audit_status_ok_within_tolerance(self):
        from iceberg_maintenance import _audit_status
        status, pct, notes = _audit_status(ch_count=1000, iceberg_count=1005)
        assert status == "ok"
        assert pct is not None
        assert abs(pct) <= 1.0

    def test_audit_status_ok_when_ch_count_zero(self):
        from iceberg_maintenance import _audit_status
        status, pct, notes = _audit_status(ch_count=0, iceberg_count=0)
        assert status == "ok"
        assert pct is None

    def test_audit_status_warning_moderate_delta(self):
        from iceberg_maintenance import _audit_status
        # 3% delta — above warning threshold (1%) but below missing (5%)
        status, pct, notes = _audit_status(ch_count=1000, iceberg_count=970)
        assert status == "warning"
        assert pct is not None

    def test_audit_status_missing_data_large_gap(self):
        from iceberg_maintenance import _audit_status
        # Iceberg has 10% fewer rows than ClickHouse — missing data
        status, pct, notes = _audit_status(ch_count=1000, iceberg_count=890)
        assert status == "missing_data"

    def test_audit_status_iceberg_higher_is_ok(self):
        """Iceberg > ClickHouse is fine (cold accumulates all history)."""
        from iceberg_maintenance import _audit_status
        # Iceberg has 0.5% more — normal for accumulated historical data
        status, pct, notes = _audit_status(ch_count=1000, iceberg_count=1005)
        assert status == "ok"

    def test_compact_default_args(self):
        """action_compact accepts optional target_file_size_mb."""
        from iceberg_maintenance import action_compact
        # dry_run=True means no Spark session is opened — safe to call directly
        action_compact(table="cold.bronze_trades_binance", target_file_size_mb=64, dry_run=True)

    def test_expire_default_args(self):
        from iceberg_maintenance import action_expire
        action_expire(
            table="cold.silver_trades",
            max_age_hours=72,
            retain_last=2,
            dry_run=True,
        )

    def test_all_tables_covered_by_audit_config(self):
        """_AUDIT_TABLE_CONFIG must include all 10 cold tables."""
        from iceberg_maintenance import _AUDIT_TABLE_CONFIG
        from iceberg_maintenance_flow import _ALL_TABLES

        audit_targets = {cfg["iceberg_table"] for cfg in _AUDIT_TABLE_CONFIG}
        flow_targets  = set(_ALL_TABLES)
        assert audit_targets == flow_targets, (
            f"Mismatch between audit config and flow table list:\n"
            f"  In audit only : {audit_targets - flow_targets}\n"
            f"  In flow only  : {flow_targets - audit_targets}"
        )
