"""Scheduled advice reuses the worker without turning opinions into decisions."""
import json
from pathlib import Path

import pytest

import advisor_jobs
import advisor_worker
import handlers
import scheduled_advisor as sa


def setup_vault(tmp_path, monkeypatch, candidates):
    home = tmp_path / 'home'
    vault = tmp_path / 'vault'
    directory = vault / '.bdh-candidates'
    directory.mkdir(parents=True)
    monkeypatch.setattr(handlers, 'get_hermes_home', lambda: home)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(handlers, 'list_vaults', lambda: [
        {'id': 'core', 'writable': True, 'candidate_enabled': True},
        {'id': 'readonly', 'writable': False, 'candidate_enabled': False},
        {'id': 'storage', 'writable': True, 'candidate_enabled': False},
    ])
    monkeypatch.setattr(handlers, 'can_curate', lambda v: v == 'core')
    monkeypatch.setattr(advisor_jobs, 'advisor_config', lambda v: {'provider': 'fixture', 'model': 'fixture'})
    monkeypatch.setattr(advisor_jobs, '_bdh_vault_root', lambda v: vault)
    monkeypatch.setattr(advisor_jobs, 'rejection_ledger', lambda v: {'cand-rejected': {'reason': 'human rejection'}})
    monkeypatch.setattr(advisor_jobs, 'active', lambda v: {'jobs': []})
    for cid, status, extra in candidates:
        (directory / f'{cid}.json').write_text(json.dumps({
            'candidate_id': cid, 'vault_id': 'core', 'title': cid,
            'definition': 'A concrete lesson about the fixture.',
            'status': status, 'extra': extra,
        }))
    return home, vault, directory


def test_preview_skips_readonly_storage_reviewed_rejected_and_advised(tmp_path, monkeypatch, capsys):
    home, vault, directory = setup_vault(tmp_path, monkeypatch, [
        ('cand-new', 'pending_review', {}),
        ('cand-pre', 'pre_approved', {}),
        ('cand-advised', 'pending_review', {'curator_verdict': 'merge'}),
        ('cand-reviewed', 'applied', {}),
        ('cand-rejected', 'pending_review', {}),
        ('cand-running', 'pending_review', {}),
    ])
    jobs = home / 'vault-brain/curate-advice/jobs'
    jobs.mkdir(parents=True)
    (jobs / 'adv-live.json').write_text(json.dumps({
        'vault': 'core', 'status': 'running', 'items': {'cand-running': {'state': 'running'}},
    }))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert sa.run(['--list']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['config_errors'] == []
    assert [v['vault'] for v in result['vaults']] == ['core']
    assert result['vaults'][0]['planned'] == 2
    after = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert after == before


@pytest.mark.parametrize('model_fails', [False, True])
def test_terminal_worker_outcome_is_reported_and_status_never_changes(tmp_path, monkeypatch, capsys, model_fails):
    home, vault, directory = setup_vault(tmp_path, monkeypatch, [('cand-new', 'pending_review', {})])
    monkeypatch.setattr(advisor_worker.Advisor, 'semantic_notes', lambda self, q: [])

    def complete(messages):
        if model_fails:
            raise RuntimeError('fixture provider failure')
        return '{"verdict":"approve","confidence":0.9,"merge_target":null,"reason":"Concrete lesson"}'

    def start(vault_id, ids, locale=None, source=None):
        job = {
            'job_id': 'adv-0000000000000000', 'vault': vault_id, 'status': 'queued',
            'provider': 'fixture', 'model': 'fixture', 'bdh_url': 'http://127.0.0.1:9',
            'vault_root': str(vault), 'candidates_dir': str(directory), 'concurrency': 1,
            'order': ids, 'items': {cid: {'state': 'queued'} for cid in ids},
            'source': source, 'language': locale,
        }
        path = advisor_jobs.jobs_dir() / f"{job['job_id']}.json"
        path.write_text(json.dumps(job))
        advisor_worker.run(path, complete=complete)
        return job

    monkeypatch.setattr(advisor_jobs, 'start', start)
    code = sa.run([])
    report = capsys.readouterr().out
    assert code == int(model_fails)
    assert 'Core' in report or 'core' in report
    assert ('errori: 1' if model_fails else 'approve: 1') in report
    candidate = json.loads((directory / 'cand-new.json').read_text())
    assert candidate['status'] == 'pending_review'
    if not model_fails:
        assert candidate['extra']['curator_source'] == 'scheduled'
        assert candidate['extra']['curator_verdict'] == 'approve'
        # Advice arriving after selection must survive a scheduled write-back.
        path = directory / 'cand-new.json'
        candidate['extra'].update(curator_verdict='reject', curator_source='on_demand')
        path.write_text(json.dumps(candidate))
        before = path.read_bytes()
        job = advisor_worker.Job(advisor_jobs.jobs_dir() / 'adv-0000000000000000.json')
        assert not advisor_worker.Advisor(job, complete).write_back(path, {
            'verdict': 'approve', 'confidence': 0.9, 'reason': 'Late reply', 'merge_target': None,
        })
        assert path.read_bytes() == before
        # A second invocation must not rerun or notify about existing advice.
        assert sa.run([]) == 0
        assert capsys.readouterr().out == ''
