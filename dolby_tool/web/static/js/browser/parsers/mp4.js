import { codec, track, transfer, primaries, matrix, decodeDovi, configurationEvidence } from '../codecs.js';
import { VIDEO_FIELDS } from '../evidence.js';
import { parseAvcConfig } from './avc.js';
import { inspectHevcConfig } from './hevc.js';

const text = (b, p, n = 4) => String.fromCharCode(...b.subarray(p, p + n));
const view = b => new DataView(b.buffer, b.byteOffset, b.byteLength);
const u32 = (b, p) => view(b).getUint32(p);
const u64 = (b, p) => Number(view(b).getBigUint64(p));
function* boxes(bytes, start = 0, end = bytes.length) {
  for (let p = start; p + 8 <= end;) {
    let size = u32(bytes, p), header = 8;
    if (size === 1) { if (p + 16 > end) return; size = u64(bytes, p + 8); header = 16; }
    if (!size) size = end - p;
    if (!Number.isSafeInteger(size) || size < header || p + size > end) return;
    yield { type: text(bytes, p + 4), start: p, data: p + header, end: p + size, size };
    p += size;
  }
}
const child = (b, parent, type) => [...boxes(b, parent.data, parent.end)].find(x => x.type === type);
const path = (b, parent, names) => names.reduce((p, name) => p && child(b, p, name), parent);
function readBits(b, offset, count) {
  if (offset < 0 || offset + count > b.length * 8) return null;
  let value = 0;
  for (let i = 0; i < count; i++) value = (value << 1) | ((b[(offset + i) >> 3] >> (7 - ((offset + i) & 7))) & 1);
  return value;
}
// Ported from the portfolio prototype's dec3 independent/dependent-substream walk.
export function hasAtmosMarker(payload) {
  let bitOffset = 13;
  const independentStreams = readBits(payload, bitOffset, 3);
  if (independentStreams == null) return false;
  bitOffset += 3;
  for (let i = 0; i <= independentStreams; i++) {
    bitOffset += 2 + 5 + 1 + 1 + 3 + 3 + 1 + 3;
    const dependentStreams = readBits(payload, bitOffset, 4);
    if (dependentStreams == null) return false;
    bitOffset += 4 + (dependentStreams > 0 ? 9 : 1);
  }
  bitOffset += 7;
  return readBits(payload, bitOffset, 1) === 1;
}
// Channels of the first (independent) substream, per ETSI TS 102 366 Annex F. The
// sample entry's channelcount is fixed at 2 for AC-3/E-AC-3, so it is not the layout.
const ACMOD_CHANNELS = [2, 1, 2, 3, 3, 4, 4, 5];
// dec3 chan_loc, MSB first: Lc/Rc, Lrs/Rrs, Cs, Ts, Lsd/Rsd, Lw/Rw, Lvh/Rvh, Cvh, LFE2.
const CHAN_LOC_CHANNELS = [2, 2, 1, 1, 2, 2, 2, 1, 1];
export function dolbyChannels(type, payload) {
  if (type === 'dac3') {
    const acmod = readBits(payload, 10, 3), lfe = readBits(payload, 13, 1);
    return acmod == null || lfe == null ? null : ACMOD_CHANNELS[acmod] + lfe;
  }
  const base = 13 + 3;
  const acmod = readBits(payload, base + 12, 3), lfe = readBits(payload, base + 15, 1);
  const dependents = readBits(payload, base + 19, 4);
  if (acmod == null || lfe == null || dependents == null) return null;
  let channels = ACMOD_CHANNELS[acmod] + lfe;
  if (dependents > 0) {
    const loc = readBits(payload, base + 23, 9);
    if (loc == null) return null;
    CHAN_LOC_CHANNELS.forEach((n, i) => { if (loc & (1 << (8 - i))) channels += n; });
  }
  return channels;
}
function parseTrack(b, trak, evidence) {
  const tkhd = child(b, trak, 'tkhd'), mdhd = path(b, trak, ['mdia', 'mdhd']), hdlr = path(b, trak, ['mdia', 'hdlr']);
  if (!tkhd || !mdhd || !hdlr) return null;
  const id = u32(b, tkhd.data + (b[tkhd.data] === 1 ? 20 : 12));
  const handler = text(b, hdlr.data + 8);
  const t = track(id, handler === 'vide' ? 'video' : handler === 'soun' ? 'audio' : ['subt', 'text', 'sbtl', 'clcp'].includes(handler) ? 'subtitle' : 'other');
  t.default = !!(u32(b, tkhd.data) & 1); t.forced = false; t.hearingImpaired = false;
  const version1 = b[mdhd.data] === 1, p = mdhd.data + (version1 ? 20 : 12);
  t.timescale = u32(b, p);
  t.durationS = (version1 ? u64(b, p + 4) : u32(b, p + 4)) / t.timescale;
  const lang = view(b).getUint16(p + (version1 ? 12 : 8));
  t.language = [10, 5, 0].map(shift => String.fromCharCode(((lang >> shift) & 31) + 96)).join('');
  if (t.language === '```') t.language = null;
  const stbl = path(b, trak, ['mdia', 'minf', 'stbl']);
  if (!stbl) return t;
  const stsd = child(b, stbl, 'stsd');
  const entries = stsd ? [...boxes(b, stsd.data + 8, stsd.end)] : [];
  let dvRecord = false;
  const emit = (fieldId, kind, value, box, note) => evidence.push({ fieldId, kind, value, source: 'MP4 sample description', parser: 'mp4', ranges: box ? [[box.start, box.end]] : [], note });
  for (const [entryIndex, entry] of entries.entries()) {
    let fourcc = entry.type;
    const encrypted = fourcc === 'encv' || fourcc === 'enca';
    let start = entry.data + (t.kind === 'video' ? 78 : t.kind === 'audio' ? 28 : 8);
    if (t.kind === 'audio') {
      const version = view(b).getUint16(entry.data + 8);
      if (version === 1) start += 16;
      if (version === 2) start += 36;
    }
    const configs = [...boxes(b, start, entry.end)];
    const sinf = configs.find(x => x.type === 'sinf');
    const frma = sinf && child(b, sinf, 'frma');
    if (frma) fourcc = text(b, frma.data);
    if (entryIndex === 0) {
      t.fourcc = entry.type; t.codec = codec(fourcc); t.encrypted = encrypted;
      if (t.kind === 'video') { t.width = view(b).getUint16(entry.data + 24); t.height = view(b).getUint16(entry.data + 26); }
      if (t.kind === 'audio') { t.channels = view(b).getUint16(entry.data + 16); t.sampleRateHz = u32(b, entry.data + 24) / 65536; }
    }
    if (encrypted) continue;
    for (const c of configs) {
      const payload = b.subarray(c.data, c.end);
      if (c.type === 'hvcC' && payload.length >= 22) {
        t.nalLengthSize = (payload[21] & 3) + 1; t.profile = (payload[1] & 31) === 2 ? 'Main 10' : 'Main';
        const config = inspectHevcConfig(payload);
        t.dvConfigIncomplete ||= !config.complete;
        t.packetConfigIncomplete ||= !config.complete;
        if (config.dvRpu) emit('video.dolby_vision', 'observed', true, c, 'Dolby Vision RPU NAL observed in hvcC');
        for (const [field, key] of [['video.hdr10_plus', 'hdr10plus'], ['video.hdr10.mdcv', 'mdcvSei'], ['video.hdr10.cll', 'cllSei']]) if (config[key]) emit(field, 'observed', true, c, 'Metadata observed in hvcC');
      }
      if (c.type === 'avcC' && t.codec === 'h264') {
        const config = parseAvcConfig(payload);
        t.nalLengthSize = config.nalLengthSize;
        t.packetConfigIncomplete ||= !config.complete;
        if (config.complete && config.colors.length === 1 && transfer(config.colors[0].transfer)) {
          const color = config.colors[0];
          t.color = { transfer: transfer(color.transfer), primaries: primaries(color.primaries), matrix: matrix(color.matrix) };
          emit('video.transfer', 'observed', t.color.transfer, c, `avcC SPS VUI explicitly specifies transfer_characteristics=${color.transfer} (${t.color.transfer})`);
        } else emit('video.transfer', 'not_found_in_scope', null, c, config.complete ? 'avcC has unspecified, missing, unsupported or differing SPS transfer characteristics; SDR is not assumed' : 'Incomplete avcC SPS VUI check');
      }
      if (['dvcC', 'dvvC', 'dvwC'].includes(c.type) && t.kind === 'video') {
        dvRecord = true;
        const config = decodeDovi(payload);
        emit('video.dolby_vision', config ? 'signaled' : 'unsupported', config, c, config ? 'Dolby Vision configuration record' : 'Damaged Dolby Vision configuration record');
      }
      if (c.type === 'colr' && text(payload, 0) === 'nclx' && payload.length >= 10) {
        t.color = { primaries: primaries(view(payload).getUint16(4)), transfer: transfer(view(payload).getUint16(6)), matrix: matrix(view(payload).getUint16(8)) };
      }
      if (['mdcv', 'SmDm'].includes(c.type)) emit('video.hdr10.mdcv', 'signaled', true, c, 'Mastering display metadata in sample description');
      if (['clli', 'CoLL'].includes(c.type)) emit('video.hdr10.cll', 'signaled', true, c, 'Content light level metadata in sample description');
      if (c.type === 'dac3' || c.type === 'dec3') {
        const channels = dolbyChannels(c.type, payload);
        if (channels && entryIndex === 0) t.channels = channels;
      }
      if (c.type === 'dec3') {
        const joc = hasAtmosMarker(payload);
        emit(`audio.${id}.atmos`, joc ? 'signaled' : 'not_found_in_scope', joc || null, c, joc ? 'E-AC-3 configuration flags JOC' : "E-AC-3 configuration doesn't flag JOC");
      }
      if (c.type === 'dac3') emit(`audio.${id}.atmos`, 'absent', false, c, "ac3 can't carry Dolby Atmos");
    }
  }
  if (t.kind === 'video' && !dvRecord && !t.encrypted) emit('video.dolby_vision', entries.length ? 'absent' : 'unsupported', entries.length ? false : null, null, entries.length ? "No Dolby Vision configuration record in the track's sample description" : 'No usable video sample description');
  const stts = child(b, stbl, 'stts');
  if (stts && u32(b, stts.data + 4)) t.fps = t.timescale / u32(b, stts.data + 12);
  t.tables = { b, stbl };
  return t;
}
function sampleTable(t) {
  if (!t?.tables) return { offsets: new Float64Array(), sizes: new Uint32Array(), sync: [], complete: true };
  const { b, stbl } = t.tables;
  const stsz = child(b, stbl, 'stsz'), stz2 = child(b, stbl, 'stz2');
  const stco = child(b, stbl, 'stco') || child(b, stbl, 'co64'), stsc = child(b, stbl, 'stsc'), stss = child(b, stbl, 'stss');
  const count = stsz ? u32(b, stsz.data + 8) : stz2 ? u32(b, stz2.data + 8) : 0;
  // A corrupt count must not allocate memory unrelated to the bounded table payload.
  if (count > b.length * 2) return { offsets: new Float64Array(), sizes: new Uint32Array(), sync: [], complete: false };
  const sizes = new Uint32Array(count), offsets = new Float64Array(count), sync = [];
  if (stsz) { const fixed = u32(b, stsz.data + 4); for (let i = 0; i < count; i++) sizes[i] = fixed || u32(b, stsz.data + 12 + i * 4); }
  if (stz2) {
    const bits = b[stz2.data + 7];
    if (![4, 8, 16].includes(bits)) return { offsets, sizes, sync, complete: false };
    for (let i = 0; i < count; i++) sizes[i] = readBits(b, (stz2.data + 12) * 8 + i * bits, bits);
  }
  if (stss) for (let i = 0; i < u32(b, stss.data + 4); i++) sync.push(u32(b, stss.data + 8 + i * 4) - 1);
  let sample = 0;
  if (stco && stsc) {
    const entries = u32(b, stsc.data + 4), chunks = u32(b, stco.data + 4);
    let entry = 0;
    for (let chunk = 1; chunk <= chunks; chunk++) {
      while (entry + 1 < entries && u32(b, stsc.data + 8 + (entry + 1) * 12) <= chunk) entry++;
      const perChunk = entries ? u32(b, stsc.data + 12 + entry * 12) : 0;
      let off = stco.type === 'co64' ? u64(b, stco.data + 8 + (chunk - 1) * 8) : u32(b, stco.data + 8 + (chunk - 1) * 4);
      for (let i = 0; i < perChunk && sample < count; i++, sample++) { offsets[sample] = off; off += sizes[sample]; }
    }
  }
  return { offsets, sizes, sync, complete: sample === count };
}
function fragmentSamples(b, moofOffset, trackId, defaults, output) {
  const root = [...boxes(b)][0];
  let implicitBase = moofOffset;
  for (const traf of boxes(b, root.data, root.end)) {
    if (traf.type !== 'traf') continue;
    const tfhd = child(b, traf, 'tfhd'); if (!tfhd) continue;
    const flags = u32(b, tfhd.data) & 0xffffff, id = u32(b, tfhd.data + 4);
    let p = tfhd.data + 8, base = flags & 0x020000 ? moofOffset : implicitBase;
    if (flags & 1) { base = u64(b, p); p += 8; }
    if (flags & 2) p += 4;
    if (flags & 8) p += 4;
    let defaultSize = defaults.get(id)?.size || 0;
    if (flags & 16) { defaultSize = u32(b, p); p += 4; }
    if (flags & 32) p += 4;
    let cursor = base;
    for (const run of boxes(b, traf.data, traf.end)) {
      if (run.type !== 'trun') continue;
      const f = u32(b, run.data) & 0xffffff, count = u32(b, run.data + 4);
      p = run.data + 8;
      if (f & 1) { cursor = base + view(b).getInt32(p); p += 4; }
      if (f & 4) p += 4;
      if (count > b.length * 2) { output.complete = false; return; }
      for (let i = 0; i < count; i++) {
        if (f & 0x100) p += 4;
        let size = defaultSize;
        if (f & 0x200) { size = u32(b, p); p += 4; }
        if (f & 0x400) p += 4;
        if (f & 0x800) p += 4;
        if (id === trackId) { output.offsets.push(cursor); output.sizes.push(size); if (!size) output.complete = false; }
        cursor += size;
      }
    }
    implicitBase = cursor;
  }
}
export async function parseMp4Structure(reader, size) {
  const result = { container: { format: 'mov,mp4,m4a,3gp,3g2,mj2', formatLongName: 'QuickTime / MOV', durationS: null, overallBitRateBps: null }, tracks: [], primaryVideo: null, evidence: [], kind: 'mp4' };
  const moofs = [], defaults = new Map();
  const unsupported = note => { for (const fieldId of [...VIDEO_FIELDS, ...result.tracks.filter(t => t.kind === 'audio').map(t => `audio.${t.id}.atmos`)]) result.evidence.push({ fieldId, kind: 'unsupported', value: null, source: 'MP4 structure', parser: 'mp4', note }); result.damaged = true; };
  let damage = null;
  for (let off = 0; off + 8 <= size;) {
    const h = await reader.read(off, 16, 'mp4 headers');
    let length = u32(h, 0), header = 8; const type = text(h, 4);
    if (length === 1) { if (h.length < 16) { damage = `Damaged structure: box '${type}' at ${off} exceeds the file`; break; } length = u64(h, 8); header = 16; }
    if (length === 0) length = size - off;
    if (!Number.isSafeInteger(length) || length < header || off + length > size) { damage = `Damaged structure: box '${type}' at ${off} exceeds the file`; break; }
    if (type === 'moov') {
      if (length > reader.policy.maxMoovBytes) unsupported(`moov box is ${Math.round(length / 1024 ** 2)} MiB, above the browser limit`);
      else {
        const b = await reader.read(off, length, 'mp4 moov'), root = [...boxes(b)][0];
        try {
          for (const box of boxes(b, root.data, root.end)) if (box.type === 'trak') {
            const entries = [], t = parseTrack(b, box, entries);
            if (t) {
              const primary = t.kind === 'video' && !['jpeg', 'png '].includes(t.fourcc) && !result.tracks.some(t => t.kind === 'video' && !['jpeg', 'png '].includes(t.fourcc));
              if (t.kind !== 'video' || primary) for (const entry of entries) {
                if (entry.ranges) entry.ranges = entry.ranges.map(([a, z]) => [a + off, z + off]);
                result.evidence.push(entry);
              }
              result.tracks.push(t);
            }
          }
          const mvex = child(b, root, 'mvex');
          if (mvex) for (const trex of boxes(b, mvex.data, mvex.end)) if (trex.type === 'trex') defaults.set(u32(b, trex.data + 4), { size: u32(b, trex.data + 16) });
        } catch (error) { if (!(error instanceof RangeError)) throw error; unsupported('Damaged structure: truncated MP4 track table'); }
      }
    }
    if (type === 'moof') moofs.push({ off, length });
    off += length;
  }
  if (damage) unsupported(damage);
  result.primaryVideo = result.tracks.find(t => t.kind === 'video' && !['jpeg', 'png '].includes(t.fourcc)) || null;
  result.audioSamples = new Map();
  for (const t of [result.primaryVideo, ...result.tracks.filter(t => t.kind === 'audio' && t.codec === 'eac3')].filter(Boolean)) {
    try {
      const table = sampleTable(t);
      if (moofs.length) {
        const output = { offsets: Array.from(table.offsets), sizes: Array.from(table.sizes), complete: table.complete };
        for (const { off, length } of moofs) {
          if (length > reader.policy.maxMoovBytes) { output.complete = false; continue; }
          fragmentSamples(await reader.read(off, length, 'mp4 fragment'), off, t.id, defaults, output);
        }
        table.offsets = Float64Array.from(output.offsets); table.sizes = Uint32Array.from(output.sizes); table.complete = output.complete;
      }
      if (t === result.primaryVideo) result.samples = table;
      else result.audioSamples.set(t.id, table);
    } catch (error) { if (!(error instanceof RangeError)) throw error; unsupported('Damaged structure: truncated MP4 sample table'); }
  }
  for (const t of result.tracks) delete t.tables;
  result.container.durationS = result.primaryVideo?.durationS || result.tracks.find(t => t.durationS)?.durationS || null;
  if (result.container.durationS) result.container.overallBitRateBps = Math.round(size * 8 / result.container.durationS);
  configurationEvidence(result.tracks, result.evidence, 'mp4');
  return result;
}
