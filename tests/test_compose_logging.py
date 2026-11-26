"""Every compose service caps its local log.

Docker's json-file driver lets one container's log grow without limit. On
2026-11-26 the catalog's log grew about 15 GiB a day and the VM disk had a day
and a half left. A service added without the cap would bring that back.
"""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_every_service_caps_its_log():
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    assert services
    uncapped = [
        name
        for name, svc in services.items()
        if (svc.get("logging") or {}).get("options", {}).get("max-size") != "50m"
        or (svc.get("logging") or {}).get("options", {}).get("max-file") != "5"
    ]
    assert not uncapped, f"services without the log cap: {uncapped}"
