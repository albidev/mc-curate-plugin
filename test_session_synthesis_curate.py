import sys
import types

import pytest

import handlers


CANDIDATE = {
    "candidate_id": "cand-new123",
    "synthesis_id": "synth-123",
    "session_id": "session-123",
    "vault_id": "projects-knowledge",
    "source": "session_synthesis",
    "title": "Read Write Path Independence",
    "definition": "Read and write paths should remain independently testable.",
    "confidence": "low",
    "status": "pending_review",
    "created_at": "2026-09-08T13:58:22+00:00",
    "provenance": {
        "source_notes": ["session-synthesis", "architecture"],
        "source_node_ids": ["vault:wiki/a.md", "vault:wiki/b.md"],
    },
    "extra": {"slug": "read-write-path-independence"},
}


def _install_proxy(monkeypatch, **overrides):
    proxy = types.ModuleType("synthesis_activity_proxy")
    proxy.load_synthesis_candidates = lambda **kwargs: {
        "vault_id": "projects-knowledge",
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


def test_curate_lists_bdh_session_synthesis_candidates(monkeypatch):
    _install_proxy(monkeypatch)

    candidates = handlers.list_candidates(vault="projects-knowledge")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["id"] == "cand-new123"
    assert candidate["status"] == "pending_review"
    assert candidate["title"] == "Read Write Path Independence"
    assert candidate["body"] == CANDIDATE["definition"]
    assert candidate["created"] == CANDIDATE["created_at"]
    assert candidate["sourceNodeIds"] == ["vault:wiki/a.md", "vault:wiki/b.md"]


def test_vault_summary_counts_pending_review(monkeypatch):
    _install_proxy(monkeypatch)

    project_vault = next(vault for vault in handlers.list_vaults() if vault["id"] == "projects-knowledge")

    assert project_vault["candidate_count"] == 1
    assert project_vault["pending_count"] == 1


def test_curate_approval_applies_bdh_candidate(monkeypatch):
    calls = []

    def approve(**kwargs):
        calls.append(("approve", kwargs))
        return {"candidate_id": "cand-new123", "status": "approved", "idempotent": False}

    def apply(**kwargs):
        calls.append(("apply", kwargs))
        return {"candidate_id": "cand-new123", "status": "created", "applied": True}

    _install_proxy(monkeypatch, approve_synthesis_candidate=approve, apply_synthesis_candidate=apply)

    result = handlers.approve("cand-new123", vault="projects-knowledge")

    assert result["id"] == "cand-new123"
    assert result["status"] == "created"
    assert [call[0] for call in calls] == ["approve", "apply"]
    assert calls[0][1]["synthesis_id"] == "synth-123"
    assert calls[1][1]["session_id"] == "session-123"


def test_legacy_candidate_extracts_description_from_body_yaml(tmp_path):
    path = tmp_path / "legacy.md"
    path.write_text(
        "---\n"
        "id: legacy-1\n"
        "title: Legacy concept\n"
        "description: \"|\"\n"
        "---\n"
        "title: Legacy concept\n"
        "description: |\n"
        "  The semantic description lives in the body YAML.\n"
        "\n"
        "## Sources\n"
        "- [[wiki/example]]\n",
        encoding="utf-8",
    )

    candidate = handlers._read_candidate(path)

    assert candidate["description"] == "The semantic description lives in the body YAML."
    assert candidate["body"] == "The semantic description lives in the body YAML."


def test_legacy_candidate_uses_markdown_body_when_description_is_missing(tmp_path):
    path = tmp_path / "repo-update.md"
    path.write_text(
        "---\n"
        "id: repo-update-1\n"
        "title: Repository update\n"
        "description: null\n"
        "---\n"
        "**Repository:** example/repo\n\n"
        "**Commits:**\n- `abc123` fix: close the hole\n",
        encoding="utf-8",
    )

    candidate = handlers._read_candidate(path)

    assert candidate["description"] == candidate["body"]
    assert "Repository" in candidate["description"]


def test_source_notes_resolve_title_inside_selected_vault(tmp_path):
    note = tmp_path / "projects" / "crossnection" / "pentair-mqtt-bridge-spec.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\n"
        "title: Pentair — Bottone Fisico di Conteggio (specifica architetturale)\n"
        "---\n\n"
        "# Pentair — Bottone Fisico di Conteggio\n\nDettagli.\n"
        "Advantech ADAM 6050 hardware reference.\n",
        encoding="utf-8",
    )

    notes = handlers._source_notes(
        ["Pentair — Bottone Fisico di Conteggio", "Advantech ADAM 6050"],
        vault_root=tmp_path,
    )

    assert notes[0]["found"] is True
    assert notes[0]["match_type"] == "found"
    assert notes[0]["path"] == str(note)
    assert notes[1]["found"] is True
    assert notes[1]["match_type"] == "related"
    assert notes[1]["path"] == str(note)


def test_curate_rejection_is_kept_in_local_feedback_log(monkeypatch):
    recorded = {}

    def record_rejection(**kwargs):
        recorded.update(kwargs)
        return {**kwargs, "rejected_at": "2026-09-08T14:00:00+00:00"}

    proxy = _install_proxy(monkeypatch)
    proxy.session_synthesis_rejections = types.SimpleNamespace(record_rejection=record_rejection)

    result = handlers.reject("cand-new123", reason="duplicated", vault="projects-knowledge")

    assert result["id"] == "cand-new123"
    assert result["status"] == "rejected"
    assert recorded["candidate_id"] == "cand-new123"
    assert recorded["synthesis_id"] == "synth-123"
    assert recorded["reason"] == "duplicated"


def test_bdh_default_vault_is_discovered_without_synthetic_core(monkeypatch):
    calls = []
    proxy = _install_proxy(monkeypatch)

    def load_candidates(**kwargs):
        calls.append(kwargs)
        return {
            "vault_id": "projects-knowledge",
            "count": 0,
            "candidates": [],
        }

    proxy.load_synthesis_candidates = load_candidates
    monkeypatch.setattr(handlers, "_load_vaults", lambda: {})
    monkeypatch.setattr(handlers, "_load_routing_vaults", lambda: {})

    vaults = handlers.list_vaults()

    assert [vault["id"] for vault in vaults] == ["projects-knowledge"]
    assert vaults[0]["candidate_enabled"] is True
    assert calls[0] == {"vault_id": None, "status": None}


def test_bdh_unknown_vault_is_not_converted_to_empty_queue(monkeypatch):
    proxy = _install_proxy(monkeypatch)

    class UnknownVaultError(RuntimeError):
        status_code = 400

    def load_candidates(**kwargs):
        raise UnknownVaultError("Unknown vault 'missing'")

    proxy.load_synthesis_candidates = load_candidates

    with pytest.raises(handlers.CurateIntegrationError) as error:
        handlers.list_candidates(vault="missing")

    assert error.value.status_code == 400
    assert error.value.code == "invalid_vault"


def test_candidate_without_vault_id_does_not_gain_core_fallback():
    candidate = handlers._normalize_session_synthesis_candidate(
        {"candidate_id": "candidate-1", "definition": "Definition"}
    )

    assert candidate["vault_id"] == ""


def test_bdh_unavailable_is_not_converted_to_empty_queue(monkeypatch):
    proxy = _install_proxy(monkeypatch)

    class UnavailableError(RuntimeError):
        status_code = 502

    def load_candidates(**kwargs):
        raise UnavailableError("BDH unavailable")

    proxy.load_synthesis_candidates = load_candidates

    with pytest.raises(handlers.CurateIntegrationError) as error:
        handlers.list_candidates(vault="projects-knowledge")

    assert error.value.status_code == 502
    assert error.value.code == "bdh_unavailable"


def test_explicit_legacy_vault_merges_local_and_bdh_candidates(monkeypatch, tmp_path):
    candidates_dir = tmp_path / "candidates"
    candidates_dir.mkdir()
    (candidates_dir / "legacy.md").write_text(
        "---\n"
        "id: legacy-1\n"
        "title: Legacy candidate\n"
        "status: pending\n"
        "---\n\n"
        "Legacy body.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        handlers,
        "_load_vaults",
        lambda: {"legacy": {"candidates_dir": str(candidates_dir)}},
    )
    monkeypatch.setattr(handlers, "_load_routing_vaults", lambda: {})
    proxy = _install_proxy(monkeypatch)
    bdh_candidate = {**CANDIDATE, "vault_id": "legacy"}
    proxy.load_synthesis_candidates = lambda **kwargs: {
        "vault_id": "legacy",
        "count": 1,
        "candidates": [bdh_candidate],
    }

    candidates = handlers.list_candidates(vault="legacy")

    assert {candidate["id"] for candidate in candidates} == {"legacy-1", "cand-new123"}


def test_stale_configured_vault_does_not_break_vault_listing(monkeypatch):
    monkeypatch.setattr(
        handlers,
        "_load_vaults",
        lambda: {"stale": {"candidates_dir": "/tmp/stale-curate-candidates"}},
    )
    monkeypatch.setattr(handlers, "_load_routing_vaults", lambda: {})
    proxy = _install_proxy(monkeypatch)

    class UnknownVaultError(RuntimeError):
        status_code = 400

    def load_candidates(**kwargs):
        if kwargs.get("vault_id") == "stale":
            raise UnknownVaultError("Unknown vault 'stale'")
        return {"vault_id": "projects-knowledge", "count": 0, "candidates": []}

    proxy.load_synthesis_candidates = load_candidates

    vaults = handlers.list_vaults()
    stale = next(vault for vault in vaults if vault["id"] == "stale")

    assert stale["mode"] == "error"
    assert stale["candidate_enabled"] is False
    assert "Unknown vault" in stale["error"]


def test_legacy_and_bdh_merge_deduplicates_candidate_ids(monkeypatch, tmp_path):
    candidates_dir = tmp_path / "candidates"
    candidates_dir.mkdir()
    (candidates_dir / "duplicate.md").write_text(
        "---\n"
        "id: cand-new123\n"
        "title: Legacy duplicate\n"
        "status: pending\n"
        "---\n\nLegacy.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(handlers, "_load_vaults", lambda: {"legacy": {"candidates_dir": str(candidates_dir)}})
    monkeypatch.setattr(handlers, "_load_routing_vaults", lambda: {})
    proxy = _install_proxy(monkeypatch)
    proxy.load_synthesis_candidates = lambda **kwargs: {
        "vault_id": "legacy",
        "count": 1,
        "candidates": [{**CANDIDATE, "vault_id": "legacy"}],
    }

    candidates = handlers.list_candidates(vault="legacy")

    assert len([candidate for candidate in candidates if candidate["id"] == "cand-new123"]) == 1
    assert next(candidate for candidate in candidates if candidate["id"] == "cand-new123")["title"] == CANDIDATE["title"]


def test_promote_ready_skips_dynamic_default_when_bdh_is_unavailable(monkeypatch):
    def unavailable():
        raise handlers.CurateIntegrationError(502, "bdh_unavailable", "BDH unavailable")

    monkeypatch.setattr(handlers, "_default_bdh_vault_id", unavailable)
    monkeypatch.setattr(handlers, "_candidates_dir", lambda vault=None: None)

    assert handlers.promote_ready() == []


def test_mutation_rejects_candidate_from_different_vault(monkeypatch):
    _install_proxy(monkeypatch)

    with pytest.raises(handlers.CurateIntegrationError) as error:
        handlers.approve("cand-new123", vault="different-vault")

    assert error.value.status_code == 409
    assert error.value.code == "vault_mismatch"

    with pytest.raises(handlers.CurateIntegrationError) as reject_error:
        handlers.reject("cand-new123", reason="wrong scope", vault="different-vault")

    assert reject_error.value.status_code == 409
    assert reject_error.value.code == "vault_mismatch"


def test_malformed_vault_is_rejected_without_a_bdh_proxy(monkeypatch):
    monkeypatch.setitem(sys.modules, "synthesis_activity_proxy", None)

    with pytest.raises(handlers.CurateIntegrationError) as error:
        handlers.list_candidates(vault="../invalid")

    assert error.value.status_code == 400
    assert error.value.code == "invalid_vault"

    with pytest.raises(handlers.CurateIntegrationError) as approve_error:
        handlers.approve("candidate-1", vault="../invalid")
    assert approve_error.value.status_code == 400
    assert approve_error.value.code == "invalid_vault"

    with pytest.raises(handlers.CurateIntegrationError) as reject_error:
        handlers.reject("candidate-1", reason="invalid scope", vault="../invalid")
    assert reject_error.value.status_code == 400
    assert reject_error.value.code == "invalid_vault"
