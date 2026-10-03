"""Accepting AI suggestions in bulk: the guarantees, not the plumbing.

Handlers are replaced by an in-memory fake with the same contract BDH enforces
(revision-checked merges); the rejection ledger is the real one, in the conftest
sandbox HERMES_HOME.
"""
import hashlib

import pytest

import accept_jobs
import advisor_jobs
import advisor_prompt
import bdh_rejections
from handlers import CurateIntegrationError


def _rev(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _cand(cid, verdict, note="why", target=None, reviewed="2026-10-03T10:00:00+00:00"):
    extra = {"curator_verdict": verdict, "curator_note": note, "curator_confidence": "0.90",
             "curator_source": "on_demand", "curator_reviewed_at": reviewed, "curator_model": "m"}
    if target:
        extra["curator_merge_target"] = target
    return {"candidate_id": cid, "vault_id": "core", "title": f"T {cid}", "definition": f"claim {cid}",
            "status": "pending_review", "source": "session_synthesis", "extra": extra}


class FakeOps:
    """BDH as Curate sees it: merges are refused unless the target revision matches."""

    def __init__(self, candidates, notes):
        self.candidates = {c["candidate_id"]: c for c in candidates}
        self.notes = dict(notes)  # node_id -> content
        self.calls = []
        self.flaky = 0

    def get_synthesis_candidate(self, cid, vault):
        c = self.candidates.get(cid)
        return None if c is None or c["status"] == "rejected" else c

    def reject(self, cid, reason, vault, filename, decided_via=""):
        self.calls.append(("reject", cid))
        bdh_rejections.record_rejection(candidate_id=cid, vault_id=vault, reason=reason, decided_via=decided_via)
        self.candidates[cid]["status"] = "rejected"

    def approve_synthesis_candidate(self, candidate_id, synthesis_id, session_id, vault_id, source):
        self.calls.append(("approve", candidate_id))
        self.candidates[candidate_id]["status"] = "approved"
        return {"status": "approved"}

    def apply_synthesis_candidate(self, candidate_id, synthesis_id, session_id, vault_id, source):
        self.candidates[candidate_id]["status"] = "created"
        return {"status": "created", "note_path": f"wiki/{candidate_id}.md", "operation_id": f"op-{candidate_id}"}

    def preview_merge(self, cid, vault, node):
        return {"candidate_revision": _rev(self.candidates[cid]["definition"]), "target_revision": _rev(self.notes[node])}

    def merge_candidate(self, cid, vault, node, crev, trev, confirmed):
        if self.flaky:
            self.flaky -= 1
            raise CurateIntegrationError(502, "bdh_unavailable", "BDH unavailable: timeout: timed out")
        if crev != _rev(self.candidates[cid]["definition"]) or trev != _rev(self.notes[node]):
            raise CurateIntegrationError(409, "invalid_vault", "BDH returned HTTP 409: Candidate or target changed; reload")
        self.calls.append(("merge", cid, node))
        self.notes[node] += f"\n- {self.candidates[cid]['definition']}"
        self.candidates[cid]["status"] = "applied"
        return {"status": "merged", "note_path": node, "operation_id": f"op-{cid}"}


def _job(ops, rows, tmp_path):
    specs = {}
    for cid, verdict in rows:
        c = ops.candidates[cid]
        spec = {"id": cid, "verdict": verdict, "advice_token": accept_jobs.advice_token(c)}
        if verdict == "merge":
            node = c["extra"]["curator_merge_target"]
            spec["merge"] = {"target_node_id": node, **ops.preview_merge(cid, "core", node)}
        specs[cid] = accept_jobs._spec(spec)
    order = sorted(specs, key=lambda c: (accept_jobs._ORDER[specs[c]["verdict"]],
                                         specs[c].get("merge", {}).get("target_node_id", ""), c))
    job = {"job_id": "acc-0000000000000001", "vault": "core", "status": "queued", "order": order,
           "total": len(order), "completed": 0, "specs": specs, "items": {c: {"state": "queued"} for c in order}}
    return accept_jobs.Applier(job, tmp_path / "job.json", ops=ops, sleep=lambda s: None)


def test_a_suggestion_changed_after_review_is_never_applied(tmp_path):
    ops = FakeOps([_cand("c1", "reject"), _cand("c2", "approve")], {})
    applier = _job(ops, [("c1", "reject"), ("c2", "approve")], tmp_path)
    ops.candidates["c1"]["extra"]["curator_verdict"] = "merge"  # advisor re-ran meanwhile
    job = applier.run()
    assert job["items"]["c1"]["state"] == "stale"
    assert job["items"]["c2"]["state"] == "done"
    assert ops.calls == [("approve", "c2")]


def test_accepted_rejections_never_become_precedents_but_human_ones_do(tmp_path):
    ops = FakeOps([_cand("c1", "reject", note="model words")], {})
    _job(ops, [("c1", "reject")], tmp_path).run()
    bdh_rejections.record_rejection(candidate_id="h1", vault_id="core", reason="Albi: too generic for us")
    raw = [_cand("c1", "reject"), _cand("h1", "approve")]
    seen = advisor_prompt.overlay_rejections(raw, advisor_jobs.rejection_ledger("core"))
    precedents = advisor_prompt.select_precedents(_cand("x", "reject"), seen)
    assert [p["why"] for p in precedents] == [
        "overruled the curator, who wanted to approve it. Reason: Albi: too generic for us"]
    assert all(c["status"] == "rejected" for c in seen)  # both are decided, neither is an open sibling


def test_merges_into_one_note_chain_only_on_this_batchs_own_changes(tmp_path):
    ops = FakeOps([_cand(c, "merge", target="vault:hub.md") for c in ("m1", "m2", "m3")],
                  {"vault:hub.md": "hub", "vault:other.md": "other"})
    applier = _job(ops, [("m1", "merge"), ("m2", "merge"), ("m3", "merge")], tmp_path)
    job = applier.run()
    assert [i["state"] for i in job["items"].values()] == ["done", "done", "done"]
    assert ops.notes["vault:hub.md"].count("- claim") == 3

    ops = FakeOps([_cand(c, "merge", target="vault:hub.md") for c in ("m1", "m2")], {"vault:hub.md": "hub"})
    applier = _job(ops, [("m1", "merge"), ("m2", "merge")], tmp_path)
    ops.notes["vault:hub.md"] += "\nedited by hand after review"
    job = applier.run()
    assert [job["items"][c]["state"] for c in ("m1", "m2")] == ["stale", "stale"]

    ops = FakeOps([_cand(c, "merge", target="vault:hub.md") for c in ("m1", "m2")], {"vault:hub.md": "hub"})
    applier = _job(ops, [("m1", "merge"), ("m2", "merge")], tmp_path)
    real_preview = ops.preview_merge

    def edited_between_the_two_merges(*args):
        result = real_preview(*args)
        ops.notes["vault:hub.md"] += "\nedited by hand"
        return result
    ops.preview_merge = edited_between_the_two_merges
    job = applier.run()
    assert job["items"]["m1"]["state"] == "done"
    assert job["items"]["m2"]["state"] == "stale"  # a change nobody reviewed is not merged over


def test_bdh_stall_after_a_write_is_retried_not_failed(tmp_path):
    ops = FakeOps([_cand("m1", "merge", target="vault:hub.md")], {"vault:hub.md": "hub"})
    ops.flaky = 3
    job = _job(ops, [("m1", "merge")], tmp_path).run()
    assert job["items"]["m1"]["state"] == "done" and job["summary"]["done"] == 1


def test_merge_rows_need_the_previewed_revisions():
    with pytest.raises(CurateIntegrationError):
        accept_jobs._spec({"id": "c1", "verdict": "merge", "advice_token": "0" * 16,
                           "merge": {"target_node_id": "vault:a.md"}})
