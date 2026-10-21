import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { analyze } from '../../dolby_tool/web/static/js/browser/engine.js';
import { Gate, CancelledError } from '../../dolby_tool/web/static/js/browser/gate.js';
import { RangeReader, FileUnavailableError } from '../../dolby_tool/web/static/js/browser/reader.js';
import { EvidenceStore } from '../../dolby_tool/web/static/js/browser/evidence.js';
import { inspectSample } from '../../dolby_tool/web/static/js/browser/parsers/hevc.js';
import { fromMediaInfo } from '../../dolby_tool/web/static/js/browser/parsers/mediainfo.js';
import { toSpec } from '../../dolby_tool/web/static/js/browser/report.js';
import { windowFractions } from '../../dolby_tool/web/static/js/browser/scan.js';
import { mp4, mkv, sample, u32, nal, sei, hdr10plus } from './fixtures.mjs';

const unlimited = { autoBytes: Infinity, autoMs: Infinity };
const run = (file, options = {}) => analyze(file, { mediainfo: false, policy: unlimited, ...options });

test('tail moov is located without reading 64 MiB mdat; DV 8.1 is signaled', async () => {
  const report = await run(mp4({ tailBytes: 64 * 1024 ** 2, dv: true, fourcc: 'dvh1' }));
  assert.equal(report.findings['video.dolby_vision'].state, 'signaled');
  assert.equal(toSpec(report).video.dolby_vision.profile, '8.1');
  assert.equal(toSpec(report).video.fourcc_warning, null);
  assert.ok(report.metrics.bytesRequested < 1024 ** 2);
});

test('late HDR10+ is never absent at the limit and is found after deeper', async () => {
  const file = mp4({ samples: Array.from({ length: 400 }, (_, i) => sample({ hdr: i >= 300 })) });
  const policy = { autoBytes: 64 * 1024, autoMs: Infinity, deeperBytes: 4 * 1024 ** 2 };
  const gate = new Gate({ policy });
  const reports = []; let paused;
  const atLimit = new Promise(resolve => { paused = resolve; });
  const pending = analyze(file, { mediainfo: false, policy, gate, onReport: report => { reports.push(report); if (report.state === 'paused-at-limit') paused(report); } });
  const limited = await atLimit;
  assert.equal(limited.findings['video.hdr10_plus'].state, 'not_found_in_scope');
  assert.equal(toSpec(limited).video.hdr10_plus, null);
  assert.ok(!reports.some(r => r.findings['video.hdr10_plus'].state === 'absent'));
  gate.deeper();
  const report = await pending;
  assert.equal(report.findings['video.hdr10_plus'].state, 'observed');
  const hit = report.findings['video.hdr10_plus'].evidence.find(e => e.kind === 'observed');
  assert.ok(Number(/sample (\d+)/.exec(hit.coverage)[1]) >= 300);
  assert.equal(report.terminal.reason, 'resolved');
});

test('only a complete sample sweep establishes HDR metadata absence', async () => {
  const report = await run(mp4({ staticHdr: false, samples: Array.from({ length: 400 }, () => sample()) }));
  for (const id of ['video.hdr10_plus', 'video.hdr10.mdcv', 'video.hdr10.cll']) {
    assert.equal(report.findings[id].state, 'absent');
    assert.ok(report.findings[id].evidence.some(e => e.note === 'Not present in any video sample (complete scan)'));
  }
  assert.equal(report.coverage.video.samplesInspected, 400);
});

test('dec3 JOC is signaled, no flag stays unknown, AAC cannot carry Atmos', async () => {
  const report = await run(mp4({ audio: [{ code: 'ec-3', joc: true }, { code: 'ec-3', joc: false }, { code: 'mp4a' }] }));
  assert.equal(report.findings['audio.2.atmos'].state, 'signaled');
  assert.equal(report.findings['audio.3.atmos'].state, 'not_found_in_scope');
  assert.equal(report.findings['audio.4.atmos'].state, 'absent');
  assert.deepEqual(toSpec(report).audio.map(t => t.atmos), [true, null, false]);
});

test('MKV block-addition DV and colour static metadata, no Cues resync finds third-cluster HDR10+', async () => {
  const report = await run(mkv(), { policy: { ...unlimited, windowSamples: 1, windowCount: 3 } });
  assert.equal(report.findings['video.dolby_vision'].state, 'signaled');
  assert.equal(report.findings['video.hdr10.mdcv'].state, 'signaled');
  assert.equal(report.findings['video.hdr10_plus'].state, 'observed');
  assert.ok(report.findings['video.hdr10_plus'].evidence.some(e => e.kind === 'observed' && e.coverage.includes('2.000s')));
  assert.equal(toSpec(report).video.fourcc, '');
});

test('missing DV config plus observed RPU is conflicting, never a false negative', async () => {
  const report = await run(mp4({ samples: [sample({ rpu: true })] }));
  assert.equal(report.findings['video.dolby_vision'].state, 'conflicting');
  assert.equal(toSpec(report).video.dolby_vision.present, null);
});

test('truncated MP4 emits damaged structure evidence and exhausts safely', async () => {
  const report = await run(new File([u32(100), 'moov'], 'truncated.mp4'));
  assert.equal(report.terminal.reason, 'exhausted');
  for (const finding of Object.values(report.findings)) {
    assert.equal(finding.state, 'unsupported');
    assert.ok(finding.evidence.some(e => e.note.startsWith('Damaged structure:')));
  }
});

test('reader cancellation, overlap accounting, exact cache and bounded physical slices', async () => {
  const paused = new Gate(); paused.pause();
  const reader = new RangeReader(new Blob([new Uint8Array(100)]), { gate: paused });
  const pending = reader.read(0, 10); paused.cancel();
  await assert.rejects(pending, CancelledError);
  const r = new RangeReader(new Blob([new Uint8Array(100)]));
  await r.read(0, 20); await r.read(10, 20); await r.read(0, 20);
  assert.deepEqual(r.metrics, { bytesRequested: 40, uniqueBytes: 30, repeatedBytes: 10, readCount: 2 });
  assert.deepEqual(r.coveredRanges(), [[0, 30]]);
  const lengths = [];
  const blob = { size: 40 * 1024 ** 2, slice(a, b) { lengths.push(b - a); return new Blob([new Uint8Array(b - a)]); } };
  const big = new RangeReader(blob); assert.equal((await big.read(0, blob.size)).length, blob.size);
  assert.deepEqual(lengths, [16 * 1024 ** 2, 16 * 1024 ** 2, 8 * 1024 ** 2]);
  for (const args of [[-1, 1], [101, 0], [0, -1], [0.5, 1]]) await assert.rejects(r.read(...args), RangeError);
  const unavailable = new RangeReader({ size: 1, slice() { return { async arrayBuffer() { throw Object.assign(new Error('gone'), { name: 'NotReadableError' }); } }; } });
  await assert.rejects(unavailable.read(0, 1), FileUnavailableError);
});

test('recorded MediaInfo ground truth: E-AC-3 without JOC stays scoped; HDR10 keys map', async () => {
  const sdr = fromMediaInfo(JSON.parse(await readFile(new URL('./recorded/sdr-mkv.json', import.meta.url))));
  const audio = sdr.tracks.find(t => t.kind === 'audio');
  assert.equal(audio.codec, 'eac3'); assert.equal(audio.channels, 6);
  assert.equal(sdr.evidence.find(e => e.fieldId === `audio.${audio.id}.atmos`).kind, 'not_found_in_scope');
  for (const label of ['hdr10-mp4', 'hdr10-mkv']) {
    const parsed = fromMediaInfo(JSON.parse(await readFile(new URL(`./recorded/${label}.json`, import.meta.url))));
    assert.equal(parsed.tracks[0].color.transfer, 'smpte2084');
    for (const id of ['video.hdr10.mdcv', 'video.hdr10.cll']) assert.ok(parsed.evidence.some(e => e.fieldId === id && e.kind === 'observed'));
  }
});

test('evidence is append-only, scopes stay null, terminal unknown and conflicts are explicit', () => {
  const store = new EvidenceStore();
  assert.equal(store.conclude('empty').state, 'checking'); store.terminal = true;
  assert.equal(store.conclude('empty').state, 'unknown');
  store.add('test', { kind: 'not_found_in_scope', value: null, note: 'sampled only' });
  assert.equal(store.conclude('test').value, null);
  store.add('test', { kind: 'signaled', value: true }); store.add('test', { kind: 'observed', value: true });
  assert.equal(store.conclude('test').state, 'observed');
  store.add('test', { kind: 'absent', value: false });
  assert.equal(store.conclude('test').state, 'conflicting'); assert.equal(store.snapshot().test.evidence.length, 4);
});

test('HEVC parsing handles SEI extension, emulation prevention and malformed lengths', () => {
  assert.equal(inspectSample(hdr10plus(), 4).hdr10plus, true);
  assert.equal(inspectSample(nal(62), 4).dvRpu, true);
  const parsed = inspectSample(Buffer.concat([sei(137, Buffer.from([0, 0, 1])), sei(144, Buffer.alloc(4)), sei(260, Buffer.alloc(270)), Buffer.from([0, 0, 255, 255])]), 4);
  assert.equal(parsed.mdcvSei, true); assert.equal(parsed.cllSei, true); assert.equal(parsed.malformedSamples, 1);
  assert.deepEqual(windowFractions(8), [0, 0.5, 0.25, 0.75, 0.125, 0.375, 0.625, 0.875]);
});

test('malformed or skipped laced packets cannot establish a complete negative', async () => {
  const malformed = await run(mp4({ staticHdr: false, samples: [Buffer.from([0, 0, 99, 99])] }));
  assert.equal(malformed.findings['video.hdr10_plus'].state, 'not_found_in_scope');
  assert.equal(toSpec(malformed).video.hdr10_plus, null);
  const laced = await run(mkv({ laced: true, clusters: [sample()] }));
  assert.notEqual(laced.findings['video.hdr10_plus'].state, 'absent');
});

test('gate active time excludes pauses and deeper increases both limits', async () => {
  const gate = new Gate({ policy: { autoBytes: 10, autoMs: 100, deeperBytes: 20, deeperMs: 200 } });
  await gate.admit(10);
  const pending = gate.admit(1); assert.equal(gate.state, 'paused-at-limit');
  const frozen = gate.activeMs;
  await new Promise(resolve => setTimeout(resolve, 5)); assert.equal(gate.activeMs, frozen);
  gate.deeper(); await pending;
  assert.equal(gate.limitBytes, 30); assert.equal(gate.limitMs, 300);
});
