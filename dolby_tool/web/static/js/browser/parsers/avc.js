// ITU-T H.264 (08/2024), 7.3.2.1.1, Annex E: https://www.itu.int/rec/T-REC-H.264
// avcC: ISO/IEC 14496-15, AVCDecoderConfigurationRecord.
// Dolby's public profile/ISOBMFF specs establish AVC profile 9, but do not
// specify a verifiable AVC RPU NAL syntax. Never treat unspecified NAL 25/30
// (or SDR VUI) as proof of presence/absence. Container DV signaling is separate.
// https://professionalsupport.dolby.com/s/article/What-is-Dolby-Vision-Profile?language=en_US
const HIGH = new Set([100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135]);
const MAX_SPS = 65535;
class Bits {
  constructor(bytes) { this.bytes = bytes; this.pos = 0; }
  u(n) {
    if (this.pos + n > this.bytes.length * 8) throw Error('truncated');
    let value = 0;
    for (let i = 0; i < n; i++, this.pos++) value = value * 2 + ((this.bytes[this.pos >> 3] >> (7 - (this.pos & 7))) & 1);
    return value;
  }
  ue(max = 0x7fffffff) {
    let zeros = 0;
    while (!this.u(1)) if (++zeros > 30) throw Error('golomb');
    const value = 2 ** zeros - 1 + this.u(zeros);
    if (value > max) throw Error('range');
    return value;
  }
  se() { const v = this.ue(); return v & 1 ? (v + 1) / 2 : -v / 2; }
}
function rbsp(nal) {
  if (nal.length > MAX_SPS || nal.length < 2 || (nal[0] & 0x80)) throw Error('NAL');
  const out = new Uint8Array(nal.length - 1);
  let n = 0, zeros = 0;
  for (let i = 1; i < nal.length; i++) {
    const v = nal[i];
    if (zeros >= 2) {
      if (v === 3) {
        if (i + 1 === nal.length || nal[i + 1] > 3) throw Error('escape');
        zeros = 0; continue;
      }
      if (v < 3) throw Error('unescaped');
    }
    out[n++] = v; zeros = v === 0 ? zeros + 1 : 0;
  }
  return out.subarray(0, n);
}
function hrd(b) {
  const count = b.ue(31) + 1; b.u(8);
  for (let i = 0; i < count; i++) { b.ue(); b.ue(); b.u(1); }
  b.u(20);
}
function sps(nal) {
  if ((nal[0] & 31) !== 7) throw Error('not SPS');
  const b = new Bits(rbsp(nal));
  const profile = b.u(8); if (b.u(8) & 3) throw Error('reserved'); b.u(8); b.ue(31);
  let chroma = 1;
  if (HIGH.has(profile)) {
    chroma = b.ue(3); if (chroma === 3) b.u(1);
    b.ue(6); b.ue(6); b.u(1);
    if (b.u(1)) for (let i = 0; i < (chroma === 3 ? 12 : 8); i++) {
      if (!b.u(1)) continue;
      let last = 8, next = 8;
      for (let j = 0; j < (i < 6 ? 16 : 64); j++) {
        if (next !== 0) { const delta = b.se(); if (delta < -128 || delta > 127) throw Error('scale'); next = (last + delta + 256) % 256; }
        last = next === 0 ? last : next;
      }
    }
  }
  if (!HIGH.has(profile) && ![66, 77, 88].includes(profile)) throw Error('unsupported profile');
  b.ue(12); const poc = b.ue(2);
  if (poc === 0) b.ue(12);
  if (poc === 1) { b.u(1); b.se(); b.se(); const count = b.ue(255); for (let i = 0; i < count; i++) b.se(); }
  b.ue(65535); b.u(1); b.ue(65535); b.ue(65535);
  if (!b.u(1)) b.u(1);
  b.u(1); if (b.u(1)) for (let i = 0; i < 4; i++) b.ue();
  let color = null;
  if (b.u(1)) {
    if (b.u(1)) { if (b.u(8) === 255) { b.u(16); b.u(16); } }
    if (b.u(1)) b.u(1);
    if (b.u(1)) {
      b.u(3); b.u(1);
      if (b.u(1)) { const primaries = b.u(8), transfer = b.u(8), matrix = b.u(8); color = { transfer, primaries, matrix }; }
    }
    if (b.u(1)) { b.ue(5); b.ue(5); }
    if (b.u(1)) { b.u(32); b.u(32); b.u(1); }
    const nalHrd = b.u(1); if (nalHrd) hrd(b);
    const vclHrd = b.u(1); if (vclHrd) hrd(b);
    if (nalHrd || vclHrd) b.u(1);
    b.u(1);
    if (b.u(1)) { b.u(1); b.ue(16); b.ue(16); b.ue(16); b.ue(16); b.ue(65535); b.ue(65535); }
  }
  if (b.u(1) !== 1) throw Error('trailing');
  while (b.pos & 7) if (b.u(1)) throw Error('trailing');
  if (b.pos !== b.bytes.length * 8) throw Error('trailing');
  return color;
}
function addColor(result, color) {
  if (color && !result.colors.some(c => c.transfer === color.transfer && c.primaries === color.primaries && c.matrix === color.matrix)) result.colors.push(color);
}
export function parseAvcConfig(bytes) {
  const result = { nalLengthSize: null, colors: [], valid: false, complete: false, spsCount: 0, dvScanSupported: false };
  try {
    if (bytes.length < 7 || bytes[0] !== 1 || (bytes[4] & 252) !== 252 || (bytes[5] & 224) !== 224) throw Error('avcC');
    result.nalLengthSize = (bytes[4] & 3) + 1;
    if (result.nalLengthSize === 3) throw Error('reserved length');
    let p = 6;
    const readNal = type => {
      if (p + 2 > bytes.length) throw Error('length');
      const length = bytes[p] * 256 + bytes[p + 1]; p += 2;
      if (!length || p + length > bytes.length || (bytes[p] & 128) || (bytes[p] & 31) !== type) throw Error('NAL');
      const nal = bytes.subarray(p, p + length); p += length; return nal;
    };
    const count = bytes[5] & 31;
    for (let i = 0; i < count; i++) { addColor(result, sps(readNal(7))); result.spsCount++; }
    if (p >= bytes.length) throw Error('PPS count');
    const pps = bytes[p++]; for (let i = 0; i < pps; i++) readNal(8);
    if (p < bytes.length) {
      if (![100, 110, 122, 144].includes(bytes[1]) || p + 4 > bytes.length || (bytes[p] & 252) !== 252 || (bytes[p + 1] & 248) !== 248 || (bytes[p + 2] & 248) !== 248) throw Error('extension');
      p += 3; const countExt = bytes[p++]; for (let i = 0; i < countExt; i++) readNal(13);
    }
    if (p !== bytes.length) throw Error('trailing');
    result.valid = true; result.complete = true;
  } catch { /* Preserve positive observations, never claim complete on damage. */ }
  return result;
}
export function inspectAvcSample(bytes, nalLengthSize = 4) {
  const result = { colors: [], malformedSamples: 0, dvRpu: false, dvScanSupported: false, hdr10plus: false, mdcvSei: false, cllSei: false, complete: true };
  try {
    if (![1, 2, 3, 4].includes(nalLengthSize)) throw Error('length size');
    for (let p = 0; p < bytes.length;) {
      if (p + nalLengthSize > bytes.length) throw Error('length');
      let length = 0; for (let i = 0; i < nalLengthSize; i++) length = length * 256 + bytes[p++];
      if (!length || p + length > bytes.length || (bytes[p] & 128)) throw Error('NAL');
      const nal = bytes.subarray(p, p + length); p += length;
      const type = nal[0] & 31;
      if (type === 7) addColor(result, sps(nal));
      if (type === 6) {
        const data = rbsp(nal); let q = 0;
        while (q < data.length) {
          if (q === data.length - 1 && data[q] === 128) { q++; break; }
          let payloadType = 0, size = 0;
          do { if (q >= data.length) throw Error('SEI'); payloadType += data[q]; } while (data[q++] === 255);
          do { if (q >= data.length) throw Error('SEI'); size += data[q]; } while (data[q++] === 255);
          if (q + size > data.length) throw Error('SEI');
          if (payloadType === 4 && size >= 6 && [181, 0, 60, 0, 1, 4].every((v, i) => data[q + i] === v)) result.hdr10plus = true;
          if (payloadType === 137 && size === 24) result.mdcvSei = true;
          if (payloadType === 144 && size === 4) result.cllSei = true;
          q += size;
          if (q === data.length) throw Error('SEI trailing');
        }
        if (!data.length) throw Error('SEI');
      }
    }
  } catch { result.malformedSamples = 1; result.complete = false; }
  return result;
}
