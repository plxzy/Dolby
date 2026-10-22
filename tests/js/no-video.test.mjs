import test from 'node:test';
import assert from 'node:assert/strict';
import { analyze } from '../../dolby_tool/web/static/js/browser/engine.js';
import { toSpec } from '../../dolby_tool/web/static/js/browser/report.js';
import { VIDEO_FIELDS } from '../../dolby_tool/web/static/js/browser/evidence.js';
import { hdrVerdict } from '../../dolby_tool/web/static/js/hdr.js';
import { mp4, mkv, box, concat, u32, el, uint } from './fixtures.mjs';

const policy = { autoBytes: Infinity, autoMs: Infinity };
const run = (file, options = {}) => analyze(file, { mediainfo: false, policy, ...options });
const hdr = video => hdrVerdict(video, { mode: 'browser', transferName: String });
const partialAudioInfo = { analyzeData: async () => ({ media: { track: [{ '@type': 'General', Format: 'MPEG-4' }, { '@type': 'Audio', ID: '2', Format: 'AAC' }] } }) };

for (const [name, file] of [
  ['MP4', () => mp4({ video: false, audio: [{ code: 'mp4a' }] })],
  ['MKV', () => mkv({ video: false, clusters: [], audio: [{ code: 'A_AAC', samples: [] }] })],
]) {
  test(`${name}: complete audio-only track table proves no video, without inventing a video bitrate`, async () => {
    const reports = [];
    const report = await run(file(), { onReport: r => reports.push(r) });
    assert.equal(reports[0].videoPresent, null);
    assert.equal(report.structureComplete, true);
    assert.equal(report.videoPresent, false);
    for (const id of VIDEO_FIELDS) {
      assert.equal(report.findings[id].state, 'absent');
      assert.equal(report.findings[id].summary, 'No video track in the complete container structure');
    }
    const spec = toSpec(report);
    assert.equal(spec.audio.length, 1);
    assert.equal(spec.video.present, false);
    assert.equal(spec.video.bit_rate_mbps, null);
    assert.deepEqual(hdr(spec.video), { value: 'No video', detail: 'Audio-only file', tone: 'neutral' });
  });
}

test('existing video and native HDR verdicts keep their original meaning', async () => {
  const spec = toSpec(await run(mp4()));
  assert.equal(spec.video.present, true);
  assert.equal(hdr(spec.video).value, 'HDR10');
  assert.equal(hdr({ present: null }).value, 'Unknown');
  assert.equal(hdr({}).value, 'Unknown');
  assert.equal(hdrVerdict({ present: false }, { mode: 'native', transferName: String }).value, 'SDR');
});

async function appendMovieEntry(file, extra) {
  const bytes = Buffer.from(await file.arrayBuffer());
  const start = bytes.indexOf('moov') - 4, size = bytes.readUInt32BE(start), end = start + size;
  bytes.writeUInt32BE(size + extra.length, start);
  return new File([bytes.subarray(0, end), extra, bytes.subarray(end)], 'damaged.mp4');
}

test('damaged or incomplete MP4 track tables stay unknown even if MediaInfo returns audio', async () => {
  const audio = mp4({ video: false, audio: [{ code: 'mp4a' }] });
  const files = [
    new File([u32(100), 'moov'], 'truncated.mp4'),
    new File([audio, new Uint8Array([1, 2, 3])], 'trailing-header.mp4'),
    await appendMovieEntry(audio, box('trak')),
    await appendMovieEntry(audio, concat(u32(100), 'trak')),
  ];
  for (const file of files) {
    for (const mediainfo of [false, partialAudioInfo]) {
      const report = await run(file, { mediainfo });
      assert.equal(report.videoPresent, null, file.name);
      assert.notEqual(report.findings['video.dolby_vision'].state, 'absent');
      assert.notEqual(hdr(toSpec(report).video).value, 'No video');
    }
  }
});

test('missing Matroska track type cannot establish no-video absence', async () => {
  const file = new File([el(0x1a45dfa3), el(0x18538067, el(0x1654ae6b, el(0xae, uint(0xd7, 1), el(0x86, Buffer.from('A_AAC')))))], 'missing-type.mkv');
  const report = await run(file);
  assert.equal(report.structureComplete, false);
  assert.equal(report.videoPresent, null);
  assert.equal(report.findings['video.dolby_vision'].state, 'unsupported');
});

test('MediaInfo-only partial audio inventory is not proof of no video', async () => {
  const report = await run(new File(['unrecognised container'], 'audio.ts'), { mediainfo: partialAudioInfo });
  assert.equal(report.tracks[0].kind, 'audio');
  assert.equal(report.videoPresent, null);
  assert.equal(report.findings['video.dolby_vision'].state, 'unsupported');
});
