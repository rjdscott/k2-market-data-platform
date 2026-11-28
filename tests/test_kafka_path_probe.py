"""scripts/lib/kafka_path_probe.py's host-side claims that plan 004's chaos scripts add or widen
(offset-gap; gap-audit on a 004 product's batch ids), and run-path-lib.sh's kclaim, which numbers
the probe's line as the script's own. Files as the scripts leave them under <rp>; no container."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("kafka_path_probe", ROOT / "scripts/lib/kafka_path_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

TOPIC, LANDING = "market.crypto.v3.raw.kraken", "lake.kraken_trades.landing"


def snapshot(sid, ends):
    return [sid, None, "2001-09-09 01:46:40", "append", {"k2.job": "connector", "k2.kafka-offsets": json.dumps({TOPIC: ends})}]


def write(rp: Path, files: dict):
    for name, body in files.items():
        (rp / name).write_text(body if isinstance(body, str) else json.dumps(body))


@pytest.fixture
def gap_rp(tmp_path):
    """b1 committed partition 0 through 10 and 1 through 8; partition 0 trimmed to 15; b4's row."""
    gaps = [{"at": "2001-09-09 01:50:00", "partitions": [{"partition": 0, "fromOffset": 10, "toOffset": 14}], "reason": "drill"}]
    write(tmp_path, {"facts-b1.json": {"landing": {"snapshots": [snapshot(1, {"0": 10, "1": 8})]}},
                     "trims.json": {"0": 15}, "b4.load.out": json.dumps({"landing": LANDING, "exit": 0}) + "\n",
                     "b4.record.out": json.dumps({"reason": "drill", "gaps": gaps, "row": {
                         "run_id": "kraken_trades/b4", "task": "load", "event": "offset_gap", "table_name": LANDING}}) + "\n",
                     "b4.record.rc": "0\n", "b4.record.err": ""})
    return tmp_path


def test_offset_gap_passes_on_the_trimmed_range_and_reason(gap_rp, capsys):
    assert probe.claim("offset-gap", gap_rp)
    assert capsys.readouterr().out.startswith("PASS 4 ")


@pytest.mark.parametrize("change", [
    lambda r: r["gaps"][0]["partitions"][0].update(toOffset=15),  # one past the log start - 1
    lambda r: r["gaps"][0].update(reason="other"),
    lambda r: r["row"].update(event="passed"),
    lambda r: r["gaps"].append(dict(r["gaps"][0])),  # two gaps where one was accepted
])
def test_offset_gap_fails_on_any_other_row(gap_rp, change):
    r = json.loads((gap_rp / "b4.record.out").read_text())
    change(r)
    (gap_rp / "b4.record.out").write_text(json.dumps(r) + "\n")
    assert not probe.claim("offset-gap", gap_rp)


def test_offset_gap_fails_when_the_record_container_failed(gap_rp):
    (gap_rp / "b4.record.rc").write_text("1\n")
    assert not probe.claim("offset-gap", gap_rp)


def b4_facts(batch):
    ends = {"0": 10}
    return {"topic": {"latest": {TOPIC: ends}},
            "landing": {"snapshots": [snapshot(2, ends)], "main": [[0, 5]]},
            "bronze": {"snapshots": [snapshot(3, ends)], "refs": {"main": 3}, "main": [[0, 5]]},
            "checks": [[batch, 3, c, True, "ok"] for c in ("accounting", "keys_unique", "parses")]}


@pytest.mark.parametrize("batch, passed", [("kraken_trades/b4", True), ("kafka_fixture/b4", True), ("kraken_trades/b5", False)])
def test_gap_audit_reads_b4s_rows_under_any_dag(tmp_path, batch, passed):
    write(tmp_path, {"facts-b4.json": b4_facts(batch), "b4.audit.rc": "0\n", "b4.publish.rc": "0\n"})
    assert probe.claim("gap-audit", tmp_path) is passed


def kclaim(rp: Path, w: Path) -> subprocess.CompletedProcess:
    script = f'. scripts/lib/run-path-lib.sh; W={w}; RP={rp}; RP_N=2 RP_CLAIMS=3 P=t T0=$SECONDS; kclaim offset-gap; echo NOT-STOPPED'
    return subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True)


def test_kclaim_numbers_the_probes_line_as_the_scripts_next(gap_rp, tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    r = kclaim(gap_rp, w)
    assert r.returncode == 0 and "NOT-STOPPED" in r.stdout, r.stderr
    [line] = (w / "claims").read_text().splitlines()
    assert line.startswith("PASS 3 the accept-data-loss run records offset_gap: ")


def test_kclaim_stops_the_run_at_a_fail(gap_rp, tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    (gap_rp / "b4.record.rc").write_text("1\n")
    r = kclaim(gap_rp, w)
    assert r.returncode != 0 and "NOT-STOPPED" not in r.stdout
    assert (w / "claims").read_text().startswith("FAIL 3 ") and "0 PASS, 1 FAIL" in r.stdout
