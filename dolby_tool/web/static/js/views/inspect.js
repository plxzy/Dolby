/** Inspect view: one file in, a verdict strip, spec cards and the raw JSON out. */

import { getJson, postJson } from '../api.js';
import { badge, verdict } from '../components/badge.js';
import { card, checklist, kvList } from '../components/card.js';
import { copyMenu } from '../components/copy.js';
import { jsonDrawer } from '../components/drawer.js';
import { bone, emptyState, notice, toast, withCode } from '../components/feedback.js';
import { icon } from '../components/icon.js';
import { meter } from '../components/meter.js';
import { dataTable } from '../components/table.js';
import { $, el, replace } from '../dom.js';
import { bindDropzone } from '../dragdrop.js';
import * as fmt from '../format.js';

// The compare score saturates at 80 Mbps (UHD Blu-ray territory), so the meter shares that scale.
const BITRATE_SCALE_MBPS = 80;
const DV_FOURCCS = new Set(['dvh1', 'dvhe', 'dvav', 'dva1']);
const DOLBY_AUDIO = new Set(['eac3', 'ac3', 'truehd', 'ac4']);

const DV_PROFILE_NOTES = {
  5: 'Single layer, IPT-PQ colour. No HDR10 fallback — non-DV screens show wrong colours.',
  7: 'Dual layer (UHD Blu-ray). Apple players ignore the enhancement layer and play HDR10.',
  8.1: 'Single layer with an HDR10-compatible base — the streaming default.',
  8.2: 'Single layer with an SDR-compatible base.',
  8.4: 'Single layer with an HLG-compatible base, as recorded by iPhone.',
};

let form;
let input;
let output;
let requestId = 0;

export function init({ capabilities }) {
  form = $('#inspect-form');
  input = $('#inspect-path');
  output = $('#inspect-output');
  const browse = $('#inspect-browse');

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    inspect(input.value);
  });
  browse.addEventListener('click', pickFile);
  $('#inspect-reset').addEventListener('click', reset);
  bindDropzone(form, {
    onPaths: ([path]) => inspect(path),
    onProblem: (message) => toast(message, { tone: 'warn' }),
  });
  capabilities.then((caps) => {
    if (caps && !caps.tools.osascript) {
      browse.disabled = true;
      browse.title = 'The native file picker needs macOS — paste a path or drop a file instead';
    }
  });
  reset();
}

function reset() {
  requestId += 1;
  input.value = '';
  form.dataset.state = 'empty';
  replace(output, intro());
}

async function pickFile() {
  try {
    const { paths } = await getJson('/api/pick');
    if (paths.length) inspect(paths[0]);
  } catch (error) {
    toast(error.message, { tone: 'danger' });
  }
}

async function inspect(rawPath) {
  const path = rawPath.trim();
  if (!path) {
    input.focus();
    return;
  }
  const id = ++requestId;
  input.value = path;
  form.dataset.state = 'result';
  output.setAttribute('aria-busy', 'true');
  replace(output, loadingSkeleton());
  try {
    const spec = await postJson('/api/inspect', { path });
    if (id === requestId) replace(output, result(spec));
  } catch (error) {
    if (id === requestId) {
      replace(output, notice({ tone: 'danger', title: 'Couldn’t inspect this file', body: error.message }));
    }
  } finally {
    if (id === requestId) output.removeAttribute('aria-busy');
  }
}

// ---------------------------------------------------------------------------
// Empty and loading states

function intro() {
  const features = [
    [
      'dolbyVision',
      'Dolby Vision',
      'Profile, compatibility layer and the sample-entry FourCC that decides whether TV.app engages DV.',
    ],
    ['hdr', 'HDR metadata', 'Mastering display, content light level and HDR10+ dynamic metadata.'],
    ['atmos', 'Audio & subtitles', 'Every track’s codec, channel layout, bitrate, language and Atmos flag.'],
  ];
  return el(
    'ul',
    { class: 'intro-grid', 'aria-label': 'What Inspect reports' },
    features.map(([iconName, title, body]) =>
      el(
        'li',
        { class: 'intro-item' },
        el('span', { class: 'intro-icon' }, icon(iconName, { size: 18 })),
        el('h3', {}, title),
        el('p', {}, body),
      ),
    ),
  );
}

function loadingSkeleton() {
  return el(
    'div',
    { class: 'inspect-result', 'aria-label': 'Inspecting…' },
    el(
      'div',
      { class: 'result-header' },
      el('div', { class: 'skeleton-stack' }, bone('260px', '22px'), bone('420px', '12px')),
    ),
    el(
      'ul',
      { class: 'verdicts', 'aria-hidden': 'true' },
      Array.from({ length: 5 }, () =>
        el('li', { class: 'verdict' }, bone('70px', '10px'), bone('110px', '22px'), bone('140px', '10px')),
      ),
    ),
    el(
      'div',
      { class: 'spec-grid', dataset: { layout: 'plain' }, 'aria-hidden': 'true' },
      Array.from({ length: 3 }, () =>
        el(
          'div',
          { class: 'card skeleton-card' },
          bone('90px', '14px'),
          bone('100%', '10px'),
          bone('80%', '10px'),
          bone('60%', '10px'),
        ),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Result

function result(spec) {
  const dv = spec.video.dolby_vision ?? { present: false };
  return el(
    'article',
    { class: 'inspect-result', 'aria-labelledby': 'inspect-file-title' },
    resultHeader(spec),
    spec.video.fourcc_warning &&
      notice({ tone: 'danger', title: 'TV.app won’t engage Dolby Vision', body: withCode(spec.video.fourcc_warning) }),
    el('ul', { class: 'verdicts', 'aria-label': 'At a glance' }, verdictTiles(spec)),
    el(
      'div',
      { class: 'spec-grid', dataset: { layout: dv.present ? 'dv' : 'plain' } },
      videoCard(spec.video),
      colorCard(spec.video),
      dv.present && dolbyVisionCard(dv),
      containerCard(spec.container),
      audioCard(spec.audio),
      subtitlesCard(spec.subtitles),
    ),
    jsonDrawer(spec, { label: 'Raw JSON', meta: 'The full spec, as returned by /api/inspect' }),
  );
}

function resultHeader(spec) {
  const extension = spec.filename.includes('.') ? spec.filename.split('.').pop().toUpperCase() : null;
  const facts = [extension, fmt.duration(spec.container.duration_s), spec.container.size_pretty].filter(Boolean);
  return el(
    'header',
    { class: 'result-header' },
    el('span', { class: 'result-icon' }, icon('file', { size: 22 })),
    el(
      'div',
      { class: 'result-heading' },
      el('h2', { id: 'inspect-file-title' }, spec.filename),
      el('p', { class: 'result-facts' }, facts.join(' · ')),
      el('p', { class: 'result-path mono' }, spec.path),
    ),
    copyMenu([
      { label: 'Copy summary', iconName: 'copy', text: () => summaryText(spec) },
      { label: 'Copy JSON', iconName: 'code', text: () => JSON.stringify(spec, null, 2) },
    ]),
  );
}

function verdictTiles(spec) {
  const { video, audio } = spec;
  return [
    dolbyVisionVerdict(video),
    fourccVerdict(video),
    hdrVerdict(video),
    atmosVerdict(audio),
    pictureVerdict(video),
  ];
}

function dolbyVisionVerdict(video) {
  const dv = video.dolby_vision;
  if (!dv?.present) {
    return verdict({
      label: 'Dolby Vision',
      iconName: 'dolbyVision',
      value: 'Not present',
      detail: 'No DOVI configuration record',
      tone: 'neutral',
    });
  }
  const level = dv.level != null ? ` · Level ${dv.level}` : '';
  return verdict({
    label: 'Dolby Vision',
    iconName: 'dolbyVision',
    value: `DV ${dv.profile ?? dv.profile_raw}`,
    detail: `${dv.compatibility_name} compat${level}`,
    tone: 'success',
  });
}

function fourccVerdict(video) {
  const fourcc = video.fourcc && !video.fourcc.startsWith('[') ? video.fourcc : null;
  const base = { label: 'Sample entry', iconName: 'tag' };
  if (!fourcc) return verdict({ ...base, value: 'None', detail: 'This container has no FourCC', tone: 'neutral' });
  if (video.dolby_vision?.present) {
    return DV_FOURCCS.has(fourcc)
      ? verdict({ ...base, value: fourcc, detail: 'TV.app engages Dolby Vision', tone: 'success' })
      : verdict({ ...base, value: fourcc, detail: 'TV.app will fall back to the base layer', tone: 'danger' });
  }
  if (fourcc === 'hev1') return verdict({ ...base, value: fourcc, detail: 'Apple players expect hvc1', tone: 'warn' });
  if (fourcc === 'hvc1' || fourcc === 'avc1') {
    return verdict({ ...base, value: fourcc, detail: 'Apple-compatible tag', tone: 'success' });
  }
  return verdict({ ...base, value: fourcc, detail: `${fmt.codecName(video.codec)} sample entry` });
}

function hdrVerdict(video) {
  const { mdcv, cll } = video.hdr10 ?? {};
  const statics = [mdcv && 'MDCV', cll && 'CLL'].filter(Boolean).join(' + ');
  const base = { label: 'HDR', iconName: 'hdr' };
  if (video.hdr10_plus) {
    return verdict({
      ...base,
      value: 'HDR10+',
      detail: `Dynamic metadata${statics ? ` · ${statics}` : ''}`,
      tone: 'success',
    });
  }
  if (mdcv || cll) return verdict({ ...base, value: 'HDR10', detail: `Static metadata · ${statics}`, tone: 'success' });
  if (video.color_transfer === 'smpte2084') {
    return verdict({ ...base, value: 'PQ', detail: 'No static HDR10 metadata', tone: 'warn' });
  }
  if (video.color_transfer === 'arib-std-b67')
    return verdict({ ...base, value: 'HLG', detail: 'Hybrid log-gamma', tone: 'success' });
  const transfer = video.color_transfer ? fmt.transferName(video.color_transfer) : 'No HDR transfer signalled';
  return verdict({ ...base, value: 'SDR', detail: transfer, tone: 'neutral' });
}

function atmosVerdict(tracks) {
  const base = { label: 'Spatial audio', iconName: 'atmos' };
  const atmos = tracks.find((track) => track.atmos === true);
  if (atmos) {
    return verdict({ ...base, value: 'Dolby Atmos', detail: audioSummary(atmos), tone: 'success' });
  }
  const unconfirmed = tracks.find((track) => track.atmos == null && DOLBY_AUDIO.has(track.codec));
  if (unconfirmed) {
    return verdict({
      ...base,
      value: 'Unconfirmed',
      detail: `${audioSummary(unconfirmed)} · no JOC flag seen`,
      tone: 'unknown',
    });
  }
  const primary = tracks.find((track) => track.default) ?? tracks[0];
  return verdict({
    ...base,
    value: 'No Atmos',
    detail: primary ? audioSummary(primary) : 'No audio tracks',
    tone: 'neutral',
  });
}

function pictureVerdict(video) {
  return verdict({
    label: 'Picture',
    iconName: 'resolution',
    value: fmt.resolutionClass(video.height) ?? 'Unknown',
    detail: [fmt.resolution(video.width, video.height), fmt.fps(video.fps)].join(' · '),
  });
}

function audioSummary(track) {
  return [fmt.codecName(track.codec), fmt.channelsLabel(track.channels)].join(' ');
}

// ---------------------------------------------------------------------------
// Cards

function videoCard(video) {
  const fourcc = video.fourcc && !video.fourcc.startsWith('[') ? video.fourcc : null;
  return card({
    title: 'Video',
    iconName: 'video',
    className: 'span-video',
    body: [
      kvList([
        ['Codec', fmt.codecName(video.codec), { hint: video.profile }],
        ['FourCC', fourcc, { mono: true }],
        ['Resolution', fmt.resolution(video.width, video.height), { hint: fmt.resolutionClass(video.height) }],
        ['Frame rate', fmt.fps(video.fps)],
      ]),
      meter({
        label: 'Video bitrate',
        value: video.bit_rate_mbps,
        max: BITRATE_SCALE_MBPS,
        unit: 'Mbps',
        ticks: [0, 20, 40, 60, 80],
        caption: 'Scale tops out at 80 Mbps — where the compare score saturates.',
      }),
    ],
  });
}

function colorCard(video) {
  const dv = video.dolby_vision ?? {};
  return card({
    title: 'Colour & HDR',
    iconName: 'color',
    className: 'span-color',
    body: [
      kvList([
        ['Primaries', fmt.primariesName(video.color_primaries)],
        ['Transfer', fmt.transferName(video.color_transfer)],
        ['Matrix', fmt.matrixName(video.color_space)],
        ['Pixel format', fmt.pixelFormat(video.pix_fmt), { hint: video.pix_fmt }],
      ]),
      checklist([
        ['Mastering display (MDCV)', Boolean(video.hdr10?.mdcv)],
        ['Content light level (CLL)', Boolean(video.hdr10?.cll)],
        ['HDR10+ dynamic metadata', Boolean(video.hdr10_plus)],
        ['Dolby Vision RPU', dv.present ? Boolean(dv.rpu_present) : false],
      ]),
    ],
  });
}

function dolbyVisionCard(dv) {
  const layers = [
    ['Base layer', dv.bl_present],
    ['Enhancement layer', dv.el_present],
    ['RPU', dv.rpu_present],
  ];
  return card({
    title: 'Dolby Vision',
    iconName: 'dolbyVision',
    className: 'span-dv',
    body: [
      kvList([
        ['Profile', dv.profile, { hint: dv.profile_raw != null ? `raw ${dv.profile_raw}` : null }],
        [
          'Compatibility',
          dv.compatibility_name,
          { hint: dv.compatibility_id != null ? `id ${dv.compatibility_id}` : null },
        ],
        ['Level', dv.level],
        [
          'Layers',
          el(
            'span',
            { class: 'badge-row' },
            layers.map(([name, present]) =>
              badge(name, { tone: present ? 'success' : 'neutral', iconName: present ? 'check' : 'close' }),
            ),
          ),
          { wide: true },
        ],
      ]),
      DV_PROFILE_NOTES[dv.profile] && el('p', { class: 'card-note' }, DV_PROFILE_NOTES[dv.profile]),
    ],
  });
}

function containerCard(container) {
  return card({
    title: 'Container',
    iconName: 'container',
    className: 'span-container',
    body: kvList([
      ['Format', container.format_long_name ?? container.format_name, { hint: container.format_name, wide: true }],
      ['Duration', fmt.duration(container.duration_s)],
      ['Size', container.size_pretty],
      ['Overall bitrate', fmt.bitrate(container.overall_bit_rate_bps)],
    ]),
  });
}

function trackFlags(...flags) {
  const shown = flags.filter(Boolean);
  return shown.length ? el('span', { class: 'badge-row' }, shown) : fmt.DASH;
}

function audioCard(tracks) {
  return card({
    title: 'Audio',
    iconName: 'audio',
    meta: fmt.plural(tracks.length, 'track'),
    className: 'span-full',
    body: tracks.length
      ? dataTable({
          caption: 'Audio tracks',
          columns: [
            { label: '#', className: 'num col-index', value: (_, i) => i + 1 },
            {
              label: 'Codec',
              value: (t) => [fmt.codecName(t.codec), t.profile && el('span', { class: 'cell-hint' }, t.profile)],
            },
            {
              label: 'Channels',
              value: (t) => [
                t.channel_layout ? fmt.channelLayout(t.channel_layout) : fmt.channelsLabel(t.channels),
                t.channels && el('span', { class: 'cell-hint' }, fmt.plural(t.channels, 'channel')),
              ],
            },
            {
              label: 'Bitrate',
              className: 'num',
              value: (t) => (t.bit_rate_kbps ? `${t.bit_rate_kbps} kbps` : fmt.DASH),
            },
            { label: 'Sample rate', className: 'num', value: (t) => fmt.hertz(t.sample_rate_hz) },
            { label: 'Language', value: (t) => fmt.languageName(t.language) },
            { label: 'Title', className: 'col-title', value: (t) => t.title || fmt.DASH },
            {
              label: 'Flags',
              value: (t) =>
                trackFlags(
                  t.atmos === true && badge('Atmos', { tone: 'accent', iconName: 'atmos' }),
                  t.default && badge('Default'),
                ),
            },
          ],
          rows: tracks,
        })
      : emptyState({ iconName: 'audio', title: 'No audio tracks' }),
  });
}

function subtitlesCard(tracks) {
  return card({
    title: 'Subtitles',
    iconName: 'subtitles',
    meta: fmt.plural(tracks.length, 'track'),
    className: 'span-full',
    body: tracks.length
      ? dataTable({
          caption: 'Subtitle tracks',
          columns: [
            { label: '#', className: 'num col-index', value: (_, i) => i + 1 },
            { label: 'Format', value: (t) => fmt.codecName(t.codec) },
            { label: 'Language', value: (t) => fmt.languageName(t.language) },
            { label: 'Title', className: 'col-title', value: (t) => t.title || fmt.DASH },
            {
              label: 'Flags',
              value: (t) =>
                trackFlags(
                  t.default && badge('Default'),
                  t.forced && badge('Forced', { tone: 'info' }),
                  t.hearing_impaired && badge('SDH', { tone: 'info' }),
                ),
            },
          ],
          rows: tracks,
        })
      : emptyState({ iconName: 'subtitles', title: 'No subtitle tracks' }),
  });
}

// ---------------------------------------------------------------------------
// Clipboard

function summaryText(spec) {
  const { video: v, audio, container } = spec;
  const dv = v.dolby_vision ?? {};
  const lines = [
    `File: ${spec.filename}`,
    `Path: ${spec.path}`,
    `Size: ${container.size_pretty} | Duration: ${container.duration_pretty}`,
    `Video: ${v.codec} | ${v.resolution} | ${v.fps} fps | ${v.bit_rate_mbps ? `${v.bit_rate_mbps} Mbps` : 'n/a'}`,
    `Dolby Vision: ${dv.present ? `Yes (Profile ${dv.profile || dv.profile_raw}, ${dv.compatibility_name})` : 'No'}`,
    `HDR10+: ${v.hdr10_plus ? 'Yes' : 'No'} | HDR10: ${v.hdr10?.mdcv || v.hdr10?.cll ? 'Yes' : 'No'}`,
    `FourCC: ${v.fourcc || 'n/a'}`,
  ];
  if (audio.length) {
    lines.push(`Audio tracks (${audio.length}):`);
    audio.forEach((a, i) => {
      const kbps = a.bit_rate_kbps ? `${a.bit_rate_kbps} kbps` : '—';
      lines.push(
        `  [#${i}] ${a.codec || '—'} | ${a.channels}ch | ${kbps} | ${a.channel_layout || '—'}${a.atmos ? ' (Atmos)' : ''}`,
      );
    });
  }
  return lines.join('\n');
}
