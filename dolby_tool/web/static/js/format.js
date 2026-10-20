/** Pure formatters that turn ffprobe / log vocabulary into readable labels. */

export const DASH = '—';

const numberFormats = new Map();

/** Format a number with at most `decimals` fraction digits; nullish becomes a dash. */
export function num(value, decimals = 2) {
  if (value == null || Number.isNaN(value)) return DASH;
  if (!numberFormats.has(decimals)) {
    numberFormats.set(decimals, new Intl.NumberFormat('en', { maximumFractionDigits: decimals }));
  }
  return numberFormats.get(decimals).format(value);
}

/** "1 track" / "3 tracks". */
export function plural(count, singular, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

/** snake_case or kebab-case identifier -> "Sentence case". */
export function humanize(identifier) {
  const text = String(identifier ?? '')
    .replace(/[_-]+/g, ' ')
    .trim();
  return text ? text[0].toUpperCase() + text.slice(1) : DASH;
}

export function yesNo(value) {
  if (value == null) return 'not captured';
  return value ? 'yes' : 'no';
}

export function bytes(size) {
  if (!size) return DASH;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  return `${num(size / 1024 ** exponent)} ${units[exponent]}`;
}

/** Bits per second -> "18.4 Mbps" / "384 kbps". */
export function bitrate(bps) {
  if (!bps) return DASH;
  return bps >= 1_000_000 ? `${num(bps / 1_000_000)} Mbps` : `${Math.round(bps / 1000)} kbps`;
}

export function hertz(hz) {
  if (!hz) return DASH;
  return hz >= 1000 ? `${num(hz / 1000, 1)} kHz` : `${hz} Hz`;
}

export function fps(rate) {
  return rate ? `${num(rate, 3)} fps` : DASH;
}

/** Seconds -> "1:42:07" / "2:05" / "0:02". */
export function duration(seconds) {
  if (seconds == null) return DASH;
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

/** Wall-clock "HH:MM:SS" for an epoch timestamp in seconds. */
export function clock(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleTimeString('en-GB', { hour12: false });
}

export function resolution(width, height) {
  return width && height ? `${width} × ${height}` : DASH;
}

/** Marketing class for a frame height: "4K UHD", "Full HD", … */
export function resolutionClass(height) {
  if (!height) return null;
  if (height >= 4000) return '8K';
  if (height >= 2000) return '4K UHD';
  if (height >= 1400) return 'QHD';
  if (height >= 1000) return 'Full HD';
  if (height >= 700) return 'HD';
  return 'SD';
}

const CODECS = {
  hevc: 'HEVC',
  h264: 'H.264',
  av1: 'AV1',
  vp9: 'VP9',
  mpeg2video: 'MPEG-2',
  eac3: 'E-AC-3',
  ac3: 'AC-3',
  ac4: 'AC-4',
  truehd: 'TrueHD',
  mlp: 'MLP',
  aac: 'AAC',
  alac: 'ALAC',
  flac: 'FLAC',
  opus: 'Opus',
  mp3: 'MP3',
  dts: 'DTS',
  subrip: 'SubRip',
  mov_text: 'Timed Text',
  hdmv_pgs_subtitle: 'PGS',
  dvd_subtitle: 'VobSub',
  webvtt: 'WebVTT',
  ass: 'ASS',
};

export function codecName(codec) {
  if (!codec) return DASH;
  if (codec.startsWith('pcm_')) return 'PCM';
  return CODECS[codec] ?? codec;
}

export function channelsLabel(channels) {
  if (!channels) return DASH;
  return { 1: 'Mono', 2: 'Stereo', 6: '5.1', 8: '7.1' }[channels] ?? `${channels} ch`;
}

/** ffprobe layout "5.1(side)" -> "5.1 (side)", "stereo" -> "Stereo". */
export function channelLayout(layout) {
  if (!layout) return DASH;
  const spaced = layout.replace('(', ' (');
  return spaced[0].toUpperCase() + spaced.slice(1);
}

const PRIMARIES = {
  bt2020: 'BT.2020',
  bt709: 'BT.709',
  smpte432: 'Display P3',
  smpte431: 'DCI-P3',
  bt470bg: 'BT.601 (PAL)',
  smpte170m: 'BT.601 (NTSC)',
};
const TRANSFERS = {
  smpte2084: 'PQ · SMPTE ST 2084',
  'arib-std-b67': 'HLG · ARIB STD-B67',
  bt709: 'BT.709 (SDR)',
  'bt2020-10': 'BT.2020 10-bit (SDR)',
  'bt2020-12': 'BT.2020 12-bit (SDR)',
  smpte170m: 'BT.601 (SDR)',
};
const MATRICES = {
  bt2020nc: 'BT.2020 non-constant',
  bt2020c: 'BT.2020 constant',
  bt709: 'BT.709',
  smpte170m: 'BT.601',
  bt470bg: 'BT.601',
};

export const primariesName = (value) => (value ? (PRIMARIES[value] ?? value) : DASH);
export const transferName = (value) => (value ? (TRANSFERS[value] ?? value) : DASH);
export const matrixName = (value) => (value ? (MATRICES[value] ?? value) : DASH);

/** "yuv420p10le" -> "10-bit 4:2:0". */
export function pixelFormat(pixFmt) {
  const match = /^(?:yuv|yuvj)(4\d\d)p(\d+)?/.exec(pixFmt ?? '');
  if (!match) return pixFmt || DASH;
  const [, sampling, depth = '8'] = match;
  return `${depth}-bit ${sampling.split('').join(':')}`;
}

const languages = new Intl.DisplayNames(['en'], { type: 'language', fallback: 'none' });

/** ISO 639 code -> English name ("eng" -> "English"); unknown codes pass through. */
export function languageName(code) {
  if (!code || code === 'und') return DASH;
  try {
    return languages.of(code) ?? code;
  } catch {
    return code;
  }
}
