import { codec, track } from '../codecs.js';

let loading;
// Optional URLs let Node smoke tests load the identical vendored browser build.
export function loadMediaInfo({ moduleUrl = '/static/vendor/mediainfo/index.min.js', locateFile = () => '/static/vendor/mediainfo/MediaInfoModule.wasm' } = {}) {
  loading ??= import(moduleUrl).then(({ mediaInfoFactory }) => mediaInfoFactory({ format: 'object', chunkSize: 1024 * 1024, locateFile }));
  return loading;
}
export async function runMediaInfo(mi, reader, size) {
  return mi.analyzeData(size, (len, off) => reader.read(off, len, 'mediainfo'));
}
const numeric = value => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
const yes = value => value == null ? null : /^(yes|true|1)$/i.test(String(value));
const kindOf = t => ({ Video: 'video', Audio: 'audio', Text: 'subtitle' })[t['@type']] || null;
const formatCodec = value => ({ AVC: 'h264', HEVC: 'hevc', AV1: 'av1', 'E-AC-3': 'eac3', 'AC-3': 'ac3', 'AC-4': 'ac4', 'MLP FBA': 'truehd', AAC: 'aac', 'UTF-8': 'subrip' })[value] || value?.toLowerCase();
export function fromMediaInfo(result, structureTracks = []) {
  const tracks = structureTracks.map(t => ({ ...t, color: { ...t.color } })), evidence = [];
  const media = result.media?.track || [], general = media.find(t => t['@type'] === 'General') || {};
  const container = { format: general.Format || null, durationS: numeric(general.Duration), overallBitRateBps: numeric(general.OverallBitRate) };
  const source = `MediaInfoLib ${result.creatingLibrary?.version || 'unknown'}`;
  const used = new Set();
  for (const mi of media) {
    const kind = kindOf(mi); if (!kind) continue;
    const id = numeric(mi.ID?.toString().split(' ')[0]), order = numeric(mi.StreamOrder?.toString().split('-').at(-1));
    let t = tracks.find(t => !used.has(t) && t.kind === kind && id !== null && String(t.id) === String(id));
    if (!t && order !== null && tracks[order]?.kind === kind && !used.has(tracks[order])) t = tracks[order];
    t ||= tracks.find(t => t.kind === kind && !used.has(t));
    if (!t) { t = track(id ?? order ?? tracks.length, kind); tracks.push(t); }
    used.add(t);
    const values = { codec: mi.CodecID ? codec(mi.CodecID) : formatCodec(mi.Format), profile: mi.Format_Profile || null, width: numeric(mi.Width), height: numeric(mi.Height), fps: numeric(mi.FrameRate), channels: numeric(mi.Channels), channelLayout: mi.ChannelLayout || null, sampleRateHz: numeric(mi.SamplingRate), bitRateBps: numeric(mi.BitRate), language: mi.Language || null, title: mi.Title || null, default: yes(mi.Default), forced: yes(mi.Forced) };
    // Some CodecIDs are brands, not codec names (e.g. mp4a-40-2).
    if (mi.Format && (!values.codec || values.codec === mi.CodecID?.toLowerCase())) values.codec = formatCodec(mi.Format) || values.codec;
    for (const [key, value] of Object.entries(values)) if (t[key] == null && value != null) t[key] = value;
    const colors = {
      transfer: ({ PQ: 'smpte2084', HLG: 'arib-std-b67', 'BT.709': 'bt709', 'BT.2020 (10-bit)': 'bt2020-10', 'BT.2020 (12-bit)': 'bt2020-12' })[mi.transfer_characteristics],
      primaries: ({ 'BT.709': 'bt709', 'BT.2020': 'bt2020', 'Display P3': 'smpte432' })[mi.colour_primaries],
      matrix: ({ 'BT.709': 'bt709', 'BT.2020 non-constant': 'bt2020nc', 'BT.2020 constant': 'bt2020c' })[mi.matrix_coefficients],
    };
    for (const [key, value] of Object.entries(colors)) if (!t.color[key] && value) t.color[key] = value;
    const emit = (fieldId, kind, value, note) => evidence.push({ fieldId, kind, value, note, parser: 'mediainfo', source, coverage: 'MediaInfo frame parse' });
    if (kind === 'video' && t === tracks.find(t => t.kind === 'video' && !['jpeg', 'png '].includes(t.fourcc))) {
      const hdr = mi.HDR_Format || '';
      if (/Dolby Vision/i.test(hdr)) {
        const profile = /(?:dvhe|dvav)\.(\d+)/i.exec(mi.HDR_Format_Profile || '');
        const existing = evidence.find(e => e.fieldId === 'video.dolby_vision');
        if (!existing) emit('video.dolby_vision', 'observed', { present: true, profile_raw: profile ? Number(profile[1]) : null, profile: profile ? String(Number(profile[1])) : null }, 'MediaInfo detected Dolby Vision');
      }
      if (/SMPTE ST 2094 App 4|HDR10\+/i.test(hdr)) emit('video.hdr10_plus', 'observed', true, 'MediaInfo detected HDR10+');
      if (/SMPTE ST 2086/i.test(hdr) || mi.MasteringDisplay_ColorPrimaries) emit('video.hdr10.mdcv', 'observed', true, 'MediaInfo parsed mastering display metadata');
      if (mi.MaxCLL != null) emit('video.hdr10.cll', 'observed', true, 'MediaInfo parsed MaxCLL');
      if (colors.transfer) {
        emit('video.transfer', 'observed', colors.transfer, `Transfer is ${colors.transfer}`);
        if (colors.transfer !== 'smpte2084') for (const id of ['video.hdr10_plus', 'video.hdr10.mdcv', 'video.hdr10.cll']) emit(id, 'absent', false, `Transfer is ${colors.transfer}; these apply to PQ streams`);
      }
    }
    if (kind === 'audio') {
      const atmos = /JOC/i.test(mi.Format_AdditionalFeatures || '') || /Atmos/i.test(mi.Format_Commercial_IfAny || '');
      if (atmos) emit(`audio.${t.id}.atmos`, 'observed', true, 'MediaInfo detected JOC/Atmos');
      else if (mi.Format && ['eac3', 'truehd'].includes(t.codec)) emit(`audio.${t.id}.atmos`, 'not_found_in_scope', null, "MediaInfo's frame parse found no JOC/Atmos marker");
      else if (mi.Format && !t.encrypted && !t.encoded && ['aac', 'alac', 'opus', 'flac', 'mp3', 'pcm', 'dts', 'ac3'].includes(values.codec)) emit(`audio.${t.id}.atmos`, 'absent', false, `${values.codec} can't carry Dolby Atmos`);
    }
  }
  return { container, tracks, evidence };
}
