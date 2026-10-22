const CODES = {
  hvc1: 'hevc', hev1: 'hevc', dvh1: 'hevc', dvhe: 'hevc', 'V_MPEGH/ISO/HEVC': 'hevc',
  avc1: 'h264', avc3: 'h264', dvav: 'h264', dva1: 'h264', 'V_MPEG4/ISO/AVC': 'h264',
  av01: 'av1', V_AV1: 'av1', 'ec-3': 'eac3', A_EAC3: 'eac3', 'ac-3': 'ac3', A_AC3: 'ac3',
  'ac-4': 'ac4', A_AC4: 'ac4',
  mp4a: 'aac', A_TRUEHD: 'truehd', fLaC: 'flac', A_FLAC: 'flac', Opus: 'opus', A_OPUS: 'opus',
  alac: 'alac', A_ALAC: 'alac', '.mp3': 'mp3', 'A_MPEG/L3': 'mp3', tx3g: 'mov_text', mp4s: 'mov_text',
  'S_TEXT/UTF8': 'subrip', 'S_HDMV/PGS': 'hdmv_pgs_subtitle',
};
export function codec(code = '') {
  if (code.startsWith('A_AAC')) return 'aac';
  if (code.startsWith('A_DTS') || code.startsWith('dts')) return 'dts';
  if (code.startsWith('A_PCM') || ['lpcm', 'sowt', 'twos', 'in24', 'in32', 'fl32', 'fl64'].includes(code)) return 'pcm';
  return CODES[code] || code.toLowerCase();
}
export const transfer = code => ({ 1: 'bt709', 14: 'bt2020-10', 15: 'bt2020-12', 16: 'smpte2084', 18: 'arib-std-b67' })[code] || null;
export const primaries = code => ({ 1: 'bt709', 9: 'bt2020', 12: 'smpte432' })[code] || null;
export const matrix = code => ({ 1: 'bt709', 9: 'bt2020nc', 10: 'bt2020c' })[code] || null;
export function track(id, kind) {
  return { id, kind, codec: null, fourcc: '', profile: null, width: null, height: null, fps: null, bitRateBps: null, channels: null, channelLayout: null, sampleRateHz: null, language: null, title: null, default: null, forced: null, hearingImpaired: null, color: { transfer: null, primaries: null, matrix: null } };
}
export function decodeDovi(bytes) {
  if (bytes.length < 5) return null;
  const p = bytes[2] >> 1, compat = bytes[4] >> 4;
  return { present: true, profile_raw: p, compatibility_id: compat, profile: p === 8 ? `8.${compat}` : String(p), level: ((bytes[2] & 1) << 5) | (bytes[3] >> 3), rpu_present: !!(bytes[3] & 4), bl_present: !!(bytes[3] & 1), el_present: !!(bytes[3] & 2) };
}
export function configurationEvidence(tracks, evidence, parser) {
  const emit = (fieldId, kind, value, note) => evidence.push({ fieldId, kind, value, note, source: 'Container configuration', parser });
  const video = tracks.find(t => t.kind === 'video' && !['jpeg', 'png '].includes(t.fourcc));
  if (video?.color.transfer) {
    emit('video.transfer', 'signaled', video.color.transfer, `Transfer is ${video.color.transfer}`);
    if (video.color.transfer !== 'smpte2084') for (const field of ['video.hdr10_plus', 'video.hdr10.mdcv', 'video.hdr10.cll']) emit(field, 'absent', false, `Transfer is ${video.color.transfer}; these apply to PQ streams`);
  }
  for (const t of tracks) {
    if (t.kind === 'audio' && !t.encrypted && !t.encoded && ['aac', 'alac', 'opus', 'flac', 'mp3', 'pcm', 'dts', 'ac3'].includes(t.codec)) emit(`audio.${t.id}.atmos`, 'absent', false, `${t.codec} can't carry Dolby Atmos`);
    if (t.encrypted && (t.kind !== 'video' || t === video)) for (const field of t.kind === 'video' ? ['video.dolby_vision', 'video.hdr10.mdcv', 'video.hdr10.cll', 'video.hdr10_plus', 'video.transfer'] : [`audio.${t.id}.atmos`]) emit(field, 'unsupported', null, `Encrypted track (${t.fourcc})`);
  }
}
