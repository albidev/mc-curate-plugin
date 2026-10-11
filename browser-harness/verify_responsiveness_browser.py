"""Real Curate UI, isolated API fixtures. No request reaches a live API.
Run with Playwright against the browser-harness (CURATE_UI_TEST_URL).
"""
import asyncio
import json
import os
from urllib.parse import urlparse
from playwright.async_api import async_playwright, expect

CANDIDATE = {'id': 'cand-responsive', 'source': 'session_synthesis', 'vault_id': 'core',
             'title': 'Responsiveness fixture', 'body': 'Isolated candidate.', 'status': 'pending_review'}

async def run():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=os.environ.get(
            'CURATE_BROWSER_EXECUTABLE', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
        page = await browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        cluster_release = asyncio.Event()
        candidate_release = asyncio.Event()
        candidate_release.set()
        state = {'candidate': dict(CANDIDATE), 'fail_candidates': False, 'hold_candidates': False, 'clustered': False}
        second = {**CANDIDATE, 'id': 'cand-second', 'title': 'Second fixture'}
        writes = []
        async def api(route):
            path = urlparse(route.request.url).path
            status = 200
            if path.endswith('/candidates/clustered'):
                await cluster_release.wait()
                data = {'clusters': [{'cluster_id': 'fixture-cluster', 'representative': CANDIDATE['id'],
                         'representative_title': CANDIDATE['title'], 'member_ids': [CANDIDATE['id'], second['id']],
                         'member_titles': [CANDIDATE['title'], second['title']], 'similarities': {},
                         'best_similarity': None, 'mean_similarity': None}] if state['clustered'] else [], 'sidecar': 'available' if state['clustered'] else 'unavailable'}
            elif path.endswith('/candidates/vaults'):
                data = {'vaults': [{'id': 'core', 'label': 'Core', 'writable': True, 'candidate_enabled': True,
                                    'candidate_count': 1, 'pending_count': 1, 'reviewed_count': 0}], 'default_vault': 'core'}
            elif path.endswith('/candidates'):
                snapshot = dict(state['candidate'])
                if state['hold_candidates']:
                    await candidate_release.wait()
                data = {'candidates': [snapshot] + ([dict(second)] if state['clustered'] else []), 'vault': 'core'}
                if state['fail_candidates']:
                    status, data = 502, {'error': 'fixture refresh unavailable'}
            elif path.endswith('/candidates/approve') or path.endswith('/candidates/reject'):
                writes.append(route.request.post_data_json)
                state['candidate']['status'] = 'applied' if path.endswith('/approve') else 'rejected'
                state['hold_candidates'] = True
                candidate_release.clear()
                data = {'success': True, 'candidate': dict(state['candidate'])}
            elif path.endswith('/synthesis/merge-targets'):
                data = {'vault_id': 'core', 'targets': [{'node_id': 'fixture-note', 'title': 'Fixture target', 'note_path': 'fixture.md'}],
                        'suggested_target_node_id': 'fixture-note', 'suggested_target_error': None, 'total_count': 1, 'has_more': False}
            elif path.endswith('/synthesis/merge-preview'):
                data = {'vault_id': 'core', 'candidate': {'candidate_id': CANDIDATE['id'], 'title': CANDIDATE['title'], 'definition': CANDIDATE['body']},
                        'target': {'node_id': 'fixture-note', 'title': 'Fixture target', 'note_path': 'fixture.md', 'content': '# Fixture'},
                        'candidate_revision': 'a' * 64, 'target_revision': 'b' * 64}
            elif path.endswith('/synthesis/merge'):
                writes.append(route.request.post_data_json)
                state['candidate']['status'] = 'merged'
                state['hold_candidates'] = True
                candidate_release.clear()
                data = {'status': 'merged', 'applied': True, 'note_path': 'fixture.md', 'operation_id': 'fixture-op', 'idempotent': False}
            elif path.endswith('/curate/advise/active'):
                data = {'jobs': [], 'advisor': {'configured': False}}
            elif path.endswith('/curate/accept/active'):
                data = {'jobs': []}
            else:
                raise AssertionError(f'Unmocked API request: {path}')
            await route.fulfill(status=status, content_type='application/json', body=json.dumps(data))
        await page.route('**/api/local/**', api)
        try:
            await page.goto(os.environ.get('CURATE_UI_TEST_URL', 'http://127.0.0.1:5269/?vault=core'))
            await expect(page.get_by_role('heading', name=CANDIDATE['title'], exact=True)).to_be_visible(timeout=3000)
            print('PASS: candidates render while optional clustering remains unresolved')
            cluster_release.set()
            await expect(page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
            # A confirmed action must publish its returned candidate before reconciliation completes.
            await page.get_by_title('Approve candidate', exact=True).click()
            await expect(page.get_by_text('in vault', exact=True)).to_be_visible(timeout=3000)
            assert len(writes) == 1
            print('PASS: confirmed approval updates card while candidates reconciliation remains unresolved')
            candidate_release.set()
            state['hold_candidates'] = False
            await expect(page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
            state['fail_candidates'] = True
            await page.get_by_role('button', name='Refresh', exact=True).click()
            await expect(page.get_by_text('fixture refresh unavailable', exact=False)).to_be_visible()
            await expect(page.get_by_role('heading', name=CANDIDATE['title'], exact=True)).to_be_visible()
            print('PASS: failed background refresh preserves existing cards')
            state.update(candidate=dict(CANDIDATE), fail_candidates=False, hold_candidates=False)
            await page.reload()
            await expect(page.get_by_role('heading', name=CANDIDATE['title'], exact=True)).to_be_visible()
            await page.get_by_title('Reject candidate', exact=True).click()
            await page.get_by_placeholder('Why should this candidate be rejected?').fill('Fixture-only rejection')
            await page.locator('form').get_by_role('button', name='Reject candidate', exact=True).click()
            await expect(page.get_by_text('rejected', exact=True)).to_be_visible(timeout=3000)
            await expect(page.get_by_placeholder('Why should this candidate be rejected?')).not_to_be_visible()
            print('PASS: confirmed rejection updates card and closes form before reconciliation')
            candidate_release.set()
            state.update(candidate=dict(CANDIDATE), hold_candidates=False)
            await page.reload()
            await expect(page.get_by_role('heading', name=CANDIDATE['title'], exact=True)).to_be_visible()
            await page.get_by_title('Merge into existing note', exact=True).click()
            dialog = page.get_by_role('dialog', name='Merge into existing note')
            await expect(dialog.get_by_role('button', name='Confirm merge', exact=True)).to_be_enabled()
            await dialog.get_by_role('button', name='Confirm merge', exact=True).click()
            await expect(dialog).not_to_be_visible()
            await expect(page.get_by_text('in vault', exact=True)).to_be_visible(timeout=3000)
            assert writes[-1]['confirmed'] is True
            assert writes[-1]['candidate_revision'] == 'a' * 64
            assert writes[-1]['target_revision'] == 'b' * 64
            print('PASS: confirmed merge updates card before reconciliation, preserving revision contract')
            candidate_release.set()
            state.update(candidate=dict(CANDIDATE), hold_candidates=False, clustered=True)
            await page.reload()
            cluster_approve = page.get_by_title('Approve cluster (promotes representative, marks others merged)', exact=True)
            await expect(cluster_approve).to_be_visible()
            await cluster_approve.click()
            await expect(page.get_by_text('in vault', exact=True)).to_be_visible(timeout=3000)
            await expect(page.get_by_role('heading', name=second['title'], exact=True)).to_be_visible()
            await expect(page.get_by_title('Approve candidate', exact=True)).to_be_enabled()
            print('PASS: confirmed representative detaches from stale cluster; next action enabled before reconciliation')
            assert not errors, errors
        finally:
            cluster_release.set()
            candidate_release.set()
            await browser.close()

if __name__ == '__main__':
    asyncio.run(run())
