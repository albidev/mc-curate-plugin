#!/usr/bin/env python3
"""Curate advisor worker: approve / merge / reject opinions for candidates.

Runs as a detached subprocess inside the Hermes runtime (spawned by
``advisor_jobs``), because the model call goes through Hermes'
``agent.auxiliary_client.call_llm`` provider resolution and credentials.

The job file is the live channel: this process is its only writer after the
spawn and rewrites it atomically after every state change, so the Curate UI
can poll it and flip each card as soon as its opinion lands. Each finished
opinion is also written into the candidate's ``extra.curator_*`` fields — the
same contract the scheduled curator review uses — so it survives reloads.

Read-only towards BDH: semantic shortlisting uses ``/api/query`` with
``learn: false`` (no Hebbian update, no neurogenesis).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    # Appended, never prepended: plugin modules must not shadow Hermes ones.
    sys.path.append(str(_HERE))

import advisor_prompt as prompt  # noqa: E402

MAX_NOTES = 6
EXCERPT_CHARS = 700
# BDH /api/query costs 4-14s even sequentially (measured 2026-10-03); a short
# timeout silently degrades to title matching and changes verdicts.
QUERY_TIMEOUT_S = 60
BDH_PARALLEL_QUERIES = 3  # BDH also serves live chats: never flood it
LLM_TIMEOUT_S = 90
LLM_ATTEMPTS = 2
_BDH_SLOTS = threading.BoundedSemaphore(BDH_PARALLEL_QUERIES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: Dict[str, Any], *, sort_keys: bool = False) -> None:
    tmp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _excerpt(path: Path, limit: int = EXCERPT_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    return " ".join(text.split())[:limit]


class Job:
    """Thread-safe view of the job file; this process is its only writer."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def update(self, **fields: Any) -> None:
        with self.lock:
            self.data.update(fields)
            self._flush()

    def item(self, cid: str, **fields: Any) -> None:
        with self.lock:
            entry = self.data["items"].setdefault(cid, {})
            entry.update(fields)
            items = self.data["items"].values()
            self.data["completed"] = sum(1 for i in items if i.get("state") in ("done", "error", "skipped"))
            self._flush()

    def _flush(self) -> None:
        self.data["updated_at"] = _now()
        atomic_write_json(self.path, self.data)


class Advisor:
    """One advisor run. Network/model seams are methods so tests can replace them."""

    def __init__(self, job: Job, complete: Optional[Callable[[List[Dict[str, str]]], str]] = None):
        self.job = job
        spec = job.data
        self.vault_id: str = spec["vault"]
        self.provider: str = spec["provider"]
        self.model: str = spec["model"]
        self.description: str = spec.get("description") or ""
        self.signal: List[str] = [str(s) for s in spec.get("signal") or []]
        self.noise: List[str] = [str(s) for s in spec.get("noise") or []]
        self.bdh_url: str = spec["bdh_url"].rstrip("/")
        self.candidates_dir = Path(spec["candidates_dir"])
        self.vault_root = Path(spec["vault_root"]).resolve()
        self._complete = complete or self._call_llm
        self._all: Optional[List[Dict[str, Any]]] = None

    # -- seams ---------------------------------------------------------------
    def _call_llm(self, messages: List[Dict[str, str]]) -> str:
        from agent.auxiliary_client import call_llm  # Hermes runtime only

        response = call_llm(provider=self.provider, model=self.model, messages=messages,
                            temperature=0, max_tokens=500, timeout=LLM_TIMEOUT_S)
        return response.choices[0].message.content or ""

    def _http(self, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = QUERY_TIMEOUT_S) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(f"{self.bdh_url}{path}", data=data, method="POST" if data else "GET",
                                         headers={"Content-Type": "application/json"})
        with _BDH_SLOTS, urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    # -- context -------------------------------------------------------------
    def _note_file(self, node_id: str, absolute: Optional[str] = None) -> Optional[Path]:
        candidate = Path(absolute) if absolute else self.vault_root / prompt.note_path_from_node_id(node_id)
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        return resolved if resolved.is_relative_to(self.vault_root) else None

    def semantic_notes(self, query: str) -> List[Dict[str, Any]]:
        payload = self._http("/api/query", {"query": query, "vault_id": self.vault_id, "learn": False})
        notes = []
        for note in (payload.get("activated_notes") or [])[:MAX_NOTES]:
            node_id = str(note.get("id") or "")
            if not node_id:
                continue
            path = self._note_file(node_id, note.get("path"))
            notes.append({"node_id": node_id, "title": note.get("title") or "", "score": note.get("score"),
                          "excerpt": _excerpt(path) if path else ""})
        return notes

    def title_notes(self, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
        words = sorted(set(re.findall(r"[A-Za-z0-9]{5,}", str(candidate.get("title") or ""))), key=len, reverse=True)[:3]
        seen, notes = set(), []
        for word in words:
            query = urllib.parse.urlencode({"candidate_id": candidate["candidate_id"], "vault_id": self.vault_id, "q": word})
            for target in (self._http(f"/api/synthesis/merge-targets?{query}").get("targets") or []):
                node_id = target.get("node_id")
                if node_id and node_id not in seen and len(notes) < MAX_NOTES:
                    seen.add(node_id)
                    path = self._note_file(node_id)
                    notes.append({"node_id": node_id, "title": target.get("title") or "", "score": None,
                                  "excerpt": _excerpt(path) if path else ""})
        return notes

    def shortlist(self, candidate: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
        query = f"{candidate.get('title') or ''}. {candidate.get('definition') or ''}"
        try:
            return self.semantic_notes(query), "semantic"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return self.title_notes(candidate), "title_fallback"

    def target_is_mergeable(self, candidate_id: str, node_id: str, title: str) -> bool:
        """Ask BDH's own merge-target list, so the hint resolves at merge time."""
        query = urllib.parse.urlencode({"candidate_id": candidate_id, "vault_id": self.vault_id, "q": title or node_id})
        targets = self._http(f"/api/synthesis/merge-targets?{query}").get("targets") or []
        return any(t.get("node_id") == node_id for t in targets)

    def all_candidates(self) -> List[Dict[str, Any]]:
        """Every candidate of the vault, read once per job (pending + Albi's past decisions).

        Curate rejections live in the local ledger, not in the candidate files; the
        job carries a snapshot of it (``rejections``) taken when it started.
        """
        if self._all is None:
            items = []
            for path in sorted(self.candidates_dir.glob("cand-*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(raw, dict):
                    items.append(raw)
            self._all = prompt.overlay_rejections(items, self.job.data.get("rejections") or {})
        return self._all

    def pending(self) -> List[Dict[str, Any]]:
        return [c for c in self.all_candidates() if c.get("status") in prompt.ADVISABLE_STATUSES]

    # -- one candidate ---------------------------------------------------------
    def advise(self, cid: str) -> None:
        started = time.monotonic()
        self.job.item(cid, state="running", started_at=_now())
        try:
            path = self.candidates_dir / f"{cid}.json"
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if candidate.get("candidate_id") != cid or (candidate.get("vault_id") and candidate["vault_id"] != self.vault_id):
                raise ValueError("candidate identity does not match this vault")
            if candidate.get("status") not in prompt.ADVISABLE_STATUSES:
                self.job.item(cid, state="skipped", error=f"status is {candidate.get('status')}")
                return
            notes, mode = self.shortlist(candidate)
            titles = {n["node_id"]: n["title"] for n in notes}
            messages = [
                {"role": "system", "content": prompt.SYSTEM_PROMPT},
                {"role": "user", "content": prompt.build_user_prompt(
                    vault_id=self.vault_id, description=self.description, candidate=candidate, notes=notes,
                    siblings=prompt.similar_pending(candidate, self.pending()), context_mode=mode,
                    signal=self.signal, noise=self.noise,
                    precedents=prompt.select_precedents(candidate, self.all_candidates()))},
            ]
            advice = None
            for attempt in range(LLM_ATTEMPTS):
                raw = self._complete(messages)
                try:
                    advice = prompt.parse_advice(raw, titles)
                    if advice["merge_target"] and not self.target_is_mergeable(cid, advice["merge_target"], titles[advice["merge_target"]]):
                        raise prompt.AdviceError(
                            f"{advice['merge_target']} cannot receive a merge in this vault. "
                            "Choose another EXISTING NOTE, or approve/reject.")
                    break
                except prompt.AdviceError as exc:
                    advice = None
                    if attempt + 1 >= LLM_ATTEMPTS:
                        raise
                    messages += [{"role": "assistant", "content": raw},
                                 {"role": "user", "content": f"Invalid answer: {exc} Reply again with only the JSON object."}]
            assert advice is not None
            target_title = titles.get(advice["merge_target"] or "", "")
            if not self.write_back(path, advice):
                self.job.item(cid, state="skipped", error="candidate was reviewed meanwhile")
                return
            self.job.item(cid, state="done", finished_at=_now(), elapsed_s=round(time.monotonic() - started, 1),
                          verdict=advice["verdict"], confidence=advice["confidence"], reason=advice["reason"],
                          merge_target=prompt.note_path_from_node_id(advice["merge_target"]) if advice["merge_target"] else None,
                          merge_target_title=target_title or None, context_mode=mode,
                          model=f"{self.provider}/{self.model}")
        except Exception as exc:  # noqa: BLE001 — one failed card must not stop the batch
            message = f"{type(exc).__name__}: {exc}"
            self.job.item(cid, state="error", finished_at=_now(), error=message[:400])
            print(f"[advisor] {cid} failed: {message}\n{traceback.format_exc()}", file=sys.stderr)

    def write_back(self, path: Path, advice: Dict[str, Any]) -> bool:
        """Persist the opinion in ``extra.curator_*`` (re-read right before writing)."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("status") not in prompt.ADVISABLE_STATUSES:
            return False
        extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
        extra.update({
            "curator_verdict": advice["verdict"],
            "curator_confidence": f"{advice['confidence']:.2f}",
            "curator_note": advice["reason"],
            "curator_model": f"{self.provider}/{self.model}",
            "curator_source": "on_demand",
            "curator_reviewed_at": _now(),
            "curator_prompt_version": prompt.PROMPT_VERSION,
        })
        if advice["merge_target"]:
            extra["curator_merge_target"] = prompt.note_path_from_node_id(advice["merge_target"])
        else:
            # A new opinion replaces the old one entirely: a stale target would
            # keep forcing the merge dialog on Approve.
            extra.pop("curator_merge_target", None)
        raw["extra"] = extra
        atomic_write_json(path, raw, sort_keys=True)
        return True


def run(job_path: Path, complete: Optional[Callable[[List[Dict[str, str]]], str]] = None) -> Dict[str, Any]:
    job = Job(job_path)
    job.update(status="running", pid=os.getpid(), started_at=_now())
    advisor = Advisor(job, complete)
    order = list(job.data.get("order") or [])
    with ThreadPoolExecutor(max_workers=max(1, int(job.data.get("concurrency") or 4))) as pool:
        list(pool.map(advisor.advise, order))
    items = job.data["items"]
    errors = sum(1 for cid in order if items.get(cid, {}).get("state") == "error")
    job.update(status="failed" if order and errors == len(order) else "done", finished_at=_now(), errors=errors)
    return job.data


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("usage: advisor_worker.py <job.json>", file=sys.stderr)
        return 2
    job_path = Path(argv[1])
    try:
        run(job_path)
    except Exception as exc:  # noqa: BLE001 — record the failure for the UI
        traceback.print_exc()
        try:
            job = Job(job_path)
            job.update(status="failed", finished_at=_now(), error=f"{type(exc).__name__}: {exc}"[:400])
        except Exception:  # noqa: BLE001
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
