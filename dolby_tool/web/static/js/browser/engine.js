import { POLICY } from './policy.js';
import { Gate, CancelledError } from './gate.js';
import { RangeReader, FileUnavailableError } from './reader.js';
import { EvidenceStore, VIDEO_FIELDS, positive } from './evidence.js';
import { sniff } from './parsers/sniff.js';
import { parseMp4Structure } from './parsers/mp4.js';
import { parseMkvStructure } from './parsers/mkv.js';
import { loadMediaInfo, runMediaInfo, fromMediaInfo } from './parsers/mediainfo.js';
import { scanVideo } from './scan.js';

export async function analyze(file, { policy = {}, onReport = () => {}, mediainfo = true, gate = new Gate({ policy }), sessionId = crypto.randomUUID() } = {}) {
  policy = { ...POLICY, ...policy };
  const reader = new RangeReader(file, { policy, gate }), store = new EvidenceStore();
  VIDEO_FIELDS.forEach(id => store.field(id));
  let structure = { kind: null, container: { format: null, formatLongName: null, durationS: null, overallBitRateBps: null }, tracks: [], primaryVideo: null, evidence: [] };
  let coverage = { video: { samplesInspected: 0, samplesTotal: null, windows: [], videoBytesInspected: 0, videoBytesTotal: null } };
  let activity = 'Reading container structure', terminal = null, lastReport = null, lastPublished = -Infinity, structureComplete = false;
  // Only the complete structural parser can prove no video; MediaInfo may return partial tracks.
  let videoPresent = null;
  const unresolved = () => [...store.fields.keys()].filter(id => !positive(store.conclude(id)) && store.conclude(id).state !== 'absent');
  const next = text => { for (const id of store.fields.keys()) store.setNext(id, unresolved().includes(id) ? text : null); };
  const add = entries => { for (const { fieldId, ...entry } of entries) store.add(fieldId, entry); };
  function publish(force = true) {
    if (!force && performance.now() - lastPublished < policy.reportIntervalMs) return;
    lastPublished = performance.now();
    const tracks = structure.tracks.map(({ id, kind, codec, fourcc, profile, width, height, fps, bitRateBps, channels, channelLayout, sampleRateHz, language, title, default: isDefault, forced, hearingImpaired, color }) => ({ id, kind, codec, fourcc, profile, width, height, fps, bitRateBps, channels, channelLayout, sampleRateHz, language, title, default: isDefault, forced, hearingImpaired, color: { ...color } }));
    lastReport = { sessionId, file: { name: file.name || 'Video', size: file.size, lastModified: file.lastModified ?? null }, state: terminal ? 'done' : gate.state, terminal, activity, structureComplete, videoPresent, metrics: { ...reader.metrics, activeMs: gate.activeMs, limitBytes: gate.limitBytes, limitMs: gate.limitMs }, container: { ...structure.container }, tracks, findings: store.snapshot(), coverage: { video: { ...coverage.video, windows: [...coverage.video.windows] } } };
    onReport(lastReport);
  }
  const originalOnState = gate.onState;
  gate.onState = state => { originalOnState?.(state); publish(); };
  // Catch immediately: a failing concurrent module load must not be an unhandled rejection.
  const miReady = mediainfo ? (typeof mediainfo === 'object' ? Promise.resolve(mediainfo) : loadMediaInfo()).then(mi => ({ mi }), error => ({ error })) : null;
  try {
    publish();
    const head = await reader.read(0, Math.min(64 * 1024, file.size, Math.max(16, Math.floor(policy.autoBytes / 4))), 'container sniff');
    const kind = sniff(head);
    if (kind) {
      activity = 'Reading track configuration';
      structure = await (kind === 'mp4' ? parseMp4Structure : parseMkvStructure)(reader, file.size);
      structureComplete = !structure.damaged && structure.tracks.length > 0;
      if (structure.primaryVideo) videoPresent = true;
      else if (structureComplete && !structure.unknownClusters) videoPresent = false;
      add(structure.evidence);
      for (const t of structure.tracks.filter(t => t.kind === 'audio')) store.field(`audio.${t.id}.atmos`);
      next(mediainfo ? 'Next: MediaInfo frame parse' : 'Next: sample more video packets');
      publish();
    }
    if (miReady) {
      activity = 'Running MediaInfo analysis'; publish();
      try {
        const { mi, error } = await miReady; if (error) throw error;
        const parsed = fromMediaInfo(await runMediaInfo(mi, reader, file.size), structure.tracks);
        for (const [key, value] of Object.entries(parsed.container)) if (structure.container[key] == null) structure.container[key] = value;
        structure.tracks = parsed.tracks;
        structureComplete ||= parsed.tracks.length > 0;
        const primary = parsed.tracks.find(t => t.kind === 'video');
        structure.primaryVideo = primary || structure.primaryVideo || null;
        if (primary) videoPresent = true;
        add(parsed.evidence);
        for (const t of structure.tracks.filter(t => t.kind === 'audio')) store.field(`audio.${t.id}.atmos`);
      } catch (error) {
        if (error instanceof CancelledError || error instanceof FileUnavailableError) throw error;
        for (const id of unresolved()) store.limit(id, `MediaInfo unavailable: ${error.message}`);
      }
      next('Next: sample more video packets'); publish();
    }
    if (videoPresent === false) {
      for (const id of VIDEO_FIELDS) store.add(id, { kind: 'absent', value: false, source: 'Container track table', parser: structure.kind, note: 'No video track in the complete container structure' });
    }
    if (!kind) {
      for (const id of VIDEO_FIELDS) if (!positive(store.conclude(id)) && store.conclude(id).state !== 'absent') store.add(id, { kind: 'unsupported', value: null, source: 'Browser packet scan', parser: 'scan', note: 'Packet scan supports MP4/MOV and MKV' });
    } else {
      coverage.video = await scanVideo({ reader, store, structure, policy, onProgress: progress => {
        activity = progress.activity; coverage.video = progress.coverage; publish(false);
      } });
    }
    const resolved = unresolved().length === 0;
    terminal = resolved ? { reason: 'resolved', message: 'Requested fields resolved' } : { reason: 'exhausted', message: 'Supported strategies exhausted' };
  } catch (error) {
    if (error instanceof CancelledError) return null;
    terminal = error instanceof FileUnavailableError ? { reason: 'file-unavailable', message: 'The file can no longer be read' } : { reason: 'failed', message: error.message };
  } finally { gate.onState = originalOnState; }
  store.terminal = true; next(null);
  activity = terminal.message;
  gate.transition('done');
  publish();
  return lastReport;
}
