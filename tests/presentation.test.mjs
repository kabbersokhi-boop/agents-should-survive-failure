import assert from 'node:assert/strict';
import test from 'node:test';
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
