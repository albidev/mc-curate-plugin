"""Reconciliation never writes candidate/note state; only a validated assessment is registered."""
import json
from pathlib import Path

import pytest

import advisor_jobs
import handlers


def spec():
    return {
        'job_id': 'rcl-0000000000000001', 'status': 'queued', 'created_at': advisor_jobs._now(),
        'vault': 'core', 'candidate_id': 'cand-1', 'target_node_id': 'vault:rule.md',
        'candidate_revision': 'a' * 64, 'target_revision': 'b' * 64,
        'identity': {'candidate_id': 'cand-1', 'synthesis_id': 'syn-1', 'session_id': 'sess-1',
                     'source': 'session_synthesis', 'vault_id': 'core'},
        'preview': {'vault_id': 'core', 'candidate': {'candidate_id': 'cand-1', 'definition': 'Never reset the shared index.'},
                    'target': {'node_id': 'vault:rule.md', 'content': '# Shared index\nPreserve other writers staging.'}},
        'provider': 'p', 'model': 'm', 'language': 'en', 'bdh_url': 'http://127.0.0.1:1',
    }


def answer(classification='compatible'):
    return {'classification': classification, 'reason': 'Both protect concurrent staging.',
            'candidate_quote': 'Never reset the shared index.', 'target_quote': 'Preserve other writers staging.'}


def test_assessment_validates_exact_evidence_not_model_instructions():
    import reconcile_prompt
    preview = spec()['preview']
    assert reconcile_prompt.parse_assessment(json.dumps(answer()), preview)['classification'] == 'compatible'
    for field, value in [('candidate_quote', 'Invented quote'), ('target_quote', 'preserve other writers staging.'),
                         ('classification', 'approve'), ('reason', ''), ('candidate_quote', ' '),
                         ('reason', 'x' * 1001)]:
        with pytest.raises(ValueError):
            reconcile_prompt.parse_assessment(json.dumps({**answer(), field: value}), preview)


def test_worker_registers_opinion_only_and_never_auto_merges(tmp_path):
    import reconcile_worker
    path = tmp_path / 'job.json'
    job = spec()
    path.write_text(json.dumps(job))
    calls = []

    def register(payload):
        calls.append(payload)
        assert payload['candidate_revision'] == job['candidate_revision']
        assert payload['target_revision'] == job['target_revision']
        assert 'confirmed' not in payload and 'conflict_confirmed' not in payload
        assert payload['assessment']['provider'] == 'p' and payload['assessment']['model'] == 'm'
        return {'reconciliation_id': 'rec-' + 'c' * 32}

    result = reconcile_worker.run(path, complete=lambda messages: json.dumps(answer()), register=register)
    assert result['status'] == 'done' and result['reconciliation_id']
    assert len(calls) == 1
    path.write_text(json.dumps(job))
    result = reconcile_worker.run(path, complete=lambda messages: json.dumps({**answer(), 'target_quote': 'fake'}),
                                  register=register)
    assert result['status'] == 'failed' and not result.get('reconciliation_id')
    assert len(calls) == 1  # an invented quotation cannot create a durable assessment


def test_job_rejects_stale_preview_and_deduplicates_running_job(monkeypatch):
    import reconcile_jobs
    candidate = spec()['identity']
    preview = {**spec()['preview'], 'candidate_revision': 'a' * 64, 'target_revision': 'b' * 64,
               'conflict': {'required': True, 'signals': ['Never'], 'provenance_flag': True}}
    monkeypatch.setattr(handlers, '_merge_candidate', lambda cid, vault: (candidate, vault))
    monkeypatch.setattr(handlers, 'preview_merge', lambda *args: preview)
    monkeypatch.setattr(advisor_jobs, 'advisor_config', lambda _: {'provider': 'p', 'model': 'm', 'hermes_bin': '', 'language': ''})
    monkeypatch.setattr(advisor_jobs, 'resolve_runtime', lambda _: ('/python', '/agent'))
    spawned = []
    monkeypatch.setattr(reconcile_jobs, '_spawn', lambda path, config: spawned.append(path))
    request = {'vault': 'core', 'candidate_id': 'cand-1', 'target_node_id': 'vault:rule.md',
               'candidate_revision': 'a' * 64, 'target_revision': 'b' * 64, 'locale': 'it'}
    with pytest.raises(handlers.CurateIntegrationError) as exc:
        reconcile_jobs.start({**request, 'target_revision': 'c' * 64})
    assert exc.value.status_code == 409 and not spawned
    first = reconcile_jobs.start(request)
    second = reconcile_jobs.start(request)
    assert first['job_id'] == second['job_id'] and len(spawned) == 1
    assert 'preview' not in first and 'identity' not in first and 'bdh_url' not in first
    job = advisor_jobs._read(spawned[0])
    assert job is not None
    assert job['language'] == 'it'
    # Dead subprocess detection is a terminal error, not a permanently running badge.
    job['pid'] = 987654321
    advisor_jobs._write(spawned[0], job)
    monkeypatch.setattr(advisor_jobs, '_pid_alive', lambda _: False)
    assert reconcile_jobs.status(first['job_id'])['status'] == 'failed'


def test_bulk_preview_excludes_possible_conflicts_before_any_write(monkeypatch):
    import accept_jobs
    from types import SimpleNamespace
    preview = {'target': {'node_id': 'vault:rule.md', 'title': 'Rule', 'content': 'Full note'},
               'candidate': {'candidate_id': 'cand-1', 'definition': 'Never reset'},
               'conflict': {'required': True, 'signals': ['never'], 'provenance_flag': True}}
    monkeypatch.setattr(accept_jobs, '_client', lambda: SimpleNamespace(
        load_merge_targets=lambda *args: {'suggested_target_node_id': 'vault:rule.md'},
        preview_synthesis_merge=lambda *args: preview))
    row = accept_jobs._merge_preview('core', 'cand-1')
    assert row.get('blocked_code') == 'reconciliation_required'
    assert row.get('blocked') and 'merge' not in row


def test_live_slow_bootstrap_remains_queued_and_deduplicated(monkeypatch):
    import reconcile_jobs
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    job = spec()
    job['created_at'] = (datetime.now(timezone.utc) - timedelta(seconds=advisor_jobs.START_GRACE_S + 1)).isoformat()
    path = reconcile_jobs._path(job['job_id'])
    advisor_jobs._write(path, job)
    monkeypatch.setitem(advisor_jobs._PROCS, job['job_id'], SimpleNamespace(poll=lambda: None))
    assert reconcile_jobs.status(job['job_id'])['status'] == 'queued'
    assert advisor_jobs._read(path)['status'] == 'queued'
    monkeypatch.setattr(handlers, '_merge_candidate', lambda *args: (job['identity'], 'core'))
    monkeypatch.setattr(handlers, 'preview_merge', lambda *args: {**job['preview'],
        'candidate_revision': job['candidate_revision'], 'target_revision': job['target_revision'], 'conflict': {'required': True}})
    monkeypatch.setattr(advisor_jobs, 'advisor_config', lambda _: {'provider': 'p', 'model': 'm', 'hermes_bin': '', 'language': ''})
    monkeypatch.setattr(reconcile_jobs, '_spawn', lambda *args: pytest.fail('A live startup must not launch a duplicate worker'))
    same = reconcile_jobs.start({'candidate_id': job['candidate_id'], 'vault': 'core',
        'target_node_id': job['target_node_id'], 'candidate_revision': job['candidate_revision'], 'target_revision': job['target_revision']})
    assert same['job_id'] == job['job_id'] and same['status'] == 'queued'


def test_reaper_clears_process_tracking_and_marks_unfinished_jobs_failed(monkeypatch):
    import reconcile_jobs
    from types import SimpleNamespace
    job = spec(); path = reconcile_jobs._path(job['job_id'])
    advisor_jobs._write(path, job)
    proc = SimpleNamespace(wait=lambda: 1, poll=lambda: 1)
    monkeypatch.setitem(advisor_jobs._PROCS, job['job_id'], proc)
    reconcile_jobs._reap(proc, job['job_id'], path)
    assert job['job_id'] not in advisor_jobs._PROCS
    assert reconcile_jobs.status(job['job_id'])['status'] == 'failed'


def test_duplicate_json_keys_do_not_resolve_to_a_last_model_opinion():
    import reconcile_prompt
    raw = json.dumps(answer()).replace('"classification":', '"classification":"conflicting", "classification":', 1)
    with pytest.raises(ValueError, match='Duplicate'):
        reconcile_prompt.parse_assessment(raw, spec()['preview'])


@pytest.mark.parametrize('change', [
    {'vault_id': 'other'}, {'candidate': {'candidate_id': 'other', 'definition': 'Never reset'}},
    {'target': {'node_id': 'vault:other.md', 'content': 'Foreign note'}},
    {'candidate_revision': 42}, {'target': {'node_id': 'vault:rule.md', 'content': None}},
])
def test_start_rejects_malformed_or_foreign_full_text_before_model_spawn(monkeypatch, change):
    import reconcile_jobs
    job = spec()
    preview = {**job['preview'], 'candidate_revision': job['candidate_revision'],
               'target_revision': job['target_revision'], 'conflict': {'required': True}, **change}
    monkeypatch.setattr(handlers, '_merge_candidate', lambda *args: (job['identity'], 'core'))
    monkeypatch.setattr(handlers, 'preview_merge', lambda *args: preview)
    monkeypatch.setattr(advisor_jobs, 'advisor_config', lambda _: {'provider': 'p', 'model': 'm', 'hermes_bin': '', 'language': ''})
    monkeypatch.setattr(reconcile_jobs, '_spawn', lambda *args: pytest.fail('No model call for an invalid preview'))
    with pytest.raises(handlers.CurateIntegrationError) as exc:
        reconcile_jobs.start({'candidate_id': job['candidate_id'], 'vault': 'core',
            'target_node_id': job['target_node_id'], 'candidate_revision': job['candidate_revision'], 'target_revision': job['target_revision']})
    assert exc.value.status_code == 502


@pytest.mark.parametrize('exec_bootstrap', [False, True])
def test_actual_launcher_persists_pid_before_slow_bootstrap_and_survives_host_tracking_loss(tmp_path, monkeypatch, exec_bootstrap):
    import reconcile_jobs
    import sys
    import time
    from datetime import datetime, timedelta, timezone
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    ready = runtime / 'ready'; release = runtime / 'release'
    (runtime / 'hermes_bootstrap.py').write_text(
        'from pathlib import Path\nimport time\nroot=Path(__file__).parent\n'
        '(root/"ready").touch()\n'
        + ('import os,sys\nif not (root/"reexecuted").exists():\n'
           '    (root/"reexecuted").touch()\n    os.execv(sys.executable,[sys.executable,"-I",*sys.argv])\n' if exec_bootstrap else '')
        + 'deadline=time.monotonic()+30\n'
        'while not (root/"release").exists():\n'
        '    if time.monotonic()>deadline: raise TimeoutError("Fixture bootstrap not released")\n'
        '    time.sleep(0.01)\n')
    worker = runtime / 'worker.py'
    worker.write_text('import json,sys\nfrom pathlib import Path\np=Path(sys.argv[1])\n'
                      'j=json.loads(p.read_text()); j["status"]="done"; p.write_text(json.dumps(j))\n')
    job = spec()
    job['created_at'] = (datetime.now(timezone.utc) - timedelta(seconds=advisor_jobs.START_GRACE_S + 1)).isoformat()
    path = reconcile_jobs._path(job['job_id']); advisor_jobs._write(path, job)
    monkeypatch.setattr(advisor_jobs, 'resolve_runtime', lambda _: (sys.executable, str(runtime)))
    monkeypatch.setattr(reconcile_jobs, '_WORKER', worker)
    reconcile_jobs._spawn(path, {'hermes_bin': ''})
    proc = advisor_jobs._PROCS[job['job_id']]
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), path.with_suffix('.log').read_text()
        stored = advisor_jobs._read(path)
        assert stored is not None
        assert stored['spawn_pid'] == proc.pid and stored['process_lease'] is True
        # The new telemetry process has no Popen map, but the detached child is still alive.
        advisor_jobs._PROCS.pop(job['job_id'])
        assert reconcile_jobs.status(job['job_id'])['status'] == 'queued'
        monkeypatch.setattr(handlers, '_merge_candidate', lambda *args: (job['identity'], 'core'))
        monkeypatch.setattr(handlers, 'preview_merge', lambda *args: {**job['preview'],
            'candidate_revision': job['candidate_revision'], 'target_revision': job['target_revision'], 'conflict': {'required': True}})
        monkeypatch.setattr(advisor_jobs, 'advisor_config', lambda _: {'provider': 'p', 'model': 'm', 'hermes_bin': '', 'language': ''})
        monkeypatch.setattr(reconcile_jobs, '_spawn', lambda *args: pytest.fail('Host restart must not duplicate a live worker'))
        assert reconcile_jobs.start({'candidate_id': job['candidate_id'], 'vault': 'core',
            'target_node_id': job['target_node_id'], 'candidate_revision': job['candidate_revision'], 'target_revision': job['target_revision']})['job_id'] == job['job_id']
        release.touch(); proc.wait(timeout=30)
        finished = advisor_jobs._read(path)
        assert finished is not None and finished['status'] == 'done'
        if exec_bootstrap: assert (runtime / 'reexecuted').exists()
    finally:
        release.touch()
        if proc.poll() is None: proc.terminate()
        proc.wait(timeout=30)


def test_pid_reuse_cannot_strand_an_untracked_job(monkeypatch):
    import reconcile_jobs
    import os
    job = spec()
    job.update(status='running', spawn_pid=os.getpid(), process_lease=True)
    path = reconcile_jobs._path(job['job_id']); advisor_jobs._write(path, job)
    path.with_suffix('.lock').write_bytes(b'\0')  # Released lease of the old worker, not this unrelated process.
    monkeypatch.delitem(advisor_jobs._PROCS, job['job_id'], raising=False)
    assert reconcile_jobs.status(job['job_id'])['status'] == 'failed'
