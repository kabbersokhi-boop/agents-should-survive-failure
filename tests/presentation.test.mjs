import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {effectState,executionNote} from '../src/agents_should_survive_failure/static/presentation.js';
const summary = {business_status:'succeeded', temporal_status:'RUNNING', temporal_observed:true,
  effects:{approval_decisions:1,approved_suppliers:1,synthetic_notifications:1}};
test('committed effects never imply Temporal completion',()=>{
  assert.equal(effectState(summary),'Effects committed');
  assert.match(executionNote(summary),/has not completed/);
  assert.match(executionNote({...summary,temporal_status:'COMPLETED'}),/has completed/);
});
test('unavailable orchestration retains honest evidence boundary',()=>{
  assert.match(executionNote({...summary,temporal_observed:false}),/not proof/);
});
test('duplicates and invalid counts cannot become a success caption',()=>{
  assert.equal(effectState({...summary,effects:{...summary.effects,approval_decisions:2}}),'Duplicate effect detected');
  assert.match(executionNote({...summary,temporal_status:'COMPLETED',effects:{...summary.effects,approval_decisions:2}}),/Do not treat.*recovered/);
  assert.throws(()=>effectState({...summary,effects:{...summary.effects,approval_decisions:-1}}));
});
test('failed executions never suggest another human decision or successful recovery',()=>{
  for (const temporal_status of ['FAILED','TIMED_OUT','TERMINATED']) {
    const note = executionNote({...summary,temporal_status});
    assert.match(note,/did not complete successfully/);
    assert.doesNotMatch(note,/human decision is required|has completed/);
  }
  assert.match(executionNote({...summary,business_status:'failed',temporal_status:'RUNNING'}),/Review failed/);
});
test('cancellation preserves visibility of committed effects without promising recovery',()=>{
  assert.match(executionNote({...summary,temporal_status:'CANCELED'}),/Case cancelled/);
  assert.match(executionNote({...summary,business_status:'cancelled'}),/Case cancelled/);
});
test('rejection and completion without approved effects remain distinct from approval',()=>{
  const rejected = {...summary,business_status:'rejected',temporal_status:'COMPLETED',
    effects:{approval_decisions:1,approved_suppliers:0,synthetic_notifications:0}};
  assert.equal(effectState(rejected),'Supplier rejected');
  assert.match(executionNote(rejected),/Human rejection recorded/);
  assert.match(executionNote({...rejected,business_status:'succeeded'}),/completion alone does not prove supplier approval/);
});
test('decision rationale requires operator input rather than presupposing approval',()=>{
  const html = readFileSync(new URL('../src/agents_should_survive_failure/static/index.html',import.meta.url),'utf8');
  assert.match(html,/<textarea[^>]*id="rationale"[^>]*required[^>]*><\/textarea>/);
});
