"""
scripts/airflow_shadow_check.py: the schedule-shadow verdict, fed run lists in
the shapes `airflow dags list-runs -o json` and Prefect's flow-run filter return.
"""

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
shadow = importlib.import_module("airflow_shadow_check")

UTC = timezone.utc
START = datetime(2026, 11, 25, 10, 1, tzinfo=UTC)   # itself a slot
END = START + timedelta(hours=2)                      # 12:01, also a slot


def airflow_run(slot, state="success", run_type="scheduled"):
    return {"run_id": f"{run_type}__{slot.isoformat()}", "logical_date": slot.isoformat(), "state": state}


def prefect_run(state="COMPLETED"):
    return {"name": "some-flow-run", "state_type": state}


def healthy():
    slots = shadow.slots(START, END)
    return [airflow_run(s) for s in slots], [prefect_run() for _ in slots]


def passed(airflow, prefect):
    return [ok for ok, _, _ in shadow.verdict(START, END, airflow, prefect)]


def test_a_2_h_window_holds_25_slots_when_both_ends_are_slots():
    slots = shadow.slots(START, END)
    assert len(slots) == 25
    assert slots[0] == START and slots[-1] == END
    assert all(s.minute % 5 == 1 for s in slots)


def test_a_window_off_the_minute_starts_at_the_next_minute():
    slots = shadow.slots(START + timedelta(seconds=30), END)
    assert slots[0] == START + timedelta(minutes=5)


def test_healthy_shadow_passes_all_three():
    assert passed(*healthy()) == [True, True, True]


def test_a_missing_slot_fails_check_1_only():
    airflow, prefect = healthy()
    del airflow[7]
    assert passed(airflow, prefect) == [False, True, True]


def test_a_manual_run_does_not_stand_in_for_a_scheduled_slot():
    airflow, prefect = healthy()
    airflow[3] = airflow_run(datetime.fromisoformat(airflow[3]["logical_date"]), run_type="manual")
    assert passed(airflow, prefect)[0] is False


def test_a_failed_or_still_running_run_fails_check_2():
    for state in ("failed", "running"):
        airflow, prefect = healthy()
        airflow[0]["state"] = state
        assert passed(airflow, prefect) == [True, False, True]


def test_prefect_failing_or_missing_a_run_fails_check_3():
    airflow, prefect = healthy()
    prefect[2] = prefect_run("FAILED")
    assert passed(airflow, prefect) == [True, True, False]
    airflow, prefect = healthy()
    assert passed(airflow, prefect[:-1]) == [True, True, False]
