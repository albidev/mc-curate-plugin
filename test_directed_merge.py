import bdh_client
import handlers
import pytest


def test_curator_merge_intent_survives_safe_projection_and_queue_normalization():
    raw = {'candidate_id': 'cand-one', 'vault_id': 'core', 'extra': {
        'curator_merge_target': 'curate-decision-flow.md', 'curator_verdict': 'merge',
        'curator_note': 'Adds operational criteria', 'raw_transcript': 'private'},
        'transcript_sha256': 'private'}
    safe = bdh_client._safe_candidate(raw)
    normalized = handlers._normalize_session_synthesis_candidate(safe)
    assert normalized['curator_merge_target'] == 'curate-decision-flow.md'
    assert normalized['curator_verdict'] == 'merge'
    assert normalized['curator_note'] == 'Adds operational criteria'
    assert 'raw_transcript' not in safe['extra']
    assert 'transcript_sha256' not in safe


def test_merge_endpoint_forwards_owned_correlation_without_approve(monkeypatch):
    import endpoints
    raw = {'candidate_id': 'cand-one', 'synthesis_id': 'syn-owned', 'session_id': 'sess-owned',
           'vault_id': 'core', 'source': 'session_synthesis', 'status': 'pending_review'}
    calls = []
    def request(path, *, method='GET', payload=None):
        calls.append((path, method, payload))
        if path.startswith('/api/synthesis/candidates'):
            return {'vault_id': 'core', 'candidates': [raw]}
        assert path == '/api/synthesis/merge'
        return {'candidate_id': 'cand-one', 'status': 'merged', 'vault_id': 'core', 'note_path': 'wiki/a.md'}
    monkeypatch.setattr(bdh_client, '_request', request)
    monkeypatch.setattr(bdh_client.bdh_rejections, 'list_rejections', lambda: [])
    monkeypatch.setattr(handlers, 'can_curate', lambda vault: True)
    result = endpoints.mergeSynthesisCandidate({'candidate_id': 'cand-one', 'vault': 'core',
        'target_node_id': 'vault:wiki/a.md', 'candidate_revision': 'a' * 64, 'target_revision': 'b' * 64,
        'confirmed': True, 'synthesis_id': 'tampered', 'session_id': 'tampered'}, {})
    assert result['status'] == 'merged'
    merge = calls[-1]
    assert merge[2]['synthesis_id'] == 'syn-owned' and merge[2]['session_id'] == 'sess-owned'
    assert merge[2]['source'] == 'session_synthesis'
    assert [path for path, _, _ in calls if '/approve' in path or '/apply' in path] == []
    result = endpoints.mergeSynthesisCandidate({'candidate_id': 'cand-one', 'vault': 'core',
        'target_node_id': 'vault:wiki/a.md', 'candidate_revision': 'a' * 64, 'target_revision': 'b' * 64,
        'confirmed': True, 'reconciliation_id': 'rec-' + 'c' * 32, 'conflict_confirmed': True}, {})
    assert calls[-1][2]['reconciliation_id'] == 'rec-' + 'c' * 32
    assert calls[-1][2]['conflict_confirmed'] is True
    with pytest.raises(Exception, match='conflict_confirmed'):
        endpoints.mergeSynthesisCandidate({'candidate_id': 'cand-one', 'vault': 'core',
            'target_node_id': 'vault:wiki/a.md', 'candidate_revision': 'a' * 64, 'target_revision': 'b' * 64,
            'confirmed': True, 'reconciliation_id': 'rec-' + 'c' * 32, 'conflict_confirmed': 'true'}, {})


@pytest.mark.parametrize("status", ["pending_review", "pre_approved"])
def test_hinted_approve_fails_before_any_upstream_mutation(monkeypatch, status):
    candidate = {'candidate_id': 'cand-one', 'vault_id': 'core', 'status': status,
                 'extra': {'curator_merge_target': 'curate-decision-flow.md'}}
    calls = []
    class Proxy:
        def approve_synthesis_candidate(self, **kw): calls.append('approve')
        def apply_synthesis_candidate(self, **kw): calls.append('apply'); return {}
    monkeypatch.setattr(handlers, '_load_session_synthesis_candidate', lambda *a: (candidate, Proxy()))
    monkeypatch.setattr(handlers, 'can_curate', lambda v: True)
    with pytest.raises(handlers.CurateIntegrationError, match='Merge into') as error:
        handlers.approve('cand-one', 'core')
    assert error.value.status_code == 409 and calls == []


def test_merge_manifest_endpoints_are_authenticated_and_resolve_real_handlers():
    import json
    from pathlib import Path
    import endpoints
    manifest = json.loads(Path('manifest.json').read_text())
    expected = {'/synthesis/merge-targets': 'GET', '/synthesis/merge-preview': 'POST', '/synthesis/merge': 'POST',
                '/synthesis/reconcile': 'POST', '/synthesis/reconcile/status': 'GET'}
    for path, method in expected.items():
        ep = next((e for e in manifest['endpoints'] if e['path'] == path), None)
        assert ep is not None, path
        assert ep['method'] == method and ep['authRequired'] is True
        assert callable(getattr(endpoints, ep['handler']))
