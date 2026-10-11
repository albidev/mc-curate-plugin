"""Race/error coverage for the real Curate UI, with intercepted API fixtures only."""
import asyncio
import json
import os
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, expect

async def run():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=os.environ.get(
            'CURATE_BROWSER_EXECUTABLE', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
        page = await browser.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        metadata_release, old_release, old_started, old_finished = [asyncio.Event() for _ in range(4)]
        write_started, write_release = asyncio.Event(), asyncio.Event()
        state = {'hold_next': False, 'status': 'pending_review', 'fail_write': False}
        writes = []
        async def api(route):
            url = urlparse(route.request.url)
            path = url.path
            vault = parse_qs(url.query).get('vault', ['core'])[0]
            status = 200
            held = False
            if path.endswith('/candidates/vaults'):
                await metadata_release.wait()
                data = {'vaults': [{'id': v, 'label': v.capitalize(), 'writable': True, 'candidate_enabled': True,
                        'candidate_count': 1, 'pending_count': 1, 'reviewed_count': 0} for v in ['core', 'work']], 'default_vault': 'core'}
            elif path.endswith('/candidates/clustered'):
                data = {'clusters': [], 'sidecar': 'unavailable'}
            elif path.endswith('/candidates'):
                data = {'vault': vault, 'candidates': [{'id': 'fixture-' + vault, 'source': 'session_synthesis',
                    'vault_id': vault, 'title': vault.capitalize() + ' fixture', 'body': 'Only a fixture.',
                    'status': state['status'] if vault == 'core' else 'pending_review'}]}
                if state['fail_write'] and vault == 'core':
                    data['candidates'].append({**data['candidates'][0], 'id': 'fixture-second', 'title': 'Another pending fixture'})
                if vault == 'core' and state['hold_next']:
                    state['hold_next'] = False
                    held = True
                    old_started.set()
                    await old_release.wait()
            elif path.endswith('/candidates/approve'):
                writes.append(route.request.post_data_json)
                if state['fail_write']:
                    write_started.set()
                    await write_release.wait()
                    status, data = 502, {'error': 'fixture write failed'}
                else:
                    state['status'] = 'applied'
                    data = {'success': True, 'candidate': {'id': 'fixture-core', 'status': 'applied', 'vault_id': 'core'}}
            elif path.endswith('/curate/advise/active'):
                data = {'jobs': [], 'advisor': {'configured': False}}
            elif path.endswith('/curate/accept/active'):
                data = {'jobs': []}
            else:
                raise AssertionError(f'Unmocked request: {path}')
            try:
                await route.fulfill(status=status, content_type='application/json', body=json.dumps(data))
            finally:
                if held:
                    old_finished.set()
        await page.route('**/api/local/**', api)
        try:
            await page.goto(os.environ.get('CURATE_UI_TEST_URL', 'http://127.0.0.1:5269/?vault=core'))
            await expect(page.get_by_role('heading', name='Core fixture', exact=True)).to_be_visible(timeout=3000)
            print('PASS: candidates render without waiting for vault metadata')
            metadata_release.set()
            await expect(page.get_by_role('button', name='Work1 total · 1 pending', exact=True)).to_be_visible()
            state['hold_next'] = True
            await page.get_by_role('button', name='Refresh', exact=True).click()
            await asyncio.wait_for(old_started.wait(), timeout=5)
            await page.get_by_role('button', name='Work1 total · 1 pending', exact=True).click()
            await expect(page.get_by_role('heading', name='Work fixture', exact=True)).to_be_visible()
            old_release.set()
            await asyncio.wait_for(old_finished.wait(), timeout=5)
            await page.wait_for_timeout(100)
            await expect(page.get_by_role('heading', name='Work fixture', exact=True)).to_be_visible()
            await expect(page.get_by_role('heading', name='Core fixture', exact=True)).not_to_be_visible()
            print('PASS: late response from previous vault cannot overwrite current vault')
            await page.get_by_role('button', name='Core1 total · 1 pending', exact=True).click()
            await expect(page.get_by_role('heading', name='Core fixture', exact=True)).to_be_visible()
            # A pre-mutation snapshot must not undo a subsequently confirmed write.
            old_release.clear(); old_started.clear(); old_finished.clear()
            state['hold_next'] = True
            await page.get_by_role('button', name='Refresh', exact=True).click()
            await asyncio.wait_for(old_started.wait(), timeout=5)
            await page.get_by_title('Approve candidate', exact=True).click()
            await expect(page.get_by_text('in vault', exact=True)).to_be_visible()
            old_release.set()
            await asyncio.wait_for(old_finished.wait(), timeout=5)
            await page.wait_for_timeout(100)
            await expect(page.get_by_text('in vault', exact=True)).to_be_visible()
            print('PASS: pre-mutation refresh cannot revert a confirmed decision')
            state.update(status='pending_review', fail_write=True)
            await page.reload()
            approvals = page.get_by_title('Approve candidate', exact=True)
            await expect(approvals.first).to_be_visible()
            await approvals.first.click()
            await asyncio.wait_for(write_started.wait(), timeout=5)
            await expect(approvals.first).to_be_disabled()
            await expect(approvals.nth(1)).to_be_disabled()
            await expect(page.get_by_text('in vault', exact=True)).not_to_be_visible()
            write_release.set()
            await expect(page.get_by_text('fixture write failed', exact=True)).to_be_visible()
            await expect(approvals.first).to_be_enabled()
            await expect(approvals.nth(1)).to_be_enabled()
            await expect(page.get_by_text('in vault', exact=True)).not_to_be_visible()
            assert len(writes) == 2
            print('PASS: pending write stays pending; failed write preserves card and releases action')
            assert not errors, errors
        finally:
            metadata_release.set(); old_release.set(); write_release.set()
            await browser.close()

if __name__ == '__main__':
    asyncio.run(run())
