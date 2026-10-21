import { codec, track, transfer, primaries, matrix, decodeDovi, configurationEvidence } from '../codecs.js';
import { VIDEO_FIELDS } from '../evidence.js';

// IDs checked against matroska.org/technical/elements.html (2026-10-21).
export const ID = Object.freeze({ Segment: 0x18538067, SeekHead: 0x114d9b74, Info: 0x1549a966, Tracks: 0x1654ae6b, Cues: 0x1c53bb6b, Cluster: 0x1f43b675 });
export function vint(bytes, offset = 0, keepMarker = false) {
  const first = bytes[offset]; if (!first) return null;
  let width = 1; while (width <= 8 && !(first & (0x80 >> (width - 1)))) width++;
  if (width > 8 || offset + width > bytes.length || (keepMarker && width > 4)) return null;
  let value = keepMarker ? first : first & (0xff >> width);
  let unknown = !keepMarker && value === (0xff >> width);
  for (let i = 1; i < width; i++) { value = value * 256 + bytes[offset + i]; unknown &&= bytes[offset + i] === 255; }
  if (!unknown && !Number.isSafeInteger(value)) return null;
  return { value, width, unknown };
}
export function* elements(bytes, start = 0, end = bytes.length) {
  for (let off = start; off < end;) {
    const id = vint(bytes, off, true); if (!id) return;
    const size = vint(bytes, off + id.width); if (!size) return;
    const data = off + id.width + size.width, stop = size.unknown ? end : data + size.value;
    if (stop > end || stop <= off) return;
    yield { id: id.value, start: off, data, end: stop, unknown: size.unknown };
    off = stop;
  }
}
const uint = b => { let n = 0; for (const v of b) n = n * 256 + v; return n; };
const string = b => new TextDecoder().decode(b).replace(/\0+$/, '');
const float = b => b.length === 4 ? new DataView(b.buffer, b.byteOffset, b.byteLength).getFloat32(0) : b.length === 8 ? new DataView(b.buffer, b.byteOffset, b.byteLength).getFloat64(0) : null;
const children = (b, node) => [...elements(b, node.data, node.end)];
const get = (b, nodes, id, decode = uint) => { const el = nodes.find(x => x.id === id); return el ? decode(b.subarray(el.data, el.end)) : null; };
export async function elementHeader(reader, offset, end = reader.blob.size) {
  if (offset >= end) return null;
  const b = await reader.read(offset, Math.min(16, end - offset), 'mkv element header');
  const id = vint(b, 0, true); if (!id) return null;
  const size = vint(b, id.width); if (!size) return null;
  const data = offset + id.width + size.width, stop = size.unknown ? end : data + size.value;
  if (stop > end || stop <= offset) return null;
  return { id: id.value, start: offset, data, end: stop, unknown: size.unknown };
}
function parseTrack(b, node, evidence) {
  const list = children(b, node), id = get(b, list, 0xd7), type = get(b, list, 0x83);
  const t = track(id, type === 1 ? 'video' : type === 2 ? 'audio' : type === 17 ? 'subtitle' : 'other');
  const rawCodec = get(b, list, 0x86, string) || '';
  t.codec = codec(rawCodec); t.codecId = rawCodec;
  t.language = get(b, list, 0x22b59d, string) || get(b, list, 0x22b59c, string) || 'eng';
  t.title = get(b, list, 0x536e, string);
  t.default = (get(b, list, 0x88) ?? 1) === 1;
  t.forced = get(b, list, 0x55aa) === 1; t.hearingImpaired = get(b, list, 0x55ab) === 1;
  const duration = get(b, list, 0x23e383); if (duration) t.fps = 1e9 / duration;
  const video = list.find(x => x.id === 0xe0), audio = list.find(x => x.id === 0xe1);
  const emit = (fieldId, kind, value, box, note) => evidence.push({ fieldId, kind, value, source: 'Matroska track configuration', parser: 'mkv', ranges: box ? [[box.start, box.end]] : [], note });
  if (video) {
    const v = children(b, video);
    t.width = get(b, v, 0xb0); t.height = get(b, v, 0xba);
    const colour = v.find(x => x.id === 0x55b0);
    if (colour) {
      const c = children(b, colour);
      t.color = { transfer: transfer(get(b, c, 0x55ba)), primaries: primaries(get(b, c, 0x55bb)), matrix: matrix(get(b, c, 0x55b1)) };
      const mdcv = c.find(x => x.id === 0x55d0), cll = c.find(x => x.id === 0x55bc || x.id === 0x55bd);
      if (mdcv) emit('video.hdr10.mdcv', 'signaled', true, mdcv, 'Matroska MasteringMetadata');
      if (cll) emit('video.hdr10.cll', 'signaled', true, cll, 'Matroska content light level');
    }
    let dv = false;
    for (const mapping of list.filter(x => x.id === 0x41e4)) {
      const m = children(b, mapping), kind = get(b, m, 0x41e7), data = m.find(x => x.id === 0x41ed);
      if ([0x64766343, 0x64767643].includes(kind) && data) {
        const config = decodeDovi(b.subarray(data.data, data.end));
        if (config) { dv = true; emit('video.dolby_vision', 'signaled', config, mapping, 'Dolby Vision block-addition mapping'); }
      }
    }
    if (!dv) emit('video.dolby_vision', 'not_found_in_scope', null, null, 'No Dolby Vision block-addition mapping');
  }
  if (audio) { const a = children(b, audio); t.channels = get(b, a, 0x9f) ?? 1; t.sampleRateHz = get(b, a, 0xb5, float) ?? 8000; }
  const privateData = list.find(x => x.id === 0x63a2);
  if (privateData && t.codec === 'hevc' && privateData.end - privateData.data >= 22) {
    t.nalLengthSize = (b[privateData.data + 21] & 3) + 1;
    t.profile = (b[privateData.data + 1] & 31) === 2 ? 'Main 10' : 'Main';
  }
  // Compression/encryption changes packet bytes; never scan encoded blocks as HEVC.
  if (list.some(x => x.id === 0x6d80)) {
    t.encoded = true;
    for (const field of t.kind === 'video' ? VIDEO_FIELDS : [`audio.${id}.atmos`]) emit(field, 'unsupported', null, null, 'Matroska content encoding is not supported');
  }
  return t;
}
export async function parseMkvStructure(reader, size) {
  const result = { kind: 'mkv', container: { format: 'matroska,webm', formatLongName: 'Matroska / WebM', durationS: null, overallBitRateBps: null }, tracks: [], primaryVideo: null, evidence: [], clusters: [], cues: [], timestampScale: 1e6, segmentStart: 0, segmentEnd: size };
  let segment;
  for (let off = 0; off < size;) {
    const h = await elementHeader(reader, off, size); if (!h) break;
    if (h.id === ID.Segment) { segment = h; break; } off = h.end;
  }
  if (!segment) { result.damaged = true; return result; }
  result.segmentStart = segment.data; result.segmentEnd = segment.end;
  const seen = new Set(), pending = [];
  async function metadata(h) {
    if (seen.has(h.start)) return; seen.add(h.start);
    if (h.end - h.data > reader.policy.maxMoovBytes) { result.damaged = true; return; }
    const b = await reader.read(h.data, h.end - h.data, 'mkv metadata');
    const list = [...elements(b)];
    if (h.id === ID.SeekHead) for (const seek of list.filter(x => x.id === 0x4dbb)) {
      const entries = children(b, seek), target = get(b, entries, 0x53ab), pos = get(b, entries, 0x53ac);
      if ([ID.Info, ID.Tracks, ID.Cues, ID.SeekHead].includes(target) && pos !== null) pending.push({ off: segment.data + pos, id: target });
    }
    if (h.id === ID.Info) {
      result.timestampScale = get(b, list, 0x2ad7b1) ?? 1e6;
      const duration = get(b, list, 0x4489, float); if (duration !== null) result.container.durationS = duration * result.timestampScale / 1e9;
    }
    if (h.id === ID.Tracks) for (const node of list.filter(x => x.id === 0xae)) {
      const entries = [], t = parseTrack(b, node, entries);
      if (t.kind !== 'video' || !result.tracks.some(t => t.kind === 'video')) for (const entry of entries) {
        if (entry.ranges) entry.ranges = entry.ranges.map(([a, z]) => [a + h.data, z + h.data]);
        result.evidence.push(entry);
      }
      result.tracks.push(t);
    }
    if (h.id === ID.Cues) for (const point of list.filter(x => x.id === 0xbb)) {
      const c = children(b, point), time = get(b, c, 0xb3);
      for (const position of c.filter(x => x.id === 0xb7)) {
        const p = children(b, position), off = get(b, p, 0xf1), trackId = get(b, p, 0xf7);
        if (off !== null) result.cues.push({ time, trackId, offset: segment.data + off });
      }
    }
  }
  for (let off = segment.data; off < segment.end;) {
    const h = await elementHeader(reader, off, segment.end);
    if (!h) { result.damaged = true; break; }
    if (h.id === ID.Cluster) {
      result.clusters.push(h);
      // Unknown-sized clusters cannot be skipped to locate later structure safely.
      if (h.unknown) { result.unknownClusters = true; break; }
    } else if ([ID.Info, ID.Tracks, ID.Cues, ID.SeekHead].includes(h.id)) await metadata(h);
    while (pending.length) {
      const target = pending.shift(); if (seen.has(target.off) || target.off < segment.data || target.off >= segment.end) continue;
      const sought = await elementHeader(reader, target.off, segment.end);
      if (sought?.id === target.id) await metadata(sought);
    }
    off = h.end;
  }
  result.primaryVideo = result.tracks.find(t => t.kind === 'video') || null;
  result.cues = result.cues.filter(c => c.trackId === result.primaryVideo?.id).sort((a, b) => a.time - b.time);
  if (result.container.durationS) result.container.overallBitRateBps = Math.round(size * 8 / result.container.durationS);
  configurationEvidence(result.tracks, result.evidence, 'mkv');
  if (result.damaged) for (const fieldId of VIDEO_FIELDS) result.evidence.push({ fieldId, kind: 'unsupported', value: null, source: 'Matroska structure', parser: 'mkv', note: 'Damaged structure: invalid or oversized Matroska element' });
  return result;
}

export async function resyncCluster(reader, offset, end) {
  const length = Math.min(8 * 1024 ** 2, end - offset);
  if (length <= 0) return null;
  const bytes = await reader.read(offset, length, 'mkv cluster resync');
  for (let i = 0; i + 6 <= bytes.length; i++) {
    if (bytes[i] !== 0x1f || bytes[i + 1] !== 0x43 || bytes[i + 2] !== 0xb6 || bytes[i + 3] !== 0x75) continue;
    const s = vint(bytes, i + 4); if (!s) continue;
    const data = offset + i + 4 + s.width, stop = s.unknown ? end : data + s.value;
    if (stop > end) continue;
    // Timestamp is first, or second after the optional CRC-32.
    let first = await elementHeader(reader, data, stop);
    if (first?.id === 0xbf) first = await elementHeader(reader, first.end, stop);
    if (first?.id === 0xe7) return { id: ID.Cluster, start: offset + i, data, end: stop, unknown: s.unknown };
  }
  return null;
}
export async function* clusterBlocks(reader, cluster, structure) {
  let time = 0;
  for (let off = cluster.data; off < cluster.end;) {
    const h = await elementHeader(reader, off, cluster.end);
    if (!h) { structure.scanIncomplete = true; return; }
    if (h.id === ID.Cluster || [ID.Cues, ID.Info, ID.Tracks, ID.SeekHead].includes(h.id)) { cluster.nextOffset = h.start; return; }
    if (h.id === 0xe7) time = uint(await reader.read(h.data, h.end - h.data, 'mkv cluster timestamp'));
    let block = h.id === 0xa3 ? h : null;
    if (h.id === 0xa0) {
      for (let p = h.data; p < h.end;) {
        const child = await elementHeader(reader, p, h.end); if (!child) { structure.scanIncomplete = true; break; }
        if (child.id === 0xa1) { block = child; break; } p = child.end;
      }
    }
    if (block) {
      const header = await reader.read(block.data, Math.min(12, block.end - block.data), 'mkv block header');
      const num = vint(header);
      if (!num || header.length < num.width + 3) structure.scanIncomplete = true;
      else if (num.value === structure.primaryVideo.id) {
        const flags = header[num.width + 2];
        if (flags & 6) structure.scanIncomplete = true;
        else {
          const relative = new DataView(header.buffer, header.byteOffset).getInt16(num.width);
          yield { offset: block.data + num.width + 3, size: block.end - block.data - num.width - 3, time: (time + relative) * structure.timestampScale / 1e9, key: block.start };
        }
      }
    }
    off = h.end;
  }
  cluster.nextOffset = cluster.end;
}
