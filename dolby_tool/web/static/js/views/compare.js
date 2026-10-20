/** Compare view: rank several releases of a title by what Apple devices will actually play. */

import { getJson, postJson } from '../api.js';
import { badge } from '../components/badge.js';
import { card } from '../components/card.js';
import { drawer } from '../components/drawer.js';
import { bone, emptyState, notice, toast, withCode } from '../components/feedback.js';
import { icon } from '../components/icon.js';
import { scoreBar, scoreLegend } from '../components/scorebar.js';
import { dataTable } from '../components/table.js';
import { $, el, replace } from '../dom.js';
import { bindDropzone } from '../dragdrop.js';
import * as fmt from '../format.js';

const WEIGHTS_KEY = 'dolby-tool.compare-weights';
const WEIGHT_MAX = 50;

/**
 * Score components in the backend's fixed order. Colour follows the component,
 * never its rank; `standard` mirrors analysis.compare.WEIGHTS.
 */
const COMPONENTS = [
  { key: 'dv_present', label: 'Dolby Vision', standard: 25 },
  { key: 'dv_fourcc_ok', label: 'DV FourCC', standard: 25 },
  { key: 'hdr10_plus', label: 'HDR10+', standard: 10 },
  { key: 'atmos', label: 'Atmos', standard: 15 },
  { key: 'bit_rate_mbps', label: 'Video bitrate', standard: 20 },
  { key: 'resolution', label: 'Resolution', standard: 10 },
  { key: 'subtitles', label: 'Subtitles', standard: 5 },
].map((component, index) => ({ ...component, color: `var(--series-${index + 1})` }));

const shownFourcc = (fourcc) => (fourcc && !fourcc.startsWith('[') ? fourcc : null);
const mark = (value) => (value === true ? 'Yes' : value === false ? 'No' : 'Unknown');

/** Rows of the side-by-side table; `winner` names the key in the response's `winners`. */
const DIMENSIONS = [
  { label: 'Resolution', value: (r) => r.resolution },
  { label: 'Frame rate', value: (r) => fmt.fps(r.fps) },
  { label: 'Video codec', value: (r) => fmt.codecName(r.video_codec) },
  { label: 'FourCC', winner: 'fourcc_ok', mono: true, value: (r) => shownFourcc(r.fourcc) ?? 'none' },
  {
    label: 'Video bitrate',
    winner: 'video_bitrate_mbps',
    value: (r) => (r.video_bitrate_mbps ? `${fmt.num(r.video_bitrate_mbps)} Mbps` : null),
  },
  {
    label: 'Dolby Vision',
    winner: 'dv_profile',
    value: (r) => (r.dv_profile ? `Profile ${r.dv_profile}${r.dv_compat ? ` · ${r.dv_compat}` : ''}` : 'No'),
  },
  { label: 'HDR10', value: (r) => (r.hdr10 ? 'Yes' : 'No') },
  { label: 'HDR10+', winner: 'hdr10_plus', value: (r) => mark(r.hdr10_plus) },
  { label: 'Audio codec', value: (r) => fmt.codecName(r.audio_codec) },
  {
    label: 'Channels',
    winner: 'audio_channels',
    value: (r) => (r.audio_channels ? fmt.channelsLabel(r.audio_channels) : null),
  },
  {
    label: 'Audio bitrate',
    winner: 'audio_bitrate_kbps',
    value: (r) => (r.audio_bitrate_kbps ? `${r.audio_bitrate_kbps} kbps` : null),
  },
  { label: 'Atmos', winner: 'audio_atmos', value: (r) => mark(r.audio_atmos) },
  { label: 'Subtitles', winner: 'subtitle_count', value: (r) => r.subtitle_count },
  { label: 'Size', value: (r) => r.size },
  { label: 'Duration', value: (r) => r.duration },
];

const files = new Map(); // path -> size in bytes (null when unknown)
let weights;
let ranWith = null; // serialised weights behind the results on screen
let requestId = 0;
let ui;

export function init({ capabilities }) {
  weights = loadWeights();
  ui = {
    list: $('#compare-list'),
    count: $('#compare-count'),
    go: $('#compare-go'),
    goLabel: $('#compare-go-label'),
    clear: $('#compare-clear'),
    add: $('#compare-add'),
    folder: $('#compare-folder'),
    output: $('#compare-output'),
    weights: $('#weights-list'),
    max: $('#weights-max'),
  };
  ui.go.addEventListener('click', run);
  ui.clear.addEventListener('click', clearAll);
  ui.add.addEventListener('click', pickFiles);
  $('#compare-folder-form').addEventListener('submit', (event) => {
    event.preventDefault();
    addFolder();
  });
  $('#weights-reset').addEventListener('click', () => {
    weights = standardWeights();
    saveWeights();
    renderWeights();
  });
  bindDropzone($('#compare-drop'), {
    onPaths: (paths) => addPaths(paths.map((path) => [path, null])),
    onProblem: (message) => toast(message, { tone: 'warn' }),
  });
  capabilities.then((caps) => {
    if (caps && !caps.tools.osascript) {
      ui.add.disabled = true;
      ui.add.title = 'The native file picker needs macOS — drop files or add a folder instead';
    }
  });
  renderWeights();
  renderFiles();
}

// ---------------------------------------------------------------------------
// Files

function addPaths(entries) {
  for (const [path, size] of entries) files.set(path, size ?? files.get(path) ?? null);
  renderFiles();
}

async function pickFiles() {
  try {
    const { paths } = await getJson('/api/pick', { multi: true });
    addPaths(paths.map((path) => [path, null]));
  } catch (error) {
    toast(error.message, { tone: 'danger' });
  }
}

async function addFolder() {
  const path = ui.folder.value.trim();
  if (!path) {
    ui.folder.focus();
    return;
  }
  try {
    const listing = await getJson('/api/list', { path });
    if (!listing.files.length) {
      toast(`No videos found in ${listing.path}`, { tone: 'warn' });
      return;
    }
    addPaths(listing.files.map((file) => [file.path, file.size]));
    ui.folder.value = '';
    toast(`Added ${fmt.plural(listing.files.length, 'video')} from ${listing.path}`, { tone: 'success' });
  } catch (error) {
    toast(error.message, { tone: 'danger' });
  }
}

function clearAll() {
  requestId += 1;
  files.clear();
  ranWith = null;
  ui.output.removeAttribute('aria-busy');
  replace(ui.output);
  renderFiles();
}

function renderFiles() {
  const entries = [...files];
  ui.count.textContent = fmt.plural(entries.length, 'file');
  ui.clear.disabled = !entries.length && ranWith == null;
  replace(
    ui.list,
    entries.length
      ? entries.map(([path, size]) => fileRow(path, size))
      : el(
          'li',
          { class: 'file-empty' },
          emptyState({
            iconName: 'drop',
            title: 'Drop releases here',
            body: 'Add two or more versions of the same title — files from Finder, the file picker, or every video in a folder.',
          }),
        ),
  );
  updateGoButton();
}

function fileRow(path, size) {
  const cut = path.lastIndexOf('/') + 1;
  const name = path.slice(cut) || path;
  return el(
    'li',
    { class: 'file-row' },
    el('span', { class: 'file-icon' }, icon('file', { size: 18 })),
    el(
      'span',
      { class: 'file-text' },
      el('span', { class: 'file-name' }, name),
      el('span', { class: 'file-dir', title: path }, path.slice(0, cut)),
    ),
    size != null && el('span', { class: 'file-size' }, fmt.bytes(size)),
    el(
      'button',
      {
        class: 'btn btn-ghost btn-sm btn-icon',
        type: 'button',
        'aria-label': `Remove ${name}`,
        onclick: () => {
          files.delete(path);
          renderFiles();
        },
      },
      icon('close', { size: 15 }),
    ),
  );
}

// ---------------------------------------------------------------------------
// Weights

function standardWeights() {
  return Object.fromEntries(COMPONENTS.map((c) => [c.key, c.standard]));
}

function loadWeights() {
  const standard = standardWeights();
  try {
    const saved = JSON.parse(localStorage.getItem(WEIGHTS_KEY) ?? '{}');
    for (const key of Object.keys(standard)) {
      if (Number.isFinite(saved[key])) standard[key] = saved[key];
    }
  } catch {
    // Unreadable or blocked storage: start from the standard weights.
  }
  return standard;
}

function saveWeights() {
  try {
    localStorage.setItem(WEIGHTS_KEY, JSON.stringify(weights));
  } catch {
    // Weights still apply to this page; they just won't be remembered.
  }
}

function renderWeights() {
  replace(ui.weights, COMPONENTS.map(weightRow));
  updateGoButton();
}

function weightRow(component) {
  const id = `weight-${component.key}`;
  const range = el('input', {
    type: 'range',
    min: 0,
    max: WEIGHT_MAX,
    step: 1,
    class: 'weight-range',
    'aria-label': `${component.label} weight`,
    style: { '--seg': component.color },
  });
  const number = el('input', { type: 'number', min: 0, max: WEIGHT_MAX, step: 1, id, class: 'input weight-number' });
  range.value = number.value = weights[component.key];
  const commit = (source) => {
    const value = Math.min(Math.max(Math.round(Number(source.value) || 0), 0), WEIGHT_MAX);
    weights[component.key] = value;
    range.value = value;
    if (source !== number) number.value = value;
    range.style.setProperty('--fill', `${(value / WEIGHT_MAX) * 100}%`);
    saveWeights();
    updateGoButton();
  };
  range.addEventListener('input', () => commit(range));
  number.addEventListener('input', () => commit(number));
  number.addEventListener('change', () => (number.value = weights[component.key]));
  range.style.setProperty('--fill', `${(weights[component.key] / WEIGHT_MAX) * 100}%`);
  return el(
    'li',
    { class: 'weight' },
    el('span', { class: 'legend-swatch', style: { '--seg': component.color } }),
    el('label', { for: id, class: 'weight-label' }, component.label),
    range,
    number,
  );
}

function updateGoButton() {
  const max = Object.values(weights).reduce((sum, value) => sum + value, 0);
  ui.max.textContent = fmt.num(max);
  const stale = ranWith != null && ranWith !== JSON.stringify(weights);
  ui.goLabel.textContent = stale ? 'Update scores' : 'Compare';
  ui.go.dataset.stale = String(stale);
  ui.go.disabled = !files.size || ui.output.hasAttribute('aria-busy');
}

// ---------------------------------------------------------------------------
// Run

async function run() {
  if (!files.size) return;
  const body = { paths: [...files.keys()], weights: { ...weights } };
  const id = ++requestId;
  const refreshing = ranWith != null;
  ui.output.setAttribute('aria-busy', 'true');
  if (!refreshing) replace(ui.output, loadingSkeleton());
  updateGoButton();
  try {
    const response = await postJson('/api/compare', body);
    if (id !== requestId) return;
    ranWith = JSON.stringify(body.weights);
    replace(ui.output, results(response));
  } catch (error) {
    if (id !== requestId) return;
    ranWith = null;
    replace(ui.output, notice({ tone: 'danger', title: 'Comparison failed', body: error.message }));
  } finally {
    if (id === requestId) {
      ui.output.removeAttribute('aria-busy');
      renderFiles();
    }
  }
}

function loadingSkeleton() {
  return el(
    'ol',
    { class: 'ranking', 'aria-label': 'Comparing…' },
    Array.from({ length: Math.min(files.size, 3) }, () =>
      el(
        'li',
        { class: 'rank-card card', 'aria-hidden': 'true' },
        bone('40px', '40px', 'bone-round'),
        el(
          'div',
          { class: 'rank-main skeleton-stack' },
          bone('40%', '16px'),
          bone('60%', '10px'),
          bone('100%', '14px'),
        ),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Results

function results(response) {
  const { rows, scores, errors } = response;
  // Each component's ceiling is its weight, so the effective maximum is the sum of the weights used.
  const max = COMPONENTS.reduce((sum, c) => sum + (response.weights?.[c.key] ?? 0), 0);
  const order = scores.map((_, index) => index).sort((a, b) => scores[b].total - scores[a].total || a - b);
  const totals = order.map((index) => scores[index].total);
  const ranks = totals.map((total) => totals.indexOf(total) + 1);
  const tiedBest = scores.filter((score) => score.is_best).length > 1;

  return el(
    'div',
    { class: 'compare-results' },
    errors.map((error) =>
      notice({ tone: 'danger', title: `Couldn’t inspect ${error.path.split('/').pop()}`, body: error.error }),
    ),
    rows.length > 0 && [
      el(
        'section',
        { class: 'ranking-section', 'aria-labelledby': 'ranking-title' },
        el(
          'header',
          { class: 'section-header' },
          el('h2', { id: 'ranking-title' }, 'Ranking'),
          scoreLegend(COMPONENTS),
        ),
        el(
          'ol',
          { class: 'ranking' },
          order.map((index, position) =>
            rankCard({
              row: rows[index],
              score: scores[index],
              weightsUsed: response.weights,
              rank: ranks[position],
              max,
              tiedBest,
            }),
          ),
        ),
      ),
      dimensionsCard(response, order, ranks),
      scoreTable(response, order, max),
    ],
  );
}

function rankCard({ row, score, weightsUsed, rank, max, tiedBest }) {
  const parts = COMPONENTS.map((c) => ({ ...c, value: score.components[c.key] ?? 0, weight: weightsUsed[c.key] ?? 0 }));
  const facts = [
    row.resolution,
    fmt.codecName(row.video_codec),
    shownFourcc(row.fourcc),
    row.dv_profile && `DV ${row.dv_profile}`,
    row.audio_codec && `${fmt.codecName(row.audio_codec)} ${fmt.channelsLabel(row.audio_channels)}`,
    row.size,
  ].filter(Boolean);
  return el(
    'li',
    { class: 'rank-card card', dataset: { best: String(score.is_best) } },
    el('span', { class: 'rank-number' }, el('span', { class: 'visually-hidden' }, 'Rank '), rank),
    el(
      'div',
      { class: 'rank-main' },
      el(
        'div',
        { class: 'rank-head' },
        el(
          'div',
          { class: 'rank-title' },
          el('h3', { title: row.path }, row.filename),
          score.is_best && badge(tiedBest ? 'Tied best' : 'Best pick', { tone: 'success', iconName: 'award' }),
        ),
        el(
          'p',
          { class: 'rank-score' },
          el('strong', {}, fmt.num(score.total, 1)),
          el('span', {}, ` / ${fmt.num(max)}`),
        ),
      ),
      el('p', { class: 'rank-facts' }, facts.join(' · ')),
      scoreBar({ parts, total: score.total, max, label: `${row.filename} score` }),
      row.warnings.map((warning) =>
        el('p', { class: 'rank-warning' }, icon('alert', { size: 14 }), el('span', {}, withCode(warning))),
      ),
    ),
  );
}

function dimensionsCard({ rows, winners }, order, ranks) {
  const cell = (dimension, index) => {
    const best = dimension.winner ? (winners[dimension.winner] ?? []) : [];
    // A row where every file ties has no winner worth marking.
    const isWinner = best.includes(index) && best.length < rows.length;
    const value = dimension.value(rows[index]);
    return el(
      'td',
      { class: [dimension.mono && 'mono', isWinner && 'is-winner'] },
      isWinner && icon('check', { size: 14, className: 'winner-mark' }),
      value === '' || value == null ? fmt.DASH : value,
      isWinner && el('span', { class: 'visually-hidden' }, ' (best)'),
    );
  };
  return card({
    title: 'Side by side',
    iconName: 'compare',
    meta: el('span', { class: 'winner-key' }, icon('check', { size: 13 }), 'best in row'),
    className: 'dimensions-card',
    body: el(
      'div',
      { class: 'table-wrap', tabindex: 0, role: 'region', 'aria-label': 'Side-by-side comparison' },
      el(
        'table',
        { class: 'table dimension-table' },
        el('caption', { class: 'visually-hidden' }, 'Each file’s value per dimension; a check marks the best'),
        el(
          'thead',
          {},
          el(
            'tr',
            {},
            el('th', { scope: 'col' }, el('span', { class: 'visually-hidden' }, 'Dimension')),
            order.map((index, position) =>
              el(
                'th',
                { scope: 'col', title: rows[index].path },
                el('span', { class: 'col-rank' }, `#${ranks[position]}`),
                rows[index].filename,
              ),
            ),
          ),
        ),
        el(
          'tbody',
          {},
          DIMENSIONS.map((dimension) =>
            el(
              'tr',
              {},
              el('th', { scope: 'row' }, dimension.label),
              order.map((index) => cell(dimension, index)),
            ),
          ),
        ),
      ),
    ),
  });
}

/** The score bars' table twin: every component as a number. */
function scoreTable({ rows, scores }, order, max) {
  return drawer({
    iconName: 'compare',
    label: 'Score table',
    meta: 'Every component as numbers',
    content: () =>
      dataTable({
        caption: 'Score components per file',
        className: 'score-table',
        columns: [
          { label: 'File', className: 'col-file', value: (index) => rows[index].filename },
          ...COMPONENTS.map((c) => ({
            label: c.label,
            className: 'num',
            value: (index) => fmt.num(scores[index].components[c.key] ?? 0, 1),
          })),
          {
            label: 'Total',
            className: 'num col-total',
            value: (index) => `${fmt.num(scores[index].total, 1)} / ${fmt.num(max)}`,
          },
        ],
        rows: order,
      }),
  });
}
