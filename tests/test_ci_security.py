"""The CI `security` job can fail the build, and what it accepts is decided,
explained and dated: every .trivyignore.yaml entry carries a reason and an
expiry no more than a year out."""

import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_the_trivy_gate_fails_on_a_fixable_critical():
    steps = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["security"]["steps"]
    gates = [s["with"] for s in steps if "trivy-action" in s.get("uses", "") and s.get("with", {}).get("exit-code")]
    assert len(gates) == 1, "one Trivy step must carry exit-code"
    gate = gates[0]
    assert str(gate["exit-code"]) == "1" and gate["severity"] == "CRITICAL" and gate["ignore-unfixed"] is True
    assert gate["trivyignores"] == ".trivyignore.yaml" and (ROOT / gate["trivyignores"]).is_file()


def test_every_accepted_finding_has_a_reason_and_an_expiry_within_a_year():
    doc = yaml.safe_load((ROOT / ".trivyignore.yaml").read_text())
    assert set(doc) <= {"vulnerabilities", "misconfigurations", "secrets", "licenses"}
    limit = datetime.date.today() + datetime.timedelta(days=366)
    for kind, entries in doc.items():
        for e in entries or []:
            assert e.get("id"), f"{kind}: an entry with no id"
            assert len(str(e.get("statement", "")).strip()) >= 10, f"{e['id']}: no statement of why it is accepted"
            expiry = e.get("expired_at")
            assert isinstance(expiry, datetime.date), f"{e['id']}: no expired_at date"
            assert expiry <= limit, f"{e['id']}: expires more than a year out"
