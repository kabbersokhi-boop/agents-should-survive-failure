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

// The short overview is tied to the same recorded case and has its own inventory.
const overview = json('overview-verification.json');
assert.equal(overview.schema_version, 1);
assert.equal(overview.run_id, proof.run_id);
assert.equal(overview.media.file, 'overview.mp4');
assert.equal(overview.media.audio, true);
assert.equal(overview.media.caption_layers, 1);
assert.equal(overview.media.subtitle_streams, 0);
assert.equal(overview.media.width, 1920);
assert.equal(overview.media.height, 1080);
assert.ok(overview.media.duration_seconds >= 60 && overview.media.duration_seconds <= 120);
const overviewNames = new Set();
for (const asset of overview.assets) {
  assert.match(asset.name, /^[a-z0-9][a-z0-9.-]*$/);
  assert.ok(!overviewNames.has(asset.name));
  overviewNames.add(asset.name);
  const bytes = readFileSync(path.join(directory, asset.name));
  assert.equal(bytes.length, asset.bytes, `${asset.name}: overview size changed`);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), asset.sha256, `${asset.name}: overview hash changed`);
}
for (const name of ['overview.mp4', 'overview-preview.gif', 'overview-poster.png', 'overview.en.srt', 'overview-transcript.txt', 'overview-chapters.json']) assert.ok(overviewNames.has(name));
assert.equal(readFileSync(path.join(directory, 'overview.mp4')).subarray(4, 8).toString(), 'ftyp');
assert.ok(['GIF87a', 'GIF89a'].includes(readFileSync(path.join(directory, 'overview-preview.gif')).subarray(0, 6).toString()));
const overviewPoster = readFileSync(path.join(directory, 'overview-poster.png'));
assert.equal(overviewPoster.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
assert.equal(overviewPoster.readUInt32BE(16), 1920);
assert.equal(overviewPoster.readUInt32BE(20), 1080);
const overviewChapters = json('overview-chapters.json');
assert.equal(overviewChapters.run_id, proof.run_id);
assert.equal(overviewChapters.chapters.length, overview.media.chapter_count);
let overviewEnd = 0;
for (const chapter of overviewChapters.chapters) {
  assert.ok(Math.abs(chapter.at - overviewEnd) < 0.001);
  assert.ok(chapter.duration > 0);
  overviewEnd += chapter.duration;
}
assert.ok(Math.abs(overviewEnd - overview.media.duration_seconds) < 0.1);
const subtitleTime = value => {
  const match = /^(\d{2}):(\d{2}):(\d{2}),(\d{3})$/.exec(value);
  assert.ok(match, 'Invalid overview subtitle timestamp');
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) + Number(match[4]) / 1000;
};
let captionEnd = 0;
let captionCount = 0;
for (const block of readFileSync(path.join(directory, 'overview.en.srt'), 'utf8').trim().split(/\r?\n\r?\n/)) {
  const lines = block.split(/\r?\n/);
  assert.equal(Number(lines[0]), ++captionCount);
  const [start, end] = lines[1].split(' --> ').map(subtitleTime);
  assert.ok(start >= captionEnd && end > start && end <= overviewEnd);
  assert.ok(lines.slice(2).join('').trim().length > 0);
  captionEnd = end;
}
assert.equal(captionCount, overview.media.subtitle_cues);
assert.doesNotMatch(readFileSync(path.join(directory, 'overview-transcript.txt'), 'utf8'), /\bI'm\b|\bI built\b|\bI designed\b/);
console.log(`Project overview verified: ${overviewNames.size} assets, ${overviewChapters.chapters.length} chapters, ${captionCount} caption cues, same recorded case.`);
