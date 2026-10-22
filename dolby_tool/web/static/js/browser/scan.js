import { POLICY } from './policy.js';
import { positive } from './evidence.js';
import { transfer, primaries, matrix } from './codecs.js';
import { inspectSample } from './parsers/hevc.js';
import { inspectAvcSample } from './parsers/avc.js';
import { inspectEac3Sample } from './parsers/eac3.js';
import { ID, elementHeader, clusterBlocks, resyncCluster } from './parsers/mkv.js';

const TARGETS = { 'video.hdr10_plus': 'hdr10plus', 'video.hdr10.mdcv': 'mdcvSei', 'video.hdr10.cll': 'cllSei', 'video.dolby_vision': 'dvRpu' };
export function windowFractions(count) {
  const result = count ? [0] : [];
  for (let denominator = 2; result.length < count; denominator *= 2) for (let n = 1; n < denominator && result.length < count; n += 2) result.push(n / denominator);
  return result;
}
export async function scanVideo({ reader, store, structure, policy = {}, onProgress = () => {} }) {
  policy = { ...POLICY, ...policy };
  const track = structure.primaryVideo;
  const coverage = { samplesInspected: 0, samplesTotal: structure.samples?.sizes.length ?? null, windows: [], videoBytesInspected: 0, videoBytesTotal: structure.samples?.sizes.reduce((n, x) => n + x, 0) ?? null, malformedSamples: 0 };
  const unresolved = () => Object.keys(TARGETS).filter(id => !(track?.codec === 'h264' && id === 'video.dolby_vision') && !positive(store.conclude(id)) && (id === 'video.dolby_vision' || store.conclude(id).state !== 'absent'));
  const emit = (id, kind, note, extra = {}) => store.add(id, { kind, value: kind === 'observed' ? true : kind === 'absent' ? false : null, source: `${track?.codec === 'h264' ? 'AVC' : 'HEVC'} packet scan`, parser: track?.codec === 'h264' ? 'avc' : 'hevc', note, ...extra });
  const audio = structure.tracks.filter(t => t.kind === 'audio' && t.codec === 'eac3' && !t.encrypted && !t.encoded && !positive(store.conclude(`audio.${t.id}.atmos`)) && store.conclude(`audio.${t.id}.atmos`).state !== 'absent');
  const audioStats = new Map(audio.map(t => [t.id, { samples: 0, frames: 0, bytes: 0, valid: !structure.damaged, visited: new Set() }]));
  const audioOpen = t => !positive(store.conclude(`audio.${t.id}.atmos`));
  let videoValid = !structure.damaged && !track?.packetConfigIncomplete && (structure.samples?.complete ?? true), dvScanSupported = track?.codec === 'hevc';
  const videoSupported = track && ['hevc', 'h264'].includes(track.codec) && !track.encrypted && !track.encoded && !structure.damaged && (structure.kind !== 'mp4' || structure.samples);
  if (!videoSupported) {
    videoValid = false;
    for (const id of unresolved()) if (store.conclude(id).state !== 'absent') emit(id, 'unsupported', structure.damaged ? 'Damaged structure prevents a complete packet scan' : track?.encrypted ? `Encrypted track (${track.fourcc})` : track?.encoded ? 'Matroska content encoding is not supported' : 'In-stream scan supports AVC and HEVC with usable sample boundaries');
  }
  if (track?.codec === 'h264' && !positive(store.conclude('video.dolby_vision')) && store.conclude('video.dolby_vision').state !== 'absent') {
    emit('video.dolby_vision', 'not_found_in_scope', 'Track Dolby Vision mappings checked; AVC profile 9 RPU syntax is not supported, so no whole-stream negative conclusion');
    store.limit('video.dolby_vision', 'AVC SDR transfer characteristics do not exclude Dolby Vision profile 9');
  }
  const needsVideo = () => videoSupported && unresolved().length > 0;
  const needsScan = () => needsVideo() || audio.some(audioOpen);
  const visited = new Set();
  const progress = activity => onProgress({ activity, coverage: { ...coverage, windows: [...coverage.windows] } });
  const markScope = () => {
    if (videoSupported) for (const id of unresolved()) if (store.conclude(id).state !== 'absent') emit(id, 'not_found_in_scope', `No marker in ${coverage.samplesInspected} inspected ${track.codec === 'h264' ? 'AVC' : 'HEVC'} samples; sampled windows alone cannot establish absence`, { coverage: `${coverage.samplesInspected} samples; ${coverage.windows.length} windows` });
    for (const t of audio.filter(audioOpen)) {
      const stats = audioStats.get(t.id);
      store.add(`audio.${t.id}.atmos`, { kind: 'not_found_in_scope', value: null, source: 'E-AC-3 BSI scan', parser: 'eac3', note: `No JOC extension type A signal in ${stats.frames} parsed E-AC-3 frames (${stats.samples} packets); whole-track absence requires every frame`, coverage: `${stats.bytes} audio bytes inspected` });
    }
  };
  async function inspect(sample, bytes, t = track) {
    if (t?.kind === 'audio') {
      const stats = audioStats.get(t.id); if (!stats || stats.visited.has(sample.key) || !audioOpen(t)) return;
      stats.visited.add(sample.key);
      const found = inspectEac3Sample(bytes);
      stats.samples++; stats.frames += found.frames; stats.bytes += bytes.length; stats.valid &&= found.complete;
      if (!found.complete) store.limit(`audio.${t.id}.atmos`, `Incomplete E-AC-3 BSI: ${found.reason}`);
      if (found.joc) store.add(`audio.${t.id}.atmos`, { kind: 'signaled', value: true, source: 'E-AC-3 BSI scan', parser: 'eac3', note: 'E-AC-3 addbsi extension type A signals JOC (object payload not decoded)', ranges: [[sample.offset, sample.offset + sample.size]], coverage: `${stats.frames} frames parsed` });
      return;
    }
    if (!needsVideo() || visited.has(sample.key)) return;
    visited.add(sample.key);
    const found = track.codec === 'h264' ? inspectAvcSample(bytes, track.nalLengthSize || 4) : inspectSample(bytes, track.nalLengthSize || 4);
    if (track.codec === 'h264') dvScanSupported = found.dvScanSupported === true;
    coverage.samplesInspected++; coverage.videoBytesInspected += bytes.length;
    coverage.malformedSamples += found.malformedSamples;
    if (found.malformedSamples) videoValid = false;
    for (const color of found.colors || []) {
      const value = transfer(color.transfer);
      if (value) {
        track.color = { transfer: value, primaries: primaries(color.primaries), matrix: matrix(color.matrix) };
        store.add('video.transfer', { kind: 'observed', value, source: 'AVC SPS VUI', parser: 'avc', note: `AVC SPS explicitly specifies transfer_characteristics=${color.transfer} (${value})`, ranges: [[sample.offset, sample.offset + sample.size]] });
      }
    }
    for (const id of unresolved()) if (found[TARGETS[id]]) emit(id, 'observed', id === 'video.dolby_vision' ? 'Dolby Vision RPU NAL observed' : 'Metadata observed in video sample', { ranges: [[sample.offset, sample.offset + sample.size]], coverage: `sample ${sample.index ?? coverage.samplesInspected} at ${sample.time.toFixed(3)}s` });
  }
  async function readSample(sample, t = track) {
    const seen = t?.kind === 'audio' ? audioStats.get(t.id)?.visited : visited;
    if (seen?.has(sample.key)) return;
    if (sample.offset < 0 || sample.size <= 0 || sample.size > policy.maxReadBytes || sample.offset + sample.size > reader.blob.size) {
      if (t?.kind === 'audio') audioStats.get(t.id).valid = false; else videoValid = false;
      return;
    }
    await inspect(sample, sample.bytes || await reader.read(sample.offset, sample.size, `${t?.kind || 'video'} sample`), t);
  }
  async function* blocksFrom(first, max = Infinity) {
    let cluster = first, count = 0;
    const ids = [...(videoSupported ? [track.id] : []), ...audio.map(t => t.id)];
    while (cluster) {
      for await (const block of clusterBlocks(reader, cluster, structure, ids, Math.min(policy.sweepBatchBytes, policy.maxReadBytes))) { yield block; if (++count >= max) return; }
      let next = cluster.nextOffset ?? cluster.end; cluster = null;
      while (next < structure.segmentEnd) {
        const h = await elementHeader(reader, next, structure.segmentEnd); if (!h) { structure.scanIncomplete = true; return; }
        if (h.id === ID.Cluster) { cluster = h; break; } next = h.end;
      }
    }
  }
  const packetTrack = block => block.trackId === track?.id ? track : audio.find(t => t.id === block.trackId);
  const tableFor = t => t === track ? structure.samples : structure.audioSamples?.get(t.id);
  const tableSample = (t, table, i) => ({ key: i, index: i, offset: table.offsets[i], size: table.sizes[i], time: i / (t.fps || t.sampleRateHz / 1536 || 1) });
  for (const [i, fraction] of windowFractions(policy.windowCount).entries()) {
    if (!needsScan()) break;
    const pct = fraction * 100;
    progress(`Checking codec samples at ${pct}% (${i + 1} of ${policy.windowCount} windows)`);
    if (structure.kind === 'mp4') {
      for (const t of [...(needsVideo() ? [track] : []), ...audio.filter(audioOpen)]) {
        const table = tableFor(t); if (!table) continue;
        const count = table.sizes.length;
        let first = Math.min(count - 1, Math.floor(count * fraction));
        if (table.sync.length) first = table.sync.find(n => n >= first) ?? count;
        for (let j = Math.max(0, first); j < Math.min(count, first + policy.windowSamples); j++) await readSample(tableSample(t, table, j), t);
      }
    } else {
      let cluster;
      if (structure.cues.length) {
        const target = fraction * (structure.container.durationS || 0) * 1e9 / structure.timestampScale;
        const cue = structure.cues.reduce((best, c) => Math.abs(c.time - target) < Math.abs(best.time - target) ? c : best);
        cluster = await elementHeader(reader, cue.offset, structure.segmentEnd);
      } else cluster = await resyncCluster(reader, fraction === 0 ? structure.segmentStart : Math.floor(fraction * reader.blob.size), structure.segmentEnd);
      if (cluster?.id === ID.Cluster) for await (const block of blocksFrom(cluster, policy.windowSamples)) { await readSample(block, packetTrack(block)); if (!needsScan()) break; }
    }
    coverage.windows.push(pct); markScope();
  }
  if (!needsScan()) return coverage;
  if (structure.kind === 'mp4') {
    for (const t of [...(needsVideo() ? [track] : []), ...audio.filter(audioOpen)]) {
      const table = tableFor(t);
      if (!table) { if (t === track) videoValid = false; else audioStats.get(t.id).valid = false; continue; }
      if (t.kind === 'audio') audioStats.get(t.id).valid &&= table.complete;
      const seen = t === track ? visited : audioStats.get(t.id).visited;
      for (let i = 0; i < table.sizes.length && (t === track ? needsVideo() : audioOpen(t));) {
        if (seen.has(i)) { i++; continue; }
        const first = i, start = table.offsets[i]; let end = start;
        while (i < table.sizes.length && !seen.has(i) && table.offsets[i] === end && end - start + table.sizes[i] <= policy.sweepBatchBytes) end += table.sizes[i++];
        if (i === first) await readSample(tableSample(t, table, i++), t);
        else if (start >= 0 && end <= reader.blob.size) {
          const bytes = await reader.read(start, end - start, `${t.kind} sweep`);
          for (let j = first; j < i && (t === track ? needsVideo() : audioOpen(t)); j++) { const sample = tableSample(t, table, j); await inspect(sample, bytes.subarray(sample.offset - start, sample.offset - start + sample.size), t); }
        } else if (t === track) videoValid = false; else audioStats.get(t.id).valid = false;
        progress(`Sweeping remaining ${t.kind} samples (${(i / Math.max(1, table.sizes.length) * 100).toFixed(1)}%)`);
      }
    }
  } else {
    const first = structure.clusters[0] || await resyncCluster(reader, structure.segmentStart, structure.segmentEnd);
    let total = 0, totalBytes = 0;
    if (first) for await (const block of blocksFrom(first)) {
      if (block.trackId === track?.id) { total++; totalBytes += block.size; }
      await readSample(block, packetTrack(block));
      progress(`Sweeping remaining codec samples (${Math.min(100, block.offset / reader.blob.size * 100).toFixed(1)}% of file covered)`);
      if (!needsScan()) break;
    } else structure.scanIncomplete = true;
    if (needsVideo()) { coverage.samplesTotal = total; coverage.videoBytesTotal = totalBytes; }
  }
  markScope();
  videoValid &&= !structure.scanIncomplete && !structure.incompleteTracks?.has(track?.id);
  if (videoValid && (coverage.samplesInspected > 0 || structure.kind === 'mp4') && coverage.samplesInspected === coverage.samplesTotal) {
    for (const id of unresolved()) if (id !== 'video.dolby_vision' || (dvScanSupported && !track.dvConfigIncomplete)) emit(id, 'absent', 'Not present in any video sample (complete scan)', { coverage: `100% of ${coverage.samplesInspected} video samples; codec private and track mappings checked` });
  } else if (needsVideo()) for (const id of unresolved()) if (store.conclude(id).state !== 'absent') store.limit(id, coverage.malformedSamples ? 'Malformed video samples prevent an exhaustive negative conclusion' : 'Incomplete sample coverage prevents an exhaustive negative conclusion');
  if (track?.codec === 'h264' && !positive(store.conclude('video.transfer'))) {
    store.add('video.transfer', { kind: 'not_found_in_scope', value: null, source: 'AVC SPS VUI', parser: 'avc', note: 'No explicit supported transfer characteristic in parsed AVC SPS; unspecified is not proof of SDR' });
  }
  for (const t of audio.filter(audioOpen)) {
    const stats = audioStats.get(t.id), table = tableFor(t);
    const complete = stats.valid && stats.frames > 0 && !structure.scanIncomplete && !structure.incompleteTracks?.has(t.id) && (structure.kind !== 'mp4' || (table?.complete && stats.samples === table.sizes.length));
    if (complete) store.add(`audio.${t.id}.atmos`, { kind: 'absent', value: false, source: 'E-AC-3 BSI scan', parser: 'eac3', note: `No JOC extension type A signal in any of ${stats.frames} E-AC-3 frames (complete track BSI check; object payload not decoded)`, coverage: `100% of ${stats.samples} audio packets; ${stats.bytes} bytes` });
    else store.limit(`audio.${t.id}.atmos`, 'Incomplete frame/track coverage prevents an exhaustive negative conclusion');
  }
  return coverage;
}
