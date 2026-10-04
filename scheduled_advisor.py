#!/usr/bin/env python3
"""Run Curate's existing Ask AI worker; only the operator applies its advice."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)

import advisor_jobs
import advisor_prompt
import handlers


def _state_dir() -> Path:
    return handlers.get_hermes_home() / 'vault-brain' / 'curate-advice'


def _in_flight(vault: str, preview: bool) -> set[str]:
    if preview:
        # status()/active() may reconcile dead workers. Inspection never writes.
        jobs = []
        for path in (_state_dir() / 'jobs').glob('adv-*.json'):
            job = json.loads(path.read_text())
            if job.get('vault') == vault and job.get('status') in ('queued', 'running'):
                jobs.append(job)
    else:
        jobs = advisor_jobs.active(vault)['jobs']
    return {cid for job in jobs for cid, item in job.get('items', {}).items()
            if item.get('state') in ('queued', 'running')}


def _candidates(vault: str, preview: bool) -> list[str]:
    root = advisor_jobs._bdh_vault_root(vault)
    directory = root / '.bdh-candidates'
    if not directory.exists():
        return []
    rejected = advisor_jobs.rejection_ledger(vault)
    busy = _in_flight(vault, preview)
    ids = []
    for path in sorted(directory.glob('*.json')):
        raw = json.loads(path.read_text())
        cid = raw.get('candidate_id')
        if cid != path.stem or cid in rejected or cid in busy:
            continue
        if raw.get('vault_id') and raw['vault_id'] != vault:
            raise ValueError(f'Candidate {cid} belongs to another vault')
        if raw.get('status') not in advisor_prompt.ADVISABLE_STATUSES:
            continue
        if (raw.get('extra') or {}).get('curator_verdict'):
            continue
        ids.append(cid)
    return ids


def build_plan(preview: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    results, errors = [], []
    for row in handlers.list_vaults():
        vault = row['id']
        if row.get('error'):
            errors.append(f"{vault}: {row['error']}")
            continue
        # Storage/review-only vaults are intentional, not failed candidate queues.
        if not row.get('writable') or row.get('read_only') or not row.get('candidate_enabled'):
            continue
        try:
            if not handlers.can_curate(vault):
                continue
            config = advisor_jobs.advisor_config(vault)
            ids = _candidates(vault, preview)
            results.append({'vault': vault, 'label': row.get('label') or vault,
                            'planned': min(len(ids), advisor_jobs.MAX_BATCH),
                            'remaining': max(0, len(ids) - advisor_jobs.MAX_BATCH),
                            'ids': ids[:advisor_jobs.MAX_BATCH],
                            'provider': config['provider'], 'model': config['model'],
                            'status': 'planned', 'link': f'/curate?vault={vault}'})
        except Exception as exc:
            errors.append(f'{vault}: {type(exc).__name__}: {exc}')
    return results, errors


def _wait(job_id: str, deadline: float) -> dict[str, Any]:
    while True:
        job = advisor_jobs.status(job_id)
        if job.get('status') in ('done', 'failed'):
            return job
        if time.monotonic() >= deadline:
            raise TimeoutError(f'Advisor {job_id} still running after the bounded wait')
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def execute(results: list[dict[str, Any]], deadline: float) -> None:
    # One vault at a time: each worker already uses its configured concurrency.
    for result in results:
        if not result['ids']:
            result['status'] = 'none'
            continue
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError('Run budget exhausted before starting this vault')
            job = advisor_jobs.start(result['vault'], result['ids'], locale='it', source='scheduled')
            result['job_id'] = job['job_id']
            final = _wait(job['job_id'], deadline)
            items = list(final.get('items', {}).values())
            result['counts'] = dict(Counter(item.get('verdict') for item in items if item.get('state') == 'done'))
            result['done'] = sum(item.get('state') == 'done' for item in items)
            result['skipped'] = sum(item.get('state') == 'skipped' for item in items)
            result['errors'] = sum(item.get('state') == 'error' for item in items)
            result['status'] = final['status']
            if final['status'] == 'failed' or result['errors']:
                result['error'] = final.get('error') or f"{result['errors']} candidati non valutati"
        except handlers.CurateIntegrationError as exc:
            if exc.code == 'nothing_to_advise':
                result['status'] = 'none'  # Human/manual job won the race.
            else:
                result.update(status='failed', errors=1, error=exc.message)
        except Exception as exc:
            result.update(status='failed', errors=1, error=f'{type(exc).__name__}: {exc}')


def format_report(results: list[dict[str, Any]], errors: list[str]) -> str:
    changed = [r for r in results if r.get('done') or r.get('error')]
    if not changed and not errors:
        return ''
    lines = ['**Curate — Ask AI**', 'Solo suggerimenti: nessun candidato approvato, rifiutato o applicato automaticamente.']
    for result in changed:
        counts = result.get('counts', {})
        lines.append(f"- **{result['label']}** — approve: {counts.get('approve', 0)}, "
                     f"merge: {counts.get('merge', 0)}, reject: {counts.get('reject', 0)}, "
                     f"errori: {result.get('errors', 0)}. [Apri Curate]({result['link']})")
        if result.get('error'):
            lines.append(f"  Errore: {result['error']}")
        if result.get('remaining'):
            lines.append(f"  Restano {result['remaining']} candidati per il prossimo passaggio.")
    lines.extend(f'- Errore: {error}' for error in errors)
    return '\n'.join(lines) + '\n'


def _persist(results: list[dict[str, Any]], errors: list[str], code: int) -> None:
    directory = _state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'scheduled-summary.json'
    temporary = directory / f'.scheduled-summary.{os.getpid()}.tmp'
    payload = {'run_at': datetime.now(timezone.utc).isoformat(), 'results': results,
               'config_errors': errors, 'exit_code': code}
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(target)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Inspect eligible candidates without writes')
    parser.add_argument('--list', action='store_true', help='Same read-only inspection in JSON')
    args = parser.parse_args(argv)
    if args.dry_run or args.list:
        try:
            results, errors = build_plan(preview=True)
        except Exception as exc:
            results, errors = [], [f'{type(exc).__name__}: {exc}']
        print(json.dumps({'vaults': results, 'config_errors': errors}, ensure_ascii=False, indent=2))
        return int(bool(errors))
    directory = _state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'scheduled.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # The existing runner will publish the result.
        deadline = time.monotonic() + 2700
        try:
            results, errors = build_plan()
            execute(results, deadline)
            code = int(bool(errors) or any(r.get('error') for r in results))
            _persist(results, errors, code)
            sys.stdout.write(format_report(results, errors))
            return code
        except Exception as exc:
            print(f'Curate Ask AI — errore: {type(exc).__name__}: {exc}')
            return 1


if __name__ == '__main__':
    raise SystemExit(run())
