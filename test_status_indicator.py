import json
from pathlib import Path

import handlers


def test_curate_status_reports_pending_candidates(monkeypatch):
    monkeypatch.setattr(
        handlers,
        "list_vaults",
        lambda: [
            {"id": "core", "pending_count": 2},
            {"id": "projects", "pending_count": 1},
        ],
    )

    status = handlers.curate_status()

    assert status == {
        "active": True,
        "count": 3,
        "pendingCount": 3,
        "tone": "warning",
        "label": "3 pending candidates",
    }


def test_curate_status_stays_inactive_when_no_pending_candidates(monkeypatch):
    monkeypatch.setattr(handlers, "list_vaults", lambda: [{"id": "core", "pending_count": 0}])

    status = handlers.curate_status()

    assert status["active"] is False
    assert status["count"] == 0
    assert status["tone"] == "neutral"
    assert status["label"] == "0 pending candidates"


def test_curate_status_degrades_when_backend_is_unavailable(monkeypatch):
    def fail():
        raise RuntimeError("BDH offline")

    monkeypatch.setattr(handlers, "list_vaults", fail)

    status = handlers.curate_status()

    assert status["active"] is False
    assert status["count"] == 0
    assert status["label"] == "Curate status unavailable"


def test_status_indicator_uses_namespaced_plugin_route():
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text(encoding="utf-8"))
    indicator_path = manifest["navItem"]["indicator"]["endpoint"]
    status_endpoint = next(
        endpoint for endpoint in manifest["endpoints"] if endpoint["handler"] == "curateStatus"
    )

    assert indicator_path == "/curate/status"
    assert status_endpoint["path"] == indicator_path
