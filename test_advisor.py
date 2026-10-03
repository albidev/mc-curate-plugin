"""Curate AI advisor: config routing, answer validation, worker write-back, dead-job reconcile.

Everything runs against the conftest sandbox HERMES_HOME and a temp vault;
the model and BDH are replaced through the worker's seams.
"""
import json
import os
from pathlib import Path

import pytest

import advisor_jobs
import advisor_prompt
import advisor_worker
import handlers


def _write_settings(text: str) -> None:
    path = handlers._vaults_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_unreadable_config_is_reported_not_silently_dropped(monkeypatch):
    import builtins
    import endpoints

    _write_settings("advisor:\n  default: {provider: p, model: m}\nvaults: [unclosed\n")
    monkeypatch.setattr(handlers, "list_vaults", lambda: [])  # BDH is not part of this contract
    payload = endpoints.listVaults({}, {})
    assert payload["config_error"] and "curate-vaults.yaml" in payload["config_error"]
    assert advisor_jobs.config_summary("core")["configured"] is False
    assert advisor_jobs.config_summary("core")["detail"] == payload["config_error"]

    _write_settings("advisor:\n  default: {provider: p, model: m}\n")
    real_import = builtins.__import__

    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("No module named 'yaml'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    assert "PyYAML" in (handlers.read_vaults_config()[1] or "")
    monkeypatch.undo()

    handlers._vaults_file().unlink()
    assert handlers.read_vaults_config() == ({}, None)  # no file: plain defaults, no warning


def test_vault_override_wins_and_other_vaults_inherit_default():
    _write_settings(
        "advisor:\n  default: {provider: anthropic, model: claude-sonnet-5}\n"
        "vaults:\n  core:\n    advisor: {provider: ollama-cloud, model: deepseek-v4.1-flash, signal: [real incidents]}\n"
        "  work:\n    advisor: {description: Client work}\n"
    )
    core = advisor_jobs.advisor_config("core")
    other = advisor_jobs.advisor_config("work")
    assert (core["provider"], core["model"]) == ("ollama-cloud", "deepseek-v4.1-flash")
    assert (other["provider"], other["model"]) == ("anthropic", "claude-sonnet-5")
    assert other["description"] == "Client work"
    assert core["signal"] == ["real incidents"] and other["signal"] == []  # profiles never leak across vaults

    _write_settings("vaults:\n  core: {}\n")
    with pytest.raises(handlers.CurateIntegrationError) as exc:
        advisor_jobs.advisor_config("core")
    assert exc.value.code == "advisor_not_configured"


def test_merge_target_must_come_from_the_shortlist():
    allowed = ["vault:wiki/concepts/a.md"]
    with pytest.raises(advisor_prompt.AdviceError):
        advisor_prompt.parse_advice('{"verdict":"merge","confidence":0.7,"merge_target":"wiki/invented.md","reason":"x"}', allowed)
    advice = advisor_prompt.parse_advice(
        'noise {"verdict":"reject","confidence":7,"merge_target":"vault:wiki/concepts/a.md","reason":"dup"}', allowed)
    assert advice["merge_target"] is None and advice["confidence"] == 1.0


def _candidate(cid, status="pending_review", extra=None):
    return {"candidate_id": cid, "vault_id": "core", "title": f"Title {cid}", "definition": "A definition.",
            "status": status, "confidence": "low", "extra": extra or {}, "provenance": {"source_notes": []}}


def test_worker_writes_opinions_live_and_never_touches_reviewed_candidates(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    cands = vault / ".bdh-candidates"
    cands.mkdir(parents=True)
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "wiki" / "concepts" / "a.md").write_text("---\nx: 1\n---\nExisting body.", encoding="utf-8")
    files = {
        "cand-merge": _candidate("cand-merge"),
        "cand-reject": _candidate("cand-reject", extra={"curator_verdict": "merge", "curator_merge_target": "old.md"}),
        "cand-applied": _candidate("cand-applied", status="applied"),
    }
    for cid, raw in files.items():
        (cands / f"{cid}.json").write_text(json.dumps(raw), encoding="utf-8")
    job_path = tmp_path / "adv-0000000000000000.json"
    job_path.write_text(json.dumps({
        "job_id": "adv-0000000000000000", "vault": "core", "status": "queued", "provider": "p", "model": "m",
        "bdh_url": "http://127.0.0.1:9", "vault_root": str(vault), "candidates_dir": str(cands),
        "concurrency": 2, "order": list(files), "items": {cid: {"state": "queued"} for cid in files},
        "owner": "Dana", "language": "it",
    }), encoding="utf-8")

    node = "vault:wiki/concepts/a.md"
    monkeypatch.setattr(advisor_worker.Advisor, "semantic_notes",
                        lambda self, q: [{"node_id": node, "title": "A", "score": 0.9, "excerpt": "Existing body."}])
    monkeypatch.setattr(advisor_worker.Advisor, "target_is_mergeable", lambda self, cid, n, t: n == node)
    answers = {
        "cand-merge": ['{"verdict":"merge","confidence":0.8,"merge_target":"vault:wiki/nope.md","reason":"r"}',
                       '{"verdict":"merge","confidence":0.8,"merge_target":"' + node + '","reason":"adds a case"}'],
        "cand-reject": ['{"verdict":"reject","confidence":0.9,"merge_target":null,"reason":"generic"}'],
    }

    seen = []

    def complete(messages):
        seen.append(messages)
        cid = next(c for c in answers if f"title: Title {c}\n" in messages[1]["content"])
        return answers[cid].pop(0)

    result = advisor_worker.run(job_path, complete)

    items = result["items"]
    assert result["status"] == "done" and result["completed"] == 3
    assert items["cand-merge"]["verdict"] == "merge" and items["cand-merge"]["merge_target"] == "wiki/concepts/a.md"
    assert items["cand-applied"]["state"] == "skipped"
    merged = json.loads((cands / "cand-merge.json").read_text())["extra"]
    rejected = json.loads((cands / "cand-reject.json").read_text())["extra"]
    applied = json.loads((cands / "cand-applied.json").read_text())
    assert merged["curator_merge_target"] == "wiki/concepts/a.md" and merged["curator_source"] == "on_demand"
    assert rejected["curator_verdict"] == "reject" and "curator_merge_target" not in rejected
    assert applied == files["cand-applied"]
    assert json.loads(job_path.read_text())["items"] == items  # the file the UI polls holds the same state
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert "the way Dana does" in system and "in Italian" in system and "PAST DECISIONS BY DANA" in user


def test_prompt_names_the_configured_owner_and_reason_language_only():
    _write_settings("advisor:\n  default: {provider: p, model: m}\n  owner: Dana\n"
                    "vaults:\n  core:\n    advisor: {language: Italian}\n  work: {}\n")
    core, work = advisor_jobs.advisor_config("core"), advisor_jobs.advisor_config("work")
    assert (core["owner"], core["language"]) == ("Dana", "Italian") and (work["owner"], work["language"]) == ("Dana", "")

    anonymous = advisor_prompt.system_prompt()
    assert "the vault owner's knowledge vault" in anonymous and "in English" in anonymous
    assert "Dana" not in anonymous and "Albi" not in anonymous
    assert "in Italian" in advisor_prompt.system_prompt("Dana", "it")
    assert "PAST DECISIONS BY THE VAULT OWNER" in advisor_prompt.build_user_prompt(
        vault_id="core", description="", candidate=_candidate("c1"), notes=[], siblings=[])


def test_dead_worker_marks_job_failed_instead_of_spinning_forever():
    path = advisor_jobs.jobs_dir() / "adv-1111111111111111.json"
    path.write_text(json.dumps({
        "job_id": "adv-1111111111111111", "vault": "core", "status": "running", "pid": 2 ** 22 + os.getpid(),
        "created_at": "2026-10-03T00:00:00+00:00", "items": {"cand-x": {"state": "running"}},
    }), encoding="utf-8")
    job = advisor_jobs.status("adv-1111111111111111")
    assert job["status"] == "failed" and job["items"]["cand-x"]["state"] == "error"
    assert json.loads(Path(path).read_text())["status"] == "failed"


def test_precedents_are_the_owners_informative_decisions_only():
    reviewed = [
        # The owner approved what the curator wanted to reject: an override, always informative.
        {**_candidate("cand-o1", "applied", {"curator_verdict": "reject", "curator_note": "generic"}), "title": "Vite Proxy 502 Diagnosis"},
        # Plain agreement: no information about the owner's bar.
        {**_candidate("cand-a1", "applied", {"curator_verdict": "approve"}), "title": "Vite Proxy Agreement"},
        # Rejection with a content reason.
        {**_candidate("cand-r1", "rejected"), "title": "Proxy UX Pattern", "rejection_reason": "pattern UX generico"},
        # Rejection justified by provenance: the model cannot see provenance, so it must not learn from it.
        {**_candidate("cand-r2", "rejected"), "title": "Proxy Probe", "rejection_reason": "probe artifact — manual reproduction run, not a daemon synthesis"},
        # Still pending: not a decision.
        {**_candidate("cand-p1"), "title": "Proxy Pending"},
    ]
    picked = advisor_prompt.select_precedents({"candidate_id": "cand-new", "title": "Vite Proxy Upstream", "definition": ""}, reviewed)
    assert [p["title"] for p in picked][0] == "Vite Proxy 502 Diagnosis"  # most related first
    assert {p["title"] for p in picked} == {"Vite Proxy 502 Diagnosis", "Proxy UX Pattern"}
    assert all(p["decision"] in ("approved", "rejected") for p in picked)

    many = [{**_candidate(f"cand-x{i}", "rejected"), "rejection_reason": "generic"} for i in range(10)]
    assert len(advisor_prompt.select_precedents({"candidate_id": "c", "title": "t"}, many, limit=6)) == 3  # never one-sided
