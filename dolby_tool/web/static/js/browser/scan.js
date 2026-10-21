import { POLICY } from './policy.js';
import { positive } from './evidence.js';
import { inspectSample } from './parsers/hevc.js';
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
  const unresolved = () => Object.keys(TARGETS).filter(id => !positive(store.conclude(id)) && (id === 'video.dolby_vision' || store.conclude(id).state !== 'absent'));
  const emit = (id, kind, note, extra = {}) => store.add(id, { kind, value: kind === 'observed' ? true : kind === 'absent' ? false : null, source: 'HEVC packet scan', parser: 'hevc', note, ...extra });
  if (!unresolved().length) return coverage;
  if (!track || track.codec !== 'hevc' || track.encrypted || track.encoded || structure.damaged) {
    for (const id of unresolved()) emit(id, 'unsupported', structure.damaged ? 'Damaged structure prevents a complete packet scan' : track?.encrypted ? `Encrypted track (${track.fourcc})` : track?.encoded ? 'Matroska content encoding is not supported' : 'In-stream scan supports HEVC only');
    return coverage;
  }
  if (structure.kind === 'mp4' && !structure.samples) {
    for (const id of unresolved()) emit(id, 'unsupported', 'No usable video sample table');
    return coverage;
  }
  const visited = new Set();
  let valid = !structure.damaged && (structure.samples?.complete ?? true);
  const progress = activity => onProgress({ activity, coverage: { ...coverage, windows: [...coverage.windows] } });
  const markScope = phase => {
    for (const id of unresolved()) if (store.conclude(id).state !== 'absent') emit(id, 'not_found_in_scope', 'No marker in inspected video samples', { coverage: phase === 'windows' ? `${coverage.samplesInspected} samples in ${coverage.windows.length} windows` : `${coverage.samplesTotal ? (coverage.samplesInspected / coverage.samplesTotal * 100).toFixed(1) : 'unknown'}% of video samples` });
  };
  async function inspect(sample, bytes) {
    if (visited.has(sample.key)) return;
    visited.add(sample.key);
    const found = inspectSample(bytes, track.nalLengthSize || 4);
    coverage.samplesInspected++; coverage.videoBytesInspected += bytes.length;
    coverage.malformedSamples += found.malformedSamples;
    if (found.malformedSamples) valid = false;
    for (const id of unresolved()) if (found[TARGETS[id]]) emit(id, 'observed', id === 'video.dolby_vision' ? 'Dolby Vision RPU NAL observed' : 'Metadata observed in HEVC sample', { ranges: [[sample.offset, sample.offset + sample.size]], coverage: `sample ${sample.index ?? coverage.samplesInspected} at ${sample.time.toFixed(3)}s` });
  }
  const mp4Sample = i => ({ key: i, index: i, offset: structure.samples.offsets[i], size: structure.samples.sizes[i], time: i / (track.fps || 1) });
  async function readSample(sample) {
    if (visited.has(sample.key)) return;
    if (sample.offset < 0 || sample.size > policy.maxReadBytes || sample.offset + sample.size > reader.blob.size) { valid = false; return; }
    await inspect(sample, await reader.read(sample.offset, sample.size, 'video sample'));
  }
  async function* blocksFrom(first, max = Infinity) {
    let cluster = first, count = 0;
    while (cluster) {
      for await (const block of clusterBlocks(reader, cluster, structure)) { yield block; if (++count >= max) return; }
      let next = cluster.nextOffset ?? cluster.end; cluster = null;
      while (next < structure.segmentEnd) {
        const h = await elementHeader(reader, next, structure.segmentEnd); if (!h) { valid = false; return; }
        if (h.id === ID.Cluster) { cluster = h; break; } next = h.end;
      }
    }
  }
  for (const [i, fraction] of windowFractions(policy.windowCount).entries()) {
    if (!unresolved().length) break;
    const pct = fraction * 100;
    progress(`Checking video samples at ${pct}% (${i + 1} of ${policy.windowCount} windows)`);
    if (structure.kind === 'mp4') {
      const count = structure.samples.sizes.length;
      let first = Math.min(count - 1, Math.floor(count * fraction));
      if (structure.samples.sync.length) first = structure.samples.sync.find(n => n >= first) ?? count;
      for (let j = Math.max(0, first); j < Math.min(count, first + policy.windowSamples) && unresolved().length; j++) await readSample(mp4Sample(j));
    } else {
      let cluster;
      if (structure.cues.length) {
        const target = fraction * (structure.container.durationS || 0) * 1e9 / structure.timestampScale;
        const cue = structure.cues.reduce((best, c) => Math.abs(c.time - target) < Math.abs(best.time - target) ? c : best);
        cluster = await elementHeader(reader, cue.offset, structure.segmentEnd);
      } else {
        const offset = fraction === 0 ? structure.segmentStart : Math.floor(fraction * reader.blob.size);
        cluster = await resyncCluster(reader, offset, structure.segmentEnd);
      }
      if (cluster?.id === ID.Cluster) for await (const block of blocksFrom(cluster, policy.windowSamples)) { await readSample(block); if (!unresolved().length) break; }
    }
    coverage.windows.push(pct); markScope('windows');
    progress(`Checking video samples at ${pct}% (${i + 1} of ${policy.windowCount} windows)`);
  }
  if (!unresolved().length) return coverage;
  if (structure.kind === 'mp4') {
    const count = structure.samples.sizes.length;
    for (let i = 0; i < count && unresolved().length;) {
      if (visited.has(i)) { i++; continue; }
      const first = i, start = structure.samples.offsets[i];
      let end = start;
      while (i < count && !visited.has(i) && structure.samples.offsets[i] === end && end - start + structure.samples.sizes[i] <= policy.sweepBatchBytes) { end += structure.samples.sizes[i++]; }
      if (i === first) { await readSample(mp4Sample(i++)); }
      else if (start >= 0 && end <= reader.blob.size) {
        const bytes = await reader.read(start, end - start, 'video sweep');
        for (let j = first; j < i && unresolved().length; j++) { const sample = mp4Sample(j); await inspect(sample, bytes.subarray(sample.offset - start, sample.offset - start + sample.size)); }
      } else { valid = false; }
      progress(`Sweeping remaining video samples (${(coverage.samplesInspected / Math.max(1, count) * 100).toFixed(1)}% of video data covered)`);
    }
  } else {
    const first = structure.clusters[0] || await resyncCluster(reader, structure.segmentStart, structure.segmentEnd);
    let total = 0, totalBytes = 0;
    if (first) for await (const block of blocksFrom(first)) {
      total++; totalBytes += block.size;
      await readSample(block);
      progress(`Sweeping remaining video samples (${Math.min(100, block.offset / reader.blob.size * 100).toFixed(1)}% of video data covered)`);
      if (!unresolved().length) break;
    }
    else valid = false;
    if (unresolved().length) { coverage.samplesTotal = total; coverage.videoBytesTotal = totalBytes; }
    valid &&= !structure.scanIncomplete;
  }
  markScope('sweep');
  if (valid && coverage.samplesInspected === coverage.samplesTotal) {
    for (const id of unresolved()) if (id !== 'video.dolby_vision') emit(id, 'absent', 'Not present in any video sample (complete scan)', { coverage: '100% of video samples' });
  } else if (unresolved().length) for (const id of unresolved()) store.limit(id, coverage.malformedSamples ? 'Malformed HEVC samples prevent an exhaustive negative conclusion' : 'Incomplete sample coverage prevents an exhaustive negative conclusion');
  return coverage;
}
