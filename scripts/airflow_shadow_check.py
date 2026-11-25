#!/usr/bin/env python3
"""The verdict half of scripts/airflow-shadow-check.sh, which fetches the two
run lists and calls this:

    python3 scripts/airflow_shadow_check.py START END airflow.json prefect.json

START and END are ISO-8601 with an offset; the window is [START, END], both
ends inclusive because both APIs filter that way. airflow.json is
`airflow dags list-runs lake_ingest -o json`; prefect.json is Prefect's
`POST /api/flow_runs/filter` for the lake-ingest flow over the same window.
Stdlib only, so tests/test_airflow_shadow_check.py runs it with no stack.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta


def slots(start: datetime, end: datetime) -> list[datetime]:
    """Every firing of `1-59/5 * * * *` (minutes 1, 6 ... 56) in [start, end]."""
    first = start.replace(second=0, microsecond=0)
    if first < start:
        first += timedelta(minutes=1)
    out, t = [], first
    while t <= end:
        if t.minute % 5 == 1:
            out.append(t)
        t += timedelta(minutes=1)
    return out


def verdict(start: datetime, end: datetime, airflow: list, prefect: list) -> list[tuple[bool, str, str]]:
    """(passed, check, detail) for the three checks the shell script names."""
    expected = slots(start, end)
    runs = {
        datetime.fromisoformat(r["logical_date"]): r["state"]
        for r in airflow
        if r["run_id"].startswith("scheduled__") and r["logical_date"]
    }
    missing = [s for s in expected if s not in runs]
    not_ok = [(s, runs[s]) for s in expected if s in runs and runs[s] != "success"]
    pf_bad = [(r["name"], r["state_type"]) for r in prefect if r["state_type"] != "COMPLETED"]
    done = len(prefect) - len(pf_bad)
    return [
        (not missing, "(1) every slot ran",
         f"{len(expected) - len(missing)} of {len(expected)} slots"
         + (f"; missing {', '.join(s.isoformat() for s in missing)}" if missing else "")),
        (not not_ok, "(2) none failed",
         "all success" if not not_ok else "; ".join(f"{s.isoformat()} {st}" for s, st in not_ok)),
        (not pf_bad and len(prefect) == len(expected), "(3) Prefect undisturbed",
         f"{done} COMPLETED of {len(prefect)} runs for {len(expected)} slots"
         + (f"; not completed: {pf_bad}" if pf_bad else "")),
    ]


def main(argv: list[str]) -> int:
    start, end = (datetime.fromisoformat(a) for a in argv[1:3])
    with open(argv[3]) as a, open(argv[4]) as p:
        checks = verdict(start, end, json.load(a), json.load(p))
    for ok, check, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}: {check} ({detail})")
    ok = all(c[0] for c in checks)
    print("shadow check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
