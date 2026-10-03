"""Rendered Curate flow, isolated API fixtures (run with Playwright Python).
CURATE_UI_TEST_URL points to a Vite harness rendering the real CurateRoute.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

CANDIDATE = {'id': 'cand-one', 'source': 'session_synthesis', 'vault_id': 'core',
    'title': 'Merge vs Reject Heuristics', 'body': 'Adds precise operational criteria.',
    'status': 'pending_review', 'curator_merge_target': 'curate-decision-flow.md'}
TARGET = {'node_id': 'vault:wiki/concepts/curate-decision-flow.md', 'title': 'Curate Decision Flow',
          'note_path': 'wiki/concepts/curate-decision-flow.md'}


def run():
    with sync_playwright() as pw:
        launch = {'headless': True}
        if executable := os.environ.get('CURATE_BROWSER_EXECUTABLE'):
            launch['executable_path'] = executable
        browser = pw.chromium.launch(**launch)
        page = browser.new_page()
        errors, writes, previews = [], [], []
        mode = {'value': 'normal'}
        page.on('pageerror', lambda error: errors.append(str(error)))
        def api(route):
            path = urlparse(route.request.url).path
            body = route.request.post_data_json if route.request.method == 'POST' else None
            status = 200
            if path.endswith('/candidates/vaults'):
                data = {'vaults': [{'id': 'core', 'label': 'Core', 'writable': True, 'candidate_enabled': True,
                                    'candidate_count': 1, 'pending_count': 1, 'reviewed_count': 0}], 'default_vault': 'core'}
            elif path.endswith('/curate/advise/active'): data = {'jobs': [], 'advisor': {'configured': True,'model':'fixture-model','provider':'fixture-provider'}}
            elif path.endswith('/curate/accept/active'): data = {'jobs': []}
            elif path.endswith('/candidates/clustered'): data = {'clusters': []}
            elif path.endswith('/candidates'): data = {'candidates': [CANDIDATE], 'vault': 'core'}
            elif path.endswith('/merge-targets'):
                assert path == '/api/local/synthesis/merge-targets', path
                data = {'vault_id': 'core', 'targets': [TARGET], 'suggested_target_node_id': None if mode['value'] == 'missing' else TARGET['node_id'],
                        'suggested_target_error': 'Suggested target is missing; select a note explicitly.' if mode['value'] == 'missing' else None, 'total_count': 1, 'has_more': False}
            elif path.endswith('/merge-preview'):
                assert path == '/api/local/synthesis/merge-preview', path
                previews.append(body)
                assert body['target_node_id'] == TARGET['node_id']
                data = {'vault_id': 'core', 'candidate': {'candidate_id': 'cand-one', 'title': CANDIDATE['title'], 'definition': CANDIDATE['body']},
                        'target': {**TARGET, 'content': '# Existing note\nOriginal criteria.'},
                        'candidate_revision': 'a' * 64, 'target_revision': 'b' * 64}
            elif path.endswith('/merge'):
                assert path == '/api/local/synthesis/merge', path
                writes.append(body)
                data = {'candidate_id': 'cand-one', 'vault_id': 'core', 'status': 'merged', 'applied': True,
                        'note_path': TARGET['note_path'], 'operation_id': 'op-isolated', 'idempotent': False}
            else:
                raise AssertionError(f'Unexpected request {path}: {body}')
            if path.endswith('/merge') and mode['value'] == 'stale':
                status, data = 409, {'error': 'Candidate or target changed; reload and review the preview before confirming'}
            route.fulfill(status=status, content_type='application/json', body=json.dumps(data))
        page.route('**/api/local/**', api)
        page.goto(os.environ.get('CURATE_UI_TEST_URL', 'http://127.0.0.1:5269/?vault=core'))
        try:
            expect(page.get_by_role('heading', name=CANDIDATE['title'], exact=True)).to_be_visible(timeout=7000)
        except AssertionError:
            print('RENDER_ERRORS', errors)
            print('BODY', page.locator('body').inner_text()[:1800])
            raise
        page.get_by_title('Merge into existing note', exact=True).click(timeout=7000)
        dialog = page.get_by_role('dialog', name='Merge into existing note')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text('Original criteria.', exact=False)).to_be_visible()
        expect(dialog.get_by_text(CANDIDATE['body'], exact=True)).to_be_visible()
        assert writes == [], 'Opening/selecting/previewing must not mutate'
        dialog.get_by_role('button', name='Confirm merge', exact=True).click()
        expect(dialog).not_to_be_visible()
        assert len(writes) == 1
        assert writes[0]['confirmed'] is True
        assert writes[0]['target_node_id'] == TARGET['node_id']
        assert writes[0]['candidate_revision'] == 'a' * 64 and writes[0]['target_revision'] == 'b' * 64
        assert not errors, errors
        print('PASS: real Curate card → suggested target → read-only preview → one confirmed merge; no page errors')
        def reopen(value='normal', detail=False):
            mode['value'] = value
            writes.clear()
            previews.clear()
            page.reload()
            expect(page.get_by_role('heading', name=CANDIDATE['title'], exact=True)).to_be_visible()
            if detail:
                page.get_by_role('heading', name=CANDIDATE['title'], exact=True).click()
                page.get_by_role('button', name='Merge into…', exact=True).click()
            else:
                page.get_by_title('Merge into existing note', exact=True).click()
            return page.get_by_role('dialog', name='Merge into existing note')

        dialog = reopen()
        expect(dialog.get_by_text('Original criteria.', exact=False)).to_be_visible()
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        assert writes == []
        print('PASS cancel/Escape: no mutation')

        dialog = reopen('missing')
        expect(dialog.get_by_text('Suggested target is missing; select a note explicitly.')).to_be_visible()
        expect(dialog.get_by_role('button', name='Confirm merge')).to_be_disabled()
        assert previews == [] and writes == []
        dialog.get_by_role('textbox').fill('Curate')
        dialog.get_by_role('button', name='Curate Decision Flow', exact=False).click()
        expect(dialog.get_by_role('button', name='Confirm merge')).to_be_enabled()
        assert writes == []
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        print('PASS missing hint: bounded search and explicit selection; no arbitrary target')

        dialog = reopen('stale')
        expect(dialog.get_by_role('button', name='Confirm merge')).to_be_enabled()
        dialog.get_by_role('button', name='Confirm merge').click()
        expect(dialog.get_by_role('alert')).to_contain_text('Candidate or target changed')
        expect(dialog.get_by_role('button', name='Confirm merge')).to_be_disabled()
        dialog.get_by_role('button', name='Reload preview').click()
        expect(dialog.get_by_role('button', name='Confirm merge')).to_be_enabled()
        assert len(writes) == 1 and len(previews) == 2
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        print('PASS stale 409: fresh preview required; no automatic retry/Approve')

        page.set_viewport_size({'width': 390, 'height': 844})
        dialog = reopen(detail=True)
        expect(dialog.get_by_role('button', name='Confirm merge')).to_be_enabled()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        dialog.get_by_role('button', name='Confirm merge').evaluate('(el) => { el.click(); el.click(); }')
        expect(dialog).not_to_be_visible()
        assert len(writes) == 1
        assert not errors, errors
        print('PASS mobile detail: no horizontal overflow; double-click sends one merge')
        browser.close()

if __name__ == '__main__': run()
