/**
 * Live events timeline for TV Capture: a one-line, human summary for every
 * event kind and renderer hint the parser emits, consecutive repeats folded
 * into a ×N counter, newest at the bottom with autoscroll while pinned there.
 */

import { el, replace } from '../dom.js';
import * as fmt from '../format.js';

const MAX_ROWS = 500;

/** Event kinds in a fixed order; each owns one series colour for its chip dot. */
const KINDS = {
  hls_variant: 'HLS variant',
  codec_type: 'Video decode',
  audio_format: 'Audio format',
  renderer_hint: 'Renderer',
  pipeline_engine: 'Engine',
  file_player: 'File player',
  luma_chroma: 'Luma/chroma',
};

const on = (value, yes = 'on', no = 'off') => (value == null ? null : value ? yes : no);
export const join = (...parts) => parts.filter((part) => part || part === 0).join(' · ');
const channels = (count) => (count ? `${count} ch` : null);
const rate = (hz) => (hz ? fmt.hertz(hz) : null);
const size = (width, height) => (width && height ? `${width}×${height}` : null);

/** Renderer hints: describe(event) per hint. Item summaries in the capture summary reuse these. */
export const RENDERER_HINTS = {
  media_formatinfo: (e) =>
    join(
      `Media format ${e.format ?? '?'}`,
      e.label,
      channels(e.channels),
      rate(e.sample_rate),
      `app spatial rendering ${on(e.rendering_spatial_audio) ?? '?'}`,
    ),
  spatial_power: (e) =>
    join(
      `Spatialization ${on(e.spatialization) ?? '?'}`,
      `stereo upmix ${on(e.stereo_upmix) ?? '?'}`,
      `head tracking ${on(e.head_tracking) ?? '?'}`,
    ),
  head_tracking_preference: (e) =>
    `Prefers head-tracked spatialization: ${fmt.yesNo(e.prefers_head_tracked_spatialization)}`,
  route: (e) => `Output route: ${e.route}`,
  max_spatializable_channels: (e) =>
    e.max_spatializable_channels
      ? `Route can spatialize up to ${e.max_spatializable_channels} channels`
      : 'Route can’t spatialize (0 channels)',
  spatial_audio_sources: (e) =>
    join(
      `Spatial audio sources: ${(e.spatial_audio_sources ?? []).join(', ') || 'none'}`,
      e.has_unknown_source && 'unknown source (?src)',
    ),
  spatial_binding_app: (e) => `Spatial audio bound to ${e.spatial_binding_app}`,
  atmos_decoder_state: (e) =>
    join(
      `Atmos decoder: Atmos ${fmt.yesNo(e.decoder_is_atmos)}`,
      `OAR ${fmt.yesNo(e.decoder_oar_mode)}`,
      e.decoder_is_tvos != null && `tvOS ${fmt.yesNo(e.decoder_is_tvos)}`,
    ),
  atmos_decoder_subtype: (e) => `Atmos decoder subtype ${e.decoder_subtype}`,
  audioqueue_forced_atmos_714: () => 'AudioQueue forced a 7.1.4 Atmos layout',
  auspatial_channel_layout: (e) => `AUSpatialMixer layout ${e.channel_layout}`,
  auspatial_channel_processors: (e) => `AUSpatialMixer channel processors: ${e.channel_processors}`,
  auspatial_mixer: () => 'AUSpatialMixer active',
  mixer_spatial_status: (e) =>
    join(
      `Mixer ${e.format ?? '?'}`,
      channels(e.channels),
      `spatializable ${fmt.yesNo(e.content_spatializable)}`,
      e.spatialization_status != null && `status ${e.spatialization_status}`,
    ),
  spatial_rendering_changed: () => 'Spatial rendering changed',
  allowed_spatialization_formats_mask: (e) => `Allowed spatialization formats mask ${e.mask}`,
  immersive_rendering_requested: (e) => `Immersive rendering requested: ${fmt.yesNo(e.immersive_rendering_requested)}`,
};

function describeAudio(e) {
  if (e.source === 'coreaudio_input_format')
    return join(`CoreAudio input ${e.format}`, channels(e.channels), rate(e.sample_rate));
  return join(
    e.format,
    channels(e.channels),
    rate(e.sample_rate),
    e.decodable != null && (e.decodable ? 'decodable' : 'not decodable'),
    e.spatialization && `spatialization ${e.spatialization}`,
    e.spatialization_eligible && `eligible ${e.spatialization_eligible}`,
    e.immersive_rendering_requested != null && `immersive ${fmt.yesNo(e.immersive_rendering_requested)}`,
    e.pipeline_engine,
  );
}

const DESCRIBE = {
  hls_variant: (e) =>
    join(
      size(e.width, e.height),
      e.video_range,
      e.codecs,
      e.peak_bps && `peak ${fmt.bitrate(e.peak_bps)}`,
      e.avg_bps && `avg ${fmt.bitrate(e.avg_bps)}`,
      e.fps && `${e.fps} fps`,
      e.audio_group,
    ) || 'Variant selected',
  codec_type: (e) => join(`Decoding ${e.fourcc ?? '?'}`, e.decoder, size(e.width, e.height)),
  audio_format: describeAudio,
  pipeline_engine: (e) => `Pipeline engine ${e.pipeline_engine}`,
  file_player: (e) =>
    join(
      `Local file ${e.codec}`,
      e.encryption_scheme != null && `encryption scheme ${e.encryption_scheme}`,
      size(e.width, e.height),
    ),
  luma_chroma: (e) => `Luma bit depth ${e.luma_depth ?? '?'} · chroma format ${e.chroma_format ?? '?'}`,
  renderer_hint: (e) => RENDERER_HINTS[e.hint]?.(e) ?? fmt.humanize(e.hint),
};

function describeEvent(event) {
  return DESCRIBE[event.kind]?.(event) ?? event.raw ?? fmt.humanize(event.kind);
}

/** Wall-clock time of an event: live events carry `ts`; imported ones keep it in the raw line. */
function eventTime(event) {
  if (event.ts) return fmt.clock(event.ts);
  return /\b\d{2}:\d{2}:\d{2}\b/.exec(event.raw ?? '')?.[0] ?? '';
}

/** Timeline bound to an <ol role="log"> inside a scrolling container. */
export class Timeline {
  #list;
  #scroller;
  #jump;
  #last = null;

  constructor(list, { scroller, jumpButton }) {
    this.#list = list;
    this.#scroller = scroller;
    this.#jump = jumpButton;
    scroller.addEventListener('scroll', () => {
      if (this.#pinned()) this.#jump.hidden = true;
    });
    jumpButton.addEventListener('click', () => this.#scrollToEnd());
  }

  clear() {
    this.#last = null;
    replace(this.#list);
    this.#jump.hidden = true;
  }

  /** Add one live event (animated). */
  add(event) {
    const pinned = this.#pinned();
    this.#append(event, true);
    if (pinned) this.#scrollToEnd();
    else this.#jump.hidden = false;
  }

  /** Replace the timeline with a batch of events (an imported log). */
  load(events) {
    this.clear();
    for (const event of events) this.#append(event, false);
    this.#scrollToEnd();
  }

  #append(event, animate) {
    const text = describeEvent(event);
    const key = `${event.kind}\u0000${text}`;
    if (this.#last?.key === key) {
      this.#last.count += 1;
      this.#last.counter ??= this.#last.row.appendChild(el('span', { class: 'event-repeat' }));
      this.#last.counter.textContent = `×${this.#last.count}`;
      return;
    }
    const row = el(
      'li',
      { class: ['event', animate && 'is-new'], dataset: { kind: event.kind }, title: event.raw },
      el('time', { class: 'event-time' }, eventTime(event)),
      el('span', { class: 'event-kind' }, KINDS[event.kind] ?? fmt.humanize(event.kind)),
      el('span', { class: 'event-text' }, text),
    );
    this.#list.append(row);
    this.#last = { key, row, count: 1, counter: null };
    while (this.#list.childElementCount > MAX_ROWS) this.#list.firstElementChild.remove();
  }

  #pinned() {
    const s = this.#scroller;
    return s.scrollHeight - s.scrollTop - s.clientHeight < 32;
  }

  #scrollToEnd() {
    this.#scroller.scrollTop = this.#scroller.scrollHeight;
    this.#jump.hidden = true;
  }
}
