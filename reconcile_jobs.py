"""Sidecar jobs for a pair-specific AI comparison; no merge or candidate writes."""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time

import advisor_jobs
import bdh_client
import curate_merge_validation as merge_validation
import handlers
import reconcile_prompt
import reconcile_lease

_WORKER = Path(__file__).with_name('reconcile_worker.py')
_LAUNCHER = Path(__file__).with_name('reconcile_launcher.py')
_ID = re.compile(r'rcl-[0-9a-f]{16}')
_LOCK = threading.Lock()
_PUBLIC = ('job_id', 'status', 'vault', 'candidate_id', 'target_node_id', 'candidate_revision', 'target_revision',
           'created_at', 'finished_at', 'error', 'assessment', 'reconciliation_id')


def jobs_dir() -> Path:
    path = handlers.get_hermes_home() / 'vault-brain' / 'curate-reconciliation' / 'jobs'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _public(job: dict) -> dict:
    return {key: job[key] for key in _PUBLIC if key in job}


def _path(job_id: str) -> Path:
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        raise handlers.CurateIntegrationError(400, 'bad_request', 'Invalid reconciliation job id.')
    return jobs_dir() / (job_id + '.json')


def _spawn(path: Path, config: dict) -> None:
    python, agent_dir = advisor_jobs.resolve_runtime(config['hermes_bin'])
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV')}
    env['HERMES_HOME'] = str(handlers.get_hermes_home())
    with path.with_suffix('.log').open('ab') as log:
        proc = subprocess.Popen([python, '-I', str(_LAUNCHER), agent_dir, str(_WORKER), str(path)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, cwd=path.parent, start_new_session=True)
    job_id = path.stem
    advisor_jobs._PROCS[job_id] = proc
    threading.Thread(target=_reap, args=(proc, job_id, path), name='curate-reconcile-' + job_id, daemon=True).start()


def _reap(proc, job_id: str, path: Path) -> None:
    proc.wait()
    with _LOCK:
        try:
            job = advisor_jobs._read(path)
            if job is not None:
                _reconcile(job, path)  # An exit before the worker's first write is a failure too.
        finally:
            advisor_jobs._PROCS.pop(job_id, None)


def _reconcile(job: dict, path: Path) -> dict:
    proc = advisor_jobs._PROCS.get(job.get('job_id'))
    if proc is not None:
        if proc.poll() is None:
            # Bootstrap can legitimately exceed the advisor's untracked-worker grace.
            return advisor_jobs._read(path) or job
        return advisor_jobs._reconcile(job, path)
    if job.get('status') in ('queued', 'running') and job.get('process_lease') is True:
        if reconcile_lease.held(path):
            return advisor_jobs._read(path) or job
        latest = advisor_jobs._read(path) or job
        if latest.get('status') in ('queued', 'running'):
            latest.update(status='failed', finished_at=advisor_jobs._now(),
                          error='Reconciliation worker exited before finishing; see its job log.')
            advisor_jobs._write(path, latest)
        return latest
    return advisor_jobs._reconcile(job, path)


def start(body: dict) -> dict:
    # Use the same strict identity syntax as the merge endpoint; don't repair client tokens.
    cid, vault = merge_validation.identity(body)
    node = merge_validation.field(body, 'target_node_id')
    revisions = {k: merge_validation.field(body, k, r'[0-9a-f]{64}')
                 for k in ('candidate_revision', 'target_revision')}
    candidate, vault = handlers._merge_candidate(cid, vault)
    preview = handlers.preview_merge(cid, vault, node)
    valid = isinstance(preview, dict) and preview.get('vault_id') == vault
    if valid:
        pc, pt, conflict = preview.get('candidate'), preview.get('target'), preview.get('conflict')
        valid = (isinstance(pc, dict) and pc.get('candidate_id') == cid and isinstance(pc.get('definition'), str)
            and isinstance(pt, dict) and pt.get('node_id') == node and isinstance(pt.get('content'), str)
            and isinstance(conflict, dict) and isinstance(conflict.get('required'), bool)
            and all(isinstance(preview.get(k), str) and re.fullmatch(r'[0-9a-f]{64}', preview[k]) for k in revisions))
    if not valid:
        raise handlers.CurateIntegrationError(502, 'invalid_preview', 'BDH returned a malformed or foreign merge preview. No analysis was started.')
    if any(preview[k] != value for k, value in revisions.items()):
        raise handlers.CurateIntegrationError(409, 'stale_preview', 'Candidate or note changed; reload the preview before analysis.')
    if not (preview.get('conflict') or {}).get('required'):
        raise handlers.CurateIntegrationError(409, 'reconciliation_not_required', 'This preview has no possible-conflict warning.')
    config = advisor_jobs.advisor_config(vault)
    language = str(config['language'] or (body.get('locale') if body.get('locale') in ('it', 'en') else 'en'))
    try:
        reconcile_prompt.messages(preview, language)  # fail oversized FULL context before spawning, never truncate
    except ValueError as exc:
        raise handlers.CurateIntegrationError(413, 'context_too_large', str(exc)) from exc
    identity = {k: candidate[k] for k in ('candidate_id', 'synthesis_id', 'session_id', 'source', 'vault_id')}
    with _LOCK:
        cutoff = time.time() - advisor_jobs.JOB_RETENTION_S
        for path in jobs_dir().glob('rcl-*.json'):
            job = advisor_jobs._read(path)
            if not job:
                continue
            if job.get('status') in ('queued', 'running'):
                job = _reconcile(job, path)
                if job.get('status') in ('queued', 'running') and all(job.get(k) == v for k, v in
                    {'vault': vault, 'candidate_id': cid, 'target_node_id': node, **revisions}.items()):
                    return _public(job)
            elif path.stat().st_mtime < cutoff:
                path.unlink()
                path.with_suffix('.log').unlink(missing_ok=True)
                path.with_suffix('.lock').unlink(missing_ok=True)
        job_id = 'rcl-' + secrets.token_hex(8)
        path = _path(job_id)
        job = {'job_id': job_id, 'status': 'queued', 'created_at': advisor_jobs._now(),
               'vault': vault, 'candidate_id': cid, 'target_node_id': node, **revisions,
               'identity': identity, 'preview': preview, 'language': language,
               'provider': config['provider'], 'model': config['model'], 'bdh_url': bdh_client._bdh_base_url()}
        advisor_jobs._write(path, job)
        try:
            _spawn(path, config)
        except Exception as exc:
            job.update(status='failed', error=str(exc)[:1000], finished_at=advisor_jobs._now())
            advisor_jobs._write(path, job)
            raise
        return _public(job)


def status(job_id: str) -> dict:
    path = _path(job_id)
    with _LOCK:
        job = advisor_jobs._read(path)
        if job is None:
            raise handlers.CurateIntegrationError(404, 'not_found', 'Reconciliation job not found.')
        return _public(_reconcile(job, path))
