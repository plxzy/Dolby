export const concat = (...parts) => Buffer.concat(parts.map(p => Buffer.from(p)));
export const u16 = n => { const b = Buffer.alloc(2); b.writeUInt16BE(n); return b; };
export const u32 = n => { const b = Buffer.alloc(4); b.writeUInt32BE(n); return b; };
export const u64 = n => { const b = Buffer.alloc(8); b.writeBigUInt64BE(BigInt(n)); return b; };
export const box = (type, ...parts) => { const payload = concat(...parts); return concat(u32(payload.length + 8), Buffer.from(type), payload); };
export const full = (type, ...parts) => box(type, u32(0), ...parts);
export const dvcC = (profile = 8, compat = 1) => Buffer.from([1, 0, profile << 1, 5, compat << 4]);
export function dec3(joc) { const b = Buffer.alloc(6); if (joc) b[5] = 1; return b; }
export function nal(type, payload = Buffer.alloc(0)) { return concat(u32(payload.length + 2), [type << 1, 1], payload); }
export function sei(type, payload) {
  const extension = n => [...Array(Math.floor(n / 255)).fill(255), n % 255];
  const raw = concat(extension(type), extension(payload.length), payload, [0x80]), escaped = [];
  let zeros = 0;
  for (const byte of raw) { if (zeros >= 2 && byte <= 3) { escaped.push(3); zeros = 0; } escaped.push(byte); zeros = byte === 0 ? zeros + 1 : 0; }
  return nal(39, escaped);
}
export const hdr10plus = () => sei(4, Buffer.from([0xb5, 0, 0x3c, 0, 1, 4, 1]));
export function sample({ hdr = false, rpu = false, size = 2048 } = {}) {
  const metadata = concat(hdr ? hdr10plus() : [], rpu ? nal(62) : []);
  return concat(metadata, nal(1, Buffer.alloc(Math.max(0, size - metadata.length - 6), 0x55)));
}
function trak(id, kind, entry, sizes = [], chunk = 0) {
  const tkhd = full('tkhd', u32(0), u32(0), u32(id), Buffer.alloc(64));
  const mdhd = full('mdhd', u32(0), u32(0), u32(24000), u32(sizes.length * 1000 || 24000), u16((5 << 10) | (14 << 5) | 7), u16(0));
  const hdlr = full('hdlr', u32(0), Buffer.from(kind === 'video' ? 'vide' : 'soun'), Buffer.alloc(12));
  const stbl = box('stbl', full('stsd', u32(1), entry), full('stts', u32(1), u32(sizes.length), u32(1000)), full('stsz', u32(0), u32(sizes.length), ...sizes.map(u32)), full('stsc', u32(1), u32(1), u32(sizes.length), u32(1)), full('stco', u32(sizes.length ? 1 : 0), ...(sizes.length ? [u32(chunk)] : [])));
  return box('trak', tkhd, box('mdia', mdhd, hdlr, box('minf', stbl)));
}
export function mp4({ dv = false, fourcc = 'hvc1', samples = [], tailBytes = 0, pq = true, staticHdr = true, audio = [], avcc = null, colr = true, video = true } = {}) {
  const videoHeader = Buffer.alloc(78); videoHeader.writeUInt16BE(1920, 24); videoHeader.writeUInt16BE(1080, 26);
  const hvcc = Buffer.alloc(23); hvcc[0] = 1; hvcc[1] = 2; hvcc[21] = 3;
  const entry = box(fourcc, videoHeader, avcc ? box('avcC', avcc) : box('hvcC', hvcc), ...(dv ? [box('dvcC', dvcC())] : []), ...(colr ? [box('colr', Buffer.from('nclx'), u16(9), u16(pq ? 16 : 1), u16(9), [0])] : []), ...(staticHdr ? [box('mdcv', Buffer.alloc(24)), box('clli', Buffer.alloc(4))] : []));
  const ftyp = box('ftyp', Buffer.from('isom'), u32(0));
  function makeMoov(offset) {
    let cursor = offset + samples.reduce((n, s) => n + s.length, 0);
    const audioTracks = audio.map((a, i) => {
      const header = Buffer.alloc(28); header.writeUInt16BE(6, 16); header.writeUInt32BE(48000 * 65536, 24);
      const packets = a.samples || [], start = cursor;
      cursor += packets.reduce((n, s) => n + s.length, 0);
      return trak(i + 2, 'audio', box(a.code, header, ...(a.code === 'ec-3' ? [box('dec3', dec3(a.joc))] : []), ...(a.dsi ? [box('dac4', a.dsi)] : [])), packets.map(s => s.length), start);
    });
    return box('moov', ...(video ? [trak(1, 'video', entry, samples.map(s => s.length), offset)] : []), ...audioTracks);
  }
  let moov = makeMoov(0);
  const payloadOffset = ftyp.length + (tailBytes ? 0 : moov.length) + 8;
  moov = makeMoov(payloadOffset);
  if (tailBytes) {
    const chunk = new Uint8Array(1024 ** 2);
    return new File([ftyp, u32(tailBytes + 8), 'mdat', ...Array(tailBytes / chunk.length).fill(chunk), moov], 'tail.mp4');
  }
  return new File([ftyp, moov, box('mdat', ...samples, ...audio.flatMap(a => a.samples || []))], 'fixture.mp4');
}
export function ebmlSize(n) {
  let width = 1; while (n >= 2 ** (7 * width) - 1) width++;
  const b = Buffer.alloc(width); let value = BigInt(n) | (1n << BigInt(width * 7));
  for (let i = width - 1; i >= 0; i--) { b[i] = Number(value & 255n); value >>= 8n; } return b;
}
const idBytes = n => Buffer.from(n.toString(16).padStart(Math.ceil(n.toString(16).length / 2) * 2, '0'), 'hex');
export const el = (id, ...parts) => { const payload = concat(...parts); return concat(idBytes(id), ebmlSize(payload.length), payload); };
export const uint = (id, n) => { const bytes = []; do { bytes.unshift(n % 256); n = Math.floor(n / 256); } while (n); return el(id, bytes); };
const float64 = n => { const b = Buffer.alloc(8); b.writeDoubleBE(n); return b; };
export function mkv({ dv = true, clusters = [sample({ size: 80 }), sample({ size: 80 }), sample({ hdr: true, size: 80 })], laced = false, videoCodec = 'V_MPEGH/ISO/HEVC', codecPrivate = null, colorTransfer = 16, staticHdr = true, audio = [], video = true } = {}) {
  const privateData = codecPrivate || Buffer.alloc(23);
  if (!codecPrivate) { privateData[0] = 1; privateData[1] = 2; privateData[21] = 3; }
  const videoTrack = el(0xae, uint(0xd7, 1), uint(0x83, 1), el(0x86, Buffer.from(videoCodec)), el(0x63a2, privateData), uint(0x23e383, 41666667), el(0xe0, uint(0xb0, 1920), uint(0xba, 1080), el(0x55b0, ...(colorTransfer == null ? [] : [uint(0x55ba, colorTransfer)]), ...(staticHdr ? [el(0x55d0, el(0x55d1, float64(0.68))), uint(0x55bc, 1000)] : []))), ...(dv ? [el(0x41e4, uint(0x41e7, 0x64766343), el(0x41ed, dvcC()))] : []));
  const audioTracks = audio.map((a, i) => el(0xae, uint(0xd7, i + 2), uint(0x83, 2), el(0x86, Buffer.from(a.code || 'A_EAC3')), ...(a.dsi ? [el(0x63a2, a.dsi)] : []), ...(a.encoded ? [el(0x6d80)] : []), el(0xe1, uint(0x9f, 6), el(0xb5, float64(48000)))));
  const count = Math.max(clusters.length, ...audio.map(a => a.samples.length));
  const body = el(0x18538067, el(0x1549a966, uint(0x2ad7b1, 1000000), el(0x4489, float64(count * 1000))), el(0x1654ae6b, ...(video ? [videoTrack] : []), ...audioTracks), ...Array.from({ length: count }, (_, i) => el(0x1f43b675, uint(0xe7, i * 1000), ...(clusters[i] && video ? [el(0xa3, [0x81, 0, 0, laced ? 0x82 : 0x80], clusters[i])] : []), ...audio.flatMap((a, j) => a.samples[i] ? [el(0xa3, [0x82 + j, 0, 0, 0x80], a.samples[i])] : []))));
  return new File([el(0x1a45dfa3), body], 'fixture.mkv');
}

// Compact generated baseline-profile SPS VUI, without encoder strings or file metadata.
export function avcConfig(transferCode = 1) {
  let bits = '';
  const put = (v, n) => { bits += v.toString(2).padStart(n, '0'); };
  const ue = v => { const code = (v + 1).toString(2); bits += '0'.repeat(code.length - 1) + code; };
  put(66, 8); put(0, 8); put(40, 8); ue(0); ue(0); ue(0); ue(0); ue(1);
  put(0, 1); ue(3); ue(3); put(1, 1); put(1, 1); put(0, 1); put(1, 1);
  put(0, 1); put(0, 1); put(1, 1); put(5, 3); put(0, 1); put(1, 1);
  put(1, 8); put(transferCode, 8); put(1, 8); put(0, 6); put(1, 1);
  while (bits.length % 8) put(0, 1);
  const sps = [0x67]; let zeros = 0;
  for (const byte of bits.match(/.{8}/g).map(v => parseInt(v, 2))) {
    if (zeros >= 2 && byte <= 3) { sps.push(3); zeros = 0; }
    sps.push(byte); zeros = byte === 0 ? zeros + 1 : 0;
  }
  return concat([1, 66, 0, 40, 255, 225], u16(sps.length), sps, [1, 0, 2, 0x68, 0x80]);
}
// BSI syntax only, not playable JOC audio: all optional fields off, stereo, six blocks.
export function eac3Frame(joc = false) {
  let bits = '';
  const put = (v, n) => { bits += v.toString(2).padStart(n, '0'); };
  put(0x0b77, 16); put(0, 2); put(0, 3); put(63, 11); put(0, 2); put(3, 2);
  put(2, 3); put(0, 1); put(16, 5); put(0, 5); put(0, 1); put(0, 1); put(0, 1);
  put(joc ? 1 : 0, 1);
  if (joc) { put(1, 6); put(1, 8); put(16, 8); }
  while (bits.length % 8) put(0, 1);
  return concat(bits.match(/.{8}/g).map(v => parseInt(v, 2)), Buffer.alloc(128 - bits.length / 8));
}
