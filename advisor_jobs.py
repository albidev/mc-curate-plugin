"""Curate advisor jobs — the plugin-side half of the AI opinion feature.

The telemetry process (where this plugin is loaded) cannot import Hermes'
model client, so each request spawns ``advisor_worker.py`` inside the Hermes
runtime and hands it a job file. The worker rewrites that file after every
candidate; the UI polls ``status`` to update cards live.

Configuration lives in the local ``curate-vaults.yaml`` (never committed):

    advisor:
      default: {provider: anthropic, model: claude-sonnet-5}
      concurrency: 4
    vaults:
      core:
        advisor: {provider: ollama-cloud, model: deepseek-v4.1-flash, description: ...}
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import advisor_prompt
import handlers
from handlers import CurateIntegrationError

_JOB_ID_RE = re.compile(r"^adv-[0-9a-f]{16}$")
_CANDIDATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_ACTIVE = ("queued", "running")
MAX_BATCH = 300
START_GRACE_S = 60
JOB_RETENTION_S = 7 * 24 * 3600
_WORKER = Path(__file__).resolve().parent / "advisor_worker.py"
_LAUNCH = (
    "import os,sys,runpy; sys.path.insert(0, sys.argv[1]); "
    "os.environ.setdefault('HERMES_HOME', str(__import__('hermes_constants').get_default_hermes_root())); "
    "import hermes_bootstrap; sys.argv = sys.argv[2:]; runpy.run_path(sys.argv[0], run_name='__main__')"
)
_PROCS: Dict[str, subprocess.Popen] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jobs_dir() -> Path:
    path = handlers.get_hermes_home() / "vault-brain" / "curate-advice" / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _settings() -> Dict[str, Any]:
    path = handlers._vaults_file()
    if not path.exists():
        return {}
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def advisor_config(vault: str) -> Dict[str, Any]:
    """Resolve provider/model for a vault: vault override, then ``advisor.default``."""
    settings = _settings()
    root = _dict(settings.get("advisor"))
    default = _dict(root.get("default"))
    vault_cfg = _dict(_dict(_dict(settings.get("vaults")).get(vault)).get("advisor"))
    provider = str(vault_cfg.get("provider") or default.get("provider") or "").strip()
    model = str(vault_cfg.get("model") or default.get("model") or "").strip()
    if not provider or not model:
        raise CurateIntegrationError(409, "advisor_not_configured",
                                     f"No advisor provider/model configured for vault {vault} in curate-vaults.yaml.")
    try:
        concurrency = int(vault_cfg.get("concurrency") or root.get("concurrency") or 4)
    except (TypeError, ValueError):
        concurrency = 4
    def _lines(key: str) -> List[str]:
        value = vault_cfg.get(key)
        return [" ".join(str(v).split()) for v in value if str(v).strip()] if isinstance(value, list) else []

    return {"provider": provider, "model": model, "concurrency": max(1, min(concurrency, 8)),
            "description": str(vault_cfg.get("description") or "").strip(),
            "signal": _lines("signal"), "noise": _lines("noise"),
            "hermes_bin": str(root.get("hermes_bin") or "").strip()}


def rejection_ledger(vault: str) -> Dict[str, Dict[str, Any]]:
    """Curate's local rejections for one vault, keyed by candidate id."""
    import bdh_rejections

    return {str(e.get("candidate_id")): {k: e.get(k) for k in ("reason", "rejected_at", "decided_via") if e.get(k)}
            for e in bdh_rejections.list_rejections() or []
            if isinstance(e, dict) and e.get("candidate_id") and str(e.get("vault_id") or "") == vault}


def _bdh_vault_root(vault: str) -> Path:
    import bdh_client

    with urllib.request.urlopen(f"{bdh_client._bdh_base_url()}/api/vaults", timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    for item in payload.get("vaults") or []:
        if item.get("id") == vault and item.get("path"):
            return Path(item["path"]).expanduser().resolve()
    raise CurateIntegrationError(404, "vault_not_found", f"BDH does not serve vault {vault}.")


def resolve_runtime(hermes_bin: str = "") -> Tuple[str, str]:
    """Return (python, hermes_agent_dir) of the installed Hermes runtime."""
    candidates = [hermes_bin, shutil.which("hermes") or "",
                  str(handlers.get_hermes_home() / "hermes-agent" / ".hermes" / "bin" / "hermes")]
    launcher = next((c for c in candidates if c and os.access(c, os.X_OK)), None)
    if not launcher:
        raise CurateIntegrationError(503, "hermes_runtime_unavailable",
                                     "Hermes launcher not found; set advisor.hermes_bin in curate-vaults.yaml.")
    result = subprocess.run([launcher, "--print-runtime-command"], capture_output=True, text=True, timeout=30)
    try:
        argv = json.loads(result.stdout)
        python = argv[0]
        agent_dir = re.search(r"sys\.path\.insert\(0, '([^']+)'\)", argv[-1]).group(1)  # type: ignore[union-attr]
    except (ValueError, IndexError, AttributeError, TypeError) as exc:
        raise CurateIntegrationError(503, "hermes_runtime_unavailable",
                                     f"Could not resolve the Hermes runtime from {launcher}.") from exc
    if not (os.access(python, os.X_OK) and Path(agent_dir, "hermes_bootstrap.py").is_file()):
        raise CurateIntegrationError(503, "hermes_runtime_unavailable", "Resolved Hermes runtime is incomplete.")
    return python, agent_dir


def _job_path(job_id: str) -> Path:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise CurateIntegrationError(400, "bad_request", "Invalid job id.")
    return jobs_dir() / f"{job_id}.json"


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reconcile(job: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """Mark a job failed when its worker died without finishing it."""
    if job.get("status") not in _ACTIVE:
        return job
    proc = _PROCS.get(job.get("job_id", ""))
    exited = proc is not None and proc.poll() is not None
    pid = job.get("pid") or job.get("spawn_pid")
    try:
        age = time.time() - datetime.fromisoformat(job["created_at"]).timestamp()
    except (KeyError, ValueError):
        age = 0
    dead = exited or (pid and not _pid_alive(pid)) or (not pid and age > START_GRACE_S)
    if not dead:
        return job
    latest = _read(path) or job  # the worker may have finished between reads
    if latest.get("status") not in _ACTIVE:
        return latest
    latest.update(status="failed", finished_at=_now(),
                  error=latest.get("error") or "Advisor worker exited before finishing; see curate-advice log.")
    for item in (latest.get("items") or {}).values():
        if item.get("state") in ("queued", "running"):
            item.update(state="error", error="worker exited")
    _write(path, latest)
    return latest


def _write(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _prune() -> None:
    cutoff = time.time() - JOB_RETENTION_S
    for path in jobs_dir().glob("adv-*.*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _active_jobs(vault: str) -> List[Dict[str, Any]]:
    out = []
    for path in jobs_dir().glob("adv-*.json"):
        job = _read(path)
        if job and job.get("vault") == vault and job.get("status") in _ACTIVE:
            job = _reconcile(job, path)
            if job.get("status") in _ACTIVE:
                out.append(job)
    return sorted(out, key=lambda j: j.get("created_at") or "")


def start(vault: Any, candidate_ids: Any) -> Dict[str, Any]:
    vault_id = handlers._validate_vault_id(vault)
    if not vault_id:
        raise CurateIntegrationError(400, "bad_request", "Missing vault.")
    if not isinstance(candidate_ids, list) or not candidate_ids:
        raise CurateIntegrationError(400, "bad_request", "candidate_ids must be a non-empty list.")
    ids = list(dict.fromkeys(str(c) for c in candidate_ids))
    if len(ids) > MAX_BATCH or not all(_CANDIDATE_ID_RE.fullmatch(c) for c in ids):
        raise CurateIntegrationError(400, "bad_request", f"Up to {MAX_BATCH} valid candidate ids are allowed.")
    if not handlers.can_curate(vault_id):
        raise CurateIntegrationError(403, "vault_not_curable", "Candidate mutations are disabled for this vault.")
    config = advisor_config(vault_id)
    vault_root = _bdh_vault_root(vault_id)
    candidates_dir = vault_root / ".bdh-candidates"

    in_flight = {cid for job in _active_jobs(vault_id) for cid, item in (job.get("items") or {}).items()
                 if item.get("state") in _ACTIVE}
    rejections = rejection_ledger(vault_id)
    order, skipped = [], {}
    for cid in ids:
        raw = _read(candidates_dir / f"{cid}.json")
        if raw is None or raw.get("candidate_id") != cid:
            skipped[cid] = "not_found"
        elif cid in rejections:
            skipped[cid] = "status_rejected"
        elif raw.get("status") not in advisor_prompt.ADVISABLE_STATUSES:
            skipped[cid] = f"status_{raw.get('status')}"
        elif cid in in_flight:
            skipped[cid] = "already_running"
        else:
            order.append(cid)
    if not order:
        raise CurateIntegrationError(409, "nothing_to_advise",
                                     "No requested candidate can be advised now (reviewed, missing, or already in progress).")

    python, agent_dir = resolve_runtime(config["hermes_bin"])
    _prune()
    import bdh_client

    job_id = f"adv-{secrets.token_hex(8)}"
    path = jobs_dir() / f"{job_id}.json"
    job = {
        "job_id": job_id, "vault": vault_id, "status": "queued", "created_at": _now(),
        "provider": config["provider"], "model": config["model"], "description": config["description"],
        "signal": config["signal"], "noise": config["noise"],
        "concurrency": config["concurrency"], "prompt_version": advisor_prompt.PROMPT_VERSION,
        "bdh_url": bdh_client._bdh_base_url(), "vault_root": str(vault_root), "candidates_dir": str(candidates_dir),
        "order": order, "total": len(order), "completed": 0,
        "items": {cid: {"state": "queued"} for cid in order}, "skipped": skipped,
        "rejections": rejections,
    }
    _write(path, job)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    log = open(jobs_dir() / f"{job_id}.log", "ab")
    try:
        proc = subprocess.Popen([python, "-I", "-c", _LAUNCH, agent_dir, str(_WORKER), str(path)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                                cwd=str(jobs_dir()), start_new_session=True)
    finally:
        log.close()
    _PROCS[job_id] = proc
    threading.Thread(target=proc.wait, name=f"curate-advisor-{job_id}", daemon=True).start()  # reap
    # The worker is now the job file's only writer; never write it from here again.
    return {**job, "spawn_pid": proc.pid}


def status(job_id: Any) -> Dict[str, Any]:
    path = _job_path(job_id)
    job = _read(path)
    if job is None:
        raise CurateIntegrationError(404, "not_found", "Advisor job not found.")
    return _reconcile(job, path)


def active(vault: Any) -> Dict[str, Any]:
    vault_id = handlers._validate_vault_id(vault)
    if not vault_id:
        raise CurateIntegrationError(400, "bad_request", "Missing vault.")
    return {"vault": vault_id, "jobs": _active_jobs(vault_id)}


def config_summary(vault: Any) -> Dict[str, Any]:
    vault_id = handlers._validate_vault_id(vault)
    if not vault_id:
        raise CurateIntegrationError(400, "bad_request", "Missing vault.")
    try:
        config = advisor_config(vault_id)
    except CurateIntegrationError as exc:
        return {"vault": vault_id, "configured": False, "detail": exc.message}
    return {"vault": vault_id, "configured": True, "provider": config["provider"], "model": config["model"]}


__all__ = ["start", "status", "active", "config_summary", "advisor_config", "resolve_runtime", "jobs_dir",
           "rejection_ledger"]
