import test from 'node:test';
import assert from 'node:assert/strict';
import { analyze } from '../../dolby_tool/web/static/js/browser/engine.js';
import { parseMp4Structure } from '../../dolby_tool/web/static/js/browser/parsers/mp4.js';
import { elements } from '../../dolby_tool/web/static/js/browser/parsers/mkv.js';
import { RangeReader } from '../../dolby_tool/web/static/js/browser/reader.js';
import { toSpec } from '../../dolby_tool/web/static/js/browser/report.js';
import { fromMediaInfo } from '../../dolby_tool/web/static/js/browser/parsers/mediainfo.js';
import { readFile } from 'node:fs/promises';
import { box, u32, u64, mp4, mkv, sample, concat } from './fixtures.mjs';

const run = file => analyze(file, { mediainfo: false, policy: { autoBytes: Infinity, autoMs: Infinity } });

test('fragment tfhd/trun offsets follow default-base-is-moof and explicit base_data_offset', async () => {
  for (const explicit of [false, true]) {
    const base = Buffer.from(await mp4({ dv: true }).arrayBuffer());
    const packet = sample({ hdr: true });
    const tfhd = box('tfhd', u32(explicit ? 0x11 : 0x20010), u32(1), ...(explicit ? [u64(0)] : []), u32(packet.length));
    const moof = offset => box('moof', box('traf', tfhd, box('trun', u32(1), u32(1), u32(offset))));
    const fragment = moof(explicit ? base.length + moof(0).length + 8 : moof(0).length + 8);
    const file = new File([base, fragment, box('mdat', packet)], 'fragment.mp4');
    const structure = await parseMp4Structure(new RangeReader(file), file.size);
    assert.equal(structure.samples.sizes[0], packet.length);
    assert.equal(structure.samples.offsets[0], base.length + fragment.length + 8);
    const report = await run(file);
    assert.equal(report.findings['video.hdr10_plus'].state, 'observed');
  }
});

test('64-bit and EOF-sized top-level boxes skip payloads', async () => {
  const original = Buffer.from(await mp4({ dv: true }).arrayBuffer());
  const prefix = concat(u32(1), Buffer.from('free'), u64(32), Buffer.alloc(16));
  const file = new File([prefix, original, u32(0), 'free', Buffer.alloc(20)], 'wide.mp4');
  const report = await run(file);
  assert.equal(report.findings['video.dolby_vision'].state, 'signaled');
  assert.equal(report.terminal.reason, 'resolved');
});

test('unknown-sized Segment and Cluster reach later clusters and complete a negative scan', async () => {
  const original = Buffer.from(await mkv({ clusters: Array.from({ length: 3 }, () => sample({ size: 80 })) }).arrayBuffer());
  const top = [...elements(original)], segment = top.find(x => x.id === 0x18538067), nodes = [...elements(original, segment.data, segment.end)];
  const unknown = Buffer.from([1, 255, 255, 255, 255, 255, 255, 255]);
  let firstCluster = true;
  const chunks = nodes.map(node => {
    if (node.id === 0x1f43b675 && firstCluster) { firstCluster = false; return concat(Buffer.from('1f43b675', 'hex'), unknown, original.subarray(node.data, node.end)); }
    return original.subarray(node.start, node.end);
  });
  const file = new File([original.subarray(0, segment.start), Buffer.from('18538067', 'hex'), unknown, ...chunks], 'unknown.mkv');
  const report = await run(file);
  assert.equal(report.findings['video.hdr10_plus'].state, 'absent');
  assert.equal(report.coverage.video.samplesInspected, 3);
  assert.equal(report.coverage.video.samplesTotal, 3);
});

test('MediaInfo HDR10+ recorded keys and id matching preserve structure facts', async () => {
  for (const label of ['hdr10plus-mp4', 'hdr10plus-mkv']) {
    const result = JSON.parse(await readFile(new URL(`./recorded/${label}.json`, import.meta.url)));
    const parsed = fromMediaInfo(result, [{ id: 1, kind: 'video', codec: 'hevc', width: 1280, color: { transfer: null } }]);
    assert.equal(parsed.tracks.length, 1);
    assert.equal(parsed.tracks[0].width, 1280);
    assert.equal(parsed.tracks[0].color.transfer, 'smpte2084');
    assert.ok(parsed.evidence.some(e => e.fieldId === 'video.hdr10_plus' && e.kind === 'observed'));
  }
});

test('MP4 FourCC warnings match native wording; MKV never warns', async () => {
  const report = await run(mp4({ dv: true }));
  assert.equal(toSpec(report).video.fourcc_warning, "video sample-entry FourCC is 'hvc1' — TV.app will NOT engage the DV pipeline. Remux with `ffmpeg -c copy -tag:v dvh1 -strict unofficial`.");
  const changed = structuredClone(report);
  changed.findings['video.dolby_vision'].value.profile_raw = 7;
  assert.equal(toSpec(changed).video.fourcc_warning, "video sample-entry FourCC is 'hvc1' — TV.app will NOT engage the DV pipeline. This is dual-layer Dolby Vision profile 7, which Apple players do not support at all: convert it to profile 8.1 first (e.g. `dovi_tool -m 2 convert`), then remux with `-tag:v dvh1`.");
  const matroska = await run(mkv()); assert.equal(toSpec(matroska).video.fourcc_warning, null);
});

test('AC-3/E-AC-3 channel layout comes from dac3/dec3, not the fixed sample-entry count', async () => {
  const { dolbyChannels } = await import('../../dolby_tool/web/static/js/browser/parsers/mp4.js');
  // dec3 from a Dolby-authored 5.1 E-AC-3 MP4 whose sample entry says channelcount=2.
  assert.equal(dolbyChannels('dec3', Uint8Array.from([0x08, 0x00, 0x20, 0x0f, 0x00])), 6);
  // 5.1 independent substream plus a dependent substream adding Lrs/Rrs (7.1).
  assert.equal(dolbyChannels('dec3', Uint8Array.from([0x08, 0x00, 0x20, 0x0f, 0x02, 0x80])), 8);
  // dac3: acmod 2 (stereo), no LFE.
  assert.equal(dolbyChannels('dac3', Uint8Array.from([0x10, 0x10, 0x00])), 2);
  assert.equal(dolbyChannels('dec3', Uint8Array.from([0x08, 0x00])), null);
});
