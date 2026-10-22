import test from 'node:test';
import assert from 'node:assert/strict';
import { parseAc4Dsi } from '../../dolby_tool/web/static/js/browser/parsers/ac4.js';
import { analyze } from '../../dolby_tool/web/static/js/browser/engine.js';
import { toSpec } from '../../dolby_tool/web/static/js/browser/report.js';
import { scoreReport, WEIGHTS } from '../../dolby_tool/web/static/js/browser/compare.js';
import { fromMediaInfo } from '../../dolby_tool/web/static/js/browser/parsers/mediainfo.js';
import { box, full, u16, u32, concat, mp4, mkv } from './fixtures.mjs';

// Extracted from Audio_ID_6ch_128kbps_25fps_ac4.mp4, box at offset 489,
// size 37 (8-byte header + these 29 bytes). MediaInfo 26.10: 5.1 Main,
// bitstream_version 2, one presentation, no immersive content.
const REAL_DAC4 = Buffer.from('20a401400000001fffffffe0010ff98000004800008e501000008f0080', 'hex');
const run = file => analyze(file, { mediainfo: false, policy: { autoBytes: Infinity, autoMs: Infinity } });
class Writer {
  constructor() { this.bits = ''; }
  put(value, count) { this.bits += value.toString(2).padStart(count, '0'); return this; }
  align() { while (this.bits.length % 8) this.put(0, 1); return this; }
  bytes() { this.align(); return Buffer.from(this.bits.match(/.{8}/g)?.map(s => parseInt(s, 2)) || []); }
}
function rate(w) { w.put(1, 2).put(128000, 32).put(0, 32); }
// Syntax-only signalling, NOT playable AC-4 or evidence of a decoder's output.
// ETSI TS 103 190-2 V1.3.1 Annex E.10/E.11, including optional fields.
function pres({ mode = 4, mask = 0x47, object = false, ajoc = false, config = 31, top = 2, indicator = false, version = 1, optional = false, extension = true } = {}) {
  const w = new Writer();
  w.put(config, 5);
  if (config !== 6) {
    w.put(3, 3).put(optional ? 1 : 0, 1);
    if (optional) w.put(7, 5);
    w.put(0, 2).put(0, 2).put(0, 5).put(0, 10).put(object ? 0 : 1, 1);
    if (!object) {
      w.put(mode, 5);
      if (mode >= 11 && mode <= 14) w.put(mask & 8 ? 1 : 0, 1).put(top, 2);
      w.put(mask, 24);
    }
    w.put(optional ? 1 : 0, 1);
    if (optional) w.put(1, 1).put(2, 2);
    w.put(optional ? 1 : 0, 1);
    if (optional) w.put(1, 1).put(2, 8).put(0, 16);
    const group = () => {
      w.put(1, 1).put(0, 1).put(object ? 0 : 1, 1).put(1, 8).put(0, 2).put(optional ? 1 : 0, 1);
      if (optional) w.put(10, 5);
      if (object) {
        w.put(ajoc ? 1 : 0, 1);
        if (ajoc) w.put(0, 1).put(5, 4).put(11, 6);
        w.put(1, 1).put(1, 1).put(0, 1).put(0, 1);
      } else w.put(mask, 24);
      w.put(optional ? 1 : 0, 1);
      if (optional) w.put(0, 3).put(1, 1).put(3, 6).put(0x656e67, 24);
    };
    if (config === 31) group();
    else {
      w.put(0, 1); // b_multi_pid
      if (config <= 5) {
        if (config === 5) w.put(2, 3); // four groups
        for (let i = 0; i < (config <= 2 ? 2 : config <= 4 ? 3 : 4); i++) group();
      } else w.put(0, 7); // unsupported extension, no skip bytes
    }
    w.put(version === 2 ? 1 : 0, 1).put(optional ? 1 : 0, 1);
  }
  if (optional || config === 6) w.put(1, 7).put(0, 5).put(0, 10);
  w.put(optional ? 1 : 0, 1);
  if (optional) rate(w);
  w.put(optional ? 1 : 0, 1);
  if (optional) w.align().put(1, 16).put(0x41, 8).put(1, 5).put(3, 3).put(1, 8);
  w.align();
  if (extension) {
    w.put(0, 1).put(indicator ? 1 : 0, 1).put(0, 4).put(optional ? 1 : 0, 1);
    if (optional) w.put(0x101, 9); else w.put(0, 1);
  }
  return { version, bytes: w.bytes() };
}
function dsi(presentations = [pres()], { version = 1, bitstream = 2, optional = false, wideLength = false } = {}) {
  const w = new Writer();
  w.put(version, 3).put(bitstream, 7).put(1, 1).put(2, 4).put(presentations.length, 9);
  if (bitstream > 1) {
    w.put(optional ? 1 : 0, 1);
    if (optional) w.put(1, 16).put(1, 1).put(0, 128);
  }
  rate(w);
  return concat(w.bytes(), ...presentations.map(p => {
    const bytes = wideLength ? concat(p.bytes, Buffer.alloc(255 - p.bytes.length)) : p.bytes;
    return concat([p.version, wideLength ? 255 : bytes.length], ...(wideLength ? [u16(0)] : []), bytes);
  }));
}
function descriptions(configs) {
  const header = Buffer.alloc(28); header.writeUInt16BE(2, 16); header.writeUInt32BE(48000 * 65536, 24);
  const entries = configs.map(bytes => box('ac-4', header, ...(bytes ? [box('dac4', bytes)] : [])));
  const mdhd = full('mdhd', u32(0), u32(0), u32(48000), u32(48000), u16(0), u16(0));
  const tkhd = full('tkhd', u32(0), u32(0), u32(1), Buffer.alloc(64));
  return new File([box('ftyp', Buffer.from('isom'), u32(0)), box('moov', box('trak', tkhd, box('mdia', mdhd, full('hdlr', u32(0), Buffer.from('soun'), Buffer.alloc(12)), box('minf', box('stbl', full('stsd', u32(entries.length), ...entries))))))], 'descriptions.mp4');
}

test('real Dolby dac4 bytes conclusively signal 5.1 without immersive content', () => {
  const p = parseAc4Dsi(REAL_DAC4);
  assert.equal(p.kind, 'absent', p.note); assert.equal(p.value, false);
  assert.equal(p.complete, true); assert.equal(p.presentations.length, 1);
  assert.equal(p.channels, 6); assert.equal(p.channelLayout, '5.1');
  assert.equal(p.sampleRateHz, 48000); assert.match(p.note, /all 1 presentation.*fully parsed/);
});
test('constructed height modes, IMS, indicator and object/A-JOC groups are signalled', () => {
  for (const options of [
    { mode: 12, mask: 0x7f }, { mode: 12, mask: 0x77 }, { mode: 10, mask: 0x57 },
    { mode: 1, mask: 1, version: 2 }, { mode: 1, mask: 1, indicator: true },
    { object: true }, { object: true, ajoc: true, optional: true },
  ]) {
    const p = parseAc4Dsi(dsi([pres(options)]));
    assert.equal(p.kind, 'signaled', p.note); assert.equal(p.value, true);
    assert.equal(p.complete, true, p.note);
  }
  const p = parseAc4Dsi(dsi([pres({ mode: 12, mask: 0x77 })]));
  assert.equal(p.channels, 10); assert.equal(p.channelLayout, '5.1.4');
});
test('first presentation layout is not a sum or the last alternative; any positive wins', () => {
  for (const presentations of [
    [pres({ mode: 1, mask: 1 }), pres({ mode: 12, mask: 0x7f })],
    [pres({ mode: 1, mask: 1 }), { version: 99, bytes: Buffer.alloc(1) }, pres({ object: true })],
  ]) {
    const p = parseAc4Dsi(dsi(presentations));
    assert.equal(p.kind, 'signaled'); assert.equal(p.channels, 2); assert.equal(p.channelLayout, 'stereo');
  }
});
test('all presentation/group loops and optional syntax must finish before a negative', () => {
  for (const config of [0, 1, 2, 3, 4, 5, 31]) {
    const p = parseAc4Dsi(dsi([pres({ config, optional: true }), pres({ mode: 1, mask: 1, extension: false })], { optional: true }));
    assert.equal(p.complete, true, p.note); assert.equal(p.kind, 'absent', p.note);
    assert.equal(p.channels, 6);
  }
});
test('every byte truncation of real dac4 remains inconclusive, never negative', () => {
  for (let length = 0; length < REAL_DAC4.length; length++) {
    const p = parseAc4Dsi(REAL_DAC4.subarray(0, length));
    assert.notEqual(p.kind, 'absent', `length=${length}`); assert.equal(p.value, null);
  }
});
test('unknown versions, reserved syntax, missing presentations and extension bytes never become negative', () => {
  for (const bytes of [
    dsi([], { version: 0 }), dsi([], { version: 2 }), dsi([pres()], { bitstream: 3 }), dsi([]),
    dsi([{ version: 0, bytes: pres().bytes }]), dsi([{ version: 9, bytes: Buffer.alloc(1) }]),
    dsi([pres({ mode: 16 })]), dsi([pres({ config: 7 })]), dsi([pres({ config: 6 })]),
    dsi([pres({ mask: 0 })]), dsi([pres()], { wideLength: true }), concat(dsi(), [0]),
  ]) {
    const p = parseAc4Dsi(bytes); assert.notEqual(p.kind, 'absent', p.note); assert.equal(p.value, null, p.note);
  }
  const p = parseAc4Dsi(dsi([pres(), { version: 99, bytes: Buffer.alloc(1) }]));
  assert.equal(p.kind, 'not_found_in_scope'); assert.match(p.note, /presentation 2: unsupported/);
});
test('pres_bytes bounds prevent reading a following presentation as part of a truncated one', () => {
  const short = pres(); short.bytes = short.bytes.subarray(0, 6);
  const p = parseAc4Dsi(dsi([short, pres()]));
  assert.equal(p.kind, 'not_found_in_scope', p.note); assert.equal(p.value, null);
  const extension = pres({ optional: true }); extension.bytes = extension.bytes.subarray(0, extension.bytes.length - 1);
  assert.notEqual(parseAc4Dsi(dsi([extension])).kind, 'absent');
});
test('MP4 and Matroska integrate DSI channels and evidence with unchanged native Atmos scoring', async () => {
  for (const make of [
    bytes => mp4({ audio: [{ code: 'ac-4', dsi: bytes }] }),
    bytes => mkv({ audio: [{ code: 'A_AC4', dsi: bytes, samples: [] }] }),
  ]) {
    for (const [bytes, kind, points] of [[REAL_DAC4, 'absent', 0], [dsi([pres({ object: true })]), 'signaled', WEIGHTS.atmos], [REAL_DAC4.subarray(0, 12), 'unsupported', null]]) {
      const report = await run(make(bytes)), spec = toSpec(report);
      assert.equal(spec.audio[0].codec, 'ac4');
      assert.equal(report.findings['audio.2.atmos'].state, kind);
      const score = scoreReport(report).components.atmos;
      assert.deepEqual(score, points == null ? { min: 0, max: WEIGHTS.atmos } : { min: points, max: points });
      if (kind === 'absent') { assert.equal(spec.audio[0].channels, 6); assert.equal(spec.audio[0].channel_layout, '5.1'); }
    }
  }
});
test('MP4 requires every sample description; a missing/unsupported dac4 cannot be erased by a negative', async () => {
  for (const configs of [[REAL_DAC4, null], [REAL_DAC4, dsi([pres()], { version: 2 })], [null]]) {
    const report = await run(descriptions(configs));
    assert.notEqual(report.findings['audio.1.atmos'].state, 'absent');
    assert.equal(toSpec(report).audio[0].atmos, null);
  }
  const report = await run(descriptions([REAL_DAC4, dsi([pres({ mode: 12, mask: 0x77 })])]));
  assert.equal(report.findings['audio.1.atmos'].state, 'signaled');
  assert.equal(toSpec(report).audio[0].channels, 6); // first, not 10 or fixed channelcount=2
});
test('missing/encoded MKV private data stays unsupported; MediaInfo maps AC-4 without a false negative', async () => {
  for (const encoded of [false, true]) {
    const report = await run(mkv({ audio: [{ code: 'A_AC4', ...(encoded ? { dsi: REAL_DAC4 } : {}), encoded, samples: [] }] }));
    assert.equal(report.findings['audio.2.atmos'].state, 'unsupported');
  }
  const parsed = fromMediaInfo({ media: { track: [{ '@type': 'Audio', Format: 'AC-4', Channels: '6' }] } });
  assert.equal(parsed.tracks[0].codec, 'ac4'); assert.equal(parsed.evidence.length, 0);
});
