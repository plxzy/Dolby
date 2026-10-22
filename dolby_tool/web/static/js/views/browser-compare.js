/** Browser Compare shares native setup/weights, but never uses the backend. */
import { badge } from '../components/badge.js';
import { card } from '../components/card.js';
import { drawer } from '../components/drawer.js';
import { emptyState, notice } from '../components/feedback.js';
import { icon } from '../components/icon.js';
import { scoreLegend } from '../components/scorebar.js';
import { dataTable } from '../components/table.js';
import { el, replace } from '../dom.js';
import { bindFileDrop } from '../dragdrop.js';
import * as fmt from '../format.js';
import { scoreSpec, rankScores } from '../browser/compare.js';
import { CompareSessions } from '../browser/compare-sessions.js';
import { toSpec } from '../browser/report.js';

const rangeText = part => part.min === part.max ? fmt.num(part.min, 1) : `${fmt.num(part.min, 1)}–${fmt.num(part.max, 1)}`;
const evidenceText = finding => ({ signaled: 'Yes · signaled', observed: 'Yes · observed', absent: 'No · confirmed', checking: 'Checking', not_found_in_scope: 'Not seen in checked scope', unsupported: 'Can’t check here', unknown: 'Unknown', conflicting: 'Conflicting evidence' })[finding?.state] || 'Checking';
const emptyReport = file => ({ file: { name: file.name, size: file.size }, container: {}, tracks: [], findings: {}, structureComplete: false, state: 'queued', terminal: null });

export function createBrowserCompare({ ui, components, getWeights }) {
  let started = false, renderTimer = null;
  const nodes = new Map();
  const queue = new CompareSessions({ onChange: item => {
    if (item) updateFile(item);
    if (!renderTimer) renderTimer = setTimeout(() => { renderTimer = null; renderResults(); }, 250);
  } });
  const fileInput = el('input', { type: 'file', multiple: true, accept: 'video/*,.mkv,.mp4,.m4v,.mov,.ts,.m2ts,.webm', hidden: true, 'aria-label': 'Choose videos to compare' });
  ui.add.after(fileInput);
  document.getElementById('compare-folder-form').remove();
  document.querySelector('[data-view="compare"] .view-heading p').textContent = 'Compare local releases on this device. Nothing is uploaded.';
  document.getElementById('compare-drop').append(el('p', { class: 'compare-local-note' }, 'One file scans at a time to bound memory and CPU. A paused file holds the queue: continue deeper or stop it to move on.'));
  ui.add.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => { addFiles([...fileInput.files]); fileInput.value = ''; });
  bindFileDrop(document.getElementById('compare-drop'), { onFiles: addFiles });
  ui.go.addEventListener('click', () => {
    if (queue.items.length < 2) return;
    started = true;
    queue.run();
    refresh();
  });
  ui.clear.addEventListener('click', () => {
    queue.clear(); started = false;
    clearTimeout(renderTimer); renderTimer = null;
    replace(ui.output); renderFiles(); refresh();
  });
  window.addEventListener('pagehide', event => { if (!event.persisted) { queue.clear(); clearTimeout(renderTimer); } });

  function addFiles(files) {
    for (const file of files) {
      if (queue.items.some(item => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified)) continue;
      queue.add(file);
    }
    renderFiles();
    if (started) queue.run();
    refresh();
  }
  function renderFiles() {
    const live = new Set(queue.items.map(item => item.id));
    for (const [id, node] of nodes) if (!live.has(id)) { node.row.remove(); nodes.delete(id); }
    ui.list.querySelector('.file-empty')?.remove();
    for (const item of queue.items) {
      if (!nodes.has(item.id)) {
        const status = el('p', { class: 'session-activity', role: 'status' });
        const metrics = el('p', { class: 'session-metrics tabular' });
        const pause = el('button', { type: 'button', class: 'btn btn-sm', onclick: () => queue.command(item, item.report?.state === 'paused' ? 'resume' : 'pause') }, 'Pause');
        const deeper = el('button', { type: 'button', class: 'btn btn-primary btn-sm', onclick: () => queue.command(item, 'deeper') }, 'Continue deeper (+2 GB)');
        const stop = el('button', { type: 'button', class: 'btn btn-sm', onclick: () => queue.command(item, 'stop') }, 'Stop');
        const row = el('li', { class: 'compare-session', dataset: { file: item.file.name } },
          el('div', { class: 'file-row' }, el('span', { class: 'file-icon' }, icon('file', { size: 18 })),
            el('span', { class: 'file-text' }, el('span', { class: 'file-name', title: item.file.name }, item.file.name)),
            el('span', { class: 'file-size' }, fmt.bytes(item.file.size)),
            el('button', { type: 'button', class: 'btn btn-ghost btn-sm btn-icon', 'aria-label': `Remove ${item.file.name}`, onclick: () => { queue.remove(item); renderFiles(); refresh(); } }, icon('close', { size: 15 }))),
          el('div', { class: 'compare-session-detail' }, status, metrics, el('div', { class: 'session-actions' }, pause, deeper, stop)));
        nodes.set(item.id, { row, status, metrics, pause, deeper, stop });
        ui.list.append(row);
      }
      updateFile(item);
    }
    if (!queue.items.length) ui.list.append(el('li', { class: 'file-empty' }, emptyState({ iconName: 'drop', title: 'Drop releases here', body: 'Choose or drop two or more local video files. They never leave your device.' })));
    ui.count.textContent = fmt.plural(queue.items.length, 'file');
  }
  function updateFile(item) {
    const node = nodes.get(item.id);
    if (!node) return;
    const r = item.report, terminal = Boolean(r?.terminal), active = queue.active === item;
    const state = terminal ? r.terminal.reason === 'stopped' ? 'Stopped' : 'Done' : r?.state === 'paused-at-limit' ? 'Paused at limit' : r?.state === 'paused' ? 'Paused' : active ? 'Running' : item.requested ? 'Queued' : 'Ready';
    node.status.textContent = `${state}${r?.activity ? ` · ${r.activity}` : ''}`;
    const m = r?.metrics;
    node.metrics.textContent = m ? `Read locally: ${(m.bytesRequested / 1048576).toFixed(1)} MiB · Unique: ${(item.file.size ? m.uniqueBytes / item.file.size * 100 : 0).toFixed(1)}% · ${(m.activeMs / 1000).toFixed(1)}s active` : '';
    node.pause.hidden = node.stop.hidden = !active || terminal;
    node.pause.hidden ||= r?.state === 'paused-at-limit';
    node.deeper.hidden = !active || terminal || r?.state !== 'paused-at-limit';
    node.pause.textContent = r?.state === 'paused' ? 'Resume' : 'Pause';
    node.pause.disabled = node.deeper.disabled = queue.hostHidden;
  }
  function refresh() {
    ui.go.disabled = queue.items.length < 2;
    ui.goLabel.textContent = started ? 'Update scores' : 'Compare';
    ui.go.dataset.stale = 'false';
    ui.clear.disabled = !queue.items.length;
    for (const item of queue.items) updateFile(item);
    if (started) renderResults();
  }
  function renderResults() {
    if (!started || !queue.items.length) { replace(ui.output); return; }
    if (queue.items.length < 2) {
      replace(ui.output, notice({ tone: 'info', title: 'Add another file', body: 'Compare needs two or more local files. The remaining scan keeps its evidence.' }));
      return;
    }
    const previousDrawer = ui.output.querySelector('details');
    const drawerOpen = previousDrawer?.open;
    const drawerFocused = previousDrawer?.querySelector('summary') === document.activeElement;
    const tableScroll = ui.output.querySelector('.table-wrap')?.scrollLeft || 0;
    const weights = getWeights();
    const reports = queue.items.map(item => ({ ...emptyReport(item.file), ...item.report }));
    const specs = reports.map(toSpec), scores = specs.map((spec, i) => scoreSpec(spec, weights, { tracksComplete: reports[i].structureComplete === true }));
    const ranking = rankScores(scores, { scanning: queue.items.some(item => !item.report?.terminal) });
    const cards = ranking.order.map(index => {
      const item = queue.items[index], score = scores[index], spec = specs[index];
      const winner = ranking.winners.includes(index), overlap = ranking.overlaps[index].length > 0;
      return el('li', { class: 'rank-card card', dataset: { best: String(winner) } },
        el('span', { class: 'rank-number', 'aria-label': overlap && !winner ? 'Undecided rank' : `Rank ${ranking.ranks[index]}` }, overlap && !winner ? '≈' : ranking.ranks[index]),
        el('div', { class: 'rank-main' },
          el('div', { class: 'rank-head' }, el('div', { class: 'rank-title' }, el('h3', { title: item.file.name }, item.file.name),
            winner ? badge(ranking.winners.length > 1 ? 'Tied best' : 'Best pick', { tone: 'success' }) : overlap ? badge('Overlapping · undecided', { tone: 'warn' }) : null),
            el('p', { class: 'rank-score' }, el('strong', {}, rangeText(score)), el('span', {}, ` / ${fmt.num(score.ceiling)}`))),
          el('p', { class: 'rank-facts' }, [spec.video.resolution, fmt.codecName(spec.video.codec), fmt.bytes(item.file.size)].filter(Boolean).join(' · ')),
          el('div', { class: 'compare-range-bar', role: 'img', 'aria-label': `Score ${rangeText(score)} of ${score.ceiling}. Solid: confirmed points; striped: unresolved points.` },
            components.map(c => { const p = score.components[c.key]; return el('span', { class: 'compare-range-component', title: `${c.label}: ${rangeText(p)}`, style: { '--seg': c.color, '--known': `${score.ceiling ? p.min / score.ceiling * 100 : 0}%`, '--pending': `${score.ceiling ? (p.max - p.min) / score.ceiling * 100 : 0}%` } }, el('span', { class: 'compare-range-known' }), el('span', { class: 'compare-range-pending' })); })),
          spec.video.fourcc_warning && el('p', { class: 'rank-warning' }, spec.video.fourcc_warning)));
    });
    const audio = specs.map(spec => spec.audio.find(t => t.atmos === true) || spec.audio[0]);
    const best = values => {
      if (values.some(v => v == null)) return [];
      const top = Math.max(...values);
      const indexes = values.flatMap((v, i) => v && v === top ? [i] : []);
      return indexes.length < values.length ? indexes : [];
    };
    const flagValues = {
      dv_present: specs.map(s => s.video.dolby_vision.present),
      dv_fourcc_ok: reports.map((r, i) => r.structureComplete ? ['dvh1', 'dvhe', 'dvav', 'dva1'].includes(specs[i].video.fourcc) : null),
      hdr10_plus: specs.map(s => s.video.hdr10_plus),
      atmos: specs.map((s, i) => s.audio.some(t => t.atmos === true) ? true : reports[i].structureComplete && s.audio.every(t => t.atmos === false) ? false : null),
    };
    const flags = key => best(flagValues[key].map(value => value == null ? null : Number(value)));
    const hdrText = r => {
      const mdcv = r.findings['video.hdr10.mdcv'], cll = r.findings['video.hdr10.cll'];
      return evidenceText([mdcv, cll].find(f => ['observed', 'signaled'].includes(f?.state)) ||
        (mdcv?.state === 'absent' && cll?.state === 'absent' ? { state: 'absent' } : mdcv));
    };
    const dimensionRows = [
      ['Resolution', specs.map(s => s.video.resolution || 'Unknown')],
      ['Frame rate', specs.map(s => fmt.fps(s.video.fps))],
      ['Video codec', specs.map(s => fmt.codecName(s.video.codec))],
      ['Video bitrate', specs.map(s => s.video.bit_rate_mbps == null ? 'Unknown' : `${fmt.num(s.video.bit_rate_mbps)} Mbps`), best(specs.map(s => s.video.bit_rate_mbps))],
      ['FourCC', reports.map((r, i) => specs[i].video.fourcc || (r.structureComplete ? 'none' : 'Unknown')), flags('dv_fourcc_ok')],
      ['Dolby Vision', reports.map((r, i) => specs[i].video.dolby_vision.profile ? `Profile ${specs[i].video.dolby_vision.profile}` : evidenceText(r.findings['video.dolby_vision'])), flags('dv_present')],
      ['HDR10', reports.map(hdrText)],
      ['HDR10+', reports.map(r => evidenceText(r.findings['video.hdr10_plus'])), flags('hdr10_plus')],
      ['Audio codec', audio.map(t => fmt.codecName(t?.codec))],
      ['Channels', audio.map(t => fmt.channelsLabel(t?.channels)), best(audio.map(t => t?.channels))],
      ['Audio bitrate', audio.map(t => t?.bit_rate_kbps == null ? 'Unknown' : `${t.bit_rate_kbps} kbps`), best(audio.map(t => t?.bit_rate_kbps))],
      ['Atmos', reports.map(r => {
        const tracks = r.tracks.filter(t => t.kind === 'audio'), findings = tracks.map(t => r.findings[`audio.${t.id}.atmos`]);
        return evidenceText(findings.find(f => ['observed', 'signaled'].includes(f?.state)) || (r.structureComplete && findings.every(f => f?.state === 'absent') ? { state: 'absent' } : findings.find(f => f?.state !== 'absent')));
      }), flags('atmos')],
      ['Subtitles', reports.map((r, i) => r.structureComplete ? specs[i].subtitles.length : `${specs[i].subtitles.length}+ · checking`), best(reports.map((r, i) => r.structureComplete ? specs[i].subtitles.length : null))],
      ['Size', specs.map(s => fmt.bytes(s.container.size_bytes))],
      ['Duration', specs.map(s => fmt.duration(s.container.duration_s))],
      ...components.map(c => [`${c.label} points`, scores.map(s => rangeText(s.components[c.key]))]),
    ];
    replace(ui.output, el('div', { class: 'compare-results browser-compare-results' },
      el('section', { class: 'ranking-section', 'aria-labelledby': 'ranking-title' },
        el('header', { class: 'section-header' }, el('h2', { id: 'ranking-title' }, 'Ranking'), ranking.provisional && badge('Provisional', { tone: 'warn' })),
        el('p', { class: 'compare-range-note' }, 'Scores are possible ranges, not estimates. Solid points are confirmed; stripes are unresolved. Overlapping ranges have no decided order. A best pick requires separated ranges, or fully resolved tied scores.'),
        scoreLegend(components), el('ol', { class: 'ranking' }, cards)),
      queue.items.flatMap(item => ['failed', 'file-unavailable'].includes(item.report?.terminal?.reason) ? [notice({ tone: 'danger', title: `Couldn’t finish ${item.file.name}`, body: item.report.terminal.message })] : []),
      card({ title: 'Side by side', iconName: 'compare', className: 'dimensions-card', body: dataTable({ caption: 'Evidence and component score ranges; a check marks best in a conclusive row', className: 'dimension-table', columns: [{ label: 'Dimension', value: row => row[0] }, ...ranking.order.map(i => ({ label: queue.items[i].file.name, value: row => row[2]?.includes(i) ? el('span', { class: 'compare-dimension-winner' }, icon('check', { size: 14 }), row[1][i], el('span', { class: 'visually-hidden' }, ' (best)')) : row[1][i] }))], rows: dimensionRows }) }),
      drawer({ label: 'Evidence details', iconName: 'inspect', meta: 'Why a score may remain a range', content: () => el('div', { class: 'compare-evidence' }, reports.map((r, i) => el('section', {}, el('h3', {}, queue.items[i].file.name), ...Object.entries(r.findings).map(([id, finding]) => el('p', {}, `${id}: ${evidenceText(finding)}. ${finding.summary || ''} ${(finding.limitations || []).join(' ')}`))))) })));
    const nextDrawer = ui.output.querySelector('details');
    if (drawerOpen) nextDrawer.open = true;
    if (drawerFocused) nextDrawer.querySelector('summary').focus({ preventScroll: true });
    ui.output.querySelector('.table-wrap').scrollLeft = tableScroll;
  }
  renderFiles(); refresh();
  return { refresh, pauseFromHost: () => { queue.pauseFromHost(); refresh(); }, resumeFromHost: () => { queue.resumeFromHost(); refresh(); } };
}
