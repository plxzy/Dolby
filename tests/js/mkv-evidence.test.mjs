import test from 'node:test';
import assert from 'node:assert/strict';
import { analyze } from '../../dolby_tool/web/static/js/browser/engine.js';
import { Gate } from '../../dolby_tool/web/static/js/browser/gate.js';
import { RangeReader } from '../../dolby_tool/web/static/js/browser/reader.js';
import { clusterBlocks, elementHeader, elements } from '../../dolby_tool/web/static/js/browser/parsers/mkv.js';
import { inspectHevcConfig } from '../../dolby_tool/web/static/js/browser/parsers/hevc.js';
import { scoreReport } from '../../dolby_tool/web/static/js/browser/compare.js';
import { toSpec } from '../../dolby_tool/web/static/js/browser/report.js';
import { mkv, mp4, sample, hdr10plus, u32, u16, concat, el, uint, ebmlSize, avcConfig, eac3Frame } from './fixtures.mjs';
import { hdrVerdict } from '../../dolby_tool/web/static/js/hdr.js';

const policy = { autoBytes: Infinity, autoMs: Infinity, windowCount: 1, windowSamples: 1 };
const run = file => analyze(file, { mediainfo: false, policy });
const avcPacket = () => concat(u32(2), [0x65, 0x80]);
const avcMkv = (transferCode = 1, extras = {}) => mkv({ dv: false, videoCodec: 'V_MPEG4/ISO/AVC', codecPrivate: avcConfig(transferCode), colorTransfer: null, staticHdr: false, clusters: [avcPacket()], ...extras });

test('MKV DV absence needs a complete valid HEVC sweep; missing mapping alone is not absence', async () => {
  const report = await run(mkv({ dv: false, clusters: [sample(), sample()] }));
  assert.equal(report.findings['video.dolby_vision'].state, 'absent');
  assert.match(report.findings['video.dolby_vision'].summary, /complete scan/);
  assert.match(report.findings['video.dolby_vision'].evidence.at(-1).coverage, /100% of 2/);
  const found = await run(mkv({ dv: false, clusters: [sample(), sample({ rpu: true })] }));
  assert.equal(found.findings['video.dolby_vision'].state, 'observed');
});

test('CodecPrivate RPU and damaged hvcC are not converted into a negative', async () => {
  const config = Buffer.alloc(23); config[0] = 1; config[21] = 3; config[22] = 1;
  const withRpu = concat(config, [62], u16(1), u16(2), [124, 1]);
  assert.deepEqual(inspectHevcConfig(withRpu), { complete: true, dvRpu: true });
  const found = await run(mkv({ dv: false, codecPrivate: withRpu, clusters: [sample()] }));
  assert.equal(found.findings['video.dolby_vision'].state, 'observed');
  const bad = await run(mkv({ dv: false, codecPrivate: withRpu.subarray(0, 24), clusters: [sample()] }));
  assert.equal(toSpec(bad).video.dolby_vision.present, null);
});

test('HEVC suffix SEI and CodecPrivate SEI are included in the metadata check', async () => {
  const suffix = hdr10plus(); suffix[4] = 40 << 1;
  const config = Buffer.alloc(23); config[0] = 1; config[21] = 3; config[22] = 1;
  const payload = suffix.subarray(4), privateData = concat(config, [40], u16(1), u16(payload.length), payload);
  for (const options of [{ clusters: [suffix] }, { codecPrivate: privateData, clusters: [sample()] }]) {
    const report = await run(mkv({ dv: false, staticHdr: false, ...options }));
    assert.equal(report.findings['video.hdr10_plus'].state, 'observed');
  }
});

test('explicit AVC SPS BT709 resolves HDR evidence in both MKV and MP4 without HEVC-only fallback', async () => {
  for (const file of [avcMkv(), mp4({ fourcc: 'avc1', avcc: avcConfig(), colr: false, staticHdr: false, samples: [avcPacket()] })]) {
    const report = await run(file);
    assert.equal(report.findings['video.transfer'].state, 'observed');
    assert.equal(toSpec(report).video.color_transfer, 'bt709');
    for (const id of ['video.hdr10.mdcv', 'video.hdr10.cll', 'video.hdr10_plus']) assert.equal(report.findings[id].state, 'absent');
  }
  const mkvReport = await run(avcMkv());
  assert.equal(toSpec(mkvReport).video.dolby_vision.present, null);
  assert.match(mkvReport.findings['video.dolby_vision'].summary, /profile 9/);
});

test('unspecified AVC transfer remains honest while complete SEI absence is conclusive', async () => {
  const report = await run(avcMkv(2));
  assert.equal(toSpec(report).video.color_transfer, null);
  assert.equal(report.findings['video.transfer'].state, 'not_found_in_scope');
  assert.match(report.findings['video.transfer'].summary, /not proof of SDR/);
  assert.equal(report.findings['video.hdr10_plus'].state, 'absent');
  assert.equal(report.findings['video.hdr10.mdcv'].state, 'absent');
  // The HDR card must not turn complete HDR-metadata absence into an SDR claim.
  const card = hdrVerdict(toSpec(report).video, { mode: 'browser', transferName: String });
  assert.equal(card.value, 'Unknown');
  assert.equal(hdrVerdict(toSpec(await run(avcMkv())).video, { mode: 'browser', transferName: String }).value, 'SDR');
});

test('EAC3 whole-track BSI absence and late JOC signaling work in MKV and MP4', async () => {
  for (const kind of ['mkv', 'mp4']) {
    const make = packets => kind === 'mkv'
      ? mkv({ dv: false, clusters: [sample()], audio: [{ samples: packets }] })
      : mp4({ samples: [sample()], audio: [{ code: 'ec-3', samples: packets }] });
    const plain = await run(make([eac3Frame(), eac3Frame(), eac3Frame()]));
    assert.equal(plain.findings['audio.2.atmos'].state, 'absent');
    assert.match(plain.findings['audio.2.atmos'].summary, /any of 3 E-AC-3 frames/);
    assert.match(plain.findings['audio.2.atmos'].evidence.at(-1).coverage, /100% of 3/);
    assert.equal(scoreReport(plain).components.atmos.max, 0);
    const joc = await run(make([eac3Frame(), eac3Frame(), eac3Frame(true)]));
    assert.equal(joc.findings['audio.2.atmos'].state, 'signaled');
    assert.equal(toSpec(joc).audio[0].atmos, true);
  }
});

test('malformed, missing and empty audio cannot establish Atmos absence', async () => {
  for (const packets of [[], [eac3Frame().subarray(0, 20)], [eac3Frame(), Buffer.from('JOC')]]) {
    const report = await run(mkv({ clusters: [sample()], audio: [{ samples: packets }] }));
    assert.notEqual(report.findings['audio.2.atmos'].state, 'absent');
    assert.equal(toSpec(report).audio[0].atmos, null);
  }
});

test('all Matroska lace modes yield exact frames, including unequal EBML deltas', async () => {
  for (const mode of [1, 2, 3]) {
    const packets = mode === 2 ? [eac3Frame(), eac3Frame()] : [eac3Frame(), concat(eac3Frame(), eac3Frame()), eac3Frame()];
    const lace = mode === 1 ? concat([packets.length - 1], ...packets.slice(0, -1).map(p => concat(Array(Math.floor(p.length / 255)).fill(255), [p.length % 255])))
      : mode === 2 ? Buffer.from([1]) : concat([2], ebmlSize(128), ebmlSize(128 + 8191));
    const bytes = el(0x1f43b675, uint(0xe7, 0), el(0xa3, [0x82, 0, 0, 0x80 | (mode << 1)], lace, ...packets));
    const reader = new RangeReader(new Blob([bytes]));
    const cluster = await elementHeader(reader, 0), structure = { primaryVideo: { id: 1 }, timestampScale: 1e6 };
    const found = [];
    for await (const block of clusterBlocks(reader, cluster, structure, [2], 4096)) found.push(Buffer.from(block.bytes));
    assert.deepEqual(found, packets);
    assert.equal(structure.scanIncomplete, undefined);
  }
});

async function withBlockGroup(extra) {
  const bytes = Buffer.from(await mkv({ dv: false, clusters: [] }).arrayBuffer());
  const segment = [...elements(bytes)].find(e => e.id === 0x18538067);
  return new File([bytes.subarray(0, segment.start), el(0x18538067, bytes.subarray(segment.data, segment.end), el(0x1f43b675, uint(0xe7, 0), el(0xa0, el(0xa1, [0x81, 0, 0, 0], sample()), extra)))], 'group.mkv');
}
test('unparsed BlockAdditions and CodecState block exhaustive negative conclusions', async () => {
  for (const extra of [el(0x75a1, el(0xa6, uint(0xee, 4), el(0xa5, [181, 0, 60, 0, 1, 4]))), el(0xa4, [1, 2, 3])]) {
    const report = await run(await withBlockGroup(extra));
    assert.notEqual(report.findings['video.dolby_vision'].state, 'absent');
    assert.notEqual(report.findings['video.hdr10_plus'].state, 'absent');
  }
});

test('auto-pause preserves intervals until complete deeper scan, without raising defaults', async () => {
  const file = mkv({ dv: false, clusters: Array.from({ length: 30 }, () => sample({ size: 4096 })), audio: [{ samples: Array.from({ length: 30 }, () => eac3Frame()) }] });
  const limited = { ...policy, autoBytes: 32768, deeperBytes: 1024 ** 2, sweepBatchBytes: 8192 };
  const gate = new Gate({ policy: limited }); let atLimit;
  const paused = new Promise(resolve => { atLimit = resolve; });
  const pending = analyze(file, { mediainfo: false, gate, policy: limited, onReport: report => { if (report.state === 'paused-at-limit') atLimit(report); } });
  const before = await paused;
  assert.notEqual(before.findings['video.dolby_vision'].state, 'absent');
  assert.notEqual(before.findings['audio.2.atmos'].state, 'absent');
  assert.equal(scoreReport(before).resolved, false);
  gate.deeper();
  const after = await pending;
  assert.equal(after.findings['video.dolby_vision'].state, 'absent');
  assert.equal(after.findings['audio.2.atmos'].state, 'absent');
  assert.equal(scoreReport(after).resolved, true);
});
