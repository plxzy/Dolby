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
export function mp4({ dv = false, fourcc = 'hvc1', samples = [], tailBytes = 0, pq = true, staticHdr = true, audio = [] } = {}) {
  const videoHeader = Buffer.alloc(78); videoHeader.writeUInt16BE(1920, 24); videoHeader.writeUInt16BE(1080, 26);
  const hvcc = Buffer.alloc(23); hvcc[0] = 1; hvcc[1] = 2; hvcc[21] = 3;
  const entry = box(fourcc, videoHeader, box('hvcC', hvcc), ...(dv ? [box('dvcC', dvcC())] : []), box('colr', Buffer.from('nclx'), u16(9), u16(pq ? 16 : 1), u16(9), [0]), ...(staticHdr ? [box('mdcv', Buffer.alloc(24)), box('clli', Buffer.alloc(4))] : []));
  const ftyp = box('ftyp', Buffer.from('isom'), u32(0));
  const audioTracks = audio.map((a, i) => {
    const header = Buffer.alloc(28); header.writeUInt16BE(6, 16); header.writeUInt32BE(48000 * 65536, 24);
    return trak(i + 2, 'audio', box(a.code, header, ...(a.code === 'ec-3' ? [box('dec3', dec3(a.joc))] : [])));
  });
  let moov = box('moov', trak(1, 'video', entry, samples.map(s => s.length), 0), ...audioTracks);
  const payloadOffset = ftyp.length + (tailBytes ? 0 : moov.length) + 8;
  moov = box('moov', trak(1, 'video', entry, samples.map(s => s.length), payloadOffset), ...audioTracks);
  if (tailBytes) {
    const chunk = new Uint8Array(1024 ** 2);
    return new File([ftyp, u32(tailBytes + 8), 'mdat', ...Array(tailBytes / chunk.length).fill(chunk), moov], 'tail.mp4');
  }
  return new File([ftyp, moov, box('mdat', ...samples)], 'fixture.mp4');
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
export function mkv({ dv = true, clusters = [sample({ size: 80 }), sample({ size: 80 }), sample({ hdr: true, size: 80 })], laced = false } = {}) {
  const privateData = Buffer.alloc(23); privateData[0] = 1; privateData[1] = 2; privateData[21] = 3;
  const video = el(0xae, uint(0xd7, 1), uint(0x83, 1), el(0x86, Buffer.from('V_MPEGH/ISO/HEVC')), el(0x63a2, privateData), uint(0x23e383, 41666667), el(0xe0, uint(0xb0, 1920), uint(0xba, 1080), el(0x55b0, uint(0x55ba, 16), el(0x55d0, el(0x55d1, float64(0.68))), uint(0x55bc, 1000))), ...(dv ? [el(0x41e4, uint(0x41e7, 0x64766343), el(0x41ed, dvcC()))] : []));
  const body = el(0x18538067, el(0x1549a966, uint(0x2ad7b1, 1000000), el(0x4489, float64(clusters.length * 1000))), el(0x1654ae6b, video), ...clusters.map((s, i) => el(0x1f43b675, uint(0xe7, i * 1000), el(0xa3, [0x81, 0, 0, laced ? 0x82 : 0x80], s))));
  return new File([el(0x1a45dfa3), body], 'fixture.mkv');
}
