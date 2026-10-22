import test from 'node:test';
import assert from 'node:assert/strict';
import { parseAvcConfig, inspectAvcSample } from '../../dolby_tool/web/static/js/browser/parsers/avc.js';

// Generated syntax elements, not media files or release-specific fixtures.
function makeSps({ transfer = 1, primaries = 1, matrix = 1, description = true, vui = true, high = false, poc = 0 } = {}) {
  let bits = '';
  const u = (v, n) => { bits += v.toString(2).padStart(n, '0'); };
  const ue = v => { const code = (v + 1).toString(2); bits += '0'.repeat(code.length - 1) + code; };
  u(high ? 100 : 66, 8); u(0, 8); u(40, 8); ue(0);
  if (high) {
    ue(3); u(0, 1); ue(2); ue(2); u(0, 1); u(1, 1);
    for (let i = 0; i < 12; i++) {
      u(1, 1);
      // delta_scale = -8 makes nextScale zero: subsequent entries implicit.
      ue(16);
    }
  }
  ue(0); ue(poc);
  if (poc === 0) ue(0);
  if (poc === 1) { u(0, 1); ue(0); ue(0); ue(2); ue(0); ue(0); }
  ue(1); u(0, 1); ue(79); ue(44); u(1, 1); u(1, 1); u(0, 1); u(vui ? 1 : 0, 1);
  if (vui) {
    u(0, 1); u(0, 1); u(1, 1); u(5, 3); u(0, 1); u(description ? 1 : 0, 1);
    if (description) { u(primaries, 8); u(transfer, 8); u(matrix, 8); }
    // chroma location, timing, NAL/VCL HRD, pic_struct, restriction absent.
    u(0, 6);
  }
  u(1, 1); while (bits.length % 8) u(0, 1);
  return escapeNal(0x67, Uint8Array.from(bits.match(/.{8}/g).map(v => parseInt(v, 2))));
}
function escapeNal(header, bytes) {
  const out = [header]; let zeros = 0;
  for (const v of bytes) {
    if (zeros >= 2 && v <= 3) { out.push(3); zeros = 0; }
    out.push(v); zeros = v === 0 ? zeros + 1 : 0;
  }
  return Uint8Array.from(out);
}
function config(nals, size = 4) {
  return Uint8Array.from([1, 100, 0, 40, 252 | (size - 1), 224 | nals.length,
    ...nals.flatMap(n => [n.length >> 8, n.length & 255, ...n]), 1, 0, 2, 0x68, 0x80]);
}
function sample(nals, width = 4) {
  return Uint8Array.from(nals.flatMap(n => [...Array.from({ length: width }, (_, i) => Math.floor(n.length / 256 ** (width - i - 1)) & 255), ...n]));
}

test('AVC VUI retains numeric BT709, PQ, HLG and unspecified values', () => {
  for (const transfer of [1, 16, 18, 2]) {
    const sps = makeSps({ transfer, primaries: transfer === 1 ? 1 : 9, matrix: 2 });
    const parsed = parseAvcConfig(config([sps]));
    assert.equal(parsed.valid, true); assert.equal(parsed.complete, true);
    assert.equal(parsed.nalLengthSize, 4);
    assert.deepEqual(parsed.colors, [{ transfer, primaries: transfer === 1 ? 1 : 9, matrix: 2 }]);
    assert.deepEqual(inspectAvcSample(sample([sps])).colors, parsed.colors);
  }
});
test('missing VUI or color description never invents BT709', () => {
  for (const options of [{ vui: false }, { description: false }]) {
    const parsed = parseAvcConfig(config([makeSps(options)]));
    assert.equal(parsed.complete, true); assert.deepEqual(parsed.colors, []);
  }
});
test('high profile 4:4:4 scaling lists and every POC branch remain aligned', () => {
  for (const poc of [0, 1, 2]) {
    const parsed = parseAvcConfig(config([makeSps({ high: true, poc, transfer: 18 })]));
    assert.equal(parsed.complete, true); assert.equal(parsed.colors[0].transfer, 18);
  }
});
test('length prefixes, repeated SPS and positive evidence before truncation', () => {
  const sps = makeSps();
  for (const width of [1, 2, 3, 4]) {
    const parsed = inspectAvcSample(sample([sps, sps], width), width);
    assert.equal(parsed.complete, true); assert.equal(parsed.colors.length, 1);
  }
  const bytes = sample([sps]);
  const parsed = inspectAvcSample(Uint8Array.from([...bytes, 0]));
  assert.equal(parsed.malformedSamples, 1); assert.equal(parsed.complete, false);
  assert.equal(parsed.colors.length, 1);
  assert.equal(inspectAvcSample(bytes, 0).malformedSamples, 1);
});
test('truncated SPS and config never become complete', () => {
  const sps = makeSps({ high: true });
  for (let n = 0; n < sps.length; n++) {
    assert.equal(parseAvcConfig(config([sps.subarray(0, n)])).complete, false);
  }
  const bytes = config([sps]);
  for (let n = 0; n < bytes.length; n++) assert.equal(parseAvcConfig(bytes.subarray(0, n)).complete, false);
  assert.equal(parseAvcConfig(config([sps], 3)).valid, false);
});
test('invalid emulation prevention and unbounded Golomb are rejected', () => {
  for (const bytes of [[0x67, 0, 0, 3], [0x67, 0, 0, 3, 4], [0x67, 0, 0, 1], [0x67, 66, 0, 40, ...new Array(12).fill(0)]]) {
    assert.equal(inspectAvcSample(sample([Uint8Array.from(bytes)])).malformedSamples, 1);
  }
});
test('AVC SEI detects positive HDR metadata with escaped RBSP', () => {
  const payload = [4, 6, 181, 0, 60, 0, 1, 4, 137, 24, ...new Array(24).fill(0), 144, 4, 0, 0, 0, 1, 128];
  const parsed = inspectAvcSample(sample([escapeNal(6, payload)]));
  assert.equal(parsed.complete, true);
  assert.equal(parsed.hdr10plus, true); assert.equal(parsed.mdcvSei, true); assert.equal(parsed.cllSei, true);
  assert.equal(inspectAvcSample(sample([Uint8Array.from([6, 137, 24, 0])])).malformedSamples, 1);
});
test('AVC unspecified NALs and SDR VUI do not establish DV absence or presence', () => {
  const parsed = inspectAvcSample(sample([makeSps(), Uint8Array.of(25, 128), Uint8Array.of(30, 128)]));
  assert.equal(parsed.dvRpu, false); assert.equal(parsed.dvScanSupported, false);
  assert.equal(parseAvcConfig(config([makeSps()])).dvScanSupported, false);
});
