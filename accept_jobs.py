"""Accept AI suggestions in bulk — the human half of the Curate advisor.

Flow: ``plan`` reads the queue's current advice server-side (plus a merge preview
for every merge suggestion) so the review panel shows exactly what will happen;
``start`` applies the rows the human ticked as one background job in this
process, one candidate at a time; the UI polls ``status``.

Guarantees, each tied to a contract that already exists:
- A row is applied only if the advice is still the one the human saw (``advice_token``);
  a re-run of the advisor in between turns the row ``stale``, never a different action.
- A merge sends the candidate/target revisions of the preview the human saw, exactly
  like the single-merge dialog. The only revision change tolerated is the one this
  very batch made, by merging an earlier row into the same note.
- A rejection taken from a suggestion is recorded with ``decided_via="ai_accepted"``,
  so the advisor never learns from its own words (see ``advisor_prompt.precedent_of``).
- Every vault write makes BDH's vault watcher rebuild the graph, which can stall BDH
  for a minute or two. BDH calls are retried with backoff; directed merge is
  idempotent for identical revisions, approve/apply short-circuits once applied.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import advisor_jobs
import advisor_prompt
import handlers
from handlers import CurateIntegrationError

_JOB_ID_RE = re.compile(r"^acc-[0-9a-f]{16}$")
_CANDIDATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE = ("queued", "running")
MAX_ITEMS = 300
PRESELECT_CONFIDENCE = 0.8
PLAN_PARALLEL = 3
EXCERPT_CHARS = 600
BDH_RETRY_S = 150.0
_ORDER = {"reject": 0, "approve": 1, "merge": 2}
_THREADS: Dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- advice ------------------------------------------------------------------

def _extra(candidate: Dict[str, Any]) -> Dict[str, Any]:
    value = candidate.get("extra")
    return value if isinstance(value, dict) else {}


def advice_token(candidate: Dict[str, Any]) -> str:
    """Identity of the stored advice: any re-run or edit of the opinion changes it."""
    extra = _extra(candidate)
    payload = {k: str(extra.get(k) or "") for k in (
        "curator_verdict", "curator_merge_target", "curator_note", "curator_reviewed_at", "curator_model")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _confidence(extra: Dict[str, Any]) -> Optional[float]:
    try:
        value = float(extra.get("curator_confidence") or "")
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1.0 else None


def _body_excerpt(content: str) -> str:
    """Start of the target note without frontmatter: what the note is about."""
    text = content or ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    text = text.strip()
    return text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else "")


# -- BDH calls with backoff ----------------------------------------------------

def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    message = str(getattr(exc, "message", "") or exc).lower()
    return status in (502, 503, 504) or any(s in message for s in ("timed out", "timeout", "unavailable", "refused"))


def with_bdh_retry(call: Callable[[], Any], budget_s: float = BDH_RETRY_S,
                   sleep: Callable[[float], None] = time.sleep) -> Any:
    deadline = time.monotonic() + budget_s
    delay = 2.0
    while True:
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — classified below
            if not _retryable(exc) or time.monotonic() + delay > deadline:
                raise
            sleep(delay)
            delay = min(delay * 2, 20.0)


def _client():
    return handlers._require_bdh_client()


def _snapshot(vault: str) -> Dict[str, Dict[str, Any]]:
    """Pending candidates of a vault as Curate sees them (ledger rejections removed)."""
    data = with_bdh_retry(lambda: _client().load_synthesis_candidates(vault_id=vault), budget_s=30)
    return {str(c.get("candidate_id")): c for c in data.get("candidates") or [] if isinstance(c, dict)}


# -- plan ----------------------------------------------------------------------

def _validate(vault: Any) -> str:
    vault_id = handlers._validate_vault_id(vault)
    if not vault_id:
        raise CurateIntegrationError(400, "bad_request", "Missing vault.")
    if not handlers.can_curate(vault_id):
        raise CurateIntegrationError(403, "vault_not_curable", "Candidate mutations are disabled for this vault.")
    return vault_id


def _ids(values: Any) -> List[str]:
    if not isinstance(values, list) or not values:
        raise CurateIntegrationError(400, "bad_request", "candidate_ids must be a non-empty list.")
    ids = list(dict.fromkeys(str(v) for v in values))
    if len(ids) > MAX_ITEMS or not all(_CANDIDATE_ID_RE.fullmatch(c) for c in ids):
        raise CurateIntegrationError(400, "bad_request", f"Up to {MAX_ITEMS} valid candidate ids are allowed.")
    return ids


def _merge_preview(vault: str, cid: str) -> Dict[str, Any]:
    client = _client()
    targets = with_bdh_retry(lambda: client.load_merge_targets(cid, vault, ""), budget_s=30)
    node = targets.get("suggested_target_node_id")
    if not node:
        return {"blocked": targets.get("suggested_target_error") or "The suggested note is not a writable note of this vault."}
    preview = with_bdh_retry(lambda: client.preview_synthesis_merge(cid, vault, node), budget_s=30)
    target = preview.get("target") or {}
    if target.get("node_id") != node or (preview.get("candidate") or {}).get("candidate_id") != cid:
        return {"blocked": "BDH returned a preview for a different candidate or note."}
    if (preview.get("conflict") or {}).get("required"):
        return {"blocked": "Possible conflicting evidence: open this candidate and reconcile it individually before merging.",
                "blocked_code": "reconciliation_required"}
    return {"merge": {
        "target_node_id": node, "title": str(target.get("title") or ""), "note_path": str(target.get("note_path") or ""),
        "target_excerpt": _body_excerpt(str(target.get("content") or "")),
        "claim": str((preview.get("candidate") or {}).get("definition") or ""),
        "candidate_revision": preview.get("candidate_revision"), "target_revision": preview.get("target_revision"),
    }}


def plan(vault: Any, candidate_ids: Any) -> Dict[str, Any]:
    """Rows for the review panel: current advice + merge previews, nothing written."""
    vault_id = _validate(vault)
    ids = _ids(candidate_ids)
    snapshot = _snapshot(vault_id)
    rows: List[Dict[str, Any]] = []
    for cid in ids:
        candidate = snapshot.get(cid)
        if candidate is None or candidate.get("status") not in advisor_prompt.ADVISABLE_STATUSES:
            continue  # decided meanwhile, or not reviewable: nothing to accept
        extra = _extra(candidate)
        verdict = str(extra.get("curator_verdict") or "")
        if verdict not in advisor_prompt.VERDICTS:
            continue
        confidence = _confidence(extra)
        source = "on_demand" if extra.get("curator_source") == "on_demand" else "scheduled"
        row = {
            "id": cid, "title": str(candidate.get("title") or cid), "definition": str(candidate.get("definition") or "")[:600],
            "verdict": verdict, "confidence": confidence, "reason": str(extra.get("curator_note") or ""),
            "source": source, "model": str(extra.get("curator_model") or ""),
            "reviewed_at": str(extra.get("curator_reviewed_at") or ""), "advice_token": advice_token(candidate),
            # Scheduled-cron opinions come from an older model run: shown, never pre-ticked.
            "preselected": source == "on_demand" and confidence is not None and confidence >= PRESELECT_CONFIDENCE,
        }
        if verdict == "approve" and extra.get("curator_merge_target"):
            row["blocked"] = "Carries a merge target: open it and merge, or ask the AI again."
        rows.append(row)

    merges = [row for row in rows if row["verdict"] == "merge" and not row.get("blocked")]
    def _attach(row: Dict[str, Any]) -> None:
        try:
            row.update(_merge_preview(vault_id, row["id"]))
        except Exception as exc:  # noqa: BLE001 — one unreachable preview must not hide the rest
            row["blocked"] = f"Preview unavailable: {getattr(exc, 'message', None) or exc}"[:300]
    with ThreadPoolExecutor(max_workers=PLAN_PARALLEL) as pool:
        list(pool.map(_attach, merges))
    for row in rows:
        if row.get("blocked"):
            row["preselected"] = False
    rows.sort(key=lambda r: (_ORDER[r["verdict"]], -(r["confidence"] or 0.0), r["title"].casefold()))
    return {"vault": vault_id, "threshold": PRESELECT_CONFIDENCE, "rows": rows, "active": _active_jobs(vault_id)}


# -- jobs ----------------------------------------------------------------------

def _job_path(job_id: Any) -> Path:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise CurateIntegrationError(400, "bad_request", "Invalid job id.")
    return advisor_jobs.jobs_dir() / f"{job_id}.json"


def _read(path: Path) -> Optional[Dict[str, Any]]:
    return advisor_jobs._read(path)


def _write(path: Path, payload: Dict[str, Any]) -> None:
    advisor_jobs._write(path, payload)


def _reconcile(job: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """A job is run by a thread of the process that started it; if that is gone, it failed."""
    if job.get("status") not in _ACTIVE:
        return job
    thread = _THREADS.get(str(job.get("job_id")))
    owner_alive = job.get("pid") == os.getpid() and thread is not None and thread.is_alive()
    if owner_alive or (job.get("pid") != os.getpid() and advisor_jobs._pid_alive(job.get("pid"))):
        return job
    latest = _read(path) or job
    if latest.get("status") not in _ACTIVE:
        return latest
    latest.update(status="failed", finished_at=_now(),
                  error="The Curate service restarted while applying; rows still queued were not applied.")
    for item in (latest.get("items") or {}).values():
        if item.get("state") in _ACTIVE:
            item.update(state="error", error="not applied: service restarted")
    _write(path, latest)
    return latest


def _active_jobs(vault: str) -> List[Dict[str, Any]]:
    out = []
    for path in advisor_jobs.jobs_dir().glob("acc-*.json"):
        job = _read(path)
        if job and job.get("vault") == vault and job.get("status") in _ACTIVE:
            job = _reconcile(job, path)
            if job.get("status") in _ACTIVE:
                out.append(job)
    return sorted(out, key=lambda j: j.get("created_at") or "")


def _prune() -> None:
    cutoff = time.time() - advisor_jobs.JOB_RETENTION_S
    for path in advisor_jobs.jobs_dir().glob("acc-*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _spec(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurateIntegrationError(400, "bad_request", "Each item must be an object.")
    cid, verdict, token = raw.get("id"), raw.get("verdict"), raw.get("advice_token")
    if not isinstance(cid, str) or not _CANDIDATE_ID_RE.fullmatch(cid) or verdict not in advisor_prompt.VERDICTS \
            or not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{16}", token):
        raise CurateIntegrationError(400, "bad_request", "Each item needs id, verdict and advice_token.")
    spec: Dict[str, Any] = {"id": cid, "verdict": verdict, "advice_token": token}
    if verdict == "merge":
        merge: Dict[str, Any] = raw["merge"] if isinstance(raw.get("merge"), dict) else {}
        node, crev, trev = merge.get("target_node_id"), merge.get("candidate_revision"), merge.get("target_revision")
        if not isinstance(node, str) or not node or not all(isinstance(r, str) and _REVISION_RE.fullmatch(r) for r in (crev, trev)):
            raise CurateIntegrationError(400, "bad_request", f"Merge item {cid} needs the previewed target and revisions.")
        spec["merge"] = {"target_node_id": node, "candidate_revision": crev, "target_revision": trev}
    return spec


def start(vault: Any, items: Any) -> Dict[str, Any]:
    vault_id = _validate(vault)
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
        raise CurateIntegrationError(400, "bad_request", f"items must be a list of 1-{MAX_ITEMS} rows.")
    specs: Dict[str, Dict[str, Any]] = {}
    for raw in items:
        spec = _spec(raw)
        specs[spec["id"]] = spec
    with _LOCK:
        if _active_jobs(vault_id):
            raise CurateIntegrationError(409, "accept_running", "Another batch is still being applied in this vault.")
        _prune()
        # Rejects first (local, instant), then approves, then merges grouped by note.
        order = sorted(specs, key=lambda c: (_ORDER[specs[c]["verdict"]],
                                             specs[c].get("merge", {}).get("target_node_id", ""), c))
        job_id = f"acc-{secrets.token_hex(8)}"
        path = advisor_jobs.jobs_dir() / f"{job_id}.json"
        job = {"job_id": job_id, "vault": vault_id, "status": "queued", "created_at": _now(), "pid": os.getpid(),
               "order": order, "total": len(order), "completed": 0, "specs": specs,
               "items": {cid: {"state": "queued", "verdict": specs[cid]["verdict"]} for cid in order}}
        _write(path, job)
        thread = threading.Thread(target=run, args=(path,), name=f"curate-accept-{job_id}", daemon=True)
        _THREADS[job_id] = thread
        thread.start()
    return _public(job)


class _Stale(Exception):
    """The row no longer matches what the human reviewed; nothing was written."""


class Applier:
    """Applies one job's rows in order. ``ops`` is the handlers seam (tests replace it)."""

    def __init__(self, job: Dict[str, Any], path: Path, ops: Any = handlers, sleep: Callable[[float], None] = time.sleep):
        self.job, self.path, self.ops, self.sleep = job, path, ops, sleep
        self.vault: str = job["vault"]
        # target_node_id -> (revision the human saw, revision after this batch's own merge)
        self.rebased: Dict[str, Tuple[str, str]] = {}

    def save(self, cid: str, **fields: Any) -> None:
        item = self.job["items"].setdefault(cid, {})
        item.update(fields)
        if fields.get("state") not in _ACTIVE:
            self.job["completed"] = sum(1 for i in self.job["items"].values() if i.get("state") not in _ACTIVE)
        _write(self.path, self.job)

    def retry(self, call: Callable[[], Any]) -> Any:
        return with_bdh_retry(call, sleep=self.sleep)

    def current(self, cid: str) -> Dict[str, Any]:
        candidate = self.retry(lambda: self.ops.get_synthesis_candidate(cid, self.vault))
        if candidate is None:
            raise _Stale("Already decided (or no longer in this vault).")
        if candidate.get("status") not in advisor_prompt.ADVISABLE_STATUSES:
            raise _Stale(f"No longer pending ({candidate.get('status')}).")
        if advice_token(candidate) != self.job["specs"][cid]["advice_token"]:
            raise _Stale("The AI suggestion changed after you reviewed it.")
        return candidate

    def apply(self, cid: str) -> Dict[str, Any]:
        spec = self.job["specs"][cid]
        candidate = self.current(cid)
        verdict = spec["verdict"]
        if verdict == "reject":
            reason = " ".join(str(_extra(candidate).get("curator_note") or "").split()) or "Rejected from an AI suggestion."
            self.retry(lambda: self.ops.reject(cid, reason, self.vault, None, decided_via="ai_accepted"))
            return {}
        if verdict == "approve":
            return self.approve(cid)
        return self.merge(cid, spec["merge"])

    def approve(self, cid: str) -> Dict[str, Any]:
        """Approve + apply, keeping BDH's operation_id so the row can be undone.

        Each attempt re-reads the candidate: if a timed-out attempt already approved
        it, the retry goes straight to apply, which BDH answers idempotently.
        """
        def attempt() -> Dict[str, Any]:
            candidate = self.ops.get_synthesis_candidate(cid, self.vault)
            if candidate is None:
                raise _Stale("Already decided (or no longer in this vault).")
            correlation = {"candidate_id": cid, "synthesis_id": str(candidate.get("synthesis_id") or ""),
                           "session_id": str(candidate.get("session_id") or ""), "vault_id": self.vault,
                           "source": str(candidate.get("source") or "session_synthesis")}
            if candidate.get("status") in advisor_prompt.ADVISABLE_STATUSES:
                self.ops.approve_synthesis_candidate(**correlation)
            return self.ops.apply_synthesis_candidate(**correlation) or {}
        result = self.retry(attempt)
        return {"note_path": str(result.get("note_path") or ""), "operation_id": str(result.get("operation_id") or "")}

    def merge(self, cid: str, merge: Dict[str, str]) -> Dict[str, Any]:
        node, crev, trev = merge["target_node_id"], merge["candidate_revision"], merge["target_revision"]
        seen_now = self.rebased.get(node)
        if seen_now and seen_now[0] == trev:
            trev = seen_now[1]  # only this batch touched the note since the human saw it
        try:
            result = self.retry(lambda: self.ops.merge_candidate(cid, self.vault, node, crev, trev, True))
        except CurateIntegrationError as exc:
            if exc.status_code == 409 and "changed" in exc.message.lower():
                raise _Stale("The candidate or the target note changed since the preview.") from exc
            raise
        self._rebase_followers(cid, node, merge["target_revision"])
        return {"note_path": result.get("note_path") or "", "operation_id": result.get("operation_id") or "",
                "noop": result.get("status") == "noop"}

    def _rebase_followers(self, done_cid: str, node: str, seen: str) -> None:
        """After merging into ``node``, learn its new revision from the next row merging there.

        Read right after our own merge, in the same thread: a hand edit landing in
        that window would be taken as ours, because BDH's merge response does not
        carry the note's new revision. Any later edit still fails the row as stale.
        """
        order = self.job["order"]
        for cid in order[order.index(done_cid) + 1:]:
            follower = self.job["specs"][cid].get("merge") or {}
            if follower.get("target_node_id") != node or follower.get("target_revision") != seen:
                continue
            try:
                preview = self.retry(lambda: self.ops.preview_merge(cid, self.vault, node))
            except Exception:  # noqa: BLE001 — the follower will simply fail as stale
                return
            if preview.get("candidate_revision") == follower.get("candidate_revision"):
                self.rebased[node] = (seen, str(preview.get("target_revision") or ""))
            return

    def run(self) -> Dict[str, Any]:
        self.job.update(status="running", started_at=_now())
        _write(self.path, self.job)
        for cid in list(self.job["order"]):
            self.save(cid, state="running", started_at=_now())
            try:
                outcome = self.apply(cid)
                self.save(cid, state="done", finished_at=_now(), **outcome)
            except _Stale as exc:
                self.save(cid, state="stale", finished_at=_now(), error=str(exc))
            except Exception as exc:  # noqa: BLE001 — one failed row must not stop the batch
                message = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
                self.save(cid, state="error", finished_at=_now(), error=str(message)[:400])
        states = [i.get("state") for i in self.job["items"].values()]
        self.job.update(status="done", finished_at=_now(),
                        summary={s: states.count(s) for s in ("done", "stale", "error")})
        _write(self.path, self.job)
        return self.job


def run(path: Path) -> None:
    job = _read(path)
    if not job:
        return
    try:
        Applier(job, path).run()
    except Exception as exc:  # noqa: BLE001 — surface, never leave the job "running"
        job.update(status="failed", finished_at=_now(), error=f"{type(exc).__name__}: {exc}"[:400])
        _write(path, job)


def status(job_id: Any) -> Dict[str, Any]:
    path = _job_path(job_id)
    job = _read(path)
    if job is None:
        raise CurateIntegrationError(404, "not_found", "Accept job not found.")
    return _public(_reconcile(job, path))


def active(vault: Any) -> Dict[str, Any]:
    vault_id = handlers._validate_vault_id(vault)
    if not vault_id:
        raise CurateIntegrationError(400, "bad_request", "Missing vault.")
    return {"vault": vault_id, "jobs": [_public(j) for j in _active_jobs(vault_id)]}


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in job.items() if k != "specs"}


__all__ = ["plan", "start", "status", "active", "advice_token", "with_bdh_retry", "Applier"]
