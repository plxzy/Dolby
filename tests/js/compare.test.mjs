import test from 'node:test';
import assert from 'node:assert/strict';
import { WEIGHTS, scoreSpec, scoreReport, rankScores, resolutionPoints, roundPoint } from '../../dolby_tool/web/static/js/browser/compare.js';
import { CompareSessions } from '../../dolby_tool/web/static/js/browser/compare-sessions.js';
import { analyze } from '../../dolby_tool/web/static/js/browser/engine.js';
import { mp4, sample } from './fixtures.mjs';

const resolvedSpec = () => ({ video: { dolby_vision: { present: true }, fourcc: 'dvh1', hdr10_plus: true, bit_rate_mbps: 40, height: 1080 }, audio: [{ atmos: true }], subtitles: [{}, {}, {}] });
const complete = { tracksComplete: true };

test('resolved components match native compare values, including resolution thresholds and caps', () => {
  const score = scoreSpec(resolvedSpec(), WEIGHTS, complete);
  assert.deepEqual(score.components, Object.fromEntries(Object.entries({ dv_present: 25, dv_fourcc_ok: 25, hdr10_plus: 10, atmos: 15, bit_rate_mbps: 10, resolution: 5, subtitles: 3 }).map(([k, v]) => [k, { min: v, max: v }])));
  assert.equal(score.min, 93); assert.equal(score.max, 93);
  assert.deepEqual([null, 480, 999, 1000, 1080, 1999, 2000, 2160].map(h => resolutionPoints(h, 10)), [0, 2, 2, 5, 5, 5, 10, 10]);
  const spec = resolvedSpec(); spec.video.bit_rate_mbps = 200; spec.subtitles = Array(20).fill({});
  assert.equal(scoreSpec(spec, WEIGHTS, complete).min, 105);
  assert.equal(roundPoint(2.25), 2.2); assert.equal(roundPoint(2.45), 2.5); assert.equal(roundPoint(2.75), 2.8);
});

test('unknown and scoped evidence contribute possible points, never an assumed zero', () => {
  for (const state of ['checking', 'not_found_in_scope', 'unknown', 'unsupported', 'conflicting']) {
    const report = { file: { name: 'test' }, container: {}, tracks: [], findings: { 'video.dolby_vision': { state } }, state: 'done', terminal: { reason: 'exhausted' } };
    const score = scoreReport(report);
    assert.deepEqual(score.components.dv_present, { min: 0, max: 25 });
    assert.equal(score.min, 0); assert.equal(score.max, 110); assert.equal(score.resolved, false);
  }
  const spec = resolvedSpec(); spec.video.hdr10_plus = null;
  const uncertain = scoreSpec(spec, WEIGHTS, complete);
  assert.equal(uncertain.min, 83); assert.equal(uncertain.max, 93);
  spec.video.hdr10_plus = false;
  const absent = scoreSpec(spec, WEIGHTS, complete);
  assert.equal(absent.min, 83); assert.equal(absent.max, 83);
  spec.video.hdr10_plus = true;
  assert.equal(scoreSpec(spec, WEIGHTS, complete).min, 93);
});

test('decimal custom weights retain native flag precision and compensated total rounding', () => {
  const spec = resolvedSpec();
  spec.video.bit_rate_mbps = 1; spec.video.height = 1000; spec.audio = [{ atmos: false }]; spec.subtitles = Array(5).fill({});
  const weights = { dv_present: 17, dv_fourcc_ok: 1.25, hdr10_plus: 50, atmos: 50, bit_rate_mbps: 25, resolution: 1.25, subtitles: 50 };
  const score = scoreSpec(spec, weights, complete);
  assert.deepEqual(score.components.dv_fourcc_ok, { min: 1.25, max: 1.25 });
  assert.equal(score.min, 119.2); assert.equal(score.max, 119.2);
});

test('unknown audio, incomplete track lists and bitrate stay uncertain after stopping', () => {
  const spec = resolvedSpec(); spec.audio = [{ atmos: null }]; spec.video.bit_rate_mbps = null;
  const score = scoreSpec(spec, WEIGHTS, complete);
  assert.deepEqual(score.components.atmos, { min: 0, max: 15 });
  assert.deepEqual(score.components.bit_rate_mbps, { min: 0, max: 20 });
  assert.deepEqual(scoreSpec(spec).components.subtitles, { min: 3, max: 5 });
  spec.video.dolby_vision.present = null; spec.video.fourcc = 'hvc1';
  assert.deepEqual(scoreSpec(spec, WEIGHTS, complete).components.dv_fourcc_ok, { min: 0, max: 0 });
  assert.deepEqual(scoreSpec(spec, { ...WEIGHTS, atmos: 0 }, complete).components.atmos, { min: 0, max: 0 });
});

const s = (min, max = min) => ({ min, max, resolved: min === max });
test('overlap and touching ranges have no winner; strict separation and resolved ties do', () => {
  for (const scores of [[s(20, 60), s(50, 70)], [s(20, 50), s(50, 70)], [s(50), s(40, 60)]]) {
    const ranking = rankScores(scores);
    assert.deepEqual(ranking.winners, []); assert.equal(ranking.provisional, true);
    assert.deepEqual(ranking.overlaps, [[1], [0]]);
  }
  assert.deepEqual(rankScores([s(61, 80), s(20, 60)]).winners, [0]);
  assert.deepEqual(rankScores([s(80), s(80), s(60)]).winners, [0, 1]);
  assert.equal(rankScores([s(80), s(60)], { scanning: true }).provisional, true);
  assert.equal(rankScores([s(80), s(60)]).provisional, false);
});

test('engine track completeness and completed packet evidence narrow a real report', async () => {
  const reports = [];
  const report = await analyze(mp4({ dv: true, fourcc: 'dvh1', samples: [sample({ hdr: true })] }), { mediainfo: false, onReport: r => reports.push(r) });
  assert.equal(reports[0].structureComplete, false);
  assert.equal(report.structureComplete, true);
  assert.ok(scoreReport(reports[0]).max > scoreReport(reports[0]).min);
  assert.deepEqual(scoreReport(report).components.hdr10_plus, { min: 10, max: 10 });
});

function fakeQueue() {
  const sessions = [], changes = [];
  const queue = new CompareSessions({ onChange: item => changes.push(item), start(file, { onReport }) {
    const calls = [], session = { file, calls, emit: report => onReport(report), pause: () => calls.push('pause'), resume: () => calls.push('resume'), deeper: () => calls.push('deeper'), stop: () => { calls.push('stop'); onReport({ state: 'done', terminal: { reason: 'stopped' } }); } };
    sessions.push(session); return session;
  } });
  return { queue, sessions, changes };
}
test('comparison bounds workers to one; an auto-pause holds the queue until deeper/stop', async () => {
  const { queue, sessions } = fakeQueue();
  const a = queue.add(new File(['a'], 'a.mp4')), b = queue.add(new File(['b'], 'b.mp4'));
  queue.run(); assert.equal(sessions.length, 1);
  sessions[0].emit({ state: 'paused-at-limit' });
  queue.run(); assert.equal(sessions.length, 1);
  queue.command(a, 'deeper'); assert.deepEqual(sessions[0].calls, ['deeper']);
  queue.command(a, 'stop'); await new Promise(queueMicrotask);
  assert.equal(sessions.length, 2); assert.equal(queue.active, b);
  queue.clear(); assert.equal(queue.items.length, 0); assert.equal(queue.active, null);
});

test('host pause applies before startup, prevents queue progression, preserves user/limit pauses', async () => {
  const { queue, sessions } = fakeQueue();
  const a = queue.add(new File(['a'], 'a.mp4')); queue.add(new File(['b'], 'b.mp4'));
  queue.pauseFromHost(); queue.run(); assert.equal(sessions.length, 0);
  queue.resumeFromHost(); assert.equal(sessions.length, 1);
  queue.pauseFromHost(); assert.deepEqual(sessions[0].calls, ['pause']);
  sessions[0].emit({ state: 'paused' }); queue.resumeFromHost(); assert.deepEqual(sessions[0].calls, ['pause', 'resume']);
  queue.command(a, 'pause'); sessions[0].emit({ state: 'paused' }); queue.pauseFromHost(); queue.resumeFromHost();
  assert.deepEqual(sessions[0].calls, ['pause', 'resume', 'pause']);
  sessions[0].emit({ state: 'paused-at-limit' }); queue.pauseFromHost(); queue.resumeFromHost();
  assert.equal(sessions[0].calls.at(-1), 'pause');
  queue.pauseFromHost(); queue.command(a, 'deeper'); assert.equal(sessions[0].calls.at(-1), 'pause');
  sessions[0].emit({ state: 'done', terminal: { reason: 'resolved' } }); await new Promise(queueMicrotask);
  assert.equal(sessions.length, 1); queue.resumeFromHost(); assert.equal(sessions.length, 2);
  queue.clear();
});

test('removing or clearing ignores stopped callbacks and advances the queue safely', () => {
  const { queue, sessions } = fakeQueue();
  const a = queue.add(new File(['a'], 'a.mp4')), b = queue.add(new File(['b'], 'b.mp4'));
  queue.run(); queue.remove(a); assert.equal(queue.active, b); assert.equal(sessions.length, 2);
  sessions[0].emit({ state: 'running' }); assert.equal(queue.active, b);
  queue.clear(); assert.equal(queue.items.length, 0); assert.equal(queue.active, null);
});

test('Stop remains available during a host pause without starting the next worker', async () => {
  const { queue, sessions } = fakeQueue();
  const first = queue.add(new File(['a'], 'a.mp4')); queue.add(new File(['b'], 'b.mp4'));
  queue.run(); queue.pauseFromHost(); queue.command(first, 'stop'); await new Promise(queueMicrotask);
  assert.equal(first.report.terminal.reason, 'stopped'); assert.equal(sessions.length, 1);
  queue.resumeFromHost(); assert.equal(sessions.length, 2); queue.clear();
});
