/** TV Capture summary: verdict tiles, evidence cards and the raw events drawer. */

import { badge, verdict } from '../components/badge.js';
import { card, kvList } from '../components/card.js';
import { copyMenu } from '../components/copy.js';
import { jsonDrawer } from '../components/drawer.js';
import { emptyState, notice } from '../components/feedback.js';
import { icon } from '../components/icon.js';
import { el } from '../dom.js';
import * as fmt from '../format.js';
import { join, RENDERER_HINTS } from './capture-timeline.js';

const DOLBY_FORMATS = new Set(['ec+3', 'ec-3', 'ec3', 'qc+3']);
const AAC_FORMATS = new Set(['qaac', 'aacp', 'aac']);

/** renderer_evidence.verdict -> [tile headline, tone, full sentence]. */
const RENDERER_VERDICTS = {
  app_spatial_rendering_active: ['Spatial audio on', 'success', 'App-level spatial rendering is active.'],
  app_spatial_rendering_was_active: [
    'Was on, now off',
    'warn',
    'App-level spatial rendering was observed, but the last flag is false.',
  ],
  lower_level_active_app_spatial_false: [
    'Engaged, app flag off',
    'warn',
    'Atmos/spatial machinery is active, but TV.app’s app-level spatial rendering flag is false.',
  ],
  lower_level_spatialization_active: [
    'Spatial mixer active',
    'success',
    'Lower-level Atmos/spatial machinery is active.',
  ],
  no_spatial_renderer_activity: ['No activity', 'neutral', 'No spatial renderer activity was captured.'],
};

/** downloaded_hls_verdict -> [tile headline, tone, full sentence]. */
const MOVPKG_VERDICTS = {
  movpkg_atmos_variant_present_but_not_selected: [
    'Atmos skipped',
    'warn',
    'The .movpkg holds an Atmos variant, but the stereo group was selected.',
  ],
  movpkg_figstreamplayer_selected_stereo: [
    'Stereo selected',
    'warn',
    'FigStreamPlayer selected the stereo audio group.',
  ],
  movpkg_atmos_variant_selected: ['Atmos selected', 'success', 'The downloaded Atmos variant is playing.'],
  movpkg_atmos_variant_missing_or_incomplete: ['Atmos missing', 'warn', 'The Atmos variant is missing or incomplete.'],
};

const GROUP_KINDS = {
  atmos: ['Atmos group', 'success'],
  stereo: ['Stereo group', 'warn'],
  audio_description: ['Audio description', 'info'],
};

const DELIVERIES = { downloaded_movpkg: 'Downloaded .movpkg', online_hls: 'Online HLS' };

// ---------------------------------------------------------------------------
// Audio vocabulary

function isAtmos(audio) {
  return Boolean(audio?.is_atmos) || (DOLBY_FORMATS.has((audio?.format ?? '').toLowerCase()) && audio.channels >= 16);
}

/** "Dolby Atmos", "E-AC-3 5.1", "AAC stereo", … */
function audioName(audio) {
  const format = (audio?.format ?? '').toLowerCase();
  if (!format) return fmt.DASH;
  if (isAtmos(audio)) return 'Dolby Atmos';
  const layout = audio.channels ? ` ${fmt.channelsLabel(audio.channels).toLowerCase()}` : '';
  if (format === 'qc+3') return `Apple Dolby-like${layout}`;
  if (DOLBY_FORMATS.has(format)) return `E-AC-3${layout}`;
  if (AAC_FORMATS.has(format)) return `AAC${layout}`;
  return `${audio.format}${layout}`;
}

function audioDetail(audio) {
  return join(
    audio.format,
    audio.channels && `${audio.channels} ch`,
    audio.sample_rate && fmt.hertz(audio.sample_rate),
  );
}

/** True when the best observed audio is a real upgrade over what is playing now. */
function betterAudioSeen(p) {
  const { audio, best_audio: best } = p;
  if (!audio || !best) return false;
  return (isAtmos(best) && !isAtmos(audio)) || (best.channels ?? 0) > (audio.channels ?? 0);
}

function stateBadge(value, yes = 'yes', no = 'no') {
  if (value == null) return el('span', { class: 'muted' }, 'not captured');
  return badge(value ? yes : no, { tone: value ? 'success' : 'neutral' });
}

// ---------------------------------------------------------------------------
// Verdict tiles

function sourceVerdict(p) {
  const value = {
    hls: DELIVERIES[p.hls_delivery] ?? 'HLS stream',
    local_file: 'Local file',
  }[p.source];
  return verdict({
    label: 'Source',
    iconName: p.source === 'local_file' ? 'file' : 'stream',
    value: value ?? 'Unknown',
    detail: p.pipeline_engine ? `via ${p.pipeline_engine}` : 'Pipeline engine not captured',
  });
}

function videoVerdict(p) {
  const base = { label: 'Dolby Vision', iconName: 'dolbyVision' };
  if (p.dolby_vision_active === true) {
    return verdict({ ...base, value: 'Active', detail: `Decoded as ${p.decoded_fourcc}`, tone: 'success' });
  }
  if (p.dolby_vision_active === false) {
    return verdict({
      ...base,
      value: 'Not engaged',
      detail: `Decoded as ${p.decoded_fourcc} — HDR10/SDR fallback`,
      tone: 'danger',
    });
  }
  if (p.dv_label) return verdict({ ...base, value: 'Private HDR path', detail: p.dv_label, tone: 'info' });
  if (p.decoded_fourcc) {
    return verdict({ ...base, value: 'Not signalled', detail: `Decoded as ${p.decoded_fourcc}`, tone: 'neutral' });
  }
  const variant = p.hls_variant;
  if (variant) {
    const dvVariant = /^dv[ha]/.test(p.video_fourcc ?? '');
    return verdict({
      ...base,
      value: dvVariant ? 'DV variant' : `${variant.video_range ?? 'Video'} variant`,
      detail: join(p.video_fourcc, variant.width && `${variant.width}×${variant.height}`, 'decode not captured'),
      tone: dvVariant ? 'success' : 'neutral',
    });
  }
  return verdict({ ...base, value: 'Not captured', detail: 'No video decode events', tone: 'unknown' });
}

function audioVerdict(p) {
  const base = { label: 'Audio', iconName: 'audio' };
  if (!p.audio) return verdict({ ...base, value: 'Not captured', detail: 'No audio format events', tone: 'unknown' });
  const better = betterAudioSeen(p);
  return verdict({
    ...base,
    value: audioName(p.audio),
    detail: better ? `Better seen: ${audioName(p.best_audio)}` : audioDetail(p.audio),
    tone: isAtmos(p.audio) ? 'success' : better ? 'warn' : 'plain',
  });
}

function spatialVerdict(evidence) {
  const [value, tone] = RENDERER_VERDICTS[evidence.verdict] ?? [fmt.humanize(evidence.verdict), 'unknown'];
  return verdict({
    label: 'Spatial audio',
    iconName: 'atmos',
    value,
    detail: evidence.routes?.length ? `Route: ${evidence.routes.at(-1)}` : 'Route not captured',
    tone,
  });
}

function groupVerdict(p) {
  const [value, tone] = GROUP_KINDS[p.selected_hls_audio_group_kind] ?? ['Unknown group', 'unknown'];
  return verdict({
    label: 'HLS audio group',
    iconName: 'stream',
    value,
    detail: join(p.audio_codec, DELIVERIES[p.hls_delivery]),
    tone,
  });
}

function movpkgVerdict(p) {
  const [value, tone] = MOVPKG_VERDICTS[p.downloaded_hls_verdict] ?? [
    fmt.humanize(p.downloaded_hls_verdict),
    'unknown',
  ];
  return verdict({ label: 'Download', iconName: 'package', value, detail: 'Downloaded .movpkg verdict', tone });
}

// ---------------------------------------------------------------------------
// Cards

function playbackCard(p, { wide }) {
  const variant = p.hls_variant;
  const others = (p.pipeline_engines_observed ?? []).filter((engine) => engine !== p.pipeline_engine);
  const fourccTone = (fourcc) => {
    const f = fourcc.toLowerCase();
    if (f.startsWith('dv')) return 'success';
    if (f === 'qdh1') return 'warn';
    return f === 'hvc1' ? 'danger' : 'neutral';
  };
  const rows = [
    ['Pipeline engine', p.pipeline_engine, { hint: others.length ? `also saw ${others.join(', ')}` : null }],
    ['Decoded FourCC', p.decoded_fourcc && badge(p.decoded_fourcc, { tone: fourccTone(p.decoded_fourcc) })],
    ['Decoder', p.decoder],
    ['Decoded resolution', p.decoded_resolution],
    [
      'HLS variant',
      variant &&
        join(
          variant.width && `${variant.width}×${variant.height}`,
          variant.fps && `${variant.fps} fps`,
          variant.video_range,
        ),
    ],
    ['HDCP', variant?.hdcp],
    [
      'HLS bitrate',
      (p.peak_mbps || p.avg_mbps) && join(p.peak_mbps && `${p.peak_mbps} Mbps peak`, p.avg_mbps && `${p.avg_mbps} avg`),
    ],
    ['Variant codecs', variant?.codecs, { mono: true, wide: true }],
    ['File codec', p.file_player?.codec, { mono: true }],
    ['Encryption scheme', p.file_player?.encryption_scheme],
    ['Capture quality', p.capture_quality?.label],
  ];
  return card({
    title: 'Playback & video',
    iconName: 'video',
    className: wide ? 'span-full' : null,
    body: kvList(rows.filter(([, value]) => value != null && value !== '')),
  });
}

function audioPanel(title, audio) {
  return el(
    'section',
    { class: 'audio-panel', 'aria-label': title },
    el('p', { class: 'audio-panel-title' }, title),
    el('p', { class: 'audio-panel-name' }, audioName(audio)),
    kvList(
      [
        ['Format', audio.format, { mono: true }],
        ['Channels', audio.channels],
        ['Sample rate', fmt.hertz(audio.sample_rate)],
        ['Spatialization', audio.spatialization],
        ['Atmos eligible', audio.spatialization_eligible],
        ['Decodable', audio.decodable == null ? null : stateBadge(audio.decodable)],
        ['Diagnosis', audio.diagnosis, { wide: true }],
      ].filter(([, value]) => value != null && value !== ''),
    ),
  );
}

function audioCard(p) {
  const differs = p.best_audio && JSON.stringify(p.best_audio) !== JSON.stringify(p.audio);
  const observed = p.observed_audio ?? [];
  if (!p.audio) {
    return card({
      title: 'Audio',
      iconName: 'audio',
      className: 'span-full',
      body: emptyState({ iconName: 'audio', title: 'No audio events', body: 'Nothing reported the audio format.' }),
    });
  }
  return card({
    title: 'Audio',
    iconName: 'audio',
    className: 'span-full',
    body: [
      el(
        'div',
        { class: 'audio-panels' },
        p.audio && audioPanel(differs ? 'Current (latest event)' : 'Current = best observed', p.audio),
        differs && audioPanel('Best observed', p.best_audio),
      ),
      observed.length > 0 &&
        el(
          'div',
          { class: 'observed' },
          el('p', { class: 'observed-title' }, 'Observed formats'),
          el(
            'ul',
            { class: 'badge-row' },
            observed.map((audio) =>
              el(
                'li',
                {},
                badge(join(audio.format, audio.channels && `${audio.channels} ch`), {
                  tone: isAtmos(audio) ? 'success' : 'neutral',
                }),
                audio.count > 1 && el('span', { class: 'event-repeat' }, `×${audio.count}`),
              ),
            ),
          ),
        ),
    ],
  });
}

function hlsCard(p) {
  const [kindLabel, kindTone] = GROUP_KINDS[p.selected_hls_audio_group_kind] ?? [
    p.selected_hls_audio_group_kind,
    'neutral',
  ];
  const movpkg = MOVPKG_VERDICTS[p.downloaded_hls_verdict];
  return card({
    title: 'HLS audio & download',
    iconName: 'stream',
    body: kvList(
      [
        ['Selected audio group', p.selected_hls_audio_group, { mono: true, wide: true }],
        ['Group kind', kindLabel && badge(kindLabel, { tone: kindTone })],
        ['Audio codec', p.audio_codec, { mono: true }],
        ['Delivery', DELIVERIES[p.hls_delivery] ?? p.hls_delivery],
        [
          'Download verdict',
          p.downloaded_hls_verdict && (movpkg ? badge(movpkg[0], { tone: movpkg[1] }) : p.downloaded_hls_verdict),
          { hint: movpkg?.[2], wide: true },
        ],
      ].filter(([, value]) => value != null && value !== ''),
    ),
  });
}

function rendererCard(r) {
  const [, tone, sentence] = RENDERER_VERDICTS[r.verdict] ?? [null, 'neutral', fmt.humanize(r.verdict)];
  const list = (values) => (values?.length ? values.join(', ') : null);
  const evidence = [
    ...(r.media_formatinfo ?? []).map((item) => ['App format', RENDERER_HINTS.media_formatinfo(item)]),
    ...(r.spatial_power ?? []).map((item) => ['Spatial power', RENDERER_HINTS.spatial_power(item)]),
    ...(r.atmos_decoder_states ?? []).map((item) => ['Atmos decoder', RENDERER_HINTS.atmos_decoder_state(item)]),
    ...(r.mixer_spatial_status ?? []).map((item) => ['Mixer', RENDERER_HINTS.mixer_spatial_status(item)]),
  ];
  const rows = [
    ['Output route', list(r.routes)],
    ['Route capability', r.route_capability_label],
    [
      'Max spatializable channels',
      r.max_spatializable_channels,
      {
        hint:
          r.route_spatial_capable == null
            ? null
            : r.route_spatial_capable
              ? 'spatial-capable route'
              : 'route can’t spatialize',
      },
    ],
    ['App spatial rendering — ever', stateBadge(r.app_spatial_rendering_ever_true)],
    ['App spatial rendering — last', stateBadge(r.app_spatial_rendering_last_state)],
    ['First true at', r.first_true_at_seconds != null ? `${r.first_true_at_seconds} s into capture` : null],
    [
      'Immersive rendering requested',
      r.immersive_rendering_requested != null ? stateBadge(r.immersive_rendering_requested) : null,
    ],
    ['Allowed formats mask', list(r.allowed_spatialization_formats_masks), { mono: true }],
    ['Lower-level spatialization', stateBadge(r.lower_level_spatialization_active, 'active', 'inactive')],
    ['Spatial power', stateBadge(r.spatial_power_active, 'active', 'inactive')],
    [
      'Head tracking',
      stateBadge(r.head_tracking_active, 'active', 'inactive'),
      {
        hint: r.prefers_head_tracked_spatialization?.length
          ? `prefers: ${r.prefers_head_tracked_spatialization.map(fmt.yesNo).join(', ')}`
          : null,
      },
    ],
    ['Atmos decoder', stateBadge(r.atmos_decoder_active, 'active', 'inactive'), { hint: list(r.decoder_subtypes) }],
    ['OAR mode', stateBadge(r.oar_mode_active, 'active', 'inactive')],
    [
      'Mixer spatializable',
      stateBadge(r.mixer_content_spatializable),
      {
        hint: r.mixer_spatialization_statuses?.length ? `statuses ${r.mixer_spatialization_statuses.join(', ')}` : null,
      },
    ],
    ['AUSpatialMixer layouts', list(r.auspatial_channel_layouts), { mono: true }],
    ['AUSpatialMixer processors', list(r.auspatial_channel_processors)],
    ['AudioQueue forced 7.1.4', r.audioqueue_forced_atmos_714 ? stateBadge(true) : null],
    [
      'Spatial audio sources',
      list(r.spatial_audio_sources),
      { mono: true, hint: r.spatial_source_unknown ? 'includes an unknown source' : null },
    ],
    ['Bound apps', list(r.spatial_binding_apps)],
    ['Spatial rendering changes', r.spatial_rendering_changed_count || null],
  ];
  return card({
    title: 'Spatial audio renderer',
    iconName: 'atmos',
    className: 'span-full',
    body: [
      el('p', { class: 'renderer-verdict', dataset: { tone } }, icon('info', { size: 16 }), sentence),
      r.verdict_note && el('p', { class: 'card-note' }, r.verdict_note),
      kvList(rows.filter(([, value]) => value != null && value !== '')),
      evidence.length > 0 &&
        el(
          'div',
          { class: 'evidence' },
          el('p', { class: 'observed-title' }, 'Renderer evidence'),
          el(
            'ul',
            { class: 'evidence-list' },
            evidence.map(([kind, text]) =>
              el('li', {}, el('span', { class: 'evidence-kind' }, kind), el('span', {}, text)),
            ),
          ),
        ),
      r.observed_hints?.length > 0 &&
        el(
          'div',
          { class: 'observed' },
          el('p', { class: 'observed-title' }, 'Hints observed'),
          el(
            'ul',
            { class: 'badge-row' },
            r.observed_hints.map((hint) => el('li', {}, badge(fmt.humanize(hint)))),
          ),
        ),
    ],
  });
}

function systemCard(system) {
  const { video, display = {}, audio = {}, network, now_playing: playing } = system;
  const list = (items) => el('ul', { class: 'system-evidence-list' }, items.map((item) => el('li', {}, item)));
  const group = (title, body, className) => el(
    'div', { class: ['system-evidence-group', className] },
    el('h4', { class: 'observed-title' }, title), body,
  );
  const measured = (value, unit) => value == null ? null : `${fmt.num(value)} ${unit}`;
  return card({
    title: 'System evidence',
    iconName: 'capture',
    meta: fmt.plural(system.sample_count ?? 0, 'sample'),
    body: [
      notice({
        tone: 'info',
        title: system.verdict?.label ?? 'System observations',
        body: list(system.verdict?.notes ?? []),
      }),
      group('Video decode', video ? [
        el('p', { class: 'card-note' }, video.description),
        kvList([
          ['Codec', video.codec],
          ['Coded resolution', video.coded_resolution],
          ['Bit depth', measured(video.bit_depth, 'bit')],
          ['Chroma', video.chroma],
          ['DRM protection', stateBadge(video.protected, 'Protected', 'Not protected')],
          ['Player decode', stateBadge(video.decoded_in_player_process, 'Observed', 'Not attributed')],
        ]),
      ] : el('p', { class: 'card-note' }, 'No hardware decoder session observed.')),
      group('Display HDR', kvList([
        ['HDR presented', stateBadge(display.hdr_presented, 'Presented', 'Not observed')],
        ['Max EDR headroom', measured(display.max_edr_headroom, '× SDR')],
        ['Potential headroom', measured(display.potential_edr_headroom, '× SDR')],
        ['Panel nits cap', measured(display.max_panel_nits_cap, 'nits')],
      ])),
      group('Audio output', kvList([
        ['Outputs observed', audio.outputs?.length ? audio.outputs.join(', ') : null],
        ['Playback assertion', stateBadge(audio.playback_assertion_seen, 'Observed', 'Not observed')],
      ])),
      group('Download throughput', network ? [
        kvList([
          ['Process', network.process],
          ['Average download', measured(network.avg_mbps, 'Mbit/s')],
          ['Peak download', measured(network.peak_mbps, 'Mbit/s')],
          ['Received', measured(network.received_mb, 'MB')],
        ]),
        el('p', { class: 'card-note' }, 'Measured process downloads, not the advertised media bitrate.'),
      ] : el('p', { class: 'card-note' }, 'No download throughput observed during this capture.')),
      group('Now Playing', playing ? kvList([
        ['Show / album', playing.album],
        ['Title', playing.title],
        ['Artist', playing.artist],
        ['Position / duration', `${fmt.duration(playing.elapsed_s)} / ${fmt.duration(playing.duration_s)}`],
        ['Playback', playing.is_playing == null ? null : playing.is_playing ? 'Playing' : 'Paused'],
      ].filter(([, value]) => value != null)) : el('p', { class: 'card-note' },
        system.now_playing_unavailable || 'No Now Playing metadata observed.')),
      group('Opened media files', system.media_files?.length ? list(system.media_files)
        : el('p', { class: 'card-note' }, 'No media files observed. Streaming media may not expose a local file.')),
      system.not_observable?.length > 0 && group(
        'Needs administrator', list(system.not_observable), 'system-admin-note',
      ),
    ],
  });
}

// ---------------------------------------------------------------------------
// Summary

/** Render a capture summary (live or imported). `origin` labels where it came from. */
export function renderSummary(summary, { origin }) {
  const returned = summary.events_returned ?? summary.events?.length ?? summary.event_count;
  const dropped = summary.events_dropped ?? Math.max(0, summary.event_count - returned);
  const limit = summary.raw_event_limit ?? returned;
  const p = summary.playback;
  const facts = join(
    origin,
    fmt.plural((summary.event_count ?? 0) + (summary.system_event_count ?? 0), 'event'),
    summary.duration_s ? `${fmt.num(summary.duration_s, 1)} s` : null,
  );

  const header = el(
    'header',
    { class: 'summary-header' },
    el(
      'div',
      {},
      el('h2', { id: 'capture-summary-title' }, 'Playback summary'),
      el('p', { class: 'summary-facts' }, facts),
    ),
    copyMenu([
      {
        label: 'Copy summary',
        text: () =>
          JSON.stringify(
            {
              event_count: summary.event_count,
              system_event_count: summary.system_event_count,
              capture_mode: summary.capture_mode,
              log_access: summary.log_access,
              system: summary.system,
              events_returned: returned,
              events_dropped: dropped,
              raw_event_limit: limit,
              duration_s: summary.duration_s,
              playback: p,
            },
            null,
            2,
          ),
      },
      { label: 'Copy full JSON', iconName: 'code', text: () => JSON.stringify(summary, null, 2) },
    ]),
  );

  if ((!p || !Object.keys(p).length) && !summary.system) {
    return el(
      'div',
      { class: 'capture-summary-body' },
      header,
      emptyState({
        iconName: 'capture',
        title: 'No playback events captured',
        body: 'Did TV.app play anything while capturing? Start the capture before pressing play to catch initialisation.',
      }),
    );
  }

  const evidence = p?.renderer_evidence;
  const tiles = p ? [
    sourceVerdict(p),
    videoVerdict(p),
    audioVerdict(p),
    evidence && spatialVerdict(evidence),
    p.selected_hls_audio_group && groupVerdict(p),
    p.downloaded_hls_verdict && movpkgVerdict(p),
  ] : [];
  return el(
    'div',
    { class: 'capture-summary-body' },
    header,
    p && el('ul', { class: 'verdicts', 'aria-label': 'Playback verdicts' }, tiles),
    p?.dv_diagnosis &&
      notice({
        tone: p.dolby_vision_active === false ? 'warn' : 'info',
        title: 'Video pipeline',
        body: p.dv_diagnosis,
      }),
    p?.capture_quality?.likely_missed_audio_init &&
      notice({ tone: 'warn', title: 'Audio initialisation probably missed', body: p.capture_quality.note }),
    p && el(
      'div',
      { class: 'summary-grid' },
      playbackCard(p, { wide: !p.selected_hls_audio_group }),
      p.selected_hls_audio_group && hlsCard(p),
      audioCard(p),
      evidence && rendererCard(evidence),
    ),
    summary.system && systemCard(summary.system),
    (!summary.system || summary.event_count > 0) && jsonDrawer(() => summary.events ?? [], {
      label: 'Raw log events',
      meta:
        dropped > 0
          ? `Newest ${returned} of ${summary.event_count} — ${dropped} older dropped (cap ${limit})`
          : `All ${fmt.plural(returned, 'retained event')} (cap ${limit})`,
    }),
  );
}
