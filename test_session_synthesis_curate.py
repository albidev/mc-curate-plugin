import sys
import types

import handlers


CANDIDATE = {
    "candidate_id": "cand-new123",
    "synthesis_id": "synth-123",
    "session_id": "session-123",
    "vault_id": "core",
    "source": "session_synthesis",
    "title": "Read Write Path Independence",
    "definition": "Read and write paths should remain independently testable.",
    "confidence": "low",
    "status": "pending_review",
    "created_at": "2026-09-08T13:58:22+00:00",
    "provenance": {"source_notes": ["session-synthesis", "architecture"]},
    "extra": {"slug": "read-write-path-independence"},
}


def _install_proxy(monkeypatch, **overrides):
    proxy = types.ModuleType("synthesis_activity_proxy")
    proxy.load_synthesis_candidates = lambda **kwargs: {
        "vault_id": "core",
        "count": 1,
        "candidates": [CANDIDATE],
    }
    proxy.get_synthesis_candidate = lambda candidate_id, vault_id=None: (
        CANDIDATE if candidate_id == CANDIDATE["candidate_id"] else None
    )
    for name, value in overrides.items():
        setattr(proxy, name, value)
    monkeypatch.setitem(sys.modules, "synthesis_activity_proxy", proxy)
    return proxy


def test_core_curate_lists_bdh_session_synthesis_candidates(monkeypatch):
    _install_proxy(monkeypatch)

    candidates = handlers.list_candidates(vault="core")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["id"] == "cand-new123"
    assert candidate["status"] == "pending_review"
    assert candidate["title"] == "Read Write Path Independence"
    assert candidate["body"] == CANDIDATE["definition"]
    assert candidate["created"] == CANDIDATE["created_at"]


def test_core_vault_summary_counts_pending_review(monkeypatch):
    _install_proxy(monkeypatch)

    core = next(vault for vault in handlers.list_vaults() if vault["id"] == "core")

    assert core["candidate_count"] == 1
    assert core["pending_count"] == 1


def test_core_curate_approval_applies_bdh_candidate(monkeypatch):
    calls = []

    def approve(**kwargs):
        calls.append(("approve", kwargs))
        return {"candidate_id": "cand-new123", "status": "approved", "idempotent": False}

    def apply(**kwargs):
        calls.append(("apply", kwargs))
        return {"candidate_id": "cand-new123", "status": "created", "applied": True}

    _install_proxy(monkeypatch, approve_synthesis_candidate=approve, apply_synthesis_candidate=apply)

    result = handlers.approve("cand-new123", vault="core")

    assert result["id"] == "cand-new123"
    assert result["status"] == "created"
    assert [call[0] for call in calls] == ["approve", "apply"]
    assert calls[0][1]["synthesis_id"] == "synth-123"
    assert calls[1][1]["session_id"] == "session-123"


def test_core_curate_rejection_is_kept_in_local_feedback_log(monkeypatch):
    recorded = {}

    def record_rejection(**kwargs):
        recorded.update(kwargs)
        return {**kwargs, "rejected_at": "2026-09-08T14:00:00+00:00"}

    proxy = _install_proxy(monkeypatch)
    proxy.session_synthesis_rejections = types.SimpleNamespace(record_rejection=record_rejection)

    result = handlers.reject("cand-new123", reason="duplicated", vault="core")

    assert result["id"] == "cand-new123"
    assert result["status"] == "rejected"
    assert recorded["candidate_id"] == "cand-new123"
    assert recorded["synthesis_id"] == "synth-123"
    assert recorded["reason"] == "duplicated"
