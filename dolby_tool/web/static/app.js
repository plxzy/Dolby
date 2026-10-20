// dolby-tool frontend

// ---------------------------------------------------------------------------
// utilities

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// SVG Icons used for categories
const ICON_CONTAINER = `<svg fill="currentColor" width="14" height="14" viewBox="0 0 56 56" xmlns="http://www.w3.org/2000/svg"><path d="M 28.0000 26.6406 L 50.0783 14.1016 C 49.7264 13.75 49.3045 13.4688 48.7890 13.1875 L 32.2657 3.7657 C 30.8126 2.9453 29.4063 2.5000 28.0000 2.5000 C 26.5938 2.5000 25.1875 2.9453 23.7344 3.7657 L 7.2110 13.1875 C 6.6954 13.4688 6.2735 13.75 5.9219 14.1016 Z M 26.4063 53.5 L 26.4063 29.4532 L 4.3985 16.8906 C 4.2813 17.4063 4.2110 17.9688 4.2110 18.6719 L 4.2110 36.9297 C 4.2110 40.3281 5.4063 41.5938 7.5860 42.8360 L 25.9375 53.2891 C 26.1016 53.3828 26.2422 53.4532 26.4063 53.5 Z M 29.5938 53.5 C 29.7579 53.4532 29.8985 53.3828 30.0626 53.2891 L 48.4141 42.8360 C 50.5938 41.5938 51.7890 40.3281 51.7890 36.9297 L 51.7890 18.6719 C 51.7890 17.9688 51.7189 17.4063 51.6018 16.8906 L 29.5938 29.4532 Z"/></svg>`;
const ICON_VIDEO = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M7 5V19M17 5V19M3 8H7M17 8H21M3 16H7M17 16H21M3 12H7M17 12H21M6.2 20H17.8C18.9201 20 19.4802 20 19.908 19.782C20.2843 19.5903 20.5903 19.2843 20.782 18.908C21 18.4802 21 17.9201 21 16.8V7.2C21 6.0799 21 5.51984 20.782 5.09202C20.5903 4.71569 20.2843 4.40973 19.908 4.21799C19.4802 4 18.9201 4 17.8 4H6.2C5.0799 4 4.51984 4 4.09202 4.21799C3.71569 4.40973 3.40973 4.71569 3.21799 5.09202C3 5.51984 3 6.07989 3 7.2V16.8C3 17.9201 3 18.4802 3.21799 18.908C3.40973 19.2843 3.71569 19.5903 4.09202 19.782C4.51984 20 5.07989 20 6.2 20Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const ICON_DOLBY = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M0 4V20H24V4H0ZM10 12C10 9.79086 8.20914 8 6 8H4V16H6C8.20914 16 10 14.2091 10 12ZM18 16H20V8H18C15.7909 8 14 9.79086 14 12C14 14.2091 15.7909 16 18 16Z" fill="currentColor"/></svg>`;
const ICON_HDR = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 17.75C10.8628 17.75 9.75106 17.4128 8.80547 16.781C7.85989 16.1491 7.1229 15.2511 6.6877 14.2004C6.25249 13.1498 6.13862 11.9936 6.36049 10.8782C6.58235 9.76284 7.12999 8.73829 7.93414 7.93414C8.73829 7.12999 9.76284 6.58235 10.8782 6.36049C11.9936 6.13862 13.1498 6.25249 14.2004 6.6877C15.2511 7.1229 16.1491 7.85989 16.781 8.80547C17.4128 9.75106 17.75 10.8628 17.75 12C17.7474 13.5242 17.1407 14.9852 16.0629 16.0629C14.9852 17.1407 13.5242 17.7474 12 17.75ZM12 7.75C11.1594 7.75 10.3377 7.99926 9.63883 8.46626C8.93992 8.93325 8.39519 9.59701 8.07351 10.3736C7.75184 11.1502 7.66768 12.0047 7.83167 12.8291C7.99565 13.6536 8.40043 14.4108 8.9948 15.0052C9.58917 15.5996 10.3464 16.0044 11.1709 16.1683C11.9953 16.3323 12.8498 16.2482 13.6264 15.9265C14.403 15.6048 15.0668 15.0601 15.5337 14.3612C16.0007 13.6623 16.25 12.8406 16.25 12C16.2474 10.8736 15.7987 9.79417 15.0023 8.99772C14.2058 8.20126 13.1264 7.75264 12 7.75Z" fill="currentColor"/><path d="M12 5C11.8019 4.99741 11.6126 4.91756 11.4725 4.77747C11.3324 4.63737 11.2526 4.44811 11.25 4.25V2.75C11.25 2.55109 11.329 2.36032 11.4697 2.21967C11.6103 2.07902 11.8011 2 12 2C12.1989 2 12.3897 2.07902 12.5303 2.21967C12.671 2.36032 12.75 2.55109 12.75 2.75V4.25C12.7474 4.44811 12.6676 4.63737 12.5275 4.77747C12.3874 4.91756 12.1981 4.99741 12 5Z" fill="currentColor"/><path d="M12 22C11.8019 21.9974 11.6126 21.9176 11.4725 21.7775C11.3324 21.6374 11.2526 21.4481 11.25 21.25V19.75C11.25 19.5511 11.329 19.3603 11.4697 19.2197C11.6103 19.079 11.8011 19 12 19C12.1989 19 12.3897 19.079 12.5303 19.2197C12.671 19.3603 12.75 19.5511 12.75 19.75V21.25C12.7474 21.4481 12.6676 21.6374 12.5275 21.7775C12.3874 21.9176 12.1981 21.9974 12 22Z" fill="currentColor"/><path d="M21.25 12.75H19.75C19.5511 12.75 19.3603 12.671 19.2197 12.5303C19.079 12.3897 19 12.1989 19 12C19 11.8011 19.079 11.6103 19.2197 11.4697C19.3603 11.329 19.5511 11.25 19.75 11.25H21.25C21.4489 11.25 21.6397 11.329 21.7803 11.4697C21.921 11.6103 22 11.8011 22 12C22 12.1989 21.921 12.3897 21.7803 12.5303C21.6397 12.671 21.4489 12.75 21.25 12.75Z" fill="currentColor"/><path d="M4.25 12.75H2.75C2.55109 12.75 2.36032 12.671 2.21967 12.5303C2.07902 12.3897 2 12.1989 2 12C2 11.8011 2.07902 11.6103 2.21967 11.4697C2.36032 11.329 2.55109 11.25 2.75 11.25H4.25C4.44891 11.25 4.63968 11.329 4.78033 11.4697C4.92098 11.6103 5 11.8011 5 12C5 12.1989 4.92098 12.3897 4.78033 12.5303C4.63968 12.671 4.44891 12.75 4.25 12.75Z" fill="currentColor"/><path d="M6.50001 7.24995C6.30707 7.2352 6.12758 7.14545 6.00001 6.99995L4.91001 5.99995C4.83844 5.92838 4.78167 5.84341 4.74293 5.7499C4.7042 5.65639 4.68427 5.55617 4.68427 5.45495C4.68427 5.35373 4.7042 5.25351 4.74293 5.16C4.78167 5.06649 4.83844 4.98152 4.91001 4.90995C4.98158 4.83838 5.06655 4.78161 5.16006 4.74287C5.25357 4.70414 5.3538 4.6842 5.45501 4.6842C5.55623 4.6842 5.65645 4.70414 5.74996 4.74287C5.84347 4.78161 5.92844 4.83838 6.00001 4.90995L7.00001 5.99995C7.123 6.13746 7.19099 6.31547 7.19099 6.49995C7.19099 6.68443 7.123 6.86244 7.00001 6.99995C6.87244 7.14545 6.69295 7.2352 6.50001 7.24995Z" fill="currentColor"/><path d="M18.56 19.31C18.4615 19.3104 18.3638 19.2912 18.2728 19.2534C18.1818 19.2157 18.0993 19.1601 18.03 19.09L17 18C16.9332 17.86 16.9114 17.7028 16.9376 17.5499C16.9638 17.3971 17.0368 17.2561 17.1465 17.1464C17.2561 17.0368 17.3971 16.9638 17.55 16.9376C17.7028 16.9113 17.8601 16.9331 18 17L19.09 18C19.2305 18.1406 19.3094 18.3312 19.3094 18.53C19.3094 18.7287 19.2305 18.9194 19.09 19.06C19.0233 19.1355 18.9419 19.1967 18.8508 19.2397C18.7597 19.2827 18.6607 19.3066 18.56 19.31Z" fill="currentColor"/><path d="M17.5 7.24995C17.3071 7.2352 17.1276 7.14545 17 6.99995C16.877 6.86244 16.809 6.68443 16.809 6.49995C16.809 6.31547 16.877 6.13746 17 5.99995L18 4.90995C18.1445 4.76541 18.3406 4.6842 18.545 4.6842C18.7494 4.6842 18.9455 4.76541 19.09 4.90995C19.2345 5.05449 19.3158 5.25054 19.3158 5.45495C19.3158 5.65936 19.2345 5.85541 19.09 5.99995L18 6.99995C17.8724 7.14545 17.6929 7.2352 17.5 7.24995Z" fill="currentColor"/><path d="M5.44001 19.31C5.34147 19.3104 5.24383 19.2912 5.15282 19.2534C5.06181 19.2157 4.97926 19.1601 4.91001 19.09C4.76956 18.9494 4.69067 18.7587 4.69067 18.56C4.69067 18.3612 4.76956 18.1706 4.91001 18.03L6.00001 17C6.13997 16.9331 6.2972 16.9113 6.45006 16.9376C6.60293 16.9638 6.7439 17.0368 6.85357 17.1464C6.96324 17.2561 7.03621 17.3971 7.06244 17.5499C7.08866 17.7028 7.06685 17.86 7.00001 18L6.00001 19.09C5.92728 19.1638 5.83985 19.2216 5.74338 19.2595C5.64691 19.2974 5.54356 19.3146 5.44001 19.31Z" fill="currentColor"/></svg>`;
const ICON_AUDIO = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V6l9-2v12"/><circle cx="7" cy="18" r="2.5"/><circle cx="16" cy="16" r="2.5"/><path d="M9 9.5 18 7.5"/></svg>`;
const ICON_SUBTITLES = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path><path d="M8 7h8M8 11h8"></path></svg>`;
const ICON_SCORE = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6M18 9h1.5a2.5 2.5 0 0 0 0-5H18M4 22h16M10 14.66V17c0 .55-.45 1-1 1H4v2h16v-2h-5c-.55 0-1-.45-1-1v-2.34M12 2a4 4 0 0 1 4 4v5c0 2.21-1.79 4-4 4s-4-1.79-4-4V6a4 4 0 0 1 4-4z"></path></svg>`;

function h3WithIcon(iconHtml, text) {
  const iconSpan = el('span', { class: 'heading-icon', style: 'display: inline-flex; align-items: center;' });
  iconSpan.innerHTML = iconHtml;
  return el('h3', {}, iconSpan, text);
}

function api(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(async (r) => {
    const text = await r.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`);
    return data;
  });
}

function get(path) {
  return fetch(path).then(async (r) => {
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  });
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) c.forEach((cc) => cc && e.appendChild(cc));
    else if (typeof c === 'string' || typeof c === 'number') e.appendChild(document.createTextNode(String(c)));
    else e.appendChild(c);
  }
  return e;
}

function kv(k, v, pillClass) {
  if (v === null || v === undefined || v === '') v = '—';
  const valueEl = pillClass
    ? el('span', { class: `pill ${pillClass}` }, String(v))
    : el('span', { class: 'v' }, String(v));
  return el('div', { class: 'kv' }, el('span', { class: 'k' }, k), valueEl);
}

function tvDecodedFourccClass(fourcc) {
  const f = (fourcc || '').toLowerCase();
  if (f.startsWith('dv')) return 'good';
  if (f === 'qdh1') return 'warn';
  if (f === 'hvc1') return 'bad';
  return 'neutral';
}

function audioLabel(audio) {
  if (!audio) return '—';
  const format = (audio.format || '').toLowerCase();
  const displayFormat = format === 'qc+3'
    ? 'qc+3 (Apple Dolby-like)'
    : ['ec+3', 'ec-3', 'ec3'].includes(format)
      ? `${audio.format} (E-AC-3)`
      : ['qaac', 'aacp', 'aac'].includes(format) && (audio.channels || 0) <= 2
        ? `${audio.format} (AAC fallback)`
        : audio.format;
  const parts = [
    displayFormat,
    audio.channels ? `ch=${audio.channels}` : null,
    audio.sample_rate ? `${audio.sample_rate} Hz` : null,
    audio.spatialization ? `spat=${audio.spatialization}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(' ') : '—';
}

function audioPillClass(audio) {
  const format = (audio?.format || '').toLowerCase();
  const channels = audio?.channels || 0;
  if (['ec+3', 'ec-3', 'ec3'].includes(format) && channels >= 16) return 'good';
  if (format === 'qc+3' && channels >= 16) return 'good';
  if (channels > 2) return 'good';
  if (['qaac', 'aacp', 'aac'].includes(format) && channels <= 2) return 'neutral';
  return 'neutral';
}

function boolText(v) {
  if (v === true) return 'yes';
  if (v === false) return 'no';
  return null;
}

function capturedText(v) {
  const text = boolText(v);
  return text == null ? 'not captured' : text;
}

function activeText(v) {
  const text = boolText(v);
  return text == null ? 'not captured' : (v ? 'active' : 'inactive');
}

function rendererVerdictLabel(r) {
  if (!r) return null;
  if (r.verdict === 'app_spatial_rendering_active') return {
    text: 'App-level spatial rendering active',
    className: 'good',
  };
  if (r.verdict === 'app_spatial_rendering_was_active') return {
    text: 'App-level spatial rendering was observed, but the last flag is false',
    className: 'warn',
  };
  if (r.verdict === 'lower_level_active_app_spatial_false') return {
    text: 'Atmos/spatial machinery active, but app-level spatial rendering flag is false',
    className: 'warn',
  };
  if (r.verdict === 'lower_level_spatialization_active') return {
    text: 'Lower-level Atmos/spatial machinery active',
    className: 'good',
  };
  return {
    text: 'No spatial renderer activity captured',
    className: 'neutral',
  };
}

function rendererMediaLabel(item) {
  if (!item) return '—';
  const parts = [
    item.format,
    item.label,
    item.channels ? `ch=${item.channels}` : null,
    item.sample_rate ? `${item.sample_rate} Hz` : null,
    item.rendering_spatial_audio == null ? null : `rendering=${boolText(item.rendering_spatial_audio)}`,
  ].filter(Boolean);
  return parts.join(' ');
}

function rendererPowerLabel(item) {
  if (!item) return '—';
  return [
    `spatialization=${boolText(item.spatialization)}`,
    `stereo upmix=${boolText(item.stereo_upmix)}`,
    `head tracking=${boolText(item.head_tracking)}`,
  ].filter(Boolean).join(', ');
}

function rendererMixerLabel(item) {
  if (!item) return '—';
  return [
    item.format,
    item.channels ? `ch=${item.channels}` : null,
    item.content_spatializable == null ? null : `spatializable=${boolText(item.content_spatializable)}`,
    item.spatialization_status != null ? `status=${item.spatialization_status}` : null,
  ].filter(Boolean).join(' ');
}

function rendererAtmosLabel(item) {
  if (!item) return '—';
  return [
    item.decoder_is_atmos == null ? null : `Atmos=${boolText(item.decoder_is_atmos)}`,
    item.decoder_oar_mode == null ? null : `OAR=${boolText(item.decoder_oar_mode)}`,
  ].filter(Boolean).join(', ');
}

// Last directory successfully resolved — passed as hint to /api/find so the
// find(1) fallback searches there first (useful for external volumes Spotlight skips).
let _lastResolvedDir = '';

function _recordDir(p) {
  if (p) _lastResolvedDir = p.includes('/') ? p.substring(0, p.lastIndexOf('/')) : '';
}

function pathFromDataTransfer(dt) {
  // Safari passes file:// URIs; Chrome/Firefox only expose File objects — no .path
  const uri = dt.getData('text/uri-list') || dt.getData('text/plain');
  if (uri) {
    const first = uri.split('\n').find((l) => l.trim() && !l.startsWith('#'));
    if (first) return first.startsWith('file://') ? decodeURIComponent(first.slice('file://'.length)) : first;
  }
  return null;
}

function _disambiguate(name, paths) {
  // Inline disambiguation dialog rendered into a temporary overlay
  return new Promise((resolve) => {
    const overlay = el('div', { class: 'disambig-overlay' });
    const box = el('div', { class: 'disambig-box' });
    box.appendChild(el('p', {}, `Multiple files named "${name}" found — pick one:`));
    paths.forEach((p, i) => {
      const btn = el('button', { class: 'disambig-btn', onclick: () => { overlay.remove(); resolve(p); } },
        el('span', { class: 'disambig-idx' }, String(i + 1)),
        el('span', { class: 'disambig-path' }, p),
      );
      box.appendChild(btn);
    });
    box.appendChild(el('button', { class: 'disambig-cancel', onclick: () => { overlay.remove(); resolve(null); } }, 'Cancel'));
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  });
}

async function pathsFromDataTransfer(dt, multi) {
  // Try URI list first (Safari — exposes file:// URIs directly)
  const uriList = (dt.getData('text/uri-list') || dt.getData('text/plain'))
    .split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
  if (uriList.length) {
    const paths = uriList.map((u) => u.startsWith('file://') ? decodeURIComponent(u.slice(7)) : u);
    paths.forEach(_recordDir);
    return paths;
  }
  // Fallback: File objects (Chrome on macOS) — resolve via Spotlight + find(1)
  const files = Array.from(dt.files || []);
  if (!files.length) return [];
  const resolved = await Promise.all(files.map(async (f) => {
    const url = '/api/find?name=' + encodeURIComponent(f.name) +
      (_lastResolvedDir ? '&hint=' + encodeURIComponent(_lastResolvedDir) : '');
    const res = await fetch(url).then((r) => r.json());
    if (res.paths.length === 1) { _recordDir(res.paths[0]); return res.paths[0]; }
    if (res.paths.length > 1) {
      const chosen = await _disambiguate(f.name, res.paths);
      if (chosen) _recordDir(chosen);
      return chosen;
    }
    alert(`Could not locate "${f.name}". Use Browse… or paste the full path instead.`);
    return null;
  }));
  return resolved.filter(Boolean);
}

function bindDropzone(zone, onPath, { multi = false } = {}) {
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', async (e) => {
    e.preventDefault();
    zone.classList.remove('drag');
    const paths = await pathsFromDataTransfer(e.dataTransfer, multi);
    if (!paths.length) return;
    if (multi) onPath(paths);
    else onPath(paths[0]);
  });
}



async function loadCapabilities() {
  try {
    const caps = await get('/api/capabilities');
    const missing = [];
    for (const [k, ok] of Object.entries(caps.tools || {})) {
      if (!ok) missing.push(k);
    }
    const banner = $('#capabilities-banner');
    if (missing.length) {
      banner.hidden = false;
      banner.textContent = `Limited environment detected (${caps.platform}): missing tools: ${missing.join(', ')}.`;
    }
  } catch (_e) { }
}

loadCapabilities();

// ---------------------------------------------------------------------------
// tabs

$$('.tab').forEach((t) => {
  t.addEventListener('click', () => {
    $$('.tab').forEach((x) => x.classList.remove('active'));
    $$('.panel').forEach((x) => x.classList.remove('active'));
    t.classList.add('active');
    $(`#tab-${t.dataset.tab}`).classList.add('active');
  });
});

// ---------------------------------------------------------------------------
// Inspect tab

bindDropzone($('#inspect-drop'), (p) => {
  $('#inspect-path').value = p;
  runInspect(p);
});

$('#inspect-browse').addEventListener('click', async () => {
  const { paths } = await get('/api/pick');
  if (paths.length) {
    $('#inspect-path').value = paths[0];
    runInspect(paths[0]);
  }
});

$('#inspect-go').addEventListener('click', () => runInspect($('#inspect-path').value.trim()));

$('#inspect-path').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runInspect(e.target.value.trim());
});

async function runInspect(path) {
  if (!path) return;
  const out = $('#inspect-result');
  out.innerHTML = `
    <div class="card" style="pointer-events: none;">
      <div class="skeleton-title skeleton-shimmer"></div>
      <div class="skeleton-line skeleton-shimmer" style="width: 60%"></div>
      <div class="skeleton-line skeleton-shimmer" style="width: 35%; height: 24px; border-radius: 12px; margin: 16px 0 12px 0;"></div>
      
      <div style="height: 12px; width: 80px; background: var(--border); border-radius: 3px; margin: 24px 0 12px 0;"></div>
      <div class="skeleton-grid" style="margin-bottom: 24px;">
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
      </div>
      
      <div style="height: 12px; width: 60px; background: var(--border); border-radius: 3px; margin: 24px 0 12px 0;"></div>
      <div class="skeleton-grid">
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
      </div>
    </div>
  `;
  try {
    const spec = await api('/api/inspect', { path });
    out.innerHTML = '';
    out.appendChild(renderSpec(spec));
  } catch (e) {
    out.innerHTML = '';
    out.appendChild(el('div', { class: 'error' }, e.message));
  }
}

function renderSpec(spec) {
  const v = spec.video;
  const dv = v.dolby_vision;
  const card = el('div', { class: 'card capt-summary' });
  
  // header row: title + copy icon
  const hdr = el('div', { class: 'capt-summary-hdr' });
  hdr.appendChild(el('h2', {}, spec.filename));

  const copyWrap = el('div', { class: 'copy-wrap' });
  const copyStatus = el('span', { class: 'copy-status', 'aria-live': 'polite' });

  copyWrap.appendChild(el('button', {
    class: 'copy-icon-btn',
    type: 'button',
    title: 'Copy shareable summary',
    'aria-label': 'Copy shareable summary',
    onclick: async () => {
      const parts = [
        `File: ${spec.filename}`,
        `Path: ${spec.path}`,
        `Size: ${spec.container.size_pretty} | Duration: ${spec.container.duration_pretty}`,
        `Video: ${v.codec} | ${v.resolution} | ${v.fps} fps | ${v.bit_rate_mbps ? v.bit_rate_mbps + ' Mbps' : 'n/a'}`,
        `Dolby Vision: ${dv.present ? `Yes (Profile ${dv.profile || dv.profile_raw}, ${dv.compatibility_name})` : 'No'}`,
        `HDR10+: ${v.hdr10_plus ? 'Yes' : 'No'} | HDR10: ${(v.hdr10.mdcv || v.hdr10.cll) ? 'Yes' : 'No'}`,
        `FourCC: ${v.fourcc || 'n/a'}`
      ];
      if (spec.audio.length) {
        parts.push(`Audio tracks (${spec.audio.length}):`);
        spec.audio.forEach((a, i) => {
          parts.push(`  [#${i}] ${a.codec || '—'} | ${a.channels}ch | ${a.bit_rate_kbps ? a.bit_rate_kbps + ' kbps' : '—'} | ${a.channel_layout || '—'}${a.atmos ? ' (Atmos)' : ''}`);
        });
      }
      await _copyFromButton(copyWrap, parts.join('\n'));
    },
    html: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.5 3H14.6C16.84 3 17.96 3 18.816 3.436C19.569 3.819 20.181 4.431 20.564 5.184C21 6.04 21 7.16 21 9.4V16.5M6.2 21H14.3C15.42 21 15.98 21 16.408 20.782C16.784 20.59 17.09 20.284 17.282 19.908C17.5 19.48 17.5 18.92 17.5 17.8V9.7C17.5 8.58 17.5 8.02 17.282 7.592C17.09 7.216 16.784 6.91 16.408 6.718C15.98 6.5 15.42 6.5 14.3 6.5H6.2C5.08 6.5 4.52 6.5 4.092 6.718C3.716 6.91 3.41 7.216 3.218 7.592C3 8.02 3 8.58 3 9.7V17.8C3 18.92 3 19.48 3.218 19.908C3.41 20.284 3.716 20.59 4.092 20.782C4.52 21 5.08 21 6.2 21Z"/></svg>',
  }));

  copyWrap.appendChild(el('div', { class: 'copy-menu' },
    el('button', {
      class: 'copy-menu-item',
      type: 'button',
      onclick: async () => {
        await _copyFromButton(copyWrap, JSON.stringify(spec, null, 2));
      },
    }, 'Copy full JSON'),
  ));

  copyWrap.appendChild(copyStatus);
  hdr.appendChild(copyWrap);
  card.appendChild(hdr);
  card.appendChild(el('div', { class: 'filename' }, spec.path));

  if (v.fourcc_warning) {
    card.appendChild(el('div', { class: 'warning', html: `<strong>⚠︎ TV.app DV breakage:</strong> ${v.fourcc_warning}` }));
  }

  // Verdict pills
  const pills = el('div', { class: 'row', style: 'justify-content: flex-start; margin-bottom: 12px' });
  pills.appendChild(el('span', { class: 'pill ' + (dv.present ? (v.fourcc_warning ? 'warn' : 'good') : 'neutral') },
    dv.present ? `Dolby Vision ${dv.profile || dv.profile_raw} (${dv.compatibility_name})` : 'No Dolby Vision'));
  pills.appendChild(el('span', { class: 'pill ' + (v.hdr10_plus ? 'good' : 'neutral') }, v.hdr10_plus ? 'HDR10+' : 'No HDR10+'));
  pills.appendChild(el('span', { class: 'pill ' + ((v.hdr10.mdcv || v.hdr10.cll) ? 'good' : 'neutral') }, (v.hdr10.mdcv || v.hdr10.cll) ? 'HDR10' : 'No HDR10'));
  pills.appendChild(el('span', { class: 'pill ' + (v.fourcc && (v.fourcc.startsWith('dv') ? 'good' : (dv.present ? 'bad' : 'neutral'))) }, `FourCC: ${v.fourcc || 'n/a'}`));
  const anyAtmos = spec.audio.some((a) => a.atmos === true);
  pills.appendChild(el('span', { class: 'pill ' + (anyAtmos ? 'good' : 'neutral') }, anyAtmos ? 'Atmos' : 'No Atmos'));
  card.appendChild(pills);

  // Container
  card.appendChild(h3WithIcon(ICON_CONTAINER, 'Container'));
  const cont = spec.container;
  const cgrid = el('div', { class: 'grid' });
  cgrid.appendChild(kv('Format', cont.format_name));
  cgrid.appendChild(kv('Duration', cont.duration_pretty));
  cgrid.appendChild(kv('Size', cont.size_pretty));
  cgrid.appendChild(kv('Overall bitrate', cont.overall_bit_rate_bps ? `${(cont.overall_bit_rate_bps/1_000_000).toFixed(2)} Mbps` : null));
  card.appendChild(cgrid);

  // Video
  card.appendChild(h3WithIcon(ICON_VIDEO, 'Video'));
  const vgrid = el('div', { class: 'grid' });
  vgrid.appendChild(kv('Codec', v.codec));
  vgrid.appendChild(kv('Profile', v.profile));
  vgrid.appendChild(kv('FourCC', v.fourcc, v.fourcc && v.fourcc.startsWith('dv') ? 'good' : (dv.present ? 'bad' : 'neutral')));
  vgrid.appendChild(kv('Resolution', v.resolution));
  vgrid.appendChild(kv('FPS', v.fps));
  vgrid.appendChild(kv('Pixel fmt', v.pix_fmt));
  vgrid.appendChild(kv('Bitrate', v.bit_rate_mbps ? `${v.bit_rate_mbps} Mbps` : null));
  vgrid.appendChild(kv('Transfer', v.color_transfer));
  vgrid.appendChild(kv('Primaries', v.color_primaries));
  vgrid.appendChild(kv('Color space', v.color_space));
  card.appendChild(vgrid);

  // Dolby Vision
  if (dv.present) {
    card.appendChild(h3WithIcon(ICON_DOLBY, 'Dolby Vision'));
    const dgrid = el('div', { class: 'grid' });
    dgrid.appendChild(kv('Profile', dv.profile, 'good'));
    dgrid.appendChild(kv('Raw profile', dv.profile_raw));
    dgrid.appendChild(kv('Compatibility', `${dv.compatibility_id} (${dv.compatibility_name})`));
    dgrid.appendChild(kv('Level', dv.level));
    dgrid.appendChild(kv('RPU present', dv.rpu_present ? 'yes' : 'no'));
    dgrid.appendChild(kv('BL present', dv.bl_present ? 'yes' : 'no'));
    dgrid.appendChild(kv('EL present', dv.el_present ? 'yes' : 'no'));
    card.appendChild(dgrid);
  }

  // HDR
  card.appendChild(h3WithIcon(ICON_HDR, 'HDR metadata'));
  const hgrid = el('div', { class: 'grid' });
  hgrid.appendChild(kv('Mastering display', v.hdr10.mdcv ? 'present' : '—', v.hdr10.mdcv ? 'good' : 'neutral'));
  hgrid.appendChild(kv('Content light level', v.hdr10.cll ? 'present' : '—', v.hdr10.cll ? 'good' : 'neutral'));
  hgrid.appendChild(kv('HDR10+', v.hdr10_plus ? 'present' : '—', v.hdr10_plus ? 'good' : 'neutral'));
  card.appendChild(hgrid);

  // Audio
  if (spec.audio.length) {
    card.appendChild(h3WithIcon(ICON_AUDIO, `Audio (${spec.audio.length} track${spec.audio.length === 1 ? '' : 's'})`));
    const at = el('table');
    at.appendChild(el('thead', {}, el('tr', {},
      el('th', {}, '#'),
      el('th', {}, 'Codec'),
      el('th', {}, 'Profile'),
      el('th', {}, 'Channels'),
      el('th', {}, 'Layout'),
      el('th', {}, 'Bitrate'),
      el('th', {}, 'Sample'),
      el('th', {}, 'Lang'),
      el('th', {}, 'Atmos'),
    )));
    const ab = el('tbody');
    spec.audio.forEach((a, i) => {
      ab.appendChild(el('tr', {},
        el('td', { class: 'num' }, i),
        el('td', { class: 'mono' }, a.codec || '—'),
        el('td', {}, a.profile || '—'),
        el('td', { class: 'num' }, a.channels ?? '—'),
        el('td', { class: 'mono' }, a.channel_layout || '—'),
        el('td', { class: 'num' }, a.bit_rate_kbps ? `${a.bit_rate_kbps} kbps` : '—'),
        el('td', { class: 'num' }, a.sample_rate_hz ? `${a.sample_rate_hz} Hz` : '—'),
        el('td', {}, a.language || '—'),
        el('td', {}, a.atmos === true ? '✓' : (a.atmos === false ? '✗' : '?')),
      ));
    });
    at.appendChild(ab);
    card.appendChild(at);
  }

  // Subtitles
  if (spec.subtitles.length) {
    card.appendChild(h3WithIcon(ICON_SUBTITLES, `Subtitles (${spec.subtitles.length} track${spec.subtitles.length === 1 ? '' : 's'})`));
    const st = el('table');
    st.appendChild(el('thead', {}, el('tr', {},
      el('th', {}, '#'),
      el('th', {}, 'Codec'),
      el('th', {}, 'Lang'),
      el('th', {}, 'Title'),
      el('th', {}, 'Default'),
      el('th', {}, 'Forced'),
      el('th', {}, 'SDH'),
    )));
    const sb = el('tbody');
    spec.subtitles.forEach((s, i) => {
      sb.appendChild(el('tr', {},
        el('td', { class: 'num' }, i),
        el('td', { class: 'mono' }, s.codec || '—'),
        el('td', {}, s.language || '—'),
        el('td', {}, s.title || '—'),
        el('td', {}, s.default ? '✓' : ''),
        el('td', {}, s.forced ? '✓' : ''),
        el('td', {}, s.hearing_impaired ? '✓' : ''),
      ));
    });
    st.appendChild(sb);
    card.appendChild(st);
  }

  // Raw JSON drill-down
  const det = el('details', {}, el('summary', {}, 'Raw JSON'), el('pre', {}, JSON.stringify(spec, null, 2)));
  card.appendChild(det);

  return card;
}

// ---------------------------------------------------------------------------
// Compare tab

const comparePaths = new Set();

function compareWeightsFromUi() {
  return {
    dv_present: Number($('#w-dv-present').value || 0),
    dv_fourcc_ok: Number($('#w-dv-fourcc').value || 0),
    hdr10_plus: Number($('#w-hdr10plus').value || 0),
    atmos: Number($('#w-atmos').value || 0),
    bit_rate_mbps: Number($('#w-bitrate').value || 0),
    resolution: Number($('#w-resolution').value || 0),
    subtitles: Number($('#w-subtitles').value || 0),
  };
}

function renderCompareList() {
  const ul = $('#compare-list');
  ul.innerHTML = '';
  Array.from(comparePaths).forEach((p) => {
    const li = el('li', {},
      el('span', { class: 'name' }, p),
      el('button', { class: 'remove', title: 'Remove', onclick: () => { comparePaths.delete(p); renderCompareList(); } }, '×'),
    );
    ul.appendChild(li);
  });
}

bindDropzone($('#compare-drop'), (paths) => {
  paths.forEach((p) => comparePaths.add(p));
  renderCompareList();
}, { multi: true });

$('#compare-add').addEventListener('click', async () => {
  const { paths } = await get('/api/pick?multi=true');
  paths.forEach((p) => comparePaths.add(p));
  renderCompareList();
});

$('#compare-add-folder').addEventListener('click', async () => {
  const dir = $('#compare-folder-path').value.trim();
  if (!dir) {
    alert('Paste a folder path first.');
    return;
  }
  try {
    const { files } = await get('/api/list?path=' + encodeURIComponent(dir));
    files.forEach((f) => comparePaths.add(f.path));
    renderCompareList();
  } catch (e) {
    alert(e.message);
  }
});

$('#compare-clear').addEventListener('click', () => {
  comparePaths.clear();
  renderCompareList();
  $('#compare-result').innerHTML = '';
});

$('#compare-go').addEventListener('click', async () => {
  const out = $('#compare-result');
  if (comparePaths.size < 1) { alert('Add at least one file.'); return; }
  out.innerHTML = `
    <div class="card" style="pointer-events: none;">
      <div class="skeleton-title skeleton-shimmer" style="width: 25%;"></div>
      <div class="skeleton-grid" style="margin-bottom: 24px;">
        <div class="skeleton-kv skeleton-shimmer"></div>
        <div class="skeleton-kv skeleton-shimmer"></div>
      </div>
      <div class="skeleton-table">
        <div class="skeleton-row skeleton-shimmer" style="width: 100%; height: 28px; background: var(--border); margin-bottom: 12px;"></div>
        <div class="skeleton-row skeleton-shimmer" style="width: 100%;"></div>
        <div class="skeleton-row skeleton-shimmer" style="width: 100%;"></div>
        <div class="skeleton-row skeleton-shimmer" style="width: 100%;"></div>
      </div>
    </div>
  `;
  try {
    const res = await api('/api/compare', { paths: Array.from(comparePaths), weights: compareWeightsFromUi() });
    out.innerHTML = '';
    out.appendChild(renderCompare(res));
  } catch (e) {
    out.innerHTML = '';
    out.appendChild(el('div', { class: 'error' }, e.message));
  }
});

function renderCompare(res) {
  const card = el('div', { class: 'card' });
  card.appendChild(el('h2', {}, 'Comparison'));

  if (res.errors.length) {
    res.errors.forEach((er) => {
      card.appendChild(el('div', { class: 'warning' }, `${er.path}: ${er.error}`));
    });
  }

  // Score row
  const scoreGrid = el('div', { class: 'grid', style: 'margin-bottom: 16px' });
  res.rows.forEach((r, i) => {
    const s = res.scores[i];
    const pillClass = s.is_best ? 'good' : 'neutral';
    scoreGrid.appendChild(kv(r.filename, `${s.total} / ${s.max}` + (s.is_best ? ' ★ best' : ''), pillClass));
  });
  card.appendChild(scoreGrid);

  // Big table
  const t = el('table');
  const headers = [
    'File', 'Size', 'Res', 'FPS', 'V codec', 'FourCC', 'V bitrate',
    'DV profile', 'DV compat', 'HDR10', 'HDR10+',
    'Audio', 'Ch', 'A bitrate', 'Atmos', 'Subs'
  ];
  t.appendChild(el('thead', {}, el('tr', {}, ...headers.map((h) => el('th', {}, h)))));
  const tb = el('tbody');
  res.rows.forEach((r, i) => {
    const isBest = res.scores[i].is_best;
    const w = res.winners;
    const winCell = (key, val, mono = false) => {
      const isWinner = w[key] && w[key].includes(i) && w[key].length < res.rows.length;
      const cls = (mono ? 'mono ' : '') + (isWinner ? 'winner' : '');
      return el('td', { class: cls.trim() }, val ?? '—');
    };
    const tr = el('tr', { class: isBest ? 'best-row' : '' },
      el('td', { class: 'mono', title: r.path }, r.filename),
      el('td', { class: 'num' }, r.size || '—'),
      el('td', { class: 'mono' }, r.resolution || '—'),
      el('td', { class: 'num' }, r.fps || '—'),
      el('td', { class: 'mono' }, r.video_codec || '—'),
      winCell('fourcc_ok', r.fourcc || '—', true),
      winCell('video_bitrate_mbps', r.video_bitrate_mbps ? `${r.video_bitrate_mbps} Mbps` : '—', false),
      winCell('dv_profile', r.dv_profile || '—', true),
      el('td', { class: 'mono' }, r.dv_compat || '—'),
      el('td', {}, r.hdr10 ? '✓' : '—'),
      winCell('hdr10_plus', r.hdr10_plus ? '✓' : '—'),
      el('td', { class: 'mono' }, r.audio_codec || '—'),
      winCell('audio_channels', r.audio_channels || '—'),
      winCell('audio_bitrate_kbps', r.audio_bitrate_kbps ? `${r.audio_bitrate_kbps} kbps` : '—'),
      winCell('audio_atmos', r.audio_atmos === true ? '✓' : '—'),
      winCell('subtitle_count', r.subtitle_count),
    );
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  card.appendChild(t);

  // Per-file warnings
  res.rows.forEach((r) => {
    r.warnings.forEach((w) => {
      card.appendChild(el('div', { class: 'warning' }, `${r.filename}: ${w}`));
    });
  });

  // Score component breakdown
  card.appendChild(h3WithIcon(ICON_SCORE, 'Score breakdown'));
  const sb = el('table');
  sb.appendChild(el('thead', {}, el('tr', {},
    el('th', {}, 'File'),
    el('th', {}, 'DV present'),
    el('th', {}, 'DV fourcc'),
    el('th', {}, 'HDR10+'),
    el('th', {}, 'Atmos'),
    el('th', {}, 'V bitrate'),
    el('th', {}, 'Resolution'),
    el('th', {}, 'Subs'),
    el('th', {}, 'Total'),
  )));
  const sbody = el('tbody');
  res.scores.forEach((s, i) => {
    const c = s.components;
    sbody.appendChild(el('tr', { class: s.is_best ? 'best-row' : '' },
      el('td', { class: 'mono' }, res.rows[i].filename),
      el('td', { class: 'num' }, c.dv_present),
      el('td', { class: 'num' }, c.dv_fourcc_ok),
      el('td', { class: 'num' }, c.hdr10_plus),
      el('td', { class: 'num' }, c.atmos),
      el('td', { class: 'num' }, c.bit_rate_mbps),
      el('td', { class: 'num' }, c.resolution),
      el('td', { class: 'num' }, c.subtitles),
      el('td', { class: 'num' }, `${s.total} / ${s.max}`),
    ));
  });
  sb.appendChild(sbody);
  card.appendChild(sb);

  return card;
}

// ---------------------------------------------------------------------------
// TV Capture tab

let captureWs = null;
let captureCount = 0;
let lastEventKey = null;
let lastEventNode = null;

function setCaptureStatus(state, label) {
  const s = $('#capture-status');
  s.className = 'status ' + state;
  s.textContent = label;
}

$('#capture-start').addEventListener('click', () => {
  $('#capture-events').innerHTML = '';
  $('#capture-summary').innerHTML = '';
  captureCount = 0;
  lastEventKey = null;
  lastEventNode = null;
  $('#capture-counter').textContent = '0 events';

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  captureWs = new WebSocket(`${proto}://${location.host}/ws/tvlog`);
  captureWs.onopen = () => {
    captureWs.send(JSON.stringify({ cmd: 'start' }));
  };
  captureWs.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.type === 'started') {
      setCaptureStatus('running', 'Capturing… play in TV.app now');
      $('#capture-start').disabled = true;
      $('#capture-stop').disabled = false;
    } else if (data.type === 'event') {
      addEvent(data.event);
    } else if (data.type === 'summary') {
      setCaptureStatus('done', `Done — ${data.summary.event_count} events`);
      $('#capture-summary').appendChild(renderCaptureSummary(data.summary));
      $('#capture-start').disabled = false;
      $('#capture-stop').disabled = true;
    } else if (data.type === 'error') {
      setCaptureStatus('error', data.message);
    }
  };
  captureWs.onerror = () => setCaptureStatus('error', 'WebSocket error');
  captureWs.onclose = () => {
    $('#capture-start').disabled = false;
    $('#capture-stop').disabled = true;
  };
});

$('#capture-stop').addEventListener('click', () => {
  if (captureWs && captureWs.readyState === WebSocket.OPEN) {
    captureWs.send(JSON.stringify({ cmd: 'stop' }));
  }
});

function addEvent(ev) {
  captureCount++;
  $('#capture-counter').textContent = `${captureCount} event${captureCount === 1 ? '' : 's'}`;
  const summary = formatEventSummary(ev);
  const key = `${ev.kind}:${summary}`;
  if (lastEventNode && lastEventKey === key) {
    const count = Number(lastEventNode.dataset.count || '1') + 1;
    lastEventNode.dataset.count = String(count);
    const countNode = lastEventNode.querySelector('.event-count');
    countNode.hidden = false;
    countNode.textContent = `${count}x`;
    return;
  }
  const node = el('div', { class: 'event' },
    el('span', { class: 'kind ' + ev.kind }, ev.kind),
    summary,
    el('span', { class: 'event-count', hidden: true }, '1x'),
  );
  node.dataset.count = '1';
  const wrap = $('#capture-events');
  // Insert at top because flex-direction: column-reverse renders bottom-up
  wrap.insertBefore(node, wrap.firstChild);
  lastEventKey = key;
  lastEventNode = node;
  while (wrap.children.length > 500) wrap.removeChild(wrap.lastChild);
}

function formatEventSummary(ev) {
  switch (ev.kind) {
    case 'hls_variant':
      return [
        `${ev.width ?? 'null'}x${ev.height ?? 'null'}`,
        ev.codecs,
        ev.video_range,
        ev.peak_bps ? `peak ${(ev.peak_bps/1_000_000).toFixed(1)}Mbps` : null,
      ].filter(Boolean).join(' ');
    case 'codec_type':
      return [ev.fourcc, `(${ev.decoder || 'decoder'})`, ev.width ? `${ev.width}x${ev.height}` : null]
        .filter(Boolean).join(' ');
    case 'audio_format':
      return [
        ev.format,
        ev.decodable === true ? 'is decodable' : ev.decodable === false ? 'is not decodable' : null,
        ev.channels ? `ch=${ev.channels}` : null,
        ev.spatialization ? `spat=${ev.spatialization}` : null,
        ev.pipeline_engine ? `[${ev.pipeline_engine}]` : null,
        ev.immersive_rendering_requested != null ? `immersive=${ev.immersive_rendering_requested}` : null,
      ].filter(Boolean).join(' ');
    case 'file_player':
      return [ev.codec, ev.encryption_scheme != null ? `enc=${ev.encryption_scheme}` : null, ev.width ? `${ev.width}x${ev.height}` : null]
        .filter(Boolean).join(' ');
    case 'luma_chroma':
      return `luma=${ev.luma_depth} chroma=${ev.chroma_format}`;
    case 'renderer_hint':
      if (ev.hint === 'media_formatinfo') return rendererMediaLabel(ev);
      if (ev.hint === 'spatial_power') return rendererPowerLabel(ev);
      if (ev.hint === 'route') return ev.route;
      if (ev.hint === 'atmos_decoder_state') return rendererAtmosLabel(ev);
      if (ev.hint === 'atmos_decoder_subtype') return `decoder subtype=${ev.decoder_subtype}`;
      if (ev.hint === 'mixer_spatial_status') return rendererMixerLabel(ev);
      if (ev.hint === 'allowed_spatialization_formats_mask') return `spatialization formats mask=${ev.mask}`;
      if (ev.hint === 'immersive_rendering_requested') return `immersive rendering requested=${ev.immersive_rendering_requested}`;
      return ev.hint || ev.raw || '';
    default:
      return ev.raw || '';
  }
}

async function _copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Embedded browsers can expose navigator.clipboard but still reject writes.
      // Fall through to the selection-based copy path while the click is active.
    }
  }

  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.left = '-9999px';
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, ta.value.length);
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch {
    document.body.removeChild(ta);
    return false;
  }
  document.body.removeChild(ta);
  return copied;
}

function _setCopyStatus(wrap, message, ok) {
  const status = wrap.querySelector('.copy-status');
  if (wrap.copyStatusTimer) clearTimeout(wrap.copyStatusTimer);
  status.textContent = message;
  wrap.classList.toggle('copy-ok', ok);
  wrap.classList.toggle('copy-error', !ok);
  wrap.copyStatusTimer = setTimeout(() => {
    wrap.classList.remove('copy-ok');
    wrap.classList.remove('copy-error');
    status.textContent = '';
    wrap.copyStatusTimer = null;
  }, 1200);
}

async function _copyFromButton(wrap, text) {
  const copied = await _copyText(text);
  _setCopyStatus(wrap, copied ? 'Copied' : 'Copy failed', copied);
}

function renderCaptureSummary(summary) {
  const card = el('div', { class: 'card capt-summary' });
  const eventsReturned = summary.events_returned ?? (summary.events ? summary.events.length : summary.event_count);
  const eventsDropped = summary.events_dropped ?? Math.max(0, summary.event_count - eventsReturned);
  const rawEventLimit = summary.raw_event_limit ?? eventsReturned;

  // header row: title + copy icon
  const hdr = el('div', { class: 'capt-summary-hdr' });
  hdr.appendChild(el('h2', {}, 'Playback summary'));

  const copyWrap = el('div', { class: 'copy-wrap' });
  const copyStatus = el('span', { class: 'copy-status', 'aria-live': 'polite' });

  copyWrap.appendChild(el('button', {
    class: 'copy-icon-btn',
    type: 'button',
    title: 'Copy summary',
    'aria-label': 'Copy summary',
    onclick: async () => {
      await _copyFromButton(copyWrap, JSON.stringify({
        event_count: summary.event_count,
        events_returned: eventsReturned,
        events_dropped: eventsDropped,
        raw_event_limit: rawEventLimit,
        duration_s: summary.duration_s,
        playback: summary.playback,
      }, null, 2));
    },
    html: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.5 3H14.6C16.84 3 17.96 3 18.816 3.436C19.569 3.819 20.181 4.431 20.564 5.184C21 6.04 21 7.16 21 9.4V16.5M6.2 21H14.3C15.42 21 15.98 21 16.408 20.782C16.784 20.59 17.09 20.284 17.282 19.908C17.5 19.48 17.5 18.92 17.5 17.8V9.7C17.5 8.58 17.5 8.02 17.282 7.592C17.09 7.216 16.784 6.91 16.408 6.718C15.98 6.5 15.42 6.5 14.3 6.5H6.2C5.08 6.5 4.52 6.5 4.092 6.718C3.716 6.91 3.41 7.216 3.218 7.592C3 8.02 3 8.58 3 9.7V17.8C3 18.92 3 19.48 3.218 19.908C3.41 20.284 3.716 20.59 4.092 20.782C4.52 21 5.08 21 6.2 21Z"/></svg>',
  }));

  copyWrap.appendChild(el('div', { class: 'copy-menu' },
    el('button', {
      class: 'copy-menu-item',
      type: 'button',
      onclick: async () => {
        await _copyFromButton(copyWrap, JSON.stringify(summary, null, 2));
      },
    }, 'Copy full JSON'),
  ));

  copyWrap.appendChild(copyStatus);
  hdr.appendChild(copyWrap);
  card.appendChild(hdr);
  card.appendChild(el('div', { class: 'filename' }, `${summary.event_count} events in ${summary.duration_s.toFixed(1)}s`));
  if (eventsDropped > 0) {
    card.appendChild(el('div', { class: 'info-note' },
      `Raw event JSON is capped to the newest ${eventsReturned} of ${summary.event_count} parsed events; ${eventsDropped} older events were dropped from memory.`,
    ));
  } else {
    card.appendChild(el('div', { class: 'info-note' },
      `Raw event JSON includes ${eventsReturned} retained event${eventsReturned === 1 ? '' : 's'}; cap ${rawEventLimit}.`,
    ));
  }

  const p = summary.playback || {};
  if (!p || Object.keys(p).length === 0) {
    card.appendChild(el('div', { class: 'warning' }, 'No playback events captured. Did TV.app play anything during the capture window?'));
    return card;
  }

  // Verdict pill
  const pills = el('div', { class: 'row', style: 'justify-content: flex-start; margin-bottom: 12px' });
  const hlsSourceLabel = p.hls_delivery === 'downloaded_movpkg'
    ? 'Source: downloaded .movpkg'
    : 'Source: HLS (Apple TV+ / streaming)';
  pills.appendChild(el('span', { class: 'pill ' + (p.source === 'hls' ? 'good' : p.source === 'local_file' ? 'warn' : 'neutral') },
    p.source === 'hls' ? hlsSourceLabel : p.source === 'local_file' ? 'Source: Local file' : 'Source: unknown'));
  if (p.pipeline_engine) {
    const isStream = p.pipeline_engine === 'FigStreamPlayer';
    pills.appendChild(el('span', { class: `pill ${isStream ? 'good' : 'neutral'}` }, `Engine: ${p.pipeline_engine}`));
  }
  if (p.dolby_vision_active === true) pills.appendChild(el('span', { class: 'pill good' }, 'Dolby Vision active'));
  else if (p.dolby_vision_active === false) pills.appendChild(el('span', { class: 'pill bad' }, 'Dolby Vision NOT active'));
  else if (p.dv_label) pills.appendChild(el('span', { class: 'pill warn' }, p.dv_label));
  if (p.audio && p.audio.is_atmos) pills.appendChild(el('span', { class: 'pill good' }, 'Atmos'));
  if (p.downloaded_hls_verdict === 'movpkg_atmos_variant_present_but_not_selected') {
    pills.appendChild(el('span', { class: 'pill warn' }, '.movpkg Atmos seen, stereo selected'));
  } else if (p.downloaded_hls_verdict === 'movpkg_atmos_variant_missing_or_incomplete') {
    pills.appendChild(el('span', { class: 'pill warn' }, '.movpkg Atmos missing/incomplete'));
  } else if (p.downloaded_hls_verdict === 'movpkg_figstreamplayer_selected_stereo') {
    pills.appendChild(el('span', { class: 'pill warn' }, '.movpkg stereo selected'));
  }
  if (p.best_audio && audioLabel(p.best_audio) !== audioLabel(p.audio)) {
    pills.appendChild(el('span', { class: `pill ${audioPillClass(p.best_audio)}` }, `Best audio: ${audioLabel(p.best_audio)}`));
  }
  card.appendChild(pills);

  if (p.dv_diagnosis) {
    card.appendChild(el('div', { class: 'warning' }, p.dv_diagnosis));
  }
  if (p.capture_quality?.likely_missed_audio_init) {
    card.appendChild(el('div', { class: 'warning' }, p.capture_quality.note));
  }

  // Grid
  const g = el('div', { class: 'grid' });
  if (p.decoded_fourcc) g.appendChild(kv('Decoded FourCC', p.decoded_fourcc, tvDecodedFourccClass(p.decoded_fourcc)));
  if (p.decoder) g.appendChild(kv('Decoder', p.decoder));
  if (p.decoded_resolution) g.appendChild(kv('Resolution', p.decoded_resolution));
  if (p.peak_mbps) g.appendChild(kv('HLS peak', `${p.peak_mbps} Mbps`));
  if (p.avg_mbps) g.appendChild(kv('HLS average', `${p.avg_mbps} Mbps`));
  if (p.video_fourcc) g.appendChild(kv('HLS video', p.video_fourcc));
  if (p.audio_codec) g.appendChild(kv('HLS audio', p.audio_codec));
  if (p.selected_hls_audio_group) g.appendChild(kv('HLS AudioGroup', p.selected_hls_audio_group,
    p.selected_hls_audio_group_kind === 'atmos' ? 'good' : p.selected_hls_audio_group_kind === 'stereo' ? 'warn' : 'neutral'));
  if (p.selected_hls_audio_group_kind) g.appendChild(kv('HLS AudioGroup kind', p.selected_hls_audio_group_kind,
    p.selected_hls_audio_group_kind === 'atmos' ? 'good' : p.selected_hls_audio_group_kind === 'stereo' ? 'warn' : 'neutral'));
  if (p.hls_delivery) g.appendChild(kv('HLS delivery', p.hls_delivery));
  if (p.downloaded_hls_verdict) g.appendChild(kv('Downloaded HLS verdict', p.downloaded_hls_verdict,
    p.downloaded_hls_verdict.includes('stereo') || p.downloaded_hls_verdict.includes('not_selected') ? 'warn' : 'good'));
  if (p.audio) {
    g.appendChild(kv('Current audio', audioLabel(p.audio), audioPillClass(p.audio)));
    g.appendChild(kv('Current channels', p.audio.channels));
    g.appendChild(kv('Current sample rate', p.audio.sample_rate ? `${p.audio.sample_rate} Hz` : null));
    g.appendChild(kv('Current spatialization', p.audio.spatialization));
    g.appendChild(kv('Current Atmos eligible', p.audio.spatialization_eligible));
    if (p.audio.decodable != null) g.appendChild(kv('Decodable', p.audio.decodable ? 'yes' : 'no', p.audio.decodable ? 'good' : 'bad'));
    if (p.audio.diagnosis) g.appendChild(kv('Audio diagnosis', p.audio.diagnosis, 'warn'));
  }
  if (p.best_audio) {
    g.appendChild(kv('Best observed audio', audioLabel(p.best_audio), audioPillClass(p.best_audio)));
    if (p.audio && audioLabel(p.best_audio) !== audioLabel(p.audio)) {
      g.appendChild(kv('Audio event note', 'Current audio is the latest event; best observed audio is the strongest path seen during capture.'));
    }
  }
  if (p.pipeline_engine) {
    g.appendChild(kv('Pipeline engine', p.pipeline_engine,
      p.pipeline_engine === 'FigStreamPlayer' ? 'good' : 'neutral'));
    if (p.pipeline_engines_observed?.length > 1) {
      g.appendChild(kv('All engines observed', p.pipeline_engines_observed.join(', ')));
    }
  }
  if (p.file_player) {
    g.appendChild(kv('File codec', p.file_player.codec));
    g.appendChild(kv('Encryption scheme', p.file_player.encryption_scheme));
  }
  if (p.capture_quality) {
    g.appendChild(kv('Capture quality', p.capture_quality.label, p.capture_quality.likely_missed_audio_init ? 'warn' : 'neutral'));
  }
  card.appendChild(g);

  if (p.observed_audio && p.observed_audio.length) {
    card.appendChild(h3WithIcon(ICON_AUDIO, 'Observed audio formats'));
    const list = el('div', { class: 'audio-observed' });
    p.observed_audio.forEach((audio) => {
      list.appendChild(el('div', { class: 'audio-observed-item' },
        el('span', { class: `pill ${audioPillClass(audio)}` }, audioLabel(audio)),
        audio.count > 1 ? el('span', { class: 'event-count' }, `${audio.count}x`) : null,
      ));
    });
    card.appendChild(list);
  }

  if (p.renderer_evidence) {
    card.appendChild(h3WithIcon(ICON_SCORE, 'Spatial renderer evidence'));
    const r = p.renderer_evidence;
    const verdict = rendererVerdictLabel(r);
    if (verdict) {
      card.appendChild(el('div', { class: `renderer-verdict ${verdict.className}` }, verdict.text));
    }
    if (r.verdict_note) {
      card.appendChild(el('div', { class: 'info-note' }, r.verdict_note));
    }
    const rg = el('div', { class: 'grid' });
    if (r.routes?.length) rg.appendChild(kv('Output route', r.routes.join(', ')));
    rg.appendChild(kv('Route capability', r.route_capability_label || 'not captured'));
    rg.appendChild(kv('App spatial rendering ever true', capturedText(r.app_spatial_rendering_ever_true), r.app_spatial_rendering_ever_true ? 'good' : 'neutral'));
    rg.appendChild(kv('App spatial rendering last state', capturedText(r.app_spatial_rendering_last_state), r.app_spatial_rendering_last_state ? 'good' : 'neutral'));
    if (r.first_true_at_seconds != null) rg.appendChild(kv('First true at', `${r.first_true_at_seconds}s into capture`, 'good'));
    if (r.immersive_rendering_requested != null) rg.appendChild(kv('Immersive rendering requested', capturedText(r.immersive_rendering_requested), r.immersive_rendering_requested ? 'good' : 'neutral'));
    if (r.allowed_spatialization_formats_masks?.length) rg.appendChild(kv('Spatialization formats mask', r.allowed_spatialization_formats_masks.join(', ')));
    rg.appendChild(kv('Lower-level spatialization', activeText(r.lower_level_spatialization_active), r.lower_level_spatialization_active ? 'good' : 'neutral'));
    rg.appendChild(kv('Spatial power', activeText(r.spatial_power_active), r.spatial_power_active ? 'good' : 'neutral'));
    rg.appendChild(kv('Head tracking', activeText(r.head_tracking_active), r.head_tracking_active ? 'good' : 'neutral'));
    rg.appendChild(kv('Atmos decoder', activeText(r.atmos_decoder_active), r.atmos_decoder_active ? 'good' : 'neutral'));
    rg.appendChild(kv('OAR mode', activeText(r.oar_mode_active), r.oar_mode_active ? 'good' : 'neutral'));
    rg.appendChild(kv('Mixer spatializable', capturedText(r.mixer_content_spatializable), r.mixer_content_spatializable ? 'good' : 'neutral'));
    if (r.mixer_spatialization_statuses?.length) rg.appendChild(kv('Mixer spatialization statuses', r.mixer_spatialization_statuses.join(', ')));
    if (r.media_formatinfo?.length) {
      r.media_formatinfo.forEach((item, index) => {
        rg.appendChild(kv(index ? 'App spatial flag' : 'App spatial flag', rendererMediaLabel(item), item.rendering_spatial_audio ? 'good' : 'neutral'));
      });
    }
    if (r.spatial_power?.length) {
      r.spatial_power.forEach((item) => {
        rg.appendChild(kv('Spatial power state', rendererPowerLabel(item), item.spatialization ? 'good' : 'neutral'));
      });
    }
    if (r.prefers_head_tracked_spatialization?.length) {
      rg.appendChild(kv('Prefers head tracking', r.prefers_head_tracked_spatialization.map(boolText).join(', ')));
    }
    if (r.atmos_decoder_states?.length) {
      r.atmos_decoder_states.forEach((item) => {
        rg.appendChild(kv('Atmos decoder', rendererAtmosLabel(item), item.decoder_is_atmos ? 'good' : 'neutral'));
      });
    }
    if (r.decoder_subtypes?.length) rg.appendChild(kv('Decoder subtype', r.decoder_subtypes.join(', ')));
    if (r.mixer_spatial_status?.length) {
      r.mixer_spatial_status.forEach((item) => {
        rg.appendChild(kv('Mixer spatial status', rendererMixerLabel(item), item.content_spatializable ? 'good' : 'neutral'));
      });
    }
    if (r.spatial_rendering_changed_count) rg.appendChild(kv('Spatial rendering changes', r.spatial_rendering_changed_count));
    if (r.observed_hints?.length) rg.appendChild(kv('Renderer hint types', r.observed_hints.join(', ')));
    card.appendChild(rg);
  }

  // Raw event log
  const rawSummary = eventsDropped > 0
    ? `Newest ${eventsReturned} of ${summary.event_count} events`
    : `All ${eventsReturned} retained events`;
  card.appendChild(el('details', {}, el('summary', {}, rawSummary), el('pre', {}, JSON.stringify(summary.events, null, 2))));

  return card;
}
