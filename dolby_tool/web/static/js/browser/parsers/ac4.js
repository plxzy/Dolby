// ETSI TS 103 190-2 V1.3.1 Annex E.6/E.10/E.11: configuration signalling,
// not an AC-4 decoder. Each presentation is bounded by its own pres_bytes.
const GROUPS = ['L R', 'C', 'Ls Rs', 'Lb Rb', 'Tfl Tfr', 'Tbl Tbr', 'LFE', 'Tsl Tsr', 'Tsl Tsr', 'Tfc', 'Tbc', 'Tc', 'LFE2', 'Bfl Bfr', 'Bfc', 'Cb', 'Lscr Rscr', 'Lw Rw'].map(s => s.split(' '));
const HEIGHT_MASK = 0x0fb0; // groups 4, 5, 7 (8: deprecated alias), 9, 10, 11
class Bits {
  constructor(bytes) { this.bytes = bytes; this.offset = 0; }
  read(n) {
    if (this.offset + n > this.bytes.length * 8) throw new RangeError('truncated DSI');
    let v = 0;
    for (let i = 0; i < n; i++, this.offset++) v = v * 2 + ((this.bytes[this.offset >> 3] >> (7 - (this.offset & 7))) & 1);
    return v;
  }
  skip(n) {
    if (this.offset + n > this.bytes.length * 8) throw new RangeError('truncated DSI');
    this.offset += n;
  }
  align() { this.skip((8 - this.offset % 8) % 8); }
}
function layout(mask) {
  // Deprecated group 8 aliases group 7; do not count those speakers twice.
  if (mask & 0x100) mask = (mask | 0x80) & ~0x100;
  const speakers = GROUPS.flatMap((group, i) => mask & (1 << i) ? group : []);
  const names = { 1: 'stereo', 2: 'mono', 7: '5.0', 0x47: '5.1', 0x4f: '7.1', 0x77: '5.1.4', 0x7f: '7.1.4', 0xc7: '5.1.2', 0xcf: '7.1.2' };
  return { channels: speakers.length || null, channelLayout: names[mask] || speakers.join(' ') || null };
}
function bitrate(bits) { bits.skip(2); const rate = bits.read(32); bits.skip(32); return rate || null; }
function channelMask(bits, p) {
  const reserved = bits.read(6), mask = bits.read(18);
  if (reserved || !mask) p.issues.push('reserved or empty channel mask');
  if (mask & HEIGHT_MASK) p.signals.push('height channel groups');
  return mask;
}
function substreamGroup(bits, p) {
  bits.skip(2); // b_substreams_present, b_hsf_ext
  const channelCoded = bits.read(1), count = bits.read(8);
  if (!count) p.issues.push('empty substream group');
  for (let i = 0; i < count; i++) {
    bits.skip(2); // dsi_sf_multiplier
    if (bits.read(1)) bits.skip(5); // substream_bitrate_indicator
    if (channelCoded) channelMask(bits, p);
    else {
      p.signals.push('object-based substream group');
      if (bits.read(1)) { // b_ajoc
        if (!bits.read(1)) bits.skip(4); // b_static_dmx, n_dmx_objects_minus1
        bits.skip(6); // n_umx_objects_minus1
      }
      bits.skip(4); // bed, dynamic, ISF object flags and reserved
    }
  }
  if (bits.read(1)) { // b_content_type
    bits.skip(3); // content_classifier
    if (bits.read(1)) bits.skip(bits.read(6) * 8); // language tag
  }
}
function presentation(bytes, version) {
  const p = { complete: false, signals: [], issues: [], channels: null, channelLayout: null };
  if (![1, 2].includes(version)) { p.issues.push(`unsupported presentation_version=${version}`); return p; }
  const bits = new Bits(bytes);
  try {
    const config = bits.read(5);
    let addEmdf = config === 6;
    if (config === 6) p.issues.push('EMDF-only presentation');
    else {
      bits.skip(3); // md_compat
      if (bits.read(1)) bits.skip(5); // presentation_id
      bits.skip(2 + 2 + 5 + 10); // frame rate, EMDF version, key id
      if (bits.read(1)) { // b_presentation_channel_coded
        const mode = bits.read(5);
        if (mode > 15) p.issues.push(`unsupported dsi_presentation_ch_mode=${mode}`);
        if (mode >= 11 && mode <= 14) {
          bits.skip(1); // pres_b_4_back_channels_present; active speakers are in mask
          const topPairs = bits.read(2);
          if (topPairs) p.signals.push(`immersive channel mode ${mode}`);
          if (topPairs === 3) p.issues.push('reserved pres_top_channel_pairs=3');
        }
        if ([9, 10, 15].includes(mode)) p.signals.push(`height channel mode ${mode}`);
        Object.assign(p, layout(channelMask(bits, p)));
      }
      if (bits.read(1) && bits.read(1)) bits.skip(2); // core channel mode
      if (bits.read(1)) { bits.skip(1); bits.skip(bits.read(8) * 8); } // filter
      if (config === 31) substreamGroup(bits, p);
      else {
        bits.skip(1); // b_multi_pid
        if (config <= 5) {
          const groups = config <= 2 ? 2 : config <= 4 ? 3 : bits.read(3) + 2;
          for (let i = 0; i < groups; i++) substreamGroup(bits, p);
        } else { p.issues.push(`unsupported presentation_config_v1=${config}`); bits.skip(bits.read(7) * 8); }
      }
      const preVirtualized = bits.read(1);
      // Dolby's IMS DSI extension uses presentation_version=2 with v1 syntax,
      // paired with a legacy stereo presentation (b_pres_ims is a TOC helper,
      // not a field to read from this DSI).
      if (version === 2) {
        p.signals.push('immersive stereo (presentation_version=2)');
        if (!preVirtualized) p.issues.push('IMS presentation without b_pre_virtualized');
      }
      addEmdf = bits.read(1);
    }
    if (addEmdf) bits.skip(bits.read(7) * 15);
    if (bits.read(1)) bitrate(bits);
    if (bits.read(1)) { // alternative_info
      bits.align(); bits.skip(bits.read(16) * 8);
      bits.skip(bits.read(5) * 11); // target_md_compat, target_device_category
    }
    bits.align();
    if (bits.offset + 8 <= bytes.length * 8) {
      bits.skip(1); // de_indicator
      if (bits.read(1)) p.signals.push('immersive_audio_indicator');
      bits.skip(4);
      if (bits.read(1)) bits.skip(9); else bits.skip(1); // extended presentation ID
    }
    if (bits.offset !== bytes.length * 8) p.issues.push('unparsed presentation extension bytes');
    p.complete = p.issues.length === 0;
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    p.issues.push('truncated presentation DSI');
  }
  return p;
}
export function parseAc4Dsi(bytes) {
  const result = { complete: false, immersive: false, channels: null, channelLayout: null, sampleRateHz: null, bitRateBps: null, presentations: [], issues: [] };
  const bits = new Bits(bytes);
  try {
    const version = bits.read(3);
    if (version !== 1) { result.issues.push(`unsupported ac4_dsi_version=${version}`); return finish(result); }
    const bitstreamVersion = bits.read(7);
    result.sampleRateHz = bits.read(1) ? 48000 : 44100;
    bits.skip(4); // frame_rate_index
    const count = bits.read(9);
    if (bitstreamVersion > 2) { result.issues.push(`unsupported bitstream_version=${bitstreamVersion}`); return finish(result); }
    if (bitstreamVersion > 1 && bits.read(1)) {
      bits.skip(16); if (bits.read(1)) bits.skip(128); // program ID, UUID
    }
    result.bitRateBps = bitrate(bits); bits.align();
    if (!count) result.issues.push('no presentations');
    for (let i = 0; i < count; i++) {
      const presentationVersion = bits.read(8);
      let length = bits.read(8);
      if (length === 255) length += bits.read(16);
      const start = bits.offset / 8;
      const p = presentation(bytes.subarray(start, Math.min(start + length, bytes.length)), presentationVersion);
      if (i === 0) { result.channels = p.channels; result.channelLayout = p.channelLayout; }
      result.presentations.push(p);
      result.immersive ||= p.signals.length > 0;
      result.issues.push(...p.issues.map(s => `presentation ${i + 1}: ${s}`));
      bits.skip(length * 8);
    }
    if (bits.offset !== bytes.length * 8) result.issues.push('unparsed DSI trailing bytes');
    result.complete = count > 0 && result.issues.length === 0 && result.presentations.every(p => p.complete);
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    result.issues.push('truncated AC-4 DSI');
  }
  return finish(result);
}
function finish(result) {
  const signals = [...new Set(result.presentations.flatMap(p => p.signals))];
  result.kind = result.immersive ? 'signaled' : result.complete ? 'absent' : result.presentations.some(p => p.complete) ? 'not_found_in_scope' : 'unsupported';
  result.value = result.immersive ? true : result.complete ? false : null;
  result.note = result.immersive ? `AC-4 DSI signals ${signals.join(', ')}; not decoded object audio` : result.complete
    ? `AC-4 DSI: all ${result.presentations.length} presentation(s) fully parsed; no immersive or object content signalled`
    : `AC-4 DSI inconclusive: ${[...new Set(result.issues)].join('; ')}`;
  return result;
}
