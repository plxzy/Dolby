const positive = f => f?.state === 'signaled' || f?.state === 'observed';
const boolean = f => positive(f) ? true : f?.state === 'absent' ? false : null;
function prettySize(bytes) {
  if (!bytes) return null;
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) { if (bytes < 1024) return `${bytes.toFixed(2)} ${unit}`; bytes /= 1024; }
  return `${bytes.toFixed(2)} PB`;
}
function prettyDuration(seconds) {
  if (!seconds) return null;
  const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = String(s % 60).padStart(2, '0');
  return h ? `${h}h ${String(m).padStart(2, '0')}m ${sec}s` : `${m}m ${sec}s`;
}
function warning(fourcc, dv) {
  if (!dv.present || ['dvh1', 'dvhe', 'dvav', 'dva1'].includes(fourcc)) return null;
  const prefix = `video sample-entry FourCC is '${fourcc}' — TV.app will NOT engage the DV pipeline.`;
  return dv.profile_raw === 7 ? `${prefix} This is dual-layer Dolby Vision profile 7, which Apple players do not support at all: convert it to profile 8.1 first (e.g. \`dovi_tool -m 2 convert\`), then remux with \`-tag:v dvh1\`.` : `${prefix} Remux with \`ffmpeg -c copy -tag:v dvh1 -strict unofficial\`.`;
}
export function toSpec(report) {
  const findings = report.findings, t = report.tracks.find(t => t.kind === 'video' && !['jpeg', 'png '].includes(t.fourcc)) || {};
  const duration = report.container.durationS, rate = report.videoPresent === false ? null : t.bitRateBps ?? report.container.overallBitRateBps;
  const dvFinding = findings['video.dolby_vision'];
  const dv = positive(dvFinding) ? { ...(typeof dvFinding.value === 'object' ? dvFinding.value : {}), present: true } : { present: boolean(dvFinding) };
  if (dv.present) {
    dv.compatibility_name = ({ 0: 'None', 1: 'HDR10', 2: 'SDR', 4: 'HLG', 6: 'Blu-ray HDR10' })[dv.compatibility_id ?? 0] || `id=${dv.compatibility_id}`;
    if (dv.profile_raw != null) dv.profile = dv.profile_raw === 8 && dv.compatibility_id != null ? `8.${dv.compatibility_id}` : String(dv.profile_raw);
  }
  const mp4 = /mov|mp4|mpeg-4|quicktime/i.test(report.container.format || '');
  const fourcc = mp4 ? (t.fourcc || '').toLowerCase() : '';
  return {
    path: '', filename: report.file.name,
    container: { format_name: report.container.format, format_long_name: report.container.formatLongName, duration_s: duration, duration_pretty: prettyDuration(duration), size_bytes: report.file.size, size_pretty: prettySize(report.file.size), overall_bit_rate_bps: report.container.overallBitRateBps },
    video: { present: report.videoPresent ?? null, codec: t.codec ?? null, profile: t.profile ?? null, fourcc, fourcc_warning: mp4 ? warning(fourcc, dv) : null, width: t.width ?? null, height: t.height ?? null, resolution: t.width && t.height ? `${t.width}x${t.height}` : null, pix_fmt: null, fps: t.fps == null ? null : Math.round(t.fps * 1000) / 1000, bit_rate_bps: rate ?? null, bit_rate_mbps: rate == null ? null : Math.round(rate / 10000) / 100, color_transfer: t.color?.transfer ?? null, color_primaries: t.color?.primaries ?? null, color_space: t.color?.matrix ?? null, dolby_vision: dv, hdr10: { mdcv: boolean(findings['video.hdr10.mdcv']), cll: boolean(findings['video.hdr10.cll']) }, hdr10_plus: boolean(findings['video.hdr10_plus']) },
    audio: report.tracks.flatMap((t, index) => t.kind === 'audio' ? [{ index, codec: t.codec, codec_tag: t.fourcc || null, profile: t.profile, channels: t.channels, channel_layout: t.channelLayout, sample_rate_hz: t.sampleRateHz, bit_rate_bps: t.bitRateBps, bit_rate_kbps: t.bitRateBps == null ? null : Math.round(t.bitRateBps / 1000), language: t.language, title: t.title, atmos: boolean(findings[`audio.${t.id}.atmos`]), default: !!t.default }] : []),
    subtitles: report.tracks.flatMap((t, index) => t.kind === 'subtitle' ? [{ index, codec: t.codec, language: t.language, title: t.title, default: !!t.default, forced: !!t.forced, hearing_impaired: !!t.hearingImpaired }] : []),
    evidence: findings,
    session: { state: report.state, terminal: report.terminal, activity: report.activity, metrics: report.metrics, coverage: report.coverage },
  };
}
