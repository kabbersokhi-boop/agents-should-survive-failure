// Verify the published case proof and media inventory without third-party dependencies.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const directory = path.join(root, 'docs/evidence/portfolio/demo');
const json = name => JSON.parse(readFileSync(path.join(directory, name), 'utf8'));
const verification = json('verification.json');
assert.equal(verification.schema_version, 1);
const names = new Set();
for (const asset of verification.assets) {
  assert.match(asset.name, /^[a-z0-9][a-z0-9.-]*$/);
  assert.ok(!names.has(asset.name), 'Asset names must be unique');
  names.add(asset.name);
  const bytes = readFileSync(path.join(directory, asset.name));
  assert.equal(bytes.length, asset.bytes, `${asset.name}: size changed`);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), asset.sha256, `${asset.name}: hash changed`);
  if (asset.name.endsWith('.png')) {
    assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(bytes.readUInt32BE(16), 1920);
    assert.equal(bytes.readUInt32BE(20), 1080);
  }
}
for (const name of ['supplier-crash-recovery.mp4', 'preview.gif', 'poster.png', 'chapters.json', 'recovery-proof.json', 'evaluation.json']) assert.ok(names.has(name));
const video = readFileSync(path.join(directory, verification.media.file));
assert.equal(video.subarray(4, 8).toString(), 'ftyp');
assert.ok(['GIF87a', 'GIF89a'].includes(readFileSync(path.join(directory, 'preview.gif')).subarray(0, 6).toString()));
assert.equal(verification.media.audio, false);
assert.equal(verification.media.width, 1920);
assert.equal(verification.media.height, 1080);

const proof = json('recovery-proof.json');
assert.equal(proof.verified, true);
assert.equal(proof.run_id, verification.run_id);
assert.equal(proof.project, 'agents-portfolio');
const stages = proof.stages;
assert.deepEqual(stages.map(stage => stage.stage), ['waiting_for_operator_approval', 'effects_committed_before_ack', 'worker_sigkill_observed', 'replacement_worker_started', 'recovery_verified']);
for (let index = 1; index < stages.length; index++) assert.ok(Date.parse(stages[index].at) > Date.parse(stages[index - 1].at));
assert.equal(stages[2].exit_code, 137);
assert.ok(stages[2].prior_pid > 0 && stages[3].replacement_pid > 0);
assert.notEqual(stages[2].prior_pid, stages[3].replacement_pid);
for (const stage of [stages[1], stages[4]]) {
  const evidence = stage.evidence;
  assert.equal(evidence.run_id, proof.run_id);
  assert.equal(evidence.temporal_workflow_id, `vendor-onboarding-${proof.run_id}`);
  assert.equal(evidence.temporal_observed, true);
  assert.equal(evidence.business_status, 'succeeded');
  assert.deepEqual(evidence.effects, {approval_decisions:1, approved_suppliers:1, synthetic_notifications:1});
}
assert.equal(stages[1].evidence.temporal_status, 'RUNNING');
assert.equal(stages[1].evidence.decision_activity_attempt, 1);
assert.equal(stages[4].evidence.temporal_status, 'COMPLETED');
assert.ok(stages[4].evidence.decision_activity_attempt >= 2);

const chapters = json('chapters.json');
assert.equal(chapters.run_id, proof.run_id);
assert.equal(chapters.chapters.length, verification.media.chapter_count);
let end = 0;
for (const chapter of chapters.chapters) {
  assert.ok(Math.abs(chapter.at - end) < 0.001);
  assert.ok(chapter.duration > 0);
  end += chapter.duration;
}
assert.ok(Math.abs(end - verification.media.duration_seconds) < 0.1);
const evaluation = json('evaluation.json');
assert.equal(evaluation.evaluation_run_id, verification.evaluation.run_id);
assert.equal(evaluation.dataset_sha256, verification.evaluation.dataset_sha256);
assert.equal(evaluation.status, 'succeeded');
assert.equal(evaluation.results.length, 24);
assert.ok(evaluation.results.every(result => result.status === 'passed'));
assert.equal(verification.scope.model_provider, 'deterministic');
console.log(`Portfolio evidence verified: ${names.size} hashed assets, ${chapters.chapters.length} chapters, observed SIGKILL and redelivery, one of each effect, 24/24 evaluation cases.`);
