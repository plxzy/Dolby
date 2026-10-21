/**
 * Drag & drop from Finder. Safari hands over file:// URIs; Chrome and Firefox
 * only expose File objects without a path, so those are resolved by name via
 * /api/find (Spotlight, then find(1) starting from the last folder used). When
 * several files share the name, a native <dialog> asks which one was meant.
 */

import { getJson } from './api.js';
import { el, replace } from './dom.js';

let lastDirectory = '';
let chooser = null;
let pendingChoice = null; // resolves the prompt that is currently open

function rememberDirectory(path) {
  const cut = path.lastIndexOf('/');
  if (cut > 0) lastDirectory = path.slice(0, cut);
}

/** "file:///Users/me/A%20B.mkv" -> "/Users/me/A B.mkv"; plain paths pass through. */
function pathFromUri(line) {
  if (!line.startsWith('file://')) return line;
  try {
    return decodeURIComponent(new URL(line).pathname);
  } catch {
    return line.slice('file://'.length);
  }
}

function buildChooser() {
  const dialog = el(
    'dialog',
    { class: 'sheet', 'aria-labelledby': 'chooser-title' },
    el(
      'form',
      { method: 'dialog' },
      el(
        'header',
        { class: 'sheet-header' },
        el('h2', { id: 'chooser-title' }),
        el('p', {}, 'Several files share this name. Pick the one you dropped.'),
      ),
      el('ol', { class: 'choices' }),
      el('footer', { class: 'sheet-footer' }, el('button', { class: 'btn', value: '' }, 'Cancel')),
    ),
  );
  // `close` is dispatched asynchronously, so it can arrive after the chooser has
  // already been reopened for the next file; such a stale event is ignored.
  dialog.addEventListener('close', () => {
    if (!dialog.open) settleChoice(dialog.returnValue || null);
  });
  document.body.append(dialog);
  return dialog;
}

function settleChoice(value) {
  const resolve = pendingChoice;
  pendingChoice = null;
  resolve?.(value);
}

/** Ask which of `paths` was meant; resolves to the chosen path, or null on cancel/Escape. */
function choosePath(name, paths) {
  chooser ??= buildChooser();
  settleChoice(chooser.returnValue || null); // a previous prompt whose close event hasn't landed yet
  chooser.querySelector('#chooser-title').textContent = `Which “${name}”?`;
  replace(
    chooser.querySelector('.choices'),
    paths.map((path, index) => {
      const cut = path.lastIndexOf('/') + 1;
      return el(
        'li',
        {},
        el(
          'button',
          { class: 'choice', value: path },
          el('span', { class: 'choice-index' }, index + 1),
          el('span', { class: 'choice-path' }, path.slice(0, cut), el('strong', {}, path.slice(cut))),
        ),
      );
    }),
  );
  chooser.returnValue = '';
  return new Promise((resolve) => {
    pendingChoice = resolve;
    chooser.showModal();
  });
}

async function resolveByName(name) {
  const { paths } = await getJson('/api/find', { name, hint: lastDirectory });
  if (paths.length === 1) return paths[0];
  if (paths.length > 1) return choosePath(name, paths);
  throw new Error(`Couldn’t locate “${name}”. Use Browse… or paste its full path.`);
}

/**
 * Extract absolute paths from a drop. Reads the DataTransfer synchronously
 * (it is emptied once the event returns), then resolves names one at a time
 * so at most one chooser is open. Returns { paths, problems }.
 */
async function pathsFromDrop(dataTransfer) {
  const lines = (dataTransfer.getData('text/uri-list') || dataTransfer.getData('text/plain'))
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));
  const files = Array.from(dataTransfer.files);

  const direct = lines.map(pathFromUri).filter((path) => path.startsWith('/') || path.startsWith('~'));
  if (direct.length) {
    direct.forEach(rememberDirectory);
    return { paths: direct, problems: [] };
  }

  const paths = [];
  const problems = [];
  for (const file of files) {
    try {
      const path = await resolveByName(file.name);
      if (path) {
        rememberDirectory(path);
        paths.push(path);
      }
    } catch (error) {
      problems.push(error.message);
    }
  }
  return { paths, problems };
}

/** Make `zone` accept drops. `onPaths(paths)` gets resolved paths; `onProblem(message)` each failure. */
export function bindDropzone(zone, { onPaths, onProblem }) {
  let depth = 0;
  const accepts = (event) =>
    Array.from(event.dataTransfer?.types ?? []).some((type) => type === 'Files' || type.startsWith('text/'));
  const setActive = (active) => zone.toggleAttribute('data-dragging', active);

  zone.addEventListener('dragenter', (event) => {
    if (!accepts(event)) return;
    event.preventDefault();
    depth += 1;
    setActive(true);
  });
  zone.addEventListener('dragover', (event) => {
    if (!accepts(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  });
  zone.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (!depth) setActive(false);
  });
  zone.addEventListener('drop', async (event) => {
    event.preventDefault();
    depth = 0;
    setActive(false);
    const { paths, problems } = await pathsFromDrop(event.dataTransfer);
    problems.forEach(onProblem);
    if (paths.length) onPaths(paths);
  });
}
