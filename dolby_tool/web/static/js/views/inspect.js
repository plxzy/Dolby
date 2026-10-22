/** Inspect view: one file in, a verdict strip, spec cards and the raw JSON out. */

import { getJson, postJson } from '../api.js';
import { badge, verdict } from '../components/badge.js';
import { card, checklist, kvList } from '../components/card.js';
import { copyMenu } from '../components/copy.js';
import { jsonDrawer } from '../components/drawer.js';
import { blockedHere, bone, emptyState, markUnavailable, notice, toast, withCode } from '../components/feedback.js';
import { icon } from '../components/icon.js';
import { meter } from '../components/meter.js';
import { dataTable } from '../components/table.js';
import { $, el, replace } from '../dom.js';
import { bindDropzone, bindFileDrop } from '../dragdrop.js';
import * as fmt from '../format.js';
import { MODE } from '../mode.js';
import { hdrVerdict as hdrSummary } from '../hdr.js';

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
let browse;
let requestId = 0;
let fileInput;
let activeSession = null;
let latestReport = null;
let userPaused = false;
let pausedByHost = false;
let hostHidden = false; // the embedding page has collapsed the inspector
let renderTimer = null;

export function init({ capabilities }) {
  form = $('#inspect-form');
  input = $('#inspect-path');
  output = $('#inspect-output');
  browse = $('#inspect-browse');

  if (MODE === 'browser') {
    initBrowser();
    reset();
    return;
  }

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
      markUnavailable(browse, 'The native file picker needs macOS — paste a path or drop a file instead');
    }
  });
  reset();
}

function reset() {
  requestId += 1;
  activeSession?.stop();
  activeSession = null;
  latestReport = null;
  userPaused = false;
  pausedByHost = false;
  clearTimeout(renderTimer);
  renderTimer = null;
  if (fileInput) fileInput.value = '';
  input.value = '';
  form.dataset.state = 'empty';
  replace(output, intro());
}

async function pickFile() {
  if (blockedHere(browse)) return;
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
// Browser sessions: keep controls stable while the evidence dashboard changes.

function initBrowser() {
  input.hidden = true;
  form.querySelector('[type="submit"]').hidden = true;
  browse.textContent = 'Choose file…';
  form.querySelector('.dropzone-intro p').textContent =
    'Drop a video or choose one. It’s read on this device, never uploaded.';
  form.querySelector('.dropzone-formats').textContent = 'MP4 · M4V · MOV · MKV · WebM · TS';
  fileInput = el('input', {
    type: 'file',
    accept: 'video/*,.mkv,.mp4,.m4v,.mov,.ts,.m2ts,.webm',
    'aria-label': 'Choose a video to inspect',
    hidden: true,
  });
  form.append(fileInput);
  form.addEventListener('submit', (event) => event.preventDefault());
  browse.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    receiveFiles(Array.from(fileInput.files));
    fileInput.value = '';
  });
  $('#inspect-reset').addEventListener('click', reset);
  bindFileDrop(form, { onFiles: receiveFiles });
}

function receiveFiles(files) {
  if (files.length > 1) toast('Inspect reads one file at a time.', { tone: 'info' });
  if (files.length) inspectFile(files[0]);
}

async function inspectFile(file) {
  activeSession?.stop();
  activeSession = null;
  const id = ++requestId;
  latestReport = null;
  userPaused = false;
  pausedByHost = false;
  clearTimeout(renderTimer);
  renderTimer = null;
  form.dataset.state = 'result';
  const bar = sessionBar();
  const body = el('div', { class: 'session-result' }, loadingSkeleton());
  replace(output, bar.node, body);
  let lastRender = -Infinity;
  const render = () => {
    renderTimer = null;
    if (id !== requestId || !latestReport) return;
    replace(body, result(toSpec(latestReport)));
    lastRender = performance.now();
  };
  let toSpec;
  try {
    const [sessions, reports] = await Promise.all([import('../browser/session.js'), import('../browser/report.js')]);
    if (id !== requestId) return;
    toSpec = reports.toSpec;
    activeSession = sessions.startSession(file, {
      onReport(report) {
        if (id !== requestId) return;
        latestReport = report;
        bar.update(report);
        if (report.terminal || performance.now() - lastRender >= 250) {
          clearTimeout(renderTimer);
          render();
        } else if (!renderTimer) {
          renderTimer = setTimeout(render, 250 - (performance.now() - lastRender));
        }
      },
    });
    // The host may have hidden the embed while the session modules loaded.
    if (hostHidden) {
      pausedByHost = true;
      activeSession.pause();
    }
  } catch (error) {
    if (id !== requestId) return;
    bar.fail(error.message);
    replace(body, notice({ tone: 'danger', title: 'Couldn’t inspect this file', body: error.message }));
  }
}

function sessionBar() {
  const state = el('strong', { class: 'session-state', role: 'status' }, 'Running');
  const activity = el('p', { class: 'session-activity' }, 'Reading container structure');
  const metrics = el('p', { class: 'session-metrics tabular' }, 'Read locally: 0 MiB · Unique: 0% of file · 0s');
  const progress = el('progress', { 'aria-label': 'Inspecting video' });
  const pause = el('button', {
    class: 'btn', type: 'button',
    onclick: () => {
      if (!activeSession || latestReport?.terminal) return;
      pausedByHost = false;
      if (isRunning()) {
        userPaused = true;
        activeSession.pause();
      } else {
        userPaused = false;
        activeSession.resume();
      }
    },
  }, 'Pause');
  const deeper = el('button', {
    class: 'btn btn-primary', type: 'button', hidden: true,
    onclick: () => {
      userPaused = false;
      pausedByHost = false;
      activeSession?.deeper();
    },
  }, 'Continue deeper (reads up to 2 GB more)');
  const stop = el('button', { class: 'btn', type: 'button', onclick: () => activeSession?.stop() }, 'Stop');
  const node = el('section', { class: 'session-bar', 'aria-label': 'Inspection session' },
    state, activity, metrics, progress, el('div', { class: 'session-actions' }, pause, deeper, stop));
  return {
    node,
    update(report) {
      const terminal = Boolean(report.terminal);
      state.textContent = terminal ? 'Done' :
        report.state === 'paused-at-limit' ? 'Paused at limit' : report.state === 'paused' ? 'Paused' : 'Running';
      activity.textContent = report.activity;
      const m = report.metrics;
      const pct = report.file.size ? Math.min(100, m.uniqueBytes / report.file.size * 100) : 0;
      metrics.textContent = `Read locally: ${(m.bytesRequested / 1048576).toFixed(1)} MiB · Unique: ${pct.toFixed(1)}% of file · ${Math.floor(m.activeMs / 1000)}s`;
      pause.textContent = report.state === 'paused' ? 'Resume' : 'Pause';
      pause.hidden = terminal || report.state === 'paused-at-limit';
      deeper.hidden = terminal || report.state !== 'paused-at-limit';
      stop.hidden = terminal;
      progress.hidden = terminal || report.state !== 'running';
    },
    fail(message) {
      state.textContent = message;
      pause.hidden = deeper.hidden = stop.hidden = progress.hidden = true;
    },
  };
}

// Before the worker's first report a started session is running.
const isRunning = () => !latestReport || (!latestReport.terminal && latestReport.state === 'running');

export function pauseFromHost() {
  hostHidden = true;
  if (!activeSession || userPaused || !isRunning()) return;
  pausedByHost = true;
  activeSession.pause();
}

export function resumeFromHost() {
  hostHidden = false;
  if (!activeSession || !pausedByHost || userPaused) return;
  pausedByHost = false;
  if (!latestReport || latestReport.state === 'paused' || latestReport.state === 'running') activeSession.resume();
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
      colorCard(spec.video, spec.evidence),
      dv.present && dolbyVisionCard(dv),
      containerCard(spec.container),
      audioCard(spec.audio),
      subtitlesCard(spec.subtitles),
      spec.evidence && evidenceCard(spec),
    ),
    (!spec.session || spec.session.terminal) &&
      jsonDrawer(spec, { label: 'Raw JSON', meta: spec.session ? 'The full spec and analysis evidence' : 'The full spec, as returned by /api/inspect' }),
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
  if (spec.evidence) {
    const findings = spec.evidence;
    const hdrFields = ['video.hdr10_plus', 'video.hdr10.mdcv', 'video.hdr10.cll'].map((id) => findings[id]).filter(Boolean);
    const hdr = strongestFinding(hdrFields);
    const atmos = strongestFinding(Object.entries(findings).filter(([id]) => /^audio\..+\.atmos$/.test(id)).map(([, f]) => f));
    return [
      evidenceVerdict(findings['video.dolby_vision'], dolbyVisionVerdict(video),
        { label: 'Dolby Vision', iconName: 'dolbyVision' }, spec.session.activity),
      fourccVerdict(video),
      evidenceVerdict(hdr, hdrVerdict(video), { label: 'HDR', iconName: 'hdr' }, spec.session.activity),
      !atmos && !audio.length && spec.session.terminal ? atmosVerdict(audio) :
        evidenceVerdict(atmos, atmosVerdict(audio), { label: 'Spatial audio', iconName: 'atmos' }, spec.session.activity),
      pictureVerdict(video),
    ];
  }
  return [
    dolbyVisionVerdict(video),
    fourccVerdict(video),
    hdrVerdict(video),
    atmosVerdict(audio),
    pictureVerdict(video),
  ];
}

function strongestFinding(findings) {
  const order = ['conflicting', 'observed', 'signaled', 'checking', 'not_found_in_scope', 'unknown', 'unsupported', 'absent'];
  return order.map((state) => findings.find((finding) => finding.state === state)).find(Boolean);
}

function evidenceVerdict(finding, legacy, base, activity) {
  if (!finding) return verdict({ ...base, value: 'Checking…', detail: activity, tone: 'unknown' });
  if (finding.state === 'signaled' || finding.state === 'observed') {
    const detail = legacy.querySelector('.verdict-detail');
    const suffix = finding.state === 'signaled' ? ' · signalled' : ' · seen in stream';
    if (detail) detail.textContent += suffix;
    return legacy;
  }
  if (finding.state === 'absent') return legacy;
  const coverage = finding.evidence?.map((entry) => entry.coverage).filter(Boolean).at(-1);
  const descriptions = {
    checking: ['Checking…', activity],
    not_found_in_scope: ['Not seen', coverage ? `in ${coverage}` : finding.summary],
    conflicting: ['Conflicting', finding.evidence?.map((entry) => entry.note).filter(Boolean).slice(0, 2).join(' · ') || finding.summary],
    unsupported: ['Can’t check here', finding.limitations?.join(' · ') || finding.summary],
    unknown: ['Unknown', finding.summary],
  };
  const [value, detail] = descriptions[finding.state] ?? descriptions.unknown;
  return verdict({ ...base, value, detail, tone: finding.state === 'conflicting' ? 'warn' : 'unknown' });
}

function evidenceCard(spec) {
  const fieldName = (id) => ({
    'video.dolby_vision': 'Dolby Vision',
    'video.hdr10.mdcv': 'Mastering display (MDCV)',
    'video.hdr10.cll': 'Content light level (CLL)',
    'video.hdr10_plus': 'HDR10+',
    'video.transfer': 'Transfer',
  })[id] ?? id.replace(/^audio\.(.+)\.atmos$/, 'Audio $1 · Atmos');
  return card({
    title: 'Evidence', iconName: 'code', className: 'span-full',
    body: dataTable({
      caption: 'Analysis evidence', stack: true,
      columns: [
        { label: 'Field', value: ([id]) => fieldName(id) },
        { label: 'State', value: ([, f]) => [f.state, el('span', { class: 'cell-hint' }, f.summary)] },
        { label: 'Source', value: ([, f]) => [...new Set(f.evidence.map((entry) => entry.source).filter(Boolean))].join(' · ') || fmt.DASH },
        { label: 'Coverage', value: ([, f]) => [...new Set(f.evidence.map((entry) => entry.coverage).filter(Boolean))].join(' · ') || fmt.DASH },
        { label: 'Next', value: ([, f]) => [f.next || fmt.DASH, f.limitations.length > 0 && el('span', { class: 'cell-hint' }, f.limitations.join(' · '))] },
      ],
      rows: Object.entries(spec.evidence),
    }),
  });
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
  return verdict({ label: 'HDR', iconName: 'hdr', ...hdrSummary(video, { mode: MODE, transferName: fmt.transferName }) });
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
      detail: `${audioSummary(unconfirmed)} · no conclusive Atmos signal`,
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
  return [fmt.codecName(track.codec), track.codec === 'ac4' && track.channel_layout ? fmt.channelLayout(track.channel_layout) : fmt.channelsLabel(track.channels)].join(' ');
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

function colorCard(video, evidence) {
  const dv = video.dolby_vision ?? {};
  const rpu = evidence && dv.present && dv.rpu_present == null ? null : dv.present ? Boolean(dv.rpu_present) : false;
  const item = (label, field, nativeValue) => {
    if (!evidence) return [label, nativeValue];
    const finding = evidence[field];
    const present = ['signaled', 'observed'].includes(finding?.state) ? nativeValue :
      finding?.state === 'absent' ? false : null;
    return [label, present, finding?.summary];
  };
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
        item('Mastering display (MDCV)', 'video.hdr10.mdcv', Boolean(video.hdr10?.mdcv)),
        item('Content light level (CLL)', 'video.hdr10.cll', Boolean(video.hdr10?.cll)),
        item('HDR10+ dynamic metadata', 'video.hdr10_plus', Boolean(video.hdr10_plus)),
        item('Dolby Vision RPU', 'video.dolby_vision', rpu),
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
          stack: true,
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
          stack: true,
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
  if (spec.evidence) {
    return [
      `File: ${spec.filename}`,
      `Size: ${spec.container.size_pretty} | Duration: ${spec.container.duration_pretty}`,
      `Video: ${spec.video.codec || '—'} | ${spec.video.resolution || '—'} | ${spec.video.fps ?? '—'} fps`,
      ...Object.entries(spec.evidence).map(([id, finding]) => `${id}: ${finding.state} · ${finding.summary}`),
    ].join('\n');
  }
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
