"""Real Curate UI with isolated HTTP fixtures: AI advice never performs a merge.
Run with Playwright Python against browser-harness/vite.config.mjs; no real API is called.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
from verify_merge_browser import CANDIDATE, TARGET


def run():
    with sync_playwright() as pw:
        options = {'headless': True}
        if executable := os.environ.get('CURATE_BROWSER_EXECUTABLE'):
            options['executable_path'] = executable
        browser = pw.chromium.launch(**options)
        page = browser.new_page(viewport={'width':1280,'height':900})
        errors, merges, starts = [], [], []
        state = {'mode':'compatible','preview':None}
        page.on('pageerror',lambda error:errors.append(str(error)))

        def api(route):
            path = urlparse(route.request.url).path
            body = route.request.post_data_json if route.request.method == 'POST' else None
            status = 200
            if path.endswith('/candidates/vaults'):
                data = {'vaults':[{'id':'core','label':'Core','writable':True,'candidate_enabled':True,
                         'candidate_count':1,'pending_count':1,'reviewed_count':0}], 'default_vault':'core'}
            elif path.endswith('/curate/advise/active'):
                data = {'jobs':[],'advisor':{'configured':True,'provider':'fixture','model':'fixture'}}
            elif path.endswith('/curate/accept/active'): data = {'jobs':[]}
            elif path.endswith('/candidates/clustered'): data = {'clusters':[]}
            elif path.endswith('/candidates'): data = {'candidates':[CANDIDATE],'vault':'core'}
            elif path.endswith('/merge-targets'):
                data = {'vault_id':'core','targets':[TARGET],'suggested_target_node_id':TARGET['node_id'],
                        'suggested_target_error':None,'total_count':1,'has_more':False}
            elif path.endswith('/merge-preview'):
                data = {'vault_id':'core','candidate':{'candidate_id':CANDIDATE['id'],'title':CANDIDATE['title'],
                        'definition':'Never overwrite the original criteria; append specific cases.'},
                        'target':{**TARGET,'content':'# Existing note\nOriginal criteria.'},
                        'candidate_revision':'a'*64,'target_revision':'b'*64,
                        'conflict':{'required':True,'signals':['Never'],'provenance_flag':True}}
                if state['mode'] == 'bad-required': data['conflict']['required'] = None
                if state['mode'] == 'bad-text': data['candidate']['definition'] = 42
                if state['mode'] == 'bad-hash': data['target_revision'] = 'invalid'
                if state['mode'] == 'bad-signals': data['conflict']['signals'] = [None]
                state['preview'] = data
            elif path.endswith('/synthesis/reconcile') or path.endswith('/synthesis/reconcile/status'):
                p = state['preview']
                data = {'job_id':'rcl-'+'1'*16,'vault':'core','candidate_id':CANDIDATE['id'],
                        'target_node_id':TARGET['node_id'],'candidate_revision':p['candidate_revision'],
                        'target_revision':p['target_revision'],'status':'queued'}
                if path.endswith('/synthesis/reconcile'):
                    starts.append(body)
                    assert body['candidate_revision'] == p['candidate_revision']
                    assert body['target_revision'] == p['target_revision']
                    assert 'confirmed' not in body and 'conflict_confirmed' not in body
                else:
                    if state['mode'] == 'failed': data.update(status='failed',error='Fixture provider unavailable')
                    else:
                        data.update(status='done',reconciliation_id='rec-'+'c'*32,
                          assessment={'classification':state['mode'] if state['mode'] in ['conflicting','uncertain'] else 'compatible',
                          'reason':'Both preserve the original criteria.','candidate_quote':p['candidate']['definition'],
                          'target_quote':'Original criteria.','provider':'fixture-provider','model':'fixture-model'})
                        if state['mode'] == 'stale': data['target_revision'] = 'd'*64
                        if state['mode'] == 'malformed': data['assessment']['reason'] = None
            elif path.endswith('/synthesis/merge'):
                merges.append(body)
                assert body['confirmed'] is True and body['conflict_confirmed'] is True
                assert body['reconciliation_id'] == 'rec-'+'c'*32
                assert body['candidate_revision'] == 'a'*64 and body['target_revision'] == 'b'*64
                data = {'status':'merged','applied':True,'idempotent':False,'note_path':TARGET['note_path'],'operation_id':'fixture-op'}
            else: raise AssertionError('Unstubbed API request: '+path)
            route.fulfill(status=status,content_type='application/json',body=json.dumps(data))

        page.route('**/api/local/**',api)
        url = os.environ.get('CURATE_UI_TEST_URL','http://127.0.0.1:5269/?vault=core')
        def open_dialog(mode='compatible',it=False):
            state['mode'] = mode; merges.clear(); starts.clear()
            page.goto(url)
            page.evaluate("locale => localStorage.setItem('mission-control-locale',locale)",'it' if it else 'en')
            page.reload()
            page.get_by_title('Unisci a una nota esistente' if it else 'Merge into existing note',exact=True).click()
            dialog = page.get_by_role('dialog')
            confirm = dialog.get_by_role('button',name='Conferma il merge' if it else 'Confirm merge',exact=True)
            expect(confirm).to_be_disabled()
            if mode.startswith('bad-'):
                try:
                    expect(dialog.get_by_role('alert')).to_be_visible()
                except AssertionError:
                    print('Invalid preview diagnostics:', mode, state['preview'], errors, dialog.inner_text())
                    raise
            else:
                expect(dialog.get_by_text('Never overwrite the original criteria; append specific cases.',exact=True)).to_be_visible()
            return dialog,confirm

        dialog,confirm = open_dialog()
        dialog.get_by_role('button',name='Analyze compatibility with AI',exact=True).evaluate('(el)=>{el.click();el.click()}')
        expect(dialog.get_by_role('checkbox')).to_be_visible(timeout=10000)
        assert len(starts) == 1 and not merges
        expect(confirm).to_be_disabled()
        dialog.get_by_role('checkbox').check(); expect(confirm).to_be_enabled()
        dialog.get_by_role('checkbox').uncheck(); expect(confirm).to_be_disabled()
        dialog.get_by_role('checkbox').check()
        # Changing/reloading the target clears the opinion and its human confirmation.
        dialog.get_by_role('button',name='Curate Decision Flow',exact=False).click()
        expect(confirm).to_be_disabled(); expect(dialog.get_by_role('checkbox')).not_to_be_visible()
        dialog.get_by_role('button',name='Analyze compatibility with AI',exact=True).click()
        expect(dialog.get_by_role('checkbox')).to_be_visible(timeout=10000)
        expect(confirm).to_be_disabled()
        dialog.get_by_role('checkbox').check()
        confirm.evaluate('(el)=>{el.click();el.click()}')
        expect(dialog).not_to_be_visible(); assert len(merges) == 1
        print('PASS compatible: no AI merge, separate human check, target-change reset, exact revisions, double-click lock')

        for mode in ('conflicting','uncertain','failed','stale','malformed'):
            dialog,confirm = open_dialog(mode)
            dialog.get_by_role('button',name='Analyze compatibility with AI',exact=True).click()
            if mode in ('failed','stale','malformed'): expect(dialog.get_by_role('alert')).to_be_visible(timeout=10000)
            else: expect(dialog.get_by_text('Merge stays blocked.',exact=False)).to_be_visible(timeout=10000)
            expect(confirm).to_be_disabled(); expect(dialog.get_by_role('checkbox')).not_to_be_visible()
            assert not merges
            print('PASS blocked: '+mode)

        for mode in ('bad-required','bad-text','bad-hash','bad-signals'):
            dialog,confirm = open_dialog(mode)
            expect(confirm).to_be_disabled()
            expect(dialog.get_by_role('button',name='Analyze compatibility with AI',exact=True)).not_to_be_visible()
            assert not starts and not merges
            print('PASS invalid preview: '+mode)

        page.set_viewport_size({'width':390,'height':844})
        dialog,confirm = open_dialog(it=True)
        dialog.get_by_role('button',name='Analizza la compatibilità con AI',exact=True).click()
        expect(dialog.get_by_role('checkbox')).to_be_visible(timeout=10000)
        expect(dialog.get_by_text('Aggiunta compatibile (opinione AI)',exact=False)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        dialog.get_by_role('checkbox').check(); expect(confirm).to_be_enabled()
        dialog.get_by_role('checkbox').uncheck(); expect(confirm).to_be_disabled()
        assert not merges and not errors,errors
        screenshot = os.environ.get('CURATE_RECONCILIATION_SCREENSHOT')
        if screenshot: page.screenshot(path=screenshot)
        print('PASS mobile IT: localized warning/opinion/human check, no overflow or merge; no page errors')
        browser.close()


if __name__ == '__main__': run()
