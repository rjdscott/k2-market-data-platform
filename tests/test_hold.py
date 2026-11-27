"""scripts/hold.py: make hold and make unhold, with the run-records client and Airflow faked."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "platform" / "runs")]
import hold  # noqa: E402
import k2runs  # noqa: E402

DAGS = [{"product": "market-capture.kraken-trades", "dagId": "kraken_trades"},
        {"product": "sensor-net.station-readings", "dagId": "station_readings"}]
P = "market-capture.kraken-trades"


class Fake:
    """Stands in for k2runs: `open` is the list of open hold kinds; `log` the order of calls."""
    def __init__(self, open_kinds=()):
        self.open, self.log = list(open_kinds), []

    def install(self, mp):
        mp.setattr(k2runs, "held", lambda c, p: ({"kind": self.open[-1], "held_by": "x", "held_at": "t", "reason": "r"}
                                                  if self.open else None))
        mp.setattr(k2runs, "hold", lambda c, p, r, by, **k: (self.log.append("row"), self.open.append("operator")))

        def release(c, p, by, **k):
            if "operator" not in self.open:
                raise k2runs.HoldError("none")
            self.open.remove("operator")
            self.log.append("release")
        mp.setattr(k2runs, "release", release)
        return self




def run_hold(f, mp, pause):
    f.install(mp)
    return hold.do_hold(None, P, "kraken_trades", "drill", "rob", pause)


def test_resolve_forms_and_unknown():
    for a in ("market-capture/kraken-trades", "market-capture.kraken-trades", "kraken-trades"):
        assert hold.resolve(a, DAGS) == (P, "kraken_trades")
    for a in ("", "nope", "market-capture"):
        with pytest.raises(hold.Refused):
            hold.resolve(a, DAGS)


def test_reason_rules():
    assert hold.check_reason(" drill ") == "drill"
    for r in ("", "   ", "x" * 201):
        with pytest.raises(hold.Refused):
            hold.check_reason(r)
    assert hold.check_reason("x" * 200)


def test_hold_row_then_pause(monkeypatch):
    f = Fake()
    assert run_hold(f, monkeypatch, lambda d: f.log.append(f"pause {d}")) == 0
    assert f.log == ["row", "pause kraken_trades"]


def test_hold_refused_when_open_and_names_it(monkeypatch):
    f = Fake(["operator"])
    paused = []
    with pytest.raises(hold.Refused, match="held already by x"):
        run_hold(f, monkeypatch, paused.append)
    assert f.log == [] and paused == []


def test_pause_failure_keeps_hold_and_exits_nonzero(monkeypatch, capsys):
    f = Fake()

    def boom(d):
        raise RuntimeError("no scheduler")
    assert run_hold(f, monkeypatch, boom) == 1
    assert f.open == ["operator"] and "NOT paused" in capsys.readouterr().err


def test_unhold_without_hold_refused(monkeypatch):
    f = Fake().install(monkeypatch)
    with pytest.raises(hold.Refused):
        hold.do_unhold(None, P, "kraken_trades", "rob", lambda d: f.log.append("unpause"))
    assert f.log == []


def test_unhold_order_and_unpause(monkeypatch):
    f = Fake(["operator"]).install(monkeypatch)
    assert hold.do_unhold(None, P, "kraken_trades", "rob", lambda d: f.log.append("unpause")) == 0
    assert f.log == ["release", "unpause"]


def test_unhold_with_restore_open_leaves_dag_paused(monkeypatch, capsys):
    f = Fake(["operator", "restore"]).install(monkeypatch)
    assert hold.do_unhold(None, P, "kraken_trades", "rob", lambda d: f.log.append("unpause")) == 0
    assert f.log == ["release"] and f.open == ["restore"] and "restore hold is still open" in capsys.readouterr().out


def test_unhold_never_releases_rebuild_or_restore(monkeypatch):
    f = Fake(["rebuild"]).install(monkeypatch)
    with pytest.raises(hold.Refused):
        hold.do_unhold(None, P, "kraken_trades", "rob", lambda d: None)
    assert f.open == ["rebuild"]


def test_no_secret_in_any_argv(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(hold.subprocess, "run", lambda argv, **k: calls.append(argv) or type("D", (), {"returncode": 0})())
    hold.airflow("pause", "kraken_trades")
    hold.airflow("unpause", "kraken_trades")
    assert calls and all("password" not in " ".join(a) and "secret" not in " ".join(a) for a in calls)
    assert calls[0] == ["docker", "exec", "k2-airflow-scheduler", "airflow", "dags", "pause", "kraken_trades", "-y"]


def test_live_dags_reads_airflow_pointer(tmp_path):
    rel = tmp_path / "rels" / "R1" / "compiled" / "airflow"
    rel.mkdir(parents=True)
    (rel / "dags.json").write_text(json.dumps({"dags": DAGS}))
    (tmp_path / "rels" / "pointers").mkdir()
    (tmp_path / "rels" / "pointers" / "airflow").symlink_to("../R1")
    assert hold.live_dags(tmp_path / "rels") == DAGS
    with pytest.raises(hold.Refused):
        hold.live_dags(tmp_path / "none")
