/** HDR card verdict for an inspection report's `video`, independent of the DOM. */

/**
 * `transferName` formats a transfer characteristic for display. In browser mode a
 * missing transfer stays unknown: finding no HDR10/HDR10+ metadata says nothing about
 * the transfer curve, so it is not evidence of SDR. Native mode keeps its SDR fallback.
 */
export function hdrVerdict(video, { mode, transferName }) {
  if (mode === 'browser' && video.present === false) {
    return { value: 'No video', detail: 'Audio-only file', tone: 'neutral' };
  }
  const { mdcv, cll } = video.hdr10 ?? {};
  const statics = [mdcv && 'MDCV', cll && 'CLL'].filter(Boolean).join(' + ');
  if (video.hdr10_plus) {
    return { value: 'HDR10+', detail: `Dynamic metadata${statics ? ` · ${statics}` : ''}`, tone: 'success' };
  }
  if (mdcv || cll) return { value: 'HDR10', detail: `Static metadata · ${statics}`, tone: 'success' };
  if (video.color_transfer === 'smpte2084') return { value: 'PQ', detail: 'No static HDR10 metadata', tone: 'warn' };
  if (video.color_transfer === 'arib-std-b67') return { value: 'HLG', detail: 'Hybrid log-gamma', tone: 'success' };
  if (video.color_transfer) return { value: 'SDR', detail: transferName(video.color_transfer), tone: 'neutral' };
  if (mode === 'browser') {
    return { value: 'Unknown', detail: 'Transfer not signalled · no HDR10/HDR10+ metadata found', tone: 'neutral' };
  }
  return { value: 'SDR', detail: 'No HDR transfer signalled', tone: 'neutral' };
}
