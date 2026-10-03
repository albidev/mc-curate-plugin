import assert from 'node:assert/strict';
import test from 'node:test';
import { canConfirmMerge } from './ui/merge-reconciliation-state.ts';

const preview = {vault_id:'core', candidate:{candidate_id:'cand-1',definition:'Never reset the shared index.'},
  target:{node_id:'vault:rule.md',content:'Preserve other writers staging.'},
  candidate_revision:'a'.repeat(64),target_revision:'b'.repeat(64),conflict:{required:true}};
const job = {job_id:'rcl-'+'1'.repeat(16),status:'done',vault:'core',candidate_id:'cand-1',target_node_id:'vault:rule.md',
  candidate_revision:preview.candidate_revision,target_revision:preview.target_revision,reconciliation_id:'rec-'+'c'.repeat(32),
  assessment:{classification:'compatible',reason:'consistent',candidate_quote:preview.candidate.definition,
    target_quote:preview.target.content,provider:'p',model:'m'}};

test('malformed HTTP previews never enable confirmation or throw', () => {
  for (const changed of [null,{}, {...preview,candidate:{...preview.candidate,definition:42}},
    {...preview,target:{...preview.target,content:null}}, {...preview,target_revision:'bad-hash'},
    {...preview,conflict:{required:null}}, {...preview,conflict:{required:'false'}},
    {...preview,conflict:{required:true,signals:[42]}}, {...preview,conflict:null}]) {
    assert.doesNotThrow(() => canConfirmMerge(changed,job,true));
    assert.equal(canConfirmMerge(changed,job,true),false);
  }
  // Older BDH previews deliberately omit conflict metadata altogether.
  const {conflict,...legacy} = preview;
  assert.equal(canConfirmMerge(legacy,null,false),true);
});

test('malformed HTTP assessments stay blocked without a render-time exception', () => {
  for (const changed of [{reason:null},{candidate_quote:42},{target_quote:{}},{provider:null},{model:[]},
    {reason:'x'.repeat(1001)},{candidate_quote:'x'.repeat(1201)},{classification:['compatible']}]) {
    assert.doesNotThrow(() => canConfirmMerge(preview,{...job,assessment:{...job.assessment,...changed}},true));
    assert.equal(canConfirmMerge(preview,{...job,assessment:{...job.assessment,...changed}},true),false);
  }
  for (const changed of [{job_id:'rcl-invalid'},{reconciliation_id:'rec-invalid'},{assessment:null},
    {status:'unknown'},{error:[]},{target_revision:null}]) {
    assert.doesNotThrow(() => canConfirmMerge(preview,{...job,...changed},true));
    assert.equal(canConfirmMerge(preview,{...job,...changed},true),false);
  }
});

test('a lexical warning needs a compatible bound assessment AND a human check', () => {
  assert.equal(canConfirmMerge(preview, null, false),false);
  assert.equal(canConfirmMerge(preview, job, false),false);
  assert.equal(canConfirmMerge(preview, job, true),true);
  for (const classification of ['conflicting','uncertain']) {
    assert.equal(canConfirmMerge(preview,{...job,assessment:{...job.assessment,classification}},true),false);
  }
  for (const changed of [{candidate_id:'cand-2'},{target_node_id:'vault:other.md'},
    {candidate_revision:'x'},{target_revision:'x'},{vault:'other'},{status:'running'},{reconciliation_id:''},
    {assessment:{...job.assessment,target_quote:'invented'}}]) {
    assert.equal(canConfirmMerge(preview,{...job,...changed},true),false);
  }
  assert.equal(canConfirmMerge({...preview,conflict:{required:false}},null,false),true);
  assert.equal(canConfirmMerge(null,job,true),false);
});
